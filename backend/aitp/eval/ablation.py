"""消融对比与配对统计检验（设计 §5.2 / §七 regressionPolicy）。

统计诚实：n=100 只能检出约 14pp 差异，「降 3pp 即 warn」在统计上不成立。
回归判定因此改为：
  1. 硬断言逐条比对（主判据）：同一用例 baseline pass → now fail 必报；
  2. McNemar 配对检验（辅判据）：只看翻转对，比独立两比例检验敏感一个数量级；
  3. 指标点估计只作观察，区间重叠的两点不叫回归。
"""

from __future__ import annotations

import math
from typing import Any

from ..errors import AppError, ErrorCode

#: 配对检验默认显著性水平（设计 §七 regressionPolicy.alpha）
DEFAULT_ALPHA = 0.05
#: 95% 置信、80% 功效对应的 z 值
Z_ALPHA = 1.959964
Z_BETA = 0.841621


def assert_comparable(baseline: dict[str, Any], current: dict[str, Any]) -> None:
    """同组可比性：评测集/版本/用例快照/judge 快照必须一致，配置应不同。

    不一致直接拒绝对比（错误码 160103 跑批未结束/不可对比的同族语义），
    跨组对比没有统计意义。
    """

    mismatches: list[str] = []
    for field, label in (
        ("suite_id", "评测集"),
        ("suite_version", "评测集版本"),
        ("case_set_hash", "用例集快照"),
        ("judge_snapshot", "judge 快照"),
    ):
        if str(baseline.get(field) or "") != str(current.get(field) or ""):
            mismatches.append(label)
    if mismatches:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"两次跑批不可对比（{'/'.join(mismatches)} 不一致），跨组对比已拒绝",
        )
    if str(baseline.get("config_snapshot") or "") == str(current.get("config_snapshot") or ""):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "消融对比要求两次跑批配置不同，相同配置请用 A/A 复跑")


def paired_flips(
    baseline_results: list[dict[str, Any]], current_results: list[dict[str, Any]]
) -> dict[str, list[dict[str, Any]]]:
    """按 case_id 对齐两次结果，输出翻转清单。

    b（regressed）：baseline pass → now fail；c（improved）：baseline fail → now pass。
    error/skip/invalid 不参与翻转统计（不是模型能力变化）。
    """

    base_map = {int(item["case_id"]): item for item in baseline_results}
    regressed: list[dict[str, Any]] = []
    improved: list[dict[str, Any]] = []
    for item in current_results:
        case_id = int(item["case_id"])
        before = base_map.get(case_id)
        if before is None:
            continue
        old_verdict, new_verdict = before.get("verdict"), item.get("verdict")
        if old_verdict not in {"pass", "fail"} or new_verdict not in {"pass", "fail"}:
            continue
        if old_verdict == "pass" and new_verdict == "fail":
            regressed.append(
                {
                    "caseId": case_id,
                    "caseNo": item.get("case_no"),
                    "from": "pass",
                    "to": "fail",
                    "reason": item.get("failure_reason") or "",
                }
            )
        elif old_verdict == "fail" and new_verdict == "pass":
            improved.append(
                {
                    "caseId": case_id,
                    "caseNo": item.get("case_no"),
                    "from": "fail",
                    "to": "pass",
                }
            )
    return {"regressed": regressed, "improved": improved}


def mcnemar_pvalue(b: int, c: int) -> float:
    """McNemar 双侧检验 p 值。

    小样本（n=b+c ≤ 1000）用精确二项分布；大样本退化为带连续性修正的卡方近似。
    """

    n = b + c
    if n == 0:
        return 1.0
    if n <= 1000:
        tail = float(sum(math.comb(n, k) for k in range(0, min(b, c) + 1))) / float(2**n)
        return round(min(1.0, 2.0 * tail), 6)
    chi2 = (abs(b - c) - 1.0) ** 2 / n
    return round(math.erfc(math.sqrt(chi2 / 2.0)), 6)


def paired_test(
    baseline_results: list[dict[str, Any]],
    current_results: list[dict[str, Any]],
    alpha: float = DEFAULT_ALPHA,
) -> dict[str, Any]:
    """完整配对检验：翻转清单 + McNemar 结论 + 硬断言回归标记。"""

    flips = paired_flips(baseline_results, current_results)
    b = len(flips["regressed"])
    c = len(flips["improved"])
    p_value = mcnemar_pvalue(b, c)
    return {
        "regressedCount": b,
        "improvedCount": c,
        "pValue": p_value,
        "alpha": alpha,
        "significant": p_value < alpha,
        "hardAssertionRegression": b > 0,
        "regressed": flips["regressed"],
        "improved": flips["improved"],
    }


def minimum_detectable_effect(
    p: float, n_per_group: int, z_alpha: float = Z_ALPHA, z_beta: float = Z_BETA
) -> float | None:
    """两比例检验在给定样本量下的最小可检测差异（比例，非百分点）。

    采用等组、等方差近似：d = (z_α + z_β)·sqrt(2p(1-p)/n)。
    与设计 §5.2 表对齐：p=0.85、n=100 → ≈0.141（14pp）。
    """

    if n_per_group <= 0 or not 0.0 < p < 1.0:
        return None
    p = min(0.9999, max(0.0001, p))
    return round((z_alpha + z_beta) * math.sqrt(2.0 * p * (1.0 - p) / n_per_group), 6)


def mde_table(p: float = 0.85) -> list[dict[str, Any]]:
    """设计 §5.2 的标准样本量—MDE 对照表，供报告直接引用。"""

    return [
        {"nPerGroup": n, "mde": minimum_detectable_effect(p, n)}
        for n in (50, 100, 200, 400, 2200)
    ]
