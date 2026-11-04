"""设置 API：AI 供应商档案、系统配置、审计日志。"""

from __future__ import annotations

import re
from typing import Any

from flask import Blueprint, Response, current_app, request, send_file

from ..common.constants import AI_DEFAULT_TIMEOUT, PROVIDERS
from ..common.env_scope import normalize_scope
from ..common.validators import optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.ai_profile_repo import AIProfileRepository
from ..repositories.audit_log_repo import AuditLogRepository
from ..repositories.base import dumps, loads
from ..repositories.sys_config_repo import SysConfigRepository
from ..services import ai_check, audit_service, backup_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload, query_filters

bp = Blueprint("settings", __name__)

#: 每个配置项的取值说明：告诉新用户「能填什么、每个值是什么意思」。
#: description 是短标题，这里负责解释取值，前端在字段下方带提示图标展示。
CONFIG_HINTS: dict[str, str] = {
    "ai.degrade.enabled": (
        "可填 true 或 false。true：首选模型不可用时，按优先级自动换用下一个可用模型，"
        "页面只弹 3 秒提示；false：首选模型失败就直接报错，不自动切换。"
    ),
    "ai.timeout.default": (
        "整数，单位秒，默认 60。单次 AI 调用超过这个时长即判定超时。"
        "云端接口一般 30~60，本地小模型可放宽到 120~300。"
    ),
    "knowledge.auto_reindex": (
        "可填 true 或 false。true：服务启动时按文件内容重建知识库全文索引（条目多时启动会变慢）；"
        "false：只在保存条目或手动点「重建索引」时更新。"
    ),
    "knowledge.file_mode": (
        "可填 mirror 或 db_only。mirror：审核通过的条目同时写一份 Markdown 到存放目录，便于备份与外部编辑；"
        "db_only：只存数据库，不落盘。"
    ),
    "knowledge.storage_path": (
        "绝对目录路径，例如 D:\Qoter\AItools\knowledge。知识条目会以 .md 形式写到这里；"
        "改路径不会自动搬动已有文件，需要迁移请到「知识库设置」执行迁移。"
    ),
    "module.default_types": (
        "逗号分隔的层级角色名，默认 module,submodule,feature，依次对应模块、子模块、功能点。"
        "新建模块树时按这个顺序生成层级，一般不需要修改。"
    ),
    "automation.python.interpreter": (
        "Python 解释器的绝对路径，例如 D:\app_install\python311\python.exe。"
        "留空则使用 AITP 服务自身的解释器；只影响自动化脚本执行，不影响 AI 调用。"
    ),
    "automation.python.timeout": (
        "整数，单位秒，默认 300。自动化脚本单次执行超过该时长会被强制结束，并记为「超时」。"
    ),
    "performance.jmeter.home": (
        "JMeter 安装根目录，例如 C:\tools\apache-jmeter-5.6.3，"
        "其下需要有 bin/jmeter（Windows 为 bin/jmeter.bat）。"
    ),
    "performance.jmeter.args": (
        "额外启动参数，空格分隔，例如 -Jthread.count=50 -Jduration=60。留空表示不加参数。"
    ),
}


PROFILE_WRITE_FIELDS = {
    "name", "provider", "protocol", "base_url", "model", "temperature",
    "max_tokens", "timeout", "is_default", "is_active", "sort_order", "priority",
    "is_local", "extra",
}


#: 环境变量名只允许字母、数字、下划线，且不能以数字开头
_ENV_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")


