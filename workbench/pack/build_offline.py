"""CyuWorkshop 离线安装包构建编排。

产物结构（pack/dist/CyuWorkshop/）：
  CyuWorkshop.exe            桌面壳（pywebview）
  app/server/...             冻结后端（含 web_dist 静态资源、SQLite 模式）
  runtime/ms-playwright/     Playwright chromium 浏览器内核
  安装说明.md

用法：python build_offline.py          # 构建全部
      python build_offline.py --zip   # 构建并压 zip
"""
import argparse
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

PACK = Path(__file__).resolve().parent
WORKBENCH = PACK.parent
BASE = WORKBENCH / "base"
VENV_PY = BASE / "venv" / "Scripts" / "python.exe"
WEB_DIST = WORKBENCH / "web" / "dist"

DIST = PACK / "dist"
SHELL_BUILD = DIST / "shell_build"
SHELL_DIST = SHELL_BUILD / "CyuWorkshop"
SERVER_DIST = DIST / "server"
PKG = DIST / "CyuWorkshop"

BROWSER_DIRS = ("chromium-1255", "chromium_headless_shell-1255", "ffmpeg-1011", "ffmpeg-1013")

SERVER_EXCLUDES = [
    "--exclude-module", "torch", "--exclude-module", "torchvision", "--exclude-module", "functorch",
    "--exclude-module", "transformers", "--exclude-module", "IPython", "--exclude-module", "jupyter",
    "--exclude-module", "notebook", "--exclude-module", "tensorboard", "--exclude-module", "sympy",
    # 纯测试包树，运行时永不使用（spacy.tests 含数十种语言的语料测试）
    "--exclude-module", "spacy.tests", "--exclude-module", "thinc.tests",
    "--exclude-module", "langchain_classic.agents", "--exclude-module", "langchain_community.tools",
    # OCR/unstructured_inference 深链：离线包不用 OCR（PDF 主链走 pdfminer 版面还原，
    # partition_pdf 已在 file_handle.py 改为延迟导入，缺失时自动降级 PyPDFLoader）
    "--exclude-module", "onnxruntime", "--exclude-module", "onnx",
    "--exclude-module", "cv2", "--exclude-module", "unstructured_inference",
    "--exclude-module", "unstructured.partition.pdf_image",
]


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    r = subprocess.run([str(c) for c in cmd], **kw)
    if r.returncode != 0:
        sys.exit(f"命令失败（{r.returncode}）: {cmd[:3]}...")


def _rename_stale(d: Path):
    """旧产物改名为 .stale-时间戳（不触发批量删除保护，构建成功后可手动清理）。"""
    if d.exists():
        dst = d.with_name(d.name + ".stale-" + time.strftime("%H%M%S"))
        os.rename(d, dst)
        print(f"旧产物已改名: {d.name} -> {dst.name}")


def clean():
    """旧产物改名腾位（PyInstaller --noconfirm 也可以覆盖，但改名更稳且保留旧件可回溯）。"""
    _rename_stale(PKG)
    _rename_stale(SERVER_DIST)
    _rename_stale(SHELL_DIST)


def build_server():
    run([
        VENV_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir", "--console",
        "--name", "server",
        "--distpath", DIST, "--workpath", PACK / "build" / "server",
        "--specpath", PACK / "build" / "server",
        "--paths", BASE,
        "--add-data", f"{WEB_DIST};web_dist",
        "--add-data", f"{BASE / 'static'};static",
        "--add-data", f"{BASE / 'templates'};templates",
        "--collect-all", "playwright",
        "--collect-all", "pytest_playwright",
        *SERVER_EXCLUDES,
        BASE / "server_entry.py",
    ])


def build_shell():
    run([
        VENV_PY, "-m", "PyInstaller", "--noconfirm", "--clean", "--onedir",
        "--name", "CyuWorkshop", "--windowed",
        "--distpath", SHELL_BUILD, "--workpath", PACK / "build" / "shell",
        "--specpath", PACK / "build" / "shell",
        PACK / "run_shell.py",
    ])


def copy_browsers():
    dst = PKG / "runtime" / "ms-playwright"
    dst.mkdir(parents=True, exist_ok=True)
    src = Path(os.environ["LOCALAPPDATA"]) / "ms-playwright"
    for name in BROWSER_DIRS:
        s = src / name
        if not s.exists():
            print(f"!! 浏览器组件缺失，跳过: {s}")
            continue
        print(f"复制浏览器 {name} ...", flush=True)
        shutil.copytree(s, dst / name, dirs_exist_ok=True)


README = """# Cyu工作坊 离线版

## 运行
双击 **CyuWorkshop.exe** 即可（无需联网、无需安装 Python / MySQL）。

- 数据与日志保存在 `data/` 目录（SQLite 数据库 workbench.db、上传文件 uploads、日志 logs）
- 自动化测试浏览器内核在 `runtime/ms-playwright/`

## AI 能力（接本地大模型）
设置页 →「AI 模型 → 本地大模型」填写服务端点即可离线使用：
- Ollama 默认端点：`http://127.0.0.1:11434/v1`（模型名如 `qwen2.5:7b`）
- LM Studio 默认端点：`http://127.0.0.1:1234/v1`
- 任意 OpenAI 兼容服务均可；填好后点「测试连通」验证

知识库语义检索依赖云端 embedding；离线时自动回退关键词检索，其余功能不受影响。

## 日志与排障
- 应用日志：`data/logs/workbench.log`（滚动保留 50MB）
- 桌面壳日志：`data/logs/shell.log`
- 前端 JS 错误自动上报至应用日志（logger: frontend）

## 端口
默认 127.0.0.1:8901；如被占用可在环境变量 `WORKBENCH_PORT` 指定其他端口（启动 exe 前设置）。
"""


def assemble():
    PKG.mkdir(parents=True, exist_ok=True)
    # 壳
    for item in SHELL_DIST.iterdir():
        target = PKG / item.name
        if item.is_dir():
            shutil.copytree(item, target, dirs_exist_ok=True)
        else:
            shutil.copy2(item, target)
    # 后端
    (PKG / "app").mkdir(exist_ok=True)
    shutil.copytree(SERVER_DIST, PKG / "app" / "server", dirs_exist_ok=True)
    # 浏览器 + 说明
    copy_browsers()
    (PKG / "安装说明.md").write_text(README, encoding="utf-8")
    print("组装完成:", PKG)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--zip", action="store_true", help="构建完成后压缩为 zip")
    ap.add_argument("--skip-build", action="store_true", help="跳过 PyInstaller，仅重新组装")
    args = ap.parse_args()

    run([VENV_PY, "-m", "py_compile", BASE / "server_entry.py", BASE / "wbapi.py",
         BASE / "main.py", PACK / "run_shell.py"])

    if not args.skip_build:
        clean()
        print("== 构建 server（时间较长，含依赖分析）==", flush=True)
        build_server()
        print("== 构建桌面壳 ==", flush=True)
        build_shell()
    assemble()
    if args.zip:
        print("== 压缩 zip ==", flush=True)
        shutil.make_archive(str(DIST / "CyuWorkshop-offline"), "zip", root_dir=DIST, base_dir="CyuWorkshop")
        print("zip 完成:", DIST / "CyuWorkshop-offline.zip")
    print("ALL DONE")


if __name__ == "__main__":
    main()
