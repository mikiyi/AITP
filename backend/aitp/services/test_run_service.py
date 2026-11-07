"""测试执行闭环服务：建轮次、快照用例、回写结果、失败转缺陷。

轮次是单次执行的唯一载体：创建时把用例内容冻结进 biz_test_run_case，
执行结果同时写入轮次用例与 biz_case_execution 流水，失败可一键转缺陷。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.enums import BUG_OPEN_STATUSES, BUG_SEVERITY, BUG_TYPE
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.bug_repo import BugRepository
from ..repositories.base import loads
from ..repositories.test_case_repo import TestCaseRepository
from ..repositories.test_run_repo import (
    CASE_STATUS_OF,
    RUN_CASE_RESULTS,
    RUN_COMPLETED,
    RUN_FAILED,
    RUN_NOT_EXECUTED,
    RUN_PASSED,
    TestRunCaseRepository,
    TestRunRepository,
    run_case_summary,
    run_summary,
)
from . import notification_service


def select_case_ids(conn: sqlite3.Connection, project_id: int,
                    filters: dict[str, Any] | None = None) -> list[int]:
    """按项目 + 白名单过滤条件选用例，供「建轮次」与「追加用例」共用。"""
    merged = {"project_id": project_id}
    merged.update(filters or {})
    rows = TestCaseRepository(conn).list_all(merged, None, limit=10000)
    return [int(row["id"]) for row in rows]


def _case_rows(conn: sqlite3.Connection, case_ids: list[int]) -> list[dict[str, Any]]:
    if not case_ids:
        return []
    placeholders = ",".join("?" for _ in case_ids)
    rows = conn.execute(
        f"SELECT * FROM biz_test_case WHERE is_deleted = 0 AND id IN ({placeholders})",
        case_ids,
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    return [by_id[case_id] for case_id in case_ids if case_id in by_id]


def _module_names(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[int, str]:
    ids = {int(row["module_id"]) for row in rows if row.get("module_id") not in (None, "")}
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    return {
        int(row["id"]): str(row["name"] or "")
        for row in conn.execute(
            f"SELECT id, name FROM biz_module WHERE id IN ({placeholders})", list(ids)
        )
    }


def _version_label(conn: sqlite3.Connection, version_id: int | None) -> str:
    if not version_id:
        return ""
    row = conn.execute(
        "SELECT version_code FROM biz_project_version WHERE id = ?", (version_id,)
    ).fetchone()
    return str(row["version_code"] or "") if row else ""


def _run_or_404(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = TestRunRepository(conn).get(run_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    return row


def _notify_if_completed(
    conn: sqlite3.Connection, run_id: int, previous_status: str
) -> None:
    """轮次从未完成变成完成时发一次通知；通知失败不影响结果写入。"""
    if previous_status == RUN_COMPLETED:
        return
    current = TestRunRepository(conn).get(run_id) or {}
    if str(current.get("status") or "") == RUN_COMPLETED:
        notification_service.notify_run_completed(conn, run_id)


def resolve_env(
    conn: sqlite3.Connection, env_id: int | None, env_name: str = ""
) -> tuple[int | None, str]:
    """校验环境引用并统一以环境档案名称为准，避免手填名称跑错环境。"""
    if env_id is None:
        return None, str(env_name or "").strip()
    row = conn.execute(
        "SELECT id, name FROM biz_test_env WHERE id = ? AND is_deleted = 0", (int(env_id),)
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.PARAM_ERROR, "测试环境不存在或已删除")
    return int(row["id"]), str(row["name"] or "")


def attach_envs(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补环境名称与类型，环境被删时仍保留执行当时的名称。"""
    env_ids = sorted({int(row["envId"]) for row in rows if row.get("envId")})
    if not env_ids:
        return rows
    placeholders = ",".join("?" for _ in env_ids)
    envs = {
        int(item["id"]): (str(item["name"] or ""), str(item["env_kind"] or "test"))
        for item in conn.execute(
            f"SELECT id, name, env_kind FROM biz_test_env WHERE id IN ({placeholders})",
            env_ids,
        ).fetchall()
    }
    for row in rows:
        env_id = row.get("envId")
        if env_id and int(env_id) in envs:
            name, kind = envs[int(env_id)]
            row["envName"] = name
            row["envKind"] = kind
            if not row.get("env"):
                row["env"] = name
        else:
            row["envName"] = row.get("env") or ""
            row["envKind"] = ""
    return rows


