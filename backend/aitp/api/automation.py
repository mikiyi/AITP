"""自动化脚本资产 API：脚本 CRUD、执行、执行历史、运行时体检。

四类脚本共用一套接口，用 category 区分：
  web / app / api     → Python 解释器执行
  performance         → 本地 JMeter 执行
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app

from ..common.validators import check_choice, optional_str, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.automation_repo import (
    CATEGORY_API,
    CATEGORY_APP,
    CATEGORY_LABEL,
    CATEGORY_PERFORMANCE,
    CATEGORY_SUFFIX,
    CATEGORY_WEB,
    RUN_MODES,
    RUN_MODE_JMETER,
    SCRIPT_CATEGORIES,
    SCRIPT_STATUSES,
    SCRIPT_STATUS_DISABLED,
    AutomationRunRepository,
    AutomationScriptRepository,
    run_summary,
    script_summary,
)
from ..services import nav_badge_service
from ..services import audit_service, automation_service
from .helpers import (
    audit,
    body,
    commit,
    db,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
)

bp = Blueprint("automation", __name__)

#: 查询参数名 → 数据库列名（白名单）
FILTERS = {
    "category": "category",
    "projectId": "project_id",
    "moduleId": "module_id",
    "status": "status",
    "framework": "framework",
    "source": "source",
}

#: 创建 / 更新时可写字段
WRITE_FIELDS = {
    "name", "category", "targetType", "framework", "language", "appPlatforms",
    "projectId", "moduleId", "description", "content", "source",
    "runMode", "entryCommand", "timeoutSeconds", "status", "remark",
}

#: Markdown 代码块提取：```xml ... ``` 或 ```jmx ... ```
_CODE_BLOCK_RE = re.compile(r"```[ \t]*([A-Za-z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)

#: 各类脚本优先匹配的代码块语言标签
_CODE_LANGUAGES: dict[str, tuple[str, ...]] = {
    CATEGORY_PERFORMANCE: ("xml", "jmx", "jmx xml"),
    CATEGORY_WEB: ("python", "py"),
    CATEGORY_APP: ("python", "py"),
    CATEGORY_API: ("python", "py"),
}


def _db_path() -> Path:
    return Path(current_app.config["AITP_DB_PATH"])


def _extract_code(text: str, languages: tuple[str, ...]) -> str:
    """从 AI 产出里挑出代码块。

    先按语言标签匹配（xml / jmx / python），匹配不到就取最后一个代码块
    ——AI 生成脚本时通常把说明放前面、代码放最后。
    """
    matches = _CODE_BLOCK_RE.findall(text or "")
    blocks = [(str(lang).lower(), str(code)) for lang, code in matches]
    if not blocks:
        return (text or "").strip()
    for wanted in languages:
        for lang, code in blocks:
            if lang == wanted:
                return code.strip()
    for wanted in ("python", "py"):
        for lang, code in blocks:
            if lang == wanted:
                return code.strip()
    return blocks[-1][1].strip()


def _category_of(data: dict[str, Any], default: str = "web") -> str:
    raw = data.get("category")
    if raw in (None, ""):
        return default
    value = check_choice(str(raw), SCRIPT_CATEGORIES, "category")
    return value or default


def _file_name_of(category: str, name: str) -> str:
    """按类别给默认文件名；用户只提供名字，路径由服务端定。"""
    suffix = CATEGORY_SUFFIX.get(category, ".py")
    stem = re.sub(r"[^\w\-.]+", "_", str(name or "script")).strip("_.") or "script"
    return f"{stem[:60]}{suffix}"


def _write_payload(data: dict[str, Any]) -> dict[str, Any]:
    """把接口入参转成数据库列；列名固定在代码里，不来自请求内容。"""
    payload: dict[str, Any] = {}
    # 接口字段 → 数据库列名
    simple = {
        "name": "name",
        "description": "description",
        "framework": "framework",
        "language": "language",
        "appPlatforms": "app_platforms",
        "entryCommand": "entry_command",
        "remark": "remark",
        "targetType": "target_type",
        "source": "source",
        "runMode": "run_mode",
    }
    for key, column in simple.items():
        if key in data:
            value = data.get(key)
            payload[column] = "" if value is None else str(value)
    if "projectId" in data:
        payload["project_id"] = _optional_int(data.get("projectId"))
    if "moduleId" in data:
        payload["module_id"] = _optional_int(data.get("moduleId"))
    if "timeoutSeconds" in data:
        timeout = _optional_int(data.get("timeoutSeconds"))
        payload["timeout_seconds"] = timeout if timeout and timeout > 0 else None
    if "status" in data:
        status = check_choice(str(data.get("status") or ""), SCRIPT_STATUSES, "status")
        payload["status"] = status or "ready"
    if "content" in data:
        content = data.get("content")
        payload["content"] = "" if content is None else str(content)
    return payload


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "参数必须是整数") from None


# ---------------- 目录 ----------------

@bp.get("/categories")
def list_categories() -> Response:
    """四类脚本的目录说明与数量统计，前端用它渲染四个二级菜单。"""
    conn = db()
    script_counts = AutomationScriptRepository(conn).count_by_category()
    run_stats = AutomationRunRepository(conn).stats_by_category()
    items = []
    for category in SCRIPT_CATEGORIES:
        items.append({
            "category": category,
            "label": CATEGORY_LABEL[category],
            "scriptCount": script_counts.get(category, 0),
            "runStats": run_stats.get(category, {"total": 0, "success": 0, "failed": 0}),
            "runner": "jmeter" if category == CATEGORY_PERFORMANCE else "python",
        })
    return ok(items)


@bp.get("/runtime")
def get_runtime() -> Response:
    """运行时体检：解释器与 JMeter 是否可用。"""
    conn = db()
    return ok(automation_service.check_runtime(conn))


# ---------------- 脚本 CRUD ----------------

@bp.get("/scripts")
def list_scripts() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = AutomationScriptRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [script_summary(row) for row in payload["list"]]
    payload["categoryCounts"] = repo.count_by_category()
    return ok(payload)


@bp.get("/scripts/<pk>")
def get_script(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = AutomationScriptRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    payload = script_summary(row)
    runs = AutomationRunRepository(conn).list_for_script(target, 1, 20)
    payload["runs"] = [run_summary(item) for item in runs.list]
    return ok(payload)


@bp.post("/scripts")
def create_script() -> Response:
    conn = db()
    data = body()
    category = _category_of(data)
    name = require_str(data.get("name"), "name", 200)
    payload = _write_payload(data)
    payload["name"] = name
    payload["category"] = category
    if category == CATEGORY_PERFORMANCE and payload.get("run_mode") != "jmeter":
        payload["run_mode"] = RUN_MODE_JMETER
    if "content" not in payload:
        payload["content"] = ""
    payload["file_name"] = _file_name_of(category, name)

    repo = AutomationScriptRepository(conn)
    script_no = repo.next_script_no(category)
    payload["script_no"] = script_no
    script_id = repo.insert(payload)
    commit(conn)
    row = repo.get(script_id) or {}
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_AUTOMATION_SCRIPT,
          script_id, name, {"scriptNo": script_no, "category": category})
    return ok(script_summary(row))


@bp.put("/scripts/<pk>")
def update_script(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = AutomationScriptRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    data = body()
    category = _category_of(data, str(row.get("category") or "web"))
    payload = _write_payload(data)
    if "name" in payload:
        payload["file_name"] = _file_name_of(category, payload["name"])
    repo.update(target, payload)
    commit(conn)
    updated = repo.get(target) or {}
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_AUTOMATION_SCRIPT,
          target, str(updated.get("name") or ""), {"fields": sorted(payload.keys())})
    return ok(script_summary(updated))


@bp.delete("/scripts/<pk>")
def delete_script(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = AutomationScriptRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_AUTOMATION_SCRIPT,
          target, str(row.get("name") or ""), {"scriptNo": row.get("script_no")})
    return ok({"id": target, "deleted": True})


@bp.post("/scripts/<pk>/run")
def run_script(pk: str) -> Response:
    """执行脚本。执行失败也会返回记录（status=failed），不抛异常。"""
    conn = db()
    target = path_int(pk)
    repo = AutomationScriptRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    if str(row.get("status") or "") == SCRIPT_STATUS_DISABLED:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "脚本已停用，先启用再执行")

    category = str(row.get("category") or "web")
    automation_service.require_executable(conn, category)

    data = body()
    timeout = _optional_int(data.get("timeoutSeconds"))
    if timeout is not None and timeout <= 0:
        timeout = None
    result = automation_service.execute_script(conn, row, _db_path(), timeout)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_AUTOMATION_RUN,
          int(result.get("id") or 0), str(row.get("name") or ""), {
              "scriptNo": row.get("script_no"),
              "category": category,
              "status": result.get("status"),
              "exitCode": result.get("exit_code"),
              "durationMs": result.get("duration_ms"),
          }, result="success" if result.get("status") == "success" else "failed")
    return ok(run_summary(result))


# ---------------- 执行历史 ----------------

@bp.get("/runs")
def list_runs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = query_filters({
        "category": "category",
        "scriptId": "script_id",
        "status": "status",
    })
    repo = AutomationRunRepository(conn)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [run_summary(row) for row in payload["list"]]
    payload["categoryStats"] = repo.stats_by_category()
    return ok(payload)


@bp.get("/runs/<pk>")
def get_run(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = AutomationRunRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    return ok(run_summary(row))


# ---------------- 从 AI 产出一键入库 ----------------

@bp.post("/scripts/from-output")
def create_from_output() -> Response:
    """把 AI 实验室产出的脚本一键存成资产。

    典型场景：性能脚本生成卡片产出 .jmx，点「加入性能测试」即落库。
    """
    conn = db()
    data = body()
    output = require_str(data.get("output"), "output", 200000)
    category = _category_of(data, "web")
    name = optional_str(data.get("name"), "name", 200) or f"AI 脚本 {category}"
    content = _extract_code(output, _CODE_LANGUAGES.get(category, ("python", "py")))
    if not content:
        raise AppError(ErrorCode.PARAM_ERROR, "产出里没有可入库的代码内容")

    payload = _write_payload(data)
    payload["name"] = name
    payload["category"] = category
    payload["content"] = content
    payload["source"] = str(data.get("source") or "ai")
    payload["file_name"] = _file_name_of(category, name)
    if category == CATEGORY_PERFORMANCE:
        payload["run_mode"] = RUN_MODE_JMETER

    repo = AutomationScriptRepository(conn)
    script_no = repo.next_script_no(category)
    payload["script_no"] = script_no
    script_id = repo.insert(payload)
    nav_badge_service.increase_for(conn, f"automation_{category}")
    commit(conn)
    row = repo.get(script_id) or {}
    audit(conn, audit_service.AUDIT_ACTION_IMPORT, audit_service.RESOURCE_AUTOMATION_SCRIPT,
          script_id, name, {"scriptNo": script_no, "category": category, "source": "ai"})
    return ok(script_summary(row))
