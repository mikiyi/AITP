"""多接口串联场景自动化 API：CRUD + 执行 + 执行历史。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app

from ..common.validators import check_choice, optional_int, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.api_scenario_repo import ApiScenarioRepository, ApiScenarioRunRepository
from ..repositories.base import dumps, loads
from ..services import api_scenario_service, app_scenario_service, audit_service
from .helpers import (
    audit,
    body,
    commit,
    db,
    guard_project_scope,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    require_project,
    scope_arg,
)

bp = Blueprint("api_scenarios", __name__)

FILTERS = {"projectId": "project_id", "status": "status", "scenarioType": "scenario_type"}

#: 场景类型：web=浏览器场景 / app=App 场景（adb 驱动） / api=接口场景
SCENARIO_TYPES = ("web", "app", "api")


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "scenarioNo": row.get("scenario_no"),
        "projectId": row.get("project_id"),
        "name": row.get("name"),
        "description": row.get("description") or "",
        "scenarioType": row.get("scenario_type") or "api",
        "envId": row.get("env_id"),
        "dataSource": row.get("data_source") or "",
        "steps": loads(row.get("steps_json"), []) or [],
        "status": row.get("status") or "ready",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _steps_of(data: dict[str, Any]) -> list[dict[str, Any]]:
    raw = data.get("steps")
    if not isinstance(raw, list):
        raise AppError(ErrorCode.PARAM_ERROR, "steps 必须是数组")
    steps: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "步骤必须是对象")
        stype = str(item.get("type") or "interface")
        if stype == "database":
            raw_extra = item.get("extra")
            db_extra = raw_extra if isinstance(raw_extra, dict) else {}
            db_ref = str(item.get("dbRef") or "").strip()
            statement = str(item.get("statement") or db_extra.get("查询语句") or "").strip()
            if not db_ref and not statement:
                raise AppError(ErrorCode.PARAM_ERROR, "数据库步骤必须填写查询语句或引用数据库脚本编号")
            steps.append({
                "name": str(item.get("name") or ""), "type": "database",
                "statement": statement, "dbRef": db_ref, "extra": db_extra,
                "extract": item.get("extract") if isinstance(item.get("extract"), list) else [],
                "continueOnError": bool(item.get("continueOnError")),
                "waitMs": int(item.get("waitMs") or 0),
            })
            continue
        if stype == "browser":
            action = str(item.get("action") or "navigate")
            if action != "snapshot" and not (str(item.get("url") or "").strip() or str(item.get("selector") or "").strip()):
                raise AppError(ErrorCode.PARAM_ERROR, "浏览器步骤必须填写 URL 或选择器")
            steps.append({
                "name": str(item.get("name") or ""), "type": "browser",
                "action": action,
                "url": str(item.get("url") or ""), "selector": str(item.get("selector") or ""),
                "value": str(item.get("value") or ""),
                "extract": item.get("extract") if isinstance(item.get("extract"), list) else [],
                "continueOnError": bool(item.get("continueOnError")),
                "waitMs": int(item.get("waitMs") or 0),
            })
            continue
        if stype == "automation":
            # 自动化脚本步骤：只存脚本编号，执行时按编号取脚本；引用判定扫 steps_json 里的编号
            script_no = str(item.get("scriptNo") or "").strip()
            if not script_no:
                raise AppError(ErrorCode.PARAM_ERROR, "自动化脚本步骤必须选择脚本编号")
            steps.append({
                "name": str(item.get("name") or ""), "type": "automation",
                "scriptNo": script_no,
                "extract": item.get("extract") if isinstance(item.get("extract"), list) else [],
                "continueOnError": bool(item.get("continueOnError")),
                "waitMs": int(item.get("waitMs") or 0),
            })
            continue
        if stype == "app":
            action = str(item.get("action") or "launch")
            if action not in app_scenario_service.ACTIONS:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"不支持的 App 动作：{action}（可选 {' / '.join(app_scenario_service.ACTIONS)}）",
                )
            if action == "launch" and not str(item.get("package") or "").strip():
                raise AppError(ErrorCode.PARAM_ERROR, "App 启动步骤必须填写包名")
            if action in ("tap", "assertText") and not (
                str(item.get("target") or "").strip() or str(item.get("value") or "").strip()
            ) and not (str(item.get("x") or "").strip() and str(item.get("y") or "").strip()):
                raise AppError(ErrorCode.PARAM_ERROR, "App 点选/断言步骤需要填写文本或坐标")
            if action == "input" and not str(item.get("value") or "").strip():
                raise AppError(ErrorCode.PARAM_ERROR, "App 输入步骤必须填写文本")
            steps.append({
                "name": str(item.get("name") or ""), "type": "app", "action": action,
                "target": str(item.get("target") or ""), "value": str(item.get("value") or ""),
                "package": str(item.get("package") or ""), "activity": str(item.get("activity") or ""),
                "x": str(item.get("x") or ""), "y": str(item.get("y") or ""),
                "x2": str(item.get("x2") or ""), "y2": str(item.get("y2") or ""),
                "durationMs": int(item.get("durationMs") or 300),
                "serial": str(item.get("serial") or ""),
                "extract": item.get("extract") if isinstance(item.get("extract"), list) else [],
                "continueOnError": bool(item.get("continueOnError")),
                "waitMs": int(item.get("waitMs") or 0),
            })
            continue
        extra = item.get("extra")
        if not isinstance(extra, dict) or not str(extra.get("接口URL") or "").strip():
            raise AppError(ErrorCode.PARAM_ERROR, "每步必须填写接口URL")
        steps.append({
            "name": str(item.get("name") or ""), "type": "interface", "extra": extra,
            "extract": item.get("extract") if isinstance(item.get("extract"), list) else [],
            "continueOnError": bool(item.get("continueOnError")),
            "waitMs": int(item.get("waitMs") or 0),
        })
    return steps


@bp.get("")
def list_scenarios() -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = query_filters(FILTERS)
    page = ApiScenarioRepository(conn).list(filters, keyword_of() or None,
                                            page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_public(r) for r in payload["list"]]
    return ok(payload)


@bp.get("/<pk>")
def get_scenario(pk: str) -> Response:
    conn = db()
    row = ApiScenarioRepository(conn).get(path_int(pk))
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(row, scope_arg(), "接口场景")
    return ok(_public(row))


@bp.post("")
def create_scenario() -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    name = require_str(data.get("name"), "name", 200)
    scenario_type = check_choice(
        data.get("scenarioType"), SCENARIO_TYPES, "scenarioType", "api"
    ) or "api"
    repo = ApiScenarioRepository(conn)
    scenario_no = repo.next_scenario_no()
    new_id = repo.insert({
        "scenario_no": scenario_no,
        "project_id": project_id,
        "name": name,
        "scenario_type": scenario_type,
        "description": optional_str(data.get("description"), "description", 2000),
        "env_id": optional_int(data.get("envId"), "envId"),
        "data_source": optional_str(data.get("dataSource"), "dataSource", 20000) or "",
        "steps_json": dumps(_steps_of(data)),
        "status": check_choice(data.get("status"), ("ready", "disabled"), "status", "ready") or "ready",
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, "api_scenario", new_id, name,
          {"scenarioNo": scenario_no})
    return ok(_public(repo.get(new_id) or {}))


@bp.put("/<pk>")
def update_scenario(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = ApiScenarioRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(row, scope_arg(), "接口场景")
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 200)
    if "description" in data:
        payload["description"] = optional_str(data["description"], "description", 2000)
    if "envId" in data:
        payload["env_id"] = optional_int(data["envId"], "envId")
    if "dataSource" in data:
        payload["data_source"] = optional_str(data["dataSource"], "dataSource", 20000) or ""
    if "scenarioType" in data:
        payload["scenario_type"] = check_choice(
            data["scenarioType"], SCENARIO_TYPES, "scenarioType"
        ) or "api"
    if "steps" in data:
        payload["steps_json"] = dumps(_steps_of(data))
    if "status" in data:
        payload["status"] = check_choice(data["status"], ("ready", "disabled"), "status") or "ready"
    repo.update(target, payload)
    commit(conn)
    return ok(_public(repo.get(target) or {}))


@bp.delete("/<pk>")
def delete_scenario(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = ApiScenarioRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(row, scope_arg(), "接口场景")
    repo.soft_delete(target)
    commit(conn)
    return ok({"deleted": True})


@bp.post("/<pk>/copy")
def copy_scenario(pk: str) -> Response:
    """复制单条场景：重新取 SCN 编号、名称加「副本-」前缀，步骤/环境/数据源/类型一致。"""
    conn = db()
    target = path_int(pk)
    repo = ApiScenarioRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(row, scope_arg(), "接口场景")
    new_id = repo.insert({
        "scenario_no": repo.next_scenario_no(),
        "project_id": row.get("project_id"),
        "name": (f"副本-{row.get('name') or ''}")[:200],
        "scenario_type": row.get("scenario_type") or "api",
        "description": row.get("description") or "",
        "env_id": row.get("env_id"),
        "data_source": row.get("data_source") or "",
        "steps_json": row.get("steps_json") or "[]",
        "status": row.get("status") or "ready",
    })
    commit(conn)
    new_row = repo.get(new_id) or {}
    audit(conn, audit_service.AUDIT_ACTION_CREATE, "api_scenario", new_id,
          str(new_row.get("name") or ""),
          {"scenarioNo": new_row.get("scenario_no"), "copyOf": row.get("scenario_no")})
    return ok(_public(new_row))


@bp.post("/<pk>/run")
def run_scenario(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = ApiScenarioRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(row, scope_arg(), "接口场景")
    data = body()
    env_id = optional_int(data.get("envId"), "envId") or row.get("env_id")
    db_path = Path(current_app.config["AITP_DB_PATH"])
    result = api_scenario_service.run_with_data(conn, dict(row), env_id, db_path)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, "api_scenario_run",
          int(result["runId"]), str(row.get("name")), {"passed": result["passed"]})
    return ok(result)


@bp.get("/<pk>/trend")
def scenario_trend(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    scenario = ApiScenarioRepository(conn).get(target)
    if scenario is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(scenario, scope_arg(), "接口场景")
    return ok(api_scenario_service.trend(conn, target))


@bp.get("/<pk>/runs/<run_id>/report")
def scenario_report(pk: str, run_id: str) -> Response:
    conn = db()
    scenario = ApiScenarioRepository(conn).get(path_int(pk))
    if scenario is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(scenario, scope_arg(), "接口场景")
    run_row = ApiScenarioRunRepository(conn).get(path_int(run_id))
    if run_row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    if int(run_row["scenario_id"]) != int(scenario["id"]):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不属于该场景")
    html = api_scenario_service.build_html(dict(scenario), dict(run_row))
    return Response(html, mimetype="text/html")


@bp.post("/<pk>/runs/<run_id>/to-bug")
def scenario_to_bug(pk: str, run_id: str) -> Response:
    conn = db()
    scenario = ApiScenarioRepository(conn).get(path_int(pk))
    if scenario is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(scenario, scope_arg(), "接口场景")
    run_row = ApiScenarioRunRepository(conn).get(path_int(run_id))
    if run_row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    if int(run_row["scenario_id"]) != int(scenario["id"]):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不属于该场景")
    result = api_scenario_service.create_bug_from_run(conn, dict(scenario), dict(run_row))
    return ok(result)


@bp.get("/<pk>/runs")
def list_runs(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    scenario = ApiScenarioRepository(conn).get(target)
    if scenario is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "场景不存在")
    guard_project_scope(scenario, scope_arg(), "接口场景")
    page_num, page_size = page_args()
    page = ApiScenarioRunRepository(conn).list({"scenario_id": target}, None,
                                               page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    for item in payload["list"]:
        item["summary"] = loads(item.pop("summary_json", None), {}) or {}
    return ok(payload)
