"""等待云端 pip 安装完成后重启 wb-cloud（激活全功能模式）并做健康检查。"""
import time

import paramiko

cli = paramiko.SSHClient()
cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
cli.connect("8.133.254.215", username="root", password="Yy990216@", timeout=15)


def run(cmd, timeout=120):
    _, out, err = cli.exec_command(cmd, timeout=timeout)
    o = out.read().decode()
    e = err.read().decode()
    print("$", cmd[:70])
    if o.strip():
        print(o.strip()[-900:])
    if e.strip():
        print("[stderr]", e.strip()[-300:])
    return o


# 1. 等 pip 结束（最多 60 分钟，检查 systemd 单元 wb-pip）
for i in range(120):
    _, out, _ = cli.exec_command(
        "systemctl is-active wb-pip 2>/dev/null; ps aux | grep 'pip install' | grep -v grep | wc -l",
        timeout=30)
    lines = out.read().decode().split()
    if lines and lines[0] in ("inactive", "failed") or (len(lines) > 1 and lines[-1] == "0"):
        print(f"pip 已结束（第 {i} 次检查，状态 {lines[0]}）")
        break
    time.sleep(30)
else:
    print("pip 超时仍未结束，放弃自动重启")
    cli.close()
    raise SystemExit(1)

# 2. 确认关键包已装
run("/opt/wb-cloud/app/venv/bin/pip list 2>/dev/null | grep -iE 'chromadb|langchain$|langchain-core|unstructured' | head -6")

# 3. 重启服务
run("systemctl restart wb-cloud && sleep 6 && systemctl is-active wb-cloud")

# 4. 健康检查
run("curl -s -m 8 http://127.0.0.1:8902/wb/sync/ping -H 'X-Sync-Token: 64dda18ccb108375ed19291450e60ef4282b286c9b9d2c29'")
run("curl -s -m 8 -o /dev/null -w 'app:%{http_code}\\n' http://127.0.0.1:8902/app/")
run("journalctl -u wb-cloud -n 15 --no-pager | grep -E 'ERROR|error|Traceback|Uvicorn|应用|started' | tail -8")
run("ps aux | grep uvicorn | grep -v grep | awk '{print \"uvicorn RSS(KB):\", $6}'")
run("free -m | head -2")

cli.close()
print("DONE")
