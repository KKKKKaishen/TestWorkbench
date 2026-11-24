"""云同步引擎（离线优先）。

架构：
- 服务器端（WB_SYNC_ROLE=cloud）：同一套 app 部署在云服务器（MySQL），
  提供 /wb/sync/* 推拉端点，是所有数据的中心副本。
- 客户端（默认）：本地 SQLite 照常工作（断网可用完整功能），
  后台同步线程把本地变更推到服务器、拉取其他客户端的变更。
  断网期间产生的数据先进本地行版本表（dirty 标记），恢复网络后自动推送。

同步协议：
- 行级版本表 wb_sync_rowver：每行数据的 (table_name, row_pk) 记录最近变更
  时间戳 stamp（LWW 依据）、client_id、deleted 墓碑、dirty 待推送标记。
- SQLite 客户端用触发器捕获所有写入（含 ORM、裸 SQL、DB 级联删除）；
  应用期写 bypass 标记表 wb_sync_apply_flag 时触发器不生效（拉取套用不回灌）。
  MySQL 服务器用 @sync_bypass 会话变量 + AFTER 触发器达成同样效果。
- 冲突策略：时间戳最后写入胜出（LWW）。拉取端 incoming.stamp > 本地 stamp
  才套用（本地待推送行不被远端覆盖）；推送端 incoming.stamp > 服务器 stamp
  才落库（被拒的行等拉取返回更新版本）。
- 主键分配：客户端注册获得序号 ordinal，ID 从 ordinal * 1e9 起，
  与服务器及其他客户端区间隔离；存量库启用同步时一次性平移迁移。
- 级联可见性：DB 级联删除/置空对 ORM 事件不可见，删除前后用 FK 图
  显式收集受害行补墓碑/脏标记（两端一致）。
- 文件同步：附件/auto_runs 产物按 rel_path + sha256 双向对账上传下载。
"""
import hashlib
import json
import logging
import os
import threading
import time
import urllib.parse
from contextlib import contextmanager
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event, text
from sqlalchemy.orm import Mapper, Session

import wbmodels as wb
from config import get_upload_dir
from models.database import get_engine

logger = logging.getLogger(__name__)

# 角色与常量 ------------------------------------------------------------------

IS_CLOUD = (os.getenv("WB_SYNC_ROLE") or "").strip().lower() == "cloud"

# 不参与同步的表：同步机制自身 + 每机器本地噪声 + 挂在底座文件表上的分块
_EXCLUDED = {
    "wb_sync_rowver", "wb_sync_state", "wb_sync_files", "wb_sync_apply_flag",
    "wb_ai_call_logs", "wb_kb_chunks", "wb_user",
    # 知识库云同步专用表（账号维度，走 _kb_sync_once 专属通道，不进行同步）
    "wb_kb_sync_bases", "wb_kb_sync_files", "wb_kb_sync_chunks",
}
ID_STRIDE = 1_000_000_000  # 每客户端 ID 区间宽度
PUSH_BATCH = 200
PULL_BATCH = 500
PENDING_RETRIES = 20  # 拉取套用失败重试上限（如唯一键冲突）

# 无 FK 约束但语义上引用其他表主键的列（统一平移，按引用表同幅平移保持一致）
PLAIN_REFS: dict[str, tuple[str, ...]] = {
    "wb_attachments": ("owner_id",),
    "wb_bug_links": ("link_id",),
    "wb_execution_records": ("case_id", "bug_id"),
    "wb_execution_plans": ("requirement_id",),
    "wb_test_cases": ("api_request_id",),
    "wb_api_scenario_steps": ("ref_id",),
    "wb_api_request_history": ("request_id", "environment_id"),
    "wb_api_scenarios": ("environment_id",),
    "wb_regression_schedules": ("environment_id",),
}


def _synced_tables() -> dict[str, "object"]:
    """参与同步的 wb_ 业务表：表名 -> Table（外键依赖排序，父表在前）。"""
    md = wb.Base.metadata
    return {
        t.name: t for t in md.sorted_tables
        if t.name.startswith("wb_") and t.name not in _EXCLUDED
    }


_TABLE_ORDER: list[str] = list(_synced_tables().keys())


def _table_order(name: str) -> int:
    try:
        return _TABLE_ORDER.index(name)
    except ValueError:
        return 9999


def _client_active() -> bool:
    """同步引擎已激活（客户端与云服务器都需要：ID 分配与级联墓碑两端共用）。"""
    return _ACTIVE


_ACTIVE = False


# 本地同步配置（存 wb_sync_state，不随行同步扩散） ----------------------------

_state_lock = threading.RLock()  # 可重入：get_state/set_state/status_snapshot 存在嵌套调用


def _state_get(db, key: str) -> str | None:
    r = db.execute(
        text("SELECT state_value FROM wb_sync_state WHERE state_key = :k"), {"k": key}
    ).first()
    return r[0] if r else None


def _is_mysql(db) -> bool:
    try:
        return db.bind.dialect.name == "mysql"
    except Exception:
        return False


def _state_set(db, key: str, value: str | None) -> None:
    if value is None:
        db.execute(text("DELETE FROM wb_sync_state WHERE state_key = :k"), {"k": key})
        return
    if _is_mysql(db):
        db.execute(
            text("INSERT INTO wb_sync_state(state_key, state_value) VALUES(:k, :v) "
                 "ON DUPLICATE KEY UPDATE state_value = VALUES(state_value)"),
            {"k": key, "v": value})
    else:
        db.execute(
            text("INSERT OR REPLACE INTO wb_sync_state(state_key, state_value) VALUES(:k, :v)"),
            {"k": key, "v": value})


def get_state(key: str, default: str | None = None) -> str | None:
    from models.database import create_session
    with _state_lock, create_session() as db:
        v = _state_get(db, key)
        return v if v is not None else default


def set_state(key: str, value: str | None) -> None:
    from models.database import create_session
    with _state_lock, create_session() as db:
        _state_set(db, key, value)
        db.commit()


def sync_enabled() -> bool:
    if IS_CLOUD:
        return False
    return (get_state("enabled", "") or "") == "1" and _ACTIVE


# 运行状态 ---------------------------------------------------------------------

_status = {
    "online": False,
    "last_sync_at": None,
    "last_error": None,
    "syncing": False,
}
_sync_now = threading.Event()
_hash_cache: dict[str, tuple[float, int, str]] = {}


def status_snapshot() -> dict:
    from models.database import create_session
    with _state_lock, create_session() as db:
        pending = db.execute(
            text("SELECT COUNT(*) FROM wb_sync_rowver WHERE dirty = 1")).scalar() or 0
        return {
            "role": "cloud" if IS_CLOUD else "client",
            "enabled": (_state_get(db, "enabled") or "") == "1" and _ACTIVE,
            "cloud_url": _state_get(db, "cloud_url") or "",
            "client_id": _state_get(db, "client_id") or "",
            "client_name": _state_get(db, "client_name") or "",
            "account": _state_get(db, "account") or "",
            "logged_in": bool(_state_get(db, "account")),
            "ordinal": int(_state_get(db, "ordinal") or 0),
            "pending_push": int(pending),
            "online": bool(_status["online"]),
            "syncing": bool(_status["syncing"]),
            "last_sync_at": _status["last_sync_at"],
            "last_error": _status["last_error"],
        }


# 触发器（客户端 SQLite / 服务器 MySQL） ---------------------------------------

_APPLY_FLAG_DDL_SQLITE = (
    "CREATE TABLE IF NOT EXISTS wb_sync_apply_flag (id INTEGER PRIMARY KEY CHECK (id = 1))")

# 行版本时间戳统一为纪元微秒整数（跨精度字符串比较不可靠）
_STAMP_EXPR_SQLITE = "CAST((julianday('now') - 2440587.5) * 86400000000 AS INTEGER)"
_STAMP_EXPR_MYSQL = "CAST(ROUND(UNIX_TIMESTAMP(NOW(6)) * 1000000) AS SIGNED)"


