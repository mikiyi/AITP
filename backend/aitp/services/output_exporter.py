"""能力产出转 Excel / Word。

导出规则对两种格式一致：
- 用例生成 / XMind 转用例：产出是 JSON 用例数组 → 单表「测试用例」；
- 接口测试设计 / 报告生成：产出是 Markdown → 每个 `##` 小节一节，表格原样落格。

解析不出 JSON 时按 Markdown 兜底，再不行把原文整段落表，保证任何产出都能导出。
列顺序两套来源：模板里的 columns（用户可调）优先，缺的列补在末尾；
没有模板就用默认列。列名对不上已知字段时留空，不报错。
"""

from __future__ import annotations

import json
import re
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill

from ..ai import capability_codes
from ..errors import AppError, ErrorCode

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"

#: 导出能力以 `ai.capability_codes` 为唯一口径：能力卡片上的「导出 Excel / Word」按钮
#: 按那里的集合渲染，这里再抄一份字符串就会漂移成「卡片宣称支持、后端报暂不支持」。
EXPORT_CASE_CAPABILITIES = (capability_codes.CAP_CASE_GENERATION,
                            capability_codes.CAP_XMIND_TO_CASE)
EXPORTABLE_CAPABILITIES = capability_codes.EXPORT_EXCEL_CAPABILITIES
EXPORT_WORD_CAPABILITIES = capability_codes.EXPORT_WORD_CAPABILITIES
#: 导出格式白名单
EXPORT_FORMATS = ("xlsx", "docx")

#: 产出原文上限，防止把超大文本塞进导出
MAX_OUTPUT_CHARS = 200_000

DEFAULT_CASE_COLUMNS = (
    "用例编号", "用例标题", "用例类型", "优先级", "所属模块",
    "前置条件", "测试步骤", "预期结果", "标签",
)

HEADER_FILL = PatternFill(start_color="DAEEF3", end_color="DAEEF3", fill_type="solid")
P0_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
P1_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
HEADER_FONT = Font(bold=True)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)
WRAP = Alignment(vertical="top", wrap_text=True)

MAX_COLUMN_WIDTH = 40
MAX_SHEET_TITLE = 31

CASE_TYPE_LABEL = {
    "functional": "功能测试",
    "interface": "接口测试",
    "boundary": "边界测试",
    "security": "安全测试",
    "ui": "UI测试",
    "exception": "异常测试",
}

_FENCE_RE = re.compile(r"^```[a-zA-Z]*\s*|\s*```$", re.MULTILINE)


def export_output(
    capability: str,
    output: str,
    *,
    columns: list[str] | None = None,
    sheet_name: str = "",
) -> tuple[bytes, int]:
    """产出原文 → (xlsx 字节, 导出行数)。"""
    if capability not in EXPORTABLE_CAPABILITIES:
        raise AppError(ErrorCode.PARAM_ERROR, "该能力暂不支持导出 Excel")
    text = (output or "").strip()
    if not text:
        raise AppError(ErrorCode.PARAM_ERROR, "暂无产出，无法导出")
    if len(text) > MAX_OUTPUT_CHARS:
        raise AppError(ErrorCode.PARAM_ERROR, f"产出超过 {MAX_OUTPUT_CHARS} 字，无法导出")

    if capability in EXPORT_CASE_CAPABILITIES:
        # 模型可能按模板输出 Markdown 表格或直接写说明文字，
        # 解析不出 JSON 数组不代表没有产出，落到下面的 Markdown/纯文本导出。
        items = _try_parse_case_json(text)
        if items:
            headers = _merge_columns(columns, DEFAULT_CASE_COLUMNS)
            rows = [_case_row(item, headers) for item in items]
            content = _build_case_workbook(headers, rows, sheet_name or "测试用例")
            return content, len(rows)

    sections = _parse_markdown(text)
    content, count = _build_markdown_workbook(sections)
    return content, count


# ---------------- JSON 用例 ----------------


def _strip_fences(text: str) -> str:
    return _FENCE_RE.sub("", text).strip()


