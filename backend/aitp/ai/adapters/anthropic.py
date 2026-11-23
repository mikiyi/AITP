"""Anthropic Messages 兼容协议适配器。

覆盖：Anthropic 官方，以及 MiniMax / Kimi / 小米 MiMo 等提供的 Anthropic 兼容端点
（订阅 / Coding 套餐常见）。

与 OpenAI 兼容的差异：
- 鉴权：请求头 ``x-api-key`` + ``anthropic-version``（不是 Bearer）
- 路径：``{base}/v1/messages``
- ``system`` 提为顶层字段，``messages`` 只允许 user / assistant
- 用量字段是 ``input_tokens`` / ``output_tokens``
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from ...common.constants import PROTOCOL_ANTHROPIC
from ..errors import AIError, AITimeout, AIUnreachable, classify_status
from ..types import ChatResult, Message, ModelInfo, ResolvedAIConfig, StreamEvent, Usage
from .base import BaseAdapter

ANTHROPIC_VERSION = "2023-06-01"

#: Anthropic stop_reason → 归一化结束原因（与 OpenAI finish_reason 对齐）
_STOP_MAP = {
    "end_turn": "stop",
    "stop_sequence": "stop",
    "max_tokens": "length",
    "tool_use": "tool_use",
}


def normalize_messages_base(base_url: str) -> str:
    """规整用户填写的 base_url：去掉末尾的 /v1/messages、/messages 或 /v1。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return url
    for suffix in ("/v1/messages", "/messages", "/v1"):
        if url.endswith(suffix):
            return url[: -len(suffix)].rstrip("/")
    return url