def _env_key_columns(data: dict[str, Any], existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """整理「启用本地 Key」相关字段；开启时必须给出合法的变量名。"""
    base = existing or {}
    use_env = bool(data.get("useEnvKey")) if "useEnvKey" in data else bool(base.get("use_env_key"))
    name = (
        str(data.get("envKeyName") or "").strip()
        if "envKeyName" in data
        else str(base.get("env_key_name") or "").strip()
    )
    scope = normalize_scope(
        data.get("envKeyScope") if "envKeyScope" in data else base.get("env_key_scope"),
    )
    if use_env:
        if not name:
            raise AppError(ErrorCode.PARAM_ERROR, "启用本地 Key 时必须填写环境变量名")
        if not _ENV_NAME_RE.match(name):
            raise AppError(
                ErrorCode.PARAM_ERROR,
                "环境变量名只能包含字母、数字与下划线，且不能以数字开头",
            )
    return {
        "use_env_key": 1 if use_env else 0,
        "env_key_name": name,
        "env_key_scope": scope,
    }


def _camel_of(snake: str) -> str:
    """下划线转驼峰，用于兼容两种入参写法。"""
    head, *rest = snake.split("_")
    return head + "".join(part.capitalize() for part in rest)


def _default_timeout(conn: Any) -> float:
    """AI 调用默认超时秒数；可在系统配置 ai.timeout.default 里调整。"""
    raw = SysConfigRepository(conn).get_value("ai.timeout.default", "")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return AI_DEFAULT_TIMEOUT
    return value if value > 0 else AI_DEFAULT_TIMEOUT


# ---------------- AI 供应商档案 ----------------

@bp.get("/ai/profiles")
def list_profiles() -> Response:
    conn = db()
    rows = AIProfileRepository(conn).list_all(None, keyword_of() or None, limit=500)
    return ok([AIProfileRepository.to_public(row) for row in rows])


@bp.get("/ai/providers")
def list_providers() -> Response:
    result = []
    for name, spec in PROVIDERS.items():
        result.append({
            "name": spec.name,
            "label": spec.label,
            "protocol": spec.protocol,
            "defaultBaseUrl": spec.default_base_url,
            "defaultModel": spec.default_model,
            "requiresApiKey": spec.requires_api_key,
            "envKeys": list(spec.env_key),
        })
    return ok(result)


@bp.get("/ai/profiles/<pk>")
def get_profile(pk: str) -> Response:
    conn = db()
    row = AIProfileRepository(conn).get_by_id(int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "AI 档案不存在")
    return ok(AIProfileRepository.to_public(row))


@bp.post("/ai/profiles")
def create_profile() -> Response:
    conn = db()
    data = body()
    name = require_str(data.get("name"), "name", 200)
    provider = require_str(data.get("provider"), "provider", 64)
    spec = PROVIDERS.get(provider)
    protocol = optional_str(data.get("protocol"), "protocol", 64) or (spec.protocol if spec else "openai_compat")
    base_url = optional_str(data.get("baseUrl"), "baseUrl", 500) or (
        spec.default_base_url if spec else ""
    )
    model = optional_str(data.get("model"), "model", 200) or (
        spec.default_model if spec else ""
    )
    repo = AIProfileRepository(conn)
    new_id = repo.insert({
        "name": name,
        "provider": provider,
        "protocol": protocol,
        "base_url": base_url,
        "model": model,
        "api_key_masked": "",
        "extra_json": dumps(data.get("extra") or {}),
        "temperature": float(data.get("temperature", 0.7)),
        "max_tokens": int(data.get("maxTokens", 4096)),
        "timeout": float(data.get("timeout", _default_timeout(conn))),
        "is_default": 1 if data.get("isDefault") else 0,
        "is_active": 1 if data.get("isActive", True) else 0,
        "sort_order": int(data.get("sortOrder", 0)),
        "priority": int(data.get("priority", 0)),
        "is_local": 1 if (data.get("isLocal") if data.get("isLocal") is not None
                          else (spec.name == "ollama" if spec else False)) else 0,
        **_env_key_columns(data),
    })
    api_key = optional_str(data.get("apiKey"), "apiKey", 500)
    if api_key:
        repo.save_api_key(new_id, api_key)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_AI_PROFILE, new_id, name,
          {"provider": provider, "hasApiKey": bool(api_key)})
    return ok(AIProfileRepository.to_public(repo.get_by_id(new_id) or {}))