def _try_parse_case_json(text: str) -> list[dict[str, Any]]:
    """从产出里取出用例数组；拿不到就返回空列表，交给 Markdown 导出兜底。"""
    raw = _strip_fences(text)
    start = raw.find("[")
    end = raw.rfind("]")
    if start >= 0 and end > start:
        raw = raw[start : end + 1]
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        return []
    if isinstance(parsed, dict):
        for key in ("cases", "list", "data"):
            if isinstance(parsed.get(key), list):
                parsed = parsed[key]
                break
    if not isinstance(parsed, list):
        return []
    return [
        item for item in parsed
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ]


def _merge_columns(columns: list[str] | None, defaults: tuple[str, ...]) -> list[str]:
    """模板列在前；默认列里没被模板覆盖的补在后面。"""
    merged: list[str] = []
    for name in list(columns or []) + list(defaults):
        clean = str(name or "").strip()
        if clean and clean not in merged:
            merged.append(clean)
    return merged


def _steps_text(steps: Any) -> str:
    if not isinstance(steps, list):
        return ""
    lines: list[str] = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, str):
            action, expected = step.strip(), ""
        elif isinstance(step, dict):
            action = str(step.get("action") or step.get("step") or "").strip()
            expected = str(step.get("expected") or "").strip()
        else:
            continue
        if not action and not expected:
            continue
        text = f"{index}. {action}"
        if expected:
            text += f"（预期：{expected}）"
        lines.append(text)
    return "\n".join(lines)


def _case_values(item: dict[str, Any]) -> dict[str, str]:
    """一条用例 → 列名到值的映射。"""
    case_type = str(item.get("caseType") or item.get("case_type") or "")
    raw_extra = item.get("extra")
    extra: dict[str, Any] = raw_extra if isinstance(raw_extra, dict) else {}
    values = {
        "用例编号": str(item.get("caseNo") or item.get("case_no") or ""),
        "用例标题": str(item.get("title") or ""),
        "用例类型": CASE_TYPE_LABEL.get(case_type, case_type),
        "优先级": str(item.get("priority") or ""),
        "所属模块": str(item.get("module") or extra.get("所属模块") or item.get("modulePath") or ""),
        "前置条件": str(item.get("preconditions") or ""),
        "测试步骤": _steps_text(item.get("steps")),
        "预期结果": str(item.get("expectedResult") or item.get("verifyTarget") or ""),
        "标签": str(item.get("tags") or ""),
        "接口URL": str(extra.get("接口URL") or item.get("apiPath") or ""),
        "请求方式": str(extra.get("请求方式") or item.get("method") or ""),
        "请求参数": str(extra.get("请求参数") or item.get("params") or ""),
        "预期状态码": str(extra.get("预期状态码") or ""),
        "断言规则": str(extra.get("断言规则") or ""),
    }
    return values


def _case_row(item: dict[str, Any], headers: list[str]) -> list[str]:
    values = _case_values(item)
    return [values.get(header, "") for header in headers]


def _build_case_workbook(headers: list[str], rows: list[list[str]], sheet_name: str) -> bytes:
    workbook = Workbook()
    sheet = workbook.active
    sheet.title = _safe_sheet_title(sheet_name)
    _write_table(sheet, headers, rows, start_row=1)
    return _save(workbook)


# ---------------- Markdown 产出 ----------------


def _parse_markdown(text: str) -> list[tuple[str, list[str]]]:
    """按 `##` 切小节；第一个小节之前的内容归到「产出说明」。"""
    sections: list[tuple[str, list[str]]] = []
    title = "产出说明"
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if any(item.strip() for item in buffer):
                sections.append((title, buffer))
            title = line[3:].strip() or "小节"
            buffer = []
            continue
        buffer.append(line)
    if any(item.strip() for item in buffer):
        sections.append((title, buffer))
    return sections


