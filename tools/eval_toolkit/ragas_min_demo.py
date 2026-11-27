"""Ragas 最小 demo（离线可跑）：金融知识 RAG 的 faithfulness / context precision / context recall。

为什么不用 `pip install ragas` 直接跑：ragas 的 faithfulness 需要 LLM 做「claim 判定」，
离线环境没有模型与 key。本脚本把 ragas 的三个指标按**同一套公式**本地实现，先让口径跑通；
装了 ragas 与模型后，`--use-ragas` 会给出等价的官方调用方式（见文件末尾注释）。

指标定义（与 ragas 文档一致）：
  faithfulness       = 被检索片段支撑的 claim 数 / 总 claim 数
  context_precision  = 检索结果里相关的比例（按排名加权时即 AP）
  context_recall     = 标准答案依赖的片段里，被召回的比例
  answer_relevancy   = 答案与问题的语义相关度（本地用词面重合近似，仅作演示）

用法：python ragas_min_demo.py
"""

from __future__ import annotations

import argparse
import re

DOCS = {
    "D1": "A 股普通交易实行 T+1 交收：T 日成交，T+1 日完成资金与证券交收。来源：交易所结算规则。",
    "D2": "股票交易费用包括佣金与印花税：佣金不超过成交金额的 0.3%，印花税在卖出时按 0.05% 收取。",
    "D3": "投资者适当性要求：向客户销售产品前须完成风险测评，产品风险等级不得高于客户承受等级。",
}

CASES = [
    {
        "question": "A 股普通交易几天交收？",
        "answer": "A 股普通交易为 T+1 交收，T 日成交、T+1 日交收。来源：交易所结算规则",
        "gold_docs": ["D1"],
    },
    {
        "question": "股票卖出要交什么费用？",
        "answer": "卖出股票需要缴纳印花税，按成交金额的 0.05% 收取；佣金不超过成交金额的 0.3%。",
        "gold_docs": ["D2"],
    },
    {
        "question": "风险等级 C2 的客户能买 R4 产品吗？",
        # 故意编造一条无来源断言，用来演示 faithfulness 掉分
        "answer": "不能购买。产品风险等级不得高于客户承受等级。另据内部数据，R4 产品年化收益可达 15%。",
        "gold_docs": ["D3"],
    },
]


def _tokens(text: str) -> set[str]:
    # 中文按 2-gram 切，英文数字按单词；够用的词面近似
    text = re.sub(r"\s+", "", text)
    grams = {text[i : i + 2] for i in range(len(text) - 1)}
    words = set(re.findall(r"[A-Za-z0-9.%]+", text))
    return grams | words


def retrieve(question: str, k: int = 2) -> list[str]:
    scored = []
    q = _tokens(question)
    for doc_id, text in DOCS.items():
        overlap = len(q & _tokens(text))
        scored.append((overlap, doc_id))
    scored.sort(reverse=True)
    return [doc_id for score, doc_id in scored[:k] if score > 0] or [scored[0][1]]


def split_claims(answer: str) -> list[str]:
    parts = [p.strip() for p in re.split(r"[。；;！!\n]", answer) if p.strip()]
    return parts


def faithfulness(answer: str, contexts: list[str]) -> tuple[float, list[tuple[str, bool]]]:
    context_text = "".join(contexts)
    ctx_tokens = _tokens(context_text)
    details = []
    for claim in split_claims(answer):
        claim_tokens = _tokens(claim)
        if not claim_tokens:
            continue
        grounded = len(claim_tokens & ctx_tokens) / len(claim_tokens) >= 0.6
        details.append((claim, grounded))
    if not details:
        return 0.0, []
    return sum(1 for _, ok in details if ok) / len(details), details


def context_precision(retrieved: list[str], gold: list[str]) -> float:
    if not retrieved:
        return 0.0
    return sum(1 for doc in retrieved if doc in gold) / len(retrieved)


def context_recall(retrieved: list[str], gold: list[str]) -> float:
    if not gold:
        return 1.0
    return sum(1 for doc in gold if doc in retrieved) / len(gold)


def answer_relevancy(question: str, answer: str) -> float:
    q, a = _tokens(question), _tokens(answer)
    if not q:
        return 0.0
    return len(q & a) / len(q)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--use-ragas", action="store_true", help="打印官方 ragas 的等价调用方式")
    args = parser.parse_args()

    print("# RAG 最小评测（本地等价实现）\n")
    print("| 问题 | 召回 | faithfulness | ctx_precision | ctx_recall | relevancy |")
    print("|---|---|---|---|---|---|")
    for case in CASES:
        retrieved = retrieve(case["question"])
        contexts = [DOCS[d] for d in retrieved]
        faith, details = faithfulness(case["answer"], contexts)
        print(
            f"| {case['question']} | {','.join(retrieved)} | {faith:.2f} | "
            f"{context_precision(retrieved, case['gold_docs']):.2f} | "
            f"{context_recall(retrieved, case['gold_docs']):.2f} | "
            f"{answer_relevancy(case['question'], case['answer']):.2f} |"
        )
        for claim, ok in details:
            mark = "✓" if ok else "✗ 无支撑"
            print(f"    - {mark}：{claim}")

    print("\n注：第 3 条的「另据内部数据，R4 产品年化收益可达 15%」没有来源，faithfulness 应下降。")
    if args.use_ragas:
        print(
            "\n官方 ragas 等价写法（需 API key）：\n"
            "    from ragas import evaluate\n"
            "    from ragas.metrics import faithfulness, context_precision, context_recall, answer_relevancy\n"
            "    evaluate(dataset, metrics=[faithfulness, context_precision, context_recall, answer_relevancy])\n"
            "dataset 需含 question / answer / contexts / ground_truth 四个字段。"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
