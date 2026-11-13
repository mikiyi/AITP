"""数据留存与归档（三期 GAP-44）。

按数据类型配置保留天数与到期动作：
  - archive：到期数据压缩打包到 data/archive/<类型>/<包号>.zip，索引进库可检索，原行软删；
  - delete：到期直接软删（操作日志表无软删列，归档后物理删）；
  - summary：仅留摘要——轮次留头部计数清用例快照、执行记录清 stdout/stderr、证据清请求/响应/日志正文。

引用保护（到期不清，逐条提示引用方）：
  - 证据被缺陷引用（bug_id 非空）；
  - 自动化执行记录被执行矩阵格子引用（matrix_cell_id 非空）；
  - 测试轮次被执行矩阵引用。
"""

from __future__ import annotations

import json
import sqlite3
import zipfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.archive_repo import (
    ArchiveBundleRepository,
    ArchiveItemRepository,
    bundle_public,
    item_public,
)
from ..repositories.sys_config_repo import SysConfigRepository

ACTION_ARCHIVE = "archive"
ACTION_DELETE = "delete"
ACTION_SUMMARY = "summary"

TYPE_TEST_RUN = "test_run"
TYPE_AUTOMATION_RUN = "automation_run"
TYPE_EVIDENCE = "evidence"
TYPE_LOG = "log"
TYPE_TODO = "todo"

DATA_TYPES: tuple[str, ...] = (
    TYPE_TEST_RUN, TYPE_AUTOMATION_RUN, TYPE_EVIDENCE, TYPE_LOG, TYPE_TODO,
)
TYPE_LABELS = {
    TYPE_TEST_RUN: "测试轮次",
    TYPE_AUTOMATION_RUN: "自动化执行记录",
    TYPE_EVIDENCE: "执行证据",
    TYPE_LOG: "操作日志",
    TYPE_TODO: "已完成待办",
}
ACTION_LABELS = {
    ACTION_ARCHIVE: "归档后清理",
    ACTION_DELETE: "直接清理",
    ACTION_SUMMARY: "仅留摘要",
}
#: 各类型允许的到期动作：日志与待办没有可留的「摘要」，轮次摘要是留头清明细
TYPE_ACTIONS: dict[str, tuple[str, ...]] = {
    TYPE_TEST_RUN: (ACTION_ARCHIVE, ACTION_DELETE, ACTION_SUMMARY),
    TYPE_AUTOMATION_RUN: (ACTION_ARCHIVE, ACTION_DELETE, ACTION_SUMMARY),
    TYPE_EVIDENCE: (ACTION_ARCHIVE, ACTION_DELETE, ACTION_SUMMARY),
    TYPE_LOG: (ACTION_ARCHIVE, ACTION_DELETE),
    TYPE_TODO: (ACTION_ARCHIVE, ACTION_DELETE),
}

CONFIG_AUTO = "retention.cleanup.auto"
CONFIG_LAST_RUN = "retention.cleanup.last_run"
AUTO_INTERVAL_HOURS = 20

_MAX_DUE = 5000


def _days_key(data_type: str) -> str:
    return f"retention.{data_type}.days"


def _action_key(data_type: str) -> str:
    return f"retention.{data_type}.action"


def _cutoff(days: int) -> str:
    moment = datetime.now(timezone.utc) - timedelta(days=days)
    return moment.isoformat(timespec="seconds").replace("+00:00", "Z")


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    repo = SysConfigRepository(conn)
    items: list[dict[str, Any]] = []
    for data_type in DATA_TYPES:
        raw_days = repo.get_value(_days_key(data_type), "0")
        try:
            days = int(str(raw_days or "0"))
        except (TypeError, ValueError):
            days = 0
        action = str(repo.get_value(_action_key(data_type), ACTION_ARCHIVE) or ACTION_ARCHIVE)
        if action not in TYPE_ACTIONS[data_type]:
            action = TYPE_ACTIONS[data_type][0]
        items.append({
            "dataType": data_type,
            "dataTypeLabel": TYPE_LABELS[data_type],
            "days": max(0, days),
            "action": action,
            "actionLabel": ACTION_LABELS.get(action, action),
            "actions": [
                {"value": value, "label": ACTION_LABELS[value]} for value in TYPE_ACTIONS[data_type]
            ],
        })
    return {
        "items": items,
        "autoCleanup": str(repo.get_value(CONFIG_AUTO, "1") or "1") != "0",
        "lastRunAt": str(repo.get_value(CONFIG_LAST_RUN, "") or ""),
    }


