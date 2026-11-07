"""测试用例 xlsx 导出。

严格对齐参考实现 generate_test_cases.py：
- 7 张表的顺序与表头逐格一致（功能/接口/边界/权限安全/UI/异常/覆盖矩阵）
- 表头加粗、居中换行、填充 DAEEF3；冻结 A2；列宽 = min(max_len + 2, 30)
- 优先级单元格 P0 填 FFC7CE、P1 填 FFEB9C

与源脚本的一处修正：源脚本按「列索引 5」上色，接口测试用例表的第 5 列是「请求方式」
而不是优先级，色块会落在错误列上；这里改为按表头名定位「优先级」列，定位不到则不上色。
"""

from __future__ import annotations

import io
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.worksheet.worksheet import Worksheet

from ..common.enums import CASE_STATUS_LABEL, CASE_TYPE

HEADER_FILL = PatternFill(start_color="DAEEF3", end_color="DAEEF3", fill_type="solid")
P0_FILL = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
P1_FILL = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
HEADER_FONT = Font(bold=True)
CENTER = Alignment(horizontal="center", vertical="center", wrap_text=True)

MAX_COLUMN_WIDTH = 30

HEADERS_FUNCTIONAL = (
    "用例编号", "用例标题/描述", "所属模块/功能点", "关联需求", "优先级", "用例类型",
    "前置条件", "测试步骤", "测试数据", "预期结果", "实际结果",
    "执行状态", "执行人", "执行时间", "关联缺陷", "备注",
)
HEADERS_INTERFACE = (
    "用例编号", "模块/接口名", "用例描述", "接口URL", "请求方式", "token/鉴权方式",
    "请求头", "请求参数", "请求体", "前置条件", "预期状态码", "预期响应体",
    "断言规则", "实际结果", "执行状态", "执行人/执行时间", "关联缺陷",
    "幂等性校验", "备注",
)
HEADERS_BOUNDARY = (
    "用例编号", "用例标题/描述", "所属模块/功能点", "关联需求", "优先级", "用例类型",
    "边界类型", "边界描述", "前置条件", "测试步骤", "测试数据", "预期结果",
    "实际结果", "执行状态", "执行人", "执行时间", "关联缺陷", "备注",
)
HEADERS_SECURITY = (
    "用例编号", "用例标题/描述", "所属模块/功能点", "关联需求", "优先级", "用例类型",
    "权限类型", "前置条件", "测试步骤", "测试数据", "预期结果",
    "实际结果", "执行状态", "执行人", "执行时间", "关联缺陷", "备注",
)
HEADERS_UI = (
    "用例编号", "用例标题/描述", "所属模块/功能点", "关联需求", "优先级", "用例类型",
    "前置条件", "测试步骤", "测试数据", "预期结果", "实际结果",
    "执行状态", "执行人", "执行时间", "关联缺陷", "备注",
)
HEADERS_EXCEPTION = (
    "用例编号", "用例标题/描述", "所属模块/功能点", "关联需求", "优先级", "用例类型",
    "异常类型", "前置条件", "测试步骤", "测试数据", "预期结果",
    "实际结果", "执行状态", "执行人", "执行时间", "关联缺陷", "备注",
)
HEADERS_MATRIX = ("需求点", "关联用例", "覆盖状态", "优先级")

#: 6 张用例表的表头与中文名
SHEET_HEADERS: dict[str, tuple[str, tuple[str, ...]]] = {
    "functional": ("功能测试用例", HEADERS_FUNCTIONAL),
    "interface": ("接口测试用例", HEADERS_INTERFACE),
    "boundary": ("边界测试用例", HEADERS_BOUNDARY),
    "security": ("权限安全测试用例", HEADERS_SECURITY),
    "ui": ("UI测试用例", HEADERS_UI),
    "exception": ("异常场景测试用例", HEADERS_EXCEPTION),
}

