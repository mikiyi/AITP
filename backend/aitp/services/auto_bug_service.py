"""执行失败自动转缺陷：一个配置开关，复用现有缺陷表与编号口径。

覆盖两条执行链路（不介入人工录入）：
  - 接口场景：一次场景执行未通过 → 按失败步骤生成一条缺陷；
  - 自动化脚本：脚本执行失败 / 超时 / 异常 → 按失败用例结果生成一条缺陷。

配置键 ``automation.auto_bug`` = true 时启用，默认关闭。
同一次执行只建一条（描述里带执行编号，重复调用会跳过）。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..repositories.bug_repo import BugRepository
from ..repositories.numbering import next_no
from ..repositories.sys_config_repo import SysConfigRepository

CONFIG_AUTO_BUG = "automation.auto_bug"

_FALSE_VALUES = {"", "0", "false", "off", "no", "none"}


def enabled(conn: sqlite3.Connection) -> bool:
    raw = str(SysConfigRepository(conn).get_value(CONFIG_AUTO_BUG, "") or "").strip().lower()
    return raw not in _FALSE_VALUES


def _already_created(conn: sqlite3.Connection, project_id: int, marker: str) -> bool:
    row = conn.execute(
        "SELECT id FROM biz_bug WHERE project_id = ? AND is_deleted = 0"
        " AND description LIKE ? LIMIT 1",
        (int(project_id), f"%{marker}%"),
    ).fetchone()
    return row is not None


def _create_bug(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    description: str,
    actual_result: str,
    bug_type: str,
    reporter: str,
    case_ids: list[int] | None = None,
) -> int:
    repo = BugRepository(conn)
    bug_id = repo.insert({
        "project_id": int(project_id),
        "bug_no": next_no(conn, "biz_bug", "bug_no", "BUG"),
        "title": title[:500],
        "severity": "P2",
        "priority": "P2",
        "status": "open",
        "bug_type": bug_type,
        "description": description[:100000],
        "actual_result": actual_result[:10000],
        "reporter": reporter[:64],
    })
    ids = [int(item) for item in (case_ids or []) if item]
    if ids:
        repo.link_cases(bug_id, list(dict.fromkeys(ids)), "caused_by")
    conn.commit()
    return bug_id


def from_scenario(
    conn: sqlite3.Connection,
    scenario: dict[str, Any],
    run_row: dict[str, Any],
    runs: list[dict[str, Any]],
) -> int | None:
    """接口场景执行失败 → 自动建缺陷；未启用、执行通过或已建过则返回 None。"""
    if not enabled(conn):
        return None
    run_no = str(run_row.get("run_no") or "")
    marker = f"场景执行记录 {run_no}"
    if _already_created(conn, int(scenario["project_id"]), marker):
        return None
    failed: list[dict[str, Any]] = []
    for record in runs:
        for step in record.get("steps") or []:
            if not step.get("passed"):
                failed.append(step)
    if not failed:
        return None
    lines = [f"场景「{scenario.get('name')}」执行 {run_no} 未通过，自动转缺陷。", marker, ""]
    for step in failed[:20]:
        lines.append(
            f"步骤 {step.get('stepNo')} {step.get('name')}："
            f"HTTP {step.get('statusCode') or '-'}，{step.get('message') or '断言未通过'}"
        )
        for assertion in step.get("assertions") or []:
            if not assertion.get("passed"):
                lines.append(
                    f"  ✗ {assertion.get('name')}：期望 {assertion.get('expected') or '-'}"
                    f" / 实际 {assertion.get('actual') or '-'}"
                )
    first = failed[0]
    return _create_bug(
        conn,
        project_id=int(scenario["project_id"]),
        title=f"[接口场景] {scenario.get('name')} 执行失败",
        description="\n".join(lines),
        actual_result=str(first.get("message") or f"{len(failed)} 个步骤失败"),
        bug_type="interface",
        reporter="自动转缺陷 · 接口场景",
    )


def from_automation(
    conn: sqlite3.Connection,
    script: dict[str, Any],
    run_row: dict[str, Any],
    case_results: list[dict[str, Any]],
    error_message: str,
) -> int | None:
    """自动化脚本失败 / 超时 / 异常 → 自动建缺陷。"""
    if not enabled(conn):
        return None
    run_no = str(run_row.get("run_no") or "")
    marker = f"自动化执行记录 {run_no}"
    if _already_created(conn, int(script["project_id"]), marker):
        return None
    failed = [item for item in case_results if str(item.get("result") or "") != "passed"]
    lines = [
        f"脚本「{script.get('name')}」（{script.get('script_no')}）执行 {run_no} 未通过，自动转缺陷。",
        marker,
        f"执行状态：{run_row.get('status')}；退出码：{run_row.get('exit_code')}",
    ]
    if error_message:
        lines.append(f"错误信息：{error_message[:1000]}")
    for item in failed[:20]:
        lines.append(
            f"- {item.get('caseNo') or item.get('testName') or '脚本'}："
            f"{item.get('message') or item.get('result') or '失败'}"
        )
    if len(lines) == 3 and not failed:
        lines.append("脚本进程退出非 0，未回写具体用例结果，请查看执行日志。")
    case_ids = [int(item["caseId"]) for item in failed if item.get("caseId")]
    return _create_bug(
        conn,
        project_id=int(script["project_id"]),
        title=f"[自动化] {script.get('name')} 执行失败",
        description="\n".join(lines),
        actual_result=str(error_message or run_row.get("status") or "执行失败"),
        bug_type="functional",
        reporter="自动转缺陷 · 自动化执行",
        case_ids=case_ids,
    )
