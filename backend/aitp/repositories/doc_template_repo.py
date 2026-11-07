"""测试文档模板仓储。

模板是「产出物长什么样」的约定，按类别区分，能力只能选同类模板：

- case    测试用例：用例生成 / XMind 转用例 / 接口测试设计；
- report  测试报告：报告生成；
- mindmap 测试点脑图：测试点脑图。

content 是提取出来的模板正文（也可能是用户手写），style_hint 记录从
上传文件里解析出的字体、字号等版式要求，两者一起渲染进提示词。
columns_json 只对测试用例模板有意义：决定导出 Excel 的列顺序。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository, loads

#: 模板类别
KIND_CASE = "case"
KIND_REPORT = "report"
KIND_MINDMAP = "mindmap"
TEMPLATE_KINDS = (KIND_CASE, KIND_REPORT, KIND_MINDMAP)

KIND_LABELS = {
    KIND_CASE: "测试用例",
    KIND_REPORT: "测试报告",
    KIND_MINDMAP: "测试点脑图",
}

#: 能力 → 可用的模板类别
CAPABILITY_KIND = {
    "case_generation": KIND_CASE,
    "xmind_to_case": KIND_CASE,
    "api_test_design": KIND_CASE,
    "report_generation": KIND_REPORT,
    "testpoint_generation": KIND_MINDMAP,
}

#: 支持 Excel 导出的能力（目前只有用例类）
EXCEL_CAPABILITIES = ("case_generation", "xmind_to_case", "api_test_design")

#: 导出 Excel 时允许的列上限
MAX_COLUMNS = 20
#: 模板正文上限
MAX_CONTENT = 8000
#: 版式说明上限
MAX_STYLE_HINT = 500


def normalize_columns(value: Any) -> list[str]:
    """把列定义规整成去重、去空、限长的字符串数组。"""
    if isinstance(value, str):
        raw = [item for item in value.replace("，", ",").split(",")]
    elif isinstance(value, list):
        raw = [str(item) for item in value]
    else:
        return []
    result: list[str] = []
    for item in raw:
        name = item.strip()[:40]
        if name and name not in result:
            result.append(name)
        if len(result) >= MAX_COLUMNS:
            break
    return result


def decorate(row: RowDict) -> dict[str, Any]:
    columns = loads(row.get("columns_json"), []) or []
    return {
        "id": row.get("id"),
        "name": row.get("name"),
        "kind": row.get("kind") or KIND_CASE,
        "kindLabel": KIND_LABELS.get(str(row.get("kind") or KIND_CASE), ""),
        "description": row.get("description") or "",
        "columns": columns if isinstance(columns, list) else [],
        "content": row.get("content") or "",
        "styleHint": row.get("style_hint") or "",
        "fileName": row.get("file_name") or "",
        "fileType": row.get("file_type") or "",
        "fileSize": int(row.get("file_size") or 0),
        #: 原文件落盘名，空串表示这条模板没有可下载的文件
        "storedName": row.get("stored_name") or "",
        "isDefault": bool(row.get("is_default")),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


class DocTemplateRepository(BaseRepository):
    table = "sys_doc_template"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "name", "kind", "is_default", "created_at", "updated_at"}
    searchable = ["name", "description", "content"]

    def stored_names(self) -> set[str]:
        """所有模板引用着的落盘文件名，用于判断哪些上传原件已无人使用。"""
        rows = self.raw("SELECT stored_name FROM sys_doc_template WHERE stored_name <> ''")
        return {str(row["stored_name"]) for row in rows if row["stored_name"]}


def dump_columns(columns: list[str]) -> str:
    import json

    return json.dumps(columns, ensure_ascii=False)
