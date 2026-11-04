"""AI 层的数据类型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterator


@dataclass(slots=True)
class ResolvedAIConfig:
    """一次 AI 调用的最终生效配置。sources 记录每项来自哪一层。"""

    provider: str
    protocol: str
    base_url: str
    model: str
    api_key: str = ""
    temperature: float = 0.7
    max_tokens: int = 4096
    timeout: float = 120.0
    profile_id: int | None = None
    profile_name: str | None = None
    sources: dict[str, str] = field(default_factory=dict)
    priority: int = 0
    is_local: bool = False
    locality: str = "custom"
    #: 凭证缺失时的可操作提示（本地 Key 模式会写明变量名与作用域）
    credential_hint: str = ""

    @property
    def has_api_key(self) -> bool:
        return bool(self.api_key)

    @property
    def egress(self) -> str:
        """输入是否会离开本机。"""
        from ..common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY

        return EGRESS_NONE if self.is_local else EGRESS_THIRD_PARTY

    def as_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "protocol": self.protocol,
            "baseUrl": self.base_url,
            "model": self.model,
            "profileId": self.profile_id,
            "profileName": self.profile_name,
            "sources": self.sources,
            "priority": self.priority,
            "isLocal": self.is_local,
            "locality": self.locality,
            "egress": self.egress,
        }


@dataclass(slots=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    estimated: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.total_tokens,
            "estimated": self.estimated,
        }


@dataclass(slots=True)
class ChatResult:
    content: str = ""
    reasoning: str = ""
    finish_reason: str = "stop"
    usage: Usage = field(default_factory=Usage)
    model: str = ""
    latency_ms: int = 0


@dataclass(slots=True)
class StreamEvent:
    """归一化后的流式事件。"""

    type: str  # delta / reasoning / usage / done / error
    content: str = ""
    usage: Usage | None = None
    finish_reason: str | None = None
    error_code: int | None = None
    error_message: str | None = None
    model: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class ModelInfo:
    id: str
    label: str = ""
    size_bytes: int = 0
    family: str = ""
    parameter_size: str = ""
    quantization: str = ""
    source: str = "remote"

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label or self.id,
            "size": self.size_bytes,
            "family": self.family,
            "parameterSize": self.parameter_size,
            "quantization": self.quantization,
            "source": self.source,
        }


@dataclass(slots=True)
class Message:
    role: str
    content: str
    reasoning: str = ""
    #: 多模态图片（data URL 或 http URL），为空时按纯文本发送
    images: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """OpenAI 兼容格式：带图时 content 变成多段数组。"""
        if not self.images:
            return {"role": self.role, "content": self.content}
        parts: list[dict[str, Any]] = [{"type": "text", "text": self.content}]
        for url in self.images:
            parts.append({"type": "image_url", "image_url": {"url": url}})
        return {"role": self.role, "content": parts}

    def as_ollama_dict(self) -> dict[str, Any]:
        """Ollama 原生格式：图片要单独放 images 数组，且只给纯 base64。"""
        payload: dict[str, Any] = {"role": self.role, "content": self.content}
        images: list[str] = []
        for url in self.images:
            images.append(_data_url_payload(url))
        if images:
            payload["images"] = images
        return payload


def _data_url_payload(url: str) -> str:
    """把 data URL 拆成纯 base64；非 data URL 原样返回。"""
    if not url.startswith("data:"):
        return url
    _, _, payload = url.partition(",")
    return payload


def messages_to_dicts(messages: list[Message]) -> list[dict[str, Any]]:
    return [m.as_dict() for m in messages]


StreamIterator = Iterator[StreamEvent]
