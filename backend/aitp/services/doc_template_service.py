"""测试文档模板：上传解析与提示词渲染。

支持的上传格式：
- .docx：取正文段落与表格，记录字体/字号；
- .xlsx / .csv：取首个工作表的前若干行，首行当表头，记录表头字体/字号；
- .md / .txt / .json：按纯文本读入。

模板正文会被渲染进提示词的 outputTemplate 变量，模型据此对齐
文档风格、格式与字号；导出 Excel 时用 columns 决定列顺序。
"""

from __future__ import annotations

import io
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.doc_template_repo import (
    CAPABILITY_KIND,
    MAX_CONTENT,
    MAX_STYLE_HINT,
    DocTemplateRepository,
    decorate,
)

#: 允许上传的模板格式
ALLOWED_EXTENSIONS = (".docx", ".xlsx", ".csv", ".md", ".markdown", ".txt", ".json")
#: 上传大小上限（字节）
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
#: 表格最多取多少行，防止把整本台账塞进提示词
MAX_TABLE_ROWS = 80
#: 提取出的列名上限，和仓储保持一致
MAX_COLUMNS = 20


@dataclass(slots=True)
class ParsedTemplate:
    file_name: str
    file_type: str
    file_size: int
    content: str
    columns: list[str] = field(default_factory=list)
    style_hint: str = ""


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _fmt_size(value: Any) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return ""
    if number <= 0:
        return ""
    return f"{number:g}pt"


def parse_template_file(data: bytes, filename: str) -> ParsedTemplate:
    """把上传的模板文件解析成正文、列名与版式说明。"""
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"模板只支持 {'、'.join(item.lstrip('.') for item in ALLOWED_EXTENSIONS)}",
        )
    if not data:
        raise AppError(ErrorCode.PARAM_ERROR, "模板文件为空")
    if len(data) > MAX_UPLOAD_BYTES:
        raise AppError(ErrorCode.PARAM_ERROR, "模板文件不能超过 5MB")

    if suffix == ".docx":
        content, columns, style = _parse_docx(data)
    elif suffix == ".xlsx":
        content, columns, style = _parse_xlsx(data)
    else:
        text = _decode(data).strip()
        content, columns, style = text, _columns_from_markdown(text), ""

    content = content.strip()[:MAX_CONTENT]
    if not content:
        raise AppError(ErrorCode.PARAM_ERROR, "模板文件里没有可用的文字内容")
    return ParsedTemplate(
        file_name=Path(filename).name[:120],
        file_type=suffix.lstrip("."),
        file_size=len(data),
        content=content,
        columns=columns,
        style_hint=style[:MAX_STYLE_HINT],
    )


def _columns_from_markdown(text: str) -> list[str]:
    """Markdown 模板的第一张表：首行当表头，下一行是 --- 分隔行。"""
    lines = [line.strip() for line in text.splitlines()]
    for index, line in enumerate(lines[:-1]):
        if not line.startswith("|"):
            continue
        next_line = lines[index + 1]
        if not next_line.startswith("|"):
            continue
        cells = [cell.strip() for cell in next_line.strip("|").split("|")]
        if not all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        columns = [
            cell.strip() for cell in line.strip("|").split("|") if cell.strip()
        ]
        return columns[:MAX_COLUMNS]
    return []


def _parse_docx(data: bytes) -> tuple[str, list[str], str]:
    try:
        from docx import Document
    except ImportError:
        raise AppError(ErrorCode.PARAM_ERROR, "本机缺少 python-docx，无法解析 Word 模板") from None
    document = Document(io.BytesIO(data))

    lines: list[str] = []
    for paragraph in document.paragraphs:
        text = (paragraph.text or "").strip()
        if text:
            lines.append(text)

    columns: list[str] = []
    for table in document.tables:
        for row_index, row in enumerate(table.rows):
            cells = [(cell.text or "").strip() for cell in row.cells]
            if not any(cells):
                continue
            if row_index == 0 and not columns:
                columns = [cell for cell in cells if cell][:MAX_COLUMNS]
            lines.append("| " + " | ".join(cells) + " |")

    fonts: list[str] = []
    sizes: list[str] = []
    for paragraph in document.paragraphs:
        for run in paragraph.runs:
            name = (run.font.name or "").strip()
            size = _fmt_size(run.font.size.pt if run.font.size else None)
            if name and name not in fonts:
                fonts.append(name)
            if size and size not in sizes:
                sizes.append(size)
    hint = _style_hint(fonts, sizes)
    return "\n".join(lines), columns, hint


def _parse_xlsx(data: bytes) -> tuple[str, list[str], str]:
    try:
        from openpyxl import load_workbook
    except ImportError:
        raise AppError(ErrorCode.PARAM_ERROR, "本机缺少 openpyxl，无法解析 Excel 模板") from None
    workbook = load_workbook(io.BytesIO(data), read_only=False, data_only=True)
    sheet = workbook.worksheets[0] if workbook.worksheets else None
    if sheet is None:
        raise AppError(ErrorCode.PARAM_ERROR, "Excel 模板里没有工作表")

    columns: list[str] = []
    lines: list[str] = []
    fonts: list[str] = []
    sizes: list[str] = []
    for row_index, row in enumerate(sheet.iter_rows(min_row=1, max_row=MAX_TABLE_ROWS), start=1):
        cells = [str(cell.value).strip() if cell.value is not None else "" for cell in row]
        if not any(cells):
            continue
        if row_index == 1:
            columns = [cell for cell in cells if cell][:MAX_COLUMNS]
            for cell in row[: len(columns)]:
                name = (cell.font.name or "").strip() if cell.font else ""
                size = _fmt_size(cell.font.sz if cell.font else None)
                if name and name not in fonts:
                    fonts.append(name)
                if size and size not in sizes:
                    sizes.append(size)
        while cells and not cells[-1]:
            cells.pop()
        lines.append("| " + " | ".join(cells) + " |")

    hint = _style_hint(fonts, sizes, sheet=sheet.title)
    return "\n".join(lines), columns, hint


def _style_hint(fonts: list[str], sizes: list[str], *, sheet: str = "") -> str:
    parts: list[str] = []
    if sheet:
        parts.append(f"工作表「{sheet}」")
    if fonts:
        parts.append("字体：" + "、".join(fonts[:3]))
    if sizes:
        parts.append("字号：" + "、".join(sizes[:3]))
    return "；".join(parts)


def render_for_prompt(template: dict[str, Any]) -> str:
    """模板 → 提示词里的 outputTemplate 文本。"""
    name = str(template.get("name") or "产出物模板")
    content = str(template.get("content") or "").strip()
    style = str(template.get("styleHint") or "").strip()
    file_name = str(template.get("fileName") or "").strip()
    head = f"【产出物模板：{name}】"
    if file_name:
        head += f"（来源文件：{file_name}）"
    text = f"{head}\n{content}"
    if style:
        text += f"\n【版式要求】{style}；正文与表格按该模板的字体、字号与排版风格输出。"
    return text[: MAX_CONTENT + 800]


def resolve_for_run(conn: Any, template_id: int, capability: str) -> str:
    """执行前校验模板类别与能力匹配，并渲染成提示词变量。"""
    expected = CAPABILITY_KIND.get(capability)
    if expected is None:
        raise AppError(ErrorCode.PARAM_ERROR, "该能力不支持产出物模板")
    row = DocTemplateRepository(conn).get(int(template_id))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "产出物模板不存在")
    template = decorate(row)
    if template.get("kind") != expected:
        raise AppError(ErrorCode.PARAM_ERROR, "模板类别与当前能力不匹配")
    return render_for_prompt(template)