class AnthropicAdapter(BaseAdapter):
    protocol = PROTOCOL_ANTHROPIC

    def _endpoint(self, cfg: ResolvedAIConfig, path: str) -> str:
        return f"{normalize_messages_base(cfg.base_url)}{path}"

    def _headers(self, cfg: ResolvedAIConfig) -> dict[str, str]:
        headers = {"Content-Type": "application/json", "anthropic-version": ANTHROPIC_VERSION}
        if cfg.api_key:
            headers["x-api-key"] = cfg.api_key
        return headers

    def _content_blocks(self, message: Message) -> Any:
        """无图返回纯字符串；带图转成 Anthropic content blocks。"""
        if not message.images:
            return message.content
        blocks: list[dict[str, Any]] = [{"type": "text", "text": message.content}]
        for url in message.images:
            if url.startswith("data:"):
                header, _, data = url.partition(",")
                media = header.split(";")[0].replace("data:", "") or "image/png"
                blocks.append({"type": "image",
                               "source": {"type": "base64", "media_type": media, "data": data}})
            else:
                blocks.append({"type": "image", "source": {"type": "url", "url": url}})
        return blocks

    def _split(self, messages: list[Message]) -> tuple[str, list[dict[str, Any]]]:
        """拆出 system（顶层字段），其余整理为 user/assistant 交替。"""
        system_parts: list[str] = []
        out: list[dict[str, Any]] = []
        for message in messages:
            if message.role == "system":
                if message.content:
                    system_parts.append(message.content)
                continue
            role = message.role if message.role in ("user", "assistant") else "user"
            out.append({"role": role, "content": self._content_blocks(message)})
        return "\n\n".join(system_parts), out

    def _body(
        self, cfg: ResolvedAIConfig, messages: list[Message], stream: bool, **opts: Any
    ) -> dict[str, Any]:
        system, msgs = self._split(messages)
        body: dict[str, Any] = {
            "model": cfg.model,
            "max_tokens": opts.get("max_tokens", cfg.max_tokens),
            "temperature": opts.get("temperature", cfg.temperature),
            "messages": msgs,
            "stream": stream,
        }
        if system:
            body["system"] = system
        return body

    # ---------- 非流式 ----------
    def chat(self, cfg: ResolvedAIConfig, messages: list[Message], **opts: Any) -> ChatResult:
        started = time.perf_counter()
        client = self._client_factory(cfg)
        resp = self._request(
            client,
            "POST",
            self._endpoint(cfg, "/v1/messages"),
            headers=self._headers(cfg),
            json=self._body(cfg, messages, False, **opts),
            timeout=self._timeout(cfg),
        )
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text)

        payload = resp.json()
        content = ""
        reasoning = ""
        for block in payload.get("content") or []:
            btype = block.get("type")
            if btype == "text":
                content += block.get("text") or ""
            elif btype in ("thinking", "redacted_thinking"):
                reasoning += block.get("thinking") or ""
        usage = self._parse_usage(payload.get("usage"))
        return ChatResult(
            content=content,
            reasoning=reasoning,
            finish_reason=_STOP_MAP.get(str(payload.get("stop_reason") or ""), "stop"),
            usage=usage,
            model=payload.get("model") or cfg.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ---------- 流式 ----------
    def chat_stream(
        self, cfg: ResolvedAIConfig, messages: list[Message], **opts: Any
    ) -> Iterator[StreamEvent]:
        client = self._client_factory(cfg)
        try:
            with client.stream(
                "POST",
                self._endpoint(cfg, "/v1/messages"),
                headers=self._headers(cfg),
                json=self._body(cfg, messages, True, **opts),
                timeout=self._timeout(cfg),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise classify_status(resp.status_code, resp.text)

                finish_reason = "stop"
                input_tokens = 0
                output_tokens = 0
                for raw in resp.iter_lines():
                    line = raw.strip()
                    if not line or line.startswith("event:"):
                        continue
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    etype = event.get("type")
                    if etype == "message_start":
                        usage = (event.get("message") or {}).get("usage") or {}
                        input_tokens = int(usage.get("input_tokens") or 0)
                    elif etype == "content_block_delta":
                        delta = event.get("delta") or {}
                        if delta.get("type") == "thinking_delta" and delta.get("thinking"):
                            yield StreamEvent(type="reasoning", content=str(delta["thinking"]))
                        elif delta.get("type") == "text_delta" and delta.get("text"):
                            yield StreamEvent(type="delta", content=str(delta["text"]))
                    elif etype == "message_delta":
                        stop = (event.get("delta") or {}).get("stop_reason")
                        if stop:
                            finish_reason = _STOP_MAP.get(str(stop), "stop")
                        usage = event.get("usage") or {}
                        if usage.get("output_tokens") is not None:
                            output_tokens = int(usage.get("output_tokens") or 0)
                    elif etype == "message_stop":
                        break

                yield StreamEvent(type="usage", usage=Usage(
                    prompt_tokens=input_tokens, completion_tokens=output_tokens,
                    total_tokens=input_tokens + output_tokens,
                ))
                yield StreamEvent(type="done", finish_reason=finish_reason, model=cfg.model)
        except httpx.ConnectError as exc:
            raise AIUnreachable(f"无法连接 AI 服务：{exc}") from exc
        except httpx.TimeoutException as exc:
            raise AITimeout(f"AI 请求超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"AI 请求失败：{exc}") from exc

    # ---------- 模型列表 ----------
    def list_models(self, cfg: ResolvedAIConfig) -> list[ModelInfo]:
        client = self._client_factory(cfg)
        resp = self._request(
            client,
            "GET",
            self._endpoint(cfg, "/v1/models"),
            headers=self._headers(cfg),
            timeout=self._timeout(cfg),
        )
        # 部分 Anthropic 兼容端点不提供 /v1/models，返回空列表即可（可手填模型名）
        if resp.status_code in (404, 405):
            return []
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text)
        payload = resp.json()
        result: list[ModelInfo] = []
        for item in payload.get("data") or []:
            if not isinstance(item, dict):
                continue
            model_id = str(item.get("id") or item.get("name") or "")
            if not model_id:
                continue
            result.append(ModelInfo(id=model_id, label=str(item.get("display_name") or model_id),
                                    source="anthropic_models"))
        return result

    @staticmethod
    def _parse_usage(raw: dict[str, Any] | None) -> Usage:
        if not raw:
            return Usage()
        prompt = int(raw.get("input_tokens") or 0)
        completion = int(raw.get("output_tokens") or 0)
        return Usage(prompt_tokens=prompt, completion_tokens=completion,
                     total_tokens=prompt + completion)
