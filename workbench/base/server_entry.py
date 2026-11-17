"""TestWorkbench 后端冻结入口（PyInstaller --onedir，离线安装包专用）。

两种运行模式：
1. 默认：设好离线运行所需的环境变量（SQLite/目录/浏览器路径）后，
   以进程内 uvicorn 启动 main.app。
2. ``server.exe --run-pytest <args_json> <out_json>``：自动化测试子进程模式。
   冻结环境没有独立 python.exe，wbapi 的自动化执行器通过 server.exe 自调用本模式
   跑 pytest.main（保持子进程隔离与超时可杀），结果落 JSON 后立即退出。
"""
import json
import os
import sys
from pathlib import Path


def _app_dir() -> Path:
    """server.exe 所在目录（安装包 app/；开发态即本文件所在 base/）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _package_root() -> Path:
    """安装包根目录（app/ 的上一级）。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent.parent
    return Path(__file__).resolve().parent.parent


def _run_pytest_mode() -> int:
    """--run-pytest 子进程模式：跑 pytest.main，输出写 JSON 落盘。"""
    import contextlib
    import io
    import traceback

    import pytest

    args = json.loads(Path(sys.argv[2]).read_text(encoding="utf-8"))
    out_path = Path(sys.argv[3])
    buf_out, buf_err = io.StringIO(), io.StringIO()
    rc = -1
    try:
        with contextlib.redirect_stdout(buf_out), contextlib.redirect_stderr(buf_err):
            rc = pytest.main(args)
    except SystemExit as e:  # pytest.main 理论上返回 rc，个别路径直接 SystemExit
        rc = int(e.code or 0)
    except Exception:
        buf_err.write(traceback.format_exc())
    out_path.write_text(
        json.dumps({"rc": rc, "stdout": buf_out.getvalue(), "stderr": buf_err.getvalue()},
                   ensure_ascii=False),
        encoding="utf-8")
    return 0


def _serve_mode() -> None:
    """默认模式：初始化离线目录与数据库，进程内启动 uvicorn。"""
    root = _package_root()
    data = root / "data"
    for sub in ("", "uploads", "temp_uploads", "logs", "chroma_db", "checkpoints"):
        (data / sub).mkdir(parents=True, exist_ok=True)

    # 离线运行环境（不覆盖用户已显式设置的变量）
    os.environ.setdefault("DATABASE_URL", f"sqlite:///{(data / 'workbench.db').as_posix()}")
    os.environ.setdefault("UPLOAD_DIR", str(data / "uploads"))
    os.environ.setdefault("TEMP_UPLOAD_DIR", str(data / "temp_uploads"))
    os.environ.setdefault("WORKBENCH_LOG_DIR", str(data / "logs"))
    os.environ.setdefault("RAG_DB_PATH", str(data / "chroma_db"))
    os.environ.setdefault("WORKFLOW_CHECKPOINT_DB_PATH", str(data / "checkpoints" / "langgraph.db"))
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", str(root / "runtime" / "ms-playwright"))
    os.environ.setdefault("APP_ENV", "development")
    os.environ.setdefault("SESSION_SECRET_KEY", "cyu-workbench-offline")

    port = int(os.getenv("WORKBENCH_PORT", "8901"))
    import uvicorn

    from main import UVICORN_LOG_CONFIG, app

    uvicorn.run(app, host="127.0.0.1", port=port, log_config=UVICORN_LOG_CONFIG)


if __name__ == "__main__":
    if len(sys.argv) >= 4 and sys.argv[1] == "--run-pytest":
        sys.exit(_run_pytest_mode())
    _serve_mode()
