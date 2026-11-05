"""API 层公共工具：请求体解析、分页、过滤白名单、审计写入。"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable
from urllib.parse import quote

from flask import Response, request

from ..common.pagination import Page
from ..common.validators import parse_page_args, require_int
from ..db.connection import get_db
from ..errors import AppError, ErrorCode
from ..repositories.base import BaseRepository
from ..services import audit_service, tabular_export


def db() -> sqlite3.Connection:
    """请求级连接。"""
    return get_db()


def body() -> dict[str, Any]:
    """解析 JSON 请求体；缺失或非法时返回空字典。"""
    data = request.get_json(silent=True)
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "请求体必须是 JSON 对象")
    return data


def commit(conn: sqlite3.Connection) -> None:
    conn.commit()


def page_args(args: Any = None) -> tuple[int, int]:
    return parse_page_args(args if args is not None else request.args)


def query_filters(allowed: dict[str, str], args: Any = None) -> dict[str, Any]:
    """按白名单把查询串映射为仓储过滤条件。

    allowed 是「查询参数名 → 数据库列名」的显式映射；
    未列出的参数一律忽略，列名不会来自请求内容，杜绝注入面。
    """
    src = args if args is not None else request.args
    filters: dict[str, Any] = {}
    for key, column in allowed.items():
        value = src.get(key)
        if value in (None, ""):
            continue
        filters[column] = value
    return filters


def keyword_of(args: Any = None) -> str:
    src = args if args is not None else request.args
    return str(src.get("keyword") or "").strip()


def int_arg(value: Any, field: str, default: int | None = None) -> int:
    return require_int(value, field, default)


def path_int(pk: Any, field: str = "id") -> int:
    try:
        return int(pk)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是整数") from None


def page_payload(page: Page) -> dict[str, Any]:
    return page.as_dict()


def audit(
    conn: sqlite3.Connection,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    resource_label: str = "",
    detail: dict[str, Any] | None = None,
    result: str = "success",
) -> None:
    audit_service.record(
        conn,
        action,
        resource_type,
        resource_id,
        resource_label,
        detail,
        result=result,
    )
    # 审计写入独立于业务事务，这里单独提交，避免随请求结束被回滚
    conn.commit()


def require_found(row: dict[str, Any] | None, message: str = "资源不存在") -> dict[str, Any]:
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, message)
    return row


def pick(source: dict[str, Any], allowed: Iterable[str]) -> dict[str, Any]:
    """只取白名单字段，避免把请求体里的任意列名写进数据库。"""
    allowed_set = set(allowed)
    return {k: v for k, v in source.items() if k in allowed_set}


def attach_version_codes(
    conn: sqlite3.Connection,
    rows: list[dict[str, Any]],
    field: str = "versionId",
    code_field: str = "versionCode",
) -> list[dict[str, Any]]:
    """给资产列表批量补版本号与版本名称，用于前端列表列展示。一次查询，不逐行打点。"""
    ids = {
        int(row[field])
        for row in rows
        if row.get(field) not in (None, "")
    }
    if not ids:
        return rows
    placeholders = ",".join("?" for _ in ids)
    sql = f"SELECT id, version_code, name FROM biz_project_version WHERE id IN ({placeholders})"
    codes: dict[int, str] = {}
    names: dict[int, str] = {}
    for row in conn.execute(sql, list(ids)):
        codes[int(row["id"])] = str(row["version_code"] or "")
        names[int(row["id"])] = str(row["name"] or "")
    for row in rows:
        raw = row.get(field)
        if raw in (None, ""):
            row[code_field] = ""
            row["versionName"] = ""
            continue
        vid = int(raw)
        row[code_field] = codes.get(vid, "")
        row["versionName"] = names.get(vid, "")
    return rows


def download_response(
    content: bytes,
    filename: str,
    mimetype: str,
    count: int | None = None,
) -> Response:
    """二进制下载响应；文件名只用 UTF-8 编码，不回显请求里的原始名字。"""
    response = Response(content, mimetype=mimetype)
    if mimetype.startswith("text/"):
        response.headers["Content-Type"] = f"{mimetype}; charset=utf-8"
    response.headers["Content-Disposition"] = (
        f"attachment; filename*=UTF-8''{quote(filename, safe='')}"
    )
    if count is not None:
        response.headers["X-AITP-Export-Count"] = str(count)
    return response


def tabular_format_of(raw: Any, default: str = "csv") -> str:
    """导出格式白名单：只接受 csv / json，其余报参数错误。"""
    try:
        return tabular_export.normalize_format(raw or default)
    except ValueError:
        raise AppError(ErrorCode.PARAM_ERROR, "format 只能是 csv / json") from None


def bool_arg(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def ids_of(data: dict[str, Any], field: str = "ids", max_count: int = 500) -> list[int]:
    """从请求体取一批 id，去重并保持提交顺序。"""
    raw = data.get(field)
    if not isinstance(raw, list) or not raw:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是非空数组")
    if len(raw) > max_count:
        raise AppError(ErrorCode.PARAM_ERROR, f"{field} 一次最多 {max_count} 项")
    return list(dict.fromkeys(require_int(item, f"{field}[]") for item in raw))


def batch_soft_delete(
    conn: sqlite3.Connection,
    repo: BaseRepository,
    ids: list[int],
    resource: str,
    label_key: str = "title",
) -> dict[str, Any]:
    """批量软删并记一条审计；已删除或不存在的 id 归入 skipped。"""
    removed: list[int] = []
    labels: list[str] = []
    for item in ids:
        row = repo.get(item)
        if row is None or not repo.soft_delete(item):
            continue
        removed.append(item)
        labels.append(str(row.get(label_key) or item))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, resource, None, "", {
        "count": len(removed),
        "ids": removed,
        "labels": labels[:20],
    })
    return {
        "deleted": removed,
        "count": len(removed),
        "skipped": [item for item in ids if item not in removed],
    }
