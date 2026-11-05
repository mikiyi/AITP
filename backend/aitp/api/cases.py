"""测试用例 API。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.enums import CASE_PRIORITY, CASE_STATUS, CASE_TYPE
from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.case_execution_repo import CaseExecutionRepository
from ..repositories.module_repo import ModuleRepository
from ..repositories.project_repo import ProjectRepository
from ..repositories.requirement_repo import RequirementRepository
from ..repositories.test_case_repo import TestCaseRepository
from ..services import audit_service, nav_badge_service
from ..services import tabular_export, xlsx_exporter, xlsx_importer
from .helpers import (
    audit,
    batch_soft_delete,
    body,
    commit,
    db,
    download_response,
    ids_of,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
)

bp = Blueprint("test_cases", __name__)

FILTERS = {
    "projectId": "project_id",
    "requirementId": "requirement_id",
    "moduleId": "module_id",
    "caseType": "case_type",
    "priority": "priority",
    "status": "status",
    "source": "source",
    "versionId": "version_id",
}
WRITE_FIELDS = {
    "project_id", "requirement_id", "module_id", "title", "case_type", "priority",
    "status", "preconditions", "expected_result", "actual_result", "tags", "source", "creator",
    "version_id",
}


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "requirementId": row.get("requirement_id"),
        "moduleId": row.get("module_id"),
        "caseNo": row.get("case_no"),
        "title": row.get("title"),
        "caseType": row.get("case_type"),
        "priority": row.get("priority"),
        "status": row.get("status"),
        "preconditions": row.get("preconditions"),
        "steps": row.get("steps") if "steps" in row else loads(row.get("steps_json"), []) or [],
        "expectedResult": row.get("expected_result"),
        "actualResult": row.get("actual_result"),
        "tags": row.get("tags"),
        "extra": row.get("extra") if "extra" in row else loads(row.get("extra_json"), {}) or {},
        "source": row.get("source"),
        "aiTaskId": row.get("ai_task_id"),
        "creator": row.get("creator"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


CASE_EXPORT_FORMATS = ("xlsx", *tabular_export.TABULAR_FORMATS)

CASE_TABULAR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("caseNo", "用例编号"),
    ("title", "标题"),
    ("caseType", "用例类型"),
    ("priority", "优先级"),
    ("status", "执行状态"),
    ("modulePath", "功能点"),
    ("reqNo", "需求编号"),
    ("preconditions", "前置条件"),
    ("stepsText", "操作步骤"),
    ("expectedResult", "预期结果"),
    ("tags", "标签"),
    ("linkedBugNos", "关联缺陷"),
    ("creator", "创建人"),
    ("createdAt", "创建时间"),
)


def _flat_cases(
    rows: list[dict[str, Any]],
    module_paths: dict[int, str],
    requirement_nos: dict[int, str],
    bug_nos_by_case: dict[int, list[str]],
) -> list[dict[str, Any]]:
    """把用例摊平成一行一条，供 CSV / JSON 导出使用。"""
    flat: list[dict[str, Any]] = []
    for case in rows:
        case_id = int(case["id"])
        module_id = case.get("module_id")
        requirement_id = case.get("requirement_id")
        flat.append({
            "caseNo": case.get("case_no"),
            "title": case.get("title"),
            "caseType": case.get("case_type"),
            "priority": case.get("priority"),
            "status": case.get("status"),
            "modulePath": module_paths.get(int(module_id), "") if module_id else "",
            "reqNo": requirement_nos.get(int(requirement_id), "") if requirement_id else "",
            "preconditions": case.get("preconditions"),
            "stepsText": xlsx_exporter.steps_to_text(case.get("steps") or []),
            "expectedResult": case.get("expected_result"),
            "tags": case.get("tags"),
            "linkedBugNos": "、".join(bug_nos_by_case.get(case_id, [])),
            "creator": case.get("creator"),
            "createdAt": case.get("created_at"),
        })
    return flat


@bp.get("")
def list_cases() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = TestCaseRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list_detail(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_to_public(row) for row in payload["list"]]
    return ok(payload)


@bp.get("/tree")
def case_tree() -> Response:
    """用例树：功能点树上挂各自的用例，没挂功能点的用例单独列出。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    module_repo = ModuleRepository(conn)
    tree = module_repo.tree(project_id)

    rows = TestCaseRepository(conn).list_all({"project_id": project_id}, None, limit=20000)
    by_module: dict[int, list[dict[str, Any]]] = {}
    orphans: list[dict[str, Any]] = []
    for row in rows:
        item = _to_public(row)
        module_id = row.get("module_id")
        if module_id:
            by_module.setdefault(int(module_id), []).append(item)
        else:
            orphans.append(item)

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            cases = by_module.get(int(node["id"]), [])
            node["cases"] = cases
            node["caseCount"] = len(cases)
            node["passCount"] = sum(1 for item in cases if item.get("status") == "pass")
            node["failCount"] = sum(1 for item in cases if item.get("status") == "fail")
            walk(node.get("children") or [])

    walk(tree)
    return ok({
        "list": tree,
        "orphans": orphans,
        "total": len(rows),
    })


