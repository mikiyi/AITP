"""OpenAI 兼容协议适配器。

覆盖：OpenAI / 通义千问兼容模式 / DeepSeek / 自定义兼容接口 /
LM Studio / llama.cpp server。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from ...common.constants import PROTOCOL_OPENAI_COMPAT
from ..errors import classify_status
from ..types import ChatResult, Message, ModelInfo, ResolvedAIConfig, StreamEvent, Usage
from .base import BaseAdapter


def normalize_base_url(base_url: str) -> str:
    """规整用户填写的 base_url，兼容多种书写习惯。"""
    url = (base_url or "").strip().rstrip("/")
    if not url:
        return url
    if url.endswith("/chat/completions"):
        url = url[: -len("/chat/completions")].rstrip("/")
    if not url:
        return url
    # 没有路径段时补 /v1（OpenAI 兼容服务惯例）
    tail = url.split("://", 1)[-1]
    if "/" not in tail:
        url = f"{url}/v1"
    return url


class OpenAICompatAdapter(BaseAdapter):
    protocol = PROTOCOL_OPENAI_COMPAT

    def _endpoint(self, cfg: ResolvedAIConfig, path: str) -> str:
        return f"{normalize_base_url(cfg.base_url)}{path}"

    def _headers(self, cfg: ResolvedAIConfig) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        return headers

    def _body(
        self, cfg: ResolvedAIConfig, messages: list[Message], stream: bool, **opts: Any
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": cfg.model,
            "messages": [m.as_dict() for m in messages],
            "stream": stream,
            "temperature": opts.get("temperature", cfg.temperature),
            "max_tokens": opts.get("max_tokens", cfg.max_tokens),
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        return body

    # ---------- 非流式 ----------
    def chat(self, cfg: ResolvedAIConfig, messages: list[Message], **opts: Any) -> ChatResult:
        started = time.perf_counter()
        client = self._client_factory(cfg)
        resp = self._request(
            client,
            "POST",
            self._endpoint(cfg, "/chat/completions"),
            headers=self._headers(cfg),
            json=self._body(cfg, messages, False, **opts),
            timeout=self._timeout(cfg),
        )
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text)

        payload = resp.json()
        choices = payload.get("choices") or []
        message = (choices[0].get("message") if choices else {}) or {}
        content = message.get("content") or ""
        # 推理模型的思考字段不统一：OpenAI/DeepSeek 用 reasoning_content，商汤日日新用 reasoning
        reasoning = message.get("reasoning_content") or message.get("reasoning") or ""
        finish_reason = (choices[0].get("finish_reason") if choices else None) or "stop"
        usage = self._parse_usage(payload.get("usage"))

        return ChatResult(
            content=content,
            reasoning=reasoning,
            finish_reason=finish_reason,
            usage=usage,
            model=payload.get("model") or cfg.model,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )

    # ---------- 流式 ----------
    def chat_stream(
        self, cfg: ResolvedAIConfig, messages: list[Message], **opts: Any
    ) -> Iterator[StreamEvent]:
        started = time.perf_counter()
        client = self._client_factory(cfg)
        try:
            with client.stream(
                "POST",
                self._endpoint(cfg, "/chat/completions"),
                headers=self._headers(cfg),
                json=self._body(cfg, messages, True, **opts),
                timeout=self._timeout(cfg),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise classify_status(resp.status_code, resp.text)

                finish_reason = "stop"
                usage = Usage()
                for line in resp.iter_lines():
                    if not line:
                        continue
                    line = line.strip()
                    if line.startswith("data:"):
                        line = line[5:].strip()
                    if not line or line == "[DONE]":
                        if line == "[DONE]":
                            break
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    if chunk.get("usage"):
                        usage = self._parse_usage(chunk["usage"])

                    for choice in chunk.get("choices") or []:
                        delta = choice.get("delta") or {}
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if reasoning:
                            yield StreamEvent(type="reasoning", content=reasoning)
                        content = delta.get("content") or ""
                        if content:
                            yield StreamEvent(type="delta", content=content)
                        if choice.get("finish_reason"):
                            finish_reason = choice["finish_reason"]

                yield StreamEvent(type="usage", usage=usage)
                yield StreamEvent(type="done", finish_reason=finish_reason, model=cfg.model)
        except httpx.ConnectError as exc:
            from ..errors import AIUnreachable

            raise AIUnreachable(f"无法连接 AI 服务：{exc}") from exc
        except httpx.TimeoutException as exc:
            from ..errors import AITimeout

            raise AITimeout(f"AI 请求超时：{exc}") from exc

    # ---------- 模型列表 ----------
    def list_models(self, cfg: ResolvedAIConfig) -> list[ModelInfo]:
        client = self._client_factory(cfg)
        resp = self._request(
            client,
            "GET",
            self._endpoint(cfg, "/models"),
            headers=self._headers(cfg),
            timeout=self._timeout(cfg),
        )
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text)
        payload = resp.json()
        items = payload.get("data") or payload.get("models") or []
        result: list[ModelInfo] = []
        for item in items:
            if isinstance(item, str):
                result.append(ModelInfo(id=item, source="openai_models"))
                continue
            model_id = item.get("id") or item.get("name") or ""
            if not model_id:
                continue
            result.append(ModelInfo(id=model_id, label=model_id, source="openai_models"))
        return result

    @staticmethod
    def _parse_usage(raw: dict[str, Any] | None) -> Usage:
        if not raw:
            return Usage()
        prompt = int(raw.get("prompt_tokens") or 0)
        completion = int(raw.get("completion_tokens") or 0)
        total = int(raw.get("total_tokens") or (prompt + completion))
        return Usage(prompt_tokens=prompt, completion_tokens=completion, total_tokens=total)
