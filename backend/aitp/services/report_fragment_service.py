"""报告片段跨项目复用（GAP-42）。

来源项目维护片段本体；引用项目只登记使用关系，不复制内容。
预览/导出实时读取本体，所以源片段一改，所有引用报告同步更新。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.enums import REPORT_SECTION
from ..errors import AppError, ErrorCode
from ..repositories.report_fragment_repo import (
    ReportFragmentRepository,
    ReportFragmentUseRepository,
    fragment_public,
)
from ..repositories.report_repo import ReportRepository


def _project_names(conn: sqlite3.Connection, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, name FROM biz_project WHERE id IN ({placeholders})",  # noqa: S608
        list(ids),
    ).fetchall()
    return {int(row["id"]): str(row["name"] or "") for row in rows}


def _report_titles(conn: sqlite3.Connection, ids: set[int]) -> dict[int, str]:
    if not ids:
        return {}
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, title FROM biz_report WHERE id IN ({placeholders})",  # noqa: S608
        list(ids),
    ).fetchall()
    return {int(row["id"]): str(row["title"] or "") for row in rows}


def _decorate(conn: sqlite3.Connection, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    project_names = _project_names(conn, {int(row["project_id"]) for row in rows})
    report_titles = _report_titles(
        conn, {int(row["source_report_id"]) for row in rows if row.get("source_report_id")}
    )
    use_repo = ReportFragmentUseRepository(conn)
    return [
        fragment_public(
            row,
            source_project_name=project_names.get(int(row["project_id"]), ""),
            source_report_title=report_titles.get(int(row["source_report_id"]), "")
            if row.get("source_report_id") else "",
            use_count=use_repo.count_by_fragment(int(row["id"])),
        )
        for row in rows
    ]


def get_fragment(conn: sqlite3.Connection, fragment_id: int) -> dict[str, Any]:
    row = ReportFragmentRepository(conn).get(fragment_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告片段不存在")
    return row


def list_available(conn: sqlite3.Connection, project_id: int,
                   keyword: str = "") -> list[dict[str, Any]]:
    """当前项目可插入的片段：本项目片段 + 其他项目提升为全局的片段。"""
    rows = conn.execute(
        """
        SELECT * FROM biz_report_fragment
        WHERE is_deleted = 0
          AND (project_id = ? OR is_global = 1)
          AND (? = '' OR instr(title, ?) > 0 OR instr(content, ?) > 0)
        ORDER BY is_global ASC, updated_at DESC, id DESC
        """,
        (project_id, keyword, keyword, keyword),
    ).fetchall()
    return _decorate(conn, [dict(row) for row in rows])


def list_mine(conn: sqlite3.Connection, project_id: int,
              keyword: str = "") -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_report_fragment WHERE is_deleted = 0 AND project_id = ?"
        " AND (? = '' OR instr(title, ?) > 0 OR instr(content, ?) > 0)"
        " ORDER BY updated_at DESC, id DESC",
        (project_id, keyword, keyword, keyword),
    ).fetchall()
    return _decorate(conn, [dict(row) for row in rows])


def list_global(conn: sqlite3.Connection, keyword: str = "") -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_report_fragment WHERE is_deleted = 0 AND is_global = 1"
        " AND (? = '' OR instr(title, ?) > 0 OR instr(content, ?) > 0)"
        " ORDER BY updated_at DESC, id DESC",
        (keyword, keyword, keyword),
    ).fetchall()
    return _decorate(conn, [dict(row) for row in rows])


def create_fragment(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    content: str,
    section: str = "overview",
    source_report_id: int | None = None,
    source_note: str = "",
    created_by: str = "",
    is_global: bool = False,
) -> dict[str, Any]:
    if section not in REPORT_SECTION:
        raise AppError(ErrorCode.PARAM_ERROR, f"报告章节非法：{section}")
    if source_report_id:
        report = ReportRepository(conn).get(source_report_id)
        if not report:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "来源报告不存在")
        if int(report.get("project_id") or 0) != int(project_id):
            raise AppError(ErrorCode.PARAM_ERROR, "来源报告不属于该项目")
    fragment_id = ReportFragmentRepository(conn).insert({
        "project_id": project_id,
        "title": title,
        "section": section,
        "content": content,
        "source_report_id": source_report_id,
        "source_note": source_note,
        "is_global": 1 if is_global else 0,
        "created_by": created_by,
    })
    return _decorate(conn, [get_fragment(conn, fragment_id)])[0]


def update_fragment(
    conn: sqlite3.Connection,
    fragment_id: int,
    *,
    title: str | None = None,
    content: str | None = None,
    section: str | None = None,
    source_note: str | None = None,
) -> dict[str, Any]:
    row = get_fragment(conn, fragment_id)
    payload: dict[str, Any] = {}
    if title is not None:
        payload["title"] = title
    if content is not None:
        payload["content"] = content
    if section is not None:
        if section not in REPORT_SECTION:
            raise AppError(ErrorCode.PARAM_ERROR, f"报告章节非法：{section}")
        payload["section"] = section
    if source_note is not None:
        payload["source_note"] = source_note
    if not payload:
        return _decorate(conn, [row])[0]
    ReportFragmentRepository(conn).update(fragment_id, payload)
    return _decorate(conn, [get_fragment(conn, fragment_id)])[0]


def promote_fragment(conn: sqlite3.Connection, fragment_id: int) -> dict[str, Any]:
    row = get_fragment(conn, fragment_id)
    if row.get("is_global"):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该片段已是全局片段")
    ReportFragmentRepository(conn).update(fragment_id, {"is_global": 1})
    return _decorate(conn, [get_fragment(conn, fragment_id)])[0]


def delete_fragment(conn: sqlite3.Connection, fragment_id: int) -> None:
    get_fragment(conn, fragment_id)
    ReportFragmentUseRepository(conn).ensure_not_used(fragment_id, action="删除")
    ReportFragmentRepository(conn).soft_delete(fragment_id)


def used_by_report(conn: sqlite3.Connection, report_id: int) -> list[dict[str, Any]]:
    use_repo = ReportFragmentUseRepository(conn)
    ids = use_repo.fragment_ids(report_id)
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT * FROM biz_report_fragment WHERE is_deleted = 0 AND id IN ({placeholders})",  # noqa: S608
        ids,
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    ordered = [by_id[i] for i in ids if i in by_id]
    return _decorate(conn, ordered)


def insert_into_report(conn: sqlite3.Connection, report_id: int, fragment_id: int,
                       inserted_by: str = "") -> dict[str, Any]:
    report = ReportRepository(conn).get(report_id)
    if not report:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    fragment = get_fragment(conn, fragment_id)
    if not fragment.get("is_global") and int(fragment["project_id"]) != int(report["project_id"] or 0):
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            "该片段属于其他项目且未提升为全局，请先在来源项目提升为全局片段",
        )
    ReportFragmentUseRepository(conn).add_use(fragment_id, report_id, inserted_by)
    return {
        "reportId": report_id,
        "fragments": used_by_report(conn, report_id),
    }


def remove_from_report(conn: sqlite3.Connection, report_id: int, fragment_id: int) -> dict[str, Any]:
    if not ReportRepository(conn).get(report_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "报告不存在")
    ReportFragmentUseRepository(conn).remove_use(fragment_id, report_id)
    return {
        "reportId": report_id,
        "fragments": used_by_report(conn, report_id),
    }


def cleanup_report(conn: sqlite3.Connection, report_id: int) -> None:
    """报告删除时清掉使用关系；片段本体保留。"""
    ReportFragmentUseRepository(conn).remove_by_report(report_id)
