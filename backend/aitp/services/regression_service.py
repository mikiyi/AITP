"""回归清单服务。

清单的来源有两种：手工写，或者让 AI 按变更范围生成。
生成的条目落到明细表里逐条跟踪状态——每条验证目标对应一个执行结果，
整单状态由明细结果回推，不允许「清单写了但没人跑」。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ai.capability_codes import CAP_REGRESSION_GENERATION
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..repositories.regression_repo import (
    ITEM_RESULTS,
    REGRESSION_DRAFT,
    RISK_LEVELS,
    RegressionItemRepository,
    RegressionRepository,
    item_summary,
)
from . import lab_service


def _items_from_output(parsed: Any) -> list[dict[str, Any]]:
    """从 AI 的 JSON 产出里取出回归条目。

    模型偶尔会把数组包在 {"items": [...]} 或 {"list": [...]} 里，
    两种都认；认不出来就返回空列表，不猜。
    """
    if isinstance(parsed, dict):
        for key in ("items", "list", "regressions", "regressionItems"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break
    if not isinstance(parsed, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        priority = str(raw.get("priority") or "P1").strip().upper()
        if priority not in ("P0", "P1", "P2"):
            priority = "P1"
        items.append({
            "title": title,
            "module": str(raw.get("module") or "").strip(),
            "priority": priority,
            "reason": str(raw.get("reason") or "").strip(),
            "verifyTarget": str(raw.get("verifyTarget") or raw.get("verify_target") or "").strip(),
        })
    return items


def create_regression(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    change_scope: str = "",
    affected_modules: str = "",
    version: str = "",
    risk_level: str = "low",
    content: str = "",
    source: str = "manual",
) -> int:
    repo = RegressionRepository(conn)
    risk = risk_level if risk_level in RISK_LEVELS else "low"
    return repo.insert({
        "project_id": project_id,
        "title": str(title or "回归清单"),
        "change_scope": str(change_scope or ""),
        "affected_modules": str(affected_modules or ""),
        "version": str(version or ""),
        "risk_level": risk,
        "status": REGRESSION_DRAFT,
        "source": source,
        "content": str(content or ""),
    })


def update_regression(conn: sqlite3.Connection, pk: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    payload: dict[str, Any] = {}
    if "title" in data:
        payload["title"] = str(data["title"] or "")
    if "changeScope" in data:
        payload["change_scope"] = str(data["changeScope"] or "")
    if "affectedModules" in data:
        payload["affected_modules"] = str(data["affectedModules"] or "")
    if "version" in data:
        payload["version"] = str(data["version"] or "")
    if "content" in data:
        payload["content"] = str(data["content"] or "")
    if "riskLevel" in data:
        risk = str(data["riskLevel"])
        if risk not in RISK_LEVELS:
            raise AppError(ErrorCode.PARAM_ERROR, "风险等级非法")
        payload["risk_level"] = risk
    repo.update(pk, payload)
    detail = repo.get_detail(pk)
    if detail is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    return detail


def _project_name(conn: sqlite3.Connection, project_id: int | None, fallback: str) -> str:
    """取项目名：优先用项目表里的真名，取不到就用清单标题兜底。

    提示词把 projectName 列为必填，传空串会被渲染层判成缺变量，
    所以这里必须给出一个非空的名字。
    """
    if project_id:
        project = ProjectRepository(conn).get(int(project_id))
        if project:
            name = str(project.get("name") or "").strip()
            if name:
                return name
    return str(fallback or "").strip() or "未命名项目"


def generate_items(
    conn: sqlite3.Connection,
    pk: int,
    *,
    profile_id: int | None = None,
) -> dict[str, Any]:
    """用「回归清单」能力生成条目并覆盖旧明细。"""
    repo = RegressionRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    change_scope = str(row.get("change_scope") or "").strip()
    if not change_scope:
        raise AppError(ErrorCode.PARAM_ERROR, "请先填写「变更范围」，再生成回归条目")
    variables = {
        "projectName": _project_name(
            conn, row.get("project_id"), str(row.get("title") or ""),
        ),
        "changeScope": change_scope,
        "affectedModules": str(row.get("affected_modules") or ""),
    }
    result = lab_service.run_capability(
        conn, CAP_REGRESSION_GENERATION, variables, profile_id=profile_id,
    )
    items = _items_from_output(result.parsed)
    item_repo = RegressionItemRepository(conn)
    created = item_repo.replace_all(pk, items)
    repo.update(pk, {
        "source": "ai",
        "status": "ready" if created else REGRESSION_DRAFT,
        "content": result.output,
    })
    return {
        "id": pk,
        "created": created,
        "items": [item_summary(row) for row in item_repo.list_by_regression(pk)],
        "output": result.output,
        "model": result.model,
        "reviewRequired": result.review_required,
    }


def add_item(conn: sqlite3.Connection, pk: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    title = str(data.get("title") or "").strip()
    if not title:
        raise AppError(ErrorCode.PARAM_ERROR, "回归点标题不能为空")
    priority = str(data.get("priority") or "P1").upper()
    if priority not in ("P0", "P1", "P2"):
        raise AppError(ErrorCode.PARAM_ERROR, "优先级只能是 P0 / P1 / P2")
    new_id = item_repo.insert({
        "regression_id": pk,
        "seq": item_repo.next_seq(pk),
        "title": title,
        "module": str(data.get("module") or ""),
        "priority": priority,
        "reason": str(data.get("reason") or ""),
        "verify_target": str(data.get("verifyTarget") or ""),
        "result": "not_executed",
        "remark": "",
    })
    repo.sync_status(pk)
    return item_summary(item_repo.get(new_id) or {})


def update_item(conn: sqlite3.Connection, pk: int, item_id: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    row = item_repo.get(item_id)
    if row is None or int(row.get("regression_id") or 0) != pk:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归明细不存在")
    payload: dict[str, Any] = {}
    if "title" in data:
        payload["title"] = str(data["title"] or "")
    if "module" in data:
        payload["module"] = str(data["module"] or "")
    if "reason" in data:
        payload["reason"] = str(data["reason"] or "")
    if "verifyTarget" in data:
        payload["verify_target"] = str(data["verifyTarget"] or "")
    if "priority" in data:
        priority = str(data["priority"]).upper()
        if priority not in ("P0", "P1", "P2"):
            raise AppError(ErrorCode.PARAM_ERROR, "优先级只能是 P0 / P1 / P2")
        payload["priority"] = priority
    if "result" in data:
        result = str(data["result"])
        if result not in ITEM_RESULTS:
            raise AppError(ErrorCode.PARAM_ERROR, "执行结果非法")
        payload["result"] = result
        payload["executed_at"] = utcnow_iso()
        payload["executed_by"] = str(data.get("executedBy") or "local")
    if "remark" in data:
        payload["remark"] = str(data["remark"] or "")
    item_repo.update(item_id, payload)
    repo.sync_status(pk)
    return item_summary(item_repo.get(item_id) or {})


def delete_item(conn: sqlite3.Connection, pk: int, item_id: int) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    row = item_repo.get(item_id)
    if row is None or int(row.get("regression_id") or 0) != pk:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归明细不存在")
    item_repo.soft_delete(item_id)
    repo.sync_status(pk)
    return {"id": item_id, "deleted": True}


def execute_items(
    conn: sqlite3.Connection,
    pk: int,
    results: list[dict[str, Any]],
) -> dict[str, Any]:
    """批量回写执行结果，并按明细回推整单状态。"""
    repo = RegressionRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    updated: list[int] = []
    skipped: list[int] = []
    for item in results:
        item_id = int(item.get("itemId") or 0)
        row = item_repo.get(item_id)
        if row is None or int(row.get("regression_id") or 0) != pk:
            skipped.append(item_id)
            continue
        result = str(item.get("result") or "")
        if result not in ITEM_RESULTS:
            skipped.append(item_id)
            continue
        item_repo.update(item_id, {
            "result": result,
            "remark": str(item.get("remark") or row.get("remark") or ""),
            "executed_by": str(item.get("executedBy") or "local"),
            "executed_at": utcnow_iso(),
        })
        updated.append(item_id)
    status = repo.sync_status(pk)
    return {
        "id": pk,
        "status": status,
        "updated": updated,
        "skipped": skipped,
        "stats": repo.stats_of(pk),
    }
