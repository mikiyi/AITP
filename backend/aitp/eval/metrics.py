"""指标纯函数：18 项指标、95% Wilson 区间、风险加权、分位数。

口径（与《AITP Eval 子包设计》§五一致）：
  total   = 用例数
  errors  = 基础设施 / 模型不可用（verdict=error）
  skipped = 人工跳过（verdict=skip）
  invalid = judge 输出不可解析（verdict=invalid）
  valid   = total - errors - skipped - invalid

所有比例指标同时给 95% Wilson 区间；min 类门禁卡下界、max 类卡上界。
输入约定：results 是逐条结果（可带 tool_trace/retrieved/claims/rubric/
人评等富字段），cases_by_id 提供用例侧的期望与分级；缺数据的指标
出 value=None + detail.reason=insufficient_data，不伪造 0。
"""

from __future__ import annotations

import math
from typing import Any

from .judges import cohen_kappa
from .rag import faithfulness as rag_faithfulness
from .rag import macro_ndcg, macro_recall
from .severity import SEVERITY_WEIGHTS, weight_of
from .traces import arg_accuracy, tool_prf

#: 18 项指标的固定顺序（/evals/meta 与报告同源；换 k 的 RAG 指标另算一条）
METRIC_KEYS: tuple[str, ...] = (
    "success_rate",
    "error_rate",
    "judge_invalid_rate",
    "hallucination_rate",
    "severity_weighted_error",
    "instruction_follow_rate",
    "tool_call_f1",
    "tool_arg_accuracy",
    "steps_avg",
    "redundant_step_ratio",
    "rag_recall@5",
    "rag_ndcg@5",
    "faithfulness",
    "refusal_accuracy",
    "judge_agreement",
    "false_pass_rate",
    "latency_p95",
    "cost_per_1k",
)
RAG_K = 5


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


def _unavailable(key: str, unit: str, reason: str = "insufficient_data", **detail: Any) -> dict[str, Any]:
    payload = {"reason": reason, **detail}
    return {
        "metric_key": key,
        "value": None,
        "ci_lower": None,
        "ci_upper": None,
        "ci_method": "wilson" if unit == "ratio" else "exact",
        "unit": unit,
        "numerator": None,
        "denominator": None,
        "sample_size": 0,
        "detail_json": payload,
    }


def _percentile_sorted(ordered: list[float], p: float) -> float | None:
    if not ordered:
        return None
    if len(ordered) == 1:
        return ordered[0]
    rank = (len(ordered) - 1) * (max(0.0, min(100.0, p)) / 100.0)
    low = math.floor(rank)
    high = math.ceil(rank)
    if low == high:
        return ordered[int(rank)]
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def percentile(values: list[float], p: float) -> float | None:
    """线性插值分位数（p 取 0–100）。空列表返回 None。"""

    if not values:
        return None
    return _percentile_sorted(sorted(values), p)


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
        weight = weight_of(severity)
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


def _macro_metric(key: str, value: float | None, sample_size: int) -> dict[str, Any]:
    """宏平均指标行：value 已是每查询等权均值，不能再除一次。

    Wilson 区间用伪计数（value×n, n）近似，保证区间与点估计同源。
    """

    if value is None or sample_size <= 0:
        return _unavailable(key, "ratio")
    successes = round(float(value) * sample_size, 6)
    row = ratio_metric(key, successes, sample_size)
    row["value"] = round(float(value), 6)
    row["numerator"] = successes
    return row


def _is_hallucination(result: dict[str, Any]) -> bool:
    if bool(result.get("hallucinated")):
        return True
    return str(result.get("failure_reason") or "").startswith("assert.hallucination")


