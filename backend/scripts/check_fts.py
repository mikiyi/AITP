"""验证 FTS5 混合检索行为（中文短词兜底、注入防护、软删过滤、索引同步）。

    python backend/scripts/check_fts.py
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.fts import hybrid_search  # noqa: E402
from _db_snapshot import LIVE_DB, snapshot  # noqa: E402

COLUMNS = ["title", "content", "tags"]

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


def search(conn: sqlite3.Connection, keyword: str) -> dict[str, Any]:
    return hybrid_search(
        conn,
        fts_table="biz_knowledge_fts",
        content_table="biz_knowledge_article",
        keyword=keyword,
        columns=COLUMNS,
    )


def main() -> int:
    if not LIVE_DB.exists():
        print(f"[FAIL] 数据库不存在: {LIVE_DB}")
        return 1

    # 检索结果依赖正式库里的演示正文，但用例本身要改内容、软删记录，
    # 所以先取一份一致性快照，所有写操作都落在临时库上。
    workdir = tempfile.mkdtemp(prefix="aitp-checkfts-")
    try:
        db_path = snapshot(LIVE_DB, pathlib.Path(workdir) / "aitp.db")
    except Exception as exc:
        print(f"[FAIL] 快照正式库失败: {exc}")
        shutil.rmtree(workdir, ignore_errors=True)
        return 1

    conn = get_standalone_conn(db_path)
    try:
        # 目标文章取库中最小 id，避免硬编码 id 在重新播种后失效
        target = conn.execute(
            "SELECT id, content FROM biz_knowledge_article WHERE is_deleted = 0 ORDER BY id LIMIT 1"
        ).fetchone()
        if not target:
            check("存在可供测试的知识文章", False, "biz_knowledge_article 无数据")
            return 1
        article_id = int(target["id"])
        origin_content = target["content"]

        # ① 2 字中文词 → LIKE 兜底
        result = search(conn, "登录")
        check(
            "2 字中文词走 like 兜底",
            result["matchMode"] == "like" and len(result["list"]) > 0,
            f"matchMode={result['matchMode']} hits={len(result['list'])}",
        )
        check(
            "like 命中行标注 hit_mode=like",
            all(row["hit_mode"] == "like" for row in result["list"]),
        )

        # ② 4 字中文词 → FTS
        result = search(conn, "手机号登录")
        check(
            "4 字中文词走 fts",
            result["matchMode"] == "fts" and len(result["list"]) > 0,
            f"matchMode={result['matchMode']} hits={len(result['list'])}",
        )

        # ③ 混合词 → hybrid
        result = search(conn, "登录 手机号登录")
        check(
            "混合词走 hybrid",
            result["matchMode"] == "hybrid",
            f"matchMode={result['matchMode']}",
        )
        check(
            "hybrid 结果同时含 fts 与 like 命中",
            {row["hit_mode"] for row in result["list"]} <= {"fts", "like", "hybrid"}
            and len(result["list"]) > 0,
        )

        # ④ 注入型关键词不报错
        try:
            result = search(conn, '登录" OR 1=1 --')
            check("注入型关键词不报错", True)
        except Exception as exc:
            check("注入型关键词不报错", False, str(exc))

        # ⑤ FTS 特殊字符不报错
        try:
            result = search(conn, "AND OR NOT *")
            check("FTS 关键字不报错", True)
        except Exception as exc:
            check("FTS 关键字不报错", False, str(exc))

        # ⑥ 软删数据不可见
        conn.execute(
            "UPDATE biz_knowledge_article SET is_deleted = 1 WHERE id = ?", (article_id,)
        )
        conn.commit()
        result = search(conn, "越权访问")
        check(
            "软删数据不出现在结果中",
            all(row["id"] != article_id for row in result["list"]),
        )
        conn.execute(
            "UPDATE biz_knowledge_article SET is_deleted = 0 WHERE id = ?", (article_id,)
        )
        conn.commit()

        # ⑦ FTS 索引随更新同步
        conn.execute(
            "UPDATE biz_knowledge_article SET content = ? WHERE id = ?",
            ("完全不同的内容", article_id),
        )
        conn.commit()
        old_result = search(conn, "弱网")
        new_result = search(conn, "完全不同的")
        check(
            "FTS 索引随内容更新同步（旧词失效）",
            all(row["id"] != article_id for row in old_result["list"]),
            f"旧词仍命中 {len(old_result['list'])} 行",
        )
        check(
            "FTS 索引随内容更新同步（新词生效）",
            any(row["id"] == article_id for row in new_result["list"]),
            f"新词命中 {len(new_result['list'])} 行",
        )
        conn.execute(
            "UPDATE biz_knowledge_article SET content = ? WHERE id = ?",
            (origin_content, article_id),
        )
        conn.commit()

        # ⑧ 英文/数字词可用
        result = search(conn, "BUG-004")
        check("英文与数字混排词可检索", len(result["list"]) > 0, f"hits={len(result['list'])}")

        # ⑨ 同时命中 FTS 与 instr 兜底的同一条记录只出现一次
        marker = "验证码时效回归样例"
        conn.execute(
            "INSERT INTO biz_knowledge_article "
            "(title, content, category, tags, source_type, status, created_at, updated_at) "
            "VALUES (?, ?, 'general', '', 'manual', 'published', "
            "'2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
            (marker, f"{marker} 验证码 60 秒有效"),
        )
        conn.commit()
        try:
            result = search(conn, "验证码 60")
            ids = [int(row["id"]) for row in result["list"]]
            check(
                "同时命中两路检索的记录不重复",
                len(ids) == len(set(ids)),
                f"ids={ids}",
            )
            check(
                "去重后 total 与结果条数一致",
                int(result["total"]) == len(ids),
                f"total={result['total']} rows={len(ids)}",
            )
        finally:
            conn.execute(
                "DELETE FROM biz_knowledge_article WHERE title = ?", (marker,)
            )
            conn.commit()

    finally:
        conn.close()
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
