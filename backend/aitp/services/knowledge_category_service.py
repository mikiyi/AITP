"""知识库分类校验。

文章、待审池、版本三处都存分类名，写入前统一走这里：
留空落到默认分类，给了名字必须是分类表里已有的（自定义分类要先新增）。
"""

from __future__ import annotations

import sqlite3

from ..errors import AppError, ErrorCode
from ..repositories.knowledge_category_repo import (
    DEFAULT_CATEGORY,
    SYSTEM_CATEGORIES,
    KnowledgeCategoryRepository,
)


def ensure_category(conn: sqlite3.Connection, value: object) -> str:
    """校验并规范化分类名；空值返回默认分类。"""
    name = str(value or "").strip()[:40]
    if not name:
        return DEFAULT_CATEGORY
    if KnowledgeCategoryRepository(conn).find_by_name(name) is None:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"知识分类「{name}」不存在，请先在「分类管理」里新增",
        )
    return name


def is_system(name: str) -> bool:
    return name in SYSTEM_CATEGORIES
