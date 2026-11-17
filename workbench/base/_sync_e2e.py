"""云同步双实例端到端联调（临时脚本，跑完可删）。

A = 云模式（8902）：WB_SYNC_ROLE=cloud + WB_SYNC_TOKEN，SQLite data/cloud_test.db
B = 客户端（8903）：本地 SQLite data/client_test.db，启用同步指向 A

验证链路：
1. B 启用同步（注册 + 存量迁移 + 触发器）
2. B 写入 → 推送 → A 可见（客户端 ID 区间 offset=1e9）
3. 断网：停 A → B 离线写入 → 重启 A → 恢复推送
4. 反向：A 本地写入 → B 拉取
5. 删除传播（含 FK 级联墓碑）
6. 文件同步（附件双向对账）
"""
import json
import os
import shutil
import subprocess
import sys
import time

import requests

requests.Session.default_timeout = 15  # 兜底，防止脚本无限挂起
_S = requests.Session()

def _req(method, url, **kw):
    kw.setdefault("timeout", 15)
    return _S.request(method, url, **kw)

requests.get = lambda url, **kw: _req("GET", url, **kw)
requests.post = lambda url, **kw: _req("POST", url, **kw)
requests.delete = lambda url, **kw: _req("DELETE", url, **kw)


BASE = os.path.dirname(os.path.abspath(__file__))
PY = os.path.join(BASE, "venv", "Scripts", "python.exe")
A = "http://127.0.0.1:8902"
B = "http://127.0.0.1:8903"
TOKEN = "test-token-123"
STRIDE = 1_000_000_000

procs = {}
_passed, _failed = [], []


def check(name, cond, detail=""):
    if cond:
        _passed.append(name)
        print(f"  [PASS] {name}")
    else:
        _failed.append(f"{name} {detail}")
        print(f"  [FAIL] {name} {detail}")


def start(name, port, env_extra):
    env = os.environ.copy()
    env.update(env_extra)
    p = subprocess.Popen(
        [PY, "-m", "uvicorn", "main:app", "--port", str(port), "--log-level", "warning"],
        cwd=BASE, env=env)
    procs[name] = p
    for _ in range(120):
        try:
            requests.get(f"http://127.0.0.1:{port}/docs", timeout=1)
            return
        except Exception:
            time.sleep(0.5)
    raise RuntimeError(f"{name} 启动失败")


def stop(name):
    p = procs.pop(name, None)
    if not p:
        return
    p.terminate()
    try:
        p.wait(15)
    except Exception:
        p.kill()


def force_sync(base):
    requests.post(f"{base}/wb/sync/now", timeout=5)
    for _ in range(60):
        time.sleep(0.5)
        st = requests.get(f"{base}/wb/sync/status", timeout=5).json()
        if not st["syncing"]:
            return st
    raise TimeoutError("同步轮未结束")