def update_settings(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    repo = SysConfigRepository(conn)
    entries = data.get("items")
    if not isinstance(entries, list):
        raise AppError(ErrorCode.PARAM_ERROR, "items 必须是数组")
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        data_type = str(entry.get("dataType") or "")
        if data_type not in DATA_TYPES:
            raise AppError(ErrorCode.PARAM_ERROR, f"未知数据类型：{data_type}")
        try:
            days = int(entry.get("days") or 0)
        except (TypeError, ValueError):
            raise AppError(ErrorCode.PARAM_ERROR, f"{TYPE_LABELS[data_type]}的保留天数必须是整数")
        if days < 0 or days > 3650:
            raise AppError(ErrorCode.PARAM_ERROR, f"{TYPE_LABELS[data_type]}的保留天数只能在 0~3650 之间")
        action = str(entry.get("action") or ACTION_ARCHIVE)
        if action not in TYPE_ACTIONS[data_type]:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"{TYPE_LABELS[data_type]}不支持动作「{action}」，可选："
                f"{'、'.join(TYPE_ACTIONS[data_type])}",
            )
        repo.set_value(_days_key(data_type), str(days), "int")
        repo.set_value(_action_key(data_type), action, "string")
    if "autoCleanup" in data:
        repo.set_value(CONFIG_AUTO, "1" if data.get("autoCleanup") else "0", "bool")
    conn.commit()
    return get_settings(conn)


def _due_rows(conn: sqlite3.Connection, data_type: str, cutoff: str) -> list[dict[str, Any]]:
    """到期且未被引用的行 + 被引用跳过的行，分别在 preview/cleanup 里计数。"""
    rows, _ = _due_rows_with_protected(conn, data_type, cutoff)
    return rows


