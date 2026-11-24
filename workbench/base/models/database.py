import logging
import sqlite3
from urllib.parse import quote_plus

from sqlalchemy import Text, create_engine, event, inspect, text
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.engine import Engine
from sqlalchemy.orm import close_all_sessions, declarative_base, sessionmaker

from config import (
    DEFAULT_MYSQL_PASSWORD,
    get_app_env,
    get_database_url,
    get_mysql_database,
    get_mysql_host,
    get_mysql_password,
    get_mysql_port,
    get_mysql_user,
)

logger = logging.getLogger(__name__)

Base = declarative_base()
# expire_on_commit=False：commit 后访问属性不再触发隐藏的重新 SELECT（那会
# 在 BEGIN IMMEDIATE 模式下重新拿到写锁且直到请求收尾才释放，与后台任务死锁）
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False)
engine: Engine | None = None

# 长文本列的统一类型：MySQL 用 MEDIUMTEXT（16MB），其余方言（含 SQLite 测试）保持 TEXT。
# 原因：MySQL 的 TEXT 上限是 65535 **字节**，utf8mb4 下约 2.1 万中文字即溢出，
# 而业务上限普遍按**字符**计（接口文档 200 万字符、需求 5 万字符、消息 3.2 万字符），
# 严格模式（STRICT_TRANS_TABLES）下超限直接 1406 Data too long 报错。
LongText = Text().with_variant(MEDIUMTEXT(), "mysql")


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, connection_record):
    """SQLite 默认不执行外键约束：不开启时 ondelete CASCADE/SET NULL 静默失效，
    与 MySQL 生产行为分叉（裸 FK 如 test_runs.spec_id 的置空在测试里永远测不到）"""
    if isinstance(dbapi_connection, sqlite3.Connection):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()



def build_database_url() -> str:
    database_url = get_database_url()
    if database_url:
        return database_url

    mysql_user = get_mysql_user()
    mysql_password = get_mysql_password()
    mysql_host = get_mysql_host()
    mysql_port = get_mysql_port()
    mysql_database = get_mysql_database()

    if get_app_env() == "production" and mysql_password == DEFAULT_MYSQL_PASSWORD:
        raise RuntimeError("Set DATABASE_URL or MYSQL_PASSWORD before starting in production")

    return (
        f"mysql+pymysql://{quote_plus(mysql_user)}:{quote_plus(mysql_password)}"
        f"@{mysql_host}:{mysql_port}/{mysql_database}"
    )



def _create_engine(database_url: str) -> Engine:
    if database_url.startswith("sqlite"):
        eng = create_engine(
            database_url,
            # busy_timeout: SQLite 默认 5s，云同步周期持锁时后台写入容易撞锁报错
            connect_args={"check_same_thread": False, "timeout": 30},
            echo=False,
        )

        @event.listens_for(eng, "connect")
        def _sqlite_tuning(dbapi_conn, _record):
            cur = dbapi_conn.cursor()
            # WAL：读写不互斥——长读事务（同步推送/拉取）不再阻塞写入提交
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA busy_timeout=30000")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.close()
            # 禁用 pysqlite 隐式 deferred BEGIN，改由下方 begin 事件发 BEGIN IMMEDIATE
            # （SQLAlchemy 官方配方：否则"先读后写"会话升级锁会绕过 busy_timeout）
            dbapi_conn.isolation_level = None

        @event.listens_for(eng, "begin")
        def _sqlite_begin_immediate(conn):
            """用 BEGIN IMMEDIATE 取代 deferred BEGIN：写事务一开始就取写锁，
            busy_timeout 才能生效排队。否则"先读后写"的会话在并发写入时尝试
            从读快照升级为写锁会立即报 database is locked（busy handler 不介入
            升级路径，知识库上传连发即触发）。"""
            import logging as _lg
            import threading as _th
            import time as _t

            _t0 = _t.perf_counter()
            conn.exec_driver_sql("BEGIN IMMEDIATE")
            _dt = _t.perf_counter() - _t0
            if _dt > 0.5:
                _lg.getLogger("wb.sqlite").warning(
                    "BEGIN IMMEDIATE 等待 %.1fs 线程=%s（写锁被长事务占用）",
                    _dt, _th.current_thread().name,
                )

        return eng

    return create_engine(
        database_url,
        pool_size=20,
        max_overflow=30,
        pool_pre_ping=True,
        pool_recycle=3600,
        connect_args={
            "charset": "utf8mb4",
            "connect_timeout": 10,
        },
        echo=False,
    )



def configure_database(database_url: str | None = None) -> Engine:
    global engine

    engine = _create_engine(database_url or build_database_url())
    SessionLocal.configure(bind=engine)
    return engine



def get_engine() -> Engine:
    if engine is None:
        return configure_database()
    return engine



def create_session():
    get_engine()
    return SessionLocal()