@bp.get("/coverage")
def coverage_matrix() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    return ok(TestCaseRepository(conn).coverage_matrix(project_id))


@bp.get("/coverage/modules")
def module_coverage_matrix() -> Response:
    """按功能点统计的覆盖矩阵。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    return ok(TestCaseRepository(conn).module_coverage_matrix(project_id))


@bp.get("/export")
def export_cases() -> Response:
    """导出用例：xlsx 走 7 张表（格式对齐 testSKill\\generate_test_cases.py），csv/json 走单表摊平。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    fmt = str(request.args.get("format") or "xlsx").strip().lower()
    if fmt not in CASE_EXPORT_FORMATS:
        raise AppError(ErrorCode.PARAM_ERROR, "format 只能是 xlsx / csv / json")
    filters: dict[str, Any] = {"project_id": project_id}
    column_map = {
        "caseType": "case_type",
        "status": "status",
        "priority": "priority",
        "moduleId": "module_id",
        "requirementId": "requirement_id",
    }
    for key, column in column_map.items():
        value = request.args.get(key)
        if value:
            filters[column] = value

    repo = TestCaseRepository(conn)
    raw_rows = repo.list_all(filters, keyword_of() or None, limit=10000)
    rows: list[dict[str, Any]] = []
    for raw in raw_rows:
        case = dict(raw)
        case["steps"] = loads(case.get("steps_json"), []) or []
        case["extra"] = loads(case.get("extra_json"), {}) or {}
        rows.append(case)

    module_repo = ModuleRepository(conn)
    module_paths: dict[int, str] = {}
    for case in rows:
        module_id = case.get("module_id")
        if module_id and int(module_id) not in module_paths:
            module_paths[int(module_id)] = module_repo.path_text(int(module_id), project_name="")

    requirement_repo = RequirementRepository(conn)
    requirements = requirement_repo.list_by_project(project_id)
    requirement_nos = {int(item["id"]): str(item.get("req_no") or "") for item in requirements}

    linked = repo.linked_bug_ids([int(case["id"]) for case in rows])
    bug_nos_by_case = {
        case_id: [str(item.get("bug_no") or "") for item in items]
        for case_id, items in linked.items()
    }

    project = ProjectRepository(conn).get(project_id)
    project_name = str((project or {}).get("name") or "")
    if fmt in tabular_export.TABULAR_FORMATS:
        content, mimetype, extension = tabular_export.render(
            fmt, CASE_TABULAR_COLUMNS, _flat_cases(rows, module_paths, requirement_nos, bug_nos_by_case),
        )
        audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_TEST_CASE, project_id,
              project_name, {"format": fmt, "count": len(rows)})
        return download_response(
            content,
            tabular_export.safe_filename(f"{project_name or 'AITP'}测试用例", extension),
            mimetype,
            count=len(rows),
        )

    cases_by_type: dict[str, list[dict[str, Any]]] = {case_type: [] for case_type in CASE_TYPE}
    for case in rows:
        case_type = str(case.get("case_type") or "functional")
        if case_type not in cases_by_type:
            case_type = "functional"
        cases_by_type[case_type].append(case)

    content = xlsx_exporter.export_workbook(
        cases_by_type,
        module_paths=module_paths,
        requirement_nos=requirement_nos,
        bug_nos_by_case=bug_nos_by_case,
        requirements=requirements,
    )

    filename = xlsx_exporter.build_export_filename(project_name)
    return Response(
        content,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-AITP-Export-Count": str(len(rows)),
        },
    )


def _url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


