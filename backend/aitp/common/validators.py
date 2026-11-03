"""请求参数校验与白名单。"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from ..errors import AppError, ErrorCode

SORT_ORDERS = {"asc", "desc"}


def require_int(value: Any, field: str, default: int | None = None) -> int:
    if value in (None, ""):
        if default is None:
            raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 不能为空")
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是整数") from None


def require_str(value: Any, field: str, max_length: int = 2000) -> str:
    if value is None:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 不能为空")
    text = str(value).strip()
    if not text:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 不能为空")
    if len(text) > max_length:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 长度不能超过 {max_length}")
    return text


def optional_str(value: Any, field: str, max_length: int = 2000) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if len(text) > max_length:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 长度不能超过 {max_length}")
    return text


def parse_page_args(args: Mapping[str, Any]) -> tuple[int, int]:
    """从请求参数解析 pageNum / pageSize。"""
    from .pagination import clamp_page_num, clamp_page_size

    page_num = clamp_page_num(args.get("pageNum"), 1)
    try:
        page_size = clamp_page_size(args.get("pageSize"), 20)
    except ValueError:
        raise AppError(ErrorCode.PAGE_PARAM_INVALID, "pageSize 必须是大于 0 的整数") from None
    return page_num, page_size


def parse_order(order: str | None, default: str = "desc") -> str:
    value = (order or default).lower()
    return value if value in SORT_ORDERS else default


def check_choice(value: str | None, allowed: Iterable[str], field: str, default: str | None = None) -> str | None:
    if value in (None, ""):
        return default
    text = str(value).strip()
    allowed_set = set(allowed)
    if text not in allowed_set:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"参数 {field} 取值非法，可选值：{', '.join(sorted(allowed_set))}",
        )
    return text


def check_choices(value: str | None, allowed: Iterable[str], field: str) -> list[str]:
    if value in (None, ""):
        return []
    items = [v.strip() for v in str(value).split(",") if v.strip()]
    allowed_set = set(allowed)
    for item in items:
        if item not in allowed_set:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"参数 {field} 取值非法：{item}，可选值：{', '.join(sorted(allowed_set))}",
            )
    return items
