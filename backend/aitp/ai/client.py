"""统一 AI 客户端：按协议分发到适配器，并实现优先级降级。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterator

from ..common.constants import (
    DEGRADE_AUTH_FAILED,
    DEGRADE_KEY_UNREADABLE,
    DEGRADE_MODEL_MISSING,
    DEGRADE_NO_CREDENTIAL,
    DEGRADE_TIMEOUT,
    DEGRADE_UNREACHABLE,
)
from .adapters.base import BaseAdapter
from .adapters.ollama_native import OllamaNativeAdapter
from .adapters.openai_compat import OpenAICompatAdapter
from .errors import (
    AIAuthFailed,
    AICredentialMissing,
    AIError,
    AIKeyDecryptFailed,
    AIModelNotFound,
    AITimeout,
    AIUnreachable,
)
from .types import ChatResult, Message, ModelInfo, ResolvedAIConfig, StreamEvent

ADAPTERS: dict[str, BaseAdapter] = {
    OpenAICompatAdapter.protocol: OpenAICompatAdapter(),
    OllamaNativeAdapter.protocol: OllamaNativeAdapter(),
}


def get_adapter(protocol: str) -> BaseAdapter:
    adapter = ADAPTERS.get(protocol)
    if adapter is None:
        raise ValueError(f"不支持的 AI 协议: {protocol}")
    return adapter


def degrade_reason_of(exc: Exception) -> str:
    """把异常映射为降级原因码。"""
    if isinstance(exc, AICredentialMissing):
        return DEGRADE_NO_CREDENTIAL
    if isinstance(exc, AIAuthFailed):
        return DEGRADE_AUTH_FAILED
    if isinstance(exc, AIUnreachable):
        return DEGRADE_UNREACHABLE
    if isinstance(exc, AITimeout):
        return DEGRADE_TIMEOUT
    if isinstance(exc, AIModelNotFound):
        return DEGRADE_MODEL_MISSING
    if isinstance(exc, AIKeyDecryptFailed):
        return DEGRADE_KEY_UNREADABLE
    if isinstance(exc, AIError):
        return DEGRADE_UNREACHABLE
    return DEGRADE_UNREACHABLE


@dataclass(slots=True)
class DegradeInfo:
    """降级信息：前端据此弹 3 秒提示，不阻塞主流程。"""

    degraded: bool = False
    reason: str = ""
    message: str = ""
    from_config: str = ""
    to_config: str = ""
    attempts: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "degraded": self.degraded,
            "reason": self.reason,
            "message": self.message,
            "fromConfig": self.from_config,
            "toConfig": self.to_config,
            "attempts": self.attempts,
        }


@dataclass(slots=True)
class ChatOutcome:
    result: ChatResult
    config: ResolvedAIConfig
    degrade: DegradeInfo = field(default_factory=DegradeInfo)


class AIClient:
    def __init__(self, config: ResolvedAIConfig):
        self.config = config
        self.adapter = get_adapter(config.protocol)

    def chat(self, messages: list[Message], **opts: Any) -> ChatResult:
        return self.adapter.chat(self.config, messages, **opts)

    def chat_stream(self, messages: list[Message], **opts: Any) -> Iterator[StreamEvent]:
        return self.adapter.chat_stream(self.config, messages, **opts)

    def list_models(self) -> list[ModelInfo]:
        return self.adapter.list_models(self.config)


def _label(config: ResolvedAIConfig) -> str:
    return f"{config.profile_name or config.provider}/{config.model or config.provider}"


def chat_with_fallback(
    candidates: list[ResolvedAIConfig],
    messages: list[Message],
    *,
    rules_fallback: Callable[[], ChatResult] | None = None,
    **opts: Any,
) -> ChatOutcome:
    """按优先级依次尝试候选模型；不可用时自动降级到下一个。

    全部候选都不可用时，若提供 rules_fallback 则降级到本地规则（不阻塞主流程），
    否则抛出最后一个错误。
    """
    if not candidates:
        if rules_fallback is None:
            raise AICredentialMissing("没有可用的 AI 配置，请先在「工作台配置 → AI配置」中设置。")
        return ChatOutcome(
            result=rules_fallback(),
            config=ResolvedAIConfig(provider="rules", protocol="", base_url="", model="local-rules"),
            degrade=DegradeInfo(
                degraded=True,
                reason=DEGRADE_NO_CREDENTIAL,
                message="未配置任何 AI 模型，已降级为本地规则结果。",
            ),
        )

    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None

    for index, config in enumerate(candidates):
        try:
            result = AIClient(config).chat(messages, **opts)
        except Exception as exc:  # noqa: BLE001 - 逐个候选降级
            reason = degrade_reason_of(exc)
            attempts.append({
                "config": _label(config),
                "priority": config.priority,
                "ok": False,
                "reason": reason,
                "message": str(exc),
            })
            last_error = exc
            continue

        degrade = DegradeInfo(attempts=attempts)
        if index > 0:
            degrade.degraded = True
            # 最后一次失败就是本次降级的原因；attempts 里只记失败的候选
            last_failed = attempts[-1] if attempts else None
            degrade.reason = str(last_failed["reason"]) if last_failed else DEGRADE_UNREACHABLE
            degrade.from_config = str(last_failed["config"]) if last_failed else ""
            degrade.to_config = _label(config)
            degrade.message = (
                f"首选模型不可用（{degrade.reason}），已按优先级自动降级到 {degrade.to_config}。"
            )
        return ChatOutcome(result=result, config=config, degrade=degrade)

    # 全部候选失败
    if rules_fallback is not None:
        reason = degrade_reason_of(last_error) if last_error else DEGRADE_UNREACHABLE
        return ChatOutcome(
            result=rules_fallback(),
            config=candidates[-1],
            degrade=DegradeInfo(
                degraded=True,
                reason=reason,
                message="所有 AI 模型均不可用，已降级为本地规则结果。",
                from_config=_label(candidates[0]),
                to_config="local-rules",
                attempts=attempts,
            ),
        )
    if last_error is not None:
        raise last_error
    raise AICredentialMissing("没有可用的 AI 配置，请先在「工作台配置 → AI配置」中设置。")


def stream_with_fallback(
    candidates: list[ResolvedAIConfig],
    messages: list[Message],
    **opts: Any,
) -> Iterator[StreamEvent]:
    """流式调用：首个候选在产出任何增量前失败时，自动切到下一个候选。"""
    if not candidates:
        raise AICredentialMissing("没有可用的 AI 配置，请先在「工作台配置 → AI配置」中设置。")

    attempts: list[dict[str, Any]] = []
    last_error: Exception | None = None

    for index, config in enumerate(candidates):
        if index > 0 and attempts:
            # 换了候选模型：先告诉前端"已降级"，前端弹 3 秒提示，不阻塞主流程
            yield StreamEvent(type="degrade", extra={
                "degraded": True,
                "reason": attempts[-1]["reason"],
                "fromConfig": attempts[-1]["config"],
                "toConfig": _label(config),
                "message": (
                    f"首选模型不可用（{attempts[-1]['reason']}），"
                    f"已按优先级自动降级到 {_label(config)}。"
                ),
            })
        produced = False
        try:
            for event in AIClient(config).chat_stream(messages, **opts):
                if event.type == "delta" and event.content:
                    produced = True
                yield event
            return
        except Exception as exc:  # noqa: BLE001 - 逐个候选降级
            reason = degrade_reason_of(exc)
            attempts.append({
                "config": _label(config),
                "priority": config.priority,
                "ok": False,
                "reason": reason,
                "message": str(exc),
            })
            last_error = exc
            if produced:
                # 已经吐过内容，不能换模型重来，直接把错误交给上层
                yield StreamEvent(
                    type="error",
                    error_code=getattr(exc, "code", None),
                    error_message=str(exc),
                    extra={"attempts": attempts},
                )
                return

    if last_error is not None:
        raise last_error