@bp.post("/import")
def import_cases() -> Response:
    """导入 xlsx：按表头名取值，覆盖矩阵表忽略。"""
    conn = db()
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要导入的 xlsx 文件")
    if not uploaded.filename.lower().endswith(".xlsx"):
        raise AppError(ErrorCode.PARAM_ERROR, "只支持 .xlsx 格式")

    project_id = request.form.get("projectId") or request.args.get("projectId")
    if not project_id:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId")
    project_id_int = require_int(project_id, "projectId")

    raw = uploaded.read()
    try:
        parsed = xlsx_importer.parse_workbook(raw)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解析失败统一转参数错误
        raise AppError(ErrorCode.IMPORT_PARSE_FAILED, f"xlsx 解析失败：{exc}") from None
    if not parsed:
        raise AppError(ErrorCode.IMPORT_PARSE_FAILED, "文件里没有识别到用例表")

    repo = TestCaseRepository(conn)
    module_repo = ModuleRepository(conn)
    requirement_repo = RequirementRepository(conn)

    modules = module_repo.list_all(project_id_int)
    module_by_text: dict[str, int] = {}
    for node in modules:
        name = str(node.get("name") or "")
        path = module_repo.path_text(int(node["id"]), project_name="")
        if name:
            module_by_text.setdefault(name, int(node["id"]))
        if path:
            module_by_text.setdefault(path, int(node["id"]))
            module_by_text.setdefault(path.split("/")[-1], int(node["id"]))

    requirements = requirement_repo.list_by_project(project_id_int)
    requirement_by_no = {str(item.get("req_no") or ""): int(item["id"]) for item in requirements}
    requirement_by_title = {str(item.get("title") or ""): int(item["id"]) for item in requirements}

    used_numbers = {
        str(row.get("case_no") or "")
        for row in repo.list_all({"project_id": project_id_int}, None, limit=5000)
    }

    created = 0
    skipped = 0
    for item in parsed:
        title = str(item.get("title") or "").strip()
        if not title:
            skipped += 1
            continue

        case_type = str(item.get("caseType") or "functional")
        if case_type not in CASE_TYPE:
            case_type = "functional"

        module_text = str(item.get("moduleText") or "").strip()
        module_id = module_by_text.get(module_text)
        requirement_text = str(item.get("requirementNo") or "").strip()
        requirement_id = requirement_by_no.get(requirement_text) or requirement_by_title.get(requirement_text)

        extra = dict(item.get("extra") or {})
        if module_id is None and module_text:
            extra["所属模块/功能点"] = module_text
        if requirement_id is None and requirement_text:
            extra["关联需求"] = requirement_text

        case_no = str(item.get("caseNo") or "").strip()
        if not case_no or case_no in used_numbers:
            case_no = repo.next_case_no(project_id_int, case_type)
        used_numbers.add(case_no)

        repo.insert({
            "project_id": project_id_int,
            "requirement_id": requirement_id,
            "module_id": module_id,
            "case_no": case_no,
            "title": title,
            "case_type": case_type,
            "priority": str(item.get("priority") or "P2"),
            "status": str(item.get("status") or "not_executed"),
            "preconditions": str(item.get("preconditions") or ""),
            "steps_json": dumps(item.get("steps") or []),
            "expected_result": str(item.get("expectedResult") or ""),
            "actual_result": str(item.get("actualResult") or ""),
            "tags": "",
            "extra_json": dumps(extra),
            "source": "imported",
            "creator": str(item.get("creator") or ""),
        })
        created += 1

    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE, None,
          uploaded.filename, {"created": created, "skipped": skipped, "projectId": project_id_int})
    return ok({"created": created, "skipped": skipped, "total": len(parsed)})


