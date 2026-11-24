"""CyuWorkshop 离线安装包桌面壳：pywebview 窗口 + 拉起冻结后端 server.exe。

启动流程：
1. 探测 127.0.0.1:8901 是否已有工作台服务（已在跑则直接复用）
2. 没有则拉起 <安装根>/app/server/server.exe（冻结后端，等待健康检查通过）
3. pywebview 创建原生窗口（WebView2）加载工作台页面
4. 关闭窗口 = 结束自己拉起的服务子进程，进程退出
"""
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import webview

webview.settings["ALLOW_DOWNLOADS"] = True  # 允许 WebView2 内触发下载（保存对话框默认定位到下载目录）
PORT = int(os.getenv("WORKBENCH_PORT", "8901"))
URL = f"http://127.0.0.1:{PORT}/app/"
HEALTH = f"http://127.0.0.1:{PORT}/"


def _package_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


ROOT = _package_root()
SERVER_EXE = ROOT / "app" / "server" / "server.exe"
LOG_DIR = ROOT / "data" / "logs"
LOG_FILE = LOG_DIR / "shell.log"

server_proc = None
window = None
_maxed = False


def _log(msg):
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
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

    def download_file(self, url, filename):
        """从本地服务下载文件到系统默认下载目录（无弹窗，重名自动加序号）。
        返回保存路径；失败抛异常（pywebview 会转成前端 Promise 拒绝）。"""
        import urllib.error

        dest_dir = Path.home() / "Downloads"
        dest_dir.mkdir(parents=True, exist_ok=True)
        safe_name = Path(filename).name or "download"
        dest = dest_dir / safe_name
        stem, suffix = dest.stem, dest.suffix
        i = 1
        while dest.exists():
            dest = dest_dir / f"{stem} ({i}){suffix}"
            i += 1
        try:
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            with opener.open(url, timeout=60) as resp, open(dest, "wb") as f:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
        except urllib.error.HTTPError as e:
            _log(f"download_file HTTP错误 code={e.code} url={url}")
            raise RuntimeError(f"下载失败（HTTP {e.code}）") from e
        except Exception as e:
            _log(f"download_file 异常: {e}")
            raise RuntimeError(f"下载失败: {e}") from e
        _log(f"download_file 完成: {dest}")
        return str(dest)


def _patch_window():
    """无边框窗口行为修复：
    1) MaximizedBounds 限定为工作区（屏幕减去任务栏）→ 最大化不再遮挡任务栏；
    2) 补 WS_SYSMENU/WS_MINIMIZEBOX/WS_MAXIMIZEBOX 样式 → 任务栏图标点击可最小化/还原。"""
    import ctypes

    from System.Drawing import Rectangle
    from System.Windows.Forms import Screen

    try:
        form = window.native
        if form is None:
            _log("窗口行为修复跳过：native 窗体未就绪")
            return
        # 1) 最大化边界 = 工作区
        area = Screen.FromControl(form).WorkingArea
        form.MaximizedBounds = Rectangle(area.X, area.Y, area.Width, area.Height)
        # 2) 补窗口样式（不改变无边框外观，仅恢复任务栏交互行为）
        GWL_STYLE = -16
        WS_MAXIMIZEBOX, WS_MINIMIZEBOX, WS_SYSMENU = 0x10000, 0x20000, 0x80000
        SWP_NOSIZE, SWP_NOMOVE = 0x1, 0x2
        SWP_NOZORDER, SWP_FRAMECHANGED = 0x4, 0x20
        user32 = ctypes.windll.user32
        hwnd = form.Handle.ToInt64()
        style = user32.GetWindowLongW(hwnd, GWL_STYLE)
        user32.SetWindowLongW(
            hwnd, GWL_STYLE,
            style | WS_MAXIMIZEBOX | WS_MINIMIZEBOX | WS_SYSMENU,
        )
        user32.SetWindowPos(
            hwnd, 0, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_FRAMECHANGED,
        )
        _log("窗口行为修复完成（MaximizedBounds=工作区 + 任务栏样式）")
    except Exception:
        import traceback
        _log("窗口行为修复失败:\n" + traceback.format_exc())


def _reachable(url, timeout=2):
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        opener.open(url, timeout=timeout)
        return True
    except Exception:
        return False


def _fatal_dialog(msg):
    import ctypes
    ctypes.windll.user32.MessageBoxW(0, msg, "Cyu工作坊", 0x10)


def ensure_server():
    global server_proc
    _log(f"ensure_server 开始, SERVER={SERVER_EXE}")
    if _reachable(HEALTH):
        _log("已有服务在跑，复用")
        return
    if not SERVER_EXE.exists():
        _log("未找到后端 server.exe")
        _fatal_dialog(f"未找到后端服务程序：\n{SERVER_EXE}\n\n请确认安装目录完整（app/server/server.exe）。")
        sys.exit(1)
    server_proc = subprocess.Popen(
        [str(SERVER_EXE)],
        cwd=str(SERVER_EXE.parent),
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    _log(f"服务子进程已拉起 PID={server_proc.pid}")
    for i in range(120):  # 最多等 120 秒（首次启动含建库）
        if _reachable(HEALTH):
            _log(f"服务就绪，等待 {i} 秒")
            return
        if server_proc.poll() is not None:
            _log(f"子进程提前退出 code={server_proc.returncode}")
            _cleanup_server()
            _fatal_dialog("后端服务启动失败，详情见 data/logs/workbench.log")
            sys.exit(1)
        if i % 15 == 0 and i:
            _log(f"等待服务中... {i}s")
        time.sleep(1)
    _log("等待超时 120s")
    _cleanup_server()
    _fatal_dialog("后端服务启动超时")
    sys.exit(1)


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
        window.events.loaded += lambda *args: _patch_window()
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
