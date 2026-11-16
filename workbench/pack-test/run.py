"""TestWorkbench 桌面壳（P1 版）：pywebview 窗口 + 自动拉起工作台后端。

启动流程：
1. 探测 127.0.0.1:8901 是否已有工作台服务（已在跑则直接复用）
2. 没有则用 base venv 的 python 以子进程方式拉起 uvicorn（等待健康检查通过）
3. pywebview 创建原生窗口（Win11 WebView2）加载工作台页面
4. 关闭窗口 = 结束自己拉起的服务子进程，进程退出
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import webview

PORT = 8901
URL = f"http://127.0.0.1:{PORT}/app/"
HEALTH = f"http://127.0.0.1:{PORT}/"

# 开发机路径；后续正式打包改为随包分发的精简服务端
def _find_base_dir():
    """定位 workbench/base（含 venv 与后端代码）。

    - 开发态（py 直接跑）：pack-test/run.py -> ../base
    - 打包态（PyInstaller --onedir）：exe 在 pack-test/dist/TestWorkbenchShell/，
      __file__ 在 _internal 里，不可靠；从 exe 所在目录向上找 workbench/base
    """
    roots = []
    if getattr(sys, "frozen", False):
        roots.append(Path(sys.executable).resolve().parent)  # dist/TestWorkbenchShell
    else:
        roots.append(Path(__file__).resolve().parent)  # pack-test
    for root in roots:
        # 从当前目录逐级向上（最多 6 级）找 workbench/base
        cur = root
        for _ in range(6):
            cand = cur / "base"
            if (cand / "venv" / "Scripts" / "python.exe").exists():
                return cand
            cand2 = cur / "workbench" / "base"
            if (cand2 / "venv" / "Scripts" / "python.exe").exists():
                return cand2
            cur = cur.parent
    return roots[0].parent / "base"


BASE_DIR = _find_base_dir()
BASE_VENV_PY = BASE_DIR / "venv" / "Scripts" / "python.exe"

server_proc = None
window = None
_maxed = False  # 最大化状态跟踪（用于标题栏双击/按钮切换还原）

LOG_FILE = Path(os.environ.get("TEMP", str(Path(__file__).parent))) / "TestWorkbenchShell.log"


def _log(msg):
    """窗口版进程无控制台，关键步骤落日志便于排查启动问题"""
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%H:%M:%S')} {msg}\n")
    except Exception:
        pass


class WindowApi:
    """暴露给前端 pywebview.api 的窗口控制（自绘标题栏按钮调用）"""

    def minimize(self):
        window.minimize()

    def toggle_max(self):
        global _maxed
        if _maxed:
            window.restore()
            _maxed = False
        else:
            window.maximize()
            _maxed = True

    def close(self):
        window.destroy()


def _reachable(url, timeout=2):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(url, timeout=timeout)
        return True
    except Exception:
        return False


def ensure_server():
    global server_proc
    _log(f"ensure_server 开始, BASE={BASE_DIR}, venv={BASE_VENV_PY}")
    if _reachable(HEALTH):
        _log("已有服务在跑，复用")
        return
    if not BASE_VENV_PY.exists():
        _log("未找到后端解释器")
        _fatal_dialog(f"未找到后端解释器：\n{BASE_VENV_PY}")
        sys.exit(1)
    env = os.environ.copy()
    server_proc = subprocess.Popen(
        [str(BASE_VENV_PY), "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(PORT)],
        cwd=str(BASE_DIR), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    _log(f"服务子进程已拉起 PID={server_proc.pid}")
    for i in range(180):  # 最多等 180 秒（冷启动大库导入可能超过 1 分钟）
        if _reachable(HEALTH):
            _log(f"服务就绪，等待 {i} 秒")
            return
        if server_proc.poll() is not None:
            _log(f"子进程提前退出 code={server_proc.returncode}")
            _cleanup_server()
            _fatal_dialog("后端服务启动失败，请检查 base/venv 与 .env 配置")
            sys.exit(1)
        if i % 15 == 0 and i:
            _log(f"等待服务中... {i}s")
        time.sleep(1)
    _log("等待超时 180s")
    _cleanup_server()
    _fatal_dialog("后端服务启动超时")
    sys.exit(1)


def _fatal_dialog(msg):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, msg, "TestWorkbench", 0x10)


def _cleanup_server():
    """退出前清理自己拉起的服务子进程（fatal 退出路径也要调用）"""
    global server_proc
    if server_proc and server_proc.poll() is None:
        server_proc.terminate()
        try:
            server_proc.wait(timeout=8)
        except Exception:
            server_proc.kill()


def main():
    global window
    _log("main 开始")
    try:
        ensure_server()
        window = webview.create_window(
            "Cyu工作坊",
            URL,
            width=1440,
            height=900,
            frameless=True,        # 去掉系统标题栏，客户端化外观
            js_api=WindowApi(),    # 前端自绘标题栏的最小化/最大化/关闭
        )
        _log("窗口已创建，进入事件循环")
    except Exception:
        import traceback
        _log("启动异常:\n" + traceback.format_exc())
        raise
    try:
        webview.start()
        _log("事件循环正常结束")
    finally:
        _cleanup_server()


if __name__ == "__main__":
    main()
