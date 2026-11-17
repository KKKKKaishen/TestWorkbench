"""enable_sync 卡点定位（临时）：直调同步启用逻辑，10s 后打印线程栈。"""
import faulthandler
import os
import sys

faulthandler.dump_traceback_later(10, exit=True, file=sys.stderr)

BASE = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE)
os.environ["DATABASE_URL"] = "sqlite:///./data/client_dbg.db"
os.environ["UPLOAD_DIR"] = "./data/uploads_dbg"
os.environ["WORKBENCH_LOG_DIR"] = "./data/logs_dbg"
if os.path.exists("data/client_dbg.db"):
    os.remove("data/client_dbg.db")

from models.database import init_db
import wbmodels  # noqa: F401  注册 wb_ 表到元数据（与 main.py 一致）
init_db()
import wbsync
wbsync._ACTIVE = True
snap = wbsync.enable_sync("http://127.0.0.1:8902", "test-token-123", "调试客户端")
print("OK:", snap)
