"""通用表格导出：CSV 与 JSON。

CSV 带 UTF-8 BOM，Excel 双击打开中文不乱码；嵌套字段序列化成 JSON 文本，
保证一列一格，不会把表格结构打散。
"""

from __future__ import annotations

import csv
import io
import json
from datetime import datetime
from typing import Any, Sequence

CSV_FORMAT = "csv"
JSON_FORMAT = "json"
TABULAR_FORMATS = (CSV_FORMAT, JSON_FORMAT)

BOM = "﻿"
# 单元格上限按常见 Excel 限制留余量，超长内容截断而不是让整表打不开
MAX_CELL_CHARS = 2000

Column = tuple[str, str]


def _cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text[:MAX_CELL_CHARS]


def render_csv(columns: Sequence[Column], rows: Sequence[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in columns])
    for row in rows:
        writer.writerow([_cell(row.get(key)) for key, _ in columns])
    return (BOM + buffer.getvalue()).encode("utf-8")


def render_json(columns: Sequence[Column], rows: Sequence[dict[str, Any]]) -> bytes:
    labels = [label for _, label in columns]
    payload = {
        "exportedAt": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "count": len(rows),
        "columns": labels,
        "list": [{key: row.get(key) for key, _ in columns} for row in rows],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")


def render(fmt: str, columns: Sequence[Column], rows: Sequence[dict[str, Any]]) -> tuple[bytes, str, str]:
    """返回 (内容, mimetype, 扩展名)；fmt 只接受 csv / json。"""
    if fmt == CSV_FORMAT:
        return render_csv(columns, rows), "text/csv", "csv"
    return render_json(columns, rows), "application/json", "json"


def normalize_format(raw: Any) -> str:
    fmt = str(raw or CSV_FORMAT).strip().lower()
    if fmt not in TABULAR_FORMATS:
        raise ValueError(fmt)
    return fmt


def safe_filename(stem: str, extension: str) -> str:
    """替换 Windows 非法字符，避免用请求内容拼文件名。"""
    safe = "".join("-" if ch in '\\/:*?"<>|' else ch for ch in stem).strip()
    return f"{safe or 'AITP'}.{extension}"