@contextmanager
def apply_bypass(conn):
    """在连接上临时关闭行版本触发器（拉取套用/推送落库用），退出时恢复。"""
    if conn.dialect.name == "sqlite":
        conn.execute(text("INSERT OR IGNORE INTO wb_sync_apply_flag(id) VALUES (1)"))
        try:
            yield
        finally:
            conn.execute(text("DELETE FROM wb_sync_apply_flag"))
    else:
        conn.execute(text("SET @sync_bypass = 1"))
        try:
            yield
        finally:
            conn.execute(text("SET @sync_bypass = NULL"))


def _sqlite_triggers_for(t, client_id: str) -> list[str]:
    name = t.name
    stamp = _STAMP_EXPR_SQLITE
    cid = client_id.replace("'", "")
    upsert = ("INSERT INTO wb_sync_rowver(table_name, row_pk, stamp, client_id, deleted, dirty) "
              "VALUES('{name}', {ref}, {stamp}, '{cid}', {del_}, 1) "
              "ON CONFLICT(table_name, row_pk) DO UPDATE SET stamp = excluded.stamp, "
              "deleted = {del_}, dirty = 1")
    return [
        f"""CREATE TRIGGER IF NOT EXISTS wb_sync_i_{name} AFTER INSERT ON `{name}`
WHEN (SELECT COUNT(*) FROM wb_sync_apply_flag) = 0
BEGIN
    {upsert.format(name=name, ref='NEW.id', stamp=stamp, cid=cid, del_=0)};
END""",
        f"""CREATE TRIGGER IF NOT EXISTS wb_sync_u_{name} AFTER UPDATE ON `{name}`
WHEN (SELECT COUNT(*) FROM wb_sync_apply_flag) = 0
BEGIN
    {upsert.format(name=name, ref='NEW.id', stamp=stamp, cid=cid, del_=0)};
END""",
        f"""CREATE TRIGGER IF NOT EXISTS wb_sync_d_{name} AFTER DELETE ON `{name}`
WHEN (SELECT COUNT(*) FROM wb_sync_apply_flag) = 0
BEGIN
    {upsert.format(name=name, ref='OLD.id', stamp=stamp, cid=cid, del_=1)};
END""",
    ]


def _mysql_trigger_sql(name: str, mode: str, client_mode: bool) -> str:
    kw = {"i": "INSERT", "u": "UPDATE", "d": "DELETE"}[mode]
    ref = "OLD.id" if mode == "d" else "NEW.id"
    del_ = 1 if mode == "d" else 0
    # 服务器模式：dirty=0（写入即进游标）；客户端模式：dirty=1（待推送），client_id 取自身状态
    dirty = 1 if client_mode else 0
    cid_sql = ("(SELECT state_value FROM wb_sync_state WHERE state_key = 'client_id')"
               if client_mode else "'server'")
    return f"""CREATE TRIGGER wb_sync_{mode}_{name} AFTER {kw} ON `{name}`
FOR EACH ROW BEGIN
    IF @sync_bypass IS NULL THEN
        INSERT INTO wb_sync_rowver(table_name, row_pk, stamp, client_id, deleted, dirty)
        VALUES('{name}', {ref}, {_STAMP_EXPR_MYSQL}, {cid_sql}, {del_}, {dirty})
        ON DUPLICATE KEY UPDATE stamp = {_STAMP_EXPR_MYSQL}, client_id = {cid_sql},
            deleted = {del_}, dirty = {dirty};
    END IF;
END"""