def _table_rows(lines: list[str]) -> list[list[str]]:
    """把连续的 Markdown 表格行拆成单元格；分隔行（---）丢掉。"""
    rows: list[list[str]] = []
    for line in lines:
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if all(re.fullmatch(r":?-{2,}:?", cell) for cell in cells if cell):
            continue
        rows.append(cells)
    return rows


def _build_markdown_workbook(sections: list[tuple[str, list[str]]]) -> tuple[bytes, int]:
    workbook = Workbook()
    workbook.remove(workbook.active)
    used_titles: set[str] = set()
    total_rows = 0

    for title, lines in sections:
        rows = _table_rows(lines)
        sheet = workbook.create_sheet(_unique_title(_safe_sheet_title(title), used_titles))
        if rows:
            _write_table(sheet, rows[0], rows[1:], start_row=1)
            total_rows += max(len(rows) - 1, 0)
        else:
            # 没有表格的小节按原文落在一列里，避免信息丢失
            for index, line in enumerate(lines, start=1):
                if not line.strip():
                    continue
                cell = sheet.cell(row=index, column=1, value=line.strip())
                cell.alignment = WRAP
            sheet.column_dimensions["A"].width = MAX_COLUMN_WIDTH
        # 表格前后夹杂的说明文字也保留
        notes = [line.strip() for line in lines if line.strip() and not line.strip().startswith("|")]
        if rows and notes:
            banner = sheet.cell(row=len(rows) + 2, column=1, value="\n".join(notes))
            banner.alignment = WRAP

    if not workbook.worksheets:
        sheet = workbook.create_sheet("产出")
        sheet.cell(row=1, column=1, value="产出为空")
    return _save(workbook), total_rows


# ---------------- 通用写表 ----------------