def compute_metrics(
    cases_by_id: dict[int, dict[str, Any]], results: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """把一个 run 的逐条结果折成 18 行指标（设计 §五全量口径）。"""

    total = len(results)
    passed = 0
    errors = 0
    skipped = 0
    invalid = 0
    hallucinated = 0
    instruction_total = 0
    instruction_passed = 0
    f1_scores: list[float] = []
    arg_values: list[float] = []
    steps_values: list[int] = []
    redundant_num = 0
    redundant_den = 0
    recall_queries: list[dict[str, Any]] = []
    ndcg_queries: list[dict[str, Any]] = []
    all_claims: list[dict[str, Any]] = []
    refusal_total = 0
    refusal_correct = 0
    weight_total = 0.0
    weight_failed = 0.0
    by_severity: dict[str, dict[str, int]] = {}
    human_labels: list[str] = []
    judge_labels: list[str] = []
    agreement_hits = 0
    human_fail = 0
    false_pass_hits = 0
    latencies: list[float] = []
    costs: list[float] = []
    provider_cost = False

    for item in results:
        case = cases_by_id.get(int(item.get("case_id") or 0)) or {}
        severity = str(case.get("severity") or "S1")
        weight = weight_of(severity)
        weight_total += weight
        bucket = by_severity.setdefault(severity, {"total": 0, "failed": 0})
        bucket["total"] += 1

        human = item.get("human")
        judge_label = item.get("judge_label")
        if human in {"pass", "fail"} and judge_label in {"pass", "fail"}:
            human_text = str(human)
            judge_text = str(judge_label)
            human_labels.append(human_text)
            judge_labels.append(judge_text)
            if human_text == judge_text:
                agreement_hits += 1
            if human_text == "fail":
                human_fail += 1
                if judge_text == "pass":
                    false_pass_hits += 1
        verdict = item.get("verdict")
        if verdict != "skip":
            latencies.append(float(item.get("latency_ms") or 0))
        costs.append(float(item.get("cost_micros") or 0))
        if item.get("cost_source") == "provider":
            provider_cost = True

        if verdict == "pass":
            passed += 1
        elif verdict == "error":
            errors += 1
            continue
        elif verdict == "skip":
            skipped += 1
            continue
        elif verdict == "invalid":
            invalid += 1
            continue
        elif verdict == "fail":
            weight_failed += weight
            bucket["failed"] += 1

        if _is_hallucination(item):
            hallucinated += 1
        rubric = item.get("rubric") or {}
        instruction_followed = rubric.get("instructionFollowed")
        if instruction_followed is not None:
            instruction_total += 1
            if bool(instruction_followed):
                instruction_passed += 1

        assertions = case.get("assertions") or {}
        tools_expected = case.get("tools_expected") or {}
        trace = item.get("tool_trace") or []
        if tools_expected or trace:
            f1_scores.append(
                tool_prf(
                    list(tools_expected.get("required") or []),
                    [str(call.get("tool") or "") for call in trace],
                )["f1"]
            )
            args = arg_accuracy(trace, dict(tools_expected.get("args") or {}))
            if args["value"] is not None:
                arg_values.append(float(args["value"]))
        raw_steps = item.get("steps")
        if raw_steps is not None:
            steps = int(raw_steps)
            steps_values.append(steps)
            expected_min = int(assertions.get("expected_min") or 0)
            if expected_min > 0:
                redundant_num += max(0, steps - expected_min)
                redundant_den += expected_min

        relevant = case.get("relevant_docs") or []
        graded = case.get("graded_relevance") or {}
        retrieved = item.get("retrieved") or []
        if relevant:
            recall_queries.append({"retrieved": retrieved, "relevant": relevant})
        if graded:
            ndcg_queries.append({"retrieved": retrieved, "graded": graded})
        all_claims.extend(item.get("claims") or [])

        should_refuse = assertions.get("should_refuse")
        if should_refuse is not None:
            refusal_total += 1
            if verdict == "pass":
                refusal_correct += 1

    valid = total - errors - skipped - invalid
    steps_total = sum(steps_values)
    total_cost = sum(costs)
    rows: list[dict[str, Any]] = [
        ratio_metric("success_rate", passed, valid),
        ratio_metric("error_rate", errors, total),
        ratio_metric("judge_invalid_rate", invalid, total),
        ratio_metric("hallucination_rate", hallucinated, valid),
        {
            "metric_key": "severity_weighted_error",
            "value": (weight_failed / weight_total) if weight_total > 0 else None,
            "ci_lower": None,
            "ci_upper": None,
            "ci_method": "bootstrap",
            "unit": "ratio",
            "numerator": weight_failed,
            "denominator": weight_total,
            "sample_size": len(results),
            "detail_json": {
                "bySeverity": {
                    sev: {
                        "total": item["total"],
                        "failed": item["failed"],
                        "weight": SEVERITY_WEIGHTS.get(sev, 10.0),
                    }
                    for sev, item in sorted(by_severity.items())
                }
            },
        },
        (
            ratio_metric("instruction_follow_rate", instruction_passed, instruction_total)
            if instruction_total
            else _unavailable("instruction_follow_rate", "ratio")
        ),
        (
            ratio_metric("tool_call_f1", round(sum(f1_scores), 6), len(f1_scores))
            if f1_scores
            else _unavailable("tool_call_f1", "ratio")
        ),
        (
            ratio_metric("tool_arg_accuracy", round(sum(arg_values), 6), len(arg_values))
            if arg_values
            else _unavailable("tool_arg_accuracy", "ratio")
        ),
        (
            {
                "metric_key": "steps_avg",
                "value": round(steps_total / len(steps_values), 4),
                "ci_lower": None,
                "ci_upper": None,
                "ci_method": "exact",
                "unit": "step",
                "numerator": float(steps_total),
                "denominator": float(len(steps_values)),
                "sample_size": len(steps_values),
                "detail_json": "{}",
            }
            if steps_values
            else _unavailable("steps_avg", "step")
        ),
        (
            ratio_metric("redundant_step_ratio", redundant_num, redundant_den)
            if redundant_den
            else _unavailable("redundant_step_ratio", "ratio")
        ),
    ]

    faithful = rag_faithfulness(all_claims)
    recall = macro_recall(recall_queries, RAG_K)
    ndcg = macro_ndcg(ndcg_queries, RAG_K)
    ordered_latencies = sorted(latencies)
    latency_p50 = _percentile_sorted(ordered_latencies, 50)
    latency_p95 = _percentile_sorted(ordered_latencies, 95)
    rows.extend(
        [
            _macro_metric(
                "rag_recall@5",
                recall["value"] if recall["value"] is not None else None,
                int(recall["sample_size"]),
            ),
            _macro_metric(
                "rag_ndcg@5",
                ndcg["value"] if ndcg["value"] is not None else None,
                int(ndcg["sample_size"]),
            ),
            (
                ratio_metric("faithfulness", faithful["supported"], faithful["sample_size"])
                if faithful["value"] is not None
                else _unavailable("faithfulness", "ratio")
            ),
            (
                ratio_metric("refusal_accuracy", refusal_correct, refusal_total)
                if refusal_total
                else _unavailable("refusal_accuracy", "ratio")
            ),
            (
                {
                    **ratio_metric("judge_agreement", agreement_hits, len(human_labels)),
                    "detail_json": {"kappa": cohen_kappa(human_labels, judge_labels)},
                }
                if human_labels
                else _unavailable("judge_agreement", "ratio")
            ),
            (
                ratio_metric("false_pass_rate", false_pass_hits, human_fail)
                if human_fail
                else _unavailable("false_pass_rate", "ratio")
            ),
            {
                "metric_key": "latency_p95",
                "value": latency_p95,
                "ci_lower": None,
                "ci_upper": None,
                "ci_method": "exact",
                "unit": "ms",
                "numerator": None,
                "denominator": None,
                "sample_size": len(latencies),
                "detail_json": (
                    {"p50": latency_p50, "p95": latency_p95}
                    if latencies
                    else {"reason": "insufficient_data"}
                ),
            },
            {
                "metric_key": "cost_per_1k",
                "value": (total_cost / valid * 1000.0) if valid > 0 else None,
                "ci_lower": None,
                "ci_upper": None,
                "ci_method": "exact",
                "unit": "micros",
                "numerator": total_cost,
                "denominator": float(valid),
                "sample_size": valid,
                "detail_json": {
                    "averageMicros": (total_cost / valid) if valid > 0 else None,
                    "providerCost": provider_cost,
                },
            },
        ]
    )
    return rows
