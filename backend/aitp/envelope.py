"""统一响应包封：{code, message, data, traceId, timestamp}。HTTP 恒为 200。"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from flask import Response, g, jsonify


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def current_trace_id() -> str:
    return getattr(g, "trace_id", "") or ""


def build(code: int, message: str, data: Any = None, trace_id: str | None = None) -> dict[str, Any]:
    return {
        "code": code,
        "message": message,
        "data": data,
        "traceId": trace_id or current_trace_id(),
        "timestamp": utcnow_iso(),
    }


def ok(data: Any = None, message: str = "success") -> Response:
    return jsonify(build(200000, message, data))


def fail(code: int, message: str, data: Any = None) -> Response:
    return jsonify(build(code, message, data))
