"""通用工具：时间戳。"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def localnow_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")
