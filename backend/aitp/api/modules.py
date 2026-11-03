"""四级模块树 API：项目 → 模块 → 子模块 → 功能。"""

from __future__ import annotations

import sqlite3
from typing import Any

from flask import Blueprint, Response, request

from ..ai.capability_codes import BINDABLE_CAPABILITIES, label_of
from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.module_repo import (
    NODE_TYPES,
    ModuleCapabilityRepository,
    ModuleRepository,
)
from ..repositories.project_repo import ProjectRepository
from ..services import audit_service
from .helpers import audit, body, commit, db

bp = Blueprint("modules", __name__)


def _to_public(node: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": node.get("id"),
        "projectId": node.get("project_id"),
        "parentId": node.get("parent_id"),
        "nodeType": node.get("node_type"),
        "name": node.get("name"),
        "code": node.get("code"),
        "description": node.get("description"),
        "sortOrder": node.get("sort_order"),
        "status": node.get("status"),
        "depth": node.get("depth"),
        "path": node.get("path"),
        "references": node.get("references"),
        "children": node.get("children", []),
        "createdAt": node.get("created_at"),
        "updatedAt": node.get("updated_at"),
    }


def _project_name(conn: sqlite3.Connection, project_id: int) -> str:
    row = ProjectRepository(conn).get(project_id)
    return str(row.get("name", "") or "") if row else ""


@bp.get("/tree")
def module_tree() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    if not ProjectRepository(conn).get(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    tree = ModuleRepository(conn).tree(project_id, _project_name(conn, project_id))
    return ok({"projectId": project_id, "tree": [_to_public(node) for node in tree]})


@bp.get("/options")
def module_options() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    leaf_only = request.args.get("all", "").strip().lower() not in {"1", "true", "yes"}
    options = ModuleRepository(conn).feature_options(project_id, leaf_only=leaf_only)
    return ok([
        {
            "id": item["id"],
            "name": item["name"],
            "nodeType": item["nodeType"],
            "projectId": item["projectId"],
            "path": item["path"],
        }
        for item in options
    ])


@bp.get("/<pk>")
def get_module(pk: str) -> Response:
    conn = db()
    repo = ModuleRepository(conn)
    target = int(pk)
    node = repo.get_node(target)
    if not node:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模块节点不存在")
    chain = repo.path_of(target)
    # 与 tree() 保持同一套层级口径：模块=1、子模块=2、功能=3
    node["depth"] = max(1, len(chain))
    node["path"] = repo.path_text(target, _project_name(conn, node["project_id"]))
    node["references"] = repo.reference_counts(target)
    return ok(_to_public(node))


@bp.post("")
def create_module() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    name = require_str(data.get("name"), "name", 200)
    node_type = check_choice(data.get("nodeType"), NODE_TYPES, "nodeType", "module") or "module"
    parent_id = data.get("parentId")
    if parent_id in (None, ""):
        parent_id = None
    else:
        parent_id = require_int(parent_id, "parentId")
    if not ProjectRepository(conn).get(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")

    repo = ModuleRepository(conn)
    new_id = repo.create_node(
        project_id,
        name,
        node_type=node_type,
        parent_id=parent_id,
        description=optional_str(data.get("description"), "description", 2000),
        code=optional_str(data.get("code"), "code", 64),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_MODULE, new_id, name,
          {"nodeType": node_type, "parentId": parent_id})
    return ok(_to_public(repo.get_node(new_id) or {}))


@bp.put("/<pk>")
def update_module(pk: str) -> Response:
    conn = db()
    repo = ModuleRepository(conn)
    target = int(pk)
    node = repo.get_node(target)
    if not node:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模块节点不存在")
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 200)
    if "nodeType" in data:
        payload["node_type"] = check_choice(data["nodeType"], NODE_TYPES, "nodeType", node["node_type"])
    if "description" in data:
        payload["description"] = optional_str(data["description"], "description", 2000)
    if "code" in data:
        payload["code"] = optional_str(data["code"], "code", 64)
    if "sortOrder" in data and data["sortOrder"] not in (None, ""):
        payload["sort_order"] = require_int(data["sortOrder"], "sortOrder")
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_MODULE, target,
          str(payload.get("name", node.get("name", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(_to_public(repo.get_node(target) or {}))


@bp.post("/<pk>/move")
def move_module(pk: str) -> Response:
    conn = db()
    repo = ModuleRepository(conn)
    target = int(pk)
    if not repo.get_node(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模块节点不存在")
    data = body()
    raw_parent = data.get("parentId")
    new_parent = None if raw_parent in (None, "") else require_int(raw_parent, "parentId")
    repo.move_node(target, new_parent)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_MODULE, target,
          "", {"movedTo": new_parent})
    node = repo.get_node(target) or {}
    node["path"] = repo.path_text(target, _project_name(conn, node.get("project_id", 0)))
    return ok(_to_public(node))


@bp.delete("/<pk>")
def delete_module(pk: str) -> Response:
    conn = db()
    repo = ModuleRepository(conn)
    target = int(pk)
    node = repo.get_node(target)
    if not node:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模块节点不存在")
    repo.delete_node(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_MODULE, target,
          node.get("name", ""))
    return ok({"id": target, "deleted": True})


@bp.get("/<pk>/capabilities")
def get_capabilities(pk: str) -> Response:
    conn = db()
    repo = ModuleRepository(conn)
    target = int(pk)
    if not repo.get_node(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模块节点不存在")
    bindings = ModuleCapabilityRepository(conn).list_by_module(target)
    return ok({
        "moduleId": target,
        "capabilities": [
            {
                "capability": item["capability"],
                "label": label_of(item["capability"]),
                "promptCode": item.get("prompt_code"),
                "enabled": bool(item.get("enabled")),
                "config": item.get("config") or {},
            }
            for item in bindings
        ],
    })


@bp.put("/<pk>/capabilities")
def set_capabilities(pk: str) -> Response:
    conn = db()
    module_repo = ModuleRepository(conn)
    target = int(pk)
    if not module_repo.get_node(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模块节点不存在")

    data = body()
    items = data.get("capabilities")
    if not isinstance(items, list):
        raise AppError(ErrorCode.PARAM_ERROR, "capabilities 必须是数组")

    cap_repo = ModuleCapabilityRepository(conn)
    for item in items:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "capabilities 元素必须是对象")
        capability = require_str(item.get("capability"), "capability", 64)
        if capability not in BINDABLE_CAPABILITIES:
            raise AppError(ErrorCode.PARAM_ERROR, f"未知能力：{capability}")
        prompt_code = optional_str(item.get("promptCode"), "promptCode", 120) or None
        config = item.get("config")
        if config is not None and not isinstance(config, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "config 必须是对象")
        cap_repo.set_binding(
            target,
            capability,
            enabled=bool(item.get("enabled", True)),
            prompt_code=prompt_code,
            config=config,
        )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_MODULE_CAPABILITY,
          target, "", {"count": len(items)})
    return ok({"moduleId": target, "updated": len(items)})
