"""LLM-as-judge 判定组合与校准纯函数（设计 §6.3）。

覆盖：judge 输出解析（不可解析 → invalid，剔除分母）、单评/双评/仲裁/
panel 多数、Cohen's κ、混淆矩阵、false-pass/false-fail、位置交换偏差、
冗长偏差。所有函数零 IO，门禁脚本可直接调用。
"""

from __future__ import annotations

import json
from typing import Any

JUDGE_PASS = "pass"
JUDGE_FAIL = "fail"
JUDGE_LABELS = frozenset({JUDGE_PASS, JUDGE_FAIL})


def parse_judge_verdict(raw: Any) -> dict[str, Any] | None:
    """解析 judge 原始输出。

    接受 JSON 字符串或 dict，必须含 pass/fail 的 verdict 字段；
    解析失败或标签非法一律返回 None —— 调用方记 judge_invalid，
    不允许把坏输出伪装成「模型答得差」。
    """

    if raw is None:
        return None
    payload: Any = raw
    if isinstance(raw, str):
        try:
            payload = json.loads(raw)
        except (TypeError, ValueError):
            return None
    if not isinstance(payload, dict):
        return None
    verdict = str(payload.get("verdict") or "").strip().lower()
    if verdict not in JUDGE_LABELS:
        return None
    result: dict[str, Any] = {
        "verdict": verdict,
        "score": payload.get("score"),
        "reasons": payload.get("reasons") if isinstance(payload.get("reasons"), list) else [],
        "hallucinated": bool(payload.get("hallucinated", False)),
        "dimensions": payload.get("dimensions") if isinstance(payload.get("dimensions"), dict) else {},
    }
    try:
        result["score"] = None if result["score"] is None else float(result["score"])
    except (TypeError, ValueError):
        result["score"] = None
    return result