def _due_rows_with_protected(
    conn: sqlite3.Connection, data_type: str, cutoff: str
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if data_type == TYPE_TEST_RUN:
        rows = conn.execute(
            "SELECT r.* FROM biz_test_run r WHERE r.is_deleted = 0 AND r.created_at < ?"
            " AND NOT EXISTS (SELECT 1 FROM biz_execution_matrix m"
            "                WHERE m.run_id = r.id AND m.is_deleted = 0)"
            " ORDER BY r.id LIMIT ?",
            (cutoff, _MAX_DUE),
        ).fetchall()
        protected = conn.execute(
            "SELECT r.id, r.name, m.matrix_no AS ref FROM biz_test_run r"
            " JOIN biz_execution_matrix m ON m.run_id = r.id AND m.is_deleted = 0"
            " WHERE r.is_deleted = 0 AND r.created_at < ? LIMIT 50",
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows], [
            {"id": int(row["id"]), "title": str(row["name"] or ""),
             "referencedBy": f"执行矩阵 {row['ref']}"} for row in protected
        ]
    if data_type == TYPE_AUTOMATION_RUN:
        rows = conn.execute(
            "SELECT * FROM biz_automation_run WHERE is_deleted = 0 AND created_at < ?"
            " AND matrix_cell_id IS NULL ORDER BY id LIMIT ?",
            (cutoff, _MAX_DUE),
        ).fetchall()
        protected = conn.execute(
            "SELECT r.id, r.run_no, r.script_name FROM biz_automation_run r"
            " WHERE r.is_deleted = 0 AND r.created_at < ? AND r.matrix_cell_id IS NOT NULL LIMIT 50",
            (cutoff,),
        ).fetchall()
        return [dict(row) for row in rows], [
            {"id": int(row["id"]), "title": f"{row['run_no']} {row['script_name'] or ''}".strip(),
             "referencedBy": "执行矩阵格子"} for row in protected
        ]
    if data_type == TYPE_EVIDENCE:
        rows = conn.execute(
            "SELECT * FROM biz_execution_evidence WHERE is_deleted = 0"
            " AND ((expires_at <> '' AND expires_at <= ?) OR created_at < ?)"
            " AND bug_id IS NULL ORDER BY id LIMIT ?",
            (utcnow_iso(), cutoff, _MAX_DUE),
        ).fetchall()
        protected = conn.execute(
            "SELECT e.id, e.title, e.bug_id, b.bug_no, b.title AS bug_title"
            " FROM biz_execution_evidence e"
            " LEFT JOIN biz_bug b ON b.id = e.bug_id"
            " WHERE e.is_deleted = 0 AND e.bug_id IS NOT NULL"
            " AND ((e.expires_at <> '' AND e.expires_at <= ?) OR e.created_at < ?) LIMIT 50",
            (utcnow_iso(), cutoff),
        ).fetchall()
        return [dict(row) for row in rows], [
            {"id": int(row["id"]), "title": str(row["title"] or ""),
             "referencedBy": f"缺陷 {row['bug_no'] or row['bug_id']} {row['bug_title'] or ''}".strip()}
            for row in protected
        ]
    if data_type == TYPE_LOG:
        rows = conn.execute(
            "SELECT * FROM sys_audit_log WHERE created_at < ? ORDER BY id LIMIT ?",
            (cutoff, _MAX_DUE),
        ).fetchall()
        return [dict(row) for row in rows], []
    if data_type == TYPE_TODO:
        rows = conn.execute(
            "SELECT * FROM biz_todo WHERE is_deleted = 0 AND status = 'done'"
            " AND COALESCE(completed_at, updated_at) < ? ORDER BY id LIMIT ?",
            (cutoff, _MAX_DUE),
        ).fetchall()
        return [dict(row) for row in rows], []
    raise AppError(ErrorCode.PARAM_ERROR, f"未知数据类型：{data_type}")


def preview(conn: sqlite3.Connection) -> dict[str, Any]:
    """各类型当前到期量预览：干跑，什么都不改。"""
    settings = get_settings(conn)
    items: list[dict[str, Any]] = []
    for item in settings["items"]:
        data_type = item["dataType"]
        days = int(item["days"])
        if days <= 0:
            due: list[dict[str, Any]] = []
            protected: list[dict[str, Any]] = []
        else:
            due, protected = _due_rows_with_protected(conn, data_type, _cutoff(days))
        items.append({**item, "dueCount": len(due), "protected": protected})
    return {"items": items, "autoCleanup": settings["autoCleanup"], "lastRunAt": settings["lastRunAt"]}


# ---------------------------------------------------------------------
# 归档打包
# ---------------------------------------------------------------------

def _archive_root(db_path: Path) -> Path:
    return db_path.parent / "archive"


def _item_title(data_type: str, row: dict[str, Any]) -> str:
    if data_type == TYPE_TEST_RUN:
        return str(row.get("name") or f"轮次 #{row.get('id')}")
    if data_type == TYPE_AUTOMATION_RUN:
        return f"{row.get('run_no') or ''} {row.get('script_name') or ''}".strip()
    if data_type == TYPE_EVIDENCE:
        return str(row.get("title") or f"证据 #{row.get('id')}")
    if data_type == TYPE_LOG:
        return f"{row.get('action') or ''} {row.get('resource_type') or ''} {row.get('resource_label') or ''}".strip()
    if data_type == TYPE_TODO:
        return str(row.get("title") or f"待办 #{row.get('id')}")
    return str(row.get("title") or "")


def _item_summary(data_type: str, row: dict[str, Any]) -> str:
    if data_type == TYPE_TEST_RUN:
        return f"共 {row.get('total') or 0} 过 {row.get('passed') or 0} 败 {row.get('failed') or 0} 状态 {row.get('status') or ''}"
    if data_type == TYPE_AUTOMATION_RUN:
        return f"状态 {row.get('status') or ''} 退出码 {row.get('exit_code') or '-'} 耗时 {row.get('duration_ms') or 0}ms"
    if data_type == TYPE_EVIDENCE:
        return f"结论 {row.get('result') or ''} 耗时 {row.get('duration_ms') or 0}ms 来源 {row.get('source_type') or ''}"
    if data_type == TYPE_LOG:
        return f"actor={row.get('actor') or ''} result={row.get('result') or ''}"
    if data_type == TYPE_TODO:
        return f"状态 {row.get('status') or ''} 优先级 {row.get('priority') or ''}"
    return ""


def _row_to_public(data_type: str, row: dict[str, Any]) -> dict[str, Any]:
    """归档入包前瘦身：大字段要么带、要么留摘要标记，包内仍存可复盘原文。"""
    if data_type == TYPE_EVIDENCE:
        return {
            "id": row.get("id"), "sourceType": row.get("source_type"),
            "sourceRowId": row.get("source_row_id"), "projectId": row.get("project_id"),
            "caseId": row.get("case_id"), "runId": row.get("run_id"),
            "runCaseId": row.get("run_case_id"), "bugId": row.get("bug_id"),
            "envName": row.get("env_name"), "title": row.get("title"),
            "result": row.get("result"), "durationMs": row.get("duration_ms"),
            "assertions": json.loads(str(row.get("assertions_json") or "[]")),
            "requestSummary": row.get("request_summary"),
            "responseSummary": row.get("response_summary"),
            "logSummary": row.get("log_summary"),
            "attachments": json.loads(str(row.get("attachments_json") or "[]")),
            "traceId": row.get("trace_id"), "executedAt": row.get("executed_at"),
            "expiresAt": row.get("expires_at"), "createdAt": row.get("created_at"),
        }
    if data_type == TYPE_AUTOMATION_RUN:
        return {
            "id": row.get("id"), "runNo": row.get("run_no"), "scriptId": row.get("script_id"),
            "scriptNo": row.get("script_no"), "scriptName": row.get("script_name"),
            "category": row.get("category"), "status": row.get("status"),
            "exitCode": row.get("exit_code"), "durationMs": row.get("duration_ms"),
            "stdout": row.get("stdout"), "stderr": row.get("stderr"),
            "errorMessage": row.get("error_message"), "startedAt": row.get("started_at"),
            "finishedAt": row.get("finished_at"), "createdAt": row.get("created_at"),
        }
    if data_type == TYPE_TEST_RUN:
        return {
            "id": row.get("id"), "planId": row.get("plan_id"), "projectId": row.get("project_id"),
            "versionId": row.get("version_id"), "envId": row.get("env_id"),
            "name": row.get("name"), "env": row.get("env"), "owner": row.get("owner"),
            "status": row.get("status"), "remark": row.get("remark"),
            "total": row.get("total"), "passed": row.get("passed"), "failed": row.get("failed"),
            "blocked": row.get("blocked"), "skipped": row.get("skipped"),
            "notExecuted": row.get("not_executed"), "startedAt": row.get("started_at"),
            "finishedAt": row.get("finished_at"), "createdAt": row.get("created_at"),
        }
    return dict(row)


def _write_bundle(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    data_type: str,
    action: str,
    rows: list[dict[str, Any]],
    created_by: str,
) -> dict[str, Any]:
    """把到期行打包成一个 zip 并落索引；返回 (bundle_row, items)。"""
    if not rows:
        return {"bundle": None, "items": []}
    now = utcnow_iso()
    bundle_repo = ArchiveBundleRepository(conn)
    bundle_no = _next_bundle_no(conn)
    type_dir = _archive_root(db_path) / data_type
    type_dir.mkdir(parents=True, exist_ok=True)
    file_path_rel = f"archive/{data_type}/{bundle_no}.zip"
    target = db_path.parent / file_path_rel
    period_from = str(rows[0].get("created_at") or "")
    period_to = str(rows[-1].get("created_at") or period_from)

    item_rows: list[dict[str, Any]] = []
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps({
            "bundleNo": bundle_no, "dataType": data_type, "dataTypeLabel": TYPE_LABELS[data_type],
            "action": action, "itemCount": len(rows), "periodFrom": period_from,
            "periodTo": period_to, "createdAt": now, "createdBy": created_by,
        }, ensure_ascii=False, indent=2))
        lines = [json.dumps(_row_to_public(data_type, row), ensure_ascii=False) for row in rows]
        archive.writestr("items.jsonl", "\n".join(lines))

    bundle_id = bundle_repo.insert({
        "bundle_no": bundle_no,
        "data_type": data_type,
        "action": action,
        "item_count": len(rows),
        "period_from": period_from,
        "period_to": period_to,
        "file_path": file_path_rel,
        "file_size": target.stat().st_size,
        "summary_json": json.dumps({"dataTypeLabel": TYPE_LABELS[data_type], "action": action}),
        "created_by": created_by,
    })
    item_repo = ArchiveItemRepository(conn)
    for row in rows:
        item_repo.insert({
            "bundle_id": bundle_id,
            "data_type": data_type,
            "ref_id": int(row.get("id") or 0),
            "title": _item_title(data_type, row)[:300],
            "happened_at": str(row.get("created_at") or row.get("happened_at") or ""),
            "summary": _item_summary(data_type, row)[:500],
        })
        item_rows.append({
            "refId": int(row.get("id") or 0),
            "title": _item_title(data_type, row)[:300],
            "summary": _item_summary(data_type, row)[:500],
        })
    return {
        "bundle": bundle_public(bundle_repo.get(bundle_id) or {}),
        "items": item_rows,
    }


