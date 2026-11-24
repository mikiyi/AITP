"""历史资产导入（V2.6.0）。

在原有 xlsx 用例导入之外，补 csv / json 两种存量格式，并提供「先干跑、再落库」
的两步流程：

- 干跑：解析 + 模块/需求匹配 + 编号冲突检查，返回将要创建/跳过/失败的明细，不写库；
- 落库：同一份文件再提交一次，写用例并留一条导入批次记录；
- 幂等：同项目下编号与标题都相同的用例直接跳过，重复导入不会产生重复数据。

格式约定（csv 表头 / json 字段，大小写与中英文别名均可）：
用例编号 / 用例标题 / 用例类型 / 优先级 / 所属模块 / 关联需求 / 前置条件 /
测试步骤 / 预期结果 / 实际结果 / 状态 / 创建人。
"""

from __future__ import annotations

import csv
import io
import json
import sqlite3
from typing import Any

from ..common.enums import CASE_STATUS
from ..common.time_utils import utcnow_iso
from ..repositories.numbering import next_no_monotonic
from . import xlsx_importer

FORMAT_XLSX = "xlsx"
FORMAT_CSV = "csv"
FORMAT_JSON = "json"
FORMATS = (FORMAT_XLSX, FORMAT_CSV, FORMAT_JSON)

BATCH_PREFIX = "IMP"
MAX_ROWS = 5000

#: 表头/字段别名 → 内部字段
ALIASES: dict[str, str] = {
    "用例编号": "caseNo", "编号": "caseNo", "caseNo": "caseNo", "case_no": "caseNo", "id": "caseNo",
    "用例标题": "title", "标题": "title", "title": "title", "name": "title",
    "用例类型": "caseType", "类型": "caseType", "caseType": "caseType", "type": "caseType",
    "优先级": "priority", "priority": "priority",
    "所属模块": "moduleText", "所属模块/功能点": "moduleText", "模块": "moduleText",
    "module": "moduleText", "moduleText": "moduleText",
    "关联需求": "requirementNo", "需求": "requirementNo", "需求编号": "requirementNo",
    "requirement": "requirementNo", "requirementNo": "requirementNo",
    "前置条件": "preconditions", "preconditions": "preconditions",
    "测试步骤": "stepsText", "步骤": "stepsText", "steps": "stepsText", "stepsText": "stepsText",
    "预期结果": "expectedResult", "expected": "expectedResult", "expectedResult": "expectedResult",
    "实际结果": "actualResult", "actual": "actualResult", "actualResult": "actualResult",
    "状态": "status", "status": "status",
    "创建人": "creator", "creator": "creator",
    "标签": "tags", "tags": "tags",
}

