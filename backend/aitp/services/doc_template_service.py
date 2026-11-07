"""测试文档模板：上传解析与提示词渲染。

支持的上传格式：
- .docx：取正文段落与表格，记录正文与表头的字体/字号；
- .xlsx / .csv：首行当表头，记录表头字体/字号（csv 无字体信息）；
- .md / .txt / .json：按纯文本读入。

模板正文会被渲染进提示词的 outputTemplate 变量，模型据此对齐
文档风格、格式与字号；导出 Excel 时用 columns 决定列顺序。
"""

from __future__ import annotations

import csv
import io
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from ..common.secret_store import atomic_write_bytes
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
        content, columns, style = _parse_archive(_parse_docx, data, "Word", "docx")
    elif suffix == ".xlsx":
        content, columns, style = _parse_archive(_parse_xlsx, data, "Excel", "xlsx")
    elif suffix == ".csv":
        text = _decode(data).strip()
        content, columns, style = text, _columns_from_csv(text), ""
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


def _parse_archive(
    parser: Callable[[bytes], tuple[str, list[str], str]],
    data: bytes,
    label: str,
    ext: str,
) -> tuple[str, list[str], str]:
    """docx/xlsx 本质是压缩包，损坏时解析器会抛 zip/xml 底层异常，统一转成参数错误。"""
    try:
        return parser(data)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - 底层异常类型多，都说明文件本身不可读
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{label} 模板已损坏或不是有效的 {ext} 文件，请重新导出后再上传",
        ) from exc


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


def _columns_from_csv(text: str) -> list[str]:
    """CSV 模板：第一个非空行当表头，逗号 / 分号 / 制表符都识别。"""
    first_line = next((line for line in text.splitlines() if line.strip()), "")
    try:
        delimiter = csv.Sniffer().sniff(first_line, delimiters=",;\t").delimiter
    except csv.Error:
        delimiter = next((item for item in (",", ";", "\t") if item in first_line), ",")
    try:
        rows = csv.reader(io.StringIO(text), delimiter=delimiter)
        for row in rows:
            cells = [cell.strip() for cell in row if cell.strip()]
            if cells:
                return cells[:MAX_COLUMNS]
    except csv.Error:
        return []
    return []


def _collect_style(fonts: list[str], sizes: list[str], runs: Iterable[Any]) -> None:
    """把文字里的字体名与字号收进去重列表，正文段落与表格单元格通用。"""
    for run in runs:
        name = (run.font.name or "").strip()
        size = _fmt_size(run.font.size.pt if run.font.size else None)
        if name and name not in fonts:
            fonts.append(name)
        if size and size not in sizes:
            sizes.append(size)


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

    fonts: list[str] = []
    sizes: list[str] = []
    columns: list[str] = []
    for table in document.tables:
        header_collected = False
        for row_index, row in enumerate(table.rows):
            cells = [(cell.text or "").strip() for cell in row.cells]
            if not any(cells):
                continue
            if row_index == 0 and not columns:
                columns = [cell for cell in cells if cell][:MAX_COLUMNS]
            if not header_collected:
                header_collected = True
                for cell in row.cells:
                    for paragraph in cell.paragraphs:
                        _collect_style(fonts, sizes, paragraph.runs)
            lines.append("| " + " | ".join(cells) + " |")

    for paragraph in document.paragraphs:
        _collect_style(fonts, sizes, paragraph.runs)
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


# ---------------------------------------------------------------------
# 原始模板文件落盘：解析完还留一份，用户才能把模板再下载回去
# ---------------------------------------------------------------------

#: 落盘文件名：<uuid32><后缀>，读取时按这个格式校验，请求内容碰不到路径
_STORED_NAME_RE = re.compile(r"\A[0-9a-f]{32}(\.[a-z0-9]{1,8})?\Z")

#: 下载时给浏览器的 MIME，缺项一律按二进制流处理
_MIME_BY_SUFFIX = {
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".txt": "text/plain",
    ".json": "application/json",
}


def save_template_file(root: Path, file_name: str, data: bytes) -> str:
    """原子落盘原始模板，返回 stored_name。"""
    suffix = Path(file_name).suffix.lower()[:9]
    stored = f"{uuid.uuid4().hex}{suffix}"
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_bytes(root / stored, data, mode=0o600)
    return stored


def template_file_path(root: Path, stored_name: str) -> Path | None:
    """按 stored_name 定位落盘文件；名字非法或文件不在时返回 None。"""
    if not is_valid_stored_name(stored_name):
        return None
    path = root / stored_name
    return path if path.is_file() else None


def is_valid_stored_name(stored_name: str) -> bool:
    return bool(_STORED_NAME_RE.fullmatch(stored_name or ""))


def delete_template_file(root: Path, stored_name: str) -> None:
    """换文件或删模板时清掉旧文件，不让本机堆积没人用的模板。"""
    path = template_file_path(root, stored_name)
    if path is None:
        return
    try:
        path.unlink()
    except OSError:
        pass


def cleanup_unreferenced_files(
    root: Path, stored_names: Iterable[str], referenced: set[str]
) -> list[str]:
    """删除取消保存后没人引用的上传原件；仍被模板占用的文件一律跳过。"""
    removed: list[str] = []
    for name in set(stored_names):
        if name in referenced:
            continue
        path = template_file_path(root, name)
        if path is None:
            continue
        try:
            path.unlink()
        except OSError:
            continue
        removed.append(name)
    return removed


def mimetype_of(file_name: str) -> str:
    return _MIME_BY_SUFFIX.get(Path(file_name).suffix.lower(), "application/octet-stream")