# TEXT → MEDIUMTEXT 的存量升级清单（表名/列名来自模块内常量，非外部输入）。
# 与 models/*.py 中的 LongText 列一一对应，tests/test_long_text_columns.py 会双向校验。
_MEDIUMTEXT_UPGRADES: dict[str, tuple[str, ...]] = {
    "api_specs": ("content",),
    "api_endpoints": ("parameters_json", "request_body_json", "responses_json", "response_schemas_json"),
    "api_endpoint_cases": ("request_json", "assertions_json"),
    # attachment_text 早在历史上已改为 MEDIUMTEXT（models/chat.py），一并纳入清单：
    # 既保证双向校验闭合，也让早期部署（仍是 TEXT）能被同一次迁移修正
    "messages": ("content", "attachment_text"),
    "workflows": ("requirement_text",),
    "artifacts": ("content",),
    "test_case_set_versions": ("content",),
    "test_run_results": ("request_json", "response_json", "assertions_json"),
}


def _upgrade_long_text_columns(current_engine: Engine, inspector, table_names: set[str]) -> None:
    """把存量 TEXT 列就地升级为 MEDIUMTEXT。

    幂等：已是 MEDIUMTEXT 的列直接跳过；非 MySQL 方言（SQLite）直接返回。
    MODIFY 会丢弃列上未重述的 DEFAULT/COMMENT，故这里按 information_schema 的现状
    重述可空性（这些列均无 DEFAULT/COMMENT，已在测试中固化断言）。
    """
    if current_engine.dialect.name != "mysql":
        return

    for table, columns in _MEDIUMTEXT_UPGRADES.items():
        if table not in table_names:
            continue
        existing = {column["name"]: column for column in inspector.get_columns(table)}
        pending = [
            name
            for name in columns
            if name in existing and not isinstance(existing[name]["type"], MEDIUMTEXT)
        ]
        if not pending:
            continue
        # MySQL 修改 TEXT/MEDIUMTEXT 属表重建（非 INSTANT/INPLACE），大表会锁表，
        # 因此执行前先打 WARNING 让运维可见；大表建议先在维护窗口手工执行同一 ALTER
        logger.warning(
            "升级长文本列（表重建，可能锁表）: %s -> %s", table, ", ".join(pending)
        )
        with current_engine.begin() as conn:
            for name in pending:
                nullable_clause = "" if existing[name].get("nullable", False) else " NOT NULL"
                conn.execute(text(f"ALTER TABLE {table} MODIFY {name} MEDIUMTEXT{nullable_clause}"))
        logger.info("已升级长文本列: %s (%s)", table, ", ".join(pending))