def create_run(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    name: str,
    case_ids: list[int],
    plan_id: int | None = None,
    version_id: int | None = None,
    env_id: int | None = None,
    env: str = "",
    owner: str = "",
    remark: str = "",
) -> dict[str, Any]:
    """创建轮次并冻结用例快照；没有用例时允许创建，之后再追加。"""
    if not case_ids:
        raise AppError(ErrorCode.PARAM_ERROR, "请至少选择一条用例")
    env_id, env = resolve_env(conn, env_id, env)
    rows = _case_rows(conn, case_ids)
    if not rows:
        raise AppError(ErrorCode.PARAM_ERROR, "所选用例不存在或已删除")
    wrong = [int(row["id"]) for row in rows if int(row["project_id"]) != int(project_id)]
    if wrong:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"用例 {', '.join(str(item) for item in wrong[:10])} 不属于当前项目，不能加入本轮次",
        )
    if version_id is not None:
        version = conn.execute(
            "SELECT project_id FROM biz_project_version WHERE id = ? AND is_deleted = 0",
            (int(version_id),),
        ).fetchone()
        if version is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目版本不存在")
        if int(version["project_id"]) != int(project_id):
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "项目版本不属于当前项目，不能挂到本轮次")
    if plan_id is not None:
        plan = conn.execute(
            "SELECT project_id, status FROM biz_test_plan WHERE id = ? AND is_deleted = 0",
            (int(plan_id),),
        ).fetchone()
        if plan is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试计划不存在")
        if int(plan["project_id"]) != int(project_id):
            raise AppError(ErrorCode.PARAM_ERROR, "计划不属于当前项目")
        if str(plan["status"] or "") == "archived":
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "计划已归档，请先启用后再发起轮次")

    run_repo = TestRunRepository(conn)
    run_id = run_repo.insert({
        "plan_id": plan_id,
        "project_id": project_id,
        "version_id": version_id,
        "env_id": env_id,
        "name": name,
        "env": env,
        "owner": owner,
        "status": "running",
        "remark": remark,
        "started_at": utcnow_iso(),
    })
    module_names = _module_names(conn, rows)
    case_repo = TestRunCaseRepository(conn)
    seq = case_repo.next_seq(run_id)
    for row in rows:
        case_repo.add_snapshot(
            run_id,
            row,
            seq,
            module_name=module_names.get(int(row["module_id"]), "") if row.get("module_id") else "",
        )
        seq += 1
    run_repo.refresh_counters(run_id)
    return run_summary(run_repo.get_detail(run_id) or {})


def add_cases(conn: sqlite3.Connection, run_id: int, case_ids: list[int]) -> dict[str, Any]:
    """向已有轮次追加用例；已在轮次内的跳过，保证同轮同用例只有一条。"""
    run = _run_or_404(conn, run_id)
    if str(run.get("status") or "") == RUN_COMPLETED:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "轮次已完成，不能再追加用例")
    case_repo = TestRunCaseRepository(conn)
    existing = case_repo.existing_case_ids(run_id, case_ids)
    pending = [case_id for case_id in case_ids if case_id not in existing]
    rows = _case_rows(conn, pending)
    wrong = [
        int(row["id"]) for row in rows
        if int(row["project_id"]) != int(run.get("project_id") or 0)
    ]
    if wrong:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"用例 {', '.join(str(item) for item in wrong[:10])} 不属于本轮次项目，不能追加",
        )
    module_names = _module_names(conn, rows)
    seq = case_repo.next_seq(run_id)
    added: list[int] = []
    for row in rows:
        case_repo.add_snapshot(
            run_id,
            row,
            seq,
            module_name=module_names.get(int(row["module_id"]), "") if row.get("module_id") else "",
        )
        added.append(int(row["id"]))
        seq += 1
    TestRunRepository(conn).refresh_counters(run_id)
    return {
        "runId": run_id,
        "added": added,
        "count": len(added),
        "skipped": [case_id for case_id in case_ids if case_id not in added],
    }


