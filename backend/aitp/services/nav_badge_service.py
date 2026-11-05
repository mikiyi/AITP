"""导航未读角标。

AI 产出一键添加到资产后，对应导航入口显示未读数；
用户进入该页面查看后清零。count 落在 sys_nav_badge，一个分区一行。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso

#: 允许出现角标的分区（与前端菜单映射保持一致）
SECTIONS: tuple[str, ...] = (
    "requirements",
    "cases",
    "bugs",
    "regressions",
    "reports",
    "automation_web",
    "automation_app",
    "automation_api",
    "automation_performance",
    "sql_analyses",
    # 知识库待审数不走 sys_nav_badge 累加，而是实时读待审池真实行数
    "knowledge",
)


def _check(section: str) -> str:
    value = str(section or "").strip()
    if value not in SECTIONS:
        raise ValueError(f"未知的分区：{section}")
    return value


def list_badges(conn: sqlite3.Connection) -> dict[str, int]:
    """返回全部分区的未读数；没记录的按 0 处理。

    knowledge 分区特殊：它不代表「未读累加」，而是待审池的真实条数，
    所以直接查 biz_knowledge_draft 里 pending 的行数，不读写 sys_nav_badge。
    """
    counts = {section: 0 for section in SECTIONS}
    for row in conn.execute("SELECT section, unread FROM sys_nav_badge"):
        section = str(row["section"])
        if section in counts:
            counts[section] = int(row["unread"] or 0)
    counts["knowledge"] = pending_knowledge_count(conn)
    return counts


def pending_knowledge_count(conn: sqlite3.Connection) -> int:
    """待审池的真实条数：驱动知识库导航角标，>0 才显示。"""
    row = conn.execute(
        "SELECT COUNT(*) FROM biz_knowledge_draft WHERE is_deleted = 0 AND review_status = 'pending'"
    ).fetchone()
    return int(row[0]) if row else 0


def increase(conn: sqlite3.Connection, section: str, count: int = 1) -> int:
    """未读数 += count，返回累加后的值。"""
    target = _check(section)
    step = max(1, int(count or 1))
    now = utcnow_iso()
    conn.execute(
        """
        INSERT INTO sys_nav_badge (section, unread, updated_at) VALUES (?, ?, ?)
        ON CONFLICT(section) DO UPDATE SET
            unread = unread + excluded.unread,
            updated_at = excluded.updated_at
        """,
        (target, step, now),
    )
    row = conn.execute(
        "SELECT unread FROM sys_nav_badge WHERE section = ?", (target,)
    ).fetchone()
    return int(row["unread"] if row else step)


def clear(conn: sqlite3.Connection, section: str) -> int:
    """进入页面查看后清零，返回清零前的未读数。"""
    target = _check(section)
    row = conn.execute(
        "SELECT unread FROM sys_nav_badge WHERE section = ?", (target,)
    ).fetchone()
    before = int(row["unread"] if row else 0)
    conn.execute(
        "UPDATE sys_nav_badge SET unread = 0, updated_at = ? WHERE section = ?",
        (utcnow_iso(), target),
    )
    return before


def increase_for(conn: sqlite3.Connection, section: str, count: int = 1) -> None:
    """业务侧调用：失败不该影响主流程，吞掉异常由调用方决定是否提交。"""
    try:
        increase(conn, section, count)
    except (ValueError, sqlite3.Error):
        return


def as_dict(conn: sqlite3.Connection) -> dict[str, Any]:
    return {"items": list_badges(conn), "sections": list(SECTIONS)}
