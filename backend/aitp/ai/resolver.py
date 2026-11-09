"""AI 配置解析：工作台配置(DB) > 环境变量 > 内置默认。

sources 字段记录每一项最终来自哪一层，设置页据此展示「当前生效配置来源」。
候选档案按 priority 降序排列（数据越大越优先），本地模型与云端模型一起排序；
首选不可用时由 client 按顺序降级到下一个候选。

密钥读取顺序（GAP-22 双读期）：档案「本地 Key」（use_env_key）> 引用 api_key_ref（密钥库）
> 旧密文 api_key_enc 兜底 > 进程环境变量。引用优先、旧密文兜底。
兜底分支的删除条件（下一版本移除）：
  1) 正式库与全部开发副本上 `python backend/scripts/migrate_secrets.py --dry-run`
     输出「命中行数：0 / 残留行数：0」，并执行 --apply 收口；
  2) 门禁 `python backend/scripts/check_secret_residue.py` 持续通过至少一个版本；
  3) 一个版本内未再出现「检测到残留 api_key_enc」WARNING（见 _warn_residual）。
满足后删除本文件的 api_key_enc 兜底分支与 migrate/rollback 脚本的兼容说明。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Callable

from ..common.env_scope import normalize_scope, read_scoped_env, scope_label
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
from .cost import normalize_currency, normalize_price_micros
from .errors import AICredentialMissing
from .types import ResolvedAIConfig

_PROFILE_LOADER: Callable[[], list[dict[str, Any]] | None] | None = None
_SETTING_LOADER: Callable[[str, str], str] | None = None

_LOGGER = logging.getLogger(__name__)
# 残留密文告警去重：同一档案一个进程只提醒一次（低频），绝不记录密钥内容
_warned_residual_ids: set[str] = set()


def _warn_residual(profile: dict[str, Any]) -> None:
    """双读兜底命中时的一次性 WARNING：提示运行迁移脚本；不打印任何密钥材料。"""
    pid = str(profile.get("id") if profile.get("id") is not None else "（无 id，环境变量档案）")
    if pid in _warned_residual_ids:
        return
    _warned_residual_ids.add(pid)
    _LOGGER.warning(
        "检测到残留 api_key_enc（AI 档案 id=%s）：历史密钥仍以可解密形态留在库里，"
        "请停服后运行 python backend/scripts/migrate_secrets.py --apply 完成收口。"
        "该兜底分支将在下一版本删除。",
        pid,
    )


def set_profile_loader(loader: Callable[[], list[dict[str, Any]] | None]) -> None:
    """注入数据库档案读取器（返回启用中的档案列表）。"""
    global _PROFILE_LOADER
    _PROFILE_LOADER = loader


def set_setting_loader(loader: Callable[[str, str], str]) -> None:
    """注入系统配置读取器，供降级开关、默认超时等全局项使用。"""
    global _SETTING_LOADER
    _SETTING_LOADER = loader


def _setting(key: str, default: str = "") -> str:
    if _SETTING_LOADER is None:
        return default
    try:
        return _SETTING_LOADER(key, default) or default
    except Exception:  # noqa: BLE001 - 配置读取失败不能阻断 AI 调用
        return default


def degrade_enabled() -> bool:
    """ai.degrade.enabled 关闭时不允许自动切换候选模型。"""
    return _setting("ai.degrade.enabled", "true").strip().lower() not in {"false", "0", "no", "off"}


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
    if provider == "sensenova":
        return _env_first("SENSENOVA_BASE_URL")
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
    if provider == "sensenova":
        return _env_first("SENSENOVA_API_KEY")
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


def _price_of(profile: dict[str, Any] | None, column: str) -> int:
    """GAP-30 档案 token 单价（微元 / 1000 token）。

    脏值（手改库、047 之前的老列）退化成 0=未配价：成本是增强项，不该让调用失败。
    """
    try:
        return normalize_price_micros((profile or {}).get(column), column)
    except ValueError:
        return 0


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
    # 本地 Key 模式优先：Key 由用户自己放在系统环境变量里，库里只记变量名与作用域
    use_env_key = bool((profile or {}).get("use_env_key")) and not spec.is_local
    env_key_name = str((profile or {}).get("env_key_name") or "").strip()
    env_key_scope = normalize_scope((profile or {}).get("env_key_scope"))
    api_key = ""
    if use_env_key:
        if env_key_name:
            api_key = read_scoped_env(env_key_name, env_key_scope)
        sources["apiKey"] = SOURCE_ENV if api_key else SOURCE_NONE
    elif profile and (profile.get("api_key_ref") or "").strip():
        api_key = _load_secret(str(profile["api_key_ref"]))
        sources["apiKey"] = SOURCE_DB if from_db else SOURCE_ENV
    elif profile and (profile.get("api_key_enc") or "").strip():
        # 兼容旧数据（双读期兜底）：历史密文仍在库里时按原逻辑解密。
        # 删除条件见模块 docstring（GAP-22：migrate_secrets 收口一个版本后移除）。
        _warn_residual(profile)
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
    timeout = float(_setting("ai.timeout.default", str(AI_DEFAULT_TIMEOUT)) or AI_DEFAULT_TIMEOUT)
    if profile:
        if profile.get("temperature") is not None:
            temperature = float(profile["temperature"])
        if profile.get("max_tokens") is not None:
            max_tokens = int(profile["max_tokens"])
        if profile.get("timeout") is not None:
            timeout = float(profile["timeout"])

    priority = int((profile or {}).get("priority") or 0)
    is_local = bool((profile or {}).get("is_local")) or spec.is_local

    env_hint = ""
    if use_env_key and spec.requires_api_key and not is_local and not api_key:
        env_hint = (
            f"未读取到环境变量 {env_key_name or '（未填写变量名）'}（{scope_label(env_key_scope)}）："
            "请确认变量名与本机完全一致、值已设置；或在「AI 设置」里关闭「启用本地 Key」，"
            "改用系统密钥库保存的 Key。"
        )
        if require_key:
            raise AICredentialMissing(env_hint)
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
        credential_hint=env_hint,
        # GAP-30：单价跟着档案走，成本折算才能发生在调用当场且不额外查库
        price_input_micros=_price_of(profile, "price_input_micros"),
        price_output_micros=_price_of(profile, "price_output_micros"),
        price_currency=normalize_currency((profile or {}).get("price_currency")),
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
    if not degrade_enabled():
        return candidates[:1]
    return candidates