def record_result(
    conn: sqlite3.Connection,
    run_id: int,
    run_case_id: int,
    *,
    result: str,
    actual_result: str = "",
    remark: str = "",
    executed_by: str = "",
) -> dict[str, Any]:
    """回写单条结果：轮次用例 + 执行流水 + 用例全局状态，三者同一次事务。"""
    run = _run_or_404(conn, run_id)
    if str(run.get("status") or "") == RUN_COMPLETED:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "轮次已完成，结果已冻结，不能再修改")
    previous_status = str(run.get("status") or "")
    case_repo = TestRunCaseRepository(conn)
    row = case_repo.get_of_run(run_id, run_case_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "轮次用例不存在")

    previous = str(row.get("result") or RUN_NOT_EXECUTED)
    payload: dict[str, Any] = {
        "result": result,
        "actual_result": actual_result,
        "remark": remark,
        "executed_by": executed_by,
        "executed_at": utcnow_iso() if result != RUN_NOT_EXECUTED else None,
    }
    case_repo.update(run_case_id, payload)

    if result != previous or result != RUN_NOT_EXECUTED:
        conn.execute(
            """
            INSERT INTO biz_case_execution
                (case_id, bug_id, result, environment, version, executed_by, executed_at,
                 remark, run_id, run_case_id, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                int(row["case_id"]),
                row.get("bug_id"),
                CASE_STATUS_OF.get(result, "not_executed"),
                str(run.get("env") or ""),
                _version_label(conn, run.get("version_id")),
                executed_by,
                utcnow_iso(),
                remark,
                run_id,
                run_case_id,
            ),
        )
        TestCaseRepository(conn).update(
            int(row["case_id"]), {"status": CASE_STATUS_OF.get(result, "not_executed")}
        )

    TestRunRepository(conn).refresh_counters(run_id)
    _notify_if_completed(conn, run_id, previous_status)
    return {
        "runCase": run_case_summary(case_repo.get_of_run(run_id, run_case_id) or {}),
        "run": run_summary(TestRunRepository(conn).get_detail(run_id) or {}),
    }


def batch_record(conn: sqlite3.Connection, run_id: int,
                 entries: list[dict[str, Any]], executed_by: str = "") -> dict[str, Any]:
    """批量回写结果；不存在的行归入 skipped，不中断整批。"""
    updated: list[int] = []
    skipped: list[int] = []
    for entry in entries:
        raw_id = entry.get("id")
        if raw_id in (None, ""):
            continue
        try:
            run_case_id = int(raw_id)
        except (TypeError, ValueError):
            continue
        result = str(entry.get("result") or RUN_NOT_EXECUTED)
        if result not in RUN_CASE_RESULTS:
            skipped.append(run_case_id)
            continue
        try:
            record_result(
                conn,
                run_id,
                run_case_id,
                result=result,
                actual_result=str(entry.get("actualResult") or ""),
                remark=str(entry.get("remark") or ""),
                executed_by=str(entry.get("executedBy") or executed_by),
            )
        except AppError:
            skipped.append(run_case_id)
            continue
        updated.append(run_case_id)
    return {
        "updated": updated,
        "skipped": skipped,
        "run": run_summary(TestRunRepository(conn).get_detail(run_id) or {}),
    }


def complete_run(conn: sqlite3.Connection, run_id: int, remark: str | None = None) -> dict[str, Any]:
    previous = str(_run_or_404(conn, run_id).get("status") or "")
    repo = TestRunRepository(conn)
    payload: dict[str, Any] = {"status": RUN_COMPLETED, "finished_at": utcnow_iso()}
    if remark is not None:
        payload["remark"] = remark
    repo.update(run_id, payload)
    repo.refresh_counters(run_id)
    _notify_if_completed(conn, run_id, previous)
    return run_summary(repo.get_detail(run_id) or {})


def _steps_text(steps: list[Any]) -> str:
    lines = []
    for index, step in enumerate(steps, start=1):
        if isinstance(step, dict):
            action = str(step.get("action") or step.get("step") or step.get("content") or "").strip()
            expected = str(step.get("expected") or step.get("expectedResult") or "").strip()
            text = f"{index}. {action}" + (f"（预期：{expected}）" if expected else "")
            if action:
                lines.append(text)
        else:
            text = str(step).strip()
            if text:
                lines.append(f"{index}. {text}")
    return "\n".join(lines)


def create_bug_from_failure(
    conn: sqlite3.Connection,
    run_id: int,
    run_case_id: int,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """失败用例一键转缺陷：预填标题/步骤/预期/实际，并建立双向关联。"""
    run = _run_or_404(conn, run_id)
    case_repo = TestRunCaseRepository(conn)
    row = case_repo.get_of_run(run_id, run_case_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "轮次用例不存在")
    if row.get("bug_id"):
        return {"created": False, "bugId": int(row["bug_id"]), "runCaseId": run_case_id}
    if str(row.get("result")) != RUN_FAILED:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有失败的用例才能转缺陷")

    data = overrides or {}
    priority = str(row.get("priority") or "P2")
    severity = str(data.get("severity") or "")
    if severity not in BUG_SEVERITY:
        severity = priority if priority in BUG_SEVERITY else "P2"
    case_type = str(row.get("case_type") or "functional")
    bug_type = str(data.get("bugType") or "")
    if bug_type not in BUG_TYPE:
        bug_type = case_type if case_type in BUG_TYPE else "functional"

    steps = _steps_text(loads(row.get("steps_json"), []) or [])
    description_parts = [
        f"由测试轮次「{run.get('name') or run_id}」的失败用例自动创建。",
        f"用例：{row.get('case_no')} {row.get('title')}",
    ]
    if row.get("preconditions"):
        description_parts.append(f"前置条件：{row['preconditions']}")
    if steps:
        description_parts.append(f"操作步骤：\n{steps}")
    if row.get("expected_result"):
        description_parts.append(f"预期结果：{row['expected_result']}")
    if row.get("actual_result"):
        description_parts.append(f"实际结果：{row['actual_result']}")

    bug_repo = BugRepository(conn)
    project_id = int(run["project_id"])
    title = str(data.get("title") or f"[用例失败] {row.get('title')}")[:500]
    version_label = _version_label(conn, run.get("version_id"))
    bug_id = bug_repo.insert({
        "project_id": project_id,
        "bug_no": bug_repo.next_bug_no(project_id),
        "version_id": run.get("version_id"),
        "title": title,
        "severity": severity,
        "status": "open",
        "priority": priority,
        "bug_type": bug_type,
        "description": str(data.get("description") or "\n\n".join(description_parts))[:100000],
        "steps_json": str(row.get("steps_json") or "[]"),
        "expected_result": str(row.get("expected_result") or "")[:10000],
        "actual_result": str(row.get("actual_result") or "")[:10000],
        "assignee": str(data.get("assignee") or "")[:64],
        "reporter": str(data.get("reporter") or row.get("executed_by") or run.get("owner") or "local")[:64],
        "version": version_label[:64],
        "env": str(run.get("env") or "")[:200],
        "module": str(row.get("module") or "")[:200],
        "module_id": row.get("module_id"),
    })
    case_id = int(row["case_id"])
    bug_repo.link_cases(bug_id, [case_id], "caused_by")
    case_repo.update(run_case_id, {"bug_id": bug_id})
    conn.execute(
        "UPDATE biz_case_execution SET bug_id = ? WHERE run_id = ? AND run_case_id = ?",
        (bug_id, run_id, run_case_id),
    )
    bug_row = bug_repo.get(bug_id) or {}
    return {"created": True, "bugId": bug_id, "bugNo": bug_row.get("bug_no"), "runCaseId": run_case_id}


def run_priority_rates(stats: dict[str, Any], priority: str) -> tuple[int, int]:
    """返回某优先级的 (已执行数, 通过数)，分母与整体通过率一致，排除未执行。"""
    bucket = (stats.get("byPriority") or {}).get(priority) or {}
    total = sum(int(value) for value in bucket.values())
    executed = total - int(bucket.get(RUN_NOT_EXECUTED, 0))
    return executed, int(bucket.get(RUN_PASSED, 0))


def open_bug_counts(conn: sqlite3.Connection, project_id: int,
                    version_id: int | None = None) -> dict[str, int]:
    placeholders = ",".join("?" for _ in BUG_OPEN_STATUSES)
    sql = (
        "SELECT severity, COUNT(*) AS c FROM biz_bug WHERE is_deleted = 0"
        f" AND status IN ({placeholders}) AND project_id = ?"
    )
    params: list[Any] = list(BUG_OPEN_STATUSES) + [project_id]
    if version_id is not None:
        sql += " AND version_id = ?"
        params.append(version_id)
    sql += " GROUP BY severity"
    counts = {severity: 0 for severity in BUG_SEVERITY}
    for row in conn.execute(sql, params).fetchall():
        counts[str(row["severity"])] = int(row["c"])
    return counts
