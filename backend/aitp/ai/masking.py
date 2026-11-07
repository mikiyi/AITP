"""AI 外发脱敏。

云端模型的输入会离开本机，系统提示词、用户输入、知识检索结果都要经过这一层。
本地模型不出本机，不做脱敏；是否对云端模型脱敏由 sys_config 的
``ai.mask.enabled`` 控制，默认开启。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..common.pii import TYPE_LABELS, scan_sensitive
from .types import Message

MASK_CONFIG_KEY = "ai.mask.enabled"

#: 类型码 → 中文名；前端展示与 AI 记录共用后端这一份
MASK_TYPE_LABELS = TYPE_LABELS

_DISABLED_VALUES = frozenset({"false", "0", "no", "off"})


@dataclass(slots=True)
class MaskInfo:
    """一次调用的脱敏结果。只带类型与数量，不保留原文。"""

    enabled: bool = True
    applied: bool = False
    types: list[dict[str, Any]] = field(default_factory=list)
    count: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "applied": self.applied,
            "types": self.types,
            "count": self.count,
        }


def cloud_mask_enabled(conn: sqlite3.Connection | None = None) -> bool:
    """云端模型是否脱敏：未配置或配置不是明确关闭时都按开启处理。"""
    if conn is None:
        return True
    from ..repositories.sys_config_repo import SysConfigRepository

    raw = SysConfigRepository(conn).get_value(MASK_CONFIG_KEY, "true")
    return str(raw).strip().lower() not in _DISABLED_VALUES


def info_from_counts(
    counts: dict[str, int], *, enabled: bool = True, applied: bool = True
) -> MaskInfo:
    types: list[dict[str, Any]] = [
        {"code": code, "label": MASK_TYPE_LABELS.get(code, code), "count": count}
        for code, count in counts.items()
        if count > 0
    ]
    count = sum(int(item["count"]) for item in types)
    return MaskInfo(
        enabled=enabled,
        applied=applied and count > 0,
        types=types,
        count=count,
    )


def mask_messages(messages: list[Message]) -> tuple[list[Message], MaskInfo]:
    """对消息正文脱敏，返回新消息列表；入参不会被修改。"""
    masked_messages: list[Message] = []
    totals: dict[str, int] = {}
    for message in messages:
        masked_text, counts = scan_sensitive(message.content)
        for code, count in counts.items():
            totals[code] = totals.get(code, 0) + count
        masked_messages.append(
            Message(
                role=message.role,
                content=masked_text,
                reasoning=message.reasoning,
                images=list(message.images),
            )
        )
    return masked_messages, info_from_counts(totals)


def preview_mask(text: str, *, enabled: bool = True) -> MaskInfo:
    """预览会替换掉的敏感信息：只给类型与数量，不回显原文。"""
    _, counts = scan_sensitive(text)
    return info_from_counts(counts, enabled=enabled, applied=False)