@bp.put("/ai/profiles/<pk>")
def update_profile(pk: str) -> Response:
    conn = db()
    repo = AIProfileRepository(conn)
    target = int(pk)
    row = repo.get_by_id(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "AI 档案不存在")
    data = body()
    payload: dict[str, Any] = {}
    # 同时兼容驼峰与下划线两种写法，前端统一用驼峰
    for key in ("name", "provider", "protocol", "base_url", "model", "sort_order", "priority"):
        camel = _camel_of(key)
        if key in data:
            payload[key] = data[key]
        elif camel in data:
            payload[key] = data[camel]
    for key in ("temperature", "timeout"):
        if key in data:
            payload[key] = float(data[key])
    if "max_tokens" in data:
        payload["max_tokens"] = int(data["max_tokens"])
    elif "maxTokens" in data:
        payload["max_tokens"] = int(data["maxTokens"])
    for key in ("is_default", "is_active", "is_local"):
        camel = _camel_of(key)
        if key in data:
            payload[key] = 1 if data[key] else 0
        elif camel in data:
            payload[key] = 1 if data[camel] else 0
    if "extra" in data:
        payload["extra_json"] = dumps(data["extra"] or {})
    if any(key in data for key in ("useEnvKey", "envKeyName", "envKeyScope")):
        payload.update(_env_key_columns(data, row))
    repo.update(target, payload)
    api_key = optional_str(data.get("apiKey"), "apiKey", 500)
    if api_key:
        repo.save_api_key(target, api_key)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_AI_PROFILE, target,
          str(payload.get("name", row.get("name", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(AIProfileRepository.to_public(repo.get_by_id(target) or {}))


@bp.delete("/ai/profiles/<pk>")
def delete_profile(pk: str) -> Response:
    conn = db()
    repo = AIProfileRepository(conn)
    target = int(pk)
    row = repo.get_by_id(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "AI 档案不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_AI_PROFILE, target,
          row.get("name", ""))
    return ok({"id": target, "deleted": True})


@bp.post("/ai/profiles/<pk>/api-key")
def set_profile_api_key(pk: str) -> Response:
    """保存 API Key 到系统密钥库；数据库只留掩码与引用。"""
    conn = db()
    repo = AIProfileRepository(conn)
    target = int(pk)
    row = repo.get_by_id(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "AI 档案不存在")
    data = body()
    api_key = optional_str(data.get("apiKey"), "apiKey", 500)
    masked = repo.save_api_key(target, api_key)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_AI_PROFILE, target,
          row.get("name", ""), {"apiKeyUpdated": bool(api_key)})
    return ok({"id": target, "apiKeyMasked": masked, "hasApiKey": bool(api_key)})


# ---------------- AI 生效状态 / 自检 / 模型列表 ----------------

@bp.get("/ai/status")
def ai_status() -> Response:
    """当前生效的 AI 配置与各字段来源层，供页面头部状态条展示。"""
    return ok(ai_check.status(db()))


@bp.post("/ai/selfcheck")
def ai_selfcheck() -> Response:
    """真实调用一次 AI 做自检；可传未保存的配置来测试。"""
    conn = db()
    data = body()
    result = ai_check.self_check(conn, data)
    audit(
        conn,
        audit_service.AUDIT_ACTION_CONFIG,
        audit_service.RESOURCE_AI_PROFILE,
        result.get("profileId"),
        "selfcheck",
        {
            "ok": result["ok"],
            "provider": result["provider"],
            "model": result["model"],
            "reason": result["reason"],
            "draft": result["draft"],
        },
        result="success" if result["ok"] else "failed",
    )
    return ok(result)


@bp.post("/ai/models")
def ai_models() -> Response:
    """列出服务端可用模型；未保存的配置也能查询。"""
    conn = db()
    data = body()
    return ok(ai_check.list_models(conn, data))


# ---------------- 系统配置 ----------------

@bp.get("/config")
def list_config() -> Response:
    conn = db()
    rows = SysConfigRepository(conn).list_all()
    result = []
    for row in rows:
        result.append({
            "key": row.get("config_key"),
            "value": row.get("config_value"),
            "valueType": row.get("value_type"),
            "scope": row.get("scope"),
            "description": row.get("description"),
            "hint": CONFIG_HINTS.get(str(row.get("config_key") or ""), ""),
            "isSystem": bool(row.get("is_system")),
        })
    return ok(result)


@bp.put("/config")
def update_config() -> Response:
    conn = db()
    data = body()
    items = data.get("items")
    if not isinstance(items, list) or not items:
        raise AppError(ErrorCode.PARAM_ERROR, "items 必须是非空数组")
    repo = SysConfigRepository(conn)
    updated = 0
    for item in items:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "items 元素必须是对象")
        key = require_str(item.get("key"), "key", 120)
        value = optional_str(item.get("value"), "value", 2000)
        value_type = optional_str(item.get("valueType"), "valueType", 20) or "string"
        repo.set_value(key, value, value_type=value_type)
        updated += 1
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_SYSTEM_CONFIG, None, "",
          {"count": updated})
    return ok({"updated": updated})


