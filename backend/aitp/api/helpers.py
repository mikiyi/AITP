"""API 层公共工具：请求体解析、分页、过滤白名单、审计写入。"""

from __future__ import annotations

import sqlite3
from typing import Any, Iterable

from flask import request

from ..common.pagination import Page
from ..common.validators import parse_page_args, require_int
from ..db.connection import get_db
from ..errors import AppError, ErrorCode
from ..services import audit_service


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


def bool_arg(value: Any, default: bool = False) -> bool:
    if value in (None, ""):
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}
