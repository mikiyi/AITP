"""设置 API：AI 供应商档案、系统配置、首页卡片定制、审计日志。"""

from __future__ import annotations

import hashlib
import re
import secrets
from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, Response, current_app, request, send_file

from ..common.constants import AI_DEFAULT_TIMEOUT, PROVIDERS
from ..common.env_scope import normalize_scope
from ..common.validators import (
    optional_int,
    optional_str,
    require_float,
    require_int,
    require_str,
)
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.ai_profile_repo import AIProfileRepository
from ..repositories.audit_log_repo import AuditLogRepository
from ..repositories.base import dumps, loads
from ..repositories.ci_repo import (
    CiTokenRepository,
    NotificationChannelRepository,
    NotificationLogRepository,
    channel_summary,
    events_json,
    log_summary,
)
from ..repositories.sys_config_repo import SysConfigRepository
from ..services import ai_check, audit_service, backup_schedule, backup_service, home_card_service, notification_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload, path_int, query_filters

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
    "ai.mask.enabled": (
        "可填 true 或 false，默认 true。true：调用云端模型前自动把手机号、身份证、邮箱、"
        "银行卡号、API Key、账号口令替换成占位文本，AI 记录里只留类型与数量；"
        "false：原样发送。本地模型不出本机，始终不脱敏。"
    ),
    "backup.auto.enabled": (
        "可填 true 或 false，默认 true。true：按设定的频率与时间自动生成整包备份；"
        "false：只保留手动备份。停机错过的时间点会在下次启动时补跑一次。"
    ),
    "backup.auto.frequency": (
        "可填 daily（每天）或 weekly（每周）。配合 backup.auto.time 与 backup.auto.weekday 使用。"
    ),
    "backup.auto.time": "执行时间，24 小时制 HH:MM，例如 02:00。按本机时间计算。",
    "backup.auto.weekday": (
        "仅每周备份生效：1=周一，2=周二 … 7=周日。"
    ),
    "backup.auto.retention": (
        "自动备份保留份数，默认 7，范围 1~90。超出后只删除最旧的自动备份，"
        "不会删除手动备份与恢复前自动生成的备份。"
    ),
    "backup.auto.min_free_mb": (
        "磁盘剩余空间低于该值时跳过本次自动备份并发出通知，默认 512，填 0 表示不检查。"
    ),
    "home.cards": (
        "首页卡片的显示与顺序，由「设置 → 首页卡片定制」写入，形如 "
        '[{"key":"bugStatus","visible":true},…]。知识库问答助手、数据概览、功能快捷入口属于固定区，'
        "不会写进来；手工改这里只建议调整 visible，key 写错或漏配会自动回落到默认布局。"
    ),
    "knowledge.auto_reindex": (
        "可填 true 或 false。true：服务启动时按文件内容重建知识库全文索引（条目多时启动会变慢）；"
        "false：只在保存条目或手动点「重建索引」时更新。"
    ),
    "knowledge.file_mode": (
        "可填 mirror 或 db_only。mirror：审核通过的条目同时写一份 Markdown 到存放目录，便于备份与外部编辑；"
        "db_only：只存数据库，不落盘。"
    ),
    "knowledge.assistant_default_mode": (
        "知识库问答助手打开时预选的回答模式，可填 kb、llm 或 hybrid，默认 hybrid。"
        "该设置只影响前端预选项与「默认」标识，不改动已有对话。"
    ),
    "knowledge.storage_path": (
        "绝对目录路径，例如 Windows 的 D:\\aitp\\knowledge 或 macOS / Linux 的 ~/aitp/knowledge（支持 ~）。"
        "知识条目会以 .md 形式写到这里；"
        "改路径不会自动搬动已有文件，需要迁移请到「知识库设置」执行迁移。"
    ),
    "module.default_types": (
        "逗号分隔的层级角色名，默认 module,submodule,feature，依次对应模块、子模块、功能点。"
        "新建模块树时按这个顺序生成层级，一般不需要修改。"
    ),
    "automation.python.interpreter": (
        "Python 解释器的绝对路径，例如 Windows 的 C:\\Python312\\python.exe 或 macOS / Linux 的 /usr/bin/python3。"
        "留空则使用 AITP 服务自身的解释器；只影响自动化脚本执行，不影响 AI 调用。"
    ),
    "automation.python.timeout": (
        "整数，单位秒，默认 300。自动化脚本单次执行超过该时长会被强制结束，并记为「超时」。"
    ),
    "performance.jmeter.home": (
        "JMeter 安装根目录，例如 Windows 的 C:\\tools\\apache-jmeter-5.6.3 或 macOS / Linux 的 /opt/apache-jmeter-5.6.3，"
        "其下需要有 bin/jmeter（Windows 为 bin/jmeter.bat）。"
    ),
    "performance.jmeter.args": (
        "额外启动参数，空格分隔，例如 -Jthread.count=50 -Jduration=60。留空表示不加参数。"
    ),
    "automation.appium.command": (
        "Appium 命令行的绝对路径，例如 Windows 的 C:\\tools\\appium\\appium.cmd 或 macOS / Linux 的 /usr/local/bin/appium；留空则按系统 PATH 查找。"
        "App 自动化的 iOS / Android 用例走 Appium，这里只用于检测本机是否装好，不会自动安装。"
    ),
    "automation.hdc.command": (
        "鸿蒙 hdc 命令行工具的绝对路径，例如 Windows 的 C:\\devtools\\hdc.exe 或 macOS / Linux 的 /usr/local/bin/hdc；留空则按系统 PATH 查找。"
        "App 自动化的鸿蒙（HOS）用例走 Hypium，需要 hypium 包加 hdc 两个依赖。"
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


def _local_key_rule(spec: Any, columns: dict[str, Any]) -> dict[str, Any]:
    """本地大模型不使用 API Key，本地 Key 开关一律落成关闭。"""
    if spec is not None and getattr(spec, "is_local", False):
        columns = dict(columns)
        columns["use_env_key"] = 0
    return columns


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
            "isLocal": spec.is_local,
            "envKeys": list(spec.env_key),
        })
    return ok(result)


