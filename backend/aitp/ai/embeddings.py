"""嵌入模型通道（四期专项 B）。

只做一件事：把文本批量转成向量。配置存在 `sys_config`，与聊天档案分开：

    ai.embedding.enabled   "1"/"0"（默认 0）
    ai.embedding.protocol  ollama_native | openai_compat
    ai.embedding.baseUrl   例如 http://127.0.0.1:11434
    ai.embedding.model     nomic-embed-text 等
    ai.embedding.apiKey    可选（本地模型留空）

口径：
  - **默认关闭**：云端嵌入必须显式开启，避免悄悄把知识正文外发；本地地址放行；
  - **失败即降级**：任何异常返回 None，调用方回落到纯 FTS5 关键词检索；
  - **不进场聊天档案**：不参与 chat_with_fallback 的候选排序，也不计入聊天成本口径。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

import httpx

from ..logging_setup import log_event
from ..repositories.sys_config_repo import SysConfigRepository
from .adapters.base import _proxy_env_fixed

logger = logging.getLogger(__name__)

ENABLED_KEY = "ai.embedding.enabled"
PROTOCOL_KEY = "ai.embedding.protocol"
BASE_URL_KEY = "ai.embedding.baseUrl"
MODEL_KEY = "ai.embedding.model"
API_KEY_KEY = "ai.embedding.apiKey"

PROTOCOL_OLLAMA = "ollama_native"
PROTOCOL_OPENAI = "openai_compat"

DEFAULT_TIMEOUT = 30
#: 一次最多送多少段文本，避免单请求体过大
MAX_BATCH = 16

#: 本地地址前缀：本地嵌入默认放行，不要求显式开启
_LOCAL_HOSTS = ("http://127.0.0.1", "http://localhost", "http://0.0.0.0", "http://[::1]")


def settings(conn: sqlite3.Connection | None) -> dict[str, Any]:
    """读取嵌入配置；读不到一律给「未启用」的默认值，不抛错。"""
    default = {
        "enabled": False,
        "protocol": PROTOCOL_OLLAMA,
        "baseUrl": "",
        "model": "",
        "apiKey": "",
    }
    if conn is None:
        return default
    try:
        repo = SysConfigRepository(conn)
        enabled = str(repo.get_value(ENABLED_KEY, "0") or "0").strip().lower()
        return {
            "enabled": enabled in ("1", "true", "yes", "on"),
            "protocol": str(repo.get_value(PROTOCOL_KEY, PROTOCOL_OLLAMA) or PROTOCOL_OLLAMA),
            "baseUrl": str(repo.get_value(BASE_URL_KEY, "") or "").strip(),
            "model": str(repo.get_value(MODEL_KEY, "") or "").strip(),
            "apiKey": str(repo.get_value(API_KEY_KEY, "") or "").strip(),
        }
    except (sqlite3.Error, KeyError, ValueError) as exc:  # noqa: BLE001 - 配置坏了当没配
        log_event(logger, "embedding_settings_failed", level=logging.WARNING,
                  error=str(exc)[:200])
        return default


def is_local(base_url: str) -> bool:
    low = str(base_url or "").strip().lower()
    return any(low.startswith(host) for host in _LOCAL_HOSTS)


def usable(settings_map: dict[str, Any]) -> bool:
    """是否具备可用的嵌入通道：地址与模型齐备，且本地放行 / 云端显式开启。"""
    if not str(settings_map.get("baseUrl") or "").strip():
        return False
    if not str(settings_map.get("model") or "").strip():
        return False
    if is_local(str(settings_map.get("baseUrl") or "")):
        return True
    return bool(settings_map.get("enabled"))


def describe(settings_map: dict[str, Any]) -> dict[str, Any]:
    """给前端/门禁的可用性说明。"""
    usable_now = usable(settings_map)
    if usable_now:
        reason = "本地嵌入模型" if is_local(str(settings_map.get("baseUrl") or "")) else "已开启云端嵌入"
    elif not str(settings_map.get("baseUrl") or "").strip() or \
            not str(settings_map.get("model") or "").strip():
        reason = "未配置嵌入模型，语义检索关闭（关键词检索照常）"
    else:
        reason = "云端嵌入默认关闭，请在 AI 设置里显式开启"
    return {
        "usable": usable_now,
        "enabled": bool(settings_map.get("enabled")),
        "protocol": str(settings_map.get("protocol") or ""),
        "baseUrl": str(settings_map.get("baseUrl") or ""),
        "model": str(settings_map.get("model") or ""),
        "local": is_local(str(settings_map.get("baseUrl") or "")),
        "reason": reason,
    }


def _endpoint(base_url: str, protocol: str) -> str:
    base = str(base_url or "").strip().rstrip("/")
    if protocol == PROTOCOL_OLLAMA:
        return f"{base}/api/embeddings"
    if base.endswith("/v1"):
        return f"{base}/embeddings"
    return f"{base}/v1/embeddings"


def embed_texts(
    texts: list[str],
    settings_map: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[list[float]] | None:
    """批量取向量；不可用或任何失败返回 None（调用方降级到关键词检索）。"""
    if not texts or not usable(settings_map):
        return None
    protocol = str(settings_map.get("protocol") or PROTOCOL_OLLAMA)
    base_url = str(settings_map.get("baseUrl") or "")
    model = str(settings_map.get("model") or "")
    api_key = str(settings_map.get("apiKey") or "")
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    vectors: list[list[float]] = []
    # 云端嵌入（数据离开本机）先走统一脱敏；本地模型原样发送。
    payload_texts = texts
    if not is_local(base_url):
        from .masking import mask_messages
        from .types import Message

        masked_messages, _info = mask_messages(
            [Message(role="user", content=text) for text in texts]
        )
        payload_texts = [item.content for item in masked_messages]
    try:
        # 与聊天适配器同一坑：NO_PROXY 里 [::1] 这类条目会让 httpx 建客户端直接崩，
        # 借用 adapters.base 的清洗上下文；不处理的话本机嵌入会永远静默降级成关键词检索
        with _proxy_env_fixed(), httpx.Client(timeout=timeout) as client:
            for start in range(0, len(payload_texts), MAX_BATCH):
                batch = payload_texts[start:start + MAX_BATCH]
                if protocol == PROTOCOL_OLLAMA:
                    for text in batch:
                        resp = client.post(
                            _endpoint(base_url, protocol), headers=headers,
                            json={"model": model, "prompt": text},
                        )
                        resp.raise_for_status()
                        payload = resp.json()
                        vector = payload.get("embedding") or []
                        if not vector:
                            return None
                        vectors.append([float(item) for item in vector])
                else:
                    resp = client.post(
                        _endpoint(base_url, protocol), headers=headers,
                        json={"model": model, "input": batch},
                    )
                    resp.raise_for_status()
                    payload = resp.json()
                    data = sorted(payload.get("data") or [],
                                  key=lambda item: int(item.get("index") or 0))
                    if len(data) != len(batch):
                        return None
                    for item in data:
                        vector = item.get("embedding") or []
                        if not vector:
                            return None
                        vectors.append([float(value) for value in vector])
    except (httpx.HTTPError, ValueError, TypeError, KeyError) as exc:  # noqa: BLE001 - 降级
        log_event(logger, "embedding_request_failed", level=logging.WARNING,
                  protocol=protocol, model=model, error=str(exc)[:200])
        return None
    if len({len(vector) for vector in vectors}) > 1:
        return None
    return vectors


def pack(vector: list[float]) -> bytes:
    """向量落库：float32 小端定长 BLOB（个人版数据量足够小，无需向量库）。"""
    import struct

    return struct.pack(f"<{len(vector)}f", *vector)


def unpack(blob: bytes | memoryview | None) -> list[float]:
    """BLOB 还原向量；损坏返回空表（调用方跳过该块）。"""
    if not blob:
        return []
    raw = bytes(blob)
    if len(raw) % 4 != 0:
        return []
    import struct

    count = len(raw) // 4
    return list(struct.unpack(f"<{count}f", raw))


def cosine(a: list[float], b: list[float]) -> float:
    """余弦相似度；零向量返回 0，绝不抛 ZeroDivisionError。"""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return float(dot / (na ** 0.5 * nb ** 0.5))


__all__ = [
    "API_KEY_KEY", "BASE_URL_KEY", "ENABLED_KEY", "MODEL_KEY", "PROTOCOL_KEY",
    "PROTOCOL_OLLAMA", "PROTOCOL_OPENAI", "cosine", "describe", "embed_texts",
    "is_local", "pack", "settings", "unpack", "usable",
]
