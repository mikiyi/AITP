"""测试报告 API。"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, request

from ..common.enums import (BUG_SEVERITY, BUG_STATUS, CASE_PRIORITY, CASE_STATUS,
                             CASE_TYPE, REPORT_SECTION, REPORT_STATUS, REPORT_TYPE)
from ..common.validators import check_choice, optional_int, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.knowledge_ref_repo import KnowledgeRefRepository
from ..repositories.project_repo import ProjectRepository
from ..repositories.report_repo import ReportRepository
from ..repositories.test_run_repo import RESULT_LABELS, RUN_FAILED
from ..services import nav_badge_service
from ..services import audit_service, execution_protocol, report_exporter, report_trends
from .helpers import (attach_version_codes, audit, body, check_ref_project, commit, db,
                     guard_project_scope, keyword_of, page_args, page_payload, path_int, query_filters,
                     reject_project_move, require_project, scope_arg)

bp = Blueprint("reports", __name__)

FILTERS = {"projectId": "project_id", "reportType": "report_type", "status": "status", "versionId": "version_id"}

#: 失败样本一次最多带出多少条；超出只在结论里给总数（与 report_exporter 的上限一致）
FAILURE_SAMPLE_LIMIT = 50
#: 「报告所选失败样本」的持久化键。沿用 execution.evidence_retention_days:<项目id>
#: 这种「按实体后缀」的配置口径，报告蓝图内自足，不新增表、不动章节枚举。
SELECTION_CONFIG_KEY = "report.failure_samples"
#: 证据下钻读口（GAP-32 的统一证据接口），报告与缺陷共用同一条
EVIDENCE_QUERY_URL = "/api/v1/test-runs/evidence"
_DATE_TEXT_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "versionId": row.get("version_id"),
        "reportNo": row.get("report_no"),
        "title": row.get("title"),
        "reportType": row.get("report_type"),
        "periodStart": row.get("period_start"),
        "periodEnd": row.get("period_end"),
        "summary": row.get("summary"),
        "content": row.get("content"),
        "status": row.get("status"),
        "aiTaskId": row.get("ai_task_id"),
        "creator": row.get("creator"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _ref_ids(data: dict[str, Any]) -> list[int]:
    """从请求体里取出报告引用的知识条目 id 列表。"""
    raw = data.get("knowledgeRefs")
    if not isinstance(raw, list):
        return []
    ids: list[int] = []
    for item in raw:
        value: Any = item.get("articleId") if isinstance(item, dict) else item
        try:
            ids.append(int(value))
        except (TypeError, ValueError):
            continue
    return ids


def _sync_knowledge_refs(conn: Any, report_id: int, article_ids: list[int]) -> None:
    """登记「报告引用知识条目」，被引用的条目禁止撤销 / 删除。"""
    ref_repo = KnowledgeRefRepository(conn)
    ref_repo.remove_refs_by_target("report", report_id)
    for article_id in article_ids:
        ref_repo.add_ref(article_id, "report", report_id)


def _knowledge_refs(conn: Any, report_id: int) -> list[dict[str, Any]]:
    """报告引用的知识条目列表（含条目标题）。"""
    rows = conn.execute(
        "SELECT r.article_id AS article_id, a.title AS title"
        " FROM biz_knowledge_ref r"
        " LEFT JOIN biz_knowledge_article a ON a.id = r.article_id"
        " WHERE r.ref_type = 'report' AND r.ref_id = ? ORDER BY r.id ASC",
        (report_id,),
    ).fetchall()
    return [
        {"articleId": int(r["article_id"]), "title": str(r["title"] or "")}
        for r in rows
    ]



def _group_counts(conn: Any, sql: str, params: tuple[Any, ...]) -> dict[str, int]:
    """按分组列统计数量，结果字典带全部枚举键（缺项补 0）。"""
    counts: dict[str, int] = {}
    for row in conn.execute(sql, params).fetchall():
        counts[str(row[0])] = int(row[1])
    return counts


def _project_stats(conn: Any, project_id: int) -> dict[str, Any]:
    """项目当前的用例与缺陷分布，供图表与报告指标共用同一口径。"""
    case_status = _group_counts(
        conn,
        "SELECT status, COUNT(*) FROM biz_test_case"
        " WHERE is_deleted = 0 AND project_id = ? GROUP BY status",
        (project_id,),
    )
    case_priority = _group_counts(
        conn,
        "SELECT priority, COUNT(*) FROM biz_test_case"
        " WHERE is_deleted = 0 AND project_id = ? GROUP BY priority",
        (project_id,),
    )
    case_type = _group_counts(
        conn,
        "SELECT case_type, COUNT(*) FROM biz_test_case"
        " WHERE is_deleted = 0 AND project_id = ? GROUP BY case_type",
        (project_id,),
    )
    bug_status = _group_counts(
        conn,
        "SELECT status, COUNT(*) FROM biz_bug"
        " WHERE is_deleted = 0 AND project_id = ? GROUP BY status",
        (project_id,),
    )
    bug_severity = _group_counts(
        conn,
        "SELECT severity, COUNT(*) FROM biz_bug"
        " WHERE is_deleted = 0 AND project_id = ? GROUP BY severity",
        (project_id,),
    )

    case_total = sum(case_status.values())
    bug_total = sum(bug_status.values())
    passed = case_status.get("pass", 0)
    open_bugs = sum(count for status, count in bug_status.items() if status != "closed")

    return {
        "caseStatus": {status: case_status.get(status, 0) for status in CASE_STATUS},
        "casePriority": {priority: case_priority.get(priority, 0) for priority in CASE_PRIORITY},
        "caseType": {case: case_type.get(case, 0) for case in CASE_TYPE},
        "bugStatus": {status: bug_status.get(status, 0) for status in BUG_STATUS},
        "bugSeverity": {severity: bug_severity.get(severity, 0) for severity in BUG_SEVERITY},
        "metrics": {
            "caseTotal": case_total,
            "passed": passed,
            "failed": case_status.get("fail", 0),
            "blocked": case_status.get("blocked", 0),
            "notExecuted": case_status.get("not_executed", 0),
            "passRate": round(passed / case_total, 4) if case_total else 0.0,
            "bugTotal": bug_total,
            "openBugs": open_bugs,
        },
    }


def _build_generated_items(stats: dict[str, Any], project_name: str) -> list[dict[str, Any]]:
    """用实时数据拼出报告章节骨架。"""
    metric = stats["metrics"]
    case_status = stats["caseStatus"]
    bug_status = stats["bugStatus"]

    overview_metric = {
        "totalCases": metric["caseTotal"],
        "passRate": metric["passRate"],
        "totalBugs": metric["bugTotal"],
        "openBugs": metric["openBugs"],
    }
    case_metric = {
        "totalCases": metric["caseTotal"],
        "passed": metric["passed"],
        "failed": metric["failed"],
        "blocked": metric["blocked"],
        "notExecuted": metric["notExecuted"],
        "passRate": metric["passRate"],
    }
    bug_metric = {
        "totalBugs": metric["bugTotal"],
        "openBugs": metric["openBugs"],
        "closed": bug_status.get("closed", 0),
    }

    return [
        {
            "section": "overview",
            "title": "测试概览",
            "content": f"{project_name} 本轮共执行 {metric['caseTotal']} 条用例，"
                       f"发现 {metric['bugTotal']} 个缺陷，其中 {metric['openBugs']} 个未闭环。",
            "metric": overview_metric,
            "sortOrder": 0,
        },
        {
            "section": "case",
            "title": "用例执行情况",
            "content": f"通过 {metric['passed']} 条，失败 {metric['failed']} 条，"
                       f"阻塞 {metric['blocked']} 条，未执行 {metric['notExecuted']} 条，"
                       f"通过率 {metric['passRate'] * 100:.2f}%。",
            "metric": case_metric,
            "sortOrder": 1,
        },
        {
            "section": "bug",
            "title": "缺陷情况",
            "content": f"共 {metric['bugTotal']} 个缺陷，未闭环 {metric['openBugs']} 个，"
                       f"已关闭 {bug_status.get('closed', 0)} 个。",
            "metric": bug_metric,
            "sortOrder": 2,
        },
        {
            "section": "conclusion",
            "title": "结论与建议",
            "content": "以上结论由 AITP 依据当前项目实时数据自动汇总，"
                       "请人工复核后再发布；AI 不代替人工批准上线。",
            "metric": {},
            "sortOrder": 3,
        },
    ]


# ---------------------------------------------------------------- 失败样本与执行证据（GAP-35）


def _report_scope(row: dict[str, Any]) -> dict[str, Any]:
    """报告的取数范围：项目必填，版本与统计区间可选。

    区间按「轮次用例的执行时间（缺失时回落到轮次创建时间）」的日期部分过滤，
    只有形如 YYYY-MM-DD 的端点才生效——统计区间是自由文本，不能拿它猜日期。
    """
    scope: dict[str, Any] = {
        "projectId": int(row.get("project_id") or 0),
        "versionId": int(row["version_id"]) if row.get("version_id") not in (None, "") else None,
        "periodStart": str(row.get("period_start") or "").strip(),
        "periodEnd": str(row.get("period_end") or "").strip(),
    }
    for key in ("periodStart", "periodEnd"):
        if not _DATE_TEXT_RE.match(scope[key]):
            scope[key] = ""
    return scope


def _selection_key(report_id: int) -> str:
    return f"{SELECTION_CONFIG_KEY}:{int(report_id)}"


def _selected_ids(conn: Any, report_id: int) -> list[int]:
    """报告页勾选后保存下来的失败样本 id；空表示没有单独选，按全量口径。"""
    from ..repositories.sys_config_repo import SysConfigRepository

    raw = SysConfigRepository(conn).get_value(_selection_key(report_id))
    ids = loads(raw, []) if isinstance(raw, str) else raw
    if not isinstance(ids, list):
        return []
    result: list[int] = []
    for item in ids:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def _save_selection(conn: Any, report_id: int, run_case_ids: list[int]) -> None:
    from ..repositories.sys_config_repo import SysConfigRepository

    key = _selection_key(report_id)
    if not run_case_ids:
        SysConfigRepository(conn).delete(key)
        return
    SysConfigRepository(conn).set_value(
        key, dumps(sorted(set(run_case_ids))), "string", "report",
        f"报告 {int(report_id)} 所选失败样本（轮次用例 id）", 0,
    )


def _failed_run_case_rows(
    conn: Any, scope: dict[str, Any], *, limit: int | None = FAILURE_SAMPLE_LIMIT,
    only_ids: list[int] | None = None,
) -> tuple[list[Any], int]:
    """范围内真实失败的轮次用例：(取回的行, 真实总条数)。

    总数走同一条 WHERE 单独 COUNT，报告里的失败样本条数因此永远等于真实失败执行记录，
    不受分页上限影响，也不是前端硬填的数。
    """
    where = [
        "rc.is_deleted = 0", "r.is_deleted = 0", "rc.result = ?",
        "r.project_id = ?",
    ]
    params: list[Any] = [RUN_FAILED, scope["projectId"]]
    if scope["versionId"]:
        where.append("r.version_id = ?")
        params.append(scope["versionId"])
    at_expr = "COALESCE(NULLIF(rc.executed_at, ''), r.created_at)"
    if scope["periodStart"]:
        where.append(f"substr({at_expr}, 1, 10) >= ?")
        params.append(scope["periodStart"])
    if scope["periodEnd"]:
        where.append(f"substr({at_expr}, 1, 10) <= ?")
        params.append(scope["periodEnd"])
    if only_ids is not None:
        placeholders = ",".join("?" for _ in only_ids)
        where.append(f"rc.id IN ({placeholders})")
        params.extend(int(item) for item in only_ids)
    clause = " AND ".join(where)
    base = (
        " FROM biz_test_run_case rc JOIN biz_test_run r ON r.id = rc.run_id"
        f" WHERE {clause}"
    )
    total = int(conn.execute(f"SELECT COUNT(*){base}", params).fetchone()[0] or 0)
    sql = (
        "SELECT rc.id AS run_case_id, rc.run_id, rc.case_id, rc.case_no, rc.title,"
        " rc.priority, rc.module, rc.result, rc.actual_result, rc.remark, rc.bug_id,"
        f" rc.executed_by, rc.executed_at, r.name AS run_name, {at_expr} AS at_time"
        f"{base} ORDER BY at_time DESC, rc.id ASC"
    )
    rows = conn.execute(sql + (f" LIMIT {int(limit)}" if limit else ""), params).fetchall()
    return list(rows), total


def _failure_points(evidence: dict[str, Any] | None, row: Any) -> list[str]:
    """失败要点：优先给没过的断言与错误信息，都没有时回落到轮次上填的实际结果 / 备注。"""
    points: list[str] = []
    if evidence:
        for item in evidence.get("assertions") or []:
            if not isinstance(item, dict) or item.get("passed"):
                continue
            text = (
                f"断言 {str(item.get('name') or '').strip()}："
                f"期望 {item.get('expected')}，实际 {item.get('actual')}"
            ).strip()
            message = str(item.get("message") or "").strip()
            points.append(f"{text}；{message}" if message else text)
        error = str(evidence.get("errorMessage") or "").strip()
        if error:
            points.append(f"错误：{error[:200]}")
    if not points:
        for raw in (row["actual_result"], row["remark"]):
            text = str(raw or "").strip()
            if text:
                points.append(text[:200])
    return points[:report_exporter.MAX_FAILURE_POINTS]


def _failure_sample(conn: Any, row: Any, total: int) -> dict[str, Any]:
    """一条失败样本 + 它的证据摘要：报告详情、预览、三种导出共用这一个结构。"""
    run_case_id = int(row["run_case_id"])
    case_id = int(row["case_id"]) if row["case_id"] not in (None, "") else None
    evidence = execution_protocol.latest_evidence_for_run_case(conn, run_case_id)
    source_type = str((evidence or {}).get("sourceType") or "")
    source_row_id = int((evidence or {}).get("sourceRowId") or 0)
    attachments = [item for item in ((evidence or {}).get("attachments") or []) if isinstance(item, dict)]
    evidence_id = (evidence or {}).get("evidenceId")
    points = _failure_points(evidence, row)
    bug_id = int(row["bug_id"]) if row["bug_id"] not in (None, "") else None
    bug_no = ""
    if bug_id:
        bug_row = conn.execute("SELECT bug_no FROM biz_bug WHERE id = ?", (bug_id,)).fetchone()
        bug_no = str(bug_row["bug_no"] or "") if bug_row else ""
    return {
        "sampleKey": f"run-case:{run_case_id}",
        "runCaseId": run_case_id,
        "runId": int(row["run_id"]),
        "runName": str(row["run_name"] or ""),
        "caseId": case_id,
        "caseNo": str(row["case_no"] or ""),
        "title": str(row["title"] or ""),
        "priority": str(row["priority"] or ""),
        "module": str(row["module"] or ""),
        "result": str(row["result"] or ""),
        "resultLabel": RESULT_LABELS.get(str(row["result"] or ""), str(row["result"] or "")),
        "executedBy": str(row["executed_by"] or ""),
        "executedAt": str(row["executed_at"] or ""),
        "durationMs": int((evidence or {}).get("durationMs") or 0),
        "bugId": bug_id,
        "bugNo": bug_no,
        "hasEvidence": bool(evidence),
        "evidenceId": int(evidence_id) if evidence_id not in (None, "") else None,
        "sourceType": source_type,
        "sourceTypeLabel": str((evidence or {}).get("sourceTypeLabel") or ""),
        "sourceRowId": source_row_id or None,
        "evidenceUrl": f"{EVIDENCE_QUERY_URL}?runCaseId={run_case_id}" if evidence else "",
        "drillUrl": (
            f"{EVIDENCE_QUERY_URL}/{source_type}/{source_row_id}"
            if evidence and source_type and source_row_id
            else (f"{EVIDENCE_QUERY_URL}?runCaseId={run_case_id}" if evidence else "")
        ),
        "attachments": attachments,
        "attachmentCount": len(attachments),
        "failurePoints": points,
        "failureSummary": points[0] if points else "",
        "evidenceSummary": execution_protocol.describe_evidence(evidence) if evidence else "",
        "failureTotal": total,
    }


def _run_case_ids_of(raw: Any) -> list[int] | None:
    """解析 ``runCaseIds=1,2,3`` 查询参数；缺省返回 None（= 用保存过的所选，没有就用全量）。"""
    text = str(raw or "").strip()
    if not text:
        return None
    ids: list[int] = []
    for part in text.split(","):
        part = part.strip()
        if not part:
            continue
        try:
            ids.append(int(part))
        except ValueError:
            raise AppError(ErrorCode.PARAM_ERROR, f"runCaseIds 只能是逗号分隔的整数：{part}") from None
    return ids


def _failure_samples(
    conn: Any, report_row: dict[str, Any], run_case_ids: list[int] | None = None, *,
    strict: bool = False,
) -> dict[str, Any]:
    """报告维度的失败样本快照：范围统计 + 所选子集 + 每条的证据摘要。

    ``strict=True``（保存选择时）越界的样本 id 直接报错；读取侧保持宽容——
    报告的版本 / 区间改了、或选中的轮次用例被删，都回落到全量口径，
    不能让「详情打不开」或「导出悄悄丢证据」发生。
    """
    scope = _report_scope(report_row)
    all_rows, total = _failed_run_case_rows(conn, scope)
    selected = run_case_ids if run_case_ids is not None else _selected_ids(conn, int(report_row["id"] or 0))
    chosen_rows = all_rows
    if selected:
        wanted = {int(item) for item in selected[:FAILURE_SAMPLE_LIMIT]}
        in_scope = {int(row["run_case_id"]) for row in all_rows}
        unknown = sorted(wanted - in_scope)
        if unknown and strict:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"所选失败样本不在本报告统计范围内：{unknown}；请刷新后重新勾选",
            )
        order = {int(row["run_case_id"]): index for index, row in enumerate(all_rows)}
        subset = sorted(
            (row for row in all_rows if int(row["run_case_id"]) in wanted),
            key=lambda row: order[int(row["run_case_id"])],
        )
        chosen_rows = subset or all_rows
    samples = [_failure_sample(conn, row, total) for row in chosen_rows]
    return {
        "scope": scope,
        "total": total,
        "selected": sorted(int(row["run_case_id"]) for row in chosen_rows) if selected else [],
        "list": samples,
        "hint": "" if samples else report_exporter.FAILURE_EMPTY_TEXT,
    }


@bp.get("")
def list_reports() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = ReportRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = attach_version_codes(conn, [_to_public(row) for row in payload["list"]])
    return ok(payload)


@bp.get("/<pk>")
def get_report(pk: str) -> Response:
    conn = db()
    repo = ReportRepository(conn)
    row = repo.get_detail(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    guard_project_scope(row, scope_arg(), "报告")
    payload = _to_public(row)
    payload["items"] = repo.list_items(path_int(pk))
    payload["knowledgeRefs"] = _knowledge_refs(conn, path_int(pk))
    failure = _failure_samples(conn, row)
    payload["failureTotal"] = failure["total"]
    payload["failureSamples"] = failure["list"]
    payload["failureSelected"] = failure["selected"]
    payload["evidenceHint"] = failure["hint"]
    payload["evidenceSection"] = report_exporter.failure_markdown_section(failure["list"])
    return ok(payload)


@bp.get("/<pk>/failure-samples")
def list_failure_samples(pk: str) -> Response:
    """报告下钻用失败样本清单（GAP-35 验收标准 2 的取数口）。

    来源是本报告统计范围内真实失败的轮次用例，每条带一份执行证据摘要；
    没有失败样本时返回空清单 + 中文空态提示，不报错、不 500。
    """
    conn = db()
    target = path_int(pk)
    row = ReportRepository(conn).get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    guard_project_scope(row, scope_arg(), "报告")
    failure = _failure_samples(conn, row, _run_case_ids_of(request.args.get("runCaseIds")))
    return ok({
        "reportId": target,
        "scope": failure["scope"],
        "total": failure["total"],
        "selected": failure["selected"],
        "list": failure["list"],
        "evidenceCount": sum(1 for item in failure["list"] if item["hasEvidence"]),
        "hint": failure["hint"],
        "evidenceSection": report_exporter.failure_markdown_section(failure["list"]),
    })


@bp.post("/<pk>/failure-samples")
def select_failure_samples(pk: str) -> Response:
    """把勾选的失败样本定为报告的「所选样本」；空数组回到全量口径。

    所选只影响后续详情 / 预览 / 导出带出哪些样本，不改任何执行数据。
    """
    conn = db()
    target = path_int(pk)
    row = ReportRepository(conn).get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    guard_project_scope(row, scope_arg(), "报告")
    data = body()
    raw_ids = data.get("runCaseIds")
    if raw_ids in (None, ""):
        raise AppError(ErrorCode.PARAM_ERROR, "runCaseIds 必须是数组；清空选择请传 []")
    if not isinstance(raw_ids, list):
        raise AppError(ErrorCode.PARAM_ERROR, "runCaseIds 必须是数组")
    ids: list[int] = []
    for item in raw_ids[:FAILURE_SAMPLE_LIMIT]:
        ids.append(require_int(item, "runCaseIds[]"))
    failure = _failure_samples(conn, row, ids, strict=True)
    _save_selection(conn, target, ids)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REPORT, target,
          str(row.get("title") or ""), {"failureSamples": len(failure["list"])})
    return ok({
        "reportId": target,
        "selected": failure["selected"],
        "total": failure["total"],
        "list": failure["list"],
        "hint": failure["hint"],
        "evidenceSection": report_exporter.failure_markdown_section(failure["list"]),
    })


@bp.get("/<pk>/preview")
def preview_report(pk: str) -> Response:
    """报告预览：按所选失败样本把证据摘要一起渲染出来（md / html 两种文本形态）。"""
    conn = db()
    target = path_int(pk)
    row = ReportRepository(conn).get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    guard_project_scope(row, scope_arg(), "报告")
    fmt = check_choice(request.args.get("format"), ("md", "html"), "format", "md") or "md"
    items = list(row.get("items") or [])
    failure = _failure_samples(conn, row, _run_case_ids_of(request.args.get("runCaseIds")))
    samples = failure["list"]
    rendered = (
        report_exporter.render_markdown(row, items, samples) if fmt == "md"
        else report_exporter.render_html(row, items, samples=samples)
    )
    return ok({
        "reportId": target,
        "format": fmt,
        "content": rendered,
        "failureTotal": failure["total"],
        "selected": failure["selected"],
        "failureSamples": samples,
        "evidenceSection": report_exporter.failure_markdown_section(samples),
        "hint": failure["hint"],
    })


@bp.post("")
def create_report() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    title = require_str(data.get("title"), "title", 500)
    require_project(conn, project_id)
    version_id = check_ref_project(
        conn, "biz_project_version", data.get("versionId"), project_id, "项目版本", "versionId"
    )
    report_type = check_choice(data.get("reportType"), REPORT_TYPE, "reportType", "summary") or "summary"
    repo = ReportRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "report_no": repo.next_report_no(project_id),
        "title": title,
        "report_type": report_type,
        "period_start": optional_str(data.get("periodStart"), "periodStart", 40),
        "period_end": optional_str(data.get("periodEnd"), "periodEnd", 40),
        "summary": optional_str(data.get("summary"), "summary", 20000),
        "content": optional_str(data.get("content"), "content", 500000),
        "status": "draft",
        "creator": optional_str(data.get("creator"), "creator", 64),
        "version_id": version_id,
    })
    _sync_knowledge_refs(conn, new_id, _ref_ids(data))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_REPORT, new_id, title,
          {"reportType": report_type})
    result = _to_public(ReportRepository(conn).get_detail(new_id) or {})
    result["knowledgeRefs"] = _knowledge_refs(conn, new_id)
    return ok(result)


@bp.put("/<pk>")
def update_report(pk: str) -> Response:
    conn = db()
    repo = ReportRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    data = body()
    reject_project_move(data, row.get("project_id"), "报告")
    payload: dict[str, Any] = {}
    for field, column in (("title", "title"), ("summary", "summary"), ("content", "content"),
                          ("periodStart", "period_start"), ("periodEnd", "period_end")):
        if field in data:
            payload[column] = optional_str(data[field], field, 500000)
    if "reportType" in data:
        payload["report_type"] = check_choice(data["reportType"], REPORT_TYPE, "reportType", row["report_type"])
    if "status" in data:
        payload["status"] = check_choice(data["status"], REPORT_STATUS, "status", row["status"])
    if "versionId" in data:
        payload["version_id"] = check_ref_project(
            conn, "biz_project_version", data["versionId"],
            row.get("project_id"), "项目版本", "versionId",
        )
    repo.update(target, payload)
    if "knowledgeRefs" in data:
        _sync_knowledge_refs(conn, target, _ref_ids(data))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REPORT, target,
          str(payload.get("title", row.get("title", "")) or ""), {"fields": sorted(payload.keys())})
    result = _to_public(repo.get_detail(target) or {})
    result["knowledgeRefs"] = _knowledge_refs(conn, target)
    return ok(result)


@bp.delete("/<pk>")
def delete_report(pk: str) -> Response:
    conn = db()
    repo = ReportRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    repo.soft_delete(target)
    KnowledgeRefRepository(conn).remove_refs_by_target("report", target)
    _save_selection(conn, target, [])
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_REPORT, target,
          row.get("title", ""))
    return ok({"id": target, "deleted": True})


@bp.get("/<pk>/items")
def list_report_items(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    if not ReportRepository(conn).get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    return ok(ReportRepository(conn).list_items(target))


@bp.put("/<pk>/items")
def replace_report_items(pk: str) -> Response:
    conn = db()
    repo = ReportRepository(conn)
    target = path_int(pk)
    if not repo.get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    data = body()
    items = data.get("items")
    if not isinstance(items, list):
        raise AppError(ErrorCode.PARAM_ERROR, "items 必须是数组")
    normalized = []
    for item in items:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "items 元素必须是对象")
        section = check_choice(item.get("section"), REPORT_SECTION, "section", "overview") or "overview"
        normalized.append({
            "section": section,
            "title": optional_str(item.get("title"), "title", 500),
            "content": optional_str(item.get("content"), "content", 500000),
            "metric_json": dumps(item.get("metric")) if item.get("metric") is not None else None,
            "sort_order": require_int(item.get("sortOrder", 0), "sortOrder", 0),
        })
    repo.replace_items(target, normalized)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REPORT, target, "",
          {"items": len(normalized)})
    return ok({"reportId": target, "items": repo.list_items(target)})


@bp.get("/templates")
def list_templates() -> Response:
    from ..repositories.report_repo import ReportTemplateRepository

    conn = db()
    rows = ReportTemplateRepository(conn).list_all(None, None, limit=200)
    result = []
    for row in rows:
        item = dict(row)
        item["sections"] = loads(item.pop("sections_json", None), []) or []
        result.append(item)
    return ok(result)


@bp.get("/<pk>/export")
def export_report(pk: str) -> Response:
    """导出报告：md / html / pdf / print，三种格式都带上所选失败样本的执行证据摘要。"""
    conn = db()
    target = path_int(pk)
    row = ReportRepository(conn).get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    fmt = str(request.args.get("format") or "md").strip().lower()
    if fmt not in report_exporter.EXPORT_FORMATS:
        raise AppError(ErrorCode.PARAM_ERROR, "format 只能是 md / html / pdf / print")

    items = list(row.get("items") or [])
    title = str(row.get("title") or "测试报告")
    # 导出永远带证据维度：没有失败样本时是空态章节，而不是「没有这一章」
    samples = _failure_samples(conn, row, _run_case_ids_of(request.args.get("runCaseIds")))["list"]
    degraded = False
    if fmt == "md":
        payload = report_exporter.render_markdown(row, items, samples).encode("utf-8")
        mimetype = "text/markdown"
        extension = "md"
    elif fmt == "html":
        payload = report_exporter.render_html(row, items, samples=samples).encode("utf-8")
        mimetype = "text/html"
        extension = "html"
    elif fmt == "pdf":
        payload, degraded = report_exporter.render_pdf(row, items, samples)
        mimetype = "application/pdf"
        extension = "pdf"
    else:
        payload = report_exporter.render_html(
            row, items, print_view=True, samples=samples
        ).encode("utf-8")
        mimetype = "text/html"
        extension = "html"

    stem = report_exporter.file_stem(title, str(row.get("report_no") or ""), target)
    filename = f"{stem}.{extension}"
    disposition = "inline" if fmt == "print" else "attachment"
    response = Response(payload, mimetype=mimetype)
    if mimetype.startswith("text/"):
        response.headers["Content-Type"] = f"{mimetype}; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f"{disposition}; filename=\"report-{target}.{extension}\"; "
        f"filename*=UTF-8''{quote(filename, safe='')}"
    )
    response.headers["X-AITP-Report-No"] = str(row.get("report_no") or "")
    response.headers["X-AITP-Failure-Samples"] = str(len(samples))
    if degraded:
        response.headers["X-AITP-Font-Warning"] = "1"
    return response


@bp.get("/trends")
def report_trends_api() -> Response:
    """项目指标趋势：按天/周统计执行、通过率与缺陷进出。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    granularity = check_choice(
        request.args.get("granularity"), report_trends.GRANULARITIES, "granularity", "day"
    ) or "day"
    periods = optional_int(request.args.get("periods"), "periods")
    return ok(report_trends.trend(
        conn, project_id, granularity, periods, scope_arg(field="versionId")
    ))


