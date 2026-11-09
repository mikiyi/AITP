"""请求级可观测性（GAP-23）：把 traceId、路径、状态码、耗时落到业务日志。

与中间件的分工：
  - middleware.py 负责生成 traceId 并回显 X-Trace-Id 响应头（本模块只读，不写）；
  - 本模块注册自己的 before/after_request，负责「谁在什么时候失败了、慢在哪」。

AITP 的接口 HTTP 状态恒为 200，业务成败只体现在包封的 code 字段里，
所以「报错」不能只看 HTTP 状态码：这里额外嗅探响应体开头的业务码。

日志量控制：只记三类——HTTP 4xx/5xx、业务码非成功、慢请求（阈值见 SLOW_REQUEST_MS）。
成功的快请求不写日志，避免首页轮询把业务日志刷满。
"""

from __future__ import annotations

import logging
import re
import sys
import time
from typing import Any

from flask import Flask, Response, g, request

from .envelope import current_trace_id
from .logging_setup import log_event

#: 慢请求阈值（毫秒）：超过这个时长的接口即便成功也值得留一行，用于事后定位「卡在哪」。
#: 取 2000ms 是因为站内正常接口在本地 SQLite 上是几十毫秒量级，
#: 只有 AI 调用、批量执行、大文件导入这类操作会稳定超过它，正好是需要留痕的一类。
SLOW_REQUEST_MS = 2000

#: 包封成功码，与 envelope.ok() 写死的 200000 对应
SUCCESS_BUSINESS_CODE = 200000

#: 只嗅探响应体开头，避免为取一个数字去解析整个 JSON
_CODE_HEAD_BYTES = 256
_CODE_PATTERN = re.compile(rb'"code"\s*:\s*(-?\d+)')

#: 接口访问日志的落点：aitp.access 便于单独 grep 或后续按模块调级
logger = logging.getLogger("aitp.access")

_API_PREFIX = "/api/"


def _elapsed_ms(started: float | None) -> int:
    if started is None:
        return -1
    return max(0, int((time.perf_counter() - started) * 1000))


def _business_code(resp: Response) -> int | None:
    """从统一包封里读业务码；读不到（非 JSON、流式、已消费）时返回 None。

    只碰响应体开头：AITP 的分页/清单接口可能是几百 KB 的 JSON，
    为了取一个数字去复制或解析整段正文不划算。
    """
    try:
        if resp.direct_passthrough or not resp.is_json:
            return None
        chunks = resp.response
        if not isinstance(chunks, (list, tuple)):
            return None
        head = b""
        for chunk in chunks:
            raw = chunk if isinstance(chunk, bytes) else str(chunk).encode("utf-8", "replace")
            head += raw[:_CODE_HEAD_BYTES]
            if len(head) >= _CODE_HEAD_BYTES:
                break
    except Exception:  # noqa: BLE001 - 观测不能影响响应
        return None
    match = _CODE_PATTERN.search(head)
    if match is None:
        return None
    try:
        return int(match.group(1))
    except ValueError:
        return None


def _request_started() -> float | None:
    """优先复用中间件写入的 g.start_ts，取不到时用自己记的量。"""
    value: Any = getattr(g, "start_ts", None)
    if value is None:
        value = getattr(g, "observability_start_ts", None)
    return float(value) if isinstance(value, float) else None


def _observe_response(resp: Response) -> None:
    if not request.path.startswith(_API_PREFIX):
        return
    elapsed_ms = _elapsed_ms(_request_started())
    http_status = int(getattr(resp, "status_code", 0) or 0)
    code = _business_code(resp)
    trace_id = current_trace_id() or "-"

    failed = http_status >= 400 or (code is not None and code != SUCCESS_BUSINESS_CODE)
    slow = SLOW_REQUEST_MS >= 0 and elapsed_ms > SLOW_REQUEST_MS
    if not failed and not slow:
        return

    fields: dict[str, Any] = {
        "traceId": trace_id,
        "method": request.method,
        "path": request.path,
        "status": http_status,
        "ms": elapsed_ms,
    }
    if code is not None:
        fields["code"] = code
    if failed:
        log_event(logger, "api_error", level=logging.WARNING, **fields)
    else:
        log_event(logger, "api_slow", **fields)


def _note_hook_failure(stage: str, exc: BaseException) -> None:
    """观测钩子自身出错的留痕口径：宁可退到 stderr，也绝不静默丢现场。

    这里不能再简单写 `pass`——慢请求/错误统计丢了一段就再也查不回来。
    但留痕本身也可能失败（例如流式响应无法取体），所以 log_event 再抛时退回 stderr 直写。
    """
    try:
        log_event(logger, "observability_failed", level=logging.WARNING,
                  traceId=current_trace_id(), stage=stage,
                  error=f"{type(exc).__name__}: {exc}")
    except Exception as inner:  # noqa: BLE001 - 留痕失败只可能是日志机器本身，退到 stderr
        print(f"[aitp] observability_failed stage={stage} trace={current_trace_id()} "
              f"error={type(exc).__name__}: {exc} inner={type(inner).__name__}",
              file=sys.stderr)


def register_observability(app: Flask) -> None:
    """注册请求级观测钩子；必须在 setup_logging 之后、由应用工厂调用。"""

    @app.before_request
    def _observability_before() -> None:
        try:
            g.observability_start_ts = time.perf_counter()
        except Exception as exc:  # noqa: BLE001 - 上下文异常不影响请求，但要留痕
            _note_hook_failure("before_request", exc)

    @app.after_request
    def _observability_after(resp: Response) -> Response:
        try:
            _observe_response(resp)
        except Exception as exc:  # noqa: BLE001 - 日志绝不能把请求带崩，但要留痕
            _note_hook_failure("after_request", exc)
        return resp
