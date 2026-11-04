"""Ollama 原生协议适配器。

流式为 NDJSON（每行一个 JSON，无 data: 前缀、无 [DONE]，以 done:true 收尾）。
模型列表走 GET /api/tags，绝不 shell out 调 ollama list。
"""

from __future__ import annotations

import json
import time
from typing import Any, Iterator

import httpx

from ...common.constants import PROTOCOL_OLLAMA_NATIVE
from ..errors import classify_status
from ..types import ChatResult, Message, ModelInfo, ResolvedAIConfig, StreamEvent, Usage
from .base import BaseAdapter


class OllamaNativeAdapter(BaseAdapter):
    protocol = PROTOCOL_OLLAMA_NATIVE

    def _endpoint(self, cfg: ResolvedAIConfig, path: str) -> str:
        return f"{(cfg.base_url or '').strip().rstrip('/')}{path}"

    def _headers(self, cfg: ResolvedAIConfig) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"
        return headers

    def _body(
        self, cfg: ResolvedAIConfig, messages: list[Message], stream: bool, **opts: Any
    ) -> dict[str, Any]:
        return {
            "model": cfg.model,
            "messages": [m.as_ollama_dict() for m in messages],
            "stream": stream,
            "options": {
                "temperature": opts.get("temperature", cfg.temperature),
                "num_predict": opts.get("max_tokens", cfg.max_tokens),
            },
        }

    # ---------- 非流式 ----------
    def chat(self, cfg: ResolvedAIConfig, messages: list[Message], **opts: Any) -> ChatResult:
        started = time.perf_counter()
        client = self._client_factory(cfg)
        resp = self._request(
            client,
            "POST",
            self._endpoint(cfg, "/api/chat"),
            headers=self._headers(cfg),
            json=self._body(cfg, messages, False, **opts),
            timeout=self._timeout(cfg),
        )
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text)

        payload = resp.json()
        message = payload.get("message") or {}
        return ChatResult(
            content=message.get("content") or "",
            reasoning=message.get("thinking") or "",
            finish_reason=payload.get("done_reason") or "stop",
            usage=self._parse_usage(payload, message.get("content") or ""),
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
                self._endpoint(cfg, "/api/chat"),
                headers=self._headers(cfg),
                json=self._body(cfg, messages, True, **opts),
                timeout=self._timeout(cfg),
            ) as resp:
                if resp.status_code >= 400:
                    resp.read()
                    raise classify_status(resp.status_code, resp.text)

                finish_reason = "stop"
                usage = Usage()
                last_content = ""
                for line in resp.iter_lines():
                    line = (line or "").strip()
                    if not line:
                        continue
                    try:
                        chunk = json.loads(line)
                    except json.JSONDecodeError:
                        continue

                    message = chunk.get("message") or {}
                    thinking = message.get("thinking") or ""
                    if thinking:
                        yield StreamEvent(type="reasoning", content=thinking)
                    content = message.get("content") or ""
                    if content:
                        last_content = content
                        yield StreamEvent(type="delta", content=content)

                    if chunk.get("done"):
                        finish_reason = chunk.get("done_reason") or "stop"
                        usage = self._parse_usage(chunk, last_content)

                yield StreamEvent(type="usage", usage=usage)
                yield StreamEvent(type="done", finish_reason=finish_reason, model=cfg.model)
        except httpx.ConnectError as exc:
            from ..errors import AIUnreachable

            raise AIUnreachable(f"无法连接 AI 服务（Ollama）：{exc}") from exc
        except httpx.TimeoutException as exc:
            from ..errors import AITimeout

            raise AITimeout(f"AI 请求超时：{exc}") from exc

    # ---------- 模型列表 ----------
    def list_models(self, cfg: ResolvedAIConfig) -> list[ModelInfo]:
        client = self._client_factory(cfg)
        resp = self._request(
            client,
            "GET",
            self._endpoint(cfg, "/api/tags"),
            headers=self._headers(cfg),
            timeout=self._timeout(cfg),
        )
        if resp.status_code >= 400:
            raise classify_status(resp.status_code, resp.text)
        payload = resp.json()
        result: list[ModelInfo] = []
        for item in payload.get("models") or []:
            name = item.get("name") or item.get("model") or ""
            if not name:
                continue
            details = item.get("details") or {}
            result.append(
                ModelInfo(
                    id=name,
                    label=name,
                    size_bytes=int(item.get("size") or 0),
                    family=details.get("family") or "",
                    parameter_size=details.get("parameter_size") or "",
                    quantization=details.get("quantization_level") or "",
                    source="ollama_tags",
                )
            )
        return result

    @staticmethod
    def _parse_usage(payload: dict[str, Any], content: str) -> Usage:
        prompt = int(payload.get("prompt_eval_count") or 0)
        completion = int(payload.get("eval_count") or 0)
        if prompt or completion:
            return Usage(
                prompt_tokens=prompt,
                completion_tokens=completion,
                total_tokens=prompt + completion,
            )
        # 老版本没有计数字段时按长度估算
        est_completion = max(1, len(content) // 2) if content else 0
        return Usage(
            prompt_tokens=0,
            completion_tokens=est_completion,
            total_tokens=est_completion,
            estimated=True,
        )
