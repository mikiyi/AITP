"""独立数据池 API：跨用例 / 场景共享一份测试数据。

    GET    /api/v1/data-pools            列表（按项目过滤，支持关键字）
    POST   /api/v1/data-pools            新建（项目内名称唯一）
    GET    /api/v1/data-pools/<pk>       详情
    PUT    /api/v1/data-pools/<pk>       更新内容 / 说明 / 名称
    DELETE /api/v1/data-pools/<pk>       删除
    POST   /api/v1/data-pools/<pk>/preview  解析预览（只回样本）

用例扩展字段与场景数据源统一用 ``pool:<名称>`` 引用，
解析复用 services.case_test_data，不另造一套取数口径。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response

from ..common.validators import optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import loads
from ..repositories.data_pool_repo import DataPoolRepository
from ..services import audit_service, case_test_data
from .helpers import (
    audit, body, commit, db, guard_project_scope, keyword_of, page_args,
    page_payload, path_int, require_project, scope_arg,
)

bp = Blueprint("data_pools", __name__)

_AUDIT_RESOURCE = "data_pool"


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "name": row.get("name"),
        "content": row.get("content") or "",
        "description": row.get("description") or "",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _guard_name(conn: Any, project_id: int, name: str, exclude_id: int | None = None) -> None:
    row = conn.execute(
        "SELECT id FROM biz_data_pool WHERE project_id = ? AND name = ? AND is_deleted = 0",
        (project_id, name),
    ).fetchone()
    if row is not None and int(row["id"]) != int(exclude_id or 0):
        raise AppError(ErrorCode.PARAM_ERROR, f"当前项目已有同名数据池「{name}」")


def _load(conn: Any, target: int) -> dict[str, Any]:
    row = DataPoolRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "数据池不存在或已删除")
    return row


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def _is_pool_ref(value: Any, name: str) -> bool:
    """与 case_test_data.resolve_for 同口径：去空白、前缀不区分大小写、名称精确匹配。"""
    text = str(value or "").strip()
    prefix = case_test_data.POOL_PREFIX
    if text[: len(prefix)].lower() != prefix:
        return False
    return text[len(prefix):].strip() == name


def _pool_references(conn: Any, project_id: int, name: str) -> list[dict[str, Any]]:
    """找出仍以 ``pool:<name>`` 引用该池的用例与接口场景。

    只查当前项目（池按项目隔离），只看解析器真正消费的字段：
    用例扩展字段「测试数据」、场景数据源；已软删的记录不再算引用。
    """
    pattern = f"%{case_test_data.POOL_PREFIX}{_escape_like(name)}%"
    refs: list[dict[str, Any]] = []
    cases = conn.execute(
        "SELECT id, case_no, title, extra_json FROM biz_test_case"
        " WHERE project_id = ? AND is_deleted = 0 AND extra_json LIKE ? ESCAPE '\\'",
        (project_id, pattern),
    ).fetchall()
    for row in cases:
        extra = loads(row["extra_json"], {}) or {}
        if isinstance(extra, dict) and _is_pool_ref(extra.get("测试数据"), name):
            refs.append({
                "type": "case", "id": int(row["id"]),
                "no": str(row["case_no"] or ""), "title": str(row["title"] or ""),
            })
    scenarios = conn.execute(
        "SELECT id, name, data_source FROM biz_api_scenario"
        " WHERE project_id = ? AND is_deleted = 0 AND data_source LIKE ? ESCAPE '\\'",
        (project_id, pattern),
    ).fetchall()
    for row in scenarios:
        if _is_pool_ref(row["data_source"], name):
            refs.append({
                "type": "scenario", "id": int(row["id"]),
                "no": "", "title": str(row["name"] or ""),
            })
    return refs


def _reference_summary(refs: list[dict[str, Any]]) -> str:
    case_refs = [item for item in refs if item["type"] == "case"]
    scenario_refs = [item for item in refs if item["type"] == "scenario"]
    counts: list[str] = []
    if case_refs:
        counts.append(f"{len(case_refs)} 条用例")
    if scenario_refs:
        counts.append(f"{len(scenario_refs)} 个接口场景")
    owners: list[str] = []
    for item in case_refs:
        owners.append(f"用例 {item['no'] or item['id']} {item['title']}".strip())
    for item in scenario_refs:
        owners.append(f"场景「{item['title'] or item['id']}」")
    shown = "、".join(owners[:5])
    if len(owners) > 5:
        shown += f" 等 {len(owners)} 处"
    return f"{'、'.join(counts)}（{shown}）"


def _guard_references(conn: Any, project_id: int, name: str, action: str) -> None:
    refs = _pool_references(conn, project_id, name)
    if not refs:
        return
    raise AppError(
        ErrorCode.RESOURCE_REFERENCED,
        f"数据池「{name}」仍被 {_reference_summary(refs)} 引用，{action}后这些用例 / 场景会取不到数。"
        "请先把引用改为内联数据或换用其他数据池。",
        {"references": refs},
    )


@bp.get("")
def list_pools() -> Response:
    conn = db()
    project_id = require_project(conn, require_int(scope_arg() or None, "projectId"))
    page_num, page_size = page_args()
    page = DataPoolRepository(conn).list(
        {"project_id": project_id}, keyword_of() or None,
        page_num=page_num, page_size=page_size,
    )
    payload = page_payload(page)
    payload["list"] = [_public(row) for row in payload["list"]]
    return ok(payload)


@bp.post("")
def create_pool() -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    name = require_str(data.get("name"), "name", 100)
    _guard_name(conn, project_id, name)
    repo = DataPoolRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "name": name,
        "content": optional_str(data.get("content"), "content", 20000) or "",
        "description": optional_str(data.get("description"), "description", 500) or "",
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, _AUDIT_RESOURCE, new_id, name, {"projectId": project_id})
    return ok(_public(repo.get(new_id) or {}))


@bp.get("/<pk>")
def get_pool(pk: str) -> Response:
    conn = db()
    row = _load(conn, path_int(pk))
    guard_project_scope(row, scope_arg(), "数据池")
    return ok(_public(row))


@bp.put("/<pk>")
def update_pool(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = _load(conn, target)
    guard_project_scope(row, scope_arg(), "数据池")
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        name = require_str(data["name"], "name", 100)
        _guard_name(conn, int(row["project_id"]), name, exclude_id=target)
        old_name = str(row.get("name") or "")
        if name != old_name:
            _guard_references(conn, int(row["project_id"]), old_name, "改名")
        payload["name"] = name
    if "content" in data:
        payload["content"] = optional_str(data["content"], "content", 20000) or ""
    if "description" in data:
        payload["description"] = optional_str(data["description"], "description", 500) or ""
    if not payload:
        raise AppError(ErrorCode.PARAM_ERROR, "没有可更新的字段")
    repo = DataPoolRepository(conn)
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, _AUDIT_RESOURCE, target,
          str(payload.get("name") or row.get("name") or ""), {"fields": sorted(payload.keys())})
    return ok(_public(repo.get(target) or {}))


@bp.delete("/<pk>")
def delete_pool(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = _load(conn, target)
    guard_project_scope(row, scope_arg(), "数据池")
    _guard_references(conn, int(row["project_id"]), str(row.get("name") or ""), "删除")
    DataPoolRepository(conn).soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, _AUDIT_RESOURCE, target, str(row.get("name") or ""))
    return ok({"id": target, "deleted": True})


@bp.post("/<pk>/preview")
def preview_pool(pk: str) -> Response:
    """池内容解析预览；引用方编辑器预览走 /test-cases/preview-test-data。"""
    conn = db()
    row = _load(conn, path_int(pk))
    guard_project_scope(row, scope_arg(), "数据池")
    return ok(case_test_data.preview(str(row.get("content") or "")))
