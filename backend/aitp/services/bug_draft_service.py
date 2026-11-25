"""缺陷待审区：自动化 / App 场景 / MCP 写入的半自动流转（V2.7.0）。

流程：执行失败或外部 AI 提交 → ``biz_bug_draft``（pending）
     → 人在缺陷页确认 → 创建正式 ``biz_bug`` 并回填 draft.bug_id
     → 或忽略（ignored），不产生正式缺陷。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso
from ..repositories.bug_repo import BugRepository

STATUS_PENDING = "pending"
STATUS_CONFIRMED = "confirmed"
STATUS_IGNORED = "ignored"

STATUS_LABELS = {STATUS_PENDING: "待确认", STATUS_CONFIRMED: "已转缺陷", STATUS_IGNORED: "已忽略"}


def _case_ids(raw: Any) -> list[int]:
    try:
        parsed = json.loads(str(raw or "[]"))
    except (TypeError, ValueError):
        parsed = []
    if not isinstance(parsed, list):
        return []
    result: list[int] = []
    for item in parsed:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result


def summary(row: dict[str, Any]) -> dict[str, Any]:
    status = str(row.get("status") or STATUS_PENDING)
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "source": row.get("source"),
        "sourceRef": row.get("source_ref"),
        "title": row.get("title"),
        "description": row.get("description"),
        "actualResult": row.get("actual_result"),
        "severity": row.get("severity"),
        "priority": row.get("priority"),
        "bugType": row.get("bug_type"),
        "reporter": row.get("reporter"),
        "caseIds": _case_ids(row.get("case_ids_json")),
        "status": status,
        "statusLabel": STATUS_LABELS.get(status, status),
        "bugId": row.get("bug_id"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def list_drafts(conn: sqlite3.Connection, project_id: int | None = None,
                status: str | None = None, limit: int = 50) -> dict[str, Any]:
    where = ["is_deleted = 0"]
    params: list[Any] = []
    if project_id:
        where.append("project_id = ?")
        params.append(int(project_id))
    if status:
        where.append("status = ?")
        params.append(status)
    rows = conn.execute(
        f"SELECT * FROM biz_bug_draft WHERE {' AND '.join(where)} "
        "ORDER BY status = 'pending' DESC, id DESC LIMIT ?",
        (*params, max(1, min(int(limit), 200))),
    ).fetchall()
    pending = conn.execute(
        "SELECT COUNT(*) AS total FROM biz_bug_draft WHERE is_deleted = 0 AND status = 'pending'"
        + (" AND project_id = ?" if project_id else ""),
        ((int(project_id),) if project_id else ()),
    ).fetchone()
    return {
        "list": [summary(dict(row)) for row in rows],
        "pendingCount": int(pending["total"] or 0) if pending else 0,
    }


def _get(conn: sqlite3.Connection, draft_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM biz_bug_draft WHERE id = ? AND is_deleted = 0", (int(draft_id),)
    ).fetchone()
    return dict(row) if row else None


def confirm(conn: sqlite3.Connection, draft_id: int, reporter: str = "") -> dict[str, Any]:
    draft = _get(conn, draft_id)
    if draft is None:
        raise ValueError("待审缺陷不存在")
    if str(draft.get("status")) != STATUS_PENDING:
        raise ValueError("该待审缺陷已处理，请刷新列表")
    source = str(draft.get("source") or "automation")
    prefix = "mcp" if source == "mcp" else "automation"
    repo = BugRepository(conn)
    bug_id = repo.insert({
        "project_id": int(draft["project_id"]),
        "bug_no": repo.next_bug_no(prefix),
        "source": source,
        "source_ref": str(draft.get("source_ref") or "")[:200],
        "title": str(draft.get("title") or "")[:500],
        "severity": str(draft.get("severity") or "P2")[:20],
        "priority": str(draft.get("priority") or "P2")[:20],
        "status": "open",
        "bug_type": str(draft.get("bug_type") or "functional")[:40],
        "description": str(draft.get("description") or "")[:100000],
        "actual_result": str(draft.get("actual_result") or "")[:10000],
        "reporter": (reporter or str(draft.get("reporter") or ""))[:64],
    })
    case_ids = _case_ids(draft.get("case_ids_json"))
    if case_ids:
        repo.link_cases(bug_id, list(dict.fromkeys(case_ids)), "caused_by")
    now = utcnow_iso()
    conn.execute(
        "UPDATE biz_bug_draft SET status = ?, bug_id = ?, updated_at = ? WHERE id = ?",
        (STATUS_CONFIRMED, bug_id, now, int(draft_id)),
    )
    conn.commit()
    bug = repo.get(bug_id) or {}
    return {
        "draft": summary(_get(conn, draft_id) or {}),
        "bugId": bug_id,
        "bugNo": bug.get("bug_no"),
        "title": bug.get("title"),
    }


def ignore(conn: sqlite3.Connection, draft_id: int, note: str = "") -> dict[str, Any]:
    draft = _get(conn, draft_id)
    if draft is None:
        raise ValueError("待审缺陷不存在")
    if str(draft.get("status")) != STATUS_PENDING:
        raise ValueError("该待审缺陷已处理，请刷新列表")
    description = str(draft.get("description") or "")
    if note:
        description = f"{description}\n\n[忽略原因] {note[:500]}"
    conn.execute(
        "UPDATE biz_bug_draft SET status = ?, description = ?, updated_at = ? WHERE id = ?",
        (STATUS_IGNORED, description, utcnow_iso(), int(draft_id)),
    )
    conn.commit()
    return {"draft": summary(_get(conn, draft_id) or {})}


__all__ = ["STATUS_PENDING", "confirm", "ignore", "list_drafts", "summary"]