@bp.get("/ai/profiles/<pk>")
def get_profile(pk: str) -> Response:
    conn = db()
    row = AIProfileRepository(conn).get_by_id(path_int(pk))
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
        "temperature": require_float(data.get("temperature"), "temperature", 0.7),
        "max_tokens": require_int(data.get("maxTokens"), "maxTokens", 4096),
        "timeout": require_float(data.get("timeout"), "timeout", _default_timeout(conn)),
        "is_default": 1 if data.get("isDefault") else 0,
        "is_active": 1 if data.get("isActive", True) else 0,
        "sort_order": require_int(data.get("sortOrder"), "sortOrder", 0),
        "priority": require_int(data.get("priority"), "priority", 0),
        "is_local": 1 if (data.get("isLocal") if data.get("isLocal") is not None
                          else (spec.name == "ollama" if spec else False)) else 0,
        **_local_key_rule(spec, _env_key_columns(data)),
    })
    api_key = optional_str(data.get("apiKey"), "apiKey", 500)
    if api_key:
        repo.save_api_key(new_id, api_key)
    if data.get("isDefault"):
        repo.clear_other_defaults(new_id)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_AI_PROFILE, new_id, name,
          {"provider": provider, "hasApiKey": bool(api_key)})
    return ok(AIProfileRepository.to_public(repo.get_by_id(new_id) or {}))


