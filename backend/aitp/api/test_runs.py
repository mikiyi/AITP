"""测试轮次 API：轮次 CRUD、用例快照、结果回写、失败转缺陷、导出。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, current_app, request

from ..common.validators import check_choice, optional_int, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.automation_repo import (
    SCRIPT_STATUS_DISABLED,
    AutomationRunRepository,
    AutomationScriptRepository,
    run_summary as automation_run_summary,
)
from ..repositories.test_env_repo import TestEnvRepository
from ..repositories.test_plan_repo import TestPlanRepository
from ..repositories.test_run_repo import (
    RESULT_LABELS,
    RUN_CASE_RESULTS,
    TestRunCaseRepository,
    TestRunRepository,
    run_case_summary,
    run_summary,
)
from ..services import audit_service, tabular_export, test_run_service
from ..services import automation_service, interface_runner, job_queue
from .helpers import (
    attach_version_codes,
    audit,
    body,
    check_ref_project,
    commit,
    db,
    download_response,
    guard_project_scope,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    require_project,
    scope_arg,
    tabular_format_of,
)

bp = Blueprint("test_runs", __name__)

FILTERS = {
    "projectId": "project_id",
    "versionId": "version_id",
    "planId": "plan_id",
    "status": "status",
}

SCOPE_FIELDS = {
    "moduleId": "module_id",
    "requirementId": "requirement_id",
    "caseType": "case_type",
    "priority": "priority",
    "status": "status",
}


def _scope_filters(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    filters: dict[str, Any] = {}
    for key, column in SCOPE_FIELDS.items():
        value = data.get(key)
        if value not in (None, ""):
            filters[column] = value
    return filters


def _run_or_404(conn: Any, target: int) -> dict[str, Any]:
    row = TestRunRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    guard_project_scope(row, scope_arg(), "测试轮次")
    return row


def _attach_plan_names(conn: Any, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """列表里补计划名称，避免前端只拿当前页计划反查导致显示为空。"""
    plan_ids = sorted({int(row["planId"]) for row in rows if row.get("planId")})
    if not plan_ids:
        return rows
    placeholders = ",".join("?" for _ in plan_ids)
    names = {
        int(item["id"]): item["name"]
        for item in conn.execute(
            f"SELECT id, name FROM biz_test_plan WHERE id IN ({placeholders})",
            plan_ids,
        ).fetchall()
    }
    for row in rows:
        plan_id = row.get("planId")
        row["planName"] = names.get(int(plan_id), "") if plan_id else ""
    return rows


@bp.get("")
def list_runs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = TestRunRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = test_run_service.attach_envs(
        conn,
        _attach_plan_names(
            conn,
            attach_version_codes(conn, [run_summary(row) for row in payload["list"]]),
        ),
    )
    return ok(payload)


@bp.get("/<pk>")
def get_run(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = TestRunRepository(conn)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    guard_project_scope(row, scope_arg(), "测试轮次")
    payload = run_summary(row)
    payload = attach_version_codes(conn, [payload])[0]
    payload = test_run_service.attach_envs(conn, [payload])[0]
    if row.get("plan_id"):
        plan = TestPlanRepository(conn).get(int(row["plan_id"]))
        payload["planName"] = (plan or {}).get("name") or ""
    payload["cases"] = [
        run_case_summary(item) for item in TestRunCaseRepository(conn).list_by_run(target)
    ]
    return ok(payload)


@bp.post("")
def create_run() -> Response:
    """不依赖计划直接发起一轮测试；支持按用例 id 或筛选范围选用例。"""
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    case_ids = data.get("caseIds")
    if isinstance(case_ids, list) and case_ids:
        ids = [require_int(item, "caseIds[]") for item in case_ids]
    else:
        ids = test_run_service.select_case_ids(
            conn, project_id, _scope_filters(data.get("scope") or data)
        )
    if not ids:
        raise AppError(ErrorCode.PARAM_ERROR, "当前筛选条件下没有可执行的用例")
    name = optional_str(data.get("name"), "name", 200) or "临时测试轮次"
    payload = test_run_service.create_run(
        conn,
        project_id=project_id,
        name=name,
        case_ids=ids,
        plan_id=optional_int(data.get("planId"), "planId"),
        version_id=optional_int(data.get("versionId"), "versionId"),
        env_id=optional_int(data.get("envId"), "envId"),
        env=optional_str(data.get("env"), "env", 100) or "",
        owner=optional_str(data.get("owner"), "owner", 64) or "",
        remark=optional_str(data.get("remark"), "remark", 2000) or "",
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_RUN,
          int(payload["id"]), name, {"cases": len(ids)})
    return ok(payload)


@bp.put("/<pk>")
def update_run(pk: str) -> Response:
    conn = db()
    repo = TestRunRepository(conn)
    target = path_int(pk)
    row = _run_or_404(conn, target)
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 200)
    if "envId" in data:
        env_id, env_name = test_run_service.resolve_env(
            conn, optional_int(data["envId"], "envId")
        )
        payload["env_id"] = env_id
        payload["env"] = env_name
    elif "env" in data:
        payload["env"] = optional_str(data["env"], "env", 100) or ""
    if "owner" in data:
        payload["owner"] = optional_str(data["owner"], "owner", 64) or ""
    if "remark" in data:
        payload["remark"] = optional_str(data["remark"], "remark", 2000) or ""
    if "versionId" in data:
        payload["version_id"] = check_ref_project(
            conn, "biz_project_version", data.get("versionId"),
            row.get("project_id"), "项目版本", "versionId",
        )
    if payload:
        repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_RUN, target,
          str(payload.get("name") or row.get("name") or ""), {"fields": sorted(payload.keys())})
    return ok(run_summary(repo.get_detail(target) or {}))


@bp.delete("/<pk>")
def delete_run(pk: str) -> Response:
    conn = db()
    repo = TestRunRepository(conn)
    target = path_int(pk)
    row = _run_or_404(conn, target)
    repo.soft_delete(target)
    conn.execute("UPDATE biz_test_run_case SET is_deleted = 1 WHERE run_id = ?", (target,))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_RUN, target,
          str(row.get("name") or ""))
    return ok({"id": target, "deleted": True})


@bp.get("/<pk>/cases")
def list_run_cases(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    _run_or_404(conn, target)
    result = request.args.get("result") or ""
    keyword = keyword_of()
    rows = TestRunCaseRepository(conn).list_by_run(target)
    if result:
        rows = [row for row in rows if row.get("result") == result]
    if keyword:
        needle = keyword.lower()
        rows = [
            row for row in rows
            if needle in str(row.get("title") or "").lower()
            or needle in str(row.get("case_no") or "").lower()
        ]
    return ok({"list": [run_case_summary(row) for row in rows], "total": len(rows)})


@bp.post("/<pk>/cases")
def add_run_cases(pk: str) -> Response:
    """向轮次追加用例：指定 caseIds，或按筛选范围批量追加。"""
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    case_ids = data.get("caseIds")
    if isinstance(case_ids, list) and case_ids:
        ids = [require_int(item, "caseIds[]") for item in case_ids]
    else:
        ids = test_run_service.select_case_ids(
            conn, int(run["project_id"]), _scope_filters(data.get("scope") or data)
        )
    if not ids:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要追加的用例")
    result = test_run_service.add_cases(conn, target, ids)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"added": result["count"]})
    return ok(result)


@bp.delete("/<pk>/cases/<case_pk>")
def remove_run_case(pk: str, case_pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    row_id = path_int(case_pk, "caseId")
    repo = TestRunCaseRepository(conn)
    row = repo.get_of_run(target, row_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "轮次用例不存在")
    repo.soft_delete(row_id)
    TestRunRepository(conn).refresh_counters(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"runCaseId": row_id})
    return ok({"id": row_id, "deleted": True})


@bp.put("/<pk>/cases/<case_pk>")
def record_run_case(pk: str, case_pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    result = check_choice(data.get("result"), RUN_CASE_RESULTS, "result", "not_executed") or "not_executed"
    payload = test_run_service.record_result(
        conn,
        target,
        path_int(case_pk, "caseId"),
        result=result,
        actual_result=optional_str(data.get("actualResult"), "actualResult", 10000) or "",
        remark=optional_str(data.get("remark"), "remark", 2000) or "",
        executed_by=optional_str(data.get("executedBy"), "executedBy", 64) or "",
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"runCaseId": path_int(case_pk, "caseId"), "result": result})
    return ok(payload)


@bp.post("/<pk>/cases/batch")
def batch_record_run_cases(pk: str) -> Response:
    """批量回写：前端一次提交本页所有变更，后端逐条落库并返回整轮统计。"""
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    entries = data.get("entries")
    if not isinstance(entries, list) or not entries:
        raise AppError(ErrorCode.PARAM_ERROR, "entries 必须是非空数组")
    payload = test_run_service.batch_record(
        conn, target, entries,
        executed_by=optional_str(data.get("executedBy"), "executedBy", 64) or "",
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"updated": len(payload["updated"])})
    return ok(payload)


@bp.post("/<pk>/cases/<case_pk>/interface-run")
def run_interface_run_case(pk: str, case_pk: str) -> Response:
    """执行轮次里的一条接口用例：按快照定义发请求并回写结果。"""
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    payload = interface_runner.run_run_case(
        conn,
        target,
        path_int(case_pk, "caseId"),
        env_id=optional_int(data.get("envId"), "envId"),
        timeout=optional_int(data.get("timeoutSeconds"), "timeoutSeconds")
        or interface_runner.DEFAULT_TIMEOUT,
        executed_by=optional_str(data.get("executedBy"), "executedBy", 64) or "local",
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""),
          {"runCaseId": path_int(case_pk, "caseId"), "statusCode": payload.get("statusCode"),
           "result": payload.get("result")})
    return ok(payload)


@bp.post("/<pk>/interface-run")
def run_interface_batch(pk: str) -> Response:
    """批量执行轮次内全部接口用例；默认投递任务队列，sync=true 时同步执行。"""
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    env_id = optional_int(data.get("envId"), "envId")
    if env_id is None and run.get("env_id"):
        env_id = int(run["env_id"])
    if env_id is None:
        raise AppError(ErrorCode.PARAM_ERROR, "轮次未绑定测试环境，请先设置执行环境")
    if TestEnvRepository(conn).get_by_id(env_id) is None:
        raise AppError(ErrorCode.PARAM_ERROR, "执行环境不存在或已删除")
    timeout = optional_int(data.get("timeoutSeconds"), "timeoutSeconds") \
        or interface_runner.DEFAULT_TIMEOUT
    executed_by = optional_str(data.get("executedBy"), "executedBy", 64) or "local"

    if data.get("sync") is True:
        payload = interface_runner.run_batch(
            conn, target, env_id=env_id, timeout=timeout, executed_by=executed_by
        )
        audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_TEST_RUN, target,
              str(run.get("name") or ""),
              {"mode": "sync", "total": payload["total"], "failed": payload["failed"]})
        commit(conn)
        return ok(payload)

    task = job_queue.submit(
        conn,
        kind="interface.batch_run",
        title=f"接口批量执行 · {run.get('name') or target}",
        payload={"runId": target, "envId": env_id, "timeoutSeconds": timeout},
        priority=80,
    )
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"mode": "async", "taskId": task.get("id")})
    commit(conn)
    return ok({"queued": True, "task": task, "runId": target, "envId": env_id})


@bp.get("/<pk>/automation-runs")
def list_automation_runs(pk: str) -> Response:
    """本轮回写过的自动化执行记录，用来看每段脚本的回写结论。"""
    conn = db()
    target = path_int(pk)
    _run_or_404(conn, target)
    rows = AutomationRunRepository(conn).list_for_test_run(target)
    return ok({"list": [automation_run_summary(row) for row in rows]})


@bp.post("/<pk>/automation-run")
def run_automation_for_run(pk: str) -> Response:
    """把自动化脚本挂到本轮执行，结果自动回写轮次；默认投递任务队列。"""
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    raw_ids = data.get("scriptIds")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise AppError(ErrorCode.PARAM_ERROR, "请至少选择一个自动化脚本")

    repo = AutomationScriptRepository(conn)
    scripts: list[dict[str, Any]] = []
    for raw in raw_ids[:20]:
        script_id = optional_int(raw, "scriptIds")
        row = repo.get(script_id) if script_id else None
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "自动化脚本不存在或已删除")
        if str(row.get("status") or "") == SCRIPT_STATUS_DISABLED:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, f"脚本「{row.get('name')}」已停用")
        project_id = row.get("project_id")
        if project_id not in (None, "") and int(project_id) != int(run["project_id"]):
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"脚本「{row.get('name')}」属于其他项目，不能在本轮执行",
            )
        automation_service.require_executable(
            conn, str(row.get("category") or "web"), str(row.get("framework") or "")
        )
        scripts.append(dict(row))

    timeout = optional_int(data.get("timeoutSeconds"), "timeoutSeconds")
    if timeout is not None and timeout <= 0:
        timeout = None

    if data.get("sync") is True:
        db_path = current_app.config["AITP_DB_PATH"]
        results = [
            automation_run_summary(
                automation_service.execute_script(conn, script, db_path, timeout, target)
            )
            for script in scripts
        ]
        audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_TEST_RUN, target,
              str(run.get("name") or ""),
              {"mode": "sync", "scripts": [script.get("script_no") for script in scripts]})
        commit(conn)
        return ok({"total": len(results), "runs": results, "runId": target})

    tasks = [
        job_queue.submit(
            conn,
            kind="automation.run",
            title=f"执行自动化脚本 · {script.get('name') or script.get('script_no')}",
            payload={
                "scriptId": int(script["id"]),
                "testRunId": target,
                "timeoutSeconds": timeout,
            },
            priority=70,
        )
        for script in scripts
    ]
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""),
          {"mode": "async", "scripts": [script.get("script_no") for script in scripts],
           "taskIds": [task.get("id") for task in tasks]})
    commit(conn)
    return ok({"queued": True, "tasks": tasks, "runId": target})


@bp.post("/<pk>/cases/<case_pk>/bug")
def create_bug_from_run_case(pk: str, case_pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    payload = test_run_service.create_bug_from_failure(
        conn, target, path_int(case_pk, "caseId"), data
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_BUG,
          int(payload["bugId"]), str(payload.get("bugNo") or ""),
          {"runId": target, "runCaseId": payload["runCaseId"]})
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"bugId": payload["bugId"]})
    return ok(payload)


@bp.post("/<pk>/complete")
def complete_run(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    data = body()
    payload = test_run_service.complete_run(
        conn, target, optional_str(data.get("remark"), "remark", 2000)
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_TRANSITION, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"status": "completed"})
    return ok(payload)


EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("caseNo", "用例编号"),
    ("title", "用例标题"),
    ("priority", "优先级"),
    ("module", "功能点"),
    ("resultLabel", "执行结果"),
    ("expectedResult", "预期结果"),
    ("actualResult", "实际结果"),
    ("remark", "备注"),
    ("bugNo", "关联缺陷"),
    ("executedBy", "执行人"),
    ("executedAt", "执行时间"),
)


@bp.get("/<pk>/export")
def export_run_cases(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    run = _run_or_404(conn, target)
    fmt = tabular_format_of(request.args.get("format"))
    rows_raw = TestRunCaseRepository(conn).list_by_run(target)
    bug_ids = {int(row["bug_id"]) for row in rows_raw if row.get("bug_id")}
    bug_nos: dict[int, str] = {}
    if bug_ids:
        placeholders = ",".join("?" for _ in bug_ids)
        bug_nos = {
            int(row["id"]): str(row["bug_no"] or "")
            for row in conn.execute(
                f"SELECT id, bug_no FROM biz_bug WHERE id IN ({placeholders})", list(bug_ids)
            )
        }
    rows = []
    for row in rows_raw:
        item = run_case_summary(row)
        item["resultLabel"] = RESULT_LABELS.get(str(item.get("result")), "")
        item["bugNo"] = bug_nos.get(int(item["bugId"])) if item.get("bugId") else ""
        rows.append(item)
    content, mimetype, extension = tabular_export.render(fmt, EXPORT_COLUMNS, rows)
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_TEST_RUN, target,
          str(run.get("name") or ""), {"format": fmt, "count": len(rows)})
    return download_response(
        content, tabular_export.safe_filename(f"测试轮次-{run.get('name') or target}", extension),
        mimetype, count=len(rows),
    )
