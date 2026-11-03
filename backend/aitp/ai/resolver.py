"""AI 配置解析：工作台配置(DB) > 环境变量 > 内置默认。

sources 字段记录每一项最终来自哪一层，设置页据此展示「当前生效配置来源」。
候选档案按 priority 降序排列（数据越大越优先），本地模型与云端模型一起排序；
首选不可用时由 client 按顺序降级到下一个候选。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any, Callable

from ..common.constants import (
    AI_DEFAULT_TIMEOUT,
    LOCALITY_CLOUD,
    LOCALITY_CUSTOM,
    LOCALITY_LOCAL,
    PROTOCOL_OLLAMA_NATIVE,
    PROTOCOL_OPENAI_COMPAT,
    PROVIDERS,
    SOURCE_DEFAULT,
    SOURCE_DB,
    SOURCE_ENV,
    SOURCE_NONE,
    ProviderSpec,
)
from ..common.security import decrypt
from .errors import AICredentialMissing
from .types import ResolvedAIConfig

_PROFILE_LOADER: Callable[[], list[dict[str, Any]] | None] | None = None


def set_profile_loader(loader: Callable[[], list[dict[str, Any]] | None]) -> None:
    """注入数据库档案读取器（返回启用中的档案列表）。"""
    global _PROFILE_LOADER
    _PROFILE_LOADER = loader


def _load_profiles() -> list[dict[str, Any]]:
    if _PROFILE_LOADER is None:
        return []
    try:
        profiles = _PROFILE_LOADER() or []
    except Exception:
        return []
    return sort_profiles(profiles)


def sort_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """优先级降序；同级时默认档案优先，再按 id 升序。"""
    return sorted(
        profiles,
        key=lambda p: (
            -int(p.get("priority") or 0),
            -int(p.get("is_default") or 0),
            int(p.get("id") or 0),
        ),
    )


def _env_first(*names: str) -> str:
    for name in names:
        value = os.environ.get(name)
        if value and value.strip():
            return value.strip()
    return ""


def _provider_spec(provider: str) -> ProviderSpec:
    return PROVIDERS.get(provider) or PROVIDERS["custom"]


def _env_base_url(provider: str) -> str:
    generic = _env_first("AITP_AI_BASE_URL")
    if generic:
        return generic
    if provider == "ollama":
        return _env_first("OLLAMA_HOST")
    if provider == "deepseek":
        return _env_first("DEEPSEEK_BASE_URL")
    if provider == "dashscope":
        return _env_first("DASHSCOPE_BASE_URL")
    if provider == "openai":
        return _env_first("OPENAI_BASE_URL")
    return ""


def _env_api_key(provider: str) -> str:
    generic = _env_first("AITP_AI_API_KEY")
    if generic:
        return generic
    if provider == "openai":
        return _env_first("OPENAI_API_KEY")
    if provider == "dashscope":
        return _env_first("DASHSCOPE_API_KEY")
    if provider == "deepseek":
        return _env_first("DEEPSEEK_API_KEY")
    return ""


def _env_model(provider: str) -> str:
    generic = _env_first("AITP_AI_MODEL")
    if generic:
        return generic
    if provider == "openai":
        return _env_first("OPENAI_MODEL")
    return ""


def _load_secret(ref: str) -> str:
    """从 OS 密钥库取密钥；读不出来时返回空串，由上层按「未配置凭证」处理。"""
    try:
        from ..common.secret_store import get_store

        return get_store().get(ref)
    except Exception:
        return ""


def _env_profile() -> dict[str, Any]:
    """把环境变量折算成一个虚拟档案，语义上等价于 DB 档案。"""
    provider = (_env_first("AITP_AI_PROVIDER") or "").strip().lower()
    if not provider:
        provider = "ollama" if _env_first("OLLAMA_HOST") else "openai"
    return {"provider": provider, "source": SOURCE_ENV}


def ensure_credential(config: ResolvedAIConfig) -> ResolvedAIConfig:
    """校验凭证是否齐备。resolve() 与 CLI 直构配置共用。"""
    spec = _provider_spec(config.provider)
    # 本机自托管服务（Ollama / LM Studio 等）通常不校验密钥，不应因缺 key 被拦下
    if spec.requires_api_key and not config.api_key and not config.is_local:
        raise AICredentialMissing(
            f"未配置 AI 凭证：{spec.label} 需要 API Key。请在「工作台配置 → AI配置」中填写，"
            f"或设置环境变量 {'/'.join(spec.env_key or ('AITP_AI_API_KEY',))}。"
        )
    if not config.base_url:
        raise AICredentialMissing(f"未配置 AI 接口地址（{spec.label}）。请在「工作台配置 → AI配置」中填写。")
    return config


def _locality(provider: str, is_local: bool) -> str:
    if is_local:
        return LOCALITY_LOCAL
    spec = PROVIDERS.get(provider)
    if spec is None:
        return LOCALITY_CUSTOM
    return LOCALITY_CLOUD if spec.name != "custom" else LOCALITY_CUSTOM


def _build_config(
    profile: dict[str, Any] | None,
    *,
    key_path: Path | None,
    require_key: bool,
) -> ResolvedAIConfig:
    sources: dict[str, str] = {}
    from_db = False
    if profile:
        from_db = profile.get("source") != SOURCE_ENV

    if profile:
        provider = (profile.get("provider") or "").strip() or "custom"
        sources["provider"] = SOURCE_DB if from_db else SOURCE_ENV
    else:
        provider = (_env_first("AITP_AI_PROVIDER") or "").strip().lower()
        sources["provider"] = SOURCE_ENV if provider else SOURCE_DEFAULT
        if not provider:
            provider = "ollama" if _env_first("OLLAMA_HOST") else "openai"

    spec = _provider_spec(provider)

    # ---- base_url ----
    base_url = ""
    if profile and (profile.get("base_url") or "").strip():
        base_url = profile["base_url"].strip()
        sources["baseUrl"] = SOURCE_DB if from_db else SOURCE_ENV
    else:
        base_url = _env_base_url(provider)
        if base_url:
            sources["baseUrl"] = SOURCE_ENV
        else:
            base_url = spec.default_base_url
            sources["baseUrl"] = SOURCE_DEFAULT if base_url else SOURCE_NONE

    # ---- model ----
    model = ""
    if profile and (profile.get("model") or "").strip():
        model = profile["model"].strip()
        sources["model"] = SOURCE_DB if from_db else SOURCE_ENV
    else:
        model = _env_model(provider)
        if model:
            sources["model"] = SOURCE_ENV
        else:
            model = spec.default_model
            sources["model"] = SOURCE_DEFAULT if model else SOURCE_NONE

    # ---- api key ----
    # 数据库只存引用与掩码，密钥本体在 OS 密钥库（DPAPI / 钥匙串）里
    api_key = ""
    if profile and (profile.get("api_key_ref") or "").strip():
        api_key = _load_secret(str(profile["api_key_ref"]))
        sources["apiKey"] = SOURCE_DB if from_db else SOURCE_ENV
    elif profile and (profile.get("api_key_enc") or "").strip():
        # 兼容旧数据：历史密文仍在库里时按原逻辑解密
        if key_path is not None:
            api_key = decrypt(profile["api_key_enc"], key_path)
        sources["apiKey"] = SOURCE_DB if from_db else SOURCE_ENV
    else:
        api_key = _env_api_key(provider)
        sources["apiKey"] = SOURCE_ENV if api_key else SOURCE_NONE

    # ---- protocol ----
    protocol = ""
    if profile and (profile.get("protocol") or "").strip():
        protocol = profile["protocol"].strip()
    elif provider == "ollama":
        protocol = PROTOCOL_OLLAMA_NATIVE
    else:
        protocol = PROTOCOL_OPENAI_COMPAT

    temperature = 0.7
    max_tokens = 4096
    timeout = AI_DEFAULT_TIMEOUT
    if profile:
        if profile.get("temperature") is not None:
            temperature = float(profile["temperature"])
        if profile.get("max_tokens") is not None:
            max_tokens = int(profile["max_tokens"])
        if profile.get("timeout") is not None:
            timeout = float(profile["timeout"])

    priority = int((profile or {}).get("priority") or 0)
    is_local = bool((profile or {}).get("is_local")) or spec.is_local

    if require_key and spec.requires_api_key and not api_key and not is_local:
        raise AICredentialMissing(
            f"未配置 AI 凭证：{spec.label} 需要 API Key。请在「工作台配置 → AI配置」中填写，"
            f"或设置环境变量 {'/'.join(spec.env_key or ('AITP_AI_API_KEY',))}。"
        )

    if not base_url:
        raise AICredentialMissing(f"未配置 AI 接口地址（{spec.label}）。请在「工作台配置 → AI配置」中填写。")

    config = ResolvedAIConfig(
        provider=provider,
        protocol=protocol,
        base_url=base_url,
        model=model,
        api_key=api_key,
        temperature=temperature,
        max_tokens=max_tokens,
        timeout=timeout,
        profile_id=(profile or {}).get("id"),
        profile_name=(profile or {}).get("name"),
        sources=sources,
        priority=priority,
        is_local=is_local,
        locality=_locality(provider, is_local),
    )
    if require_key:
        ensure_credential(config)
    return config


def resolve(
    profile: dict[str, Any] | None = None,
    *,
    key_path: Path | None = None,
    require_key: bool = True,
    use_db: bool = True,
) -> ResolvedAIConfig:
    """解析出本次调用的最终配置。

    profile 为 None 时按优先级取数据库中的最高优先级档案；数据库无档案时回落到环境变量。
    """
    if profile is None:
        profiles = _load_profiles() if use_db else []
        profile = profiles[0] if profiles else _env_profile()
    return _build_config(profile, key_path=key_path, require_key=require_key)


def _is_usable(config: ResolvedAIConfig) -> bool:
    """候选是否真的能发起调用。

    云端模型没有 API Key、或没有接口地址时都不能用；
    本机自托管服务不校验密钥，只看有没有接口地址；
    没有可用候选时上层会给出「未配置 AI 凭证」提示，而不是盲连默认地址。
    """
    spec = _provider_spec(config.provider)
    if spec.requires_api_key and not config.api_key and not config.is_local:
        return False
    return bool(config.base_url)


def resolve_candidates(
    *,
    key_path: Path | None = None,
    require_key: bool = False,
    use_db: bool = True,
) -> list[ResolvedAIConfig]:
    """返回按优先级排序的候选配置（本地模型与云端模型混排）。

    用于降级：首选不可用时依次尝试下一个候选。
    """
    candidates: list[ResolvedAIConfig] = []
    for profile in (_load_profiles() if use_db else []):
        try:
            config = _build_config(profile, key_path=key_path, require_key=require_key)
        except AICredentialMissing:
            continue
        if _is_usable(config):
            candidates.append(config)
    if not candidates:
        try:
            fallback = _build_config(_env_profile(), key_path=key_path, require_key=require_key)
        except AICredentialMissing:
            fallback = None
        if fallback is not None and _is_usable(fallback):
            candidates.append(fallback)
    return candidates