def main():
    # 0. 清理旧环境
    for f in ("data/cloud_test.db", "data/client_test.db"):
        fp = os.path.join(BASE, f)
        if os.path.exists(fp):
            os.remove(fp)
    for d in ("data/uploads_a", "data/uploads_b"):
        shutil.rmtree(os.path.join(BASE, d), ignore_errors=True)

    env_a = {"DATABASE_URL": "sqlite:///./data/cloud_test.db", "WB_SYNC_ROLE": "cloud",
             "WB_SYNC_TOKEN": TOKEN, "UPLOAD_DIR": "./data/uploads_a",
             "WORKBENCH_LOG_DIR": "./data/logs_a"}
    env_b = {"DATABASE_URL": "sqlite:///./data/client_test.db",
             "UPLOAD_DIR": "./data/uploads_b", "WORKBENCH_LOG_DIR": "./data/logs_b"}

    print("== 1. 启动云实例 A 与客户端 B ==")
    start("A", 8902, env_a)
    start("B", 8903, env_b)
    check("A 云同步端点开启", requests.get(f"{A}/wb/sync/ping",
          headers={"X-Sync-Token": TOKEN}).status_code == 200)
    check("A 未带令牌被拒", requests.get(f"{A}/wb/sync/ping").status_code == 401)

    print("== 2. B 启用同步 ==")
    r = requests.post(f"{B}/wb/sync/config", json={
        "cloud_url": A, "token": TOKEN, "client_name": "联调客户端", "enabled": True}).json()
    check("B 注册 ordinal=1", r.get("ordinal") == 1, str(r))

    print("== 3. B 写入 → 推送 → A 可见 ==")
    pid_b = requests.post(f"{B}/wb/projects", json={
        "name": "客户端项目", "code": "SYNCB1"}).json()["id"]
    check("B 客户端 ID 落在 1e9 区间", pid_b >= STRIDE, f"id={pid_b}")
    rid_b = requests.post(f"{B}/wb/requirements", json={
        "project_id": pid_b, "title": "客户端需求"}).json()["id"]
    st = force_sync(B)
    check("B 推送后待推送归零", st["pending_push"] == 0 and st["online"], str(st))
    projects_a = requests.get(f"{A}/wb/projects").json()
    check("A 看到客户端项目", any(p["id"] == pid_b for p in projects_a))
    reqs_a = requests.get(f"{A}/wb/requirements?project_id={pid_b}").json()
    check("A 看到客户端需求（父表先于子表落库）", any(x["id"] == rid_b for x in reqs_a))

    print("== 4. 断网离线写入 → 恢复后自动补推 ==")
    stop("A")
    time.sleep(1)
    pid_b2 = requests.post(f"{B}/wb/projects", json={
        "name": "离线项目", "code": "SYNCB2"}).json()["id"]
    st = force_sync(B)
    check("断网时同步状态离线", st["online"] is False and st["pending_push"] >= 1, str(st))
    start("A", 8902, env_a)
    st = force_sync(B)
    check("恢复联网后补推归零", st["pending_push"] == 0 and st["online"], str(st))
    check("A 收到离线项目", any(p["id"] == pid_b2 for p in
                                requests.get(f"{A}/wb/projects").json()))

    print("== 5. A 本地写入 → B 拉取 ==")
    pid_a = requests.post(f"{A}/wb/projects", json={
        "name": "服务器项目", "code": "SYNCA1"}).json()["id"]
    check("A 服务器 ID 从小区间起步", pid_a < STRIDE, f"id={pid_a}")
    st = force_sync(B)
    projects_b = requests.get(f"{B}/wb/projects").json()
    check("B 拉到服务器项目", any(p["id"] == pid_a for p in projects_b))

    print("== 6. 删除传播（级联墓碑） ==")
    requests.delete(f"{B}/wb/requirements/{rid_b}")  # 子行直接删
    requests.delete(f"{B}/wb/projects/{pid_b}")      # 父行删除
    force_sync(B)
    reqs_a = requests.get(f"{A}/wb/requirements?project_id={pid_b}").json()
    projs_a = requests.get(f"{A}/wb/projects").json()
    check("A 收到需求删除", not any(x["id"] == rid_b for x in reqs_a))
    check("A 收到项目删除", not any(p["id"] == pid_b for p in projs_a))
    # 反向删除：A 删服务器项目 → B 拉到墓碑
    requests.delete(f"{A}/wb/projects/{pid_a}")
    force_sync(B)
    check("B 收到服务器删除", not any(p["id"] == pid_a for p in
                                      requests.get(f"{B}/wb/projects").json()))

    print("== 7. 文件同步（附件） ==")
    bid_b = requests.post(f"{B}/wb/bugs", json={
        "project_id": pid_b2, "title": "文件同步验证 Bug"}).json()["id"]
    files = {"file": ("evidence.png", b"\x89PNG\r\n\x1a\nfake-image-bytes-123", "image/png")}
    requests.post(f"{B}/wb/attachments?owner_type=bug&owner_id={bid_b}", files=files)
    force_sync(B)
    a_files = os.listdir(os.path.join(BASE, "data", "uploads_a", "wb", "bug", str(bid_b)))
    check("A 收到附件文件", len(a_files) == 1, str(a_files))
    # 反向：A 端直连库插入附件（模拟另一台电脑的产物）→ B 下载
    import sqlite3
    con = sqlite3.connect(os.path.join(BASE, "data", "cloud_test.db"))
    att_dir = os.path.join(BASE, "data", "uploads_a", "wb", "bug", str(bid_b))
    os.makedirs(att_dir, exist_ok=True)
    with open(os.path.join(att_dir, "server-artifact.txt"), "wb") as f:
        f.write(b"server-side-artifact")
    rel = f"wb/bug/{bid_b}/server-artifact.txt"
    con.execute("INSERT INTO wb_attachments(owner_type, owner_id, file_name, file_path, "
                "file_size, mime_type) VALUES(?,?,?,?,?,?)",
                ("bug", bid_b, "server-artifact.txt", rel, 19, "text/plain"))
    con.commit()
    con.close()
    force_sync(B)
    got = os.path.join(BASE, "data", "uploads_b", rel)
    check("B 下载到服务器侧产物", os.path.exists(got) and
          open(got, "rb").read() == b"server-side-artifact")

    print("== 8. 触发器回灌检查（拉取不产生待推送脏标记） ==")
    st = force_sync(B)
    check("拉取套用后无脏标记残留", st["pending_push"] == 0, str(st))

    print(f"\n结果：{len(_passed)} 通过 / {len(_failed)} 失败")
    if _failed:
        for f in _failed:
            print("  FAIL:", f)
        sys.exit(1)


if __name__ == "__main__":
    try:
        main()
    finally:
        for name in list(procs):
            stop(name)
