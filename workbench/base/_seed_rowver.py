# -*- coding: utf-8 -*-
"""为已迁移的本地库补种 wb_sync_rowver（存量行标记 dirty=1 待推送）并补装客户端触发器。

用法：在 base 目录下运行  venv/Scripts/python.exe _seed_rowver.py
"""
import sys

import wbsync
import wbmodels
from models.database import get_engine
from sqlalchemy import text


def main() -> None:
    eng = get_engine()
    is_mysql = eng.dialect.name == "mysql"
    client_id = wbsync.get_state("client_id", "") or "local"
    ordinal = wbsync.get_state("ordinal", "0")
    print(f"dialect={eng.dialect.name} client_id={client_id} ordinal={ordinal}")

    tables = [t for t in wbmodels.Base.metadata.sorted_tables
              if t.name.startswith("wb_") and t.name not in wbsync._EXCLUDED]
    stamp = wbsync._stamp_now()
    total = 0
    with eng.connect().execution_options(isolation_level="AUTOCOMMIT") as conn:
        for t in tables:
            seed = ("INSERT IGNORE INTO" if is_mysql else "INSERT OR IGNORE INTO")
            sql = (f"{seed} wb_sync_rowver(table_name, row_pk, stamp, client_id, deleted, dirty) "
                   f"SELECT '{t.name}', id, {stamp}, '{client_id}', 0, 1 "
                   f"FROM `{t.name}`")
            r = conn.execute(text(sql))
            total += r.rowcount or 0
    print(f"seeded dirty rows: {total} ({len(tables)} tables)")

    wbsync.install_triggers()
    print("triggers installed")


if __name__ == "__main__":
    sys.exit(main())