def install_triggers() -> None:
    """按方言安装行版本触发器（幂等）。客户端在启动/启用时调用，服务器启动时调用。"""
    eng = get_engine()
    client_mode = not IS_CLOUD
    if eng.dialect.name == "sqlite":
        client_id = get_state("client_id", "") or "local"
        with eng.begin() as conn:
            conn.execute(text(_APPLY_FLAG_DDL_SQLITE))
            for t in _synced_tables().values():
                for ddl in _sqlite_triggers_for(t, client_id):
                    conn.execute(text(ddl))
        logger.info("同步触发器已安装（sqlite，%d 表）", len(_synced_tables()))
    elif eng.dialect.name == "mysql":
        from sqlalchemy import inspect
        cur_mode = "client" if client_mode else "server"
        # 模式不符（如先用错误模式装过）则全部重建
        if (get_state("trigger_mode", "") or "") and (get_state("trigger_mode", "") or "") != cur_mode:
            with eng.begin() as conn:
                for r in conn.execute(text(
                        "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                        "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME LIKE 'wb_sync_%'")).fetchall():
                    conn.execute(text(f"DROP TRIGGER `{r[0]}`"))
            logger.info("检测到触发器模式不符，已重建为 %s 模式", cur_mode)
        set_state("trigger_mode", cur_mode)
        insp = inspect(eng)
        existing = set()
        with eng.connect() as conn:
            for r in conn.execute(text(
                    "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
                    "WHERE TRIGGER_SCHEMA = DATABASE()")):
                existing.add(r[0])
        created = 0
        with eng.begin() as conn:
            for t in _synced_tables().values():
                for mode in ("i", "u", "d"):
                    tn = f"wb_sync_{mode}_{t.name}"
                    if tn in existing:
                        continue
                    conn.execute(text(_mysql_trigger_sql(t.name, mode, client_mode)))
                    created += 1
        logger.info("同步触发器已安装（mysql，%s，新建 %d 个）",
                    "客户端" if client_mode else "服务器", created)


# 客户端 ID 区间分配（同一 flush 连接上原子递增，避免跨连接写锁） ----------------


@event.listens_for(Mapper, "before_insert")
def _before_insert_assign_id(mapper, connection, target):
    """统一 ID 分配：客户端用 ordinal*1e9 区间；服务器 ordinal=0 占小区间。

    避免客户端推送的显式大 ID 抬高服务器自增计数器后反撞客户端区间。"""
    if not _ACTIVE:
        return
    table = target.__table__
    if not table.name.startswith("wb_") or table.name in _EXCLUDED:
        return
    if getattr(target, "id", None) is not None:
        return  # 拉取套用/显式指定的 ID 不重新分配
    try:
        ordinal_row = connection.execute(text(
            "SELECT state_value FROM wb_sync_state WHERE state_key = 'ordinal'")).first()
        is_mysql = connection.dialect.name == "mysql"
        bump = ("CAST(CAST(state_value AS SIGNED) + 1 AS CHAR)" if is_mysql
                else "CAST(CAST(state_value AS INTEGER) + 1 AS TEXT)")
        connection.execute(text(
            f"UPDATE wb_sync_state SET state_value = {bump} WHERE state_key = 'id_seq'"))
        seq_row = connection.execute(text(
            "SELECT state_value FROM wb_sync_state WHERE state_key = 'id_seq'")).first()
        if not seq_row:
            return
        ordinal = int(ordinal_row[0] or 0) if ordinal_row else 0
        target.id = ordinal * ID_STRIDE + int(seq_row[0])
    except Exception:
        logger.exception("同步 ID 分配失败（%s），回退自增", table.name)


# 级联受害行收集（DB 级联删除/置空对事件不可见，需显式补墓碑/脏标记） ------------


def _fk_children() -> dict[str, list[tuple[str, str, str]]]:
    """parent_table -> [(child_table, child_fk_col, ondelete)]"""
    graph: dict[str, list[tuple[str, str, str]]] = {}
    for t in wb.Base.metadata.sorted_tables:
        for fk in t.foreign_keys:
            parent = fk.constraint.referred_table.name
            graph.setdefault(parent, []).append(
                (t.name, fk.parent.name, (fk.constraint.ondelete or "").upper()))
    return graph


def _collect_cascade_victims(conn, deleted: list[tuple[str, int]]) -> tuple[list, list]:
    """对将删除的 (table, pk) 列表沿 FK 图收集：
    - hard: 会被 DB 级联删除的子行 —— 删除后补墓碑
    - null: 会被 ON DELETE SET NULL 置空的子行 —— 删除后补脏标记
    必须在执行删除之前调用。"""
    graph = _fk_children()
    hard: list[tuple[str, int]] = []
    null: list[tuple[str, int]] = []
    seen = set(deleted)
    queue = list(deleted)
    while queue:
        ptable, ppk = queue.pop(0)
        for ctable, ccol, ondel in graph.get(ptable, []):
            if ctable in _EXCLUDED:
                continue
            try:
                rows = conn.execute(text(
                    f"SELECT id FROM `{ctable}` WHERE `{ccol}` = :v"), {"v": ppk}).fetchall()
            except Exception:
                continue
            for (cid,) in rows:
                key = (ctable, cid)
                if key in seen:
                    continue
                seen.add(key)
                if ondel == "SET NULL":
                    null.append(key)
                else:
                    hard.append(key)
                    queue.append(key)
    return hard, null


def _stamp_now() -> int:
    return time.time_ns() // 1000


def _rv_upsert_sql(conn_or_db=None) -> str:
    """行版本 upsert，按方言分支（conn_or_db 为空时查全局引擎）。"""
    try:
        dia = (conn_or_db or get_engine()).dialect.name
    except Exception:
        dia = "sqlite"
    if dia == "mysql":
        return ("INSERT INTO wb_sync_rowver(table_name, row_pk, stamp, client_id, deleted, dirty) "
                "VALUES(:t, :p, :s, :c, :d, :dirty) "
                "ON DUPLICATE KEY UPDATE stamp = VALUES(stamp), deleted = VALUES(deleted), "
                "dirty = VALUES(dirty)")
    return ("INSERT INTO wb_sync_rowver(table_name, row_pk, stamp, client_id, deleted, dirty) "
            "VALUES(:t, :p, :s, :c, :d, :dirty) "
            "ON CONFLICT(table_name, row_pk) DO UPDATE SET stamp = :s, deleted = :d, dirty = :dirty")


def _rv_write(conn, tname: str, pk: int, stamp: str, client_id: str,
              deleted: bool, dirty: bool) -> None:
    conn.execute(text(_rv_upsert_sql(conn)),
                 {"t": tname, "p": pk, "s": stamp, "c": client_id,
                  "d": 1 if deleted else 0, "dirty": 1 if dirty else 0})


def _apply_cascade_aftermath(conn, hard, null, stamp: str, client_id: str,
                             dirty: bool = True) -> None:
    """删除落库后：硬删子行补墓碑，SET NULL 子行补脏标记。"""
    for ctable, cid in hard:
        _rv_write(conn, ctable, cid, stamp, client_id, deleted=True, dirty=dirty)
    for ctable, cid in null:
        _rv_write(conn, ctable, cid, stamp, client_id, deleted=False, dirty=dirty)


# 客户端：ORM 删除前后钩子 -----------------------------------------------------

_pending_cascades: dict[int, tuple[list, list]] = {}


@event.listens_for(Session, "before_flush")
def _before_flush_collect(session, flush_context, instances):
    if not _ACTIVE or not session.deleted:
        return
    try:
        conn = session.connection()
        victims = []
        for obj in session.deleted:
            t = obj.__table__.name
            if t.startswith("wb_") and t not in _EXCLUDED:
                victims.append((t, obj.id))
        if victims:
            _pending_cascades[id(session)] = _collect_cascade_victims(conn, victims)
    except Exception:
        logger.exception("收集级联受害行失败")


@event.listens_for(Session, "after_flush")
def _after_flush_tombstone(session, flush_context):
    pending = _pending_cascades.pop(id(session), None)
    if not pending or not _ACTIVE:
        return
    if not pending[0] and not pending[1]:
        return  # 无级联受害行：直接跳过（避免无谓的状态读取）
    try:
        conn = session.connection()
        stamp = _stamp_now()
        # 必须复用当前 session 读状态：另开新会话会在 BEGIN IMMEDIATE 模式下
        # 撞上本事务持有的写锁，busy_timeout 等满 30s（曾致所有删除操作耗时 32 秒）
        try:
            client_id = ("server" if IS_CLOUD
                         else (_state_get(session, "client_id") or "local"))
        except Exception:
            client_id = "local"  # wb_sync_state 尚未创建（同步从未启用）
        _apply_cascade_aftermath(conn, pending[0], pending[1], stamp, client_id,
                                 dirty=not IS_CLOUD)
    except Exception:
        logger.exception("补级联墓碑失败")


# 序列化与类型回写 -------------------------------------------------------------

_JSON_TYPES = (dict, list)


def _row_to_payload(t, dbrow) -> dict:
    out = {}
    for c in t.columns:
        v = getattr(dbrow, c.name, None)
        if isinstance(v, datetime):
            v = v.strftime("%Y-%m-%d %H:%M:%S.%f")
        elif isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, _JSON_TYPES):
            v = json.loads(json.dumps(v, ensure_ascii=False, default=str))
        out[c.name] = v
    return out


def _coerce_value(c, v):
    if v is None:
        return None
    # JSON 列经裸 SQL（text()）绑定时无列类型信息：dict/list 必须 json 序列化，
    # 否则 sqlite3 报 "type 'dict' is not supported"（MySQL JSON 列同样接受 JSON 字符串）
    if isinstance(v, (dict, list)):
        return json.dumps(v, ensure_ascii=False)
    try:
        py = c.type.python_type
    except Exception:
        return v
    if py is datetime and isinstance(v, str):
        return datetime.fromisoformat(v)
    if py is Decimal and not isinstance(v, Decimal):
        return Decimal(str(v))
    if py is int and isinstance(v, str):
        return int(v)
    if py is bool and isinstance(v, int):
        return bool(v)
    return v


def _upsert_row(conn, t, data: dict) -> None:
    """按主键 upsert 一行（调用方负责 bypass；触发器不重复记版本）。"""
    pk = data.get("id")
    values = {c.name: _coerce_value(c, data.get(c.name)) for c in t.columns if c.name in data}
    values["id"] = pk
    cols = [c.name for c in t.columns if c.name in values]
    exists = conn.execute(text(f"SELECT 1 FROM `{t.name}` WHERE id = :p"), {"p": pk}).first()
    if exists:
        assigns = ", ".join(f"`{c}` = :{c}" for c in cols if c != "id")
        conn.execute(text(f"UPDATE `{t.name}` SET {assigns} WHERE id = :id"),
                     {c: values[c] for c in cols})
    else:
        ph = ", ".join(f":{c}" for c in cols)
        collist = ", ".join(f"`{c}`" for c in cols)
        conn.execute(text(f"INSERT INTO `{t.name}` ({collist}) VALUES ({ph})"),
                     {c: values[c] for c in cols})


def _parse_stamp(s) -> int:
    """时间戳统一为纪元微秒整数。"""
    if isinstance(s, int):
        return s
    if isinstance(s, datetime):
        return int(s.timestamp() * 1_000_000)
    s = str(s).strip()
    if s.replace("-", "").isdigit() or s.isdigit():
        return int(s)
    return int(datetime.fromisoformat(s).timestamp() * 1_000_000)


# HTTP 客户端（urllib，冻结环境零依赖） -----------------------------------------


def _http_json(method: str, url: str, payload: dict | None = None,
               timeout: int = 15, raw_body: bytes | None = None) -> dict:
    import urllib.error
    import urllib.request

    headers = {"X-Sync-Token": get_state("token", "") or "",
               "X-Sync-Client": get_state("client_id", "") or ""}
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        headers["Content-Type"] = "application/json"
    elif raw_body is not None:
        data = raw_body
        headers["Content-Type"] = "application/octet-stream"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body.decode("utf-8")) if body else {}
    except urllib.error.HTTPError as e:
        detail = ""
        try:
            detail = e.read().decode("utf-8")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {e.code}: {detail}") from e


