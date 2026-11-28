"""RAG 评测纯函数（设计 §五）：召回@k、nDCG@k、忠实度。

- recall@k 只看「有没有召回到」，不区分第 1 位还是第 k 位；
- nDCG@k 用 graded relevance（0–3）衡量排序质量；
- 忠实度按断言计数：被检索片段支撑的断言 / 断言总数，引用缺失计未支撑。
"""

from __future__ import annotations

import math
from typing import Any


def recall_at_k(retrieved: list[str], relevant: list[str], k: int) -> float | None:
    """单查询 recall@k = 命中的相关文档数 / 相关文档总数。无相关文档返回 None。"""

    relevant_set = {str(item) for item in relevant if item}
    if not relevant_set:
        return None
    top = {str(item) for item in retrieved[:k] if item}
    return round(len(top & relevant_set) / len(relevant_set), 6)


def macro_recall(queries: list[dict[str, Any]], k: int) -> dict[str, Any]:
    """多查询 macro 平均（每查询等权，不被长相关列表的查询主导）。"""

    scores = [
        score
        for query in queries
        if (score := recall_at_k(list(query.get("retrieved") or []), list(query.get("relevant") or []), k))
        is not None
    ]
    if not scores:
        return {"value": None, "sample_size": 0}
    return {"value": round(sum(scores) / len(scores), 6), "sample_size": len(scores)}


def dcg(grades: list[float]) -> float:
    """标准 DCG：位置 p（1 基）的折扣是 1/log2(p+1)，位置越靠后折扣越大。"""

    return sum(float(grade) / math.log2(index + 2.0) for index, grade in enumerate(grades))


def ndcg_at_k(
    retrieved: list[str], graded_relevance: dict[str, int], k: int
) -> float | None:
    """单查询 nDCG@k。graded_relevance: docId → 0–3 等级。"""

    grades_map = {str(doc): int(grade) for doc, grade in graded_relevance.items()}
    ideal_grades = sorted((grade for grade in grades_map.values() if grade > 0), reverse=True)
    if not ideal_grades:
        return None
    actual_grades = [grades_map.get(str(doc), 0) for doc in retrieved[:k]]
    ideal_top = ideal_grades[:k]
    ideal = dcg([float(item) for item in ideal_top])
    if ideal <= 0:
        return None
    return round(min(1.0, dcg([float(item) for item in actual_grades]) / ideal), 6)


def macro_ndcg(queries: list[dict[str, Any]], k: int) -> dict[str, Any]:
    scores = [
        score
        for query in queries
        if (score := ndcg_at_k(
            list(query.get("retrieved") or []),
            dict(query.get("graded") or {}),
            k,
        ))
        is not None
    ]
    if not scores:
        return {"value": None, "sample_size": 0}
    return {"value": round(sum(scores) / len(scores), 6), "sample_size": len(scores)}


def faithfulness(claims: list[dict[str, Any]]) -> dict[str, Any]:
    """忠实度 = 被支撑断言数 / 断言总数。

    每条断言：{"text", "supported": bool, "cited": bool}；
    引用缺失（cited=False）一律计未支撑，防止无来源断言蒙混。
    """

    total = len(claims)
    if total == 0:
        return {"value": None, "sample_size": 0, "supported": 0, "unsupported": 0}
    supported = sum(1 for claim in claims if claim.get("supported") and claim.get("cited", True))
    unsupported = total - supported
    return {
        "value": round(supported / total, 6),
        "sample_size": total,
        "supported": supported,
        "unsupported": unsupported,
    }