@bp.put("/ai/profiles/<pk>")
def update_profile(pk: str) -> Response:
    conn = db()
    repo = AIProfileRepository(conn)
    target = path_int(pk)
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
            payload[key] = require_float(data[key], key)
    if "max_tokens" in data:
        payload["max_tokens"] = require_int(data["max_tokens"], "max_tokens")
    elif "maxTokens" in data:
        payload["max_tokens"] = require_int(data["maxTokens"], "maxTokens")
    for key in ("sort_order", "priority"):
        if key in payload:
            payload[key] = require_int(payload[key], key)
    for key in ("is_default", "is_active", "is_local"):
        camel = _camel_of(key)
        if key in data:
            payload[key] = 1 if data[key] else 0
        elif camel in data:
            payload[key] = 1 if data[camel] else 0
    if "extra" in data:
        payload["extra_json"] = dumps(data["extra"] or {})
    if any(key in data for key in ("useEnvKey", "envKeyName", "envKeyScope")):
        final_provider = str(payload.get("provider") or row.get("provider") or "")
        payload.update(_local_key_rule(PROVIDERS.get(final_provider), _env_key_columns(data, row)))
    repo.update(target, payload)
    if payload.get("is_default"):
        repo.clear_other_defaults(target)
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
    target = path_int(pk)
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
    target = path_int(pk)
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


@bp.get("/home-cards")
def get_home_cards() -> Response:
    """首页卡片布局：固定区 + 可定制区；?default=1 只回默认布局，供「恢复默认」预览。"""
    if str(request.args.get("default") or "") in ("1", "true"):
        return ok({"list": home_card_service.default_layout()})
    return ok({"list": home_card_service.load(db())})


@bp.put("/home-cards")
def update_home_cards() -> Response:
    """保存首页卡片的显示与排序；未知 key 会被丢弃，漏配的卡片自动补齐。"""
    conn = db()
    layout = home_card_service.save(conn, body().get("cards"))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_SYSTEM_CONFIG, None, "",
          {"key": home_card_service.HOME_CARDS_KEY,
           "visible": [item["key"] for item in layout if item["visible"]]})
    return ok({"list": layout})


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


# ---------------- CI 令牌 ----------------

def _ci_status(row: dict[str, Any] | None) -> dict[str, Any]:
    endpoint = f"{notification_service.base_url()}/api/v1/ci/runs"
    return {
        "enabled": bool(row),
        "name": (row or {}).get("name") or "",
        "tokenPrefix": (row or {}).get("token_prefix") or "",
        "projectId": (row or {}).get("project_id"),
        "lastUsedAt": (row or {}).get("last_used_at"),
        "createdAt": (row or {}).get("created_at"),
        "baseUrl": notification_service.base_url(),
        "endpoint": endpoint,
        "events": [
            {"value": key, "label": label}
            for key, label in notification_service.EVENT_LABELS.items()
            if key in notification_service.SUBSCRIBABLE_EVENTS
        ],
    }


@bp.get("/ci")
def get_ci_settings() -> Response:
    return ok(_ci_status(CiTokenRepository(db()).get_active()))


@bp.post("/ci/token")
def issue_ci_token() -> Response:
    """生成 CI 令牌；明文只在本次响应里返回一次，库里只留哈希与前缀。"""
    conn = db()
    data = body()
    name = optional_str(data.get("name"), "name", 100) or "CI 触发令牌"
    project_id = optional_int(data.get("projectId"), "projectId")
    if project_id is not None:
        exists = conn.execute(
            "SELECT 1 FROM biz_project WHERE id = ? AND is_deleted = 0", (project_id,)
        ).fetchone()
        if exists is None:
            raise AppError(ErrorCode.PARAM_ERROR, "绑定的项目不存在或已删除")

    token = "aitp_" + secrets.token_urlsafe(32)
    repo = CiTokenRepository(conn)
    repo.revoke_all()
    new_id = repo.insert({
        "name": name,
        "token_prefix": token[:12],
        "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "project_id": project_id,
        "enabled": 1,
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_CI_TOKEN, new_id,
          name, {"projectId": project_id})
    payload = _ci_status(repo.get_active())
    payload["token"] = token
    return ok(payload)


@bp.delete("/ci/token")
def revoke_ci_token() -> Response:
    conn = db()
    revoked = CiTokenRepository(conn).revoke_all()
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_REVOKE, audit_service.RESOURCE_CI_TOKEN, None, "",
          {"revoked": revoked})
    return ok({"revoked": revoked, "enabled": False})


# ---------------- Webhook 通知 ----------------

