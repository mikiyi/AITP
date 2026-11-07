"""通用工具：时间戳。"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def localnow_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def parse_iso(value: str) -> datetime | None:
    text = (value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone()


def local_stamp(value: str, with_time: bool = False) -> str:
    """把 UTC ISO 时间转成本机时区的紧凑数字串，用于标题前缀。

    解析失败时退回当前时间：标题只是给人看的，不该因为一个畸形时间戳让整条入库失败。
    """
    parsed = parse_iso(value) or datetime.now().astimezone()
    return parsed.strftime("%Y%m%d%H%M%S" if with_time else "%Y%m%d")