def panel_decision(verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """panel 多数裁决；同票时返回 need_arbitration=True 交第三方仲裁。"""

    labels = [item["verdict"] for item in verdicts if item]
    if not labels:
        return {"verdict": None, "need_arbitration": True, "votes": {}}
    votes = {JUDGE_PASS: labels.count(JUDGE_PASS), JUDGE_FAIL: labels.count(JUDGE_FAIL)}
    if votes[JUDGE_PASS] == votes[JUDGE_FAIL]:
        return {"verdict": None, "need_arbitration": True, "votes": votes}
    winner = max(votes, key=lambda key: votes[key])
    return {"verdict": winner, "need_arbitration": False, "votes": votes}


def double_judge(
    first: dict[str, Any] | None,
    second: dict[str, Any] | None,
    arbitrator: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """双评：两个独立 judge 同判则采纳，分歧走仲裁；任一不可解析即 invalid。"""

    if first is None or second is None:
        return {"verdict": None, "invalid": True, "arbitrated": False}
    if first["verdict"] == second["verdict"]:
        return {"verdict": first["verdict"], "invalid": False, "arbitrated": False}
    if arbitrator is None:
        return {"verdict": None, "invalid": True, "arbitrated": False, "reason": "split_no_arbitrator"}
    return {"verdict": arbitrator["verdict"], "invalid": False, "arbitrated": True}


def cohen_kappa(labels_a: list[str], labels_b: list[str]) -> float:
    """Cohen's κ（二分类）。全一致=1；边际全同时按 0 处理（随机水平）。"""

    count_a = {JUDGE_PASS: 0, JUDGE_FAIL: 0}
    count_b = {JUDGE_PASS: 0, JUDGE_FAIL: 0}
    total = 0
    observed_hits = 0
    for raw_a, raw_b in zip(labels_a, labels_b):
        a = str(raw_a)
        b = str(raw_b)
        total += 1
        if a == b:
            observed_hits += 1
        if a in count_a:
            count_a[a] += 1
        if b in count_b:
            count_b[b] += 1
    if total == 0:
        return 0.0
    observed = observed_hits / total
    margin_a = {label: count_a[label] / total for label in JUDGE_LABELS}
    margin_b = {label: count_b[label] / total for label in JUDGE_LABELS}
    expected = sum(margin_a.get(label, 0.0) * margin_b.get(label, 0.0) for label in JUDGE_LABELS)
    if expected >= 1.0:
        return 1.0 if observed >= 1.0 else 0.0
    return round((observed - expected) / (1.0 - expected), 6)


def confusion_matrix(human_labels: list[str], judge_labels: list[str]) -> dict[str, int]:
    """人评 × judge 的 2×2 混淆矩阵（键：tp/fp/fn/tn，以 pass 为正类）。"""

    matrix = {"tp": 0, "fp": 0, "fn": 0, "tn": 0}
    for human, judge in zip(human_labels, judge_labels):
        if human == JUDGE_PASS and judge == JUDGE_PASS:
            matrix["tp"] += 1
        elif human == JUDGE_FAIL and judge == JUDGE_PASS:
            matrix["fp"] += 1
        elif human == JUDGE_PASS and judge == JUDGE_FAIL:
            matrix["fn"] += 1
        elif human == JUDGE_FAIL and judge == JUDGE_FAIL:
            matrix["tn"] += 1
    return matrix


def calibration_summary(
    human_labels: list[str], judge_labels: list[str]
) -> dict[str, Any]:
    """一次人评对拍的完整校准结果（设计 §6.3 准入三件套）。"""

    matrix = confusion_matrix(human_labels, judge_labels)
    total = len(human_labels)
    agreement = (
        sum(1 for a, b in zip(human_labels, judge_labels) if a == b) / total if total else None
    )
    # false-pass：该判 fail 却判 pass，分母是人评 fail 总数
    false_pass = matrix["fp"] / (matrix["fp"] + matrix["tn"]) if (matrix["fp"] + matrix["tn"]) else None
    # false-fail：误伤，分母是人评 pass 总数
    false_fail = matrix["fn"] / (matrix["tp"] + matrix["fn"]) if (matrix["tp"] + matrix["fn"]) else None
    kappa = cohen_kappa(human_labels, judge_labels) if total else None
    return {
        "sampleSize": total,
        "agreement": agreement,
        "kappa": kappa,
        "falsePassRate": false_pass,
        "falseFailRate": false_fail,
        "matrix": matrix,
        "calibrated": bool(
            agreement is not None
            and agreement >= 0.8
            and kappa is not None
            and kappa >= 0.6
            and (false_pass is None or false_pass <= 0.05)
        ),
    }


def position_bias(samples: list[dict[str, Any]]) -> float | None:
    """位置交换偏差 = |P(A 胜|A 在前) − P(A 胜|A 在后)|；>0.1 视为显著。

    每条样本：{"a_first": bool, "a_won": bool}。
    """

    first = [item for item in samples if item.get("a_first")]
    second = [item for item in samples if not item.get("a_first")]
    if not first or not second:
        return None
    rate_first = sum(1 for item in first if item.get("a_won")) / len(first)
    rate_second = sum(1 for item in second if item.get("a_won")) / len(second)
    return round(abs(rate_first - rate_second), 6)


def verbosity_bias(samples: list[dict[str, Any]]) -> float | None:
    """冗长偏差：pass 组与 fail 组平均长度差，按总均值归一。

    每条样本：{"verdict": "pass"/"fail", "length": int}。
    返回值越接近 0 越中立；明显为正说明 judge 偏好长回答。
    """

    pass_lengths = [int(item["length"]) for item in samples if item.get("verdict") == JUDGE_PASS]
    fail_lengths = [int(item["length"]) for item in samples if item.get("verdict") == JUDGE_FAIL]
    if not pass_lengths or not fail_lengths:
        return None
    mean_pass = sum(pass_lengths) / len(pass_lengths)
    mean_fail = sum(fail_lengths) / len(fail_lengths)
    pooled = (sum(pass_lengths) + sum(fail_lengths)) / (len(pass_lengths) + len(fail_lengths))
    if pooled <= 0:
        return 0.0
    return round((mean_pass - mean_fail) / pooled, 6)
