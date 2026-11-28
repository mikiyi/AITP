"""风险分级（设计 §5.3）：S0 灾难 / S1 严重 / S2 轻微，错误不等价。

权重口径：一条 S0 错误 ≈ 50 条 S2 错误，避免「99 条格式都对」掩盖
「1 条把客户带进坑里」。本模块是纯函数，不碰 DB。
"""

from __future__ import annotations

SEVERITY_S0 = "S0"
SEVERITY_S1 = "S1"
SEVERITY_S2 = "S2"

SEVERITIES: frozenset[str] = frozenset({SEVERITY_S0, SEVERITY_S1, SEVERITY_S2})

#: 分级权重（设计 §5.3 表）
SEVERITY_WEIGHTS: dict[str, float] = {SEVERITY_S0: 50.0, SEVERITY_S1: 10.0, SEVERITY_S2: 1.0}

#: 未知/缺省级别按 S1 处理（严重但非灾难），避免 None 被当成最轻的 S2
DEFAULT_WEIGHT = SEVERITY_WEIGHTS[SEVERITY_S1]


def weight_of(severity: str | None) -> float:
    """取分级权重；未知名按 S1 兜底。"""

    if not severity:
        return DEFAULT_WEIGHT
    return SEVERITY_WEIGHTS.get(str(severity), DEFAULT_WEIGHT)


def rank_of(severity: str | None) -> int:
    """S0=0（最高）→ S2=2，用于排序与难例优先采样。"""

    table = {SEVERITY_S0: 0, SEVERITY_S1: 1, SEVERITY_S2: 2}
    return table.get(str(severity or ""), 1)


def is_valid(severity: str | None) -> bool:
    return severity in SEVERITIES
