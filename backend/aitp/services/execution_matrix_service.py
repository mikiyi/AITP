"""执行矩阵调度与差异归因（三期 GAP-43）。

矩阵 = 环境集合 × 端/浏览器，一次调度走任务队列逐格真实执行；
同一用例跨格对比，把差异归因为「环境特有 / 端特有 / 普遍失败 / 混合」，
普遍失败可一键转缺陷并带上各格证据。

- 接口模式：绑定一条测试轮次，按格覆盖环境的应用服务器地址逐格跑接口用例；
- 自动化模式：绑定若干脚本，按格注入 AITP_ENV_* / AITP_BROWSER 逐格跑脚本。
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Callable

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.automation_repo import AutomationRunRepository, AutomationScriptRepository
from ..repositories.base import dumps, loads
from ..repositories.bug_repo import BugRepository
from ..repositories.execution_matrix_repo import (
    ExecutionMatrixCellRepository,
    ExecutionMatrixRepository,
    ExecutionMatrixResultRepository,
)
from ..repositories.test_env_repo import TestEnvRepository
from ..repositories.test_run_repo import TestRunCaseRepository, TestRunRepository
from . import automation_service, automation_writeback, execution_protocol, interface_runner

MODE_INTERFACE = "interface"
MODE_AUTOMATION = "automation"
MODES: tuple[str, ...] = (MODE_INTERFACE, MODE_AUTOMATION)
MODE_LABELS = {MODE_INTERFACE: "接口用例", MODE_AUTOMATION: "自动化脚本"}

TARGET_DEFAULT = "default"
TARGET_ALL = "all"
TARGET_MODES: tuple[str, ...] = (TARGET_DEFAULT, TARGET_ALL)

MATRIX_DRAFT = "draft"
MATRIX_QUEUED = "queued"
MATRIX_RUNNING = "running"
MATRIX_DONE = "done"
MATRIX_FAILED = "failed"
MATRIX_CANCELLED = "cancelled"
MATRIX_STATUSES: tuple[str, ...] = (
    MATRIX_DRAFT, MATRIX_QUEUED, MATRIX_RUNNING, MATRIX_DONE, MATRIX_FAILED, MATRIX_CANCELLED,
)
MATRIX_STATUS_LABELS = {
    MATRIX_DRAFT: "草稿",
    MATRIX_QUEUED: "排队中",
    MATRIX_RUNNING: "执行中",
    MATRIX_DONE: "已完成",
    MATRIX_FAILED: "失败",
    MATRIX_CANCELLED: "已取消",
}

CELL_PENDING = "pending"
CELL_RUNNING = "running"
CELL_DONE = "done"
CELL_FAILED = "failed"
CELL_SKIPPED = "skipped"
CELL_STATUS_LABELS = {
    CELL_PENDING: "待执行",
    CELL_RUNNING: "执行中",
    CELL_DONE: "已完成",
    CELL_FAILED: "失败",
    CELL_SKIPPED: "已跳过",
}

CLASS_COMMON_PASS = "common_pass"
CLASS_COMMON_FAILURE = "common_failure"
CLASS_ENV_SPECIFIC = "environment_specific"
CLASS_TARGET_SPECIFIC = "target_specific"
CLASS_MIXED = "mixed"
CLASSIFICATION_LABELS = {
    CLASS_COMMON_PASS: "普遍通过",
    CLASS_COMMON_FAILURE: "普遍失败",
    CLASS_ENV_SPECIFIC: "环境特有",
    CLASS_TARGET_SPECIFIC: "端特有",
    CLASS_MIXED: "混合差异",
}

PROD_ENV_KIND = "prod"

#: 单矩阵环境数 / 浏览器数 / 脚本数上限：矩阵执行量成倍放大，必须留硬边界
MAX_ENVS = 10
MAX_TARGETS = 8
MAX_SCRIPTS = 20
MAX_BROWSERS = 6

ProgressFn = Callable[[int, int], None]
CancelFn = Callable[[], bool]


def _next_matrix_no(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT matrix_no FROM biz_execution_matrix WHERE is_deleted = 0"
    ).fetchall()
    used: set[int] = set()
    for row in rows:
        value = str(row[0] or "")
        head, _, seq = value.rpartition("-")
        if head.upper() != "MX" or not seq.isdigit():
            continue
        used.add(int(seq))
    index = 1
    while index in used:
        index += 1
    return f"MX-{index:03d}"


def _project_or_404(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, name FROM biz_project WHERE id = ? AND is_deleted = 0",
        (int(project_id),),
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    return dict(row)


def _load_envs(conn: sqlite3.Connection, env_ids: list[int]) -> list[dict[str, Any]]:
    if not env_ids:
        raise AppError(ErrorCode.PARAM_ERROR, "请至少选择一个测试环境")
    if len(env_ids) > MAX_ENVS:
        raise AppError(ErrorCode.PARAM_ERROR, f"矩阵最多选 {MAX_ENVS} 个环境")
    unique_ids = list(dict.fromkeys(env_ids))
    repo = TestEnvRepository(conn)
    envs: list[dict[str, Any]] = []
    for env_id in unique_ids:
        row = repo.get_by_id(int(env_id))
        if row is None:
            raise AppError(ErrorCode.PARAM_ERROR, "所选测试环境不存在或已删除")
        envs.append(row)
    return envs


def _interface_targets(conn: sqlite3.Connection, env: dict[str, Any], target_mode: str) -> list[dict[str, str]]:
    """展开一格的「端」：默认只跑环境首台应用；all 展开全部应用服务器。"""
    env_name = str(env.get("name") or "")
    if target_mode == TARGET_ALL:
        servers = TestEnvRepository(conn).list_app_servers(int(env["id"]))
        targets = [
            {"target": str(item.get("base_url") or "").strip(), "label": str(item.get("base_url") or "").strip()}
            for item in servers if str(item.get("base_url") or "").strip()
        ]
        if len(targets) > MAX_TARGETS:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"环境「{env_name}」配置了 {len(targets)} 台应用服务器，超过矩阵单环境 {MAX_TARGETS} 台上限",
            )
        if targets:
            return targets
    base = str(env.get("app_base_url") or "").strip()
    if not base:
        raise AppError(ErrorCode.PARAM_ERROR, f"环境「{env_name}」未配置应用服务器地址，无法加入矩阵")
    return [{"target": base, "label": base}]


def _browser_targets(raw: Any) -> list[dict[str, str]]:
    browsers = raw if isinstance(raw, list) else []
    values = [str(item or "").strip() for item in browsers]
    values = [item for item in values if item]
    if not values:
        values = ["chromium"]
    unique: list[str] = []
    for item in values:
        if len(item) > 40:
            raise AppError(ErrorCode.PARAM_ERROR, f"浏览器标识过长：{item[:40]}")
        if item not in unique:
            unique.append(item)
    if len(unique) > MAX_BROWSERS:
        raise AppError(ErrorCode.PARAM_ERROR, f"浏览器维度最多 {MAX_BROWSERS} 个")
    return [{"target": item, "label": item} for item in unique]


def _load_scripts(conn: sqlite3.Connection, project_id: int, raw_ids: Any) -> list[dict[str, Any]]:
    ids = raw_ids if isinstance(raw_ids, list) else []
    if not ids:
        raise AppError(ErrorCode.PARAM_ERROR, "自动化矩阵请至少选择一个脚本")
    if len(ids) > MAX_SCRIPTS:
        raise AppError(ErrorCode.PARAM_ERROR, f"矩阵最多选 {MAX_SCRIPTS} 个脚本")
    repo = AutomationScriptRepository(conn)
    scripts: list[dict[str, Any]] = []
    for raw in ids:
        try:
            script_id = int(raw)
        except (TypeError, ValueError):
            raise AppError(ErrorCode.PARAM_ERROR, "脚本编号必须是数字")
        row = repo.get(script_id)
        if row is None:
            raise AppError(ErrorCode.PARAM_ERROR, "所选自动化脚本不存在或已删除")
        if int(row.get("project_id") or 0) != int(project_id):
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "所选脚本不属于当前项目")
        scripts.append(row)
    return scripts


def _load_run(conn: sqlite3.Connection, project_id: int, raw_run_id: Any) -> dict[str, Any]:
    try:
        run_id = int(raw_run_id)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "接口矩阵必须绑定一条测试轮次")
    run = TestRunRepository(conn).get(run_id)
    if run is None:
        raise AppError(ErrorCode.PARAM_ERROR, "所选测试轮次不存在或已删除")
    if int(run.get("project_id") or 0) != int(project_id):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "所选轮次不属于当前项目")
    # 矩阵旁路执行、不回写轮次结论，已完成轮次也能复用为跨环境回归对象
    rows = [
        row for row in TestRunCaseRepository(conn).list_by_run(run_id)
        if str(row.get("case_type") or "") == "interface"
    ]
    if not rows:
        raise AppError(ErrorCode.PARAM_ERROR, "所选轮次里没有「接口测试」类型的用例，无法执行接口矩阵")
    return run


def create_matrix(conn: sqlite3.Connection, project_id: int, data: dict[str, Any]) -> dict[str, Any]:
    _project_or_404(conn, project_id)
    name = str(data.get("name") or "").strip()
    if not name:
        raise AppError(ErrorCode.PARAM_ERROR, "请填写矩阵名称")
    if len(name) > 100:
        raise AppError(ErrorCode.PARAM_ERROR, "矩阵名称最多 100 字")
    mode = str(data.get("mode") or MODE_INTERFACE)
    if mode not in MODES:
        raise AppError(ErrorCode.PARAM_ERROR, "mode 只能是 interface / automation")

    raw_env_ids = data.get("envIds")
    env_ids: list[int] = []
    if isinstance(raw_env_ids, list):
        for raw in raw_env_ids:
            try:
                env_ids.append(int(raw))
            except (TypeError, ValueError):
                raise AppError(ErrorCode.PARAM_ERROR, "envIds 必须是环境 id 数组")
    envs = _load_envs(conn, env_ids)

    run: dict[str, Any] | None = None
    scripts: list[dict[str, Any]] = []
    cells: list[dict[str, Any]] = []
    if mode == MODE_INTERFACE:
        run = _load_run(conn, project_id, data.get("runId"))
        target_mode = str(data.get("targetMode") or TARGET_DEFAULT)
        if target_mode not in TARGET_MODES:
            raise AppError(ErrorCode.PARAM_ERROR, "targetMode 只能是 default / all")
        targets_json: list[dict[str, str]] = []
        for env in envs:
            for target in _interface_targets(conn, env, target_mode):
                cells.append({
                    "env_id": int(env["id"]),
                    "env_name": str(env.get("name") or ""),
                    "env_kind": str(env.get("env_kind") or ""),
                    "target": target["target"],
                    "target_label": target["label"],
                    "browser": "",
                })
                if not any(item["target"] == target["target"] for item in targets_json):
                    targets_json.append({"target": target["target"], "label": target["label"]})
    else:
        target_mode = TARGET_DEFAULT
        scripts = _load_scripts(conn, project_id, data.get("scriptIds"))
        browsers = _browser_targets(data.get("browsers"))
        targets_json = [{"target": item["target"], "label": item["label"]} for item in browsers]
        for env in envs:
            for browser in browsers:
                cells.append({
                    "env_id": int(env["id"]),
                    "env_name": str(env.get("name") or ""),
                    "env_kind": str(env.get("env_kind") or ""),
                    "target": browser["target"],
                    "target_label": browser["label"],
                    "browser": browser["target"],
                })

    mrepo = ExecutionMatrixRepository(conn)
    matrix_id = mrepo.insert({
        "project_id": int(project_id),
        "matrix_no": _next_matrix_no(conn),
        "name": name,
        "mode": mode,
        "run_id": int(run["id"]) if run else None,
        "env_ids_json": dumps([int(env["id"]) for env in envs]),
        "script_ids_json": dumps([int(script["id"]) for script in scripts]),
        "target_mode": target_mode,
        "targets_json": dumps(targets_json),
        "status": MATRIX_DRAFT,
        "cell_total": len(cells),
        "cell_done": 0,
    })
    cell_repo = ExecutionMatrixCellRepository(conn)
    for cell in cells:
        cell_repo.insert({"matrix_id": matrix_id, "status": CELL_PENDING, **cell})
    return get_matrix_detail(conn, matrix_id)


def get_matrix(conn: sqlite3.Connection, matrix_id: int) -> dict[str, Any]:
    row = ExecutionMatrixRepository(conn).get(int(matrix_id))
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "执行矩阵不存在")
    return row


# ---------------------------------------------------------------------
# 汇总与调度
# ---------------------------------------------------------------------

def _run_name(conn: sqlite3.Connection, run_id: Any) -> str:
    if not run_id:
        return ""
    row = TestRunRepository(conn).get(int(run_id))
    return str(row.get("name") or "") if row else ""


def matrix_summary(conn: sqlite3.Connection, row: dict[str, Any]) -> dict[str, Any]:
    mode = str(row.get("mode") or MODE_INTERFACE)
    status = str(row.get("status") or MATRIX_DRAFT)
    return {
        "id": int(row["id"]),
        "projectId": int(row["project_id"]),
        "matrixNo": str(row.get("matrix_no") or ""),
        "name": str(row.get("name") or ""),
        "mode": mode,
        "modeLabel": MODE_LABELS.get(mode, mode),
        "runId": row.get("run_id"),
        "runName": _run_name(conn, row.get("run_id")),
        "envIds": loads(row.get("env_ids_json"), []) or [],
        "scriptIds": loads(row.get("script_ids_json"), []) or [],
        "targetMode": str(row.get("target_mode") or TARGET_DEFAULT),
        "targets": loads(row.get("targets_json"), []) or [],
        "status": status,
        "statusLabel": MATRIX_STATUS_LABELS.get(status, status),
        "cellTotal": int(row.get("cell_total") or 0),
        "cellDone": int(row.get("cell_done") or 0),
        "summary": loads(row.get("summary_json"), {}) or {},
        "prodConfirmedBy": str(row.get("prod_confirmed_by") or ""),
        "prodConfirmedAt": row.get("prod_confirmed_at"),
        "errorMessage": str(row.get("error_message") or ""),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def cell_summary(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or CELL_PENDING)
    return {
        "id": int(row["id"]),
        "envId": row.get("env_id"),
        "envName": str(row.get("env_name") or ""),
        "envKind": str(row.get("env_kind") or ""),
        "target": str(row.get("target") or ""),
        "targetLabel": str(row.get("target_label") or row.get("target") or ""),
        "browser": str(row.get("browser") or ""),
        "status": status,
        "statusLabel": CELL_STATUS_LABELS.get(status, status),
        "total": int(row.get("total") or 0),
        "passed": int(row.get("passed") or 0),
        "failed": int(row.get("failed") or 0),
        "errorMessage": str(row.get("error_message") or ""),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
    }


def get_matrix_detail(conn: sqlite3.Connection, matrix_id: int) -> dict[str, Any]:
    row = get_matrix(conn, matrix_id)
    cells = ExecutionMatrixCellRepository(conn).list_by_matrix(matrix_id)
    return {"matrix": matrix_summary(conn, row), "cells": [cell_summary(cell) for cell in cells]}


def delete_matrix(conn: sqlite3.Connection, matrix_id: int) -> dict[str, Any]:
    row = get_matrix(conn, matrix_id)
    if str(row.get("status") or "") in (MATRIX_QUEUED, MATRIX_RUNNING):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "矩阵正在执行，请先取消任务再删除")
    mrepo = ExecutionMatrixRepository(conn)
    cell_repo = ExecutionMatrixCellRepository(conn)
    for cell in cell_repo.list_by_matrix(matrix_id):
        cell_repo.soft_delete(int(cell["id"]))
    conn.execute(
        "UPDATE biz_execution_matrix_result SET is_deleted = 1 WHERE matrix_id = ? AND is_deleted = 0",
        (int(matrix_id),),
    )
    mrepo.soft_delete(matrix_id)
    return {"id": matrix_id, "deleted": True}


def run_matrix(
    conn: sqlite3.Connection,
    matrix_id: int,
    *,
    confirm_prod: bool = False,
    requested_by: str = "",
) -> dict[str, Any]:
    """一次调度：生产环境必须显式确认（默认不勾选），确认后投递任务队列。"""
    row = get_matrix(conn, matrix_id)
    if str(row.get("status") or "") in (MATRIX_QUEUED, MATRIX_RUNNING):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "矩阵正在执行，请等本轮结束")
    prod_envs = [
        str(env.get("name") or "")
        for env in _load_envs(conn, [int(item) for item in (loads(row.get("env_ids_json"), []) or [])])
        if str(env.get("env_kind") or "") == PROD_ENV_KIND
    ]
    if prod_envs and not confirm_prod:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"矩阵包含生产环境（{'、'.join(prod_envs)}），请勾选「已确认在生产环境执行」后再调度",
        )

    from . import job_queue

    cell_repo = ExecutionMatrixCellRepository(conn)
    cell_repo.reset_for_run(matrix_id)
    task = job_queue.submit(
        conn,
        kind="matrix.run",
        title=f"执行矩阵 {row.get('matrix_no') or ''} · {row.get('name') or ''}".strip(),
        payload={"matrixId": matrix_id},
        priority=60,
    )
    ExecutionMatrixRepository(conn).update(matrix_id, {
        "status": MATRIX_QUEUED,
        "cell_done": 0,
        "error_message": "",
        "prod_confirmed_by": requested_by if prod_envs else "",
        "prod_confirmed_at": utcnow_iso() if prod_envs else None,
    })
    conn.execute(
        "UPDATE biz_execution_matrix_cell SET task_id = ? WHERE matrix_id = ? AND is_deleted = 0",
        (int(task["id"]), int(matrix_id)),
    )
    conn.commit()
    return {"queued": True, "task": task, "prodEnvs": prod_envs}


def mark_cancelled(conn: sqlite3.Connection, matrix_id: int) -> None:
    mrepo = ExecutionMatrixRepository(conn)
    cell_repo = ExecutionMatrixCellRepository(conn)
    for cell in cell_repo.list_by_matrix(matrix_id):
        if str(cell.get("status") or "") in (CELL_PENDING, CELL_RUNNING):
            cell_repo.update(int(cell["id"]), {
                "status": CELL_SKIPPED, "finished_at": utcnow_iso(),
            })
    mrepo.update(matrix_id, {"status": MATRIX_CANCELLED})
    conn.commit()


# ---------------------------------------------------------------------
# 逐格执行
# ---------------------------------------------------------------------

def _insert_result(
    conn: sqlite3.Connection,
    matrix_id: int,
    cell_id: int,
    *,
    case_id: Any,
    run_case_id: Any,
    case_no: str,
    title: str,
    result: str,
    status_code: Any = None,
    duration_ms: Any = None,
    message: str = "",
    assertions: Any = None,
) -> None:
    ExecutionMatrixResultRepository(conn).insert({
        "matrix_id": int(matrix_id),
        "cell_id": int(cell_id),
        "case_id": case_id,
        "run_case_id": run_case_id,
        "case_no": str(case_no or ""),
        "title": str(title or ""),
        "result": result,
        "status_code": status_code,
        "duration_ms": duration_ms,
        "message": str(message or "")[:2000],
        "assertions_json": dumps(assertions or []),
    })


def _automation_assertions(conn: sqlite3.Connection, automation_run_id: int) -> list[dict[str, Any]]:
    row = conn.execute(
        "SELECT assertions_json FROM biz_execution_evidence"
        " WHERE source_type = 'automation' AND source_row_id = ? AND is_deleted = 0"
        " ORDER BY id DESC LIMIT 1",
        (int(automation_run_id),),
    ).fetchone()
    return loads(row["assertions_json"], []) if row else []


def _run_interface_cell(
    conn: sqlite3.Connection,
    matrix: dict[str, Any],
    cell: dict[str, Any],
    timeout: int,
) -> tuple[int, int, int]:
    summary = interface_runner.run_batch(
        conn,
        int(matrix["run_id"]),
        env_id=int(cell["env_id"]),
        timeout=timeout,
        executed_by="执行矩阵",
        base_url=str(cell.get("target") or "") or None,
        writeback=False,
    )
    total = 0
    passed = 0
    failed = 0
    for detail in summary.get("details") or []:
        total += 1
        result = str(detail.get("result") or "failed")
        if result == "passed":
            passed += 1
        else:
            failed += 1
        _insert_result(
            conn, int(matrix["id"]), int(cell["id"]),
            case_id=detail.get("caseId"),
            run_case_id=detail.get("runCaseId"),
            case_no=str(detail.get("caseNo") or ""),
            title=str(detail.get("title") or ""),
            result=result,
            status_code=detail.get("statusCode"),
            duration_ms=detail.get("durationMs"),
            message=str(detail.get("message") or ""),
            assertions=detail.get("assertions") or [],
        )
    return total, passed, failed


def _run_automation_cell(
    conn: sqlite3.Connection,
    matrix: dict[str, Any],
    cell: dict[str, Any],
    scripts: list[dict[str, Any]],
    db_path: Path,
    timeout: int,
    cancel_check: CancelFn | None,
) -> tuple[int, int, int]:
    env_id = int(cell["env_id"])
    env = TestEnvRepository(conn).get_by_id(env_id) or {}
    extra_env = {
        "AITP_ENV_ID": str(env_id),
        "AITP_ENV_NAME": str(env.get("name") or ""),
        "AITP_ENV_KIND": str(env.get("env_kind") or ""),
        "AITP_BASE_URL": str(env.get("app_base_url") or ""),
        "AITP_BROWSER": str(cell.get("browser") or cell.get("target") or ""),
        "AITP_MATRIX_ID": str(int(matrix["id"])),
        "AITP_MATRIX_CELL_ID": str(int(cell["id"])),
    }
    test_run_id = int(matrix["run_id"]) if matrix.get("run_id") else None
    total = 0
    passed = 0
    failed = 0
    for script in scripts:
        if cancel_check is not None and cancel_check():
            from . import job_queue
            raise job_queue.TaskCancelled()
        result_row = automation_service.execute_script(
            conn, script, db_path, timeout, test_run_id,
            cancel_check=cancel_check, extra_env=extra_env, env_id=env_id,
        )
        run_id = int(result_row.get("id") or 0)
        conn.execute(
            "UPDATE biz_automation_run SET matrix_cell_id = ? WHERE id = ?",
            (int(cell["id"]), run_id),
        )
        conn.commit()
        status = str(result_row.get("status") or "")
        run_result = "passed" if status == "success" else ("skipped" if status == "cancelled" else "failed")
        case_results = automation_writeback.case_results(conn, run_id)
        if case_results:
            for item in case_results:
                total += 1
                verdict = str(item.get("result") or "failed")
                if verdict == "passed":
                    passed += 1
                else:
                    failed += 1
                _insert_result(
                    conn, int(matrix["id"]), int(cell["id"]),
                    case_id=item.get("caseId"),
                    run_case_id=item.get("runCaseId"),
                    case_no=str(item.get("caseNo") or ""),
                    title=str(item.get("testName") or item.get("caseNo") or ""),
                    result=verdict,
                    status_code=None,
                    duration_ms=result_row.get("duration_ms"),
                    message=str(item.get("message") or ""),
                    assertions=_automation_assertions(conn, run_id),
                )
        else:
            total += 1
            if run_result == "passed":
                passed += 1
            else:
                failed += 1
            _insert_result(
                conn, int(matrix["id"]), int(cell["id"]),
                case_id=None,
                run_case_id=None,
                case_no=str(script.get("script_no") or ""),
                title=str(script.get("name") or ""),
                result=run_result,
                status_code=result_row.get("exit_code"),
                duration_ms=result_row.get("duration_ms"),
                message=str(result_row.get("error_message") or ""),
                assertions=_automation_assertions(conn, run_id),
            )
    return total, passed, failed


def execute_matrix(
    conn: sqlite3.Connection,
    matrix_id: int,
    db_path: Path,
    *,
    on_progress: ProgressFn | None = None,
    cancel_check: CancelFn | None = None,
) -> dict[str, Any]:
    """任务队列入口：逐格真实执行并落结果；单格失败不中断整轮。"""
    matrix = get_matrix(conn, matrix_id)
    mode = str(matrix.get("mode") or MODE_INTERFACE)
    mrepo = ExecutionMatrixRepository(conn)
    cell_repo = ExecutionMatrixCellRepository(conn)
    mrepo.update(matrix_id, {"status": MATRIX_RUNNING, "cell_done": 0, "error_message": ""})
    conn.commit()

    scripts: list[dict[str, Any]] = []
    if mode == MODE_AUTOMATION:
        script_repo = AutomationScriptRepository(conn)
        scripts = [
            row for row in (
                script_repo.get(int(script_id))
                for script_id in (loads(matrix.get("script_ids_json"), []) or [])
            )
            if row is not None
        ]
        if not scripts:
            mrepo.update(matrix_id, {"status": MATRIX_FAILED, "error_message": "矩阵内脚本已不存在"})
            conn.commit()
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "矩阵内脚本已不存在")
        category = str(scripts[0].get("category") or "web")
        automation_service.require_executable(conn, category, str(scripts[0].get("framework") or ""))

    timeout = 300
    cells = cell_repo.list_by_matrix(matrix_id)
    total_cells = len(cells)
    done = 0
    cell_failures = 0
    for cell in cells:
        if cancel_check is not None and cancel_check():
            from . import job_queue
            raise job_queue.TaskCancelled()
        cell_repo.update(int(cell["id"]), {"status": CELL_RUNNING, "started_at": utcnow_iso()})
        conn.commit()
        try:
            if mode == MODE_INTERFACE:
                total, passed_count, failed_count = _run_interface_cell(conn, matrix, cell, timeout)
            else:
                total, passed_count, failed_count = _run_automation_cell(
                    conn, matrix, cell, scripts, db_path, timeout, cancel_check,
                )
            cell_repo.update(int(cell["id"]), {
                "status": CELL_DONE,
                "total": total,
                "passed": passed_count,
                "failed": failed_count,
                "error_message": "",
                "finished_at": utcnow_iso(),
            })
        except Exception as exc:  # noqa: BLE001 - 单格失败只影响本格，整轮继续
            from . import job_queue
            if isinstance(exc, job_queue.TaskCancelled):
                cell_repo.update(int(cell["id"]), {"status": CELL_SKIPPED, "finished_at": utcnow_iso()})
                conn.commit()
                raise
            cell_failures += 1
            cell_repo.update(int(cell["id"]), {
                "status": CELL_FAILED,
                "error_message": str(exc)[:2000],
                "finished_at": utcnow_iso(),
            })
        done += 1
        mrepo.update(matrix_id, {"cell_done": done})
        conn.commit()
        if on_progress is not None:
            on_progress(done, total_cells)

    stats = _classify_stats(conn, matrix_id)
    mrepo.update(matrix_id, {
        "status": MATRIX_DONE,
        "summary_json": dumps(stats),
        "error_message": "" if cell_failures == 0 else f"{cell_failures} 个格子执行失败，详见各格记录",
    })
    conn.commit()
    return {"matrixId": matrix_id, "cells": total_cells, "cellFailures": cell_failures, "stats": stats}


# ---------------------------------------------------------------------
# 对比与差异归因
# ---------------------------------------------------------------------

def _group_verdicts(
    cells: list[dict[str, Any]],
    verdicts: dict[int, str],
    key: str,
) -> dict[str, list[str]]:
    groups: dict[str, list[str]] = {}
    for cell in cells:
        cell_id = int(cell["id"])
        if cell_id not in verdicts:
            continue
        group_key = str(cell.get(key) or cell.get("env_id") or "")
        groups.setdefault(group_key, []).append(verdicts[cell_id])
    return groups


def classify_case(cells: list[dict[str, Any]], verdicts: dict[int, str]) -> str:
    """按格结论归因：普遍失败 / 普遍通过 / 环境特有 / 端特有 / 混合差异。"""
    values = set(verdicts.values())
    if not values:
        return CLASS_MIXED
    if values == {"passed"}:
        return CLASS_COMMON_PASS
    if values == {"failed"}:
        return CLASS_COMMON_FAILURE

    def pure(groups: dict[str, list[str]]) -> bool:
        """该维度每个取值内部结论一致，且跨取值存在分歧——差异才能归到这个维度。"""
        verdict_sets = {frozenset(items) for items in groups.values()}
        return len(verdict_sets) > 1 and all(len(items) == 1 for items in verdict_sets)

    env_groups = _group_verdicts(cells, verdicts, "env_id")
    target_groups = _group_verdicts(cells, verdicts, "target")
    env_pure = pure(env_groups)
    target_pure = pure(target_groups)
    # 双纯只在一格一端（环境=端一一对应）时出现，环境是矩阵主轴，优先归因环境
    if env_pure:
        return CLASS_ENV_SPECIFIC
    if target_pure:
        return CLASS_TARGET_SPECIFIC
    return CLASS_MIXED


def compare(conn: sqlite3.Connection, matrix_id: int) -> dict[str, Any]:
    matrix = get_matrix(conn, matrix_id)
    cell_repo = ExecutionMatrixCellRepository(conn)
    cells = cell_repo.list_by_matrix(matrix_id)
    results = ExecutionMatrixResultRepository(conn).list_by_matrix(matrix_id)

    cases: dict[str, dict[str, Any]] = {}
    for row in results:
        key = str(row.get("case_no") or f"#{row.get('case_id') or 0}")
        entry = cases.setdefault(key, {
            "caseId": row.get("case_id"),
            "caseNo": key,
            "title": str(row.get("title") or ""),
            "runCaseId": row.get("run_case_id"),
            "cells": {},
            "bugId": None,
        })
        entry["cells"][str(int(row["cell_id"]))] = {
            "result": str(row.get("result") or ""),
            "statusCode": row.get("status_code"),
            "durationMs": row.get("duration_ms"),
            "message": str(row.get("message") or ""),
            "assertions": loads(row.get("assertions_json"), []) or [],
        }
        if row.get("bug_id"):
            entry["bugId"] = int(row["bug_id"])

    rows_out: list[dict[str, Any]] = []
    stats = {CLASS_COMMON_PASS: 0, CLASS_COMMON_FAILURE: 0, CLASS_ENV_SPECIFIC: 0,
             CLASS_TARGET_SPECIFIC: 0, CLASS_MIXED: 0}
    for entry in cases.values():
        verdicts: dict[int, str] = {}
        for cell in cells:
            hit = entry["cells"].get(str(int(cell["id"])))
            if hit:
                verdicts[int(cell["id"])] = "passed" if hit["result"] == "passed" else "failed"
        classification = classify_case(cells, verdicts)
        stats[classification] += 1
        entry["classification"] = classification
        entry["classificationLabel"] = CLASSIFICATION_LABELS[classification]
        entry["assertionDiffs"] = _assertion_diffs(cells, entry["cells"])
        rows_out.append(entry)

    rows_out.sort(key=lambda item: (
        0 if item["classification"] == CLASS_COMMON_FAILURE
        else 1 if item["classification"] in (CLASS_ENV_SPECIFIC, CLASS_TARGET_SPECIFIC)
        else 2,
        item["caseNo"],
    ))
    return {
        "matrix": matrix_summary(conn, matrix),
        "cells": [cell_summary(cell) for cell in cells],
        "rows": rows_out,
        "stats": {
            "commonPass": stats[CLASS_COMMON_PASS],
            "commonFailure": stats[CLASS_COMMON_FAILURE],
            "environmentSpecific": stats[CLASS_ENV_SPECIFIC],
            "targetSpecific": stats[CLASS_TARGET_SPECIFIC],
            "mixed": stats[CLASS_MIXED],
        },
    }


def _assertion_diffs(cells: list[dict[str, Any]], cell_results: dict[str, Any]) -> list[dict[str, Any]]:
    """同一断言名在各格的实际值/结论并排列出，供下钻。"""
    names: list[str] = []
    for result in cell_results.values():
        for assertion in result.get("assertions") or []:
            if not isinstance(assertion, dict):
                continue
            name = str(assertion.get("name") or "")
            if name and name not in names:
                names.append(name)
    diffs: list[dict[str, Any]] = []
    for name in names:
        per_cell: dict[str, Any] = {}
        verdicts: set[str] = set()
        for cell in cells:
            cell_id = str(int(cell["id"]))
            result = cell_results.get(cell_id) or {}
            found = None
            for assertion in result.get("assertions") or []:
                if isinstance(assertion, dict) and str(assertion.get("name") or "") == name:
                    found = assertion
                    break
            if found is None:
                continue
            per_cell[cell_id] = {
                "passed": bool(found.get("passed")),
                "expected": str(found.get("expected") or ""),
                "actual": str(found.get("actual") or ""),
                "message": str(found.get("message") or ""),
            }
            verdicts.add(f"{bool(found.get('passed'))}:{str(found.get('actual') or '')}")
        if len(verdicts) > 1:
            diffs.append({"name": name, "cells": per_cell})
    return diffs


def _classify_stats(conn: sqlite3.Connection, matrix_id: int) -> dict[str, Any]:
    payload = compare(conn, matrix_id)
    return {"stats": payload["stats"]}


# ---------------------------------------------------------------------
# 普遍失败一键转缺陷
# ---------------------------------------------------------------------

def create_bugs_from_matrix(
    conn: sqlite3.Connection,
    matrix_id: int,
    case_nos: list[str] | None = None,
    overrides: dict[str, Any] | None = None,
) -> dict[str, Any]:
    matrix = get_matrix(conn, matrix_id)
    payload = compare(conn, matrix_id)
    rows = [row for row in payload["rows"] if row["classification"] == CLASS_COMMON_FAILURE]
    if case_nos:
        wanted = {str(item) for item in case_nos}
        rows = [row for row in rows if str(row["caseNo"]) in wanted]
    if not rows:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "没有普遍失败的用例可转缺陷")

    data = overrides or {}
    bug_repo = BugRepository(conn)
    result_repo = ExecutionMatrixResultRepository(conn)
    cells = payload["cells"]
    created: list[dict[str, Any]] = []
    skipped: list[str] = []
    for row in rows:
        case_no = str(row["caseNo"])
        if row.get("bugId"):
            skipped.append(case_no)
            continue
        lines = [
            f"由执行矩阵「{matrix.get('name')}」（{matrix.get('matrix_no')}）的普遍失败用例自动创建。",
            f"用例：{case_no} {row.get('title') or ''}",
            f"范围：{len(cells)} 格（环境 × 端/浏览器），全部失败。",
            "各格结论：",
        ]
        for cell in cells:
            hit = (row.get("cells") or {}).get(str(int(cell["id"]))) or {}
            message = str(hit.get("message") or "")
            status = f"HTTP {hit['statusCode']}" if hit.get("statusCode") else ""
            lines.append(
                f"- {cell.get('envName')} / {cell.get('targetLabel') or cell.get('target')}"
                f"：失败{('（' + status + ('；' if message else '') + message[:200] + '）') if (status or message) else ''}"
            )
        diffs = row.get("assertionDiffs") or []
        if diffs:
            lines.append("断言差异：")
            for diff in diffs[:5]:
                lines.append(f"- {diff.get('name')}")
        actual_result = ""
        for cell in cells:
            hit = (row.get("cells") or {}).get(str(int(cell["id"]))) or {}
            if hit.get("message"):
                actual_result = str(hit.get("message"))
                break

        bug_id = bug_repo.insert({
            "project_id": int(matrix["project_id"]),
            "bug_no": bug_repo.next_bug_no(int(matrix["project_id"])),
            "title": str(data.get("title") or f"[执行矩阵] {row.get('title') or case_no}")[:500],
            "severity": str(data.get("severity") or "P2"),
            "status": "open",
            "priority": str(data.get("priority") or data.get("severity") or "P2"),
            "bug_type": str(data.get("bugType") or "functional"),
            "description": "\n".join(lines)[:100000],
            "actual_result": actual_result[:10000],
            "reporter": str(data.get("reporter") or "执行矩阵")[:64],
            "module": str(row.get("module") or "")[:200],
        })
        case_id = row.get("caseId")
        if case_id:
            bug_repo.link_cases(bug_id, [int(case_id)], "caused_by")
        evidence_ids = execution_protocol.link_evidence_to_bug(
            conn, int(bug_id),
            run_case_id=int(row["runCaseId"]) if row.get("runCaseId") else None,
            case_id=int(case_id) if case_id else None,
            run_id=int(matrix["run_id"]) if matrix.get("run_id") else None,
        )
        result_repo.mark_bug(int(matrix_id), case_no, int(bug_id))
        created.append({
            "caseNo": case_no,
            "title": row.get("title") or "",
            "bugId": int(bug_id),
            "evidenceIds": evidence_ids,
        })
    return {"created": created, "skipped": skipped, "total": len(created)}
