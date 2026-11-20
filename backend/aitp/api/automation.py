"""自动化脚本资产 API：脚本 CRUD、执行、执行历史、运行时体检。

四类脚本共用一套接口，用 category 区分：
  web / app / api     → Python 解释器执行
  performance         → 本地 JMeter 执行
"""

from __future__ import annotations

import difflib
import re
import sqlite3
from pathlib import Path
from typing import Any

from flask import Blueprint, Response, current_app, request, send_file

from ..common.automation_frameworks import default_for, framework_codes, normalize_platforms
from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.automation_repo import CATEGORY_API, CATEGORY_APP, CATEGORY_LABEL, CATEGORY_PERFORMANCE, CATEGORY_SUFFIX, CATEGORY_WEB, MATCH_MODE_CASE_NO, MATCH_MODES, RUN_MODE_JMETER, SCRIPT_CATEGORIES, SCRIPT_STATUSES, SCRIPT_STATUS_DISABLED, AutomationRunRepository, AutomationScriptRepository, run_summary, script_summary
from ..repositories.script_asset_repo import AutomationScriptVersionRepository, UiElementRepository
from ..services import nav_badge_service
from ..services import (
    audit_service,
    automation_jobs,
    automation_service,
    automation_writeback,
    job_queue,
    notification_service,
)  # noqa: F401 - 导入即注册队列处理器
from .helpers import (
    audit,
    body,
    check_ref_project,
    commit,
    db,
    guard_project_scope,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    reject_project_move,
    require_project,
    scope_arg,
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
    "versionId": "version_id",
}

#: 创建 / 更新时可写字段
WRITE_FIELDS = {
    "name", "category", "targetType", "framework", "language", "appPlatforms",
    "projectId", "moduleId", "envId", "description", "content", "source",
    "runMode", "entryCommand", "timeoutSeconds", "status", "remark", "versionId",
    "preDbRefs", "postDbRefs", "dbAssertRefs",
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
    if "envId" in data:
        payload["env_id"] = _optional_int(data.get("envId"))
    if "versionId" in data:
        payload["version_id"] = _optional_int(data.get("versionId"))
    if "timeoutSeconds" in data:
        timeout = _optional_int(data.get("timeoutSeconds"))
        payload["timeout_seconds"] = timeout if timeout and timeout > 0 else None
    if "status" in data:
        status = check_choice(str(data.get("status") or ""), SCRIPT_STATUSES, "status")
        payload["status"] = status or "ready"
    if "caseMatchMode" in data:
        mode = str(data.get("caseMatchMode") or "").strip() or MATCH_MODE_CASE_NO
        payload["case_match_mode"] = check_choice(mode, MATCH_MODES, "caseMatchMode")
    if "caseMatchValue" in data:
        payload["case_match_value"] = optional_str(
            data.get("caseMatchValue"), "caseMatchValue", 500
        ) or ""
    if "content" in data:
        content = data.get("content")
        payload["content"] = "" if content is None else str(content)
    for camel, column in (("preDbRefs", "pre_db_refs"),
                          ("postDbRefs", "post_db_refs"),
                          ("dbAssertRefs", "db_assert_refs")):
        if camel in data:
            payload[column] = _dump_str_list(data.get(camel))
    return payload


def _dump_str_list(raw: Any) -> str:
    """数据库脚本引用字段：统一存 JSON 字符串数组（["SQL-001"]）。"""
    from ..repositories.base import dumps, loads

    if raw is None or raw == "":
        return "[]"
    if isinstance(raw, str):
        try:
            raw = loads(raw)
        except (TypeError, ValueError):
            raw = [part.strip() for part in raw.split(",") if part.strip()]
    if not isinstance(raw, list):
        raise AppError(ErrorCode.PARAM_ERROR, "数据库脚本引用必须是编号数组")
    items = [str(item).strip() for item in raw if str(item).strip()]
    return dumps(items)


def _check_framework(category: str, payload: dict[str, Any]) -> None:
    """框架要归该类脚本管：鸿蒙的 Hypium 不能记到 Web 脚本上，反之也一样。"""
    code = str(payload.get("framework") or "").strip()
    if not code:
        return
    allowed = framework_codes(category)
    if code not in allowed:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{CATEGORY_LABEL.get(category, category)}不支持框架「{code}」，可选：{'、'.join(allowed)}",
        )


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "参数必须是整数") from None


