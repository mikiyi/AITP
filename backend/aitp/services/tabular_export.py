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
    CsvStreamWriter(buffer, columns).write(rows)
    return buffer.getvalue().encode("utf-8")


def render_json(columns: Sequence[Column], rows: Sequence[dict[str, Any]]) -> bytes:
    buffer = io.StringIO(newline="")
    writer = JsonStreamWriter(buffer, columns, len(rows))
    writer.write(rows)
    writer.close()
    return buffer.getvalue().encode("utf-8")


class CsvStreamWriter:
    """按批落盘的 CSV 写入器：导出万级数据时内存里只留当前这一批。

    与 render_csv 共用同一套单元格规则与 BOM，边写与一次写完的字节完全一致。
    """

    def __init__(self, handle: Any, columns: Sequence[Column]) -> None:
        self._columns = columns
        self._handle = handle
        handle.write(BOM)
        self._writer = csv.writer(handle)
        self._writer.writerow([label for _, label in columns])
        self._count = 0

    def write(self, rows: Sequence[dict[str, Any]]) -> int:
        for row in rows:
            self._writer.writerow([_cell(row.get(key)) for key, _ in self._columns])
        self._count += len(rows)
        return len(rows)

    def close(self) -> int:
        # csv.writer 自己没有 flush：缓冲在底层 handle 上，只落一次盘
        self._handle.flush()
        return self._count

    @property
    def count(self) -> int:
        return self._count


class JsonStreamWriter:
    """按批落盘的 JSON 写入器，输出与 render_json 逐字节一致。

    count 在文件头，所以调用方要先给出总数（导出前先 COUNT 一次，
    超过扫描上限直接失败，不写半截文件）。
    """

    def __init__(self, handle: Any, columns: Sequence[Column], count: int) -> None:
        self._handle = handle
        self._columns = columns
        self._total = int(count)
        self._written = 0
        self._opened = False
        labels = [label for _, label in columns]
        handle.write("{\n")
        handle.write(f'  "exportedAt": {json.dumps(_exported_at())},\n')
        handle.write(f'  "count": {json.dumps(self._total)},\n')
        handle.write(
            f'  "columns": {_indent(json.dumps(labels, ensure_ascii=False, indent=2), 2, pad_first=False)},\n'
        )
        handle.write('  "list": ')

    def write(self, rows: Sequence[dict[str, Any]]) -> int:
        for row in rows:
            if not self._opened:
                self._handle.write("[\n")
                self._opened = True
            elif self._written:
                self._handle.write(",\n")
            item = {key: row.get(key) for key, _ in self._columns}
            self._handle.write(_indent(json.dumps(item, ensure_ascii=False, indent=2), 4))
            self._written += 1
        return len(rows)

    def close(self) -> int:
        if self._opened:
            self._handle.write("\n  ]\n}")
        else:
            self._handle.write("[]\n}")
        return self._written

    @property
    def count(self) -> int:
        return self._written


def _exported_at() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _indent(text: str, spaces: int, pad_first: bool = True) -> str:
    """把 json.dumps(indent=2) 的整段结果按层级缩进，复刻整包 dumps 的排版。

    值紧跟在键名后面时（如 "columns": [...]）首行不能再补空格，用 pad_first=False。
    """
    pad = " " * spaces
    lines = text.split("\n")
    if not pad_first:
        return lines[0] + "".join(f"\n{pad}{line}" for line in lines[1:])
    return "\n".join(pad + line for line in lines)


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
