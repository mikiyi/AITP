"""请求中间件：traceId 生成与回显、访问日志。"""

from __future__ import annotations

import time
import uuid

from flask import Flask, Response, g, request


def register_middleware(app: Flask) -> None:
    @app.before_request
    def _before() -> None:
        g.trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        g.start_ts = time.perf_counter()

    @app.after_request
    def _after(resp: Response) -> Response:
        resp.headers["X-Trace-Id"] = getattr(g, "trace_id", "")
        start = getattr(g, "start_ts", None)
        if start is not None:
            resp.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - start) * 1000))
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp
