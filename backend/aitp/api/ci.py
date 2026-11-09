"""CI 触发 API：用令牌从命令行或流水线发起一轮测试。

令牌只在设置页生成一次，库里存 SHA-256 哈希；请求带 ``X-AITP-CI-Token``。
这一组接口是唯一绕过本地访问口令的业务路由。自 GAP-24 起，绕过口令前必须先过
「路由白名单 + 哈希 + 有效期 + 吊销」四道关（判定在 services.ci_service），令牌
泄露不再等于全权访问；本层再按 projectId 做项目范围校验。
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, Response, request

from ..common.time_utils import utcnow_iso
from ..common.validators import optional_int, optional_str, require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.automation_repo import (
    SCRIPT_STATUS_DISABLED,
    AutomationRunRepository,
    AutomationScriptRepository,
    run_summary as automation_run_summary,
)
from ..repositories.ci_repo import CiTokenRepository
from ..repositories.test_run_repo import TestRunRepository, run_summary
from ..services import audit_service, automation_service, ci_service, job_queue, test_run_service
from .helpers import attach_version_codes, body, commit, db, path_int

bp = Blueprint("ci", __name__)

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


def _token_row(conn: Any) -> dict[str, Any]:
    raw = (request.headers.get("X-AITP-CI-Token") or "").strip()
    if not raw:
        raise AppError(ErrorCode.AUTH_REQUIRED, "缺少 X-AITP-CI-Token 请求头")
    result = ci_service.verify_token(conn, raw)
    if not result.allowed or result.row is None:
        raise AppError(result.code or ErrorCode.AUTH_REQUIRED, result.message or "CI 令牌无效")
    row = result.row
    CiTokenRepository(conn).touch(int(row["id"]), utcnow_iso())
    conn.commit()
    return row


def _run_url(run_id: int) -> str:
    return f"{request.host_url.rstrip('/')}/test-runs/{run_id}"


def _project_id_of(conn: Any, token_row: dict[str, Any], data: dict[str, Any]) -> int:
    bound = token_row.get("project_id")
    requested = optional_int(data.get("projectId"), "projectId")
    if bound is not None:
        if requested is not None and int(requested) != int(bound):
            raise AppError(ErrorCode.PARAM_ERROR, "令牌只能触发绑定项目的轮次")
        project_id = int(bound)
    elif requested is None:
        raise AppError(ErrorCode.PARAM_ERROR, "projectId 不能为空：令牌未绑定项目")
    else:
        project_id = int(requested)
    row = conn.execute(
        "SELECT 1 FROM biz_project WHERE id = ? AND is_deleted = 0", (project_id,)
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.PARAM_ERROR, "项目不存在或已删除")
    return project_id


def _script_ids(raw: Any) -> list[int]:
    if raw in (None, ""):
        return []
    if not isinstance(raw, list):
        raise AppError(ErrorCode.PARAM_ERROR, "automationScriptIds 必须是数组")
    if len(raw) > 20:
        raise AppError(ErrorCode.PARAM_ERROR, "automationScriptIds 一次最多 20 个")
    return list(dict.fromkeys(require_int(item, "automationScriptIds[]") for item in raw))


def _require_cases_in_project(conn: Any, project_id: int, case_ids: list[int]) -> None:
    """令牌只能圈定绑定项目的用例，防止显式 caseIds 绕过项目边界。"""
    placeholders = ",".join("?" for _ in case_ids)
    rows = conn.execute(
        "SELECT id, project_id FROM biz_test_case"
        f" WHERE is_deleted = 0 AND id IN ({placeholders})",
        case_ids,
    ).fetchall()
    found = {int(row["id"]): int(row["project_id"]) for row in rows}
    missing = [case_id for case_id in case_ids if case_id not in found]
    if missing:
        raise AppError(ErrorCode.PARAM_ERROR, f"用例不存在或已删除：{missing}")
    foreign = [case_id for case_id, owner in found.items() if owner != project_id]
    if foreign:
        raise AppError(ErrorCode.PARAM_ERROR, "令牌只能触发绑定项目的用例")


def _load_scripts(conn: Any, project_id: int, script_ids: list[int]) -> list[dict[str, Any]]:
    repo = AutomationScriptRepository(conn)
    scripts: list[dict[str, Any]] = []
    for script_id in script_ids:
        row = repo.get(script_id)
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "自动化脚本不存在或已删除")
        if str(row.get("status") or "") == SCRIPT_STATUS_DISABLED:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, f"脚本「{row.get('name')}」已停用")
        owner = row.get("project_id")
        if owner not in (None, "") and int(owner) != project_id:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"脚本「{row.get('name')}」属于其他项目，不能在本项目执行",
            )
        automation_service.require_executable(
            conn, str(row.get("category") or "web"), str(row.get("framework") or "")
        )
        scripts.append(dict(row))
    return scripts


@bp.post("/runs")
def create_run() -> Response:
    """创建轮次并按需投递接口批量执行与自动化脚本。"""
    conn = db()
    token_row = _token_row(conn)
    data = body()
    project_id = _project_id_of(conn, token_row, data)

    raw_case_ids = data.get("caseIds")
    if isinstance(raw_case_ids, list) and raw_case_ids:
        case_ids = [require_int(item, "caseIds[]") for item in raw_case_ids]
    else:
        case_ids = test_run_service.select_case_ids(
            conn, project_id, _scope_filters(data.get("scope") or data)
        )
    if not case_ids:
        raise AppError(ErrorCode.PARAM_ERROR, "当前筛选条件下没有可执行的用例")
    _require_cases_in_project(conn, project_id, case_ids)

    name = (
        optional_str(data.get("name"), "name", 200)
        or f"CI 轮次 {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    run = test_run_service.create_run(
        conn,
        project_id=project_id,
        name=name,
        case_ids=case_ids,
        plan_id=optional_int(data.get("planId"), "planId"),
        version_id=optional_int(data.get("versionId"), "versionId"),
        env_id=optional_int(data.get("envId"), "envId"),
        env=optional_str(data.get("env"), "env", 100) or "",
        owner=optional_str(data.get("owner"), "owner", 64) or "CI",
        remark=optional_str(data.get("remark"), "remark", 2000) or "",
    )
    run_id = int(run["id"])
    env_id = run.get("envId")
    timeout = optional_int(data.get("timeoutSeconds"), "timeoutSeconds")
    if timeout is not None and timeout <= 0:
        timeout = None

    tasks: list[dict[str, Any]] = []
    warnings: list[str] = []

    run_interface = data.get("runInterface")
    run_interface = (env_id is not None) if run_interface is None else bool(run_interface)
    if run_interface:
        if env_id is None:
            raise AppError(ErrorCode.PARAM_ERROR, "接口执行需要先绑定测试环境（envId）")
        tasks.append(job_queue.submit(
            conn,
            kind="interface.batch_run",
            title=f"CI 接口执行 · {run.get('name') or run_id}",
            payload={"runId": run_id, "envId": int(env_id), "timeoutSeconds": timeout},
            priority=80,
        ))
    elif env_id is None:
        warnings.append("未绑定测试环境，接口用例不会自动执行")

    scripts = _load_scripts(conn, project_id, _script_ids(data.get("automationScriptIds")))
    automation_tasks = [
        job_queue.submit(
            conn,
            kind="automation.run",
            title=f"CI 自动化 · {script.get('name') or script.get('script_no')}",
            payload={
                "scriptId": int(script["id"]),
                "testRunId": run_id,
                "timeoutSeconds": timeout,
            },
            priority=70,
        )
        for script in scripts
    ]
    tasks.extend(automation_tasks)

    commit(conn)
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXECUTE,
        audit_service.RESOURCE_TEST_RUN,
        run_id,
        str(run.get("name") or ""),
        {
            "source": "ci",
            "tokenPrefix": token_row.get("token_prefix"),
            "cases": len(case_ids),
            "taskIds": [task.get("id") for task in tasks],
        },
        actor=f"ci:{token_row.get('name') or token_row.get('token_prefix')}",
    )
    conn.commit()

    return ok({
        "runId": run_id,
        "runName": run.get("name"),
        "projectId": project_id,
        "versionId": run.get("versionId"),
        "envId": env_id,
        "total": run.get("total"),
        "status": run.get("status"),
        "url": _run_url(run_id),
        "queued": bool(tasks),
        "tasks": tasks,
        "automationQueued": len(automation_tasks),
        "warnings": warnings,
    })


@bp.get("/runs/<pk>")
def get_run(pk: str) -> Response:
    """流水线轮询入口：轮次进度、通过率与自动化回写结论。"""
    conn = db()
    token_row = _token_row(conn)
    run_id = path_int(pk)
    row = TestRunRepository(conn).get_detail(run_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    bound = token_row.get("project_id")
    if bound is not None and int(row["project_id"]) != int(bound):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")

    payload = run_summary(row)
    payload = attach_version_codes(conn, [payload])[0]
    total = int(payload.get("total") or 0)
    not_executed = int(payload.get("notExecuted") or 0)
    executed = total - not_executed
    payload["runId"] = run_id
    payload["executed"] = executed
    payload["passRate"] = (
        round(int(payload.get("passed") or 0) / executed * 100, 2) if executed else 0.0
    )
    payload["url"] = _run_url(run_id)
    payload["automationRuns"] = [
        automation_run_summary(item)
        for item in AutomationRunRepository(conn).list_for_test_run(run_id)
    ]
    return ok(payload)
