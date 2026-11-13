"""测试用例 xlsx 导入。

与 xlsx_exporter 成对使用：导出的文件再导入，内容能还原。
- 按表头名取值，不依赖列顺序；不认识的列忽略
- 「所属模块/功能点」按项目里的模块路径或名称匹配，匹配不到时原样存进扩展字段
- 「关联需求」按需求编号匹配，匹配不到时原样存进扩展字段
- 覆盖矩阵是导出时算出来的，导入时跳过
"""

from __future__ import annotations

import io
import re
from typing import Any

from openpyxl import load_workbook

from ..common.enums import CASE_STATUS, CASE_STATUS_LABEL, CASE_TYPE
from .xlsx_exporter import SHEET_HEADERS

#: 表名 → 用例类型
SHEET_TYPE = {name: case_type for case_type, (name, _) in SHEET_HEADERS.items()}

#: 执行状态中文 → 内部取值
STATUS_BY_LABEL = {label: value for value, label in CASE_STATUS_LABEL.items()}

_STEP_PREFIX_RE = re.compile(r"^\s*\d+[\.、)]\s*")


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def steps_from_text(text: str) -> list[dict[str, Any]]:
    """「1.进入页面\\n2.点击按钮」→ 步骤列表。"""
    steps: list[dict[str, Any]] = []
    for raw in (text or "").splitlines():
        line = _STEP_PREFIX_RE.sub("", raw.strip())
        if line:
            steps.append({"action": line, "expected": ""})
    return steps


def pair_steps(steps_text: str, expected_text: str) -> list[dict[str, Any]]:
    """多行步骤与多行预期按行号一一配对；预期行数不足时留空，不串行。"""
    steps = steps_from_text(steps_text)
    expected_lines = [line.strip() for line in (expected_text or "").splitlines()]
    for index, step in enumerate(steps):
        step["expected"] = expected_lines[index] if index < len(expected_lines) else ""
    return steps


def _row_to_dict(headers: list[str], values: list[Any]) -> dict[str, str]:
    row: dict[str, str] = {}
    for index, header in enumerate(headers):
        if not header:
            continue
        row[header] = _text(values[index]) if index < len(values) else ""
    return row


def parse_workbook(data: bytes) -> list[dict[str, Any]]:
    """解析 xlsx，返回待导入的用例列表。

    每条包含 caseType 与各字段映射结果，由调用方落库。
    """
    workbook = load_workbook(filename=io.BytesIO(data), data_only=True)
    cases: list[dict[str, Any]] = []
    for sheet_name in workbook.sheetnames:
        case_type = SHEET_TYPE.get(sheet_name)
        if case_type is None:
            continue
        sheet = workbook[sheet_name]
        rows = list(sheet.iter_rows(values_only=True))
        if not rows:
            continue
        headers = [_text(cell) for cell in rows[0]]
        for raw in rows[1:]:
            if raw is None or all(_text(cell) == "" for cell in raw):
                continue
            cases.append(_build_case(case_type, _row_to_dict(headers, list(raw))))
    workbook.close()
    return cases


def _build_case(case_type: str, row: dict[str, str]) -> dict[str, Any]:
    """一行表格 → 用例字典。"""
    extra: dict[str, str] = {}
    if case_type == "interface":
        title = row.get("用例描述", "")
        extra["模块/接口名"] = row.get("模块/接口名", "")
        for key in (
            "接口URL", "请求方式", "token/鉴权方式", "请求头", "请求参数", "请求体",
            "预期状态码", "预期响应体", "断言规则", "幂等性校验",
        ):
            extra[key] = row.get(key, "")
        extra["执行时间"] = ""
        extra["备注"] = row.get("备注", "")
        priority = ""
        steps_text = ""
        expected = ""
        actual = row.get("实际结果", "")
        creator = row.get("执行人/执行时间", "")
    else:
        title = row.get("用例标题/描述", "")
        extra["测试数据"] = row.get("测试数据", "")
        extra["执行时间"] = row.get("执行时间", "")
        extra["备注"] = row.get("备注", "")
        if case_type == "boundary":
            extra["边界类型"] = row.get("边界类型", "")
            extra["边界描述"] = row.get("边界描述", "")
        elif case_type == "security":
            extra["权限类型"] = row.get("权限类型", "")
        elif case_type == "exception":
            extra["异常类型"] = row.get("异常类型", "")
        priority = row.get("优先级", "")
        steps_text = row.get("测试步骤", "")
        expected = row.get("预期结果", "")
        actual = row.get("实际结果", "")
        creator = row.get("执行人", "")

    status = STATUS_BY_LABEL.get(row.get("执行状态", ""), "not_executed")

    return {
        "caseType": case_type,
        "caseNo": row.get("用例编号", ""),
        "title": title,
        "moduleText": row.get("所属模块/功能点", ""),
        "requirementNo": row.get("关联需求", ""),
        "priority": priority if priority in ("P0", "P1", "P2", "P3") else "P2",
        "preconditions": row.get("前置条件", ""),
        "steps": pair_steps(steps_text, expected),
        "expectedResult": expected,
        "actualResult": actual,
        "status": status,
        "creator": creator,
        "extra": {key: value for key, value in extra.items() if value},
    }


__all__ = ["CASE_TYPE", "CASE_STATUS", "parse_workbook", "steps_from_text"]
