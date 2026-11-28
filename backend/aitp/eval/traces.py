"""Agent 轨迹与端态断言（设计 §6.5）：文本正确不等于任务完成。

纯函数集合：工具调用 P/R/F1、参数正确率、步数冗余、端态比对。
端态不符 → assert.end_state_mismatch，文本再漂亮也是 fail。
"""

from __future__ import annotations

from typing import Any

REASON_TOOL_MISSING = "tool.missing_required"
REASON_TOOL_FORBIDDEN = "tool.forbidden_called"
REASON_TOOL_ARG = "tool.arg_mismatch"
REASON_TRACE_STEPS = "trace.too_many_steps"
REASON_END_STATE = "assert.end_state_mismatch"


def tool_prf(expected_tools: list[str], actual_tools: list[str]) -> dict[str, float]:
    """集合口径的工具调用精确率/召回率/F1（重复调用不重复计分）。"""

    expected = {str(item) for item in expected_tools if item}
    actual = {str(item) for item in actual_tools if item}
    if not expected and not actual:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not actual or not expected:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    hits = len(expected & actual)
    precision = hits / len(actual)
    recall = hits / len(expected)
    f1 = (2 * precision * recall / (precision + recall)) if (precision + recall) else 0.0
    return {"precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def arg_accuracy(
    actual_calls: list[dict[str, Any]], expected_args: dict[str, dict[str, Any]]
) -> dict[str, Any]:
    """参数正确率 = 参数全对的调用数 / 有参数期望的实际调用数。

    expected_args: 工具名 → 期望参数（子集匹配，实际参数允许带额外键）。
    没有参数期望的工具不进分母，避免把「未约束」误算成错。
    """

    judged = 0
    matched = 0
    mismatches: list[str] = []
    for call in actual_calls:
        tool = str(call.get("tool") or "")
        if tool not in expected_args:
            continue
        judged += 1
        raw_args = call.get("args")
        args = raw_args if isinstance(raw_args, dict) else {}
        expect = expected_args[tool]
        for key, value in expect.items():
            if args.get(key) != value:
                mismatches.append(tool)
                break
        else:
            matched += 1
    return {
        "value": round(matched / judged, 6) if judged else None,
        "judged": judged,
        "matched": matched,
        "mismatches": mismatches,
    }


def evaluate_end_state(expectation: dict[str, Any], actual: dict[str, Any]) -> dict[str, Any]:
    """端态断言：expect 里的每个键值都必须在 actual 中命中（子集匹配）。

    kind: http / sql / file / none；none 表示该用例不做端态检查。
    """

    kind = str(expectation.get("kind") or "none")
    if kind == "none" or not expectation.get("checks"):
        return {"passed": True, "reasons": []}
    actual_state = actual or {}
    reasons: list[str] = []
    for check in expectation.get("checks") or []:
        if not isinstance(check, dict):
            continue
        expect = check.get("expect")
        if not isinstance(expect, dict):
            continue
        target = actual_state
        if check.get("request"):
            target = actual_state.get(str(check["request"])) or {}
        if not isinstance(target, dict) or any(target.get(key) != value for key, value in expect.items()):
            reasons.append(f"{REASON_END_STATE}:{check.get('request') or check.get('sql') or 'state'}")
    return {"passed": not reasons, "reasons": reasons}


def step_redundancy(steps: int, expected_min: int) -> int:
    """单条用例的冗余步数（只统计正差）。"""

    if expected_min <= 0:
        return 0
    return max(0, int(steps) - int(expected_min))


def evaluate_trace(
    tools_expected: dict[str, Any],
    tool_trace: list[dict[str, Any]],
    steps: int,
    max_steps: int | None = None,
) -> dict[str, Any]:
    """整条 Agent 轨迹的规则裁决，返回 F1/参数正确率与失败原因。"""

    actual_tools = [str(item.get("tool") or "") for item in tool_trace if isinstance(item, dict)]
    required = list(tools_expected.get("required") or [])
    forbidden = list(tools_expected.get("forbidden") or [])
    raw_args = tools_expected.get("args")
    expected_args: dict[str, dict[str, Any]] = raw_args if isinstance(raw_args, dict) else {}

    prf = tool_prf(required, actual_tools)
    args_result = arg_accuracy(tool_trace, expected_args)
    reasons: list[str] = []

    actual_set = set(actual_tools)
    missing = [tool for tool in required if tool not in actual_set]
    if missing:
        reasons.append(f"{REASON_TOOL_MISSING}:{','.join(missing)}")
    hit_forbidden = [tool for tool in forbidden if tool in actual_set]
    if hit_forbidden:
        reasons.append(f"{REASON_TOOL_FORBIDDEN}:{','.join(hit_forbidden)}")
    if args_result["mismatches"]:
        reasons.append(f"{REASON_TOOL_ARG}:{','.join(args_result['mismatches'])}")
    if max_steps and steps > int(max_steps):
        reasons.append(f"{REASON_TRACE_STEPS}:{steps}>{max_steps}")

    return {
        "f1": prf["f1"],
        "precision": prf["precision"],
        "recall": prf["recall"],
        "argAccuracy": args_result["value"],
        "reasons": reasons,
        "passed": not reasons,
    }
