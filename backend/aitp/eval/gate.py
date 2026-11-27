"""评测门禁：阈值求值、三档结论。

判定方向（与设计 §七一致）：
  - min 类指标卡 **Wilson 下界**（点估计达标但下界不达标 → 不放行）；
  - max 类指标卡 **Wilson 上界**（对 max 用下界等于放宽，这是上一版评审抓到的错）；
  - 样本量分两级：minExplore 以下只出证据，minPublish 以下只出 warn。
"""

from __future__ import annotations

from typing import Any

DEFAULT_THRESHOLDS: dict[str, Any] = {
    "minExplore": 30,
    "minPublish": 200,
    "successRate": {"min": 0.85},
    "errorRate": {"max": 0.05},
    "judgeInvalidRate": {"max": 0.05},
    "hallucinationRate": {"max": 0.05},
    "severityWeightedError": {"max": 0.30},
    "latencyP95Ms": {"max": 3000.0},
}

#: 门禁结论的「谁更严重」排序，聚合时取最严
CONCLUSION_ORDER = {"pass": 0, "insufficient": 1, "warn": 2, "fail": 3}

#: 阈值键 → 指标 key（阈值用 camelCase 便于前端，指标表用 snake_case）
METRIC_KEYS = {
    "successRate": "success_rate",
    "errorRate": "error_rate",
    "judgeInvalidRate": "judge_invalid_rate",
    "hallucinationRate": "hallucination_rate",
    "severityWeightedError": "severity_weighted_error",
    "latencyP95Ms": "latency_p95",
    "costPer1kMicros": "cost_per_1k",
}


def _merge(thresholds: dict[str, Any] | None) -> dict[str, Any]:
    """显式阈值以调用方为准：未配置的指标不参与判定，避免默认值悄悄加严。

    只补样本分级两个阈值——没有它们门禁无法判断「这份结果该不该出结论」。
    """

    if thresholds is None:
        return {key: (dict(value) if isinstance(value, dict) else value) for key, value in DEFAULT_THRESHOLDS.items()}
    merged: dict[str, Any] = {}
    for key, value in thresholds.items():
        if key in METRIC_KEYS or key.startswith("min"):
            merged[key] = value
    merged.setdefault("minExplore", DEFAULT_THRESHOLDS["minExplore"])
    merged.setdefault("minPublish", DEFAULT_THRESHOLDS["minPublish"])
    return merged


def evaluate_gate(
    metrics: dict[str, dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """metrics: metric_key → 指标行（含 value/ci_lower/ci_upper/sample_size）。"""

    config = _merge(thresholds)
    checks: list[dict[str, Any]] = []

    for threshold_key, metric_key in METRIC_KEYS.items():
        rule = config.get(threshold_key)
        if not isinstance(rule, dict):
            continue
        row = metrics.get(metric_key)
        if row is None or row.get("value") is None:
            checks.append(
                {
                    "key": threshold_key,
                    "metric": metric_key,
                    "value": None,
                    "limit": rule,
                    "passed": False,
                    "reason": "指标缺失或无有效样本",
                }
            )
            continue

        value = float(row["value"])
        if "min" in rule:
            limit = float(rule["min"])
            lower = row.get("ci_lower")
            judged = float(lower) if isinstance(lower, (int, float)) else value
            passed = judged >= limit
            basis = "ci_lower"
        else:
            limit = float(rule.get("max", 0.0))
            upper = row.get("ci_upper")
            judged = float(upper) if isinstance(upper, (int, float)) else value
            passed = judged <= limit
            basis = "ci_upper"
        checks.append(
            {
                "key": threshold_key,
                "metric": metric_key,
                "value": value,
                "judged": judged,
                "basis": basis,
                "limit": limit,
                "passed": passed,
            }
        )

    sample_size = int(metrics.get("success_rate", {}).get("sample_size") or 0)
    min_explore = int(config.get("minExplore", 30) or 0)
    min_publish = int(config.get("minPublish", 200) or 0)
    failures = [item for item in checks if not item["passed"]]
    notes: list[str] = []

    if sample_size < min_explore:
        conclusion = "insufficient"
        notes.append(f"有效样本 {sample_size} < {min_explore}：只出逐条证据，不出成败结论")
    elif sample_size < min_publish:
        conclusion = "warn"
        notes.append(
            f"有效样本 {sample_size} < {min_publish}：点估计与区间仅供参考，"
            f"{len(failures)} 项阈值未达标但不据此判 fail"
        )
    elif failures:
        conclusion = "fail"
    else:
        conclusion = "pass"

    return {
        "conclusion": conclusion,
        "checks": checks,
        "failures": failures,
        "sampleSize": sample_size,
        "minExplore": min_explore,
        "minPublish": min_publish,
        "notes": notes,
    }
