"""工作台编排服务。

「入口」是把一段固定工作流封装成可复用的资产：入口有名字、有触发词、
有必填信息清单、有引用的知识条目，还有一条按顺序执行的能力流水线。
入口可以存草稿、发布版本；运行时用哪个版本，事后能查。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ai.capability_codes import CAPABILITIES, label_of
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps
from ..repositories.workbench_entry_repo import (
    ENTRY_ARCHIVED,
    ENTRY_DRAFT,
    ENTRY_PUBLISHED,
    WorkbenchEntryRepository,
)
from . import composite_service

#: 入口编号白名单：只允许小写字母、数字、连字符
_CODE_MAX = 60


def _check_code(code: str) -> str:
    value = (code or "").strip()
    if not value:
        raise AppError(ErrorCode.PARAM_ERROR, "入口编号不能为空")
    if len(value) > _CODE_MAX:
        raise AppError(ErrorCode.PARAM_ERROR, f"入口编号最长 {_CODE_MAX} 个字符")
    if not all(ch.isalnum() or ch in "-_" for ch in value):
        raise AppError(ErrorCode.PARAM_ERROR, "入口编号只能包含字母、数字、连字符和下划线")
    return value


def _check_capability(capability: str) -> str:
    value = (capability or "").strip()
    if value not in CAPABILITIES:
        raise AppError(ErrorCode.PARAM_ERROR, f"未知 AI 能力：{value or '(空)'}")
    return value


def _str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AppError(ErrorCode.PARAM_ERROR, f"{field} 必须是数组")
    return [str(item).strip() for item in value if str(item).strip()]


def _check_workflow(value: Any) -> list[dict[str, Any]]:
    """校验工作流：每步必须给出合法的能力编号。"""
    if value is None:
        return []
    if not isinstance(value, list):
        raise AppError(ErrorCode.PARAM_ERROR, "workflow 必须是数组")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(value, start=1):
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, f"workflow[{index}] 必须是对象")
        capability = _check_capability(str(item.get("capability") or ""))
        variables = item.get("variables")
        if variables is None:
            variables = {}
        if not isinstance(variables, dict):
            raise AppError(ErrorCode.PARAM_ERROR, f"workflow[{index}].variables 必须是对象")
        result.append({
            "capability": capability,
            "name": str(item.get("name") or label_of(capability)),
            "moduleId": item.get("moduleId"),
            "variables": {str(k): v for k, v in variables.items()},
        })
    return result


def create_entry(
    conn: sqlite3.Connection,
    *,
    code: str,
    name: str,
    capability: str,
    description: str = "",
    triggers: list[str] | None = None,
    required: list[str] | None = None,
    knowledge: list[str] | None = None,
    workflow: list[dict[str, Any]] | None = None,
    status: str = ENTRY_DRAFT,
) -> int:
    repo = WorkbenchEntryRepository(conn)
    entry_code = _check_code(code)
    if repo.get_by_code(entry_code):
        raise AppError(ErrorCode.UNIQUE_CONFLICT, f"入口编号已存在：{entry_code}")
    return repo.insert({
        "code": entry_code,
        "name": str(name or entry_code),
        "capability": _check_capability(capability),
        "description": str(description or ""),
        "triggers_json": dumps(triggers or []),
        "required_json": dumps(required or []),
        "knowledge_json": dumps(knowledge or []),
        "workflow_json": dumps(_check_workflow(workflow)),
        "version": 1,
        "status": status if status in (ENTRY_DRAFT, ENTRY_PUBLISHED) else ENTRY_DRAFT,
    })


def update_entry(conn: sqlite3.Connection, pk: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = WorkbenchEntryRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入口不存在")
    # 已发布是终态：冻结编辑，避免「发布后还能改」破坏版本可追溯性
    if row.get("status") == ENTRY_PUBLISHED:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "已发布的编排为终态，不能编辑；如需调整请新建编排")
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = str(data["name"] or "")
    if "description" in data:
        payload["description"] = str(data["description"] or "")
    if "capability" in data:
        payload["capability"] = _check_capability(str(data["capability"]))
    if "triggers" in data:
        payload["triggers_json"] = dumps(_str_list(data["triggers"], "triggers"))
    if "required" in data:
        payload["required_json"] = dumps(_str_list(data["required"], "required"))
    if "knowledge" in data:
        payload["knowledge_json"] = dumps(_str_list(data["knowledge"], "knowledge"))
    if "workflow" in data:
        payload["workflow_json"] = dumps(_check_workflow(data["workflow"]))
    if "status" in data:
        status = str(data["status"])
        if status not in (ENTRY_DRAFT, ENTRY_PUBLISHED, ENTRY_ARCHIVED):
            raise AppError(ErrorCode.PARAM_ERROR, "入口状态非法")
        payload["status"] = status
    repo.update(pk, payload)
    detail = repo.get_detail(pk)
    if detail is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入口不存在")
    return detail


def publish_entry(conn: sqlite3.Connection, pk: int) -> dict[str, Any]:
    """发布入口：版本号 +1，之后运行会记录用的是哪个版本。"""
    repo = WorkbenchEntryRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入口不存在")
    repo.publish(pk)
    detail = repo.get_detail(pk)
    if detail is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入口不存在")
    return detail


def run_entry(
    conn: sqlite3.Connection,
    pk: int,
    *,
    variables: dict[str, Any] | None = None,
    session_id: int | None = None,
    profile_id: int | None = None,
    module_id: int | None = None,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    """按入口定义的工作流顺序执行。"""
    repo = WorkbenchEntryRepository(conn)
    row = repo.get_detail(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "入口不存在")
    workflow = list(row.get("workflow") or [])
    if not workflow:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该入口还没有配置工作流")
    extra = variables or {}
    steps: list[dict[str, Any]] = []
    for item in workflow:
        merged = dict(item.get("variables") or {})
        merged.update({k: v for k, v in extra.items() if k not in merged})
        steps.append({
            "capability": str(item["capability"]),
            "name": str(item.get("name") or label_of(str(item["capability"]))),
            "moduleId": item.get("moduleId") or module_id,
            "variables": merged,
        })
    result = composite_service.run_composite(
        conn,
        steps=steps,
        session_id=session_id,
        profile_id=profile_id,
        module_id=module_id,
        title=str(row.get("name") or ""),
        continue_on_error=continue_on_error,
    )
    result["entryId"] = pk
    result["entryVersion"] = row.get("version")
    result["entryCode"] = row.get("code")
    return result
