"""日志配置：文件处理器强制 UTF-8，避免 Windows GBK 控制台导致中文乱码或崩溃。

另外单独维护一条部署日志（logs/deploy.log），记录启动/停止/迁移等部署事件，
与业务日志分开，便于排查「服务为什么没起来」。

业务日志统一成一行可 grep 的观感：
    时间 | 级别 | 模块 | traceId | 消息 | 关键参数
traceId 由 TraceContextFilter 在每条记录上落一个 trace_id 字段（GAP-23）：
用户在页面上看到的报错追踪号，必须能直接搜到对应的服务端日志行。
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, Mapping, cast

_SECRET_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{8,})")

DEPLOY_LOGGER_NAME = "aitp.deploy"

#: 业务日志一行内的分隔符；formatter 与 log_event 产出的关键参数共用，便于 grep
FIELD_SEPARATOR = " | "
#: 无请求上下文（启动、脚本、后台线程）时 traceId 列的占位值
NO_TRACE = "-"


class NonLockingRotatingFileHandler(RotatingFileHandler):
    """写完即释放文件句柄。

    Windows 上文件被独占时，启动/停止脚本无法追加同一条日志；
    每次 emit 后关闭流可避免长期占用（下次写入会自动重新打开）。
    """

    def emit(self, record: logging.LogRecord) -> None:
        super().emit(record)
        try:
            self.close()
        except Exception:
            pass


class RedactFilter(logging.Filter):
    """把疑似 API Key 打码，防止明文落盘。"""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:
            return True
        redacted = _SECRET_PATTERN.sub("sk-***", message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


class TraceContextFilter(logging.Filter):
    """给每条记录补 trace_id 字段：请求上下文里取 g.trace_id，否则填占位符。

    取上下文失败绝不能影响请求，所以这里对 flask 的导入与 g 访问全部兜底；
    该 filter 挂在每个 handler 上（handler 级过滤器对传播来的记录同样生效），
    formatter 因此总能拿到 %(trace_id)s。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        if not hasattr(record, "trace_id"):
            try:
                record.trace_id = current_trace_id()
            except Exception:  # noqa: BLE001 - 日志上下文永远不该让业务失败
                record.trace_id = NO_TRACE
        return True


def current_trace_id() -> str:
    """当前请求的 traceId；没有请求/应用上下文时返回占位符。"""
    try:
        from flask import g

        value = getattr(g, "trace_id", "") or ""
    except Exception:  # noqa: BLE001 - 含「上下文外访问 g 抛 RuntimeError」
        return NO_TRACE
    text = str(value).strip()
    return text[:64] if text else NO_TRACE


class TraceFormatter(logging.Formatter):
    """带 trace_id 列的 formatter：自己再兜一次底。

    正常情况下 TraceContextFilter 已经写好 record.trace_id；
    万一某条记录绕过我们的 handler（第三方 handler、测试替身），
    这里补占位符而不是抛格式化异常——日志配置坏了不该连累业务输出。
    """

    def format(self, record: logging.LogRecord) -> str:
        if not hasattr(record, "trace_id"):
            try:
                record.trace_id = current_trace_id()
            except Exception:  # noqa: BLE001 - 格式化不能报错
                record.trace_id = NO_TRACE
        return super().format(record)


def format_fields(fields: Mapping[str, object]) -> str:
    """把关键参数拼成空格分隔的 key=value，值含空格时补引号。"""
    parts: list[str] = []
    for key, value in fields.items():
        if value is None:
            continue
        text = str(value)
        if not text:
            continue
        if re.search(r"\s", text):
            parts.append(f'{key}="{text}"')
        else:
            parts.append(f"{key}={text}")
    return " ".join(parts)


def log_event(
    logger: logging.Logger,
    event: str,
    *,
    level: int = logging.INFO,
    **fields: object,
) -> None:
    """打一条业务事件：`event | key=value ...`，与 formatter 的列一起构成可 grep 的一行。

    traceId 由 TraceContextFilter 写在独立列上；调用方需要跨线程核对时，
    可以显式把 trace_id 作为字段传进来（它会出现在关键参数里）。
    """
    if not logger.isEnabledFor(level):
        return
    detail = format_fields(fields)
    message = f"{event}{FIELD_SEPARATOR}{detail}" if detail else event
    logger.log(level, "%s", message)


def _reconfigure_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        try:
            # TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
            cast(Any, stream).reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def setup_logging(log_path: Path, level: int = logging.INFO,
                  deploy_log_path: Path | None = None) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    _reconfigure_streams()

    # 时间 | 级别 | 模块 | traceId | 消息 —— 关键参数由 log_event 追加在消息里
    formatter = TraceFormatter(
        "%(asctime)s | %(levelname)-7s | %(name)s | %(trace_id)s | %(message)s"
    )

    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    # 顺序：先落 traceId，再打码——打码作用于最终消息文本，与新增列互不影响
    file_handler.addFilter(TraceContextFilter())
    file_handler.addFilter(RedactFilter())

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(TraceContextFilter())
    console_handler.addFilter(RedactFilter())

    root = logging.getLogger()
    root.handlers = [file_handler, console_handler]
    root.setLevel(level)

    logging.getLogger("werkzeug").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)

    setup_deploy_logging(deploy_log_path, level)


def setup_deploy_logging(deploy_log_path: Path | None, level: int = logging.INFO) -> logging.Logger:
    """独立部署日志：只记部署事件，不与业务日志混在一起。"""
    logger = logging.getLogger(DEPLOY_LOGGER_NAME)
    logger.setLevel(level)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    if deploy_log_path is not None:
        deploy_log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = NonLockingRotatingFileHandler(
            deploy_log_path, maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        handler.setFormatter(
            TraceFormatter("%(asctime)s | %(levelname)-7s | DEPLOY | %(trace_id)s | %(message)s")
        )
        handler.addFilter(TraceContextFilter())
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(
        TraceFormatter("%(asctime)s | DEPLOY | %(trace_id)s | %(message)s")
    )
    console.addFilter(TraceContextFilter())
    console.addFilter(RedactFilter())
    logger.addHandler(console)
    return logger


def get_deploy_logger() -> logging.Logger:
    return logging.getLogger(DEPLOY_LOGGER_NAME)


def log_deploy_event(event: str, message: str = "", **fields: object) -> None:
    """记录一条部署事件。fields 会以 key=value 形式追加。"""
    logger = get_deploy_logger()
    extra = format_fields(fields)
    line = f"[{event}] {message}".strip()
    if extra:
        line = f"{line}{FIELD_SEPARATOR}{extra}"
    logger.info("%s", line)
