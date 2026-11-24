"""用例稳定性与 flaky 判定（V2.6.0）。

同一条接口用例在最近 N 次执行里既通过又失败，就视为不稳定。它和「真失败」
分开统计：报告里不把 flaky 当成回归失败，执行详情里给出醒目标记，避免
环境抖动淹没真正的缺陷。

判定只用历史执行记录，不新增状态表：数据始终可从证据链复算，迁移与
备份不会多一份可能失真的快照。
"""

from __future__ import annotations

import sqlite3
from typing import Any

#: 回看最近多少次执行
WINDOW = 5
#: 至少出现一次结果翻转才算 flaky（通过 → 失败 或 失败 → 通过）
MIN_FLIPS = 1


def recent_results(conn: sqlite3.Connection, case_id: int, limit: int = WINDOW) -> list[str]:
    rows = conn.execute(
        """
        SELECT result FROM biz_interface_execution
        WHERE case_id = ?
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(case_id), max(1, int(limit))),
    ).fetchall()
    return [str(row["result"] or "") for row in rows if str(row["result"] or "") in ("passed", "failed")]


def evaluate(results: list[str]) -> dict[str, Any]:
    """由最近结果序列判定稳定性；results 按时间倒序传入。"""
    ordered = [item for item in results if item in ("passed", "failed")]
    passed = ordered.count("passed")
    failed = ordered.count("failed")
    flips = sum(1 for prev, curr in zip(ordered, ordered[1:]) if prev != curr)
    flaky = bool(passed and failed and flips >= MIN_FLIPS)
    return {
        "flaky": flaky,
        "flips": flips,
        "passed": passed,
        "failed": failed,
        "window": len(ordered),
        "lastResult": ordered[0] if ordered else "",
    }


def observe(conn: sqlite3.Connection, case_id: int, result: str) -> dict[str, Any]:
    """当前结果已落库后调用，返回包含本次在内的稳定性结论。"""
    results = recent_results(conn, case_id, WINDOW)
    if result in ("passed", "failed") and (not results or results[0] != result):
        results = [result, *results][:WINDOW]
    return evaluate(results)


def stability(conn: sqlite3.Connection, case_id: int) -> dict[str, Any]:
    return evaluate(recent_results(conn, case_id, WINDOW))


def stability_map(conn: sqlite3.Connection, case_ids: list[int]) -> dict[int, dict[str, Any]]:
    """批量取稳定性，列表页一次查完，避免逐条 N+1。"""
    ids = sorted({int(item) for item in case_ids if int(item) > 0})
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"""
        SELECT case_id, result FROM biz_interface_execution
        WHERE case_id IN ({placeholders})
        ORDER BY case_id, id DESC
        """,
        ids,
    ).fetchall()
    grouped: dict[int, list[str]] = {case_id: [] for case_id in ids}
    for row in rows:
        case_id = int(row["case_id"])
        value = str(row["result"] or "")
        if value in ("passed", "failed") and len(grouped[case_id]) < WINDOW:
            grouped[case_id].append(value)
    return {case_id: evaluate(results) for case_id, results in grouped.items()}


def run_case_flaky(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    """本轮内被标记 flaky 的用例，报告与发布结论据此与真失败分列。"""
    rows = conn.execute(
        """
        SELECT id, case_id, case_no, title, retry_count
        FROM biz_test_run_case
        WHERE run_id = ? AND flaky = 1 AND is_deleted = 0
        ORDER BY id
        """,
        (int(run_id),),
    ).fetchall()
    return [dict(row) for row in rows]


__all__ = [
    "WINDOW",
    "MIN_FLIPS",
    "recent_results",
    "evaluate",
    "observe",
    "stability",
    "stability_map",
    "run_case_flaky",
]