def _webhook_url(raw: Any) -> str:
    value = require_str(raw, "webhookUrl", 1000)
    parsed = urlparse(value)
    if parsed.scheme not in ("http", "https") or not parsed.netloc:
        raise AppError(ErrorCode.PARAM_ERROR, "Webhook 地址必须是 http:// 或 https:// 开头")
    return value


def _events_of(raw: Any, fallback: list[str] | None = None) -> list[str]:
    source = fallback if raw is None else raw
    if not isinstance(source, list):
        raise AppError(ErrorCode.PARAM_ERROR, "events 必须是数组")
    events = [str(item) for item in source]
    unknown = [item for item in events if item not in notification_service.SUBSCRIBABLE_EVENTS]
    if unknown:
        raise AppError(ErrorCode.PARAM_ERROR, f"不支持的事件：{'、'.join(unknown)}")
    return list(dict.fromkeys(events))


def _check_project(conn: Any, project_id: int | None) -> None:
    if project_id is None:
        return
    row = conn.execute(
        "SELECT 1 FROM biz_project WHERE id = ? AND is_deleted = 0", (project_id,)
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.PARAM_ERROR, "项目不存在或已删除")


@bp.get("/notifications")
def list_notifications() -> Response:
    conn = db()
    rows = NotificationChannelRepository(conn).list_all(None, keyword_of() or None, limit=200)
    return ok({
        "list": [channel_summary(row) for row in rows],
        "eventOptions": [
            {"value": key, "label": label}
            for key, label in notification_service.EVENT_LABELS.items()
            if key in notification_service.SUBSCRIBABLE_EVENTS
        ],
    })