def _guard_run_scope(conn: sqlite3.Connection, row: dict[str, Any]) -> None:
    """执行记录按脚本归属判项目；缺 projectId 直接拒绝，跨项目按不存在处理。"""
    project_id = scope_arg()
    if project_id is None:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId，无法校验执行记录归属")
    script = AutomationScriptRepository(conn).get(int(row.get("script_id") or 0))
    owner = script.get("project_id") if script else None
    if script is None or (owner not in (None, "") and int(owner) != int(project_id)):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不属于该项目")


def _check_refs(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """脚本上的功能点/版本必须与脚本同项目，否则项目视图的脚本统计会吃到别的项目。"""
    project_id = payload.get("project_id")
    for key, table, label, field in (
        ("module_id", "biz_module", "功能点", "moduleId"),
        ("version_id", "biz_project_version", "项目版本", "versionId"),
    ):
        if key not in payload:
            continue
        payload[key] = check_ref_project(conn, table, payload.get(key), project_id, label, field)


def _check_env(conn: sqlite3.Connection, payload: dict[str, Any]) -> None:
    """envId 必须指向一套仍在用的环境，否则执行留痕里的环境就是脏数据。"""
    env_id = payload.get("env_id")
    if not env_id:
        return
    from ..repositories.test_env_repo import TestEnvRepository

    if TestEnvRepository(conn).get_by_id(int(env_id)) is None:
        raise AppError(ErrorCode.PARAM_ERROR, "绑定的测试环境不存在或已删除")


def _check_test_run(conn: sqlite3.Connection, value: Any) -> int | None:
    """脚本可以挂到某个测试轮次上执行，结果直接回写这一轮。"""
    test_run_id = _optional_int(value)
    if test_run_id is None:
        return None
    row = conn.execute(
        "SELECT id FROM biz_test_run WHERE id = ? AND is_deleted = 0", (test_run_id,)
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.PARAM_ERROR, "指定的测试轮次不存在或已删除")
    return test_run_id


# ---------------- 目录 ----------------

@bp.get("/categories")
def list_categories() -> Response:
    """四类脚本的目录说明与数量统计，前端用它渲染四个二级菜单；统计按当前项目收敛。"""
    conn = db()
    project_id = scope_arg()
    script_counts = AutomationScriptRepository(conn).count_by_category(project_id)
    run_stats = AutomationRunRepository(conn).stats_by_category(project_id)
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
    """运行时体检：解释器、JMeter 与各类框架（Appium / Hypium 等）是否可用。

    category 与 platforms 用来收窄框架清单，例如 app + hos 只回鸿蒙能用的框架。
    """
    conn = db()
    status = automation_service.check_runtime(conn)
    status["frameworks"] = automation_service.framework_runtimes(
        conn,
        str(request.args.get("category") or "").strip() or None,
        str(request.args.get("platforms") or "").strip() or None,
    )
    return ok(status)


@bp.get("/interpreters")
def list_interpreters() -> Response:
    """本机解释器体检：列出候选 Python 各自装了哪些框架依赖。"""
    conn = db()
    target = str(request.args.get("category") or "").strip() or None
    return ok({
        "current": automation_service.python_interpreter(conn),
        "candidates": automation_service.interpreter_candidates(conn, target),
    })


@bp.post("/interpreters/select")
def select_interpreter() -> Response:
    """切到另一个已装好依赖的解释器，省得用户自己猜 pip 装到了哪个 Python。"""
    conn = db()
    data = body()
    path = automation_service.set_python_interpreter(conn, str(data.get("path") or ""))
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_SYSTEM_CONFIG,
          0, "自动化 Python 解释器", {"pythonInterpreter": path})
    commit(conn)
    status = automation_service.check_runtime(conn)
    status["frameworks"] = automation_service.framework_runtimes(conn)
    return ok(status)


# ---------------- 脚本 CRUD ----------------

