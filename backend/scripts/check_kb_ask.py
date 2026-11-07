"""知识库问答检索兜底回归门禁（自然语言问句能召回正式条目）。

复现并锁死这个 bug：「支付回调幂等排查结论是什么样子？」走「基于知识库回答」
回「没有检索到」，「知识库+大模型补强」标「未引用知识库」——因为 trigram 把整句
当成一个连续短语要求原文命中，必然 0。

    python backend/scripts/check_kb_ask.py

只读：用 SQLite 备份 API 复制一份临时库，直接调 fts.hybrid_search 与
lab_service.retrieve_knowledge_context，全程不重启服务、不写 8760。
断言分两类：
  1) 兜底开关默认关闭时主路径行为不变（整句仍 0）；
  2) 问答链路开启兜底后能命中目标条目、整句问法能召回若干条；
  3) 所有兜底命中仍受 review_status='published' 外层过滤约束，待审/撤销不泄漏。
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
from aitp.services import lab_service  # noqa: E402
from _db_snapshot import LIVE_DB, snapshot  # noqa: E402

TARGET_TITLE = "支付回调幂等排查结论"
QUESTION = "支付回调幂等排查结论是什么样子？"
SENTENCE = "登录失败有哪些常见原因？"

COLUMNS = ["title", "content", "tags"]
PUBLISHED = "review_status = 'published'"

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


def _search(conn: sqlite3.Connection, keyword: str, *, fallback: bool) -> dict[str, Any]:
    return hybrid_search(
        conn,
        fts_table="biz_knowledge_fts",
        content_table="biz_knowledge_article",
        keyword=keyword,
        columns=COLUMNS,
        extra_where=PUBLISHED,
        extra_params=(),
        page_size=5,
        fallback_on_empty=fallback,
    )


def main() -> int:
    if not LIVE_DB.exists():
        print(f"[FAIL] 数据库不存在: {LIVE_DB}")
        return 1

    workdir = tempfile.mkdtemp(prefix="aitp-kbask-")
    try:
        db_path = snapshot(LIVE_DB, pathlib.Path(workdir) / "aitp.db")
    except Exception as exc:
        print(f"[FAIL] 快照正式库失败: {exc}")
        shutil.rmtree(workdir, ignore_errors=True)
        return 1

    conn = get_standalone_conn(db_path)
    conn.execute("PRAGMA query_only = ON")
    try:
        # 前置：目标条目必须是已发布正式条目（bug 的前提）
        target = conn.execute(
            "SELECT id FROM biz_knowledge_article"
            " WHERE title = ? AND review_status = 'published' AND is_deleted = 0",
            (TARGET_TITLE,),
        ).fetchone()
        check("存在已发布的正式条目「支付回调幂等排查结论」", target is not None)
        if target is None:
            print("-" * 40)
            print(f"PASS={_passed} FAIL={_failed}")
            return 1
        target_id = int(target["id"])

        # ① 主路径（兜底关闭）整句仍 0 —— 锁死默认语义，check_fts / 管理页不受影响
        primary = _search(conn, QUESTION, fallback=False)
        check("兜底关闭时主路径整句命中 0（默认语义不变）", primary["total"] == 0,
              f"total={primary['total']}")

        # ② 问答链路兜底命中目标条目
        fb = _search(conn, QUESTION, fallback=True)
        fb_ids = [int(row["id"]) for row in fb["list"]]
        check("兜底开启后整句问法能命中目标条目", target_id in fb_ids, f"ids={fb_ids}")
        check("兜底命中行全部为 published（外层 where 生效）",
              _all_published(conn, fb_ids))

        # ③ 走问答链路本体：带出处（refs 含目标条目）
        context, refs, err = lab_service.retrieve_knowledge_context(conn, QUESTION, limit=5)
        ref_ids = [int(r["articleId"]) for r in refs if r.get("articleId") is not None]
        check("retrieve_knowledge_context 返回非空上下文", bool(context))
        check("retrieve_knowledge_context 引用目标条目", target_id in ref_ids,
              f"ref_ids={ref_ids}")
        check("retrieve_knowledge_context 无检索失败", err == "", f"err={err}")

        # ④ 另一类整句问法也能召回若干条（check_ai_live 用的问句）
        ctx2, refs2, err2 = lab_service.retrieve_knowledge_context(conn, SENTENCE, limit=5)
        check("整句「登录失败…」能召回若干条", len(refs2) >= 1, f"refs={len(refs2)}")
        check("整句召回无检索失败", err2 == "", f"err={err2}")
        if refs2:
            check("整句召回行均为 published",
                  _all_published(conn, [int(r["articleId"]) for r in refs2 if r.get("articleId") is not None]))

        # ⑤ 空问句短路，不触发检索
        ctx3, refs3, err3 = lab_service.retrieve_knowledge_context(conn, "   ")
        check("空白问句返回空上下文", ctx3 == "" and refs3 == [] and err3 == "")

    finally:
        conn.close()
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


def _all_published(conn: sqlite3.Connection, ids: list[int]) -> bool:
    if not ids:
        return True
    placeholders = ",".join("?" for _ in ids)
    bad = conn.execute(
        f"SELECT COUNT(*) FROM biz_knowledge_article"
        f" WHERE id IN ({placeholders}) AND (review_status <> 'published' OR is_deleted <> 0)",
        ids,
    ).fetchone()
    return int(bad[0]) == 0


if __name__ == "__main__":
    sys.exit(main())
