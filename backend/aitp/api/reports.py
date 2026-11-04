"""测试报告 API。"""

from __future__ import annotations

from typing import Any
from urllib.parse import quote

from flask import Blueprint, Response, request

from ..common.enums import (BUG_SEVERITY, BUG_STATUS, CASE_PRIORITY, CASE_STATUS,
                             CASE_TYPE, REPORT_SECTION, REPORT_STATUS, REPORT_TYPE)
from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.knowledge_ref_repo import KnowledgeRefRepository
from ..repositories.project_repo import ProjectRepository
from ..repositories.report_repo import ReportRepository
from ..services import nav_badge_service
from ..services import audit_service, report_exporter
from .helpers import (audit, body, commit, db, keyword_of, page_args, page_payload,
                     path_int, query_filters)

bp = Blueprint("reports", __name__)

FILTERS = {"projectId": "project_id", "reportType": "report_type", "status": "status"}


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
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

@bp.get("")
def list_reports() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = ReportRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_to_public(row) for row in payload["list"]]
    return ok(payload)


@bp.get("/<pk>")
def get_report(pk: str) -> Response:
    conn = db()
    repo = ReportRepository(conn)
    row = repo.get_detail(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    payload = _to_public(row)
    payload["items"] = repo.list_items(path_int(pk))
    payload["knowledgeRefs"] = _knowledge_refs(conn, path_int(pk))
    return ok(payload)


@bp.post("")
def create_report() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    title = require_str(data.get("title"), "title", 500)
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
    payload: dict[str, Any] = {}
    for field, column in (("title", "title"), ("summary", "summary"), ("content", "content"),
                          ("periodStart", "period_start"), ("periodEnd", "period_end")):
        if field in data:
            payload[column] = optional_str(data[field], field, 500000)
    if "reportType" in data:
        payload["report_type"] = check_choice(data["reportType"], REPORT_TYPE, "reportType", row["report_type"])
    if "status" in data:
        payload["status"] = check_choice(data["status"], REPORT_STATUS, "status", row["status"])
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
    """导出报告：md / html / pdf / print。"""
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
    degraded = False
    if fmt == "md":
        payload = report_exporter.render_markdown(row, items).encode("utf-8")
        mimetype = "text/markdown"
        extension = "md"
    elif fmt == "html":
        payload = report_exporter.render_html(row, items).encode("utf-8")
        mimetype = "text/html"
        extension = "html"
    elif fmt == "pdf":
        payload, degraded = report_exporter.render_pdf(row, items)
        mimetype = "application/pdf"
        extension = "pdf"
    else:
        payload = report_exporter.render_html(row, items, print_view=True).encode("utf-8")
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
    if degraded:
        response.headers["X-AITP-Font-Warning"] = "1"
    return response


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
