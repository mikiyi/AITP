"""发布准入 / 质量门禁。

门禁结论只依据「选定轮次的结果 + 项目当前未闭环缺陷 + 需求覆盖率」三个可解释口径，
每项检查都回传实际值与阈值，避免只给一个「不通过」而说不出为什么。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.base import dumps
from ..repositories.release_gate_repo import ReleaseGateRepository
from ..repositories.test_run_repo import RUN_FAILED, RUN_PASSED, TestRunRepository
from . import traceability_service
from .test_run_service import open_bug_counts, run_priority_rates

DEFAULT_THRESHOLDS: dict[str, float] = {
    "overallPassRate": 80.0,
    "p0PassRate": 100.0,
    "p1PassRate": 90.0,
    "maxOpenP0": 0.0,
    "maxOpenP1": 3.0,
    "minCoverageRate": 80.0,
}

CHECK_LABELS = {
    "overallPassRate": "整体通过率",
    "p0PassRate": "P0 用例通过率",
    "p1PassRate": "P1 用例通过率",
    "maxOpenP0": "未闭环 P0 缺陷",
    "maxOpenP1": "未闭环 P1 缺陷",
    "minCoverageRate": "需求覆盖率",
}


def _number(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def normalize_thresholds(raw: Any) -> dict[str, float]:
    data = raw if isinstance(raw, dict) else {}
    return {key: _number(data.get(key), default) for key, default in DEFAULT_THRESHOLDS.items()}


def evaluate(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    run_id: int,
    version_id: int | None = None,
    thresholds: dict[str, float] | None = None,
) -> dict[str, Any]:
    run = TestRunRepository(conn).get(run_id)
    if not run:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    if int(run["project_id"]) != project_id:
        raise AppError(ErrorCode.PARAM_ERROR, "测试轮次不属于该项目")

    limits = normalize_thresholds(thresholds or {})
    stats = TestRunRepository(conn).stats_of(run_id)
    total = int(stats["total"])
    passed = int(stats["counts"].get(RUN_PASSED, 0))
    failed = int(stats["counts"].get(RUN_FAILED, 0))
    executed = total - int(stats["counts"].get("not_executed", 0))
    overall_rate = round(passed / executed * 100, 2) if executed else 0.0

    p0_total, p0_passed = run_priority_rates(stats, "P0")
    p1_total, p1_passed = run_priority_rates(stats, "P1")
    p0_rate = round(p0_passed / p0_total * 100, 2) if p0_total else 100.0
    p1_rate = round(p1_passed / p1_total * 100, 2) if p1_total else 100.0

    bugs = open_bug_counts(conn, project_id, version_id)
    trace = traceability_service.build_traceability(conn, project_id, version_id, run_id)
    coverage_rate = float(trace["summary"]["coverageRate"])

    checks = [
        {"key": "overallPassRate", "label": CHECK_LABELS["overallPassRate"],
         "actual": overall_rate, "threshold": limits["overallPassRate"],
         "passed": overall_rate >= limits["overallPassRate"], "unit": "%"},
        {"key": "p0PassRate", "label": CHECK_LABELS["p0PassRate"],
         "actual": p0_rate, "threshold": limits["p0PassRate"],
         "passed": p0_rate >= limits["p0PassRate"], "unit": "%"},
        {"key": "p1PassRate", "label": CHECK_LABELS["p1PassRate"],
         "actual": p1_rate, "threshold": limits["p1PassRate"],
         "passed": p1_rate >= limits["p1PassRate"], "unit": "%"},
        {"key": "maxOpenP0", "label": CHECK_LABELS["maxOpenP0"],
         "actual": bugs.get("P0", 0), "threshold": limits["maxOpenP0"],
         "passed": bugs.get("P0", 0) <= limits["maxOpenP0"], "unit": "个"},
        {"key": "maxOpenP1", "label": CHECK_LABELS["maxOpenP1"],
         "actual": bugs.get("P1", 0), "threshold": limits["maxOpenP1"],
         "passed": bugs.get("P1", 0) <= limits["maxOpenP1"], "unit": "个"},
        {"key": "minCoverageRate", "label": CHECK_LABELS["minCoverageRate"],
         "actual": coverage_rate, "threshold": limits["minCoverageRate"],
         "passed": coverage_rate >= limits["minCoverageRate"], "unit": "%"},
    ]
    failed_checks = [check for check in checks if not check["passed"]]
    status = "passed" if not failed_checks else "blocked"
    if failed_checks:
        conclusion = "不满足发布准入：" + "；".join(
            f"{check['label']} {check['actual']}{check['unit']}（要求 {check['threshold']}{check['unit']}）"
            for check in failed_checks
        )
    else:
        conclusion = f"满足发布准入：{len(checks)} 项检查全部通过。"

    readiness = build_readiness(
        conn, project_id=project_id, run_id=run_id, status=status,
        trace=trace, open_bugs=bugs, failed_checks=failed_checks,
    )

    result = {
        "status": status,
        "metrics": {
            "total": total,
            "executed": executed,
            "passed": passed,
            "failed": failed,
            "overallPassRate": overall_rate,
            "p0Total": p0_total,
            "p0Passed": p0_passed,
            "p0PassRate": p0_rate,
            "p1Total": p1_total,
            "p1Passed": p1_passed,
            "p1PassRate": p1_rate,
            "openBugs": bugs,
            "coverageRate": coverage_rate,
            "requirementTotal": trace["summary"]["requirementTotal"],
            "coveredRequirements": trace["summary"]["coveredRequirements"],
        },
        "checks": checks,
        "blocking": [check["key"] for check in failed_checks],
        "readiness": readiness,
    }
    return result


def build_readiness(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    run_id: int,
    status: str,
    trace: dict[str, Any],
    open_bugs: dict[str, int],
    failed_checks: list[dict[str, Any]],
) -> dict[str, Any]:
    """把「准入结果 + 失败归因 + flaky + 覆盖缺口」汇成发布建议。

    发布结论只有三档：可直接发布（ready）、可发但有风险（risk）、不可发（blocked）。
    风险项都能下钻到用例或缺陷，避免只给一个笼统的「不通过」。
    """
    from . import case_stability_service, failure_attribution_service

    rows = conn.execute(
        """
        SELECT case_no, title, priority FROM biz_test_run_case
        WHERE run_id = ? AND result = 'failed' AND is_deleted = 0
        ORDER BY CASE priority WHEN 'P0' THEN 0 WHEN 'P1' THEN 1 ELSE 2 END, id
        """,
        (int(run_id),),
    ).fetchall()
    failed_by_priority: dict[str, int] = {"P0": 0, "P1": 0, "P2": 0, "P3": 0}
    for row in rows:
        key = str(row["priority"] or "P2")
        failed_by_priority[key] = failed_by_priority.get(key, 0) + 1

    flaky = case_stability_service.run_case_flaky(conn, run_id)
    attribution = failure_attribution_service.summary(conn, run_id=run_id)
    requirement_total = int(trace.get("summary", {}).get("requirementTotal") or 0)
    covered = int(trace.get("summary", {}).get("coveredRequirements") or 0)
    uncovered = max(0, requirement_total - covered)

    risks: list[dict[str, Any]] = []
    if failed_by_priority.get("P0"):
        risks.append({
            "kind": "p0_failed", "label": "P0 用例失败",
            "count": failed_by_priority["P0"],
            "detail": "、".join(str(row["case_no"] or row["title"]) for row in rows
                                if str(row["priority"]) == "P0")[:300],
        })
    if open_bugs.get("P0"):
        risks.append({"kind": "open_p0_bug", "label": "未闭环 P0 缺陷",
                      "count": int(open_bugs["P0"]), "detail": "需先关闭 P0 缺陷"})
    if open_bugs.get("P1"):
        risks.append({"kind": "open_p1_bug", "label": "未闭环 P1 缺陷",
                      "count": int(open_bugs["P1"]), "detail": "确认是否可带入下个版本"})
    if uncovered:
        risks.append({"kind": "coverage_gap", "label": "需求未覆盖",
                      "count": uncovered, "detail": f"{covered}/{requirement_total} 条需求有用例覆盖"})
    if flaky:
        risks.append({"kind": "flaky", "label": "不稳定用例",
                      "count": len(flaky), "detail": "这些用例重试后通过或历史结果翻转，不计入真失败"})
    if attribution["counts"].get("environment"):
        risks.append({
            "kind": "environment", "label": "环境类失败",
            "count": attribution["counts"]["environment"],
            "detail": "先修复环境/登录态后重跑，当前失败数不代表真实质量",
        })

    if status == "blocked":
        verdict = "blocked"
        label = "不可发布"
    elif any(item["kind"] in ("p0_failed", "open_p0_bug", "coverage_gap") for item in risks):
        verdict = "risk"
        label = "可发但有阻塞风险"
    elif risks:
        verdict = "risk"
        label = "可发，需关注风险项"
    else:
        verdict = "ready"
        label = "可发布"

    recommendations: list[str] = []
    if attribution["needsEnvFix"]:
        recommendations.append(
            f"先处理 {attribution['needsEnvFix']} 条环境/数据类失败并重跑，避免污染通过率"
        )
    if attribution["needsBugTicket"]:
        recommendations.append(f"把 {attribution['needsBugTicket']} 条疑似缺陷转成缺陷单并评估影响")
    if attribution["needsAssetChange"]:
        recommendations.append(
            f"{attribution['needsAssetChange']} 条失败更像断言过期，可在修复建议里逐条确认"
        )
    if not recommendations:
        recommendations.append("失败归因没有环境或资产噪声，可直接按检查项结论决策")

    return {
        "verdict": verdict,
        "label": label,
        "summary": (
            f"{label}：{len(failed_checks)} 项准入检查未通过，"
            f"{len(risks)} 类风险待确认。"
            if failed_checks else
            f"{label}：准入检查全部通过，{len(risks)} 类风险待确认。"
        ),
        "failedByPriority": failed_by_priority,
        "flaky": [{"caseId": int(row["id"]), "caseNo": row["case_no"],
                   "title": row["title"]} for row in flaky[:20]],
        "attribution": attribution,
        "risks": risks,
        "recommendations": recommendations,
    }


def evaluate_and_save(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    run_id: int,
    name: str,
    version_id: int | None = None,
    thresholds: dict[str, float] | None = None,
    created_by: str = "",
) -> dict[str, Any]:
    limits = normalize_thresholds(thresholds or {})
    result = evaluate(conn, project_id=project_id, run_id=run_id,
                      version_id=version_id, thresholds=limits)
    repo = ReleaseGateRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "version_id": version_id,
        "run_id": run_id,
        "name": name,
        "status": result["status"],
        "thresholds_json": dumps(limits),
        "result_json": dumps(result),
        "conclusion": build_conclusion(result),
        "created_by": created_by,
    })
    from ..repositories.release_gate_repo import gate_summary

    return gate_summary(repo.get_detail(new_id) or {})


def build_conclusion(result: dict[str, Any]) -> str:
    blocking = result.get("blocking") or []
    if not blocking:
        return f"满足发布准入：{len(result.get('checks') or [])} 项检查全部通过。"
    labels = {
        check["key"]: check for check in result.get("checks") or []
    }
    reasons = []
    for key in blocking:
        check = labels.get(key)
        if check:
            reasons.append(
                f"{check['label']} {check['actual']}{check['unit']}（要求 {check['threshold']}{check['unit']}）"
            )
    return "不满足发布准入：" + "；".join(reasons)