def _cloud_url(path: str) -> str:
    return (get_state("cloud_url", "") or "").rstrip("/") + path


# 启用同步：注册 + 存量迁移 -----------------------------------------------------


class _RawAutocommit:
    """底层 DBAPI 原生连接（AUTOCOMMIT），绕开 SQLAlchemy 事务管理。

    SQLite 引擎装了 begin 事件（BEGIN IMMEDIATE）：经 SQLAlchemy 执行的语句都
    会被包进写事务，而 SQLite 规定 PRAGMA foreign_keys 不能在事务内修改
    （静默 no-op），导致迁移/清库时外键关不掉直接 IntegrityError。
    批量迁移、清库必须走原生连接逐语句自动提交。"""

    def __init__(self, eng):
        proxy = eng.raw_connection()
        self._proxy = proxy
        self.raw = getattr(proxy, "driver_connection", proxy)
        self.is_mysql = eng.dialect.name == "mysql"
        if self.is_mysql:
            self.raw.autocommit(True)

    def execute(self, sql: str, args: dict | None = None):
        return self.raw.execute(sql, args or ())

    def close(self):
        try:
            if self.is_mysql:
                self.raw.autocommit(False)
        except Exception:
            pass
        self._proxy.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False


def _remap_existing_data(offset: int) -> None:
    """存量库一次性平移到本客户端 ID 区间（幂等：只动 < offset 的 ID）。

    用原生 AUTOCOMMIT 连接执行（SQLite 关外键用 PRAGMA；MySQL 用 FOREIGN_KEY_CHECKS）；
    中途中断可重跑续迁（已平移的行 ID >= offset 会被跳过）。
    迁移同时把存量行播种进 wb_sync_rowver（dirty=1），否则推送永远看不到存量数据。"""
    eng = get_engine()
    tables = [t for t in wb.Base.metadata.sorted_tables
              if t.name.startswith("wb_") and t.name not in _EXCLUDED]
    upload = Path(get_upload_dir()).resolve()
    stamp = _stamp_now()
    client_id = get_state("client_id", "") or "local"
    seed = ("INSERT IGNORE INTO" if eng.dialect.name == "mysql"
            else "INSERT OR IGNORE INTO")
    with _RawAutocommit(eng) as rc:
        if rc.is_mysql:
            rc.execute("SET FOREIGN_KEY_CHECKS=0")
        else:
            rc.execute("PRAGMA foreign_keys=OFF")
        for t in tables:
            rc.execute(f"UPDATE `{t.name}` SET id = id + :o WHERE id < :o", {"o": offset})
            for fk in t.foreign_keys:
                col = fk.parent.name
                rc.execute(f"UPDATE `{t.name}` SET `{col}` = `{col}` + :o "
                           f"WHERE `{col}` IS NOT NULL AND `{col}` < :o", {"o": offset})
            for col in PLAIN_REFS.get(t.name, ()):  # 无约束的语义引用列同幅平移
                rc.execute(f"UPDATE `{t.name}` SET `{col}` = `{col}` + :o "
                           f"WHERE `{col}` IS NOT NULL AND `{col}` < :o", {"o": offset})
            # 播种存量行为待推送
            rc.execute(
                f"{seed} wb_sync_rowver(table_name, row_pk, stamp, client_id, deleted, dirty) "
                f"SELECT '{t.name}', id, :st, :cid, 0, 1 FROM `{t.name}`",
                {"st": stamp, "cid": client_id})
        if rc.is_mysql:
            rc.execute("SET FOREIGN_KEY_CHECKS=1")
        else:
            rc.execute("PRAGMA foreign_keys=ON")
    # auto_runs 目录随运行 ID 平移重命名，附件绝对路径同步改写
    runs_dir = upload / "auto_runs"
    if runs_dir.exists():
        for d in list(runs_dir.iterdir()):
            if d.is_dir() and d.name.isdigit() and int(d.name) < offset:
                new = runs_dir / str(int(d.name) + offset)
                if not new.exists():
                    d.rename(new)
        atts = []
        from models.database import create_session
        with create_session() as db:
            atts = db.execute(text(
                "SELECT id, file_path FROM wb_attachments WHERE file_path LIKE '%auto_runs%'"
            )).fetchall()
        new_names = {int(d.name) for d in runs_dir.iterdir()
                     if d.is_dir() and d.name.isdigit() and int(d.name) >= offset}
        with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
            for aid, fp in atts:
                new_fp = fp
                for new_id in new_names:
                    old_id = new_id - offset
                    for sep in ("/", "\\"):
                        new_fp = new_fp.replace(
                            f"auto_runs{sep}{old_id}{sep}", f"auto_runs{sep}{new_id}{sep}")
                if new_fp != fp:
                    conn.execute(text("UPDATE wb_attachments SET file_path = :p WHERE id = :i"),
                                 {"p": new_fp, "i": aid})
    logger.info("存量数据已平移到客户端区间 +%d", offset)


def enable_sync(cloud_url: str, token: str, client_name: str,
                username: str | None = None) -> dict:
    """启用同步：注册 → 存量平移（一次性）→ 初始化 ID 序列 → 装触发器 → 启线程。"""
    global _ACTIVE
    cloud_url = (cloud_url or "").strip().rstrip("/")
    token = (token or "").strip()
    if not cloud_url or not token:
        raise ValueError("服务器地址与访问令牌不能为空")

    # 先落令牌/地址（注册请求自身要带令牌鉴权）
    for k, v in {"cloud_url": cloud_url, "token": token,
                 "client_name": client_name or "客户端"}.items():
        set_state(k, v)
    if username:
        set_state("account", username)
    else:
        set_state("account", None)

    reg = _http_json("POST", cloud_url + "/wb/sync/register",
                     {"client_name": client_name or "客户端"})
    client_id, ordinal = reg["client_id"], int(reg["ordinal"])
    offset = ordinal * ID_STRIDE

    remapped = (get_state("remapped", "") or "") == "1"
    if not remapped:
        _remap_existing_data(offset)
        max_rel = 1
        from models.database import create_session
        with create_session() as db:
            for t in _synced_tables().values():
                mx = db.execute(text(f"SELECT MAX(id) FROM `{t.name}` WHERE id < :o"),
                                {"o": offset}).scalar()
                if mx:
                    max_rel = max(max_rel, mx - offset + 1)
        for k, v in {
            "client_id": client_id, "ordinal": str(ordinal),
            "id_seq": str(max_rel), "enabled": "1", "remapped": "1",
            "pull_stamp": "0", "pull_id": "0", "pull_pending": "[]",
        }.items():
            set_state(k, v)
    else:
        set_state("client_id", client_id)
        set_state("enabled", "1")

    _ACTIVE = True
    install_triggers()
    _start_worker()
    _sync_now.set()
    return status_snapshot()


def disable_sync() -> dict:
    set_state("enabled", "0")
    _stop_worker()
    return status_snapshot()


# 账户会话（云同步登录/退出）---------------------------------------------------

def _uninstall_triggers() -> None:
    """卸载行版本触发器（退出登录时防止清库动作被登记成墓碑）。"""
    eng = get_engine()
    with eng.begin() as conn:
        for tname in _synced_tables():
            for mode in ("i", "u", "d"):
                conn.execute(text(f"DROP TRIGGER IF EXISTS wb_sync_{mode}_{tname}"))
    logger.info("同步触发器已卸载")


def local_business_count() -> int:
    """本地业务行总数。未登录时 >0 表示存在「本地临时数据」。"""
    eng = get_engine()
    with eng.connect() as conn:
        total = 0
        for t in _synced_tables().values():
            total += conn.execute(text(f"SELECT COUNT(*) FROM `{t.name}`")).scalar() or 0
        return total


def _temp_backup_path() -> Path:
    return Path(get_upload_dir()).resolve().parent / "temp_backup.db"


