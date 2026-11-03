"""设置 API：AI 供应商档案、系统配置、审计日志。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.constants import AI_DEFAULT_TIMEOUT, PROVIDERS
from ..common.validators import optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.ai_profile_repo import AIProfileRepository
from ..repositories.audit_log_repo import AuditLogRepository
from ..repositories.base import dumps, loads
from ..repositories.sys_config_repo import SysConfigRepository
from ..services import ai_check, audit_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload, query_filters

bp = Blueprint("settings", __name__)

PROFILE_WRITE_FIELDS = {
    "name", "provider", "protocol", "base_url", "model", "temperature",
    "max_tokens", "timeout", "is_default", "is_active", "sort_order", "priority",
    "is_local", "extra",
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
