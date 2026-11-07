"""适配器基类与协议公共逻辑。"""

from __future__ import annotations

import json
import os
import threading
import time
from contextlib import contextmanager
from typing import Any, Callable, Iterator

import httpx

from ..errors import AIError, AIUnreachable, AITimeout
from ..types import ChatResult, Message, ModelInfo, ResolvedAIConfig, StreamEvent

_client_lock = threading.Lock()
_shared_client: httpx.Client | None = None

# 客户端工厂：允许注入自定义 httpx.Client（测试用）
ClientFactory = Callable[[ResolvedAIConfig | None], httpx.Client]

#: NO_PROXY 里这两个变量都可能被读到，httpx 优先看小写的
_NO_PROXY_KEYS = ("NO_PROXY", "no_proxy")


def _unparsable_no_proxy_entry(entry: str) -> bool:
    """判断 NO_PROXY 里的某一项 httpx 解析不了。

    httpx 会把 NO_PROXY 拆成 "all://[::1]" 这样的挂载点，
    而它自带的 URL 解析器处理不了不带端口的 IPv6 字面量，
    建客户端时直接抛 InvalidURL（Invalid port: ':1]'）。
    """
    text = entry.strip()
    if not text:
        return False
    if text.startswith("["):
        return True
    # 裸 IPv6（::1）有两个以上冒号；host:port 只有一个冒号，httpx 能解析
    return text.count(":") > 1


@contextmanager
def _proxy_env_fixed() -> Iterator[None]:
    """建客户端前把 NO_PROXY 里 httpx 解析不了的条目摘掉，退出时还原。"""
    saved: dict[str, str | None] = {key: os.environ.get(key) for key in _NO_PROXY_KEYS}
    changed = False
    for key, raw in saved.items():
        if not raw:
            continue
        parts = [part for part in raw.split(",") if part.strip()]
        kept = [part for part in parts if not _unparsable_no_proxy_entry(part)]
        if len(kept) != len(parts):
            os.environ[key] = ",".join(kept)
            changed = True
    try:
        yield
    finally:
        if changed:
            for key, raw in saved.items():
                if raw is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = raw


def shared_client(cfg: ResolvedAIConfig | None = None) -> httpx.Client:
    """进程内共享的 httpx 客户端。

    每次新建 Client 都要加载 SSL 上下文（约 700ms），复用可消除该固定开销。
    """
    global _shared_client
    if _shared_client is None:
        with _client_lock:
            if _shared_client is None:
                with _proxy_env_fixed():
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