def stash_temp_data() -> bool:
    """把本地临时数据整体转入备份库隐藏（登录选「不归纳」时调用）。

    复制到备份库后从主库删除（防触发器干扰：此时同步尚未启用、无触发器）；
    备份库只保留最近一次；返回是否存在临时数据（已转入）。"""
    if local_business_count() == 0:
        return False
    from sqlalchemy import create_engine as _ce
    backup = _temp_backup_path()
    if backup.exists():
        backup.unlink()
    tables = list(_synced_tables().values())  # 已按外键依赖排序（父表在前）
    beng = _ce(f"sqlite:///{backup}")
    wb.Base.metadata.create_all(beng, tables=tables)
    eng = get_engine()
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as main, \
            beng.begin() as dst:
        # 先复制（父表在前），再从主库删除（子表在前）
        for t in tables:
            rows = main.execute(t.select()).fetchall()
            if rows:
                dst.execute(t.insert(), [dict(r._mapping) for r in rows])
        for t in reversed(tables):
            main.execute(text(f"DELETE FROM `{t.name}`"))
        main.execute(text("DELETE FROM wb_sync_rowver"))
    beng.dispose()
    logger.info("本地临时数据已转入备份库并从主库移除：%s", backup)
    return True


def _restore_temp_data() -> bool:
    """退出登录时把备份库中的临时数据恢复到主库。返回是否恢复了数据。"""
    backup = _temp_backup_path()
    if not backup.exists():
        return False
    from sqlalchemy import create_engine as _ce
    tables = list(_synced_tables().values())
    beng = _ce(f"sqlite:///{backup}")
    eng = get_engine()
    try:
        with beng.connect() as src, _RawAutocommit(eng) as dst:
            if dst.is_mysql:
                dst.execute("SET FOREIGN_KEY_CHECKS=0")
            else:
                dst.execute("PRAGMA foreign_keys=OFF")
            for t in reversed(tables):
                dst.execute(f"DELETE FROM `{t.name}`")
            for t in tables:
                rows = src.execute(t.select()).fetchall()
                if rows:
                    dst.execute(t.insert(), [dict(r._mapping) for r in rows])
        return True
    finally:
        beng.dispose()
        try:
            backup.unlink()  # 恢复后删除备份
        except OSError:
            pass


def logout_and_clear() -> dict:
    """退出登录：停同步 → 卸触发器 → 清空账户数据与同步状态 → 恢复本地临时数据（若有）。

    清空账户数据后 UI 自然不再展示该账号内容（数据仍在云端，重新登录即拉回）。"""
    global _ACTIVE
    disable_sync()
    _ACTIVE = False
    _uninstall_triggers()
    eng = get_engine()
    with _RawAutocommit(eng) as rc:
        if rc.is_mysql:
            rc.execute("SET FOREIGN_KEY_CHECKS=0")
        else:
            rc.execute("PRAGMA foreign_keys=OFF")
        for t in reversed(list(_synced_tables().values())):
            rc.execute(f"DELETE FROM `{t.name}`")
        for extra in ("wb_sync_rowver", "wb_sync_files", "wb_sync_state"):
            rc.execute(f"DELETE FROM `{extra}`")
    restored = _restore_temp_data()
    _status.update({"online": False, "last_sync_at": None,
                    "last_error": None, "syncing": False})
    logger.info("已退出登录并清空本地账户数据（恢复临时数据：%s）", restored)
    return {"ok": True, "restored_temp": restored}


# 推拉核心 ---------------------------------------------------------------------


def _push_once() -> int:
    """把 dirty 行推给服务器，返回已处理条数。"""
    tables = _synced_tables()
    eng = get_engine()
    pushed = 0
    while True:
        rows = []
        with eng.begin() as conn:
            rvs = conn.execute(text(
                "SELECT table_name, row_pk, stamp, deleted FROM wb_sync_rowver "
                "WHERE dirty = 1 ORDER BY stamp LIMIT :n"), {"n": PUSH_BATCH}).fetchall()
            if not rvs:
                break
            stale = []
            for tname, pk, stamp, deleted in rvs:
                t = tables.get(tname)
                if t is None:  # 未知表（旧触发器残留）：直接清 dirty
                    stale.append((tname, pk))
                    continue
                data = None
                is_del = bool(deleted)
                if not is_del:
                    r = conn.execute(t.select().where(t.c.id == pk)).first()
                    if r is None:  # 行没了但无墓碑记录：按删除处理
                        is_del = True
                    else:
                        data = _row_to_payload(t, r)
                rows.append({"table": tname, "pk": pk, "op": "d" if is_del else "u",
                             "stamp": stamp, "data": data})
            for tname, pk in stale:
                conn.execute(text(
                    "UPDATE wb_sync_rowver SET dirty = 0 WHERE table_name = :t AND row_pk = :p"),
                    {"t": tname, "p": pk})
        resp = _http_json("POST", _cloud_url("/wb/sync/push"), {"rows": rows})
        done = {f"{a['table']}:{a['pk']}" for a in resp.get("accepted", [])}
        done |= {f"{a['table']}:{a['pk']}" for a in resp.get("rejected", [])}
        with eng.begin() as conn:
            for tname, pk, *_ in rvs:
                if f"{tname}:{pk}" in done:
                    conn.execute(text(
                        "UPDATE wb_sync_rowver SET dirty = 0 WHERE table_name = :t AND row_pk = :p"),
                        {"t": tname, "p": pk})
        pushed += len(done)
        if len(rvs) < PUSH_BATCH:
            break
    return pushed


def _apply_remote_row(conn, r: dict) -> None:
    """在 bypass 开启的连接上套用一条远端变更（LWW：incoming > 本地才动）。"""
    t = _synced_tables().get(r["table"])
    if t is None:
        return
    inc = _parse_stamp(r["stamp"])
    local = conn.execute(text(
        "SELECT stamp FROM wb_sync_rowver WHERE table_name = :t AND row_pk = :p"),
        {"t": r["table"], "p": r["pk"]}).first()
    if local and _parse_stamp(local[0]) >= inc:
        return  # 本地已同等或更新（含自家推送回声）
    if r.get("deleted"):
        hard, null = _collect_cascade_victims(conn, [(r["table"], r["pk"])])
        conn.execute(text(f"DELETE FROM `{t.name}` WHERE id = :p"), {"p": r["pk"]})
        _rv_write(conn, r["table"], r["pk"], r["stamp"], "remote", deleted=True, dirty=False)
        _apply_cascade_aftermath(conn, hard, null, r["stamp"], "remote")
    else:
        _upsert_row(conn, t, r.get("data") or {})
        _rv_write(conn, r["table"], r["pk"], r["stamp"], "remote", deleted=False, dirty=False)