@bp.get("/version-compare")
def report_version_compare_api() -> Response:
    """同项目两个版本的用例、执行、缺陷与准入并排对比。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    base_version_id = require_int(request.args.get("baseVersionId"), "baseVersionId")
    target_version_id = require_int(request.args.get("targetVersionId"), "targetVersionId")
    payload = report_trends.compare_versions(
        conn, project_id, base_version_id, target_version_id
    )
    return ok(payload)


@bp.post("/<pk>/trend-item")
def insert_trend_item(pk: str) -> Response:
    """把当前趋势表插入报告章节；重复插入会覆盖已有趋势章节。"""
    conn = db()
    repo = ReportRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    data = body()
    granularity = check_choice(
        data.get("granularity"), report_trends.GRANULARITIES, "granularity", "day"
    ) or "day"
    periods = optional_int(data.get("periods"), "periods")
    project_id = int(row.get("project_id") or 0)
    payload = report_trends.trend(conn, project_id, granularity, periods)
    content = report_trends.trend_markdown(payload)
    title = f"指标趋势（{payload['granularityLabel']}）"

    kept: list[dict[str, Any]] = []
    replaced = False
    for item in repo.list_items(target):
        if str(item.get("section") or "") == "trend":
            if not replaced:
                kept.append({"section": "trend", "title": title, "content": content,
                             "metric": payload["totals"]})
                replaced = True
            continue
        kept.append({
            "section": item.get("section"),
            "title": item.get("title"),
            "content": item.get("content"),
            "metric": item.get("metric"),
        })
    if not replaced:
        kept.append({"section": "trend", "title": title, "content": content,
                     "metric": payload["totals"]})
    repo.replace_items(target, kept)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_REPORT, target,
          str(row.get("title") or ""),
          {"trend": True, "granularity": granularity, "periods": periods})
    return ok({"reportId": target, "items": repo.list_items(target)})


@bp.get("/<pk>/stats")
def report_stats(pk: str) -> Response:
    """报告图表数据：按报告所属项目统计用例与缺陷分布。"""
    conn = db()
    target = path_int(pk)
    row = ReportRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    project_id = int(row.get("project_id") or 0)
    stats = _project_stats(conn, project_id)
    stats["projectId"] = project_id
    stats["reportId"] = target
    stats["periodStart"] = str(row.get("period_start") or "")
    stats["periodEnd"] = str(row.get("period_end") or "")
    return ok(stats)


@bp.post("/generate")
def generate_report() -> Response:
    """基于实时数据生成报告草稿：章节与指标均来自项目当前状态。"""
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    report_type = check_choice(data.get("reportType"), REPORT_TYPE, "reportType", "summary") or "summary"
    project_row = ProjectRepository(conn).get(project_id)
    project_name = str((project_row or {}).get("name") or f"项目 {project_id}")
    title = optional_str(data.get("title"), "title", 500) or f"{project_name} 测试报告"

    stats = _project_stats(conn, project_id)
    items = _build_generated_items(stats, project_name)
    repo = ReportRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "report_no": repo.next_report_no(project_id),
        "title": title,
        "report_type": report_type,
        "period_start": optional_str(data.get("periodStart"), "periodStart", 40),
        "period_end": optional_str(data.get("periodEnd"), "periodEnd", 40),
        "summary": "",
        "content": "",
        "status": "draft",
        "creator": optional_str(data.get("creator"), "creator", 64),
    })
    repo.replace_items(new_id, items)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_REPORT, new_id, title,
          {"source": "generate", "sections": len(items)})
    result = _to_public(repo.get_detail(new_id) or {})
    result["items"] = repo.list_items(new_id)
    result["knowledgeRefs"] = _knowledge_refs(conn, new_id)
    return ok(result)


@bp.post("/adopt")
def adopt_report() -> Response:
    """把 AI 产出的 Markdown 采纳为报告：正文落 content，二级标题拆成章节。"""
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    title = require_str(data.get("title"), "title", 500)
    report_type = check_choice(data.get("reportType"), REPORT_TYPE, "reportType", "summary") or "summary"
    content = optional_str(data.get("content"), "content", 500000) or ""
    sections = report_exporter.parse_sections(content)

    repo = ReportRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "version_id": optional_int(data.get("versionId"), "versionId"),
        "report_no": repo.next_report_no(project_id),
        "title": title,
        "report_type": report_type,
        "period_start": optional_str(data.get("periodStart"), "periodStart", 40),
        "period_end": optional_str(data.get("periodEnd"), "periodEnd", 40),
        "summary": optional_str(data.get("summary"), "summary", 20000),
        "content": content,
        "status": "draft",
        "creator": optional_str(data.get("creator"), "creator", 64),
    })
    if sections:
        repo.replace_items(new_id, [
            {
                "section": section["section"],
                "title": section["title"],
                "content": section["content"],
                "metric": section["metric"],
                "sortOrder": index,
            }
            for index, section in enumerate(sections)
        ])
    nav_badge_service.increase_for(conn, "reports")
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_REPORT, new_id, title,
          {"source": "ai_adopt", "sections": len(sections)})
    result = _to_public(repo.get_detail(new_id) or {})
    result["items"] = repo.list_items(new_id)
    result["knowledgeRefs"] = _knowledge_refs(conn, new_id)
    return ok(result)
