"""日志配置：文件处理器强制 UTF-8，避免 Windows GBK 控制台导致中文乱码或崩溃。

另外单独维护一条部署日志（logs/deploy.log），记录启动/停止/迁移等部署事件，
与业务日志分开，便于排查「服务为什么没起来」。
"""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, cast

_SECRET_PATTERN = re.compile(r"(sk-[A-Za-z0-9_\-]{8,})")

DEPLOY_LOGGER_NAME = "aitp.deploy"


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

    formatter = logging.Formatter("%(asctime)s | %(levelname)-7s | %(name)s | %(message)s")

    file_handler = RotatingFileHandler(
        log_path, maxBytes=5 * 1024 * 1024, backupCount=5, encoding="utf-8"
    )
    file_handler.setFormatter(formatter)
    file_handler.addFilter(RedactFilter())

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
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
        handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s"))
        handler.addFilter(RedactFilter())
        logger.addHandler(handler)

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s | DEPLOY | %(message)s"))
    console.addFilter(RedactFilter())
    logger.addHandler(console)
    return logger


def get_deploy_logger() -> logging.Logger:
    return logging.getLogger(DEPLOY_LOGGER_NAME)


def log_deploy_event(event: str, message: str = "", **fields: object) -> None:
    """记录一条部署事件。fields 会以 key=value 形式追加。"""
    logger = get_deploy_logger()
    extra = " ".join(f"{k}={v}" for k, v in fields.items() if v is not None)
    line = f"[{event}] {message}".strip()
    if extra:
        line = f"{line} | {extra}"
    logger.info(line)