def _pull_once() -> int:
    """按游标拉取服务器变更并套用，返回套用条数。

    FK 依赖未就绪/唯一键冲突的行进 pull_pending 队列，随下轮重试，
    超过重试上限则丢弃并记日志（不阻塞游标推进）。"""
    eng = get_engine()
    applied = 0
    cur_stamp = int(get_state("pull_stamp", "0") or 0)
    cur_id = int(get_state("pull_id", "0") or 0)
    try:
        pending = json.loads(get_state("pull_pending", "") or "[]")
    except Exception:
        pending = []
    for _round in range(1000):
        resp = _http_json("GET", _cloud_url(
            f"/wb/sync/pull?cursor_stamp={cur_stamp}&cursor_id={cur_id}&limit={PULL_BATCH}"))
        rows = list(resp.get("rows", []))
        if not rows and not pending:
            break
        if resp.get("cursor_stamp") is not None:
            cur_stamp = int(resp["cursor_stamp"] or 0)
            cur_id = int(resp.get("cursor_id") or cur_id)
        todo = pending + rows
        still_pending = []
        with eng.begin() as conn:
            # bypass：拉取套用不经触发器（不产生待推送脏标记回灌）
            with apply_bypass(conn):
                # 父表先套用（sorted_tables 顺序），失败行进重试队列
                for r in sorted(todo, key=lambda x: (_table_order(x.get("table", "")),
                                                     _parse_stamp(x.get("stamp", 0)))):
                    try:
                        retries = int(r.get("_retries") or 0)
                        if retries >= PENDING_RETRIES:
                            logger.warning("同步行重试超限，丢弃：%s#%s",
                                           r.get("table"), r.get("pk"))
                            continue
                        _apply_remote_row(conn, r)
                        applied += 1
                    except Exception as ex:
                        nr = {k: v for k, v in r.items() if not k.startswith("_")}
                        nr["_retries"] = int(r.get("_retries") or 0) + 1
                        still_pending.append(nr)
                        logger.warning("套用同步行失败（将重试）：%s#%s %s",
                                       r.get("table"), r.get("pk"), ex)
        set_state("pull_stamp", str(cur_stamp))
        set_state("pull_id", str(cur_id))
        set_state("pull_pending", json.dumps(still_pending, ensure_ascii=False)[:100000])
        if not resp.get("has_more") or not rows:
            break
    return applied


# 文件同步 ---------------------------------------------------------------------


def _resolve_upload_file(file_path: str) -> Path | None:
    p = Path(file_path)
    if not p.is_absolute():
        p = Path(get_upload_dir()).resolve() / file_path
    return p if p.exists() else None


def _file_hash(path: Path) -> str:
    st = path.stat()
    key = str(path)
    cached = _hash_cache.get(key)
    if cached and cached[0] == st.st_mtime and cached[1] == st.st_size:
        return cached[2]
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    sha = h.hexdigest()
    _hash_cache[key] = (st.st_mtime, st.st_size, sha)
    return sha


def _local_file_inventory() -> dict[str, dict]:
    """本地应同步文件清单：附件 + auto_runs 产物。"""
    upload = Path(get_upload_dir()).resolve()
    inv: dict[str, dict] = {}
    from models.database import create_session
    with create_session() as db:
        atts = db.execute(text(
            "SELECT file_path FROM wb_attachments WHERE file_path IS NOT NULL")).fetchall()
    for (fp,) in atts:
        loc = _resolve_upload_file(fp)
        if not loc or not loc.is_file():
            continue
        try:
            rel = str(loc.relative_to(upload)).replace("\\", "/")
        except ValueError:
            continue  # 上传目录之外的文件不同步
        inv[rel] = {"sha256": _file_hash(loc), "size": loc.stat().st_size}
    runs_dir = upload / "auto_runs"
    if runs_dir.exists():
        for f in runs_dir.rglob("*"):
            if f.is_file():
                rel = str(f.relative_to(upload)).replace("\\", "/")
                inv[rel] = {"sha256": _file_hash(f), "size": f.stat().st_size}
    return inv


def _sync_files_once() -> tuple[int, int]:
    """双向对账：本地缺的下载、服务器缺的上传。返回 (上传数, 下载数)。"""
    import urllib.parse
    import urllib.request

    local = _local_file_inventory()
    remote = {f["rel_path"]: f
              for f in _http_json("GET", _cloud_url("/wb/sync/files/list")).get("files", [])}
    upload_root = Path(get_upload_dir()).resolve()
    up = down = 0
    for rel, meta in local.items():
        r = remote.get(rel)
        if r and r.get("sha256") == meta["sha256"]:
            continue
        body = (upload_root / rel).read_bytes()
        _http_json("POST", _cloud_url("/wb/sync/files/upload?path=" + urllib.parse.quote(rel)),
                   raw_body=body, timeout=300)
        up += 1
    for rel, meta in remote.items():
        if rel.startswith("kb_sync/"):
            continue  # 知识库专属 blob：由 _kb_sync_once 定向搬运，不进通用对账
        hit = local.get(rel)
        if hit and hit["sha256"] == meta["sha256"]:
            continue
        target = upload_root / rel
        if target.is_file() and _file_hash(target) == meta["sha256"]:
            continue
        req = urllib.request.Request(
            _cloud_url("/wb/sync/files/download?path=" + urllib.parse.quote(rel)),
            headers={"X-Sync-Token": get_state("token", "") or ""})
        with urllib.request.urlopen(req, timeout=300) as resp:
            data = resp.read()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        _hash_cache.pop(str(target), None)
        down += 1
    return up, down


# 知识库云同步（账号维度专属通道） ---------------------------------------------
# 元数据/文档记录/分块 → 云端 wb_kb_sync_* 表；原始文件与向量快照 → 云端
# uploads/kb_sync/<account>/ blob。删除经影子对账（kb_push_shadow/kb_pull_shadow）传播。

KB_MARKER_PREFIX = "workshop_project:"  # 项目知识库标记（与 wbapi._KB_MARKER_PREFIX 同源）


def _kb_blob_rel(account: str, name: str) -> str:
    return f"kb_sync/{account}/{name}"


def _kb_local_inventory() -> list[dict]:
    """本机全部知识库（含 completed 文件清单），推送/拉取共用。"""
    from models.database import create_session
    from models.knowledge_models import KnowledgeBase, KnowledgeFile

    with create_session() as db:
        kbs = db.query(KnowledgeBase).all()
        files = db.query(KnowledgeFile).filter(KnowledgeFile.status == "completed").all()
        out = []
        for kb in kbs:
            pid = None
            desc = kb.description or ""
            if desc.startswith(KB_MARKER_PREFIX):
                try:
                    pid = int(desc[len(KB_MARKER_PREFIX):])
                except ValueError:
                    pid = None
            out.append({
                "id": kb.id, "project_id": pid, "name": kb.name or "",
                "collection_name": kb.collection_name or "",
                "files": [{
                    "id": f.id, "base_id": kb.id, "filename": f.filename or "",
                    "file_path": f.file_path or "", "file_size": f.file_size or 0,
                    "file_type": f.file_type or "", "content_hash": f.content_hash,
                    "chunk_count": f.chunk_count or 0,
                    "skipped_chunks": f.skipped_chunks or 0,
                } for f in files if f.knowledge_base_id == kb.id],
            })
        return out


def _kb_vector_snapshot(collection_name: str) -> dict | None:
    """导出 Chroma 集合快照（向量随同步搬运，换设备免重算 embedding）。"""
    try:
        from utils.file_handle import get_chromadb_client

        col = get_chromadb_client().get_collection(name=collection_name)
        if not col.count():
            return None
        got = col.get(include=["documents", "embeddings", "metadatas"])
        embs = got.get("embeddings")
        if embs is None:
            embs = []
        metas = [{k: v for k, v in (m or {}).items() if v is not None}
                 for m in (got.get("metadatas") or [])]
        return {
            "collection": collection_name,
            "metadata": col.metadata or {},
            "ids": list(got.get("ids") or []),
            "documents": list(got.get("documents") or []),
            "metadatas": metas,
            "embeddings": [[float(x) for x in e] for e in embs],
        }
    except Exception:
        logger.exception("导出向量快照失败: %s", collection_name)
        return None


def _kb_rebuild_collection(snapshot: dict) -> None:
    """用快照重建本地 Chroma 集合（向量直灌，不调用 embedding API）。"""
    from utils.file_handle import get_chromadb_client

    client = get_chromadb_client()
    name = snapshot["collection"]
    try:
        client.delete_collection(name)
    except Exception:
        pass
    meta = {k: v for k, v in (snapshot.get("metadata") or {}).items() if v is not None}
    col = client.create_collection(name=name, metadata=meta or None)
    ids = snapshot["ids"]
    step = 500
    for i in range(0, len(ids), step):
        col.add(ids=ids[i:i + step],
                documents=snapshot["documents"][i:i + step],
                metadatas=snapshot["metadatas"][i:i + step],
                embeddings=snapshot["embeddings"][i:i + step])


