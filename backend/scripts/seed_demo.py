"""演示数据 CLI：数据装配逻辑在 aitp.services.demo_seed_service。

    python backend/scripts/seed_demo.py                 # 写 data/demo.db
    python backend/scripts/seed_demo.py --reset         # 先清空 demo 库业务数据
    python backend/scripts/seed_demo.py --db data/aitp.db --append  # 正式库只追加示例项目
    python backend/scripts/seed_demo.py --db data/aitp.db --force   # 明确要对正式库清空重建

演示数据默认写进独立的 `data/demo.db`：不带 `--append` 时会无差别清空业务表。
对存量库会先补齐未应用的迁移再写入。seed() 从本模块再导出，
check_db.py / check_walkthrough.py 的 `from seed_demo import seed` 不受影响。
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

from aitp.config import load_config  # noqa: E402
from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services.demo_seed_service import (  # noqa: E402
    DEMO_CODE,
    TABLES,
    _drop_stale_mirror_files,
    seed,
    seed_append,
)

__all__ = ["seed"]  # 门禁的依赖面


def _live_db_path() -> pathlib.Path:
    """当前服务实际在用的库；演示脚本默认不许碰它。"""
    return load_config().db_path.resolve()


def _project_codes(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[0]) for row in conn.execute("SELECT code FROM biz_project")}
    except sqlite3.OperationalError:
        return set()


def _clear_business_tables(conn: sqlite3.Connection) -> list[str]:
    """清空全部业务表，返回清空前需要删除的落盘文件。"""
    stale_files = [
        str(row[0]) for row in conn.execute(
            "SELECT file_path FROM biz_knowledge_article"
            " WHERE file_path IS NOT NULL AND file_path <> ''")
    ]
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    return stale_files


def main() -> int:
    parser = argparse.ArgumentParser(description="AITP 演示数据")
    parser.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    parser.add_argument("--reset", action="store_true", help="先清空业务数据")
    parser.add_argument("--append", action="store_true",
                        help="追加模式：只重建示例项目自身，绝不清空其他项目的数据")
    parser.add_argument("--force", action="store_true",
                        help="允许对正在使用的正式库写入并清空")
    args = parser.parse_args()

    if args.reset and args.append:
        print("[FAIL] --reset（清空整库）与 --append（只动示例项目）互斥，请二选一。")
        return 1

    db_path = pathlib.Path(args.db)
    if not db_path.exists():
        print(f"[INFO] 数据库不存在，新建并建表：{db_path}")
        init_db(db_path)
    else:
        # 存量库先补齐未应用的迁移（只追加新版本，已应用的不重复执行），
        # 否则老库缺表缺列会让 seed 直接报错
        applied = init_db(db_path)
        if applied:
            print(f"[INFO] 补齐迁移：{applied}")

    if not args.force and not args.append and db_path.resolve() == _live_db_path():
        print(
            "[FAIL] 这是正在使用的正式库，演示脚本会清空业务表，已拒绝执行。\n"
            f"       目标库：{db_path}\n"
            "       要体验演示数据：不带 --db 运行，脚本会写 data/demo.db；\n"
            "       只追加示例项目不动现有数据：加 --append；\n"
            "       确实要对正式库清空重建：加 --force。"
        )
        return 1

    conn = get_standalone_conn(db_path)
    try:
        if args.append:
            counts = seed_append(conn)
            print(f"目标库：{db_path}")
            print(" | ".join(f"{k} {v}" for k, v in counts.items()))
            return 0
        stale_files: list[str] = []
        if args.reset:
            print(f"[WARN] 即将清空 {db_path} 的全部业务表。")
            stale_files = _clear_business_tables(conn)
            print("已清空业务数据。")
        else:
            codes = _project_codes(conn)
            if codes == {DEMO_CODE}:
                print("[INFO] 目标库已有演示数据，自动重建（等价于 --reset）。")
                stale_files = _clear_business_tables(conn)
            elif codes:
                preview = "、".join(sorted(codes)[:5])
                print(
                    f"[FAIL] 目标库已有业务数据（项目：{preview}），未做清空。\n"
                    "       重复灌演示数据会与现有数据冲突；只追加示例项目请加 --append，"
                    "确认要整库重建请加 --reset。"
                )
                return 1
        counts = seed(conn)
        conn.commit()
        _drop_stale_mirror_files(conn, stale_files)
        print(f"目标库：{db_path}")
        print(" | ".join(f"{k} {v}" for k, v in counts.items()))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
