"""全局搜索：一次检索需求、用例、缺陷、知识四类实体。

全部走参数化 SQL，命中条件用 instr() 做子串匹配：
- 知识库有 FTS5 索引，但 trigram 分词对短词不友好，这里统一用子串匹配保证「登录」能命中「登录失败」
- 每类实体各取若干条，按标题命中优先、更新时间倒序
"""

from __future__ import annotations

import sqlite3
from typing import Any

#: 可检索的实体类型
TYPE_REQUIREMENT = "requirement"
TYPE_CASE = "case"
TYPE_BUG = "bug"
TYPE_KNOWLEDGE = "knowledge"

SEARCH_TYPES: tuple[str, ...] = (TYPE_REQUIREMENT, TYPE_CASE, TYPE_BUG, TYPE_KNOWLEDGE)

TYPE_LABELS: dict[str, str] = {
    TYPE_REQUIREMENT: "需求",
    TYPE_CASE: "测试用例",
    TYPE_BUG: "缺陷",
    TYPE_KNOWLEDGE: "知识",
}

#: 命中后前端跳转到哪个页面
TYPE_ROUTES: dict[str, str] = {
    TYPE_REQUIREMENT: "/requirements",
    TYPE_CASE: "/test-cases",
    TYPE_BUG: "/bugs",
    TYPE_KNOWLEDGE: "/knowledge",
}

_SNIPPET_RADIUS = 40
_SNIPPET_MAX = 120


def _snippet(body: str, keyword: str) -> str:
    """截取命中位置前后的一小段文字，便于一眼看清命中在哪。"""
    text = " ".join((body or "").split())
    if not text:
        return ""
    index = text.lower().find(keyword.lower())
    if index < 0:
        return text[:_SNIPPET_MAX]
    start = max(0, index - _SNIPPET_RADIUS)
    end = min(len(text), index + len(keyword) + _SNIPPET_RADIUS)
    prefix = "…" if start > 0 else ""
    suffix = "…" if end < len(text) else ""
    return f"{prefix}{text[start:end]}{suffix}"


def _rows(
    conn: sqlite3.Connection,
    sql: str,
    params: list[Any],
) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def search_requirements(
    conn: sqlite3.Connection, keyword: str, project_id: int | None, limit: int
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, title, req_no AS no, COALESCE(content, '') AS body, updated_at"
        " FROM biz_requirement WHERE is_deleted = 0"
    )
    params: list[Any] = []
    if project_id is not None:
        sql += " AND project_id = ?"
        params.append(project_id)
    sql += (
        " AND (instr(COALESCE(title, ''), ?) > 0 OR instr(COALESCE(content, ''), ?) > 0"
        " OR instr(COALESCE(req_no, ''), ?) > 0)"
        " ORDER BY updated_at DESC LIMIT ?"
    )
    params.extend([keyword, keyword, keyword, limit])
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "no": row["no"] or "",
            "snippet": _snippet(row["body"], keyword),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params)
    ]


def search_cases(
    conn: sqlite3.Connection, keyword: str, project_id: int | None, limit: int
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, title, case_no AS no, COALESCE(expected_result, '') AS body, updated_at"
        " FROM biz_test_case WHERE is_deleted = 0"
    )
    params: list[Any] = []
    if project_id is not None:
        sql += " AND project_id = ?"
        params.append(project_id)
    sql += (
        " AND (instr(COALESCE(title, ''), ?) > 0 OR instr(COALESCE(expected_result, ''), ?) > 0"
        " OR instr(COALESCE(actual_result, ''), ?) > 0 OR instr(COALESCE(tags, ''), ?) > 0"
        " OR instr(COALESCE(case_no, ''), ?) > 0)"
        " ORDER BY updated_at DESC LIMIT ?"
    )
    params.extend([keyword] * 5 + [limit])
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "no": row["no"] or "",
            "snippet": _snippet(row["body"], keyword),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params)
    ]


def search_bugs(
    conn: sqlite3.Connection, keyword: str, project_id: int | None, limit: int
) -> list[dict[str, Any]]:
    sql = (
        "SELECT id, title, bug_no AS no, COALESCE(description, '') AS body, updated_at"
        " FROM biz_bug WHERE is_deleted = 0"
    )
    params: list[Any] = []
    if project_id is not None:
        sql += " AND project_id = ?"
        params.append(project_id)
    sql += (
        " AND (instr(COALESCE(title, ''), ?) > 0 OR instr(COALESCE(description, ''), ?) > 0"
        " OR instr(COALESCE(actual_result, ''), ?) > 0 OR instr(COALESCE(module, ''), ?) > 0"
        " OR instr(COALESCE(bug_no, ''), ?) > 0)"
        " ORDER BY updated_at DESC LIMIT ?"
    )
    params.extend([keyword] * 5 + [limit])
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "no": row["no"] or "",
            "snippet": _snippet(row["body"], keyword),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params)
    ]


def search_knowledge(
    conn: sqlite3.Connection, keyword: str, limit: int
) -> list[dict[str, Any]]:
    """只检索已发布的知识条目；待审池不进全局搜索。"""
    sql = (
        "SELECT id, title, category, COALESCE(summary, '') AS body, updated_at"
        " FROM biz_knowledge_article WHERE is_deleted = 0 AND review_status = 'published'"
        " AND (instr(COALESCE(title, ''), ?) > 0 OR instr(COALESCE(summary, ''), ?) > 0"
        " OR instr(COALESCE(content, ''), ?) > 0 OR instr(COALESCE(tags, ''), ?) > 0)"
        " ORDER BY updated_at DESC LIMIT ?"
    )
    params = [keyword, keyword, keyword, keyword, limit]
    return [
        {
            "id": row["id"],
            "title": row["title"],
            "no": str(row["category"] or ""),
            "snippet": _snippet(row["body"], keyword),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params)
    ]


def search_all(
    conn: sqlite3.Connection,
    keyword: str,
    *,
    types: tuple[str, ...] = SEARCH_TYPES,
    project_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """跨实体检索，返回按类型分组的命中列表。"""
    text = keyword.strip()
    if not text:
        return {"keyword": keyword, "groups": [], "total": 0}

    picked = [item for item in types if item in SEARCH_TYPES] or list(SEARCH_TYPES)
    per_type = max(1, min(limit, 50))

    groups: list[dict[str, Any]] = []
    total = 0
    for kind in picked:
        if kind == TYPE_REQUIREMENT:
            items = search_requirements(conn, text, project_id, per_type)
        elif kind == TYPE_CASE:
            items = search_cases(conn, text, project_id, per_type)
        elif kind == TYPE_BUG:
            items = search_bugs(conn, text, project_id, per_type)
        else:
            items = search_knowledge(conn, text, per_type)
        if not items:
            continue
        for item in items:
            item["type"] = kind
            item["typeLabel"] = TYPE_LABELS[kind]
            item["route"] = TYPE_ROUTES[kind]
        groups.append({"type": kind, "typeLabel": TYPE_LABELS[kind], "list": items})
        total += len(items)

    return {"keyword": text, "groups": groups, "total": total}
