"""SSE 帧封装。

帧类型：start / delta / reasoning / usage / degrade / done / error，
每帧带单调递增的 seq，前端据此保证顺序与去重。
"""

from __future__ import annotations

import json
from typing import Any

SSE_MIME = "text/event-stream; charset=utf-8"

FRAME_START = "start"
FRAME_DELTA = "delta"
FRAME_REASONING = "reasoning"
FRAME_USAGE = "usage"
FRAME_DEGRADE = "degrade"
FRAME_DONE = "done"
FRAME_ERROR = "error"


def encode(payload: dict[str, Any]) -> str:
    """把一帧数据编码成 SSE 文本。"""
    return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"


class SeqCounter:
    """单调递增的帧序号。"""

    __slots__ = ("_value",)

    def __init__(self) -> None:
        self._value = 0

    def next(self) -> int:
        self._value += 1
        return self._value

    def frame(self, frame_type: str, **fields: Any) -> str:
        payload: dict[str, Any] = {"seq": self.next(), "type": frame_type}
        payload.update(fields)
        return encode(payload)