def _next_bundle_no(conn: sqlite3.Connection) -> str:
    rows = conn.execute(
        "SELECT bundle_no FROM biz_archive_bundle WHERE is_deleted = 0"
    ).fetchall()
    used = {int(str(row[0]).split("-")[-1]) for row in rows if str(row[0]).split("-")[-1].isdigit()}
    index = 1
    while index in used:
        index += 1
    return f"ARC-{index:03d}"


# ---------------------------------------------------------------------
# 到期动作
# ---------------------------------------------------------------------

def _apply_archive(conn: sqlite3.Connection, data_type: str, rows: list[dict[str, Any]]) -> int:
    if data_type == TYPE_TEST_RUN:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE biz_test_run SET is_deleted = 1 WHERE id IN ({marks})", ids)
        conn.execute(f"UPDATE biz_test_run_case SET is_deleted = 1 WHERE run_id IN ({marks})", ids)
    elif data_type == TYPE_AUTOMATION_RUN:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE biz_automation_run SET is_deleted = 1 WHERE id IN ({marks})", ids)
    elif data_type == TYPE_EVIDENCE:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE biz_execution_evidence SET is_deleted = 1, purge_reason = '留存期归档'"
            f" WHERE id IN ({marks})", ids,
        )
    elif data_type == TYPE_LOG:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM sys_audit_log WHERE id IN ({marks})", ids)
    elif data_type == TYPE_TODO:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE biz_todo SET is_deleted = 1 WHERE id IN ({marks})", ids)
    return len(rows)