@bp.post("/notifications")
def create_notification() -> Response:
    conn = db()
    data = body()
    name = require_str(data.get("name"), "name", 100)
    project_id = optional_int(data.get("projectId"), "projectId")
    _check_project(conn, project_id)
    repo = NotificationChannelRepository(conn)
    new_id = repo.insert({
        "name": name,
        "project_id": project_id,
        "webhook_url": _webhook_url(data.get("webhookUrl")),
        "events_json": events_json(_events_of(data.get("events"), [])),
        "enabled": 1 if data.get("enabled", True) else 0,
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_NOTIFICATION, new_id,
          name, {"projectId": project_id})
    return ok(channel_summary(repo.get(new_id) or {}))


@bp.put("/notifications/<pk>")
def update_notification(pk: str) -> Response:
    conn = db()
    repo = NotificationChannelRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "通知渠道不存在")
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 100)
    if "projectId" in data:
        project_id = optional_int(data["projectId"], "projectId")
        _check_project(conn, project_id)
        payload["project_id"] = project_id
    if "webhookUrl" in data:
        payload["webhook_url"] = _webhook_url(data["webhookUrl"])
    if "events" in data:
        current = channel_summary(row)["events"]
        payload["events_json"] = events_json(_events_of(data["events"], current))
    if "enabled" in data:
        payload["enabled"] = 1 if data["enabled"] else 0
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_NOTIFICATION, target,
          str(payload.get("name", row.get("name", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(channel_summary(repo.get(target) or {}))


@bp.delete("/notifications/<pk>")
def delete_notification(pk: str) -> Response:
    conn = db()
    repo = NotificationChannelRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "通知渠道不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_NOTIFICATION, target,
          row.get("name", ""))
    return ok({"id": target, "deleted": True})


@bp.post("/notifications/<pk>/test")
def test_notification(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    if NotificationChannelRepository(conn).get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "通知渠道不存在")
    result = notification_service.send_test(conn, target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_NOTIFICATION, target,
          "测试通知", {"httpStatus": result.get("httpStatus")},
          result="success" if result.get("success") else "failed")
    return ok(result)


@bp.get("/notification-logs")
def list_notification_logs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = query_filters({
        "event": "event",
        "status": "status",
        "channelId": "channel_id",
        "projectId": "project_id",
    })
    page = NotificationLogRepository(conn).list(
        filters, keyword_of() or None, page_num=page_num, page_size=page_size
    )
    payload = page_payload(page)
    payload["list"] = [log_summary(row) for row in payload["list"]]
    payload["eventOptions"] = [
        {"value": key, "label": label}
        for key, label in notification_service.EVENT_LABELS.items()
    ]
    return ok(payload)


# ---------------- 数据备份 ----------------

@bp.get("/backups")
def list_backups() -> Response:
    conn = db()
    return ok({
        "list": backup_service.list_backups(),
        "bundles": backup_service.list_bundles(),
        "dir": str(backup_service.backup_dir()),
        "dbPath": str(current_app.config["AITP_DB_PATH"]),
        "auto": backup_schedule.status(conn),
    })


@bp.put("/backups/auto")
def update_auto_backup() -> Response:
    """保存自动备份计划；开关/频率/时间/星期变化时重算下次执行时间。"""
    conn = db()
    values = backup_schedule.validate_settings(body())
    plan = backup_schedule.save_settings(conn, values)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_SYSTEM_CONFIG, None,
          "自动备份计划", values)
    commit(conn)
    return ok(plan)


@bp.post("/backups/auto/run")
def run_auto_backup_now() -> Response:
    """立即按自动备份策略执行一次；打包在任务队列里完成。"""
    conn = db()
    task = backup_schedule.submit_now(conn)
    audit(conn, audit_service.AUDIT_ACTION_BACKUP, audit_service.RESOURCE_DB_BACKUP, None,
          "手动触发自动备份", {"manualTrigger": True, "taskId": task.get("id")})
    commit(conn)
    return ok(task)


def _notify_backup_failure(conn: Any, message: str) -> None:
    """备份失败时发通知；通知本身失败不能盖住原始备份错误。"""
    try:
        notification_service.notify_backup_failed(conn, message)
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 通知是附加动作
        conn.rollback()
        current_app.logger.warning("备份失败通知未发出：%s", exc)


@bp.post("/backups")
def create_backup() -> Response:
    conn = db()
    try:
        info = backup_service.create(conn)
    except Exception as exc:
        _notify_backup_failure(conn, str(exc))
        raise
    audit(conn, audit_service.AUDIT_ACTION_BACKUP, audit_service.RESOURCE_DB_BACKUP, None,
          str(info["name"]), {"sizeBytes": info["sizeBytes"]})
    return ok(info)


@bp.post("/backups/bundle")
def create_bundle_backup() -> Response:
    """整包备份：库 + 附件 / 脚本 / 生成物 / 密钥 / 知识库正文一起存走。"""
    conn = db()
    try:
        info = backup_service.create_bundle(conn)
    except Exception as exc:
        _notify_backup_failure(conn, str(exc))
        raise
    audit(conn, audit_service.AUDIT_ACTION_BACKUP, audit_service.RESOURCE_DB_BACKUP, None,
          str(info["name"]), {"sizeBytes": info["sizeBytes"], "files": info.get("files")})
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
    path = (
        backup_service.resolve_existing_bundle(name)
        if name.endswith(".zip")
        else backup_service.resolve_existing(name)
    )
    return send_file(path, as_attachment=True, download_name=path.name, mimetype="application/octet-stream")


@bp.post("/backups/<name>/restore")
def restore_backup(name: str) -> Response:
    """恢复会覆盖当前库，必须回传 confirm 且内容与备份文件名一致。"""
    conn = db()
    data = body()
    confirm = optional_str(data.get("confirm"), "confirm", 200) or ""
    if confirm != name:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "请输入备份文件名以确认恢复")
    if name.endswith(".zip"):
        # 整包除了库还会覆盖附件与正文目录，多加一句确认
        if str(data.get("confirmFiles") or "") != "覆盖本机文件":
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "整包恢复会覆盖附件与知识库正文，请输入「覆盖本机文件」")
        result = backup_service.restore_bundle(conn, name)
    else:
        result = backup_service.restore(conn, name)
    audit(conn, audit_service.AUDIT_ACTION_RESTORE, audit_service.RESOURCE_DB_BACKUP, None, name, result)
    return ok(result)


@bp.delete("/backups/<name>")
def delete_backup(name: str) -> Response:
    conn = db()
    removed = backup_service.remove(name)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_DB_BACKUP, None, removed)
    return ok({"name": removed, "deleted": True})