# ---------------- 审计日志 ----------------

@bp.get("/audit-logs")
def list_audit_logs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = query_filters({
        "action": "action",
        "resourceType": "resource_type",
        "resourceId": "resource_id",
        "result": "result",
    })
    repo = AuditLogRepository(conn)
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [
        {
            "id": row.get("id"),
            "actor": row.get("actor"),
            "action": row.get("action"),
            "resourceType": row.get("resource_type"),
            "resourceId": row.get("resource_id"),
            "resourceLabel": row.get("resource_label"),
            "detail": loads(row.get("detail_json"), {}) or {},
            "result": row.get("result"),
            "traceId": row.get("trace_id"),
            "clientIp": row.get("client_ip"),
            "createdAt": row.get("created_at"),
        }
        for row in payload["list"]
    ]
    return ok(payload)


@bp.get("/audit-logs/resources")
def list_audit_resources() -> Response:
    conn = db()
    resource_type = require_str(request.args.get("resourceType"), "resourceType", 64)
    resource_id = require_int(request.args.get("resourceId"), "resourceId")
    rows = AuditLogRepository(conn).list_by_resource(resource_type, resource_id)
    return ok(rows)


# ---------------- 数据备份 ----------------

@bp.get("/backups")
def list_backups() -> Response:
    return ok({
        "list": backup_service.list_backups(),
        "dir": str(backup_service.backup_dir()),
        "dbPath": str(current_app.config["AITP_DB_PATH"]),
    })


@bp.post("/backups")
def create_backup() -> Response:
    conn = db()
    info = backup_service.create(conn)
    audit(conn, audit_service.AUDIT_ACTION_BACKUP, audit_service.RESOURCE_DB_BACKUP, None,
          str(info["name"]), {"sizeBytes": info["sizeBytes"]})
    return ok(info)


@bp.post("/backups/upload")
def upload_backup() -> Response:
    """上传外部备份文件用于还原：先收进备份目录并校验，再由用户确认恢复。"""
    conn = db()
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要上传的备份文件")
    info = backup_service.save_upload(uploaded.read(), uploaded.filename)
    audit(conn, audit_service.AUDIT_ACTION_IMPORT, audit_service.RESOURCE_DB_BACKUP, None,
          str(info["name"]), {"sizeBytes": info["sizeBytes"]})
    return ok(info)


@bp.get("/backups/<name>/download")
def download_backup(name: str) -> Response:
    path = backup_service.resolve_existing(name)
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/octet-stream")


@bp.post("/backups/<name>/restore")
def restore_backup(name: str) -> Response:
    """恢复会覆盖当前库，必须回传 confirm 且内容与备份文件名一致。"""
    conn = db()
    data = body()
    confirm = optional_str(data.get("confirm"), "confirm", 200) or ""
    if confirm != name:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "请输入备份文件名以确认恢复")
    result = backup_service.restore(conn, name)
    audit(conn, audit_service.AUDIT_ACTION_RESTORE, audit_service.RESOURCE_DB_BACKUP, None, name, result)
    return ok(result)


@bp.delete("/backups/<name>")
def delete_backup(name: str) -> Response:
    conn = db()
    removed = backup_service.remove(name)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_DB_BACKUP, None, removed)
    return ok({"name": removed, "deleted": True})