@bp.get("/<pk>")
def get_case(pk: str) -> Response:
    conn = db()
    row = TestCaseRepository(conn).get_detail(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    return ok(_to_public(row))


@bp.post("")
def create_case() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    title = require_str(data.get("title"), "title", 500)
    case_type = check_choice(data.get("caseType"), CASE_TYPE, "caseType", "functional") or "functional"
    priority = check_choice(data.get("priority"), CASE_PRIORITY, "priority", "P2") or "P2"
    status = check_choice(data.get("status"), CASE_STATUS, "status", "not_executed") or "not_executed"
    repo = TestCaseRepository(conn)
    steps = data.get("steps")
    new_id = repo.insert({
        "project_id": project_id,
        "requirement_id": data.get("requirementId"),
        "module_id": data.get("moduleId"),
        "case_no": repo.next_case_no(project_id, case_type),
        "title": title,
        "case_type": case_type,
        "priority": priority,
        "status": status,
        "preconditions": optional_str(data.get("preconditions"), "preconditions", 5000),
        "steps_json": dumps(steps if isinstance(steps, list) else []),
        "expected_result": optional_str(data.get("expectedResult"), "expectedResult", 5000),
        "actual_result": optional_str(data.get("actualResult"), "actualResult", 5000),
        "tags": optional_str(data.get("tags"), "tags", 500),
        "extra_json": dumps(data.get("extra") or {}),
        "source": optional_str(data.get("source"), "source", 40) or "manual",
        "creator": optional_str(data.get("creator"), "creator", 64),
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE, new_id, title,
          {"caseType": case_type})
    return ok(_to_public(TestCaseRepository(conn).get_detail(new_id) or {}))


@bp.put("/<pk>")
def update_case(pk: str) -> Response:
    conn = db()
    repo = TestCaseRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    data = body()
    payload = {k: v for k, v in data.items() if k in WRITE_FIELDS}
    if "case_type" in payload:
        payload["case_type"] = check_choice(payload["case_type"], CASE_TYPE, "case_type", row["case_type"])
    if "priority" in payload:
        payload["priority"] = check_choice(payload["priority"], CASE_PRIORITY, "priority", row["priority"])
    if "status" in payload:
        payload["status"] = check_choice(payload["status"], CASE_STATUS, "status", row["status"])
    if "steps" in data:
        payload["steps_json"] = dumps(data.get("steps") or [])
    if "extra" in data:
        payload["extra_json"] = dumps(data.get("extra") or {})
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_CASE, target,
          str(payload.get("title", row.get("title", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(_to_public(repo.get_detail(target) or {}))


@bp.delete("/<pk>")
def delete_case(pk: str) -> Response:
    conn = db()
    repo = TestCaseRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_CASE, target,
          row.get("title", ""))
    return ok({"id": target, "deleted": True})


@bp.post("/batch-delete")
def batch_delete_cases() -> Response:
    """批量删除用例；已删除或不存在的 id 归入 skipped。"""
    conn = db()
    ids = ids_of(body())
    return ok(batch_soft_delete(conn, TestCaseRepository(conn), ids, audit_service.RESOURCE_TEST_CASE))


@bp.post("/adopt")
def adopt_cases() -> Response:
    """采纳 AI 生成的用例：批量转成正式用例。"""
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    items = data.get("cases")
    if not isinstance(items, list) or not items:
        raise AppError(ErrorCode.PARAM_ERROR, "cases 不能为空")

    repo = TestCaseRepository(conn)
    created: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        case_type = str(item.get("caseType") or "functional")
        if case_type not in CASE_TYPE:
            case_type = "functional"
        priority = str(item.get("priority") or "P2")
        if priority not in CASE_PRIORITY:
            priority = "P2"
        steps = item.get("steps")
        if not isinstance(steps, list):
            steps = []
        case_no = repo.next_case_no(project_id, case_type)
        new_id = repo.insert({
            "project_id": project_id,
            "requirement_id": item.get("requirementId") or data.get("requirementId"),
            "module_id": item.get("moduleId") or data.get("moduleId"),
            "case_no": case_no,
            "title": title,
            "case_type": case_type,
            "priority": priority,
            "status": "not_executed",
            "preconditions": str(item.get("preconditions") or ""),
            "steps_json": dumps(steps),
            "expected_result": str(item.get("expectedResult") or ""),
            "actual_result": "",
            "tags": str(item.get("tags") or ""),
            "extra_json": dumps(item.get("extra") or {}),
            "source": "ai_generated",
            "creator": str(item.get("creator") or "ai"),
        })
        created.append({"id": new_id, "caseNo": case_no})

    nav_badge_service.increase_for(conn, "cases", len(created))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE, None,
          f"采纳 AI 用例 {len(created)} 条", {"count": len(created), "projectId": project_id})
    return ok({"created": created, "count": len(created)})


@bp.post("/<pk>/execute")
def execute_case(pk: str) -> Response:
    conn = db()
    repo = TestCaseRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    data = body()
    result = check_choice(data.get("result"), CASE_STATUS, "result", "not_executed") or "not_executed"
    exec_id = CaseExecutionRepository(conn).record(
        target,
        result,
        duration_ms=data.get("durationMs"),
        environment=optional_str(data.get("environment"), "environment", 100),
        version=optional_str(data.get("version"), "version", 100),
        executed_by=optional_str(data.get("executedBy"), "executedBy", 64),
        remark=optional_str(data.get("remark"), "remark", 2000),
    )
    repo.update(target, {"status": result})
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_CASE, target,
          row.get("title", ""), {"result": result})
    return ok({"id": target, "status": result, "executionId": exec_id})
