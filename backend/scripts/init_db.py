"""初始化数据库：创建 data/aitp.db 并应用全部迁移。

    python backend/scripts/init_db.py            # 建库（已存在则补迁移）
    python backend/scripts/init_db.py --reset    # 删库重建
    python backend/scripts/init_db.py --check    # 只检查状态
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.db.migrator import applied_versions, init_db, migration_files  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="AITP 数据库初始化")
    parser.add_argument("--db", default=str(ROOT / "data" / "aitp.db"))
    parser.add_argument("--reset", action="store_true", help="先删除现有数据库")
    parser.add_argument("--check", action="store_true", help="只检查，不改动")
    args = parser.parse_args()

    db_path = pathlib.Path(args.db)

    if args.check:
        if not db_path.exists():
            print(f"[FAIL] 数据库不存在: {db_path}")
            return 1
        conn = sqlite3.connect(db_path)
        try:
            done = applied_versions(conn)
            tables = [
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') "
                    "AND name NOT LIKE 'sqlite_%' ORDER BY name"
                )
            ]
            print(f"[OK] 数据库: {db_path}")
            print(f"[OK] 已应用迁移版本: {sorted(done)}")
            print(f"[OK] 表/视图数量: {len(tables)}")
            for name in tables:
                print(f"       - {name}")
        finally:
            conn.close()
        return 0

    if args.reset and db_path.exists():
        for suffix in ("", "-wal", "-shm"):
            target = pathlib.Path(str(db_path) + suffix)
            if target.exists():
                target.unlink()
        print(f"[1/2] 已删除旧库 {db_path}")

    applied = init_db(db_path)
    print(f"[2/2] 初始化完成，已应用迁移: {applied}")

    conn = sqlite3.connect(db_path)
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
            )
        ]
        print(f"表数量: {len(tables)}")
        for name in tables:
            print(f"  - {name}")
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
