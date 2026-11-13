"""测试计划 API：计划 CRUD + 按计划发起测试轮次。"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from flask import Blueprint, Response

from ..common.validators import check_choice, optional_int, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.test_plan_repo import PLAN_STATUSES, TestPlanRepository, plan_summary
from ..repositories.test_run_repo import TestRunRepository, run_summary
from ..services import audit_service, test_run_service
from ..services import test_round_service
from .helpers import (
    attach_version_codes,
    audit,
    body,
    check_ref_project,
    commit,
    db,
    guard_project_scope,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    reject_project_move,
    require_project,
    scope_arg,
)

bp = Blueprint("test_plans", __name__)

FILTERS = {
    "projectId": "project_id",
    "versionId": "version_id",
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


def _with_scope(row: dict[str, Any]) -> dict[str, Any]:
    item = dict(row)
    item["scope"] = loads(item.pop("scope_json", None), {}) or {}
    return item


def _public(row: dict[str, Any], run_count: int | None = None) -> dict[str, Any]:
    item = plan_summary(_with_scope(row))
    if run_count is not None:
        item["runCount"] = run_count
    return item


@bp.get("")
def list_plans() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = TestPlanRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    counts = {
        int(row["plan_id"]): int(row["c"])
        for row in conn.execute(
            "SELECT plan_id, COUNT(*) AS c FROM biz_test_run"
            " WHERE is_deleted = 0 AND plan_id IS NOT NULL GROUP BY plan_id"
        ).fetchall()
    }
    rows = [_public(row, counts.get(int(row["id"]), 0)) for row in payload["list"]]
    payload["list"] = attach_version_codes(conn, rows)
    return ok(payload)


@bp.get("/<pk>")
def get_plan(pk: str) -> Response:
    conn = db()
    repo = TestPlanRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试计划不存在")
    guard_project_scope(row, scope_arg(), "测试计划")
    payload = _public(row)
    runs = conn.execute(
        "SELECT * FROM biz_test_run WHERE plan_id = ? AND is_deleted = 0"
        " ORDER BY id DESC LIMIT 50",
        (target,),
    ).fetchall()
    payload["runs"] = attach_version_codes(conn, [run_summary(dict(r)) for r in runs])
    payload["runCount"] = len(runs)
    return ok(payload)


def _payload_of(data: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {}
    if "projectId" in data:
        payload["project_id"] = require_int(data["projectId"], "projectId")
    if "versionId" in data:
        payload["version_id"] = optional_int(data["versionId"], "versionId")
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 200)
    if "description" in data:
        payload["description"] = optional_str(data["description"], "description", 2000) or ""
    if "owner" in data:
        payload["owner"] = optional_str(data["owner"], "owner", 64) or ""
    if "defaultEnvId" in data:
        payload["default_env_id"] = optional_int(data["defaultEnvId"], "defaultEnvId")
    if "scope" in data:
        if not isinstance(data["scope"], dict):
            raise AppError(ErrorCode.PARAM_ERROR, "scope 必须是对象")
        clean = {
            key: data["scope"][key]
            for key in SCOPE_FIELDS
            if data["scope"].get(key) not in (None, "")
        }
        payload["scope_json"] = dumps(clean)
    if "status" in data:
        payload["status"] = check_choice(data["status"], PLAN_STATUSES, "status", "active") or "active"
    if existing is None:
        payload.setdefault("status", "active")
        payload.setdefault("scope_json", "{}")
        payload.setdefault("description", "")
        payload.setdefault("owner", "")
    return payload


def _validate_env(conn: Any, payload: dict[str, Any]) -> None:
    env_id = payload.get("default_env_id")
    if env_id is not None:
        test_run_service.resolve_env(conn, int(env_id))


@bp.post("")
def create_plan() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    require_project(conn, project_id)
    repo = TestPlanRepository(conn)
    payload = _payload_of(data)
    if payload.get("version_id") is not None:
        payload["version_id"] = check_ref_project(
            conn, "biz_project_version", payload["version_id"],
            project_id, "项目版本", "versionId",
        )
    _validate_env(conn, payload)
    new_id = repo.insert({"project_id": project_id, **payload})
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_PLAN, new_id,
          str(data.get("name") or ""), {"projectId": project_id})
    return ok(_public(repo.get_detail(new_id) or {}))


@bp.put("/<pk>")
def update_plan(pk: str) -> Response:
    conn = db()
    repo = TestPlanRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试计划不存在")
    data = body()
    reject_project_move(data, row.get("project_id"), "测试计划")
    payload = _payload_of(data, existing=row)
    payload.pop("project_id", None)
    if payload.get("version_id") is not None:
        payload["version_id"] = check_ref_project(
            conn, "biz_project_version", payload["version_id"],
            row.get("project_id"), "项目版本", "versionId",
        )
    _validate_env(conn, payload)
    if payload:
        repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_PLAN, target,
          str(payload.get("name") or row.get("name") or ""), {"fields": sorted(payload.keys())})
    return ok(_public(repo.get_detail(target) or {}))


@bp.delete("/<pk>")
def delete_plan(pk: str) -> Response:
    conn = db()
    repo = TestPlanRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试计划不存在")
    active = conn.execute(
        "SELECT COUNT(*) FROM biz_test_run WHERE plan_id = ? AND is_deleted = 0"
        " AND status IN ('pending', 'running')",
        (target,),
    ).fetchone()[0]
    if int(active) > 0:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "计划下还有未完成的轮次，请先完成或删除轮次")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_PLAN, target,
          str(row.get("name") or ""))
    return ok({"id": target, "deleted": True})


@bp.post("/<pk>/runs")
def create_run_from_plan(pk: str) -> Response:
    """按计划发起一轮测试：用例范围取计划默认范围 + 本次覆盖范围。"""
    conn = db()
    repo = TestPlanRepository(conn)
    target = path_int(pk)
    plan = repo.get_detail(target)
    if not plan:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试计划不存在")
    if str(plan.get("status") or "") == "archived":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "计划已归档，请先启用后再发起轮次")
    data = body()
    project_id = int(plan["project_id"])
    version_id = optional_int(data.get("versionId"), "versionId")
    if version_id is None:
        version_id = plan.get("version_id")
    merged_scope = _scope_filters(loads(plan.get("scope_json"), {}) or {})
    merged_scope.update(_scope_filters(data.get("scope")))
    round_type = check_choice(
        data.get("roundType"), test_round_service.ROUND_TYPE_VALUES, "roundType", ""
    ) or ""
    preset = check_choice(
        data.get("scopePreset"), test_round_service.SCOPE_PRESET_VALUES, "scopePreset", ""
    ) or test_round_service.ROUND_TYPE_PRESETS.get(round_type, "")
    case_ids = data.get("caseIds")
    if isinstance(case_ids, list) and case_ids:
        ids = [require_int(item, "caseIds[]") for item in case_ids]
    elif preset:
        ids = test_round_service.resolve_scope(
            conn, project_id, version_id, preset,
            optional_int(data.get("prevRunId"), "prevRunId"),
        )
    else:
        ids = test_run_service.select_case_ids(conn, project_id, merged_scope)
    env_id = optional_int(data.get("envId"), "envId") or plan.get("default_env_id")
    env_id, env_name = test_run_service.resolve_env(
        conn, env_id, optional_str(data.get("env"), "env", 100) or ""
    )
    name = optional_str(data.get("name"), "name", 200) or (
        f"{plan.get('name')} · {datetime.now().strftime('%Y-%m-%d %H:%M')}"
    )
    payload = test_run_service.create_run(
        conn,
        project_id=project_id,
        name=name,
        case_ids=ids,
        plan_id=target,
        version_id=version_id,
        env_id=env_id,
        env=env_name,
        owner=optional_str(data.get("owner"), "owner", 64) or str(plan.get("owner") or ""),
        remark=optional_str(data.get("remark"), "remark", 2000) or "",
        round_type=round_type,
        goal=optional_str(data.get("goal"), "goal", 500)
        or test_round_service.ROUND_TYPE_GOALS.get(round_type, ""),
        planned_start=optional_str(data.get("plannedStart"), "plannedStart", 10) or "",
        planned_end=optional_str(data.get("plannedEnd"), "plannedEnd", 10) or "",
        scope_preset=preset,
        seq=int(data.get("seq") or 0),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_RUN,
          int(payload["id"]), name, {"planId": target, "cases": len(ids)})
    return ok(test_run_service.attach_envs(conn, [payload])[0])


@bp.post("/<pk>/rounds/preview-scope")
def preview_round_scope(pk: str) -> Response:
    """按范围预设预圈用例（不建轮次），返回用例清单供发起对话框勾选。"""
    conn = db()
    target = path_int(pk)
    plan = TestPlanRepository(conn).get_detail(target)
    if not plan:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试计划不存在")
    guard_project_scope(plan, scope_arg(), "测试计划")
    data = body()
    preset = check_choice(
        data.get("preset"), test_round_service.SCOPE_PRESET_VALUES, "preset", ""
    )
    if not preset:
        raise AppError(ErrorCode.PARAM_ERROR, "preset 必填")
    version_id = optional_int(data.get("versionId"), "versionId") or plan.get("version_id")
    ids = test_round_service.resolve_scope(
        conn, int(plan["project_id"]), version_id, preset,
        optional_int(data.get("prevRunId"), "prevRunId"),
    )
    cases: list[dict[str, Any]] = []
    if ids:
        marks = ",".join("?" for _ in ids)
        rows = conn.execute(
            f"SELECT id, case_no, title, priority, case_type, status FROM biz_test_case"  # noqa: S608
            f" WHERE id IN ({marks}) AND is_deleted = 0",
            ids,
        ).fetchall()
        by_id = {int(r["id"]): r for r in rows}
        cases = [
            {
                "id": int(by_id[i]["id"]),
                "caseNo": by_id[i]["case_no"],
                "title": by_id[i]["title"],
                "priority": by_id[i]["priority"],
                "caseType": by_id[i]["case_type"],
                "status": by_id[i]["status"],
            }
            for i in ids if i in by_id
        ]
    return ok({"preset": preset, "count": len(cases), "caseIds": [c["id"] for c in cases], "cases": cases})


@bp.post("/<pk>/rounds/from-template")
def create_rounds_from_template(pk: str) -> Response:
    """按 4/3 轮模板为计划一键生成轮次序列（计划中状态 + 计划时间窗）。"""
    conn = db()
    target = path_int(pk)
    plan = TestPlanRepository(conn).get_detail(target)
    if not plan:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试计划不存在")
    guard_project_scope(plan, scope_arg(), "测试计划")
    data = body()
    template = require_str(data.get("template"), "template", 20)
    start_date = require_str(data.get("startDate"), "startDate", 10)
    durations = data.get("durations")
    if durations is not None and not isinstance(durations, list):
        raise AppError(ErrorCode.PARAM_ERROR, "durations 必须是数组")
    runs = test_round_service.generate_template(
        conn, plan, template, start_date,
        env_id=optional_int(data.get("envId"), "envId"),
        owner=optional_str(data.get("owner"), "owner", 64) or "",
        durations=[int(d) for d in durations] if durations else None,
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_PLAN, target,
          str(plan.get("name") or ""), {"action": "from_template", "template": template, "rounds": len(runs)})
    return ok({"runs": test_run_service.attach_envs(conn, attach_version_codes(conn, runs))})