#: 用例类型列里写的中文名
TYPE_LABEL = {
    "functional": "功能测试",
    "interface": "接口测试",
    "boundary": "边界测试",
    "security": "安全测试",
    "ui": "UI测试",
    "exception": "异常测试",
}

#: 「用例类型」之后那一列的类型特有表头
TYPE_EXTRA_HEADER = {
    "boundary": "边界类型",
    "security": "权限类型",
    "exception": "异常类型",
}


def steps_to_text(steps: list[Any]) -> str:
    """步骤列表 → 「1.xxx\\n2.xxx」文本，与源脚本的书写格式一致。"""
    lines: list[str] = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, str):
            text = step.strip()
        else:
            text = str(step.get("action") or step.get("step") or "").strip()
        if not text:
            continue
        lines.append(f"{index}.{text}")
    return "\n".join(lines)


def _write_sheet(ws: Worksheet, headers: tuple[str, ...], rows: list[list[Any]]) -> None:
    """按源脚本的样式写表：表头填充加粗、数据居中换行、冻结 A2、列宽封顶 30。"""
    for col_idx, header in enumerate(headers, start=1):
        cell = ws.cell(row=1, column=col_idx, value=header)
        cell.font = HEADER_FONT
        cell.alignment = CENTER
        cell.fill = HEADER_FILL

    ws.freeze_panes = "A2"

    # 优先级列按表头名定位：接口测试用例表没有「优先级」列，就不上色
    priority_index = headers.index("优先级") + 1 if "优先级" in headers else 0

    for row_idx, row_data in enumerate(rows, start=2):
        for col_idx, value in enumerate(row_data, start=1):
            cell = ws.cell(row=row_idx, column=col_idx, value=value)
            cell.alignment = CENTER
            if priority_index and col_idx == priority_index and isinstance(value, str):
                if "P0" in value:
                    cell.fill = P0_FILL
                elif "P1" in value:
                    cell.fill = P1_FILL

    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            if cell.value:
                max_length = max(max_length, len(str(cell.value)))
        ws.column_dimensions[column].width = min(max_length + 2, MAX_COLUMN_WIDTH)


def _case_row(
    case: dict[str, Any],
    *,
    case_type: str,
    module_path: str,
    requirement_no: str,
    bug_nos: list[str],
) -> list[Any]:
    """把一条用例转成该类型表的行内容。"""
    extra = case.get("extra") or {}
    status_label = CASE_STATUS_LABEL.get(str(case.get("status") or ""), "未执行")
    steps_text = steps_to_text(case.get("steps") or [])
    base = {
        "用例编号": case.get("case_no") or "",
        "用例标题/描述": case.get("title") or "",
        "所属模块/功能点": module_path or extra.get("所属模块/功能点", "") or "",
        "关联需求": requirement_no or extra.get("关联需求", "") or "",
        "优先级": case.get("priority") or "P2",
        "用例类型": TYPE_LABEL.get(case_type, case_type),
        "前置条件": case.get("preconditions") or "",
        "测试步骤": steps_text,
        "测试数据": extra.get("测试数据", "") or "",
        "预期结果": case.get("expected_result") or "",
        "实际结果": case.get("actual_result") or "",
        "执行状态": status_label,
        "执行人": case.get("creator") or "",
        "执行时间": extra.get("执行时间", "") or "",
        "关联缺陷": "、".join(bug_nos),
        "备注": extra.get("备注", "") or "",
        "边界类型": extra.get("边界类型", "") or "",
        "边界描述": extra.get("边界描述", "") or "",
        "权限类型": extra.get("权限类型", "") or "",
        "异常类型": extra.get("异常类型", "") or "",
    }

    if case_type == "interface":
        return [
            base["用例编号"],
            extra.get("模块/接口名", "") or module_path,
            base["用例标题/描述"],
            extra.get("接口URL", "") or "",
            extra.get("请求方式", "") or "",
            extra.get("token/鉴权方式", "") or "",
            extra.get("请求头", "") or "",
            extra.get("请求参数", "") or "",
            extra.get("请求体", "") or "",
            base["前置条件"],
            extra.get("预期状态码", "") or "",
            extra.get("预期响应体", "") or "",
            extra.get("断言规则", "") or "",
            base["实际结果"],
            base["执行状态"],
            f"{base['执行人']} {base['执行时间']}".strip(),
            base["关联缺陷"],
            extra.get("幂等性校验", "") or "",
            base["备注"],
        ]

    headers = SHEET_HEADERS[case_type][1]
    values: list[Any] = []
    for header in headers:
        if header == "用例类型":
            values.append(TYPE_LABEL.get(case_type, case_type))
        elif header in base:
            values.append(base[header])
        else:
            values.append("")
    return values