@bp.get("/scripts")
def list_scripts() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = AutomationScriptRepository(conn)
    filters = query_filters(FILTERS)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    refs_map = repo.scenario_refs(filters.get("project_id"))
    items = []
    for row in page.list:
        item = script_summary(row)
        refs = refs_map.get(int(row.get("id") or 0), [])
        item["refs"] = refs
        item["referenced"] = len(refs) > 0
        items.append(item)
    # referenced 是计算字段，从请求串直接读
    ref_param = str(request.args.get("referenced") or "").strip().lower()
    if ref_param in ("true", "false"):
        want = ref_param == "true"
        items = [item for item in items if bool(item["referenced"]) == want]
    payload = page_payload(page)
    payload["list"] = items
    payload["total"] = len(items)
    # 四个分类的计数要跟列表同一口径，否则切到 A 项目仍显示全库脚本数
    payload["categoryCounts"] = repo.count_by_category(filters.get("project_id"))
    return ok(payload)


@bp.get("/scripts/<pk>")
def get_script(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = AutomationScriptRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    guard_project_scope(row, scope_arg(), "自动化脚本")
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
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    payload = _write_payload(data)
    payload["project_id"] = project_id
    _check_framework(category, payload)
    _check_refs(conn, payload)
    _check_env(conn, payload)
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
    reject_project_move(data, row.get("project_id"), "自动化脚本")
    category = _category_of(data, str(row.get("category") or "web"))
    payload = _write_payload(data)
    payload.pop("project_id", None)
    _check_framework(category, payload)
    _check_refs(conn, {**payload, "project_id": row.get("project_id")})
    _check_env(conn, payload)
    if "name" in payload:
        payload["file_name"] = _file_name_of(category, payload["name"])
    # P1 #7：内容变更自动快照旧版，供差异对比/评审
    if "content" in payload and str(payload.get("content") or "") != str(row.get("content") or ""):
        vrepo = AutomationScriptVersionRepository(conn)
        vrepo.insert({
            "script_id": target,
            "version_no": vrepo.next_version_no(target),
            "content": str(row.get("content") or ""),
            "change_note": str(data.get("changeNote") or ""),
            "review_status": "pending",
        })
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


@bp.post("/scripts/<pk>/copy")
def copy_script(pk: str) -> Response:
    """复制单条自动化脚本：重新取号、名称加「副本-」前缀并重算文件名，其余配置字段一致。

    运行态字段（last_run_status / last_run_at）不复制；被复制的源脚本必须与当前项目同属。
    """
    conn = db()
    target = path_int(pk)
    repo = AutomationScriptRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    guard_project_scope(row, scope_arg(), "自动化脚本")
    category = str(row.get("category") or "web")
    name = (f"副本-{row.get('name') or ''}")[:200]
    src_no = str(row.get("script_no") or "")
    ai = src_no.startswith("AI_")
    payload: dict[str, Any] = {
        "script_no": repo.next_script_no(category, ai=ai),
        "name": name,
        "category": category,
        "target_type": row.get("target_type") or "",
        "framework": row.get("framework") or "",
        "language": row.get("language") or "",
        "app_platforms": row.get("app_platforms") or "",
        "project_id": row.get("project_id"),
        "module_id": row.get("module_id"),
        "description": row.get("description") or "",
        "file_name": _file_name_of(category, name),
        "content": row.get("content") or "",
        "source": row.get("source") or "manual",
        "run_mode": row.get("run_mode") or "script",
        "entry_command": row.get("entry_command") or "",
        "timeout_seconds": row.get("timeout_seconds"),
        "status": row.get("status") or "ready",
        "remark": row.get("remark") or "",
        "version_id": row.get("version_id"),
        "case_match_mode": row.get("case_match_mode") or MATCH_MODE_CASE_NO,
        "case_match_value": row.get("case_match_value") or "",
        "pre_db_refs": row.get("pre_db_refs") or "[]",
        "post_db_refs": row.get("post_db_refs") or "[]",
        "db_assert_refs": row.get("db_assert_refs") or "[]",
    }
    new_id = repo.insert(payload)
    commit(conn)
    new_row = repo.get(new_id) or {}
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_AUTOMATION_SCRIPT,
          new_id, str(new_row.get("name") or ""),
          {"scriptNo": new_row.get("script_no"), "category": category,
           "copyOf": row.get("script_no")})
    return ok(script_summary(new_row))


# ---------------- P1 #7 脚本版本 / 差异 / 评审 ----------------

