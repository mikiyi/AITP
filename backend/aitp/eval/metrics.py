"""指标纯函数：Wilson 区间、风险加权错误率、分位数。

口径（与《AITP Eval 子包设计》§五一致）：
  total   = 用例数
  errors  = 基础设施 / 模型不可用（verdict=error）
  skipped = 人工跳过（verdict=skip）
  invalid = judge 输出不可解析（verdict=invalid）
  valid   = total - errors - skipped - invalid

所有比例指标同时给 95% Wilson 区间；min 类门禁卡下界、max 类卡上界。
"""

from __future__ import annotations

import math
from typing import Any

SEVERITY_WEIGHTS: dict[str, float] = {"S0": 50.0, "S1": 10.0, "S2": 1.0}


def wilson_interval(successes: float, total: float, z: float = 1.96) -> tuple[float | None, float | None]:
    """95% Wilson 区间（小样本比正态近似稳）。total<=0 返回 (None, None)。"""

    if total <= 0:
        return None, None
    p = max(0.0, min(1.0, successes / total))
    denom = 1.0 + z * z / total
    center = (p + z * z / (2.0 * total)) / denom
    half = z * math.sqrt(p * (1.0 - p) / total + z * z / (4.0 * total * total)) / denom
    return max(0.0, center - half), min(1.0, center + half)


def ratio_metric(key: str, successes: float, total: float, unit: str = "ratio") -> dict[str, Any]:
    """一条比例指标（含区间）。total=0 时 value/lower/upper 全为 None。"""

    lower, upper = wilson_interval(successes, total)
    return {
        "metric_key": key,
        "value": (successes / total) if total > 0 else None,
        "ci_lower": lower,
        "ci_upper": upper,
        "ci_method": "wilson",
        "unit": unit,
        "numerator": float(successes),
        "denominator": float(total),
        "sample_size": int(total),
        "detail_json": "{}",
    }


def percentile(values: list[float], p: float) -> float | None:
    """线性插值分位数（p 取 0–100）。空列表返回 None。"""

    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (max(0.0, min(100.0, p)) / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def severity_weighted_error(
    cases_by_id: dict[int, dict[str, Any]], results: list[dict[str, Any]]
) -> dict[str, Any]:
    """风险加权错误率 = Σ(w_s × 该级失败数) / Σ(w_s × 该级用例数)，取值 ∈[0,1]。

    只统计 verdict=fail 的用例；error（环境问题）不计入分子，避免基础设施故障
    把「模型答得差」的分数推高。
    """

    weight_total = 0.0
    weight_failed = 0.0
    by_severity: dict[str, dict[str, int]] = {}
    for result in results:
        case = cases_by_id.get(int(result.get("case_id") or 0)) or {}
        severity = str(case.get("severity") or "S1")
        weight = SEVERITY_WEIGHTS.get(severity, 10.0)
        weight_total += weight
        bucket = by_severity.setdefault(severity, {"total": 0, "failed": 0})
        bucket["total"] += 1
        if result.get("verdict") == "fail":
            weight_failed += weight
            bucket["failed"] += 1

    value = (weight_failed / weight_total) if weight_total > 0 else None
    detail = {
        "bySeverity": {
            sev: {
                "total": item["total"],
                "failed": item["failed"],
                "weight": SEVERITY_WEIGHTS.get(sev, 10.0),
            }
            for sev, item in sorted(by_severity.items())
        }
    }
    return {
        "metric_key": "severity_weighted_error",
        "value": value,
        "ci_lower": None,
        "ci_upper": None,
        "ci_method": "bootstrap",
        "unit": "ratio",
        "numerator": weight_failed,
        "denominator": weight_total,
        "sample_size": len(results),
        "detail_json": detail,
    }


def compute_metrics(
    cases_by_id: dict[int, dict[str, Any]], results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """把一个 run 的逐条结果折成指标行（MVP 7 项）。"""

    total = len(results)
    passed = sum(1 for item in results if item.get("verdict") == "pass")
    errors = sum(1 for item in results if item.get("verdict") == "error")
    skipped = sum(1 for item in results if item.get("verdict") == "skip")
    invalid = sum(1 for item in results if item.get("verdict") == "invalid")
    valid = total - errors - skipped - invalid

    hallucinated = sum(
        1
        for item in results
        if item.get("verdict") == "fail"
        and str(item.get("failure_reason") or "").startswith("assert.hallucination")
    )
    latencies = [float(item.get("latency_ms") or 0) for item in results if item.get("verdict") != "skip"]
    costs = [float(item.get("cost_micros") or 0) for item in results if item.get("verdict") == "pass"]
    total_cost = sum(float(item.get("cost_micros") or 0) for item in results)

    rows: list[dict[str, Any]] = [
        ratio_metric("success_rate", passed, valid),
        ratio_metric("error_rate", errors, total),
        ratio_metric("judge_invalid_rate", invalid, total),
        ratio_metric("hallucination_rate", hallucinated, valid),
        severity_weighted_error(cases_by_id, results),
    ]

    rows.append(
        {
            "metric_key": "latency_p95",
            "value": percentile(latencies, 95),
            "ci_lower": None,
            "ci_upper": None,
            "ci_method": "exact",
            "unit": "ms",
            "numerator": None,
            "denominator": None,
            "sample_size": len(latencies),
            "detail_json": {
                "p50": percentile(latencies, 50),
                "p95": percentile(latencies, 95),
            },
        }
    )
    rows.append(
        {
            "metric_key": "cost_per_1k",
            "value": (total_cost / valid * 1000.0) if valid > 0 else None,
            "ci_lower": None,
            "ci_upper": None,
            "ci_method": "exact",
            "unit": "micros",
            "numerator": total_cost,
            "denominator": float(valid),
            "sample_size": len(costs),
            "detail_json": {"averageMicros": (total_cost / valid) if valid > 0 else None},
        }
    )
    return rows
