"""设置 API：AI 供应商档案、系统配置、首页卡片定制、审计日志。"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

from flask import Blueprint, Response, current_app, request, send_file

from ..ai import cost as ai_cost
from ..common.constants import AI_DEFAULT_TIMEOUT, PLANS, PROVIDERS
from ..common.env_scope import normalize_scope
from ..common.secret_store import get_store
from ..common.time_utils import utcnow_iso
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
    token_summary,
)
from ..repositories.sys_config_repo import SysConfigRepository
from ..services import (
    ai_check,
    app_scenario_service,
    audit_service,
    backup_schedule,
    backup_service,
    home_card_service,
    mcp_service,
    mcp_token_service,
    notification_service,
)
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
    "backup.include_bug_screenshots": (
        "可填 true 或 false，默认 false。true：整包备份、项目资产包与留存归档都包含缺陷截图；"
        "false：三处都不包含（截图仍保留在 data/bug-screenshots/<缺陷编号>/ 下，不受留存清理）。"
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
    "automation.auto_bug": (
        "可填 true 或 false，默认 false。true：场景自动化执行未通过、自动化脚本执行失败/超时/异常时，"
        "自动按失败步骤或失败用例生成一条 P2 缺陷，描述里带执行编号与错误证据；"
        "false：只在执行页手动「转缺陷」。"
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


#: 档案可写字段白名单。GAP-30 的三档单价随档案一起维护（列来自迁移 047），
#: 这样「档案接口」与「/lab/cost/prices」读的是同一列，不存在第二套口径。
PROFILE_WRITE_FIELDS = {
    "name", "provider", "protocol", "base_url", "model", "temperature",
    "max_tokens", "timeout", "is_default", "is_active", "sort_order", "priority",
    "is_local", "extra",
    "price_input_micros", "price_output_micros", "price_currency",
    # Token Plan（迁移 060）：计费模式与套餐额度随档案维护
    "billing_mode", "token_quota",
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


def _price_micros(value: Any, field: str, default: int = 0) -> int:
    """档案 token 单价：非负整数微元 / 1000 token（GAP-30 口径，0 = 未配价）。"""
    micros = require_int(value, field, default)
    if micros < 0:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 不能为负数；0 表示未配价")
    return micros


def _price_currency(value: Any) -> str:
    """币种归一：白名单外一律回落 CNY，与 ai.cost 的折算口径同源。"""
    return ai_cost.normalize_currency(value)


def _billing_mode(value: Any) -> str:
    """Token Plan：计费模式归一，白名单外回落 api（与 ai.cost 同源）。"""
    return ai_cost.normalize_billing_mode(value)


def _token_quota(value: Any, field: str = "tokenQuota", default: int = 0) -> int:
    """套餐总 token 额度：非负整数；0 = 未设额度（只统计不扣减）。"""
    quota = require_int(value, field, default)
    if quota < 0:
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 不能为负数；0 表示未设额度")
    return quota


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
        plan = PLANS.get(name)
        result.append({
            "name": spec.name,
            "label": spec.label,
            "protocol": spec.protocol,
            "defaultBaseUrl": spec.default_base_url,
            "defaultModel": spec.default_model,
            "requiresApiKey": spec.requires_api_key,
            "isLocal": spec.is_local,
            "envKeys": list(spec.env_key),
            "plan": None if plan is None else {
                "label": plan.label,
                "baseUrl": plan.base_url,
                "protocol": plan.protocol,
                "defaultModel": plan.default_model,
                "keyHint": plan.key_hint,
            },
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
    is_local = 1 if (data.get("isLocal") if data.get("isLocal") is not None
                     else (spec.name == "ollama" if spec else False)) else 0
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
        # GAP-30：单价随档案维护，没填就是未配价（0）——成本只报 token 不报钱
        # 本地模型不产生云服务费，单价与套餐额度一律不计费（不接受传值）
        "price_input_micros": 0 if is_local else _price_micros(data.get("priceInputMicros"), "priceInputMicros"),
        "price_output_micros": 0 if is_local else _price_micros(data.get("priceOutputMicros"), "priceOutputMicros"),
        "price_currency": _price_currency(data.get("priceCurrency")),
        # Token Plan（迁移 060）：计费模式与套餐额度随档案创建
        "billing_mode": "api" if is_local else _billing_mode(data.get("billingMode")),
        "token_quota": 0 if is_local else _token_quota(data.get("tokenQuota")),
        # 迁移 084：是否支持图片输入，默认开启；关闭后带图请求会跳过图片并提示
        "supports_vision": 0 if data.get("supportsVision") in (False, 0, "0", "false", "False") else 1,
        "timeout": require_float(data.get("timeout"), "timeout", _default_timeout(conn)),
        "is_default": 1 if data.get("isDefault") else 0,
        "is_active": 1 if data.get("isActive", True) else 0,
        "sort_order": require_int(data.get("sortOrder"), "sortOrder", 0),
        "priority": require_int(data.get("priority"), "priority", 0),
        "is_local": is_local,
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
    # GAP-30 档案单价：没传就保持现值，传了就必须是非负整数微元（口径同 /lab/cost/prices）
    for key in ("price_input_micros", "price_output_micros"):
        camel = _camel_of(key)
        if key in data:
            payload[key] = _price_micros(data[key], key)
        elif camel in data:
            payload[key] = _price_micros(data[camel], camel)
    if "price_currency" in data:
        payload["price_currency"] = _price_currency(data["price_currency"])
    elif "priceCurrency" in data:
        payload["price_currency"] = _price_currency(data["priceCurrency"])
    # Token Plan（迁移 060）：计费模式与套餐额度，没传保持现值
    if "billing_mode" in data or "billingMode" in data:
        payload["billing_mode"] = _billing_mode(data.get("billing_mode", data.get("billingMode")))
    if "token_quota" in data or "tokenQuota" in data:
        payload["token_quota"] = _token_quota(data.get("token_quota", data.get("tokenQuota")))
    # 迁移 084：视觉能力开关，没传保持现值
    if "supports_vision" in data or "supportsVision" in data:
        value = data.get("supports_vision", data.get("supportsVision"))
        payload["supports_vision"] = 0 if value in (False, 0, "0", "false", "False") else 1
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
    # 本地模型跑在自己机器上，不产生云服务费：单价与套餐额度对它都不成立。
    # 这里按「改完之后的档案」判定本地，云端转本地时顺带清掉历史残留的计费值。
    if ai_cost.is_local_profile({**dict(row), **payload}):
        payload["price_input_micros"] = 0
        payload["price_output_micros"] = 0
        payload["billing_mode"] = ai_cost.BILLING_API
        payload["token_quota"] = 0
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

def _iso_utc(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def _expires_at_of(data: dict[str, Any]) -> str | None:
    """把 expiresInDays / expiresAt 归一成 UTC ISO 到期时间；都没给就是不过期。

    优先级：显式 expiresAt > expiresInDays。空或非法一律 PARAM_ERROR。
    """
    raw_days = optional_int(data.get("expiresInDays"), "expiresInDays")
    raw_at = optional_str(data.get("expiresAt"), "expiresAt", 40)
    if raw_at:
        try:
            parsed = datetime.fromisoformat(raw_at.replace("Z", "+00:00"))
        except ValueError:
            raise AppError(ErrorCode.PARAM_ERROR, "expiresAt 必须是合法的 ISO8601 时间") from None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        if parsed.astimezone(timezone.utc) <= datetime.now(timezone.utc):
            raise AppError(ErrorCode.PARAM_ERROR, "expiresAt 必须晚于当前时间")
        return _iso_utc(parsed)
    if raw_days is not None:
        if raw_days <= 0 or raw_days > 3650:
            raise AppError(ErrorCode.PARAM_ERROR, "expiresInDays 需在 1~3650 之间")
        return _iso_utc(datetime.now(timezone.utc) + timedelta(days=raw_days))
    return None


def _ci_status(row: dict[str, Any] | None) -> dict[str, Any]:
    endpoint = f"{notification_service.base_url()}/api/v1/ci/runs"
    summary = token_summary(row) if row else {}
    return {
        "enabled": bool(row),
        "name": (row or {}).get("name") or "",
        "tokenPrefix": (row or {}).get("token_prefix") or "",
        "projectId": (row or {}).get("project_id"),
        "lastUsedAt": (row or {}).get("last_used_at"),
        "createdAt": (row or {}).get("created_at"),
        "expiresAt": summary.get("expiresAt"),
        "revokedAt": summary.get("revokedAt"),
        "expired": summary.get("expired", False),
        "status": summary.get("status") or ("active" if row else "inactive"),
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


@bp.get("/ci/tokens")
def list_ci_tokens() -> Response:
    """近期令牌列表：显示有效期与吊销状态，供设置页排障（不回传任何明文/哈希）。"""
    rows = CiTokenRepository(db()).list_recent(limit=20)
    return ok({"list": [token_summary(row) for row in rows]})


@bp.post("/ci/token")
def issue_ci_token() -> Response:
    """生成 CI 令牌；明文只在本次响应里返回一次，库里只留哈希与前缀。

    可选有效期：expiresInDays（1~3650 天）或 expiresAt（ISO8601）；都不传则不过期。
    """
    conn = db()
    data = body()
    name = optional_str(data.get("name"), "name", 100) or "CI 触发令牌"
    project_id = optional_int(data.get("projectId"), "projectId")
    expires_at = _expires_at_of(data)
    if project_id is not None:
        exists = conn.execute(
            "SELECT 1 FROM biz_project WHERE id = ? AND is_deleted = 0", (project_id,)
        ).fetchone()
        if exists is None:
            raise AppError(ErrorCode.PARAM_ERROR, "绑定的项目不存在或已删除")

    token = "aitp_" + secrets.token_urlsafe(32)
    repo = CiTokenRepository(conn)
    repo.revoke_all(utcnow_iso())
    new_id = repo.insert({
        "name": name,
        "token_prefix": token[:12],
        "token_hash": hashlib.sha256(token.encode("utf-8")).hexdigest(),
        "project_id": project_id,
        "enabled": 1,
        "expires_at": expires_at,
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_CI_TOKEN, new_id,
          name, {"projectId": project_id, "expiresAt": expires_at})
    payload = _ci_status(repo.get_active())
    payload["token"] = token
    return ok(payload)


@bp.post("/ci/token/<pk>/revoke")
def revoke_ci_token_one(pk: str) -> Response:
    """按 id 吊销单个令牌；吊销后同一 Token 立即失效（校验会查 revoked_at/enabled）。"""
    conn = db()
    target = path_int(pk)
    repo = CiTokenRepository(conn)
    if repo.get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "CI 令牌不存在")
    revoked = repo.revoke_one(target, utcnow_iso())
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_REVOKE, audit_service.RESOURCE_CI_TOKEN, target,
          "", {"revoked": revoked})
    return ok({"id": target, "revoked": revoked, "enabled": False})


@bp.delete("/ci/token")
def revoke_ci_token() -> Response:
    """一键吊销所有启用中的令牌；enabled 置 0 同时补记 revoked_at，立即生效。"""
    conn = db()
    revoked = CiTokenRepository(conn).revoke_all(utcnow_iso())
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


#: 通知渠道私有配置白名单（密码单独进密钥库，不在此列）
NOTIFICATION_CONFIG_KEYS = ("smtpHost", "smtpPort", "smtpUser", "fromAddr", "toAddrs", "useSsl", "useTls")


def _notification_type(raw: Any, fallback: str = "webhook") -> str:
    if raw is None:
        return fallback
    return notification_service.normalize_channel_type(raw)


def _notification_template(raw: Any) -> dict[str, str]:
    data = raw if isinstance(raw, dict) else {}
    template: dict[str, str] = {}
    for key in ("title", "text"):
        if key in data:
            template[key] = str(data[key] or "")[:4000]
    return template


def _notification_config(raw: Any) -> dict[str, Any]:
    data = raw if isinstance(raw, dict) else {}
    config: dict[str, Any] = {}
    for key in NOTIFICATION_CONFIG_KEYS:
        if key not in data:
            continue
        if key == "smtpPort":
            port = require_int(data[key], "smtpPort")
            if not 1 <= port <= 65535:
                raise AppError(ErrorCode.PARAM_ERROR, "参数 smtpPort 必须在 1-65535 之间")
            config[key] = port
        elif key in ("useSsl", "useTls"):
            config[key] = bool(data[key])
        else:
            config[key] = str(data[key] or "").strip()[:300]
    return config


def _raw_channel_config(data: dict[str, Any]) -> dict[str, Any]:
    value = data.get("config")
    return value if isinstance(value, dict) else {}


def _check_notification_shape(channel_type: str, webhook_url: str, config: dict[str, Any]) -> None:
    if channel_type != "email":
        if not webhook_url:
            raise AppError(ErrorCode.PARAM_ERROR, "Webhook 渠道必须填写地址")
        return
    if not config.get("smtpHost"):
        raise AppError(ErrorCode.PARAM_ERROR, "邮箱渠道必须填写 SMTP 服务器")
    if not (config.get("fromAddr") or config.get("smtpUser")):
        raise AppError(ErrorCode.PARAM_ERROR, "邮箱渠道必须填写发件人")
    if not config.get("toAddrs"):
        raise AppError(ErrorCode.PARAM_ERROR, "邮箱渠道必须填写收件人")


def _save_smtp_password(channel_id: int, password: Any, config: dict[str, Any]) -> None:
    """密码写系统密钥库，config_json 只保留 hasPassword 标记。"""
    if password is None or password == "":
        return
    try:
        get_store().set(notification_service.smtp_secret_ref(channel_id), str(password))
    except Exception as exc:  # noqa: BLE001
        raise AppError(ErrorCode.INTERNAL_ERROR, f"密钥库不可用，邮箱密码未保存：{exc}") from exc
    config["hasPassword"] = True


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
        "channelTypes": [
            {
                "value": key,
                "label": notification_service.CHANNEL_TYPE_LABELS[key],
                "defaultTemplate": notification_service.default_template(key),
                "needsWebhook": key != "email",
            }
            for key in notification_service.CHANNEL_TYPES
        ],
        "templateVariables": list(notification_service.TEMPLATE_VARIABLES),
        "emailHint": "测试邮箱可填 yichengrenhg@126.com；密码保存在本机密钥库，不回传。",
    })


@bp.post("/notifications/preview")
def preview_notification() -> Response:
    """模板预览：用示例数据渲染标题/正文与平台投递体，不真正发送。"""
    conn = db()
    data = body()
    channel_type = _notification_type(data.get("channelType"))
    template = _notification_template(data.get("template"))
    if not template:
        template = notification_service.default_template(channel_type)
    payload = notification_service.build_payload(
        conn,
        event=notification_service.EVENT_TEST,
        title="示例：登录模块回归失败 3 条",
        project_id=optional_int(data.get("projectId"), "projectId"),
        data={"runName": "示例回归轮次", "failed": 3, "passRate": 87.5,
              "message": "这是一条模板预览，不会真正发送。"},
        link="/settings/ci",
    )
    subject, text, body_payload = notification_service.render_channel(
        {"channel_type": channel_type, "template_json": dumps(template)}, payload
    )
    return ok({
        "channelType": channel_type,
        "subject": subject,
        "text": text,
        "body": body_payload,
    })


@bp.post("/notifications")
def create_notification() -> Response:
    conn = db()
    data = body()
    name = require_str(data.get("name"), "name", 100)
    project_id = optional_int(data.get("projectId"), "projectId")
    _check_project(conn, project_id)
    channel_type = _notification_type(data.get("channelType"))
    webhook_url = "" if channel_type == "email" else _webhook_url(data.get("webhookUrl"))
    config = _notification_config(data.get("config"))
    template = _notification_template(data.get("template"))
    _check_notification_shape(channel_type, webhook_url, config)
    raw_config = _raw_channel_config(data)
    repo = NotificationChannelRepository(conn)
    new_id = repo.insert({
        "name": name,
        "project_id": project_id,
        "webhook_url": webhook_url,
        "channel_type": channel_type,
        "template_json": dumps(template),
        "config_json": dumps(config),
        "events_json": events_json(_events_of(data.get("events"), [])),
        "enabled": 1 if data.get("enabled", True) else 0,
    })
    _save_smtp_password(new_id, raw_config.get("password"), config)
    repo.update(new_id, {"config_json": dumps(config)})
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_NOTIFICATION, new_id,
          name, {"projectId": project_id, "channelType": channel_type})
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
    current = channel_summary(row)
    channel_type = _notification_type(data.get("channelType"), current["channelType"])
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 100)
    if "projectId" in data:
        project_id = optional_int(data["projectId"], "projectId")
        _check_project(conn, project_id)
        payload["project_id"] = project_id
    webhook_url = current.get("webhookUrl") or ""
    if "webhookUrl" in data:
        webhook_url = "" if channel_type == "email" else _webhook_url(data["webhookUrl"])
        payload["webhook_url"] = webhook_url
    if "channelType" in data:
        payload["channel_type"] = channel_type
    config = dict(current.get("config") or {})
    config.pop("hasPassword", None)
    if "config" in data:
        config.update(_notification_config(data.get("config")))
        payload["config_json"] = dumps(config)
    if "template" in data:
        payload["template_json"] = dumps(_notification_template(data.get("template")))
    if "events" in data:
        payload["events_json"] = events_json(_events_of(data["events"], current["events"]))
    if "enabled" in data:
        payload["enabled"] = 1 if data["enabled"] else 0
    _check_notification_shape(channel_type, webhook_url, config)
    raw_config = _raw_channel_config(data)
    _save_smtp_password(target, raw_config.get("password"), config)
    if "config" in data or raw_config.get("password"):
        payload["config_json"] = dumps(config)
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
    try:
        get_store().delete(notification_service.smtp_secret_ref(target))
    except Exception as exc:  # noqa: BLE001 - 密钥清理失败不影响停用
        current_app.logger.warning("通知渠道 %s 的 SMTP 密码清理失败：%s", target, exc)
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
    except Exception as exc:  # noqa: BLE001
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
    except Exception as exc:  # noqa: BLE001
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


# --------------------------------------------------------------------------- App 执行配置


@bp.get("/app-driver")
def get_app_driver() -> Response:
    conn = db()
    config = app_scenario_service.driver_config(conn)
    return ok({
        "adbCommand": config["adbCommand"],
        "deviceSerial": config["deviceSerial"],
        "commandTimeout": config["timeout"],
        "hint": "可填 adb，也可指向自定义可执行文件；设备序列号留空时使用唯一在线设备。",
    })


@bp.put("/app-driver")
def save_app_driver() -> Response:
    conn = db()
    data = body()
    command = require_str(data.get("adbCommand"), "adbCommand", 300)
    serial = optional_str(data.get("deviceSerial"), "deviceSerial", 120) or ""
    timeout = require_int(data.get("commandTimeout") or 20, "commandTimeout")
    if not 3 <= timeout <= 120:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 commandTimeout 必须在 3-120 之间")
    repo = SysConfigRepository(conn)
    repo.set_value("app.adb_command", command, "string", "App 场景执行的 adb 命令")
    repo.set_value("app.device_serial", serial, "string", "App 场景默认设备序列号")
    repo.set_value("app.command_timeout", str(timeout), "int", "App 场景单条 adb 命令超时秒数")
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_SYSTEM_CONFIG, None,
          "App 执行配置", {"adbCommand": command, "deviceSerial": serial})
    return ok({"adbCommand": command, "deviceSerial": serial, "commandTimeout": timeout})


@bp.post("/app-driver/probe")
def probe_app_driver() -> Response:
    conn = db()
    data = body()
    overrides = {
        "adbCommand": data.get("adbCommand"),
        "deviceSerial": data.get("deviceSerial"),
    }
    result = app_scenario_service.probe(conn, overrides)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_SYSTEM_CONFIG, None,
          "探测 App 设备", {"ok": result["ok"], "devices": result["devices"]})
    return ok(result)


# --------------------------------------------------------------------------- MCP 服务端


def _truthy(value: Any) -> bool:
    return str(value or "").strip().lower() in ("1", "true", "on", "yes")


@bp.get("/mcp")
def get_mcp_settings() -> Response:
    conn = db()
    repo = SysConfigRepository(conn)
    active, token_required = mcp_token_service.state(conn)
    tokens = [mcp_token_service.to_public(row) for row in mcp_token_service.list_tokens(conn)]
    return ok({
        "enabled": _truthy(repo.get_value("mcp.enabled", "0")),
        "allowWrite": _truthy(repo.get_value("mcp.allow_write", "0")),
        "hasToken": active is not None,
        "tokenRequired": token_required,
        "tokenPreview": str(active["preview"]) if active is not None else "",
        "lastUsedAt": str(active["last_used_at"] or "") if active is not None else "",
        "lastClient": str(active["last_client"] or "") if active is not None else "",
        "endpoint": f"{request.host_url.rstrip('/')}/api/v1/mcp",
        "protocol": "JSON-RPC 2.0 / MCP 2024-11-05",
        "tools": mcp_service.tool_definitions(),
        "tokens": tokens,
        "notes": [
            "只接受本机回环请求；配置令牌后需在请求头携带 Bearer。",
            "令牌明文只在生成或轮换时展示一次，关闭后只能重新轮换。",
            "写工具只落缺陷待审区，需在 AITP 中人工确认后才生成正式缺陷。",
        ],
    })


@bp.put("/mcp")
def save_mcp_settings() -> Response:
    conn = db()
    data = body()
    repo = SysConfigRepository(conn)
    if "enabled" in data:
        repo.set_value("mcp.enabled", "1" if data["enabled"] else "0", "bool", "是否开放 MCP 服务端")
    if "allowWrite" in data:
        repo.set_value("mcp.allow_write", "1" if data["allowWrite"] else "0", "bool",
                       "MCP 是否允许写操作（只落待审区）")
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_SYSTEM_CONFIG, None,
          "MCP 服务设置", {"fields": sorted(item for item in ("enabled", "allowWrite") if item in data)})
    return get_mcp_settings()


@bp.post("/mcp/token")
def create_mcp_token() -> Response:
    """生成或轮换 MCP 令牌；明文只在此响应里返回一次。"""
    conn = db()
    raw, row, rotated = mcp_token_service.create_token(conn)
    audit(
        conn,
        audit_service.AUDIT_ACTION_UPDATE if rotated else audit_service.AUDIT_ACTION_GENERATE,
        audit_service.RESOURCE_MCP_TOKEN,
        int(row["id"]),
        "轮换 MCP 令牌" if rotated else "生成 MCP 令牌",
        {"preview": row["preview"], "rotatedFrom": row["rotated_from"]},
    )
    return ok({"token": raw, "tokenPreview": row["preview"], "id": row["id"], "rotated": rotated})


@bp.post("/mcp/tokens/<pk>/disable")
def disable_mcp_token(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = mcp_token_service.disable_token(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_REVOKE, audit_service.RESOURCE_MCP_TOKEN, target,
          "停用 MCP 令牌", {"preview": row["preview"]})
    return get_mcp_settings()


@bp.post("/mcp/tokens/<pk>/enable")
def enable_mcp_token(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = mcp_token_service.enable_token(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_MCP_TOKEN, target,
          "启用 MCP 令牌", {"preview": row["preview"]})
    return get_mcp_settings()


@bp.delete("/mcp/tokens/<pk>")
def delete_mcp_token(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    row = mcp_token_service.delete_token(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_MCP_TOKEN, target,
          "删除 MCP 令牌", {"preview": row["preview"]})
    return get_mcp_settings()


@bp.get("/mcp/logs")
def list_mcp_logs() -> Response:
    conn = db()
    rows = conn.execute("""
        SELECT id, method, tool, status, duration_ms, detail, client, created_at
        FROM sys_mcp_call_log ORDER BY id DESC LIMIT 30
    """).fetchall()
    return ok({"list": [{
        "id": row["id"], "method": row["method"], "tool": row["tool"],
        "status": row["status"], "durationMs": row["duration_ms"],
        "detail": row["detail"], "client": row["client"], "createdAt": row["created_at"],
    } for row in rows]})
