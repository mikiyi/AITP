"""评测门禁：阈值求值、三档结论（设计 §七）。

判定方向：
  - min 类指标卡 **Wilson 下界**（点估计达标但下界不达标 → 不放行）；
  - max 类指标卡 **Wilson 上界**（对 max 用下界等于放宽，这是上一版评审抓到的错）；
  - 样本量分两级：minExplore 以下只出证据，minPublish 以下只出 warn。

族错误率控制：18 项指标同时比、α=0.05，族错误率会超过 60%。因此只有 5 个
预注册主指标能把结论打成 fail；其余 13 项越线只降为 warn，作描述性证据。
"""

from __future__ import annotations

import json
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
METRIC_KEYS: dict[str, str] = {
    "successRate": "success_rate",
    "errorRate": "error_rate",
    "judgeInvalidRate": "judge_invalid_rate",
    "hallucinationRate": "hallucination_rate",
    "severityWeightedError": "severity_weighted_error",
    "instructionFollowRate": "instruction_follow_rate",
    "toolCallF1": "tool_call_f1",
    "toolArgAccuracy": "tool_arg_accuracy",
    "stepsAvg": "steps_avg",
    "redundantStepRatio": "redundant_step_ratio",
    "ragRecallAtK": "rag_recall@5",
    "ragNdcgAtK": "rag_ndcg@5",
    "faithfulness": "faithfulness",
    "refusalAccuracy": "refusal_accuracy",
    "judgeAgreement": "judge_agreement",
    "falsePassRate": "false_pass_rate",
    "latencyP95Ms": "latency_p95",
    "costPer1kMicros": "cost_per_1k",
}

#: 预注册主指标（设计 §七）：只有这 5 项能把结论打成 fail
PRIMARY_METRICS: frozenset[str] = frozenset(
    {
        "successRate",
        "severityWeightedError",
        "hallucinationRate",
        "falsePassRate",
        "refusalAccuracy",
    }
)

#: 不参与阈值求值的配置键
_CONFIG_KEYS = {"minExplore", "minPublish", "judgeCalibrationRequired", "primaryMetrics", "regressionPolicy"}


def _merge(thresholds: dict[str, Any] | None) -> dict[str, Any]:
    """显式阈值以调用方为准：未配置的指标不参与判定，避免默认值悄悄加严。

    只补样本分级两个阈值——没有它们门禁无法判断「这份结果该不该出结论」。
    """

    if thresholds is None:
        return {key: (dict(value) if isinstance(value, dict) else value) for key, value in DEFAULT_THRESHOLDS.items()}
    merged: dict[str, Any] = {}
    for key, value in thresholds.items():
        if key in METRIC_KEYS or key in _CONFIG_KEYS or key.startswith("min"):
            merged[key] = value
    merged.setdefault("minExplore", DEFAULT_THRESHOLDS["minExplore"])
    merged.setdefault("minPublish", DEFAULT_THRESHOLDS["minPublish"])
    return merged


def _detail(row: dict[str, Any]) -> dict[str, Any]:
    """指标行的 detail_json 落库后是字符串，内存里是 dict，两种都兼容。"""

    raw = row.get("detail_json")
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str) and raw:
        try:
            parsed = json.loads(raw)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError):
            return {}
    return {}


def evaluate_gate(
    metrics: dict[str, dict[str, Any]],
    thresholds: dict[str, Any] | None = None,
    *,
    judge_calibrated: bool | None = None,
    provider_cost: bool | None = None,
    regression: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """metrics: metric_key → 指标行（含 value/ci_lower/ci_upper/sample_size）。

    可选上下文：
      judge_calibrated — judge 校准三件套是否达标（False 且门禁要求 → warn）；
      provider_cost    — 成本是否来自 provider 账单（False 时估算成本不进门禁）；
      regression       — paired_test 输出，hardAssertionRegression=True → warn。
    """

    config = _merge(thresholds)
    checks: list[dict[str, Any]] = []

    for threshold_key, metric_key in METRIC_KEYS.items():
        rule = config.get(threshold_key)
        if not isinstance(rule, dict):
            continue
        row = metrics.get(metric_key)
        primary = threshold_key in PRIMARY_METRICS
        if row is None or row.get("value") is None:
            checks.append(
                {
                    "key": threshold_key,
                    "metric": metric_key,
                    "primary": primary,
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
        check = {
            "key": threshold_key,
            "metric": metric_key,
            "primary": primary,
            "value": value,
            "judged": judged,
            "basis": basis,
            "limit": limit,
            "passed": passed,
        }

        # judgeAgreement 额外卡 κ 下界（一致率达标但 κ 不够同样不放行）
        if passed and threshold_key == "judgeAgreement" and rule.get("kappaMin") is not None:
            kappa = _detail(row).get("kappa")
            if kappa is None or float(kappa) < float(rule["kappaMin"]):
                passed = False
                check["passed"] = False
                check["reason"] = f"Cohen's κ={kappa} 低于 {rule['kappaMin']}"

        # 估算成本不允许进成本门禁（设计 §九.9）
        if (
            threshold_key == "costPer1kMicros"
            and rule.get("requireProviderCost")
            and provider_cost is False
        ):
            check["passed"] = False
            check["reason"] = "成本为估算值（cost_source=estimated），不满足 provider 成本门禁"
        checks.append(check)

    sample_size = int(metrics.get("success_rate", {}).get("sample_size") or 0)
    min_explore = int(config.get("minExplore", 30) or 0)
    min_publish = int(config.get("minPublish", 200) or 0)
    failures = [item for item in checks if not item["passed"]]
    primary_failures = [item for item in failures if item.get("primary")]
    notes: list[str] = []

    judge_required = bool(config.get("judgeCalibrationRequired"))
    judge_uncalibrated = judge_required and judge_calibrated is False
    if judge_uncalibrated:
        notes.append("judge 未完成校准（一致率/κ/false-pass 三件套未同时达标）")

    hard_regression = bool((regression or {}).get("hardAssertionRegression"))
    if hard_regression:
        regressed = (regression or {}).get("regressed") or []
        notes.append(f"硬断言发生正向→反向翻转 {len(regressed)} 条，逐条清单见对比报告")

    if sample_size < min_explore:
        conclusion = "insufficient"
        notes.append(f"有效样本 {sample_size} < {min_explore}：只出逐条证据，不出成败结论")
    elif sample_size < min_publish:
        conclusion = "warn"
        notes.append(
            f"有效样本 {sample_size} < {min_publish}：点估计与区间仅供参考，"
            f"{len(failures)} 项阈值未达标但不据此判 fail"
        )
    elif primary_failures:
        conclusion = "fail"
        notes.append(f"{len(primary_failures)} 个预注册主指标越线："
                     f"{', '.join(item['key'] for item in primary_failures)}")
    elif failures or judge_uncalibrated or hard_regression:
        conclusion = "warn"
        if failures:
            notes.append(f"{len(failures)} 项描述性指标越线（非主指标，不判 fail）")
    else:
        conclusion = "pass"

    return {
        "conclusion": conclusion,
        "checks": checks,
        "failures": failures,
        "primaryFailures": primary_failures,
        "sampleSize": sample_size,
        "minExplore": min_explore,
        "minPublish": min_publish,
        "notes": notes,
    }
