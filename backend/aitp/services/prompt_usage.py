"""内部调度类提示词的调用计数。

能力类提示词每次调用都会落一条 biz_ai_record，提示词库直接按 prompt_code 统计；
智能路由、知识库问答助手、自动整理这三类跑在幕后，落 AI 执行记录只会把列表
淹成噪声，所以单独在 sys_prompt_usage 累加一笔，提示词库把两份计数合起来。
"""

from __future__ import annotations

import logging
import sqlite3

from ..common.time_utils import utcnow_iso

logger = logging.getLogger(__name__)

#: 不走 AI 执行记录、但仍需统计调用次数的模板编码
TRACKED: tuple[str, ...] = (
    "intent-router",
    "module-router",
    "knowledge-assistant",
    "knowledge-organize",
)


def record(conn: sqlite3.Connection, code: str) -> None:
    """累加一次真实调用；计数是附属信息，失败不能影响本次 AI 结果。

    自带提交：调用方多是只读接口，不会为这一笔再 commit。
    """
    if code not in TRACKED:
        return
    try:
        conn.execute(
            """
            INSERT INTO sys_prompt_usage (prompt_code, use_count, last_used_at)
            VALUES (?, 1, ?)
            ON CONFLICT(prompt_code) DO UPDATE SET
                use_count = use_count + 1,
                last_used_at = excluded.last_used_at
            """,
            (code, utcnow_iso()),
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 计数失败不该掀翻已经拿到的模型回答
        logger.warning("提示词调用计数写入失败: %s", code, exc_info=True)


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    """各内部模板的累计调用次数；没调用过的编码不在结果里。"""
    try:
        rows = conn.execute("SELECT prompt_code, use_count FROM sys_prompt_usage").fetchall()
    except Exception:  # noqa: BLE001 - 老库缺表时按 0 次处理，不影响列表整体返回
        logger.warning("提示词调用计数读取失败", exc_info=True)
        return {}
    result: dict[str, int] = {}
    for row in rows:
        result[str(row["prompt_code"])] = int(row["use_count"] or 0)
    return result