def _matrix_rows(cases: list[dict[str, Any]], requirements: list[dict[str, Any]]) -> list[list[Any]]:
    """覆盖矩阵：需求点 → 关联用例 → 覆盖状态 → 优先级。"""
    grouped: dict[int, list[dict[str, Any]]] = {}
    for case in cases:
        req_id = case.get("requirement_id")
        if req_id is None:
            continue
        grouped.setdefault(int(req_id), []).append(case)

    rows: list[list[Any]] = []
    for requirement in requirements:
        items = grouped.get(int(requirement["id"]), [])
        numbers = "、".join(str(item.get("case_no") or "") for item in items if item.get("case_no"))
        priority = "P2"
        for item in items:
            value = str(item.get("priority") or "")
            if value == "P0":
                priority = "P0"
                break
            if value == "P1":
                priority = "P1"
        rows.append([
            requirement.get("title") or "",
            numbers,
            "已覆盖" if items else "未覆盖",
            priority,
        ])
    return rows


def export_workbook(
    cases_by_type: dict[str, list[dict[str, Any]]],
    *,
    module_paths: dict[int, str],
    requirement_nos: dict[int, str],
    bug_nos_by_case: dict[int, list[str]],
    requirements: list[dict[str, Any]],
) -> bytes:
    """生成 xlsx 二进制内容。

    cases_by_type: 用例类型 → 用例列表（每条用例已带 steps / extra 等解析好的字段）
    module_paths: 功能点 id → 路径文本
    requirement_nos: 需求 id → 需求编号
    bug_nos_by_case: 用例 id → 关联缺陷编号列表
    requirements: 覆盖矩阵用的需求列表
    """
    workbook = Workbook()
    workbook.remove(workbook.active)

    all_cases: list[dict[str, Any]] = []
    for case_type in CASE_TYPE:
        sheet_name, headers = SHEET_HEADERS[case_type]
        rows: list[list[Any]] = []
        for case in cases_by_type.get(case_type, []):
            all_cases.append(case)
            rows.append(_case_row(
                case,
                case_type=case_type,
                module_path=module_paths.get(int(case.get("module_id") or 0), ""),
                requirement_no=requirement_nos.get(int(case.get("requirement_id") or 0), ""),
                bug_nos=bug_nos_by_case.get(int(case.get("id") or 0), []),
            ))
        ws = workbook.create_sheet(title=sheet_name)
        _write_sheet(ws, headers, rows)

    ws = workbook.create_sheet(title="测试覆盖矩阵")
    _write_sheet(ws, HEADERS_MATRIX, _matrix_rows(all_cases, requirements))

    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def build_export_filename(project_name: str) -> str:
    """导出文件名：<项目名>测试用例.xlsx，去掉 Windows 非法字符。"""
    safe = "".join("-" if ch in '\\/:*?"<>|' else ch for ch in project_name).strip()
    return f"{safe or 'AITP'}测试用例.xlsx"


__all__ = [
    "SHEET_HEADERS",
    "HEADERS_MATRIX",
    "TYPE_EXTRA_HEADER",
    "build_export_filename",
    "export_workbook",
    "steps_to_text",
]