def _apply_delete(conn: sqlite3.Connection, data_type: str, rows: list[dict[str, Any]]) -> int:
    if data_type == TYPE_LOG:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"DELETE FROM sys_audit_log WHERE id IN ({marks})", ids)
        return len(rows)
    if data_type == TYPE_EVIDENCE:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE biz_execution_evidence SET is_deleted = 1, purge_reason = '保留期到期清理'"
            f" WHERE id IN ({marks})", ids,
        )
        return len(rows)
    return _apply_archive(conn, data_type, rows)


def _apply_summary(conn: sqlite3.Connection, data_type: str, rows: list[dict[str, Any]]) -> int:
    if data_type == TYPE_TEST_RUN:
        # 仅留摘要：留轮次头部与计数，清用例快照
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(f"UPDATE biz_test_run_case SET is_deleted = 1 WHERE run_id IN ({marks})", ids)
        return len(rows)
    if data_type == TYPE_AUTOMATION_RUN:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE biz_automation_run SET stdout = '', stderr = '', command_line = ''"
            f" WHERE id IN ({marks})", ids,
        )
        return len(rows)
    if data_type == TYPE_EVIDENCE:
        ids = [int(row["id"]) for row in rows]
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE biz_execution_evidence SET request_summary = '', response_summary = '',"
            f" log_summary = '', assertions_json = '[]', attachments_json = '[]',"
            f" purge_reason = '仅留摘要' WHERE id IN ({marks})", ids,
        )
        return len(rows)
    # 其它类型没有可留的摘要，退回删除
    return _apply_delete(conn, data_type, rows)


