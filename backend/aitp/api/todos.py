"""任务清单 API：清单 CRUD、勾选完成、批量维护、统计与导出。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import check_choice
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.todo_repo import TODO_VIEWS, TodoRepository, decorate
from ..services import audit_service, tabular_export, todo_service
from .helpers import (
    attach_version_codes,
    audit,
    body,
    commit,
    db,
    download_response,
    guard_project_scope,
    keyword_of,
    page_args,
    path_int,
    query_filters,
    scope_arg,
    tabular_format_of,
)

bp = Blueprint("todos", __name__)

#: 查询参数名 → 数据库列名，白名单外的一律忽略
FILTERS = {
    "projectId": "project_id",
    "moduleId": "module_id",
    "versionId": "version_id",
    "status": "status",
    "priority": "priority",
    "category": "category",
}

EXPORT_COLUMNS = (
    ("todoNo", "编号"),
    ("title", "任务"),
    ("status", "状态"),
    ("priority", "优先级"),
    ("category", "分类"),
    ("dueDate", "到期日"),
    ("isOverdue", "是否逾期"),
    ("progress", "进度"),
    ("tags", "标签"),
    ("description", "说明"),
    ("completedAt", "完成时间"),
    ("createdAt", "创建时间"),
)


def _project_scope(filters: dict[str, Any]) -> int | None:
    raw = filters.get("project_id")
    if raw in (None, ""):
        return None
    try:
        return int(raw)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 projectId 必须是整数") from None


@bp.get("")
def list_todos() -> Response:
    """按快捷视图取顶层任务，每条自带子任务；统计一次带回，省一次请求。"""
    conn = db()
    page_num, page_size = page_args()
    view = check_choice(str(request.args.get("view") or "all"), TODO_VIEWS, "view", "all") or "all"
    filters = query_filters(FILTERS)
    items, total = todo_service.list_todos(
        conn,
        view=view,
        filters=filters,
        keyword=keyword_of() or None,
        page_num=page_num,
        page_size=page_size,
    )
    attach_version_codes(conn, items)
    return ok({
        "list": items,
        "pageNum": page_num,
        "pageSize": page_size,
        "total": total,
        "pages": max(1, -(-total // page_size)),
        "view": view,
        "stats": TodoRepository(conn).stats(_project_scope(filters)),
    })


@bp.get("/stats")
def stats() -> Response:
    conn = db()
    repo = TodoRepository(conn)
    scope = _project_scope(query_filters(FILTERS))
    return ok({
        "summary": repo.stats(scope),
        "byStatus": repo.status_stats(scope),
        "categories": repo.categories(scope),
    })


@bp.get("/categories")
def categories() -> Response:
    conn = db()
    scope = _project_scope(query_filters(FILTERS))
    return ok({"list": TodoRepository(conn).categories(scope)})


@bp.get("/export")
def export_todos() -> Response:
    conn = db()
    fmt = tabular_format_of(request.args.get("format"))
    view = check_choice(str(request.args.get("view") or "all"), TODO_VIEWS, "view", "all") or "all"
    rows = todo_service.export_rows(
        conn, query_filters(FILTERS), view=view, keyword=keyword_of() or None
    )
    content, mimetype, extension = tabular_export.render(fmt, EXPORT_COLUMNS, rows)
    filename = tabular_export.safe_filename(f"AITP任务清单-{len(rows)}条", extension)
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_TODO, None, "", {
        "format": fmt,
        "count": len(rows),
    })
    return download_response(content, filename, mimetype, len(rows))


@bp.get("/<pk>")
def get_todo(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = TodoRepository(conn)
    row = repo.get_with_children(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    guard_project_scope(row, scope_arg(), "待办")
    children = row.pop("children") or []
    payload = decorate(conn, row)
    payload["children"] = [decorate(conn, child) for child in children]
    attach_version_codes(conn, [payload] + list(payload["children"]))
    return ok(payload)


@bp.post("")
def create_todo() -> Response:
    conn = db()
    data = body()
    item = todo_service.create_todo(conn, data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TODO,
          int(item["id"]), str(item.get("title") or ""), {"todoNo": item.get("todoNo")})
    return ok(item)


@bp.put("/<pk>")
def update_todo(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    data = body()
    item = todo_service.update_todo(conn, target, data)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TODO,
          target, str(item.get("title") or ""), {"fields": sorted(data.keys())})
    return ok(item)


@bp.post("/<pk>/toggle")
def toggle_todo(pk: str) -> Response:
    """勾选框用的接口：只翻完成状态，不碰其它字段。"""
    conn = db()
    target = path_int(pk)
    item = todo_service.toggle_done(conn, target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TODO,
          target, str(item.get("title") or ""), {"status": item.get("status")})
    return ok(item)


@bp.post("/<pk>/move")
def move_todo(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    direction = str(body().get("direction") or "").strip()
    result = todo_service.move(conn, target, direction)
    commit(conn)
    return ok(result)


@bp.post("/batch-update")
def batch_update() -> Response:
    conn = db()
    data = body()
    ids = _ids(data)
    fields = {key: value for key, value in data.items() if key != "ids"}
    result = todo_service.batch_update(conn, ids, fields)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TODO, None, "", {
        "count": result.get("count"),
        "fields": sorted(fields.keys()),
    })
    return ok(result)


@bp.post("/batch-delete")
def batch_delete() -> Response:
    conn = db()
    ids = _ids(body())
    repo = TodoRepository(conn)
    removed: list[int] = []
    for item in ids:
        if repo.get(item) is None:
            # 一个失效 id 不该让整批失败，与缺陷/需求的批量删除保持同一套 skipped 语义
            continue
        result = todo_service.remove_todo(conn, item)
        removed.extend(int(x) for x in result.get("removed") or [])
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TODO, None, "", {
        "requested": len(ids),
        "count": len(removed),
    })
    return ok({
        "deleted": removed,
        "count": len(removed),
        "requested": len(ids),
        "skipped": [item for item in ids if item not in removed],
    })


@bp.post("/reorder")
def reorder() -> Response:
    conn = db()
    ids = _ids(body())
    result = todo_service.reorder(conn, ids)
    commit(conn)
    return ok(result)


@bp.delete("/<pk>")
def delete_todo(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = TodoRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    result = todo_service.remove_todo(conn, target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TODO,
          target, str(row.get("title") or ""), {"count": result.get("count")})
    return ok({"id": target, "deleted": True, "removed": result.get("removed")})


def _ids(data: dict[str, Any]) -> list[int]:
    raw = data.get("ids")
    if not isinstance(raw, list) or not raw:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 ids 必须是非空数组")
    if len(raw) > 200:
        raise AppError(ErrorCode.PARAM_ERROR, "ids 一次最多 200 项")
    return list(dict.fromkeys(path_int(item, "ids[]") for item in raw))
