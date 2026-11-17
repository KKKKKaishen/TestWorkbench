from contextlib import asynccontextmanager
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

import wbmodels  # noqa: F401  注册 wb_ 表到 Base 元数据（SQLite 开发环境自动建表）

# 精简模式（WB_LITE=1）：云服务器数据中枢部署用。
# 只挂载 /wb 业务 API 与前端静态资源，跳过知识库/工作流/AI 底座等重依赖（chromadb/langchain），
# 使 2 核小内存服务器无需安装重型 ML 依赖即可作为多客户端同步中枢运行。
WB_LITE = (os.getenv("WB_LITE") or "").strip().lower() in ("1", "true", "yes")

from config import get_app_env, get_session_secret_key  # noqa: E402
from models.database import init_db  # noqa: E402
from wbapi import router as wb_router  # noqa: E402

if not WB_LITE:
    from api.api_v1 import api_router
    from api.endpoints import auth, chat, knowledge_api, testbench_api, workflow_api
    from services import knowledge_service, test_run_service

BASE_DIR = Path(__file__).resolve().parent

# 日志目录：默认 base/logs，打包模式经 WORKBENCH_LOG_DIR 指到 data/logs（可写目录）
LOG_DIR = Path(os.getenv("WORKBENCH_LOG_DIR") or (BASE_DIR / "logs"))
_LOG_FORMAT = "%(asctime)s %(levelname)s [%(name)s] %(message)s"


def _setup_logging():
    """文件滚动 + 控制台双通道；应用日志与 uvicorn 日志统一落盘。
    在创建应用之前完成，保证 create_app 期间（如生产配置校验）的日志格式统一。"""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录不可写（极少见）时退化为纯控制台，不让日志配置阻断启动
        logging.basicConfig(level=logging.INFO, format=_LOG_FORMAT, force=True)
        return
    file_handler = RotatingFileHandler(
        LOG_DIR / "workbench.log", maxBytes=5 * 1024 * 1024,
        backupCount=10, encoding="utf-8")
    console_handler = logging.StreamHandler()
    logging.basicConfig(
        level=logging.INFO, format=_LOG_FORMAT,
        handlers=[file_handler, console_handler], force=True,
    )
    # uvicorn 的三个 logger 默认 propagate=False 且自带 handler（仅控制台），
    # 这里统一接管：错误与访问日志同样进文件，避免打包后无控制台时日志丢失
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        lg = logging.getLogger(name)
        lg.handlers = [file_handler, console_handler]
        lg.propagate = False


_setup_logging()
logger = logging.getLogger(__name__)

# 打包模式（server.py）把它传给 uvicorn.run(log_config=...)，
# 让 uvicorn 在 frozen 场景同样走上面的文件+控制台配置
UVICORN_LOG_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "default": {"format": _LOG_FORMAT},
        "access": {"format": _LOG_FORMAT},
    },
    "handlers": {
        "default": {
            "class": "logging.StreamHandler",
            "formatter": "default",
            "stream": "ext://sys.stdout",
        },
        "access": {
            "class": "logging.StreamHandler",
            "formatter": "access",
            "stream": "ext://sys.stdout",
        },
    },
    "loggers": {
        "uvicorn": {"handlers": ["default"], "level": "INFO", "propagate": False},
        "uvicorn.error": {"level": "INFO"},
        "uvicorn.access": {"handlers": ["access"], "level": "INFO", "propagate": False},
    },
}


def _get_session_secret() -> str:
    session_secret = get_session_secret_key()
    if session_secret:
        return session_secret

    if get_app_env() == "production":
        raise RuntimeError("SESSION_SECRET_KEY must be set in production")

    return "dev-session-secret-change-me"


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    if not WB_LITE:
        # 后台处理任务不跨进程存活：启动时把卡在 pending/processing 的文件重置为失败，等待用户重试
        knowledge_service.reset_stale_processing_files()
        # 执行记录同理：残留的 running 会让该接口文档按占用语义被永久锁死
        test_run_service.reset_stale_running()
        # 存量执行结果快照可能含明文凭据（写侧脱敏上线前落库），启动时幂等清洗
        test_run_service.redact_stored_request_headers()
    # RG-02：回归定时调度线程（每 30s 扫描到期计划，daemon 不阻塞退出）
    from wbapi import start_regression_scheduler
    start_regression_scheduler()
    # 云同步：装行版本触发器；客户端模式启动同步线程（云模式只装触发器）
    import wbsync
    wbsync.on_startup()
    yield



def create_app() -> FastAPI:
    app = FastAPI(lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=_get_session_secret(),
        # 生产（HTTPS）下会话 cookie 不得在明文连接上传输
        https_only=get_app_env() == "production",
    )

    if (BASE_DIR / "static").exists():
        app.mount("/static", StaticFiles(directory=str(BASE_DIR / "static")), name="static")

    if not WB_LITE:
        app.include_router(api_router)
        app.include_router(auth.router)
        app.include_router(chat.router)
        app.include_router(knowledge_api.router)
        app.include_router(workflow_api.router)
        app.include_router(testbench_api.router)
    app.include_router(wb_router)

    # 全局兜底：任何未处理异常记录完整 traceback 到日志，再返回 500 JSON
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request, exc):
        logger.exception("未处理异常 %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500, content={"detail": f"服务器内部错误：{exc}"})

    # 工作台前端（Vue3 构建产物）；开发期也可走 Vite dev server (5173) 代理
    # 打包模式：web/dist 作为 datas 打进 _internal/web_dist
    web_dist = BASE_DIR.parent / "web" / "dist"
    if not web_dist.exists():
        web_dist = BASE_DIR / "web_dist"
    if web_dist.exists():
        app.mount("/app", StaticFiles(directory=str(web_dist), html=True), name="web")
    return app


app = create_app()