def run_cleanup(
    conn: sqlite3.Connection,
    db_path: Path,
    *,
    data_types: list[str] | None = None,
    dry_run: bool = False,
    created_by: str = "local",
) -> dict[str, Any]:
    """逐类型到期处理；被引用数据跳过并提示引用方。dry_run 只统计不动库。"""
    settings = get_settings(conn)
    targets = data_types or list(DATA_TYPES)
    summary: dict[str, Any] = {"dryRun": dry_run, "types": [], "protected": []}
    for item in settings["items"]:
        data_type = item["dataType"]
        if data_type not in targets:
            continue
        days = int(item["days"])
        action = str(item["action"])
        if days <= 0:
            summary["types"].append({
                "dataType": data_type, "dataTypeLabel": TYPE_LABELS[data_type],
                "due": 0, "archived": 0, "cleaned": 0, "skipped": 0, "action": action,
            })
            continue
        rows, protected = _due_rows_with_protected(conn, data_type, _cutoff(days))
        archived = 0
        cleaned = 0
        bundles: list[dict[str, Any]] = []
        if not dry_run and rows:
            if action == ACTION_ARCHIVE:
                packed = _write_bundle(conn, db_path, data_type=data_type, action=action,
                                       rows=rows, created_by=created_by)
                if packed["bundle"]:
                    bundles.append(packed["bundle"])
                    archived = len(rows)
                    cleaned += _apply_archive(conn, data_type, rows)
            elif action == ACTION_SUMMARY:
                cleaned += _apply_summary(conn, data_type, rows)
            else:
                cleaned += _apply_delete(conn, data_type, rows)
            conn.commit()
        summary["types"].append({
            "dataType": data_type, "dataTypeLabel": TYPE_LABELS[data_type],
            "due": len(rows), "archived": archived, "cleaned": cleaned,
            "skipped": len(protected), "action": action, "bundles": bundles,
        })
        summary["protected"].extend(
            {"dataType": data_type, "dataTypeLabel": TYPE_LABELS[data_type], **item}
            for item in protected
        )
    if not dry_run:
        SysConfigRepository(conn).set_value(CONFIG_LAST_RUN, utcnow_iso(), "string")
        conn.commit()
    return summary


