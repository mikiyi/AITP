"""用例集质量分析（V2.6.0）。

用确定性规则做一次「用例体检」，回答三个问题：哪些用例写得不完整、哪些
重复、哪些执行了也没有判断力。全部本地计算，不消耗模型额度；模型只用于
实验室里对单条用例做深度评审，两者互补。

评分口径透明：100 分起扣，高危 12 分、中危 6 分、低危 2 分，最低 0 分。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..repositories.base import loads

SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"
DEDUCT = {SEVERITY_HIGH: 12, SEVERITY_MEDIUM: 6, SEVERITY_LOW: 2}
SEVERITY_LABELS = {SEVERITY_HIGH: "高危", SEVERITY_MEDIUM: "中危", SEVERITY_LOW: "低危"}
MAX_CASES = 2000

_PLACEHOLDER = re.compile(r"(todo|待补充|待完善|xxx|tbd)", re.IGNORECASE)
_WS = re.compile(r"\s+")


def _norm_title(text: str) -> str:
    return _WS.sub("", str(text or "")).lower()


def _issue(
    code: str, label: str, severity: str, row: dict[str, Any], detail: str,
) -> dict[str, Any]:
    return {
        "code": code,
        "label": label,
        "severity": severity,
        "severityLabel": SEVERITY_LABELS[severity],
        "caseId": int(row["id"]),
        "caseNo": str(row.get("case_no") or ""),
        "title": str(row.get("title") or ""),
        "detail": detail,
    }


def _load_cases(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    module_id: int | None = None,
    version_id: int | None = None,
    case_ids: list[int] | None = None,
) -> list[dict[str, Any]]:
    where = ["is_deleted = 0", "project_id = ?"]
    params: list[Any] = [int(project_id)]
    if module_id is not None:
        where.append("module_id = ?")
        params.append(int(module_id))
    if version_id is not None:
        where.append("version_id = ?")
        params.append(int(version_id))
    if case_ids:
        ids = sorted({int(item) for item in case_ids if int(item) > 0})
        if not ids:
            return []
        where.append(f"id IN ({','.join('?' for _ in ids)})")
        params.extend(ids)
    sql = (
        "SELECT id, case_no, title, case_type, priority, module_id, steps_json,"
        " expected_result, preconditions, extra_json, tags"
        f" FROM biz_test_case WHERE {' AND '.join(where)}"
        " ORDER BY id LIMIT ?"
    )
    rows = [dict(row) for row in conn.execute(sql, [*params, MAX_CASES]).fetchall()]
    for row in rows:
        row["steps"] = loads(row.get("steps_json"), []) or []
        extra = loads(row.get("extra_json"), {}) or {}
        row["extra"] = extra if isinstance(extra, dict) else {}
    return rows


def analyze(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    module_id: int | None = None,
    version_id: int | None = None,
    case_ids: list[int] | None = None,
) -> dict[str, Any]:
    cases = _load_cases(
        conn, project_id=project_id, module_id=module_id,
        version_id=version_id, case_ids=case_ids,
    )
    issues: list[dict[str, Any]] = []

    title_groups: dict[str, list[dict[str, Any]]] = {}
    for row in cases:
        title = str(row.get("title") or "").strip()
        if not title:
            issues.append(_issue("title_empty", "标题为空", SEVERITY_HIGH, row, "没有标题无法检索与追溯"))
        elif len(title) < 4:
            issues.append(_issue("title_short", "标题过短", SEVERITY_LOW, row, f"标题仅 {len(title)} 字，难以判断测试意图"))
        else:
            title_groups.setdefault(_norm_title(title), []).append(row)

        steps = row.get("steps") or []
        expected = str(row.get("expected_result") or "").strip()
        if not steps:
            issues.append(_issue("steps_empty", "缺少测试步骤", SEVERITY_HIGH, row, "执行时无法复现"))
        elif len(steps) > 20:
            issues.append(_issue("steps_long", "步骤过多", SEVERITY_LOW, row, f"{len(steps)} 步，建议拆分为多条用例"))
        if not expected:
            issues.append(_issue("expected_empty", "缺少预期结果", SEVERITY_HIGH, row, "执行后没有判断依据"))
        elif len(expected) > 2000:
            issues.append(_issue("expected_long", "预期结果过长", SEVERITY_LOW, row, f"{len(expected)} 字，建议提炼关键判断点"))
        if not str(row.get("priority") or "").strip():
            issues.append(_issue("priority_empty", "缺少优先级", SEVERITY_MEDIUM, row, "无法参与风险选测与发布门禁"))
        if row.get("module_id") in (None, ""):
            issues.append(_issue("module_empty", "未归属功能点", SEVERITY_LOW, row, "覆盖统计与按模块筛选会漏掉"))
        text = f"{title} {expected} {' '.join(str(item) for item in steps)}"
        if _PLACEHOLDER.search(text):
            issues.append(_issue("placeholder", "存在占位内容", SEVERITY_MEDIUM, row, "包含 TODO/待补充 等未完成标记"))
        if str(row.get("case_type") or "") == "interface":
            url = str(row["extra"].get("接口URL") or row["extra"].get("url") or "").strip()
            rules = str(row["extra"].get("断言规则") or "").strip()
            if not url:
                issues.append(_issue("url_empty", "接口用例缺少 URL", SEVERITY_HIGH, row, "无法自动执行"))
            if not rules:
                issues.append(_issue("assertion_empty", "接口用例缺少断言", SEVERITY_MEDIUM, row, "执行只能验证有响应"))

    for group in title_groups.values():
        if len(group) <= 1:
            continue
        for row in group:
            issues.append(_issue(
                "title_duplicate", "标题重复", SEVERITY_MEDIUM, row,
                f"同项目内有 {len(group)} 条同名用例，建议合并或改名",
            ))

    severity_counts = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 0, SEVERITY_LOW: 0}
    for item in issues:
        severity_counts[item["severity"]] += 1
    score = 100 - sum(DEDUCT[item["severity"]] for item in issues)
    score = max(0, score)
    grade = "A" if score >= 90 else "B" if score >= 75 else "C" if score >= 60 else "D"
    case_issue_ids = {item["caseId"] for item in issues}
    return {
        "score": score,
        "grade": grade,
        "checked": len(cases),
        "caseIssueCount": len(case_issue_ids),
        "issueCount": len(issues),
        "severity": severity_counts,
        "severityLabels": SEVERITY_LABELS,
        "issues": issues[:500],
        "truncated": len(issues) > 500,
        "recommendation": _recommendation(score, len(cases), severity_counts),
    }


def _recommendation(score: int, checked: int, counts: dict[str, int]) -> str:
    if not checked:
        return "当前范围没有用例可体检。"
    if counts[SEVERITY_HIGH]:
        return f"先修 {counts[SEVERITY_HIGH]} 个高危问题（缺步骤/预期/URL），否则执行结果不可信。"
    if counts[SEVERITY_MEDIUM]:
        return f"再处理 {counts[SEVERITY_MEDIUM]} 个中危问题（重复标题、缺优先级、缺断言）。"
    if score >= 90:
        return "质量良好，可进入执行与评审。"
    return "整体可用，建议补齐低危问题以提升可维护性。"


__all__ = [
    "analyze",
    "SEVERITY_HIGH",
    "SEVERITY_MEDIUM",
    "SEVERITY_LOW",
    "SEVERITY_LABELS",
]