def _kb_delete_local_file(db, fr) -> None:
    """删除本地知识文档：分块 + 向量条目 + 原始文件 + 记录行。"""
    import wbmodels as _wb

    db.query(_wb.WbKbChunk).filter(_wb.WbKbChunk.file_id == fr.id).delete()
    # 向量条目按元数据删除（集合可能不存在：无语义索引时静默跳过）
    try:
        from models.knowledge_models import KnowledgeBase
        from utils.file_handle import get_chromadb_client

        kb = db.get(KnowledgeBase, fr.knowledge_base_id)
        if kb and kb.collection_name:
            get_chromadb_client().get_collection(name=kb.collection_name).delete(
                where={"file_id": fr.id})
    except Exception:
        pass
    try:
        loc = _resolve_upload_file(fr.file_path) if fr.file_path else None
        if loc and loc.is_file():
            loc.unlink()
    except OSError:
        pass
    db.delete(fr)


def _kb_sync_once() -> None:
    """知识库账号维度双向同步（同步线程每轮调用；任何失败不影响行同步）。"""
    import urllib.parse
    import urllib.request

    from models.database import create_session
    from models.knowledge_models import KnowledgeBase, KnowledgeFile

    account = (get_state("account", "") or "").strip()
    if not account:
        return

    push_shadow = json.loads(get_state("kb_push_shadow", "") or "{}")
    pull_shadow = json.loads(get_state("kb_pull_shadow", "") or "{}")
    vec_state = json.loads(get_state("kb_vec_state", "") or "{}")

    manifest = _http_json("GET", _cloud_url(
        "/wb/kbsync/manifest?account=" + urllib.parse.quote(account)))
    cloud_files = {f["id"]: f for f in manifest.get("files", [])}
    cloud_by_base: dict[str, list[dict]] = {}
    for f in cloud_files.values():
        cloud_by_base.setdefault(f["base_id"], []).append(f)
    cloud_bases = {b["id"]: b for b in manifest.get("bases", [])}

    # ---------- 推送：本机知识库 → 云端 ----------
    locals_ = _kb_local_inventory()
    new_push_shadow: dict[str, dict] = {}
    for kb in locals_:
        present = {f["id"] for f in kb["files"]}
        deleted_ids = [fid for fid in push_shadow if fid not in present]
        if deleted_ids:  # 本机已删 → 云端删（含 blob）
            _http_json("POST", _cloud_url("/wb/kbsync/delete_files"), {"ids": deleted_ids})
            for fid in deleted_ids:
                push_shadow.pop(fid, None)
        changed = [f for f in kb["files"]
                   if push_shadow.get(f["id"], {}).get("h") != (f["content_hash"] or f["id"])]
        # 向量快照：有文件变化或从未上传过时导出；无变化沿用上次 sha。
        # 本机无向量时保持云端现状（不能把其他设备上传的 vector_sha 清掉）
        snap_body = None
        vec_sha = vec_state.get(kb["collection_name"])
        if kb["collection_name"] and (changed or vec_sha is None):
            snap = _kb_vector_snapshot(kb["collection_name"])
            if snap:
                snap_body = json.dumps(snap, ensure_ascii=False).encode("utf-8")
                vec_sha = hashlib.sha256(snap_body).hexdigest()
            else:
                vec_sha = cloud_bases.get(kb["id"], {}).get("vector_sha")
        _http_json("POST", _cloud_url("/wb/kbsync/upsert_base"), {
            "id": kb["id"], "account": account,
            "project_id": kb["project_id"] or 0, "name": kb["name"],
            "collection_name": kb["collection_name"],
            "file_count": len(kb["files"]), "vector_sha": vec_sha,
        })
        if snap_body and vec_sha:
            _http_json("POST", _cloud_url("/wb/sync/files/upload?path=" + urllib.parse.quote(
                           _kb_blob_rel(account, f"{kb['id']}.vectors.json"))),
                       raw_body=snap_body, timeout=300)
            vec_state[kb["collection_name"]] = vec_sha
        for f in changed:
            _http_json("POST", _cloud_url("/wb/kbsync/upsert_file"), dict(f))
            with create_session() as db:
                rows = db.execute(text(
                    "SELECT chunk_no, content FROM wb_kb_chunks WHERE file_id = :f "
                    "ORDER BY chunk_no"), {"f": f["id"]}).fetchall()
            _http_json("POST", _cloud_url("/wb/kbsync/chunks"), {
                "file_id": f["id"],
                "chunks": [{"no": n, "content": c} for n, c in rows]})
            src = _resolve_upload_file(f["file_path"]) if f["file_path"] else None
            if src and src.is_file():
                _http_json("POST", _cloud_url("/wb/sync/files/upload?path=" + urllib.parse.quote(
                               _kb_blob_rel(account, f["file_path"]))),
                           raw_body=src.read_bytes(), timeout=300)
        new_push_shadow.update({f["id"]: {"h": f["content_hash"] or f["id"]}
                                for f in kb["files"]})
    set_state("kb_push_shadow", json.dumps(new_push_shadow))
    set_state("kb_vec_state", json.dumps(vec_state))

    # ---------- 拉取：云端 → 本机（换设备恢复 / 他端变更套用） ----------
    gone = [fid for fid in pull_shadow if fid not in cloud_files]
    if gone:  # 云端已删除（他端删的）→ 本地跟随删除
        with create_session() as db:
            for fid in gone:
                fr = db.get(KnowledgeFile, fid)
                if fr is not None:
                    _kb_delete_local_file(db, fr)
            db.commit()
    upload_root = Path(get_upload_dir()).resolve()
    with create_session() as db:
        local_files = {f.id: (f.content_hash or f.id) for f in db.query(KnowledgeFile).all()}
    restored = 0
    for b in manifest.get("bases", []):
        pid = b.get("project_id")
        # 解析本地 KB：优先同 id（换设备恢复），其次项目标记（双端各自建过库）
        kb = None
        with create_session() as db:
            kb = db.get(KnowledgeBase, b["id"])
            if kb is None and pid:
                kb = db.query(KnowledgeBase).filter(
                    KnowledgeBase.description == f"{KB_MARKER_PREFIX}{pid}").first()
            if kb is None:
                if not pid:
                    continue
                # 项目必须已随行同步到位（否则下轮重试），KB 用云端身份重建
                import wbmodels as _wb
                if db.get(_wb.WbProject, pid) is None:
                    continue
                kb = KnowledgeBase(
                    id=b["id"], name=b.get("name") or "知识库",
                    description=f"{KB_MARKER_PREFIX}{pid}",
                    collection_name=b.get("collection_name") or f"wbkb_{b['id'][:16]}",
                    owner_user_id=None, visibility="private")
                db.add(kb)
                db.commit()
        kb_id, collection = kb.id, kb.collection_name or ""
        for cf in cloud_by_base.get(b["id"], []):
            fh = cf.get("content_hash") or cf["id"]
            if local_files.get(cf["id"]) == fh:
                continue  # 本地已是同内容
            # 原始文件 blob
            raw = None
            if cf.get("file_path"):
                req = urllib.request.Request(_cloud_url(
                    "/wb/sync/files/download?path=" + urllib.parse.quote(
                        _kb_blob_rel(account, cf["file_path"]))),
                    headers={"X-Sync-Token": get_state("token", "") or ""})
                try:
                    with urllib.request.urlopen(req, timeout=300) as resp:
                        raw = resp.read()
                except Exception:
                    logger.exception("知识文档 blob 下载失败: %s", cf["id"])
                    continue
            # 分块拉取必须在本地写事务之外（嵌套连接会撞 BEGIN IMMEDIATE 写锁等 30s）
            remote_chunks = {"chunks": []}
            try:
                remote_chunks = _http_json("GET", _cloud_url(
                    "/wb/kbsync/chunks?file_id=" + urllib.parse.quote(cf["id"])))
            except Exception:
                logger.exception("知识分块拉取失败: %s", cf["id"])
            with create_session() as db:
                fr = db.get(KnowledgeFile, cf["id"])
                if fr is None:
                    fr = KnowledgeFile(id=cf["id"], knowledge_base_id=kb_id)
                    db.add(fr)
                fr.filename = cf.get("filename") or fr.filename
                fr.file_path = cf.get("file_path") or fr.file_path
                fr.file_size = cf.get("file_size") or 0
                fr.file_type = cf.get("file_type") or ""
                fr.content_hash = cf.get("content_hash")
                fr.status = "completed"
                fr.chunk_count = cf.get("chunk_count") or 0
                fr.skipped_chunks = cf.get("skipped_chunks") or 0
                fr.error = None
                if raw is not None and cf.get("file_path"):
                    target = upload_root / cf["file_path"]
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(raw)
                # 分块整文件替换
                import wbmodels as _wb
                db.query(_wb.WbKbChunk).filter(_wb.WbKbChunk.file_id == cf["id"]).delete()
                for c in remote_chunks.get("chunks", []):
                    content = str(c.get("content") or "")
                    if content.strip():
                        db.add(_wb.WbKbChunk(kb_id=kb_id, file_id=cf["id"],
                                             chunk_no=int(c.get("no") or 0),
                                             content=content))
                db.query(KnowledgeBase).filter(KnowledgeBase.id == kb_id).update({
                    "file_count": (db.query(KnowledgeFile)
                                   .filter(KnowledgeFile.knowledge_base_id == kb_id).count())})
                db.commit()
            local_files[cf["id"]] = fh
            restored += 1
        # 向量快照：云端版本与本地记录不同 → 拉回重建（换设备/他端重建索引）
        cvec = b.get("vector_sha")
        if collection and cvec and vec_state.get(collection) != cvec:
            req = urllib.request.Request(_cloud_url(
                "/wb/sync/files/download?path=" + urllib.parse.quote(
                    _kb_blob_rel(account, f"{b['id']}.vectors.json"))),
                headers={"X-Sync-Token": get_state("token", "") or ""})
            try:
                with urllib.request.urlopen(req, timeout=300) as resp:
                    snap = json.loads(resp.read().decode("utf-8"))
                _kb_rebuild_collection(snap)
                vec_state[collection] = cvec
                set_state("kb_vec_state", json.dumps(vec_state))
                logger.info("知识库向量已从云端恢复: %s（%d 条）",
                            collection, len(snap.get("ids") or []))
            except Exception:
                logger.exception("向量快照拉取失败: %s", collection)
    new_pull_shadow = {fid: (f.get("content_hash") or fid)
                       for fid, f in cloud_files.items()}
    set_state("kb_pull_shadow", json.dumps(new_pull_shadow))
    if restored or gone:
        logger.info("知识库同步：恢复/更新 %d 个文档，跟随删除 %d 个", restored, len(gone))


