"""适配器基类与协议公共逻辑。"""

from __future__ import annotations

import json
import threading
import time
from typing import Any, Callable, Iterator

import httpx

from ..errors import AIError, AIUnreachable, AITimeout
from ..types import ChatResult, Message, ModelInfo, ResolvedAIConfig, StreamEvent

_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None

# 客户端工厂：允许注入自定义 httpx.Client（测试用）
ClientFactory = Callable[[ResolvedAIConfig | None], httpx.Client]


def shared_client(cfg: ResolvedAIConfig | None = None) -> httpx.Client:
    """进程内共享的 httpx 客户端。

    每次新建 Client 都要加载 SSL 上下文（约 700ms），复用可消除该固定开销。
    """
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                _shared_client = httpx.Client(
                    timeout=httpx.Timeout(120.0, connect=10.0),
                    limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
                )
    return _shared_client


class BaseAdapter:
    protocol = ""

    def __init__(self, client_factory: ClientFactory | None = None):
        self._client_factory = client_factory or shared_client

    # ---- 子类实现 ----
    def chat(self, cfg: ResolvedAIConfig, messages: list[Message], **opts: Any) -> ChatResult:
        raise NotImplementedError

    def chat_stream(
        self, cfg: ResolvedAIConfig, messages: list[Message], **opts: Any
    ) -> Iterator[StreamEvent]:
        raise NotImplementedError

    def list_models(self, cfg: ResolvedAIConfig) -> list[ModelInfo]:
        raise NotImplementedError

    # ---- 公共工具 ----
    def _timeout(self, cfg: ResolvedAIConfig) -> httpx.Timeout:
        return httpx.Timeout(cfg.timeout, connect=10.0)

    def _request(self, client: httpx.Client, method: str, url: str, **kwargs: Any) -> httpx.Response:
        try:
            return client.request(method, url, **kwargs)
        except httpx.ConnectError as exc:
            raise AIUnreachable(f"无法连接 AI 服务（{_host_of(url)}）：{exc}") from exc
        except httpx.TimeoutException as exc:
            raise AITimeout(f"AI 请求超时：{exc}") from exc
        except httpx.HTTPError as exc:
            raise AIError(f"AI 请求失败：{exc}") from exc

    @staticmethod
    def _extract_json_lines(text: str) -> Iterator[dict[str, Any]]:
        for raw in text.splitlines():
            line = raw.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def _host_of(url: str) -> str:
    try:
        from urllib.parse import urlparse

        parsed = urlparse(url)
        return f"{parsed.hostname}:{parsed.port}" if parsed.port else (parsed.hostname or url)
    except Exception:
        return url
