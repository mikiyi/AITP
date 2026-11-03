"""分页工具。"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 20

# 仓储层统一行字典类型：SQLite 行统一经 dict(row) 转换，键为列名、值为任意标量
RowDict = dict[str, Any]
RowList = list[RowDict]


@dataclass(slots=True)
class Page:
    list: list[RowDict] = field(default_factory=list)
    total: int = 0
    pageNum: int = 1
    pageSize: int = DEFAULT_PAGE_SIZE

    @property
    def pages(self) -> int:
        if not self.pageSize:
            return 0
        return (self.total + self.pageSize - 1) // self.pageSize

    def as_dict(self) -> dict[str, Any]:
        return {
            "list": self.list,
            "pageNum": self.pageNum,
            "pageSize": self.pageSize,
            "total": self.total,
            "pages": self.pages,
        }


def build_page(
    rows: list[sqlite3.Row] | RowList,
    total: int,
    page_num: int,
    page_size: int,
) -> Page:
    return Page(
        list=[dict(r) for r in rows],
        total=int(total),
        pageNum=page_num,
        pageSize=page_size,
    )


def clamp_page_size(value: Any, default: int = DEFAULT_PAGE_SIZE) -> int:
    try:
        size = int(value)
    except (TypeError, ValueError):
        return default
    if size <= 0:
        raise ValueError("pageSize 必须大于 0")
    return min(size, MAX_PAGE_SIZE)


def clamp_page_num(value: Any, default: int = 1) -> int:
    try:
        num = int(value)
    except (TypeError, ValueError):
        return default
    return max(1, num)
