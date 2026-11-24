"""一次性增量迁移：dev 库(Workbench) 有而云端(workbench_cloud) 没有的行 → 推送到云端 API。
只做增量补齐（云端已有行不动，避免覆盖绿色客户端的较新编辑）。"""
import json
import time
from datetime import datetime
from decimal import Decimal

import paramiko
import pymysql
import urllib.request

CLOUD_URL = "https://wb.ka1shen.cn"
TOKEN = "64dda18ccb108375ed19291450e60ef4282b286c9b9d2c29"
SSH_PWD = "Yy990216@"

TABLES = [
    "wb_projects", "wb_test_cases", "wb_reports", "wb_api_collections",
    "wb_api_environments", "wb_api_request_history", "wb_api_requests",
    "wb_api_scenario_steps", "wb_api_scenarios", "wb_attachments",
    "wb_auto_runs", "wb_auto_scripts", "wb_bug_links", "wb_bugs",
    "wb_execution_plan_cases", "wb_execution_plans", "wb_execution_records",
    "wb_regression_runs", "wb_regression_schedules", "wb_regression_set_items",
    "wb_regression_sets", "wb_requirements", "wb_settings",
    "wb_test_logs", "wb_test_points",
]


def row_to_payload(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.strftime("%Y-%m-%d %H:%M:%S.%f")
        elif isinstance(v, Decimal):
            v = str(v)
        elif isinstance(v, (dict, list)):
            v = json.loads(json.dumps(v, ensure_ascii=False, default=str))
        out[k] = v
    return out


def cloud_push(rows: list) -> tuple:
    body = json.dumps({"rows": rows}).encode()
    req = urllib.request.Request(
        CLOUD_URL + "/wb/sync/push", data=body,
        headers={"X-Sync-Token": TOKEN, "Content-Type": "application/json"},
        method="POST")
    with urllib.request.urlopen(req, timeout=60) as r:
        resp = json.loads(r.read().decode())
    return resp.get("accepted", []), resp.get("rejected", [])


def main():
    # 云端已有 PK 集合（经 SSH 查询）
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    cli.connect("8.133.254.215", username="root", password=SSH_PWD, timeout=15)
    cloud_pks = {}
    for t in TABLES:
        _, out, _ = cli.exec_command(
            "mysql -uwbcloud -p'e49f7a8f010e22e439745fef7a46535b' workbench_cloud "
            f"-N -e 'SELECT id FROM {t}' 2>/dev/null", timeout=30)
        cloud_pks[t] = {int(x) for x in out.read().decode().split() if x.strip().isdigit()}
    cli.close()

    dev = pymysql.connect(host="8.133.254.215", port=3306, user="cyu",
                          password="Yy990216@", database="Workbench",
                          charset="utf8mb4", cursorclass=pymysql.cursors.DictCursor)
    stamp_base = time.time_ns() // 1000
    total_ok, total_rej = 0, 0
    for i, t in enumerate(TABLES):
        with dev.cursor() as cur:
            cur.execute(f"SELECT * FROM {t}")
            rows = cur.fetchall()
        missing = [r for r in rows if int(r["id"]) not in cloud_pks.get(t, set())]
        if not missing:
            continue
        acc_all, rej_all = [], []
        for j in range(0, len(missing), 50):
            chunk = missing[j:j + 50]
            payload = [{"table": t, "pk": int(r["id"]), "op": "u",
                        "stamp": stamp_base + i * 10000 + j,
                        "data": row_to_payload(r)} for r in chunk]
            acc, rej = cloud_push(payload)
            acc_all += acc
            rej_all += rej
        total_ok += len(acc_all)
        total_rej += len(rej_all)
        print(f"{t}: dev={len(rows)} missing={len(missing)} pushed={len(acc_all)} rejected={len(rej_all)}")
        for r in rej_all[:5]:
            print("  REJ:", r)
    dev.close()
    print(f"\n合计推送 {total_ok} 行，拒绝 {total_rej} 行")


if __name__ == "__main__":
    main()