@bp.get("/scripts/<pk>/versions")
def list_script_versions(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    page = AutomationScriptVersionRepository(conn).list(
        {"script_id": target}, None, page_num=1, page_size=100)
    return ok({"list": [
        {"id": r.get("id"), "versionNo": r.get("version_no"),
         "changeNote": r.get("change_note"), "reviewStatus": r.get("review_status"),
         "createdAt": r.get("created_at")}
        for r in page.list
    ]})


@bp.get("/scripts/<pk>/diff")
def script_diff(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = AutomationScriptRepository(conn)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "脚本不存在")
    vrepo = AutomationScriptVersionRepository(conn)
    base_id = optional_str(request.args.get("base"), "base", 20)
    target_id = optional_str(request.args.get("target"), "target", 20) or "current"
    base_content = str(row.get("content") or "")
    if base_id:
        base_row = vrepo.get(int(base_id))
        base_content = str((base_row or {}).get("content") or "")
    if target_id == "current":
        target_content = str(row.get("content") or "")
    else:
        target_row = vrepo.get(int(target_id))
        target_content = str((target_row or {}).get("content") or "")
    diff = list(difflib.unified_diff(
        base_content.splitlines(), target_content.splitlines(),
        fromfile=f"v{base_id or 'current'}", tofile=target_id, lineterm="",
    ))
    return ok({"diff": diff, "base": base_id or "current", "target": target_id})


@bp.post("/scripts/<pk>/versions/<vid>/review")
def review_script_version(pk: str, vid: str) -> Response:
    conn = db()
    vrepo = AutomationScriptVersionRepository(conn)
    row = vrepo.get(path_int(vid))
    if row is None or int(row.get("script_id") or 0) != path_int(pk):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "版本不存在")
    status = check_choice(str(body().get("status") or ""), ("approved", "rejected"), "status")
    vrepo.update(int(row["id"]), {"review_status": status})
    commit(conn)
    return ok({"id": row["id"], "reviewStatus": status})


# ---------------- P1 #8 元素库（Page Object） ----------------

def _element_public(r: dict[str, Any]) -> dict[str, Any]:
    return {"id": r.get("id"), "projectId": r.get("project_id"), "page": r.get("page"),
            "name": r.get("name"), "selector": r.get("selector"), "kind": r.get("kind"),
            "description": r.get("description"), "createdAt": r.get("created_at")}


@bp.get("/ui-elements")
def list_elements() -> Response:
    conn = db()
    filters = query_filters({"projectId": "project_id"})
    page = UiElementRepository(conn).list(filters, keyword_of() or None,
                                          page_num=1, page_size=500)
    return ok({"list": [_element_public(r) for r in page.list]})


@bp.post("/ui-elements")
def create_element() -> Response:
    conn = db()
    data = body()
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    name = require_str(data.get("name"), "name", 100)
    selector = require_str(data.get("selector"), "selector", 500)
    repo = UiElementRepository(conn)
    new_id = repo.insert({
        "project_id": project_id, "page": optional_str(data.get("page"), "page", 100) or "",
        "name": name, "selector": selector,
        "kind": check_choice(str(data.get("kind") or "css"), ("css", "xpath", "text", "role"), "kind", "css") or "css",
        "description": optional_str(data.get("description"), "description", 500) or "",
    })
    commit(conn)
    return ok(_element_public(repo.get(new_id) or {}))


@bp.put("/ui-elements/<pk>")
def update_element(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = UiElementRepository(conn)
    if repo.get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "元素不存在")
    data = body()
    payload: dict[str, Any] = {}
    for key, col in (("name", "name"), ("selector", "selector"), ("page", "page"), ("description", "description")):
        if key in data:
            payload[col] = optional_str(data[key], key, 500) or ""
    if "kind" in data:
        payload["kind"] = check_choice(str(data["kind"]), ("css", "xpath", "text", "role"), "kind", "css") or "css"
    repo.update(target, payload)
    commit(conn)
    return ok(_element_public(repo.get(target) or {}))