def maybe_auto_cleanup(conn: sqlite3.Connection, db_path: Path) -> dict[str, Any] | None:
    """启动时按配置补一次自动清理：距上次超过 20 小时、且没有排队/运行中的清理任务才补。"""
    from . import job_queue

    repo = SysConfigRepository(conn)
    if str(repo.get_value(CONFIG_AUTO, "1") or "1") == "0":
        return None
    last = str(repo.get_value(CONFIG_LAST_RUN, "") or "")
    if last:
        try:
            last_at = datetime.fromisoformat(last.replace("Z", "+00:00"))
        except ValueError:
            last_at = None
        if last_at is not None and (datetime.now(timezone.utc) - last_at).total_seconds() < AUTO_INTERVAL_HOURS * 3600:
            return None
    queued = conn.execute(
        "SELECT COUNT(*) FROM sys_task WHERE kind = 'retention.cleanup'"
        " AND status IN ('queued', 'running')",
    ).fetchone()[0]
    if int(queued) > 0:
        return None
    task = job_queue.submit(
        conn, kind="retention.cleanup", title="数据留存到期清理",
        payload={"createdBy": "auto"}, priority=90,
    )
    return {"queued": True, "task": task}


# ---------------------------------------------------------------------
# 归档区检索与回看
# ---------------------------------------------------------------------

def list_bundles(
    conn: sqlite3.Connection,
    *,
    page_num: int = 1,
    page_size: int = 20,
    keyword: str | None = None,
    data_type: str | None = None,
) -> dict[str, Any]:
    """归档区检索：关键字命中条目标题/摘要（不解压），类型过滤按包裹类型。"""
    clauses: list[str] = ["b.is_deleted = 0"]
    params: list[Any] = []
    if data_type in DATA_TYPES:
        clauses.append("b.data_type = ?")
        params.append(data_type)
    if keyword:
        like = f"%{keyword}%"
        clauses.append(
            "(b.bundle_no LIKE ? OR EXISTS (SELECT 1 FROM biz_archive_item i"
            " WHERE i.bundle_id = b.id AND i.is_deleted = 0"
            " AND (i.title LIKE ? OR i.summary LIKE ?)))"
        )
        params.extend([like, like, like])
    where = " AND ".join(clauses)
    total = int(conn.execute(
        f"SELECT COUNT(*) FROM biz_archive_bundle b WHERE {where}", params
    ).fetchone()[0])
    limit = max(1, min(int(page_size or 20), 100))
    offset = (max(1, int(page_num or 1)) - 1) * limit
    rows = conn.execute(
        f"SELECT b.* FROM biz_archive_bundle b WHERE {where}"
        " ORDER BY b.id DESC LIMIT ? OFFSET ?",
        [*params, limit, offset],
    ).fetchall()
    return {
        "total": total,
        "pageNum": int(page_num),
        "pageSize": limit,
        "list": [bundle_public(dict(row)) for row in rows],
    }


def get_bundle(conn: sqlite3.Connection, bundle_id: int) -> dict[str, Any]:
    row = ArchiveBundleRepository(conn).get(int(bundle_id))
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "归档包裹不存在")
    items = ArchiveItemRepository(conn).list_by_bundle(int(bundle_id))
    return {"bundle": bundle_public(row), "items": [item_public(item) for item in items]}


def read_item(conn: sqlite3.Connection, bundle_id: int, ref_id: int) -> dict[str, Any]:
    """回看原文：按 bundle 打开 zip，items.jsonl 里定位 ref_id。"""
    bundle = ArchiveBundleRepository(conn).get(int(bundle_id))
    if bundle is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "归档包裹不存在")
    target = Path(str(bundle.get("file_path") or ""))
    # file_path 是相对 data 目录的，这里只允许落在 data 目录内
    root = conn_db_dir(conn).resolve()
    abs_path = (root / target).resolve()
    try:
        abs_path.relative_to(root)
    except ValueError:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "归档路径不合法") from None
    if not abs_path.is_file():
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "归档文件已不存在或路径失效")
    with zipfile.ZipFile(abs_path, "r") as archive:
        try:
            content = archive.read("items.jsonl").decode("utf-8")
        except KeyError:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "归档包缺少 items.jsonl") from None
    for line in content.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if int(row.get("id") or 0) == int(ref_id):
            return dict(row)
    raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "归档原文不存在")


def conn_db_dir(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    return Path(str(row[2])).parent if row and row[2] else Path(".")
