"""知识召回回归：中文复合查询的字面召回不许再漏。

背景（三期 D1 / GAP-38 评估）：中文查询没有空格时，旧逻辑把整串当成一个连续短语，
「登录验证码」「金额精度」「异步加载」在正文分开出现时必然 0 命中。修复后要求：
  - 2~4 字 n-gram 兜底只在整串 0 命中时生效；
  - 全局搜索与上下文装配器都能召回；
  - 无关查询仍为空，不能被兜底放大成误报。

    python backend/scripts/check_knowledge_recall.py
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.common.knowledge_lexicon import expand_synonyms  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.db.fts import hybrid_search  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import context_assembler, search_service  # noqa: E402

#: 与真实库同口径的 8 条知识（标题 + 摘要即正文），覆盖支付/登录/金额/越权/弱网
ARTICLES: tuple[tuple[str, str, str], ...] = (
    ("支付回调重复通知的处理方案", "支付网关可能重复推送回调，必须以订单号做幂等控制：先查后写 + 唯一索引 + 状态机校验。", "payment"),
    ("支付回调幂等排查结论", "先查后写 + 订单号唯一索引 + 状态机三重校验，缺一不可。", "payment"),
    ("支付回调幂等保护约定", "支付成功回调可能 30 秒内重复到达，后端须按订单号做幂等，重复通知只入账一次。", "payment"),
    ("登录模块需求讨论纪要", "本次讨论聚焦手机号登录的边界场景，包括验证码 60 秒倒计时、空格与全角字符、手机号前导 0，以及并发登录同一账号。", "login"),
    ("手机号登录测试用例设计要点", "手机号登录的测试重点包括：验证码时效、错误次数限制、异地风控提示、同一账号多端登录。验证码登录失败 5 次后锁定 15 分钟。", "login"),
    ("订单金额计算的浮点陷阱", "金额计算禁止使用浮点数，统一用分为单位的整数或 Decimal，避免 0.1+0.2 问题。", "money"),
    ("越权访问的常见形态", "水平越权：改 URL 中的订单号；垂直越权：普通用户调用管理接口。测试时至少覆盖自属与他属两类数据。", "security"),
    ("弱网场景的界面反馈规范", "弱网下所有异步操作必须展示 loading 态并禁用重复提交，失败后给出可重试提示。", "ui"),
)

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


def _seed(db_path: pathlib.Path) -> None:
    init_db(db_path)
    now = utcnow_iso()
    conn = sqlite3.connect(db_path)
    try:
        for title, body, category in ARTICLES:
            conn.execute(
                "INSERT INTO biz_knowledge_article"
                " (title, content, summary, category, tags, source_type, review_status,"
                "  status, created_at, updated_at, is_deleted)"
                " VALUES (?, ?, ?, ?, '', 'manual', 'published', 'published', ?, ?, 0)",
                (title, body, body, category, now, now),
            )
        conn.execute("INSERT INTO biz_knowledge_fts(biz_knowledge_fts) VALUES('rebuild')")
        conn.commit()
    finally:
        conn.close()


def _titles(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("title") or "") for item in items]


def _hit(titles: list[str], expected: tuple[str, ...]) -> bool:
    return any(any(part in title for part in expected) for title in titles)


def run(db_path: pathlib.Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        cases = (
            ("登录验证码", ("手机号登录测试用例", "登录模块需求讨论纪要")),
            ("金额精度", ("订单金额计算的浮点陷阱",)),
            ("异步加载", ("弱网场景的界面反馈规范",)),
            ("普通用户能不能调管理员接口", ("越权访问的常见形态",)),
            ("页面转圈卡死怎么处理", ("弱网场景的界面反馈规范",)),
            ("付款通知连续到了三次余额多了", ("支付回调幂等保护约定", "支付回调重复通知")),
            ("账号凭证失效了怎么办", ("登录模块需求讨论纪要", "手机号登录测试用例")),
            ("小数计算金额为什么会有误差", ("订单金额计算的浮点陷阱",)),
        )
        for query, expected in cases:
            items, total = search_service.search_knowledge(conn, query, 3)
            check(f"全局搜索召回「{query}」", total > 0 and _hit(_titles(items), expected),
                  str(_titles(items)))
            hybrid = hybrid_search(
                conn, fts_table="biz_knowledge_fts", content_table="biz_knowledge_article",
                keyword=query, columns=["title", "content", "tags"],
                extra_where="review_status = 'published'", page_size=3, fallback_on_empty=True,
                synonyms=expand_synonyms(query),
            )
            check(f"FTS 兜底召回「{query}」",
                  hybrid["total"] > 0 and _hit(_titles(hybrid["list"]), expected),
                  str(_titles(hybrid["list"])))

        blocks = context_assembler._knowledge_blocks(
            conn, keywords=context_assembler.keywords_of("页面卡住一直转圈时怎么提示用户"), limit=3)
        check("装配器召回自然语言问句",
              _hit([str(block.title or "") for block in blocks], ("弱网场景的界面反馈规范",)),
              str([block.title for block in blocks]))

        empty_items, empty_total = search_service.search_knowledge(conn, "量子纠缠火箭发动机", 3)
        check("无关查询不被兜底放大", empty_total == 0 and not empty_items,
              f"total={empty_total} {_titles(empty_items)}")
        empty_hybrid = hybrid_search(
            conn, fts_table="biz_knowledge_fts", content_table="biz_knowledge_article",
            keyword="量子纠缠火箭发动机", columns=["title", "content", "tags"],
            extra_where="review_status = 'published'", page_size=3, fallback_on_empty=True,
        )
        check("FTS 无关查询保持为空", empty_hybrid["total"] == 0,
              str(_titles(empty_hybrid["list"])))

        exact, _ = search_service.search_knowledge(conn, "支付回调幂等", 3)
        check("精确查询排序不回归",
              bool(exact) and "支付回调幂等" in str(exact[0].get("title") or ""),
              str(_titles(exact)))
    finally:
        conn.close()


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-recall-"))
    try:
        db_path = workdir / "recall.db"
        _seed(db_path)
        run(db_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"知识召回：PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
