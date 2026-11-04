"""AI 自检、生效状态与模型列表。

自检是真实调用：不走本地规则、不做模拟，失败时按错误类型给出可操作的中文提示。
未保存的配置也能直接参与自检；密钥只在内存里用一次，不落库、不回传前端。
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from ..ai import resolver
from ..ai.client import AIClient, degrade_reason_of
from ..ai.errors import (
    AIAuthFailed,
    AICredentialMissing,
    AIKeyDecryptFailed,
    AIModelNotFound,
    AITimeout,
    AIUnreachable,
)
from ..ai.types import Message, ResolvedAIConfig
from ..common.constants import (
    EGRESS_NONE,
    SOURCE_DB,
    SOURCE_DEFAULT,
    SOURCE_DRAFT,
    SOURCE_ENV,
    SOURCE_NONE,
)
from ..errors import AppError, ErrorCode
from ..repositories.ai_profile_repo import AIProfileRepository

SOURCE_LABELS: dict[str, str] = {
    SOURCE_DB: "工作台配置",
    SOURCE_ENV: "环境变量",
    SOURCE_DEFAULT: "内置默认",
    SOURCE_NONE: "未配置",
    SOURCE_DRAFT: "待保存配置",
}

PROBE_PROMPT = "请只回复两个字：就绪"
REPLY_LIMIT = 500

# 请求字段 → 档案字段（档案表用的是下划线列名）
_OVERRIDE_COLUMNS: tuple[tuple[str, str], ...] = (
    ("provider", "provider"),
    ("protocol", "protocol"),
    ("baseUrl", "base_url"),
    ("model", "model"),
)
_NUMERIC_OVERRIDES: tuple[tuple[str, str, type[Any]], ...] = (
    ("temperature", "temperature", float),
    ("maxTokens", "max_tokens", int),
    ("timeout", "timeout", float),
)
# 档案字段 → resolver 记录的来源键
_SOURCE_KEYS: tuple[tuple[str, str], ...] = (
    ("provider", "provider"),
    ("base_url", "baseUrl"),
    ("model", "model"),
)


def _text(params: dict[str, Any], key: str) -> str:
    value = params.get(key)
    if value is None:
        return ""
    return str(value).strip()


def _overrides(params: dict[str, Any]) -> dict[str, Any]:
    """把请求里的未保存配置转成档案列名。"""
    result: dict[str, Any] = {}
    for key, column in _OVERRIDE_COLUMNS:
        value = _text(params, key)
        if value:
            result[column] = value
    # 未保存的「启用本地 Key」也要参与自检：变量名与作用域随草稿传过来
    if "useEnvKey" in params:
        result["use_env_key"] = 1 if params.get("useEnvKey") else 0
    if "envKeyName" in params:
        result["env_key_name"] = _text(params, "envKeyName")
    if "envKeyScope" in params:
        result["env_key_scope"] = _text(params, "envKeyScope") or "user"
    for key, column, caster in _NUMERIC_OVERRIDES:
        raw = params.get(key)
        if raw is None or raw == "":
            continue
        try:
            result[column] = caster(raw)
        except (TypeError, ValueError):
            raise AppError(ErrorCode.PARAM_ERROR, f"参数 {key} 必须是数字") from None
    return result


def _key_path() -> Path | None:
    try:
        from flask import current_app

        raw = current_app.config.get("AITP_SECRET_KEY_PATH")
    except Exception:
        return None
    return Path(raw) if raw else None


def build_config(conn: sqlite3.Connection, params: dict[str, Any]) -> tuple[ResolvedAIConfig, bool]:
    """构造自检/模型列表要用的配置。

    返回 (配置, 是否用了未保存的草稿配置)。
    """
    overrides = _overrides(params)
    profile_id = params.get("profileId")
    row: dict[str, Any] | None = None
    if profile_id not in (None, "", 0):
        row = AIProfileRepository(conn).get_by_id(int(profile_id))
        if row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "AI 档案不存在")

    if row is None and not overrides:
        config = resolver.resolve(key_path=_key_path(), require_key=False)
        return config, False

    merged: dict[str, Any] = dict(row or {})
    merged.update(overrides)
    config = resolver.resolve(profile=merged, key_path=_key_path(), require_key=False)

    if overrides:
        for column, source_key in _SOURCE_KEYS:
            if column in overrides:
                config.sources[source_key] = SOURCE_DRAFT

    api_key = _text(params, "apiKey")
    if api_key:
        config.api_key = api_key
        config.sources["apiKey"] = SOURCE_DRAFT
    return config, bool(overrides)


def _hint_of(exc: Exception, config: ResolvedAIConfig) -> str:
    """把异常翻译成用户能直接照做的提示。"""
    if isinstance(exc, AICredentialMissing):
        message = str(exc)
        if "环境变量" in message:
            return f"未读取到本地 Key：{message}"
        return "未配置 AI 凭证：请在「AI 配置」里设置 API Key，或设置环境变量后再试。"
    if isinstance(exc, AIAuthFailed):
        return "鉴权失败：API Key 无效或没有该模型的权限，请重新设置 Key。"
    if isinstance(exc, AIModelNotFound):
        model = config.model or "（未指定模型）"
        if config.is_local:
            return f"模型不存在：{model}。请先在本地模型服务里拉取该模型（ollama pull {model}）。"
        return f"模型不存在：{model}。请确认模型名是否正确，或改用该服务已开放的模型。"
    if isinstance(exc, AITimeout):
        return f"请求超时（{int(config.timeout)} 秒）：可调大超时时间，或换用响应更快的模型。"
    if isinstance(exc, AIKeyDecryptFailed):
        return "密钥解密失败：系统密钥库不可用，请重新设置 API Key。"
    if isinstance(exc, AIUnreachable):
        if config.is_local:
            return (
                f"无法连接本地模型服务（{config.base_url}）："
                "请先启动 Ollama（ollama serve），或确认地址与端口是否正确。"
            )
        return f"无法连接 AI 服务（{config.base_url}）：请检查接口地址与网络连通性。"
    return f"AI 请求失败：{exc}"


def _config_summary(config: ResolvedAIConfig) -> dict[str, Any]:
    return {
        "provider": config.provider,
        "model": config.model,
        "baseUrl": config.base_url,
        "profileId": config.profile_id,
        "profileName": config.profile_name,
        "priority": config.priority,
        "isLocal": config.is_local,
        "locality": config.locality,
        "egress": config.egress,
        "hasApiKey": config.has_api_key,
    }


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """当前生效的 AI 配置、各字段来源层与候选模型清单。"""
    payload: dict[str, Any] = {
        "configured": False,
        "provider": "",
        "model": "",
        "baseUrl": "",
        "profileId": None,
        "profileName": None,
        "priority": 0,
        "isLocal": False,
        "locality": "",
        "egress": EGRESS_NONE,
        "hasApiKey": False,
        "sources": {},
        "sourceLabels": {},
        "candidates": [],
        "error": "",
    }
    try:
        config = resolver.resolve(key_path=_key_path(), require_key=False)
    except Exception as exc:  # noqa: BLE001 - 状态页不应因配置不完整而报错
        payload["error"] = str(exc)
        return payload

    payload.update(_config_summary(config))
    payload["configured"] = bool(config.base_url)
    payload["sources"] = dict(config.sources)
    payload["sourceLabels"] = {
        key: SOURCE_LABELS.get(value, value) for key, value in config.sources.items()
    }

    for candidate in resolver.resolve_candidates(key_path=_key_path(), require_key=False):
        payload["candidates"].append({
            "provider": candidate.provider,
            "model": candidate.model,
            "profileId": candidate.profile_id,
            "profileName": candidate.profile_name,
            "priority": candidate.priority,
            "isLocal": candidate.is_local,
            "hasApiKey": candidate.has_api_key,
            "source": candidate.sources.get("provider", SOURCE_DB),
            "sourceLabel": SOURCE_LABELS.get(candidate.sources.get("provider", SOURCE_DB), ""),
        })
    return payload


def self_check(conn: sqlite3.Connection, params: dict[str, Any]) -> dict[str, Any]:
    """真实调用一次 AI，返回延迟、回复片段、用量与失败原因。"""
    config, draft = build_config(conn, params)
    if config.credential_hint and not config.api_key:
        payload = _config_summary(config)
        payload.update({
            "ok": False,
            "reason": "credential_missing",
            "message": config.credential_hint,
            "hint": config.credential_hint,
            "latencyMs": 0,
            "reply": "",
            "usage": {},
            "draft": draft,
        })
        return payload
    started = time.perf_counter()
    try:
        result = AIClient(config).chat(
            [Message(role="user", content=PROBE_PROMPT)],
            max_tokens=32,
        )
    except Exception as exc:  # noqa: BLE001 - 自检要把失败原因原样回传
        latency = int((time.perf_counter() - started) * 1000)
        payload = _config_summary(config)
        payload.update({
            "ok": False,
            "reason": degrade_reason_of(exc),
            "message": str(exc),
            "hint": _hint_of(exc, config),
            "latencyMs": latency,
            "reply": "",
            "usage": {},
            "draft": draft,
        })
        return payload

    payload = _config_summary(config)
    payload.update({
        "ok": True,
        "reason": "",
        "message": "自检通过",
        "hint": "",
        "latencyMs": result.latency_ms or int((time.perf_counter() - started) * 1000),
        "reply": (result.content or "").strip()[:REPLY_LIMIT],
        "usage": result.usage.as_dict(),
        "draft": draft,
    })
    return payload


def list_models(conn: sqlite3.Connection, params: dict[str, Any]) -> list[dict[str, Any]]:
    """列出服务端的可用模型（Ollama 走 /api/tags，OpenAI 兼容走 /models）。"""
    config, _ = build_config(conn, params)
    models = AIClient(config).list_models()
    return [model.as_dict() for model in models]