def _write_table(sheet: Any, headers: list[str], rows: list[list[str]], *, start_row: int) -> None:
    for col_idx, header in enumerate(headers, start=1):
        cell = sheet.cell(row=start_row, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.fill = HEADER_FILL
    sheet.freeze_panes = f"A{start_row + 1}"

    priority_index = headers.index("优先级") + 1 if "优先级" in headers else 0
    for offset, row in enumerate(rows, start=1):
        row_idx = start_row + offset
        for col_idx, value in enumerate(row, start=1):
            cell = sheet.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = WRAP
            if priority_index and col_idx == priority_index and isinstance(value, str):
                if value.startswith("P0"):
                    cell.fill = P0_FILL
                elif value.startswith("P1"):
                    cell.fill = P1_FILL

    for col in sheet.columns:
        width = 0
        letter = col[0].column_letter
        for cell in col:
            if cell.value:
                width = max(width, max((len(part) for part in str(cell.value).splitlines()), default=0))
        sheet.column_dimensions[letter].width = min(max(width + 2, 10), MAX_COLUMN_WIDTH)


def _safe_sheet_title(title: str) -> str:
    clean = re.sub(r"[\\/*?:\[\]]", "_", title).strip() or "产出"
    return clean[:MAX_SHEET_TITLE]


def _unique_title(title: str, used: set[str]) -> str:
    candidate = title
    index = 2
    while candidate in used:
        suffix = f"-{index}"
        candidate = f"{title[: MAX_SHEET_TITLE - len(suffix)]}{suffix}"
        index += 1
    used.add(candidate)
    return candidate


def _save(workbook: Workbook) -> bytes:
    import io

    stream = io.BytesIO()
    workbook.save(stream)
    return stream.getvalue()


# ---------------- Word 导出 ----------------


def export_docx(
    capability: str,
    output: str,
    *,
    columns: list[str] | None = None,
    title: str = "",
) -> tuple[bytes, int]:
    """产出原文 → (docx 字节, 导出行数)。规则与 Excel 一致，只是落成 Word 文档。"""
    if capability not in EXPORT_WORD_CAPABILITIES:
        raise AppError(ErrorCode.PARAM_ERROR, "该能力暂不支持导出 Word")
    text = (output or "").strip()
    if not text:
        raise AppError(ErrorCode.PARAM_ERROR, "暂无产出，无法导出")
    if len(text) > MAX_OUTPUT_CHARS:
        raise AppError(ErrorCode.PARAM_ERROR, f"产出超过 {MAX_OUTPUT_CHARS} 字，无法导出")

    try:
        from docx import Document
    except ImportError:
        raise AppError(ErrorCode.PARAM_ERROR, "本机缺少 python-docx，无法导出 Word") from None

    document = Document()
    _setup_docx(document)

    if capability in EXPORT_CASE_CAPABILITIES:
        items = _try_parse_case_json(text)
        if items:
            headers = _merge_columns(columns, DEFAULT_CASE_COLUMNS)
            rows = [[str(value) for value in _case_row(item, headers)] for item in items]
            document.add_heading(title or "测试用例", level=1)
            _add_docx_table(document, headers, rows)
            return _save_docx(document), len(rows)

    document.add_heading(title or "产出", level=1)
    count = _add_markdown_docx(document, text)
    return _save_docx(document), count


def _setup_docx(document: Any) -> None:
    """正文用中文字体，避免 Word 打开时字体回退成宋体的默认 12pt。"""
    from docx.oxml.ns import qn
    from docx.shared import Pt

    style = document.styles["Normal"]
    style.font.name = "微软雅黑"
    style.font.size = Pt(10.5)
    rpr = style.element.get_or_add_rPr()
    rfonts = rpr.get_or_add_rFonts()
    rfonts.set(qn("w:eastAsia"), "微软雅黑")


def _add_docx_table(document: Any, headers: list[str], rows: list[list[str]]) -> None:
    from docx.enum.table import WD_TABLE_ALIGNMENT
    from docx.shared import Pt

    table = document.add_table(rows=1 + len(rows), cols=max(len(headers), 1))
    table.style = "Table Grid"
    table.alignment = WD_TABLE_ALIGNMENT.CENTER

    header_cells = table.rows[0].cells
    for index, name in enumerate(headers):
        cell = header_cells[index]
        cell.text = name
        for paragraph in cell.paragraphs:
            for run in paragraph.runs:
                run.bold = True
                run.font.size = Pt(9)

    for row_index, row in enumerate(rows, start=1):
        cells = table.rows[row_index].cells
        for col_index, value in enumerate(row):
            if col_index >= len(cells):
                break
            cell = cells[col_index]
            cell.text = str(value or "")
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.font.size = Pt(9)


def _plain_md(text: str) -> str:
    """去掉 Markdown 行内标记，Word 里按纯文本落。"""
    return re.sub(r"[*_`]+", "", text).strip()


def _flush_md_table(document: Any, lines: list[str]) -> int:
    rows = _table_rows(lines)
    if not rows:
        return 0
    headers = [_plain_md(cell) for cell in rows[0]]
    body = [[_plain_md(cell) for cell in row] for row in rows[1:]]
    _add_docx_table(document, headers, body)
    return len(body)


def _add_markdown_docx(document: Any, text: str) -> int:
    """Markdown → Word：标题映射成 Heading，表格映射成表格，其余按段落。"""
    table_buffer: list[str] = []
    count = 0
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("|"):
            table_buffer.append(stripped)
            continue
        if table_buffer:
            count += _flush_md_table(document, table_buffer)
            table_buffer = []
        if not stripped or stripped.startswith("```"):
            continue
        if stripped.startswith("#"):
            level = len(stripped) - len(stripped.lstrip("#"))
            document.add_heading(_plain_md(stripped.lstrip("#")), level=min(max(level, 1), 4))
            continue
        bullet = re.match(r"^([-*+]|\d+\.)\s+(.*)$", stripped)
        if bullet:
            document.add_paragraph(_plain_md(bullet.group(2)), style="List Bullet")
            continue
        document.add_paragraph(_plain_md(stripped))
    if table_buffer:
        count += _flush_md_table(document, table_buffer)
    return count


def _save_docx(document: Any) -> bytes:
    import io

    stream = io.BytesIO()
    document.save(stream)
    return stream.getvalue()
