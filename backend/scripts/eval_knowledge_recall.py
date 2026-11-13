"""知识召回评估（三期 D1 / GAP-38 启动门槛）。

用真实库里的知识条目做一组「字面 + 同义/多词」查询，分别走两条现有链路：
  - 全局搜索 search_service.search_knowledge
  - AI 上下文装配器 context_assembler._knowledge_blocks

只读打开数据库，不写任何数据；命中判定按「返回标题包含任一期望标题片段」。
评估结论用于决定 GAP-38 是降级为召回增强，还是引入本地嵌入模型。

    python backend/scripts/eval_knowledge_recall.py [--db data/aitp.db] [--limit 3]
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.services import context_assembler, search_service  # noqa: E402

#: kind = literal 为字面控制组；synonym 为同义 / 自然语言组
CASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("literal", "支付回调幂等", ("支付回调幂等", "支付回调重复通知")),
    ("literal", "登录验证码", ("手机号登录测试用例", "登录模块需求讨论纪要")),
    ("literal", "金额精度", ("订单金额计算的浮点陷阱",)),
    ("literal", "越权访问", ("越权访问的常见形态",)),
    ("literal", "异步加载", ("弱网场景的界面反馈规范",)),
    ("synonym", "第三方付款通知连续推三次，余额为什么只加一次",
     ("支付回调幂等保护约定", "支付回调重复通知")),
    ("synonym", "账号凭证到期后怎么处理",
     ("登录模块需求讨论纪要", "手机号登录测试用例", "弱网环境下登录超时")),
    ("synonym", "页面卡住一直转圈时怎么提示用户", ("弱网场景的界面反馈规范",)),
    ("synonym", "金额计算为什么不能用小数", ("订单金额计算的浮点陷阱",)),
    ("synonym", "普通用户能不能调管理员接口", ("越权访问的常见形态",)),
    ("synonym", "重复提交表单怎么防止", ("弱网场景的界面反馈规范", "支付回调重复通知")),
    ("synonym", "服务日志里怎么定位用户报错", ("支付回调重复推送怎么定位",)),
    ("synonym", "页面转圈卡死怎么处理", ("弱网场景的界面反馈规范",)),
    ("synonym", "付款通知连续到了三次余额多了", ("支付回调幂等保护约定", "支付回调重复通知")),
    ("synonym", "账号凭证失效了怎么办", ("登录模块需求讨论纪要", "手机号登录测试用例")),
    ("synonym", "小数计算金额为什么会有误差", ("订单金额计算的浮点陷阱",)),
    ("synonym", "后台接口普通账号能调吗", ("越权访问的常见形态",)),
)


def _hit(titles: list[str], expected: tuple[str, ...]) -> bool:
    return any(any(part in title for part in expected) for title in titles)


def _titles(items: list[dict[str, Any]]) -> list[str]:
    return [str(item.get("title") or "") for item in items]


def evaluate(db_path: pathlib.Path, limit: int) -> int:
    conn = sqlite3.connect(f"{db_path.resolve().as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    totals = {"literal": 0, "synonym": 0}
    hits = {"literal": 0, "synonym": 0}
    search_hits = {"literal": 0, "synonym": 0}
    ctx_hits = {"literal": 0, "synonym": 0}
    rows: list[tuple[str, str, bool, bool, list[str], list[str]]] = []
    try:
        for kind, query, expected in CASES:
            items, _ = search_service.search_knowledge(conn, query, limit)
            blocks = context_assembler._knowledge_blocks(
                conn, keywords=context_assembler.keywords_of(query), limit=limit)
            search_titles = _titles(items)
            ctx_titles = [str(block.title or "") for block in blocks]
            ok_search = _hit(search_titles, expected)
            ok_ctx = _hit(ctx_titles, expected)
            totals[kind] += 1
            hits[kind] += 1 if (ok_search or ok_ctx) else 0
            search_hits[kind] += 1 if ok_search else 0
            ctx_hits[kind] += 1 if ok_ctx else 0
            rows.append((kind, query, ok_search, ok_ctx, search_titles, ctx_titles))
    finally:
        conn.close()

    print(f"{'类型':<8} {'搜索':<4} {'装配':<4} 查询")
    print("-" * 88)
    for kind, query, ok_search, ok_ctx, search_titles, ctx_titles in rows:
        print(f"{kind:<8} {'✓' if ok_search else '✗':<4} {'✓' if ok_ctx else '✗':<4} {query}")
        print(f"         搜索：{' | '.join(t[:30] for t in search_titles) or '∅'}")
        print(f"         装配：{' | '.join(t[:30] for t in ctx_titles) or '∅'}")
    print("-" * 88)
    for kind in ("literal", "synonym"):
        rate = hits[kind] / totals[kind] * 100 if totals[kind] else 0.0
        search_rate = search_hits[kind] / totals[kind] * 100 if totals[kind] else 0.0
        ctx_rate = ctx_hits[kind] / totals[kind] * 100 if totals[kind] else 0.0
        print(f"{kind:<8} 命中 {hits[kind]}/{totals[kind]}（{rate:.0f}%）"
              f"　搜索 {search_hits[kind]}/{totals[kind]}（{search_rate:.0f}%）"
              f"　装配 {ctx_hits[kind]}/{totals[kind]}（{ctx_rate:.0f}%）")
    overall = sum(hits.values()) / sum(totals.values()) * 100
    print(f"整体     命中 {sum(hits.values())}/{sum(totals.values())}（{overall:.0f}%）")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", default=str(ROOT / "data" / "aitp.db"))
    parser.add_argument("--limit", type=int, default=3)
    args = parser.parse_args()
    return evaluate(pathlib.Path(args.db), max(1, args.limit))


if __name__ == "__main__":
    raise SystemExit(main())