def _ensure_schema_updates(current_engine: Engine):
    """补齐当前版本需要的轻量 schema 变更。"""
    inspector = inspect(current_engine)
    table_names = set(inspector.get_table_names())

    if "knowledge_bases" not in table_names:
        return

    column_names = {column["name"] for column in inspector.get_columns("knowledge_bases")}
    index_names = {index["name"] for index in inspector.get_indexes("knowledge_bases")}

    with current_engine.begin() as conn:
        if "owner_user_id" not in column_names:
            conn.execute(text("ALTER TABLE knowledge_bases ADD COLUMN owner_user_id INTEGER NULL"))

        if "ix_knowledge_bases_owner_user_id" not in index_names:
            conn.execute(
                text("CREATE INDEX ix_knowledge_bases_owner_user_id ON knowledge_bases (owner_user_id)")
            )

    if "users" in table_names:
        user_columns = {c["name"] for c in inspector.get_columns("users")}
        with current_engine.begin() as conn:
            if "created_at" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN created_at DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP"))
            if "last_login_at" not in user_columns:
                conn.execute(text("ALTER TABLE users ADD COLUMN last_login_at DATETIME NULL"))

    if "messages" in table_names:
        message_columns = {c["name"] for c in inspector.get_columns("messages")}
        with current_engine.begin() as conn:
            # 消息附件改为独立字段存储，不再拼接进 content
            if "attachment_name" not in message_columns:
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_name VARCHAR(255) NULL"))
            if "attachment_text" not in message_columns:
                # MEDIUMTEXT 容纳较长文档正文；SQLite 中含 TEXT 的类型名即 TEXT 亲和
                conn.execute(text("ALTER TABLE messages ADD COLUMN attachment_text MEDIUMTEXT NULL"))

    if "workflows" in table_names:
        workflow_columns = {c["name"] for c in inspector.get_columns("workflows")}
        with current_engine.begin() as conn:
            # 需求原文的来源文档名（后补列）：存量任务均为手工粘贴，保持 NULL
            if "requirement_file_name" not in workflow_columns:
                conn.execute(
                    text("ALTER TABLE workflows ADD COLUMN requirement_file_name VARCHAR(255) NULL")
                )

    if "knowledge_files" in table_names:
        file_columns = {c["name"] for c in inspector.get_columns("knowledge_files")}
        with current_engine.begin() as conn:
            if "content_hash" not in file_columns:
                conn.execute(text("ALTER TABLE knowledge_files ADD COLUMN content_hash VARCHAR(64) NULL"))
                conn.execute(
                    text("CREATE INDEX ix_knowledge_files_content_hash ON knowledge_files (content_hash)")
                )
            if "skipped_chunks" not in file_columns:
                conn.execute(text("ALTER TABLE knowledge_files ADD COLUMN skipped_chunks INTEGER NULL"))
            if "error" not in file_columns:
                conn.execute(text("ALTER TABLE knowledge_files ADD COLUMN error TEXT NULL"))
            # 幂等回填：存量行的 NULL 会让响应模型校验失败（skipped_chunks 期望 int）。
            # 放在 ALTER 之后无条件执行——无论列是刚补的还是早已存在，NULL 都要归零
            conn.execute(text("UPDATE knowledge_files SET skipped_chunks = 0 WHERE skipped_chunks IS NULL"))

    if "test_runs" in table_names:
        run_columns = {c["name"] for c in inspector.get_columns("test_runs")}
        with current_engine.begin() as conn:
            if "endpoints_json" not in run_columns:
                conn.execute(text("ALTER TABLE test_runs ADD COLUMN endpoints_json TEXT NULL"))
            if "error" not in run_columns:
                conn.execute(text("ALTER TABLE test_runs ADD COLUMN error TEXT NULL"))
            # 幂等回填：存量执行记录无接口快照，置空数组供历史渲染
            conn.execute(text("UPDATE test_runs SET endpoints_json = '[]' WHERE endpoints_json IS NULL"))

    if "api_specs" in table_names:
        spec_columns = {c["name"] for c in inspector.get_columns("api_specs")}
        with current_engine.begin() as conn:
            if "source_url" not in spec_columns:
                conn.execute(text("ALTER TABLE api_specs ADD COLUMN source_url VARCHAR(500) NULL"))
            if "auth_config_json" not in spec_columns:
                # MySQL 的 TEXT 列不允许 DEFAULT（错误 1101），与 endpoints_json 同模式：
                # 先加 NULL 列，下方幂等回填空串（=未配置）
                conn.execute(text("ALTER TABLE api_specs ADD COLUMN auth_config_json TEXT NULL"))
            if "description" not in spec_columns:
                conn.execute(text("ALTER TABLE api_specs ADD COLUMN description TEXT NULL"))
            # 幂等回填：无论列是新加的还是已存在，NULL 都归位为空串
            conn.execute(text("UPDATE api_specs SET auth_config_json = '' WHERE auth_config_json IS NULL"))
            conn.execute(text("UPDATE api_specs SET description = '' WHERE description IS NULL"))

    if "api_endpoints" in table_names:
        endpoint_columns = {c["name"] for c in inspector.get_columns("api_endpoints")}
        with current_engine.begin() as conn:
            if "request_body_media_type" not in endpoint_columns:
                conn.execute(
                    text("ALTER TABLE api_endpoints ADD COLUMN request_body_media_type VARCHAR(100) NOT NULL DEFAULT ''")
                )
            if "response_schemas_json" not in endpoint_columns:
                # MySQL 的 TEXT 列不允许 DEFAULT（错误 1101）：先加 NULL 列，下方幂等回填空串
                conn.execute(text("ALTER TABLE api_endpoints ADD COLUMN response_schemas_json TEXT NULL"))
            # 幂等回填：存量行均为 JSON-only 解析产物，有请求体的按 application/json 归位
            conn.execute(
                text(
                    "UPDATE api_endpoints SET request_body_media_type = 'application/json' "
                    "WHERE request_body_media_type = '' AND request_body_json != ''"
                )
            )
            # 幂等回填：存量接口无响应 schema 快照，置空串（=未快照）
            conn.execute(text("UPDATE api_endpoints SET response_schemas_json = '' WHERE response_schemas_json IS NULL"))

    if "api_endpoint_cases" in table_names:
        case_columns = {c["name"] for c in inspector.get_columns("api_endpoint_cases")}
        with current_engine.begin() as conn:
            if "assertions_json" not in case_columns:
                conn.execute(text("ALTER TABLE api_endpoint_cases ADD COLUMN assertions_json TEXT NULL"))
            # 幂等回填：存量用例均为仅状态码断言，置空数组
            conn.execute(text("UPDATE api_endpoint_cases SET assertions_json = '[]' WHERE assertions_json IS NULL"))

    if "test_run_results" in table_names:
        result_columns = {c["name"] for c in inspector.get_columns("test_run_results")}
        with current_engine.begin() as conn:
            if "assertions_json" not in result_columns:
                conn.execute(text("ALTER TABLE test_run_results ADD COLUMN assertions_json TEXT NULL"))
            # 幂等回填：存量结果无断言明细，置空数组
            conn.execute(text("UPDATE test_run_results SET assertions_json = '[]' WHERE assertions_json IS NULL"))

    # 长文本列升容（TEXT 65535 字节 → MEDIUMTEXT 16MB），幂等且仅 MySQL 生效
    _upgrade_long_text_columns(current_engine, inspector, table_names)



def init_db():
    """初始化数据库（创建表结构并补齐必要字段）"""
    current_engine = get_engine()
    Base.metadata.create_all(bind=current_engine)
    _ensure_schema_updates(current_engine)



def get_db():
    db = create_session()
    try:
        yield db
    finally:
        db.close()



def check_database_connection():
    """检查数据库连接是否正常"""
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        logger.error("数据库连接失败: %s", e, exc_info=True)
        return False

