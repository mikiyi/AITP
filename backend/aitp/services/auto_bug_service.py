"""执行失败转缺陷：支持「关闭 / 待审 / 直接建」三档（V2.7.0）。

覆盖两条执行链路（不介入人工录入）：
  - 接口 / App 场景：一次场景执行未通过 → 按失败步骤生成缺陷或待审草稿；
  - 自动化脚本：脚本执行失败 / 超时 / 异常 → 按失败用例结果生成缺陷或待审草稿。

配置键：
  - ``automation.auto_bug_mode`` = off / review / auto，默认 review（半自动：
    先落 ``biz_bug_draft`` 待审区，人在缺陷页确认后才创建正式缺陷）；
  - 兼容旧键 ``automation.auto_bug``：true 等价 auto，false 等价 off。
同一次执行只建一条（描述里带执行编号，重复调用会跳过）。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso
from ..repositories.bug_repo import BugRepository
from ..repositories.sys_config_repo import SysConfigRepository

CONFIG_AUTO_BUG = "automation.auto_bug"
CONFIG_AUTO_BUG_MODE = "automation.auto_bug_mode"
MODES = ("off", "review", "auto")

_FALSE_VALUES = {"", "0", "false", "off", "no", "none"}


def mode(conn: sqlite3.Connection) -> str:
    """当前流转模式：off=关闭 / review=待审草稿 / auto=直接建缺陷。"""
    repo = SysConfigRepository(conn)
    raw = str(repo.get_value(CONFIG_AUTO_BUG_MODE, "") or "").strip().lower()
    if raw in MODES:
        return raw
    legacy = str(repo.get_value(CONFIG_AUTO_BUG, "") or "").strip().lower()
    return "auto" if legacy not in _FALSE_VALUES else "off"


def enabled(conn: sqlite3.Connection) -> bool:
    """兼容旧调用：只要不是 off 就认为开启（review 也算）。"""
    return mode(conn) != "off"


def _already_created(conn: sqlite3.Connection, project_id: int, marker: str) -> bool:
    row = conn.execute(
        "SELECT id FROM biz_bug WHERE project_id = ? AND is_deleted = 0"
        " AND description LIKE ? LIMIT 1",
        (int(project_id), f"%{marker}%"),
    ).fetchone()
    return row is not None


def _already_drafted(conn: sqlite3.Connection, project_id: int, marker: str) -> bool:
    row = conn.execute(
        "SELECT id FROM biz_bug_draft WHERE project_id = ? AND is_deleted = 0"
        " AND status = 'pending' AND description LIKE ? LIMIT 1",
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
    source_ref: str = "",
    case_ids: list[int] | None = None,
) -> int:
    repo = BugRepository(conn)
    bug_id = repo.insert({
        "project_id": int(project_id),
        "bug_no": repo.next_bug_no("automation"),
        "source": "automation",
        "source_ref": source_ref[:200],
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


def _create_draft(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    description: str,
    actual_result: str,
    bug_type: str,
    reporter: str,
    source: str,
    source_ref: str = "",
    case_ids: list[int] | None = None,
) -> int:
    now = utcnow_iso()
    cursor = conn.execute("""
        INSERT INTO biz_bug_draft
            (project_id, source, source_ref, title, description, actual_result,
             severity, priority, bug_type, reporter, case_ids_json, status,
             created_at, updated_at, is_deleted)
        VALUES (?, ?, ?, ?, ?, ?, 'P2', 'P2', ?, ?, ?, 'pending', ?, ?, 0)
    """, (
        int(project_id), source[:40], source_ref[:200], title[:500],
        description[:100000], actual_result[:10000], bug_type[:40], reporter[:64],
        json.dumps([int(item) for item in (case_ids or []) if item], ensure_ascii=False),
        now, now,
    ))
    conn.commit()
    return int(cursor.lastrowid or 0)


def _emit(
    conn: sqlite3.Connection,
    *,
    marker: str,
    project_id: int,
    title: str,
    description: str,
    actual_result: str,
    bug_type: str,
    reporter: str,
    source: str,
    source_ref: str,
    case_ids: list[int] | None = None,
) -> dict[str, Any] | None:
    current = mode(conn)
    if current == "off":
        return None
    if current == "auto":
        if _already_created(conn, project_id, marker):
            return None
        bug_id = _create_bug(
            conn, project_id=project_id, title=title, description=description,
            actual_result=actual_result, bug_type=bug_type, reporter=reporter,
            source_ref=source_ref, case_ids=case_ids,
        )
        return {"mode": "auto", "bugId": bug_id, "draftId": None}
    if _already_drafted(conn, project_id, marker):
        return None
    draft_id = _create_draft(
        conn, project_id=project_id, title=title, description=description,
        actual_result=actual_result, bug_type=bug_type, reporter=reporter,
        source=source, source_ref=source_ref, case_ids=case_ids,
    )
    return {"mode": "review", "bugId": None, "draftId": draft_id}


def from_scenario(
    conn: sqlite3.Connection,
    scenario: dict[str, Any],
    run_row: dict[str, Any],
    runs: list[dict[str, Any]],
) -> dict[str, Any] | None:
    """场景执行失败 → 按模式建缺陷或待审草稿；关闭、执行通过或已建过返回 None。"""
    if mode(conn) == "off":
        return None
    run_no = str(run_row.get("run_no") or "")
    marker = f"场景执行记录 {run_no}"
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
            f"{step.get('request', {}).get('method') or 'HTTP'} "
            f"{step.get('statusCode') or '-'}，{step.get('message') or '断言未通过'}"
        )
        for assertion in step.get("assertions") or []:
            if not assertion.get("passed"):
                lines.append(
                    f"  ✗ {assertion.get('name')}：期望 {assertion.get('expected') or '-'}"
                    f" / 实际 {assertion.get('actual') or '-'}"
                )
    first = failed[0]
    return _emit(
        conn,
        marker=marker,
        project_id=int(scenario["project_id"]),
        title=f"[场景] {scenario.get('name')} 执行失败",
        description="\n".join(lines),
        actual_result=str(first.get("message") or f"{len(failed)} 个步骤失败"),
        bug_type="interface",
        reporter="自动转缺陷 · 场景执行",
        source="scenario",
        source_ref=f"scenario-run:{run_no}",
    )


def from_automation(
    conn: sqlite3.Connection,
    script: dict[str, Any],
    run_row: dict[str, Any],
    case_results: list[dict[str, Any]],
    error_message: str,
) -> dict[str, Any] | None:
    """自动化脚本失败 / 超时 / 异常 → 按模式建缺陷或待审草稿。"""
    if mode(conn) == "off":
        return None
    run_no = str(run_row.get("run_no") or "")
    marker = f"自动化执行记录 {run_no}"
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
    return _emit(
        conn,
        marker=marker,
        project_id=int(script["project_id"]),
        title=f"[自动化] {script.get('name')} 执行失败",
        description="\n".join(lines),
        actual_result=str(error_message or run_row.get("status") or "执行失败"),
        bug_type="functional",
        reporter="自动转缺陷 · 自动化执行",
        source="automation",
        source_ref=f"automation-run:{run_no}",
        case_ids=case_ids,
    )