# 同步线程 ---------------------------------------------------------------------

_worker: threading.Thread | None = None
_worker_stop = threading.Event()
_INTERVAL_S = 20


def _worker_loop():
    logger.info("云同步线程已启动（间隔 %ds）", _INTERVAL_S)
    fail_streak = 0
    while True:
        try:
            # 休眠期间 _sync_now 置位可立即唤醒（手动"立即同步"）
            fired = _sync_now.wait(
                min(300, _INTERVAL_S * max(1, fail_streak)) if fail_streak else _INTERVAL_S)
            _sync_now.clear()
            if _worker_stop.is_set():
                return
            if not sync_enabled():
                continue
            if _status.get("syncing"):
                continue
            _status["syncing"] = True
            _push_once()
            _pull_once()
            down = _sync_files_once()[1]
            try:
                _kb_sync_once()  # 知识库账号维度同步（失败不影响行同步）
            except Exception:
                logger.exception("知识库云同步失败（不影响行同步）")
            _status["online"] = True
            _status["last_error"] = None
            _status["last_sync_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            fail_streak = 0
            # 本轮拉回了新的附件/知识文件时，检查是否有滞留在排队的知识文档需要处理
            # （行同步只写表、文件对账只下载文件，都不会调度文档处理）
            if down:
                try:
                    from wbapi import requeue_stale_kb_files
                    requeue_stale_kb_files()
                except Exception:
                    logger.exception("重排滞留知识文档失败（不影响同步）")
        except Exception as ex:
            # 循环体任何异常都不得杀死线程（此前 enable 重写状态表与读取竞态会导致线程静默死亡）
            _status["online"] = False
            _status["last_error"] = str(ex)[:300]
            fail_streak = min(15, fail_streak + 1)
            logger.warning("云同步失败（连续第 %d 次）：%s", fail_streak, ex)
        finally:
            _status["syncing"] = False


def _start_worker():
    global _worker
    if IS_CLOUD:
        return
    if _worker and _worker.is_alive():
        return
    _worker_stop.clear()
    _worker = threading.Thread(target=_worker_loop, name="wb-sync", daemon=True)
    _worker.start()


def _stop_worker():
    _worker_stop.set()


def trigger_sync_now():
    _sync_now.set()


def _init_cloud_id_space() -> None:
    """云服务器初始化 ID 空间：ordinal=0（小区间），id_seq 基线 = 各表小 ID 最大值+1。"""
    from models.database import create_session
    if (get_state("ordinal", None) or "") == "0":
        return
    max_rel = 0
    with create_session() as db:
        for t in _synced_tables().values():
            mx = db.execute(text(f"SELECT MAX(id) FROM `{t.name}` WHERE id < :o"),
                            {"o": ID_STRIDE}).scalar()
            if mx:
                max_rel = max(max_rel, mx)
        _state_set(db, "ordinal", "0")
        _state_set(db, "id_seq", str(max_rel + 1))
        db.commit()
    logger.info("云服务器 ID 空间初始化完成（ordinal=0, id_seq=%d）", max_rel + 1)


def on_startup():
    """lifespan 调用：装触发器；两端激活统一 ID 分配；客户端另启动同步线程。

    安装包预配置：客户端 data/sync_boot.json（{cloud_url, token, client_name}）
    存在且未启用同步时，后台线程自动启用（成功后改名 .done，失败保留待重试/手工处理）。"""
    global _ACTIVE
    try:
        if IS_CLOUD:
            _ACTIVE = True
            install_triggers()
            _init_cloud_id_space()
            logger.info("云服务器模式（WB_SYNC_ROLE=cloud）：同步端点已就绪")
            return
        _ACTIVE = True
        if sync_enabled():
            install_triggers()
            _start_worker()
            return
        _try_boot_sync_config()
    except Exception:
        logger.exception("同步模块启动失败（不影响本地功能）")


def _try_boot_sync_config() -> None:
    """安装包首启自动连云（不阻塞启动）。"""
    boot = Path(get_upload_dir()).resolve().parent / "sync_boot.json"
    if not boot.is_file():
        return
    try:
        cfg = json.loads(boot.read_text(encoding="utf-8"))
        if not cfg.get("cloud_url") or not cfg.get("token"):
            raise ValueError("sync_boot.json 缺少 cloud_url/token")
        threading.Thread(target=_boot_sync_worker, args=(cfg, boot),
                         name="wb-sync-boot", daemon=True).start()
    except Exception:
        logger.exception("解析 sync_boot.json 失败")


def _boot_sync_worker(cfg: dict, boot: Path) -> None:
    for attempt in range(3):
        try:
            enable_sync(cfg["cloud_url"], cfg["token"], cfg.get("client_name") or "客户端")
            boot.rename(str(boot) + ".done")
            logger.info("安装包预置同步配置生效：%s", cfg["cloud_url"])
            return
        except Exception as ex:
            logger.warning("预置同步配置第 %d 次尝试失败：%s", attempt + 1, ex)
            time.sleep(10)
    logger.error("预置同步配置自动启用失败，请在设置页手动配置")
