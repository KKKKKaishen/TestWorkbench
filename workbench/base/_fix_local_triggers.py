# -*- coding: utf-8 -*-
"""本地库一次性修复：重建客户端模式触发器 + 修正被误标 dirty=0 的本地写入。

用法：base 目录下运行  venv/Scripts/python.exe _fix_local_triggers.py
"""
import sys

import wbsync
import wbmodels
from models.database import get_engine
from sqlalchemy import text


def main() -> None:
    eng = get_engine()
    client_id = wbsync.get_state("client_id", "") or "local"
    print(f"dialect={eng.dialect.name} client_id={client_id}")

    # 1. 删除全部旧同步触发器
    with eng.begin() as conn:
        rows = conn.execute(text(
            "SELECT TRIGGER_NAME FROM information_schema.TRIGGERS "
            "WHERE TRIGGER_SCHEMA = DATABASE() AND TRIGGER_NAME LIKE 'wb_sync_%'")).fetchall()
        for (tn,) in rows:
            conn.execute(text(f"DROP TRIGGER IF EXISTS `{tn}`"))
        print(f"dropped {len(rows)} old triggers")

    # 2. 清除模式标记，按当前（客户端）代码重装
    wbsync.set_state("trigger_mode", "")
    wbsync.install_triggers()
    print("client-mode triggers installed")

    # 3. 误标为 server/dirty=0 的本地写入改回待推送
    stamp = wbsync._stamp_now()
    with eng.begin() as conn:
        r = conn.execute(text(
            "UPDATE wb_sync_rowver SET dirty = 1, client_id = :cid, stamp = :st "
            "WHERE client_id = 'server'"), {"cid": client_id, "st": stamp})
        print(f"re-marked dirty rows (server-mislabeled): {r.rowcount}")
        # 旧触发器漏更 dirty 的本地产物（含墓碑）也补回待推送
        r2 = conn.execute(text(
            "UPDATE wb_sync_rowver SET dirty = 1, stamp = :st "
            "WHERE client_id = :cid AND dirty = 0"), {"cid": client_id, "st": stamp})
        print(f"re-marked dirty rows (own rows): {r2.rowcount}")
    print("DONE")


if __name__ == "__main__":
    sys.exit(main())
