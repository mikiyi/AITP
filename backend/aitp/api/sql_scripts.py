"""数据库脚本资产 API：SQL 脚本 CRUD + 执行 + 变量。

    GET    /api/v1/sql-scripts               列表（按项目过滤 + 关键字，含引用计数）
    POST   /api/v1/sql-scripts              新建（编号 SQL-001 递增）
    GET    /api/v1/sql-scripts/<pk>         详情
    PUT    /api/v1/sql-scripts/<pk>         更新正文 / 变量 / 别称 / 绑定环境
    DELETE /api/v1/sql-scripts/<pk>         删除（被引用时留痕提示，不阻断）
    POST   /api/v1/sql-scripts/<pk>/execute 在绑定环境上执行（只读断言或写前后置）

执行引擎复用 services.db_checkpoint_service：allowWrite 时放开写操作
（前置/后置造数/清理），断言场景走只读路径。连接信息统一由环境页 DB 连接承载，
脚本本身不存连接信息。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, request

from ..common.validators import require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.sql_script_repo import (
    SqlScriptRepository,
    sql_script_summary,
)
from ..repositories.test_env_repo import TestEnvRepository
from ..services import audit_service, db_checkpoint_service, nav_badge_service
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
    reject_project_move,
    scope_arg,
)

bp = Blueprint("sql_scripts", __name__)

_AUDIT_RESOURCE = "sql_script"

FILTERS = {"projectId": "project_id", "status": "status", "envId": "env_id"}

WRITE_FIELDS = {
    "name", "alias", "content", "variables", "envId", "projectId",
    "description", "status",
}


def _public(row: dict[str, Any]) -> dict[str, Any]:
    out = sql_script_summary(row)
    repo = SqlScriptRepository(db())
    refs = repo.refs_of(int(row["id"]))
    out["refCount"] = len(refs)
    out["referenced"] = len(refs) > 0
    out["refs"] = refs
    env_row = TestEnvRepository(db()).get_by_id(int(row.get("env_id") or 0))
    out["envName"] = str((env_row or {}).get("name") or "")
    return out


def _normalize_variables(raw: Any) -> list[dict[str, str]]:
    """变量表统一为 [{name, value}]；入参接受 JSON 字符串 / 列表 / 对象。"""
    if raw is None or raw == "":
        return []
    if isinstance(raw, str):
        try:
            raw = loads(raw)
        except (TypeError, ValueError):
            raise AppError(ErrorCode.PARAM_ERROR, "variables 必须是 JSON 数组 [{name,value}]")
    if isinstance(raw, dict):
        raw = [{"name": str(k), "value": str(v)} for k, v in raw.items()]
    if not isinstance(raw, list):
        raise AppError(ErrorCode.PARAM_ERROR, "variables 必须是 JSON 数组 [{name,value}]")
    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "变量项必须是对象 {name,value}")
        name = str(item.get("name") or "").strip()
        if not name:
            raise AppError(ErrorCode.PARAM_ERROR, "变量名不能为空")
        value = item.get("value")
        if not isinstance(value, str):
            value = dumps(value) if value is not None else ""
        out.append({"name": name, "value": value})
    seen: set[str] = set()
    for item in out:
        if item["name"] in seen:
            raise AppError(ErrorCode.PARAM_ERROR, f"变量名重复：{item['name']}")
        seen.add(item["name"])
    return out


def _merge_variables(
    conn: Any, script_row: dict[str, Any], caller_vars: dict[str, str]
) -> dict[str, str]:
    """脚本自身变量表并入调用方变量池（脚本变量优先级高于调用方同名变量）。"""
    merged = dict(caller_vars or {})
    for item in loads(script_row.get("variables"), []) or []:
        name = str(item.get("name") or "").strip()
        if name:
            merged[name] = str(item.get("value") or "")
    return merged


def _resolve_statement(
    conn: Any, script_row: dict[str, Any],
    caller_vars: dict[str, str] | None = None,
) -> tuple[str, dict[str, str]]:
    """把正文里的 {{var}} 渲染成最终语句，返回 (渲染后语句, 最终变量池)。"""
    from ..services.interface_runner import _substitute

    merged = _merge_variables(conn, script_row, caller_vars or {})
    content = str(script_row.get("content") or "")
    rendered = _substitute(content, merged)
    return rendered, merged


def _load_env(conn: Any, script_row: dict[str, Any]) -> dict[str, Any] | None:
    env_id = script_row.get("env_id")
    if not env_id:
        return None
    row = TestEnvRepository(conn).get_by_id(int(env_id))
    return row if row else None


@bp.get("/sql-scripts")
def list_scripts() -> Any:
    conn = db()
    page_num, page_size = page_args()
    repo = SqlScriptRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    items = [_public(row) for row in page.list]
    # referenced 是计算字段，从请求串里直接读，不走 FILTERS
    ref_param = str(request.args.get("referenced") or "").strip().lower()
    if ref_param in ("true", "false"):
        want = ref_param == "true"
        items = [item for item in items if bool(item["referenced"]) == want]
    payload = page_payload(page)
    payload["list"] = items
    payload["total"] = len(items)
    payload["totalCount"] = repo.count_by_project(filters.get("project_id"))
    return ok(payload)


@bp.get("/sql-scripts/<pk>")
def get_script(pk: str) -> Any:
    conn = db()
    target = path_int(pk)
    row = SqlScriptRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "数据库脚本不存在")
    guard_project_scope(row, scope_arg(), "数据库脚本")
    return ok(_public(row))


@bp.post("/sql-scripts")
def create_script() -> Any:
    conn = db()
    data = body()
    name = require_str(data.get("name"), "name", 200)
    payload: dict[str, Any] = {}
    for field in WRITE_FIELDS:
        if field in data and field not in ("projectId", "envId"):
            payload[field] = data[field]
    if "envId" in data:
        if data.get("envId") not in (None, "", 0):
            payload["env_id"] = int(data["envId"])
    if "projectId" in data:
        if data.get("projectId") not in (None, "", 0):
            payload["project_id"] = int(data["projectId"])
    if "variables" in data:
        payload["variables"] = dumps(_normalize_variables(data.get("variables")))
    payload["name"] = name
    if "status" not in payload:
        payload["status"] = "ready"

    repo = SqlScriptRepository(conn)
    # ai=True 时走 AI_SQL-xxx 独立编号序列，便于在列表页一眼识别 AI 来源
    ai = bool(data.get("ai"))
    script_no = repo.next_script_no(ai=ai)
    payload["script_no"] = script_no
    # 别称在所属项目内唯一
    alias = str(payload.get("alias") or "").strip()
    if alias:
        project_id = payload.get("project_id")
        exist = repo.find_by_alias(alias, project_id=int(project_id) if project_id else None)
        if exist is not None:
            raise AppError(ErrorCode.PARAM_ERROR, f"当前项目已有同别称的数据库脚本「{alias}」")
    if payload.get("env_id"):
        if TestEnvRepository(conn).get_by_id(int(payload["env_id"])) is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "绑定的执行环境不存在")
    script_id = repo.insert(payload)
    if ai:
        nav_badge_service.increase_for(conn, "sql_scripts")
    commit(conn)
    row = repo.get(script_id) or {}
    audit(conn, audit_service.AUDIT_ACTION_CREATE, _AUDIT_RESOURCE, script_id, name,
          {"scriptNo": script_no, "ai": ai})
    return ok(_public(row))


@bp.put("/sql-scripts/<pk>")
def update_script(pk: str) -> Any:
    conn = db()
    target = path_int(pk)
    repo = SqlScriptRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "数据库脚本不存在")
    data = body()
    reject_project_move(data, row.get("project_id"), "数据库脚本")
    payload: dict[str, Any] = {}
    for field in WRITE_FIELDS:
        if field not in data:
            continue
        if field == "envId":
            if data.get("envId") in (None, "", 0):
                payload["env_id"] = None
            else:
                payload["env_id"] = int(data["envId"])
        elif field == "projectId":
            payload["project_id"] = int(data["projectId"]) if data.get("projectId") else None
        else:
            payload[field] = data[field]
    if "variables" in data:
        payload["variables"] = dumps(_normalize_variables(data.get("variables")))
    if "alias" in payload:
        alias = str(payload.get("alias") or "").strip()
        if alias:
            exist = repo.find_by_alias(
                alias,
                project_id=int(row.get("project_id") or 0) or None,
                exclude_id=target,
            )
            if exist is not None:
                raise AppError(ErrorCode.PARAM_ERROR, f"当前项目已有同别称的数据库脚本「{alias}」")
    if payload.get("env_id"):
        if TestEnvRepository(conn).get_by_id(int(payload["env_id"])) is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "绑定的执行环境不存在")
    repo.update(target, payload)
    commit(conn)
    updated = repo.get(target) or {}
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, _AUDIT_RESOURCE, target,
          str(updated.get("name") or ""), {"fields": sorted(payload.keys())})
    return ok(_public(updated))


@bp.delete("/sql-scripts/<pk>")
def delete_script(pk: str) -> Any:
    conn = db()
    target = path_int(pk)
    repo = SqlScriptRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "数据库脚本不存在")
    refs = repo.refs_of(target)
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, _AUDIT_RESOURCE, target,
          str(row.get("name") or ""), {"scriptNo": row.get("script_no"),
                                       "refs": refs})
    return ok({"id": target, "deleted": True, "refs": refs})


@bp.get("/sql-scripts/<pk>/refs")
def list_refs(pk: str) -> Any:
    conn = db()
    target = path_int(pk)
    row = SqlScriptRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "数据库脚本不存在")
    return ok({"refs": SqlScriptRepository(conn).refs_of(target)})


@bp.post("/sql-scripts/<pk>/execute")
def execute_script(pk: str) -> Any:
    """在脚本绑定的执行环境上执行。

    请求体：{allowWrite: bool, variables: dict, timeoutSeconds: int}
    只读路径（默认）把结果转成统一 JSON；写路径（前置/后置）提交后回 affectedRows。
    """
    conn = db()
    target = path_int(pk)
    row = SqlScriptRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "数据库脚本不存在")
    if str(row.get("status") or "") != "ready":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "脚本已停用，先启用再执行")
    data = body() or {}
    caller_vars = data.get("variables") or {}
    if not isinstance(caller_vars, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "variables 必须是对象 {name: value}")
    allow_write = bool(data.get("allowWrite"))
    timeout = int(data.get("timeoutSeconds") or 0) or db_checkpoint_service.DEFAULT_TIMEOUT
    env = _load_env(conn, row)
    if env is None:
        raise AppError(ErrorCode.PARAM_ERROR, "脚本未绑定执行环境，无法执行")
    rendered, _ = _resolve_statement(conn, row, {str(k): str(v) for k, v in caller_vars.items()})
    result = db_checkpoint_service.run_statement(
        conn, env, rendered, allow_write=allow_write, timeout=timeout
    )
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, _AUDIT_RESOURCE, target,
          str(row.get("name") or ""), {"scriptNo": row.get("script_no"),
                                       "allowWrite": allow_write,
                                       "affectedRows": result.get("affectedRows")})
    commit(conn)
    return ok(result)