@bp.delete("/ui-elements/<pk>")
def delete_element(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    repo = UiElementRepository(conn)
    if repo.get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "元素不存在")
    repo.soft_delete(target)
    commit(conn)
    return ok({"deleted": True})


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
    automation_service.require_executable(conn, category, str(row.get("framework") or ""))

    data = body()
    timeout = _optional_int(data.get("timeoutSeconds"))
    if timeout is not None and timeout <= 0:
        timeout = None
    test_run_id = _check_test_run(conn, data.get("testRunId"))
    if data.get("async"):
        # 后台执行：入队后立即返回任务，前端到「任务队列」看进度与结果
        from ..services import job_queue

        task = job_queue.submit(
            conn,
            kind="automation.run",
            title=f"执行脚本 {row.get('name') or ''}".strip(),
            payload={"scriptId": target, "timeoutSeconds": timeout, "testRunId": test_run_id},
        )
        audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_AUTOMATION_RUN,
              int(task["id"]), str(row.get("name") or ""), {
                  "scriptNo": row.get("script_no"),
                  "category": category,
                  "testRunId": test_run_id,
                  "async": True,
              })
        return ok({"queued": True, "task": task})
    result = automation_service.execute_script(
        conn, row, _db_path(), timeout, test_run_id
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_AUTOMATION_RUN,
          int(result.get("id") or 0), str(row.get("name") or ""), {
              "scriptNo": row.get("script_no"),
              "category": category,
              "testRunId": test_run_id,
              "status": result.get("status"),
              "exitCode": result.get("exit_code"),
              "durationMs": result.get("duration_ms"),
              "writebackStatus": result.get("writeback_status"),
          }, result="success" if result.get("status") == "success" else "failed")
    notification_service.notify_automation_failed(conn, row, result)
    commit(conn)
    payload = run_summary(result)
    payload["caseResults"] = automation_writeback.case_results(conn, int(result.get("id") or 0))
    return ok(payload)


@bp.post("/scripts/batch-run")
def batch_run_scripts() -> Response:
    """从脚本列表批量投递执行；默认走任务队列，逐个留执行记录。"""
    conn = db()
    data = body()
    raw_ids = data.get("scriptIds")
    if not isinstance(raw_ids, list) or not raw_ids:
        raise AppError(ErrorCode.PARAM_ERROR, "请至少选择一个自动化脚本")
    test_run_id = _check_test_run(conn, data.get("testRunId"))
    timeout = _optional_int(data.get("timeoutSeconds"))
    if timeout is not None and timeout <= 0:
        timeout = None

    repo = AutomationScriptRepository(conn)
    scripts: list[dict[str, Any]] = []
    for raw in raw_ids[:20]:
        script_id = _optional_int(raw)
        row = repo.get(script_id) if script_id else None
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "自动化脚本不存在或已删除")
        if str(row.get("status") or "") == SCRIPT_STATUS_DISABLED:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, f"脚本「{row.get('name')}」已停用")
        automation_service.require_executable(
            conn, str(row.get("category") or "web"), str(row.get("framework") or "")
        )
        scripts.append(dict(row))

    tasks = [
        job_queue.submit(
            conn,
            kind="automation.run",
            title=f"执行脚本 · {script.get('name') or script.get('script_no')}",
            payload={
                "scriptId": int(script["id"]),
                "timeoutSeconds": timeout,
                "testRunId": test_run_id,
            },
            priority=70,
        )
        for script in scripts
    ]
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_AUTOMATION_RUN,
          int(tasks[0]["id"]), "批量执行自动化脚本",
          {"scripts": [script.get("script_no") for script in scripts],
           "taskIds": [task.get("id") for task in tasks],
           "testRunId": test_run_id})
    commit(conn)
    return ok({"queued": True, "tasks": tasks, "total": len(tasks)})


# ---------------- 执行历史 ----------------

@bp.get("/metrics")
def automation_metrics() -> Response:
    """P2 结构化指标：按类别聚合执行次数/通过率/平均与 P95 耗时（复用执行历史）。"""
    conn = db()
    rows = AutomationRunRepository(conn).list_all()
    by: dict[str, list[dict[str, Any]]] = {}
    for r in rows:
        by.setdefault(str(r.get("category") or "web"), []).append(r)
    out = []
    for cat, items in sorted(by.items()):
        durations = sorted(int(r.get("duration_ms") or 0) for r in items)
        success = sum(1 for r in items if r.get("status") == "success")
        total = len(items)
        p95 = durations[min(len(durations) - 1, int(len(durations) * 0.95))] if durations else 0
        out.append({
            "category": cat, "categoryLabel": CATEGORY_LABEL.get(cat, cat),
            "total": total, "success": success,
            "passRate": round(success * 100 / total, 1) if total else None,
            "avgDurationMs": round(sum(durations) / total) if total else 0,
            "p95DurationMs": p95,
        })
    return ok({"list": out})


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
    # 执行留痕没有 project_id 列，项目维度顺着脚本关联收敛
    project_id = scope_arg()
    page = repo.page_with_project(filters, keyword_of() or None, project_id,
                                  page_num, page_size)
    payload = page_payload(page)
    payload["list"] = [run_summary(row) for row in payload["list"]]
    payload["categoryStats"] = repo.stats_by_category(project_id)
    return ok(payload)