_TYPE_ALIASES = {
    "功能测试": "functional", "功能": "functional", "functional": "functional",
    "接口测试": "interface", "接口": "interface", "interface": "interface",
    "边界测试": "boundary", "边界": "boundary", "boundary": "boundary",
    "安全测试": "security", "权限安全测试": "security", "security": "security",
    "ui测试": "ui", "ui": "ui",
    "异常场景测试": "exception", "异常": "exception", "exception": "exception",
}


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def _canonical(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in row.items():
        field = ALIASES.get(str(key).strip()) or ALIASES.get(str(key).strip().lower())
        if field and field not in out:
            out[field] = value
    return out


def _decode(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _normalize(row: dict[str, Any]) -> dict[str, Any]:
    raw = _canonical(row)
    case_type = str(raw.get("caseType") or "").strip()
    case_type = _TYPE_ALIASES.get(case_type.lower(), _TYPE_ALIASES.get(case_type, "functional"))
    steps_value = raw.get("stepsText") or raw.get("steps") or []
    if isinstance(steps_value, list):
        steps = []
        for item in steps_value:
            if isinstance(item, dict):
                steps.append({
                    "action": _text(item.get("action") or item.get("step") or item.get("操作")),
                    "expected": _text(item.get("expected") or item.get("预期")),
                })
            elif _text(item):
                steps.append({"action": _text(item), "expected": ""})
    else:
        steps = xlsx_importer.pair_steps(
            _text(steps_value), _text(raw.get("expectedResult") or raw.get("expected")),
        )
    status = _text(raw.get("status")) or "not_executed"
    if status not in CASE_STATUS:
        status = "not_executed"
    return {
        "caseNo": _text(raw.get("caseNo")),
        "title": _text(raw.get("title")),
        "caseType": case_type,
        "priority": _text(raw.get("priority")) or "P2",
        "moduleText": _text(raw.get("moduleText")),
        "requirementNo": _text(raw.get("requirementNo")),
        "preconditions": _text(raw.get("preconditions")),
        "steps": steps,
        "expectedResult": _text(raw.get("expectedResult") or raw.get("expected")),
        "actualResult": _text(raw.get("actualResult") or raw.get("actual")),
        "status": status,
        "creator": _text(raw.get("creator")),
        "tags": _text(raw.get("tags")),
    }


def parse_file(file_name: str, data: bytes) -> tuple[list[dict[str, Any]], str]:
    """按扩展名解析文件，返回 (规范化用例列表, 格式)。"""
    suffix = str(file_name or "").rsplit(".", 1)[-1].lower()
    if suffix == FORMAT_XLSX:
        items = xlsx_importer.parse_workbook(data)
        return [_from_xlsx(item) for item in items], FORMAT_XLSX
    if suffix == FORMAT_CSV:
        text = _decode(data)
        reader = csv.DictReader(io.StringIO(text))
        rows = [_normalize(row) for row in reader]
        return [row for row in rows if row["title"] or row["caseNo"]], FORMAT_CSV
    if suffix == FORMAT_JSON:
        payload = json.loads(_decode(data))
        if isinstance(payload, dict):
            payload = payload.get("cases") or payload.get("items") or payload.get("data") or []
        if not isinstance(payload, list):
            raise ValueError("JSON 需为用例数组，或包含 cases/items 数组的对象")
        rows = [_normalize(row) for row in payload if isinstance(row, dict)]
        return [row for row in rows if row["title"] or row["caseNo"]], FORMAT_JSON
    raise ValueError("只支持 .xlsx / .csv / .json 格式")


def _from_xlsx(item: dict[str, Any]) -> dict[str, Any]:
    """把 xlsx 解析结果对齐到统一结构，行为与既有导入保持一致。"""
    return {
        "caseNo": _text(item.get("caseNo")),
        "title": _text(item.get("title")),
        "caseType": str(item.get("caseType") or "functional"),
        "priority": _text(item.get("priority")) or "P2",
        "moduleText": _text(item.get("moduleText")),
        "requirementNo": _text(item.get("requirementNo")),
        "preconditions": _text(item.get("preconditions")),
        "steps": list(item.get("steps") or []),
        "expectedResult": _text(item.get("expectedResult")),
        "actualResult": _text(item.get("actualResult")),
        "status": str(item.get("status") or "not_executed"),
        "creator": _text(item.get("creator")),
        "tags": "",
    }


def plan_rows(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    items: list[dict[str, Any]],
) -> dict[str, Any]:
    """为每行算出「新建 / 跳过 / 失败」，不写库。"""
    from ..repositories.module_repo import ModuleRepository
    from ..repositories.requirement_repo import RequirementRepository
    from ..repositories.test_case_repo import TestCaseRepository

    repo = TestCaseRepository(conn)
    module_repo = ModuleRepository(conn)
    requirement_repo = RequirementRepository(conn)

    modules = module_repo.list_all(project_id)
    module_by_text: dict[str, int] = {}
    for node in modules:
        name = str(node.get("name") or "")
        path = module_repo.path_text(int(node["id"]), project_name="")
        if name:
            module_by_text.setdefault(name, int(node["id"]))
        if path:
            module_by_text.setdefault(path, int(node["id"]))
            module_by_text.setdefault(path.split("/")[-1], int(node["id"]))

    requirements = requirement_repo.list_by_project(project_id)
    requirement_by_no = {str(item.get("req_no") or ""): int(item["id"]) for item in requirements}
    requirement_by_title = {str(item.get("title") or ""): int(item["id"]) for item in requirements}

    existing = conn.execute(
        "SELECT case_no, title FROM biz_test_case WHERE project_id = ? AND is_deleted = 0",
        (int(project_id),),
    ).fetchall()
    existing_pairs = {(str(row["case_no"]), str(row["title"])) for row in existing}
    used_numbers = {str(row["case_no"]) for row in existing}

    rows: list[dict[str, Any]] = []
    created = skipped = failed = 0
    for index, item in enumerate(items[:MAX_ROWS], start=1):
        title = str(item.get("title") or "").strip()
        case_no = str(item.get("caseNo") or "").strip()
        if not title:
            failed += 1
            rows.append({"line": index, "action": "failed", "title": "", "caseNo": case_no,
                         "reason": "缺少用例标题"})
            continue
        if case_no and (case_no, title) in existing_pairs:
            skipped += 1
            rows.append({"line": index, "action": "skip", "title": title, "caseNo": case_no,
                         "reason": "同编号同标题已存在，按幂等跳过"})
            continue
        will_renumber = bool(case_no) and case_no in used_numbers
        module_id = module_by_text.get(str(item.get("moduleText") or "").strip())
        requirement_text = str(item.get("requirementNo") or "").strip()
        requirement_id = requirement_by_no.get(requirement_text) or requirement_by_title.get(requirement_text)
        created += 1
        rows.append({
            "line": index,
            "action": "create",
            "title": title,
            "caseNo": case_no,
            "caseType": item.get("caseType"),
            "moduleId": module_id,
            "moduleText": item.get("moduleText"),
            "requirementId": requirement_id,
            "renumber": will_renumber,
        })
        if case_no:
            used_numbers.add(case_no)
    return {
        "rows": rows,
        "summary": {
            "total": len(rows),
            "created": created,
            "skipped": skipped,
            "failed": failed,
            "renumbered": sum(1 for row in rows if row.get("renumber")),
        },
        "truncated": len(items) > MAX_ROWS,
    }


def record_batch(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    file_name: str,
    file_format: str,
    mode: str,
    summary: dict[str, Any],
    errors: list[dict[str, Any]],
    created_by: str = "local",
) -> dict[str, Any]:
    now = utcnow_iso()
    batch_no = next_no_monotonic(conn, "biz_case_import_batch", "batch_no", BATCH_PREFIX)
    cursor = conn.execute(
        """
        INSERT INTO biz_case_import_batch
            (batch_no, project_id, file_name, format, mode, status, total, created,
             skipped, failed, errors_json, created_by, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            batch_no, int(project_id), str(file_name)[:255], file_format, mode,
            "failed" if summary.get("failed") else "done",
            int(summary.get("total") or 0), int(summary.get("created") or 0),
            int(summary.get("skipped") or 0), int(summary.get("failed") or 0),
            json.dumps(errors[:200], ensure_ascii=False), created_by[:64], now, now,
        ),
    )
    return {
        "id": int(cursor.lastrowid or 0),
        "batchNo": batch_no,
        "fileName": file_name,
        "format": file_format,
        "mode": mode,
        "summary": summary,
    }


def list_batches(conn: sqlite3.Connection, *, project_id: int, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, batch_no, file_name, format, mode, status, total, created, skipped,
               failed, created_at
        FROM biz_case_import_batch
        WHERE project_id = ? AND is_deleted = 0
        ORDER BY id DESC LIMIT ?
        """,
        (int(project_id), max(1, min(100, int(limit)))),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "batchNo": row["batch_no"],
            "fileName": row["file_name"],
            "format": row["format"],
            "mode": row["mode"],
            "status": row["status"],
            "total": int(row["total"] or 0),
            "created": int(row["created"] or 0),
            "skipped": int(row["skipped"] or 0),
            "failed": int(row["failed"] or 0),
            "createdAt": row["created_at"],
        }
        for row in rows
    ]


__all__ = [
    "FORMATS",
    "FORMAT_XLSX",
    "FORMAT_CSV",
    "FORMAT_JSON",
    "parse_file",
    "plan_rows",
    "record_batch",
    "list_batches",
    "MAX_ROWS",
]