@bp.get("/runs/<pk>")
def get_run(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = AutomationRunRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    _guard_run_scope(conn, row)
    payload = run_summary(row)
    payload["caseResults"] = automation_writeback.case_results(conn, target)
    payload["artifacts"] = automation_service.list_run_artifacts(
        _db_path(), str(row.get("run_no") or "")
    )
    return ok(payload)


@bp.get("/runs/<pk>/artifacts/download")
def download_run_artifact(pk: str) -> Response:
    """下载执行产物；路径只允许落在本次执行目录内。"""
    conn = db()
    target = path_int(pk)
    row = AutomationRunRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    _guard_run_scope(conn, row)
    relative = str(request.args.get("path") or "").strip()
    if not relative:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少产物路径")
    path = automation_service.artifact_file(
        _db_path(), str(row.get("run_no") or ""), relative
    )
    if path is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "产物不存在或路径不合法")
    return send_file(path, as_attachment=True, download_name=path.name)


@bp.get("/runs/<pk>/report")
def export_run_report(pk: str) -> Response:
    """把一次执行汇总成 Markdown 报告下载（四类脚本通用，性能附 JTL 摘要）。"""
    conn = db()
    target = path_int(pk)
    row = AutomationRunRepository(conn).get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行记录不存在")
    _guard_run_scope(conn, row)
    run_no = str(row.get("run_no") or "")
    artifacts = automation_service.list_run_artifacts(_db_path(), run_no)
    jtl = None
    for item in artifacts:
        if str(item["path"]).lower().endswith(".jtl"):
            jtl_path = automation_service.artifact_file(_db_path(), run_no, str(item["path"]))
            if jtl_path is not None:
                jtl = automation_service.parse_jtl(jtl_path)
            break
    script = None
    if row.get("script_id"):
        script_row = AutomationScriptRepository(conn).get(int(row["script_id"]))
        if script_row is not None:
            script = dict(script_row)
    report = automation_service.build_run_report(
        dict(row), script, artifacts, jtl,
        automation_writeback.case_results(conn, target),
    )
    file_name = f"automation-report-{run_no or target}.md"
    return Response(
        report,
        mimetype="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="{file_name}"'},
    )


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
    project_id = require_project(conn, require_int(data.get("projectId"), "projectId"))
    payload["project_id"] = project_id
    if not str(payload.get("framework") or "").strip():
        # AI 产出未带框架时按类别落默认框架，避免列表「框架」列空着
        payload["framework"] = default_for(category, normalize_platforms(payload.get("app_platforms")))
    _check_framework(category, payload)
    _check_refs(conn, payload)
    _check_env(conn, payload)
    payload["name"] = name
    payload["category"] = category
    payload["content"] = content
    payload["source"] = str(data.get("source") or "ai")
    payload["file_name"] = _file_name_of(category, name)
    if category == CATEGORY_PERFORMANCE:
        payload["run_mode"] = RUN_MODE_JMETER

    repo = AutomationScriptRepository(conn)
    # ai=True 时走 AI_AUTO-W / AI_AUTO-A / AI_AUTO-I / AI_PERF 独立编号序列，
    # 在列表页一眼可识别 AI 来源脚本
    ai = bool(data.get("ai"))
    script_no = repo.next_script_no(category, ai=ai)
    payload["script_no"] = script_no
    script_id = repo.insert(payload)
    nav_badge_service.increase_for(conn, f"automation_{category}")
    commit(conn)
    row = repo.get(script_id) or {}
    audit(conn, audit_service.AUDIT_ACTION_IMPORT, audit_service.RESOURCE_AUTOMATION_SCRIPT,
          script_id, name, {"scriptNo": script_no, "category": category, "source": "ai"})
    return ok(script_summary(row))
