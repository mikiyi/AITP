"""全局搜索：一次检索需求、用例、缺陷、知识四类实体。

全部走参数化 SQL，匹配用 instr() 做子串匹配：
- 知识库有 FTS5 索引，但 trigram 分词对短词不友好，这里统一用子串匹配保证「登录」能命中「登录失败」
- SQLite 的 lower() 只处理 ASCII，正好满足「英文不区分大小写、中文不受影响」
- 空格分隔的多个关键词按 AND 处理：每个词都要落在该实体的某个字段里

每类实体分成两次查询：先 COUNT 出真实命中数，再取前 limit 条。前端据此展示
「共 N 条 / 已展示 M 条」，并支持按分组继续展开。

已删除项目下的数据不进搜索：项目列表里点不进去，搜出来只会是死链；
知识条目允许 project_id 为空（全局知识），仍然参与检索。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable, Sequence

from ..common.knowledge_lexicon import expand_synonyms

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

#: 软删除的项目下数据不参与搜索；项目已不在切换器里，点了也进不去。
_LIVE_PROJECT = "project_id IN (SELECT id FROM biz_project WHERE is_deleted = 0)"


def _tokens(keyword: str) -> list[str]:
    """空格拆词；全是空白时退回整串，避免空条件。"""
    parts = [part for part in keyword.split() if part]
    return parts or [keyword.strip()]


def _match_sql(columns: Sequence[str], tokens: Sequence[str]) -> tuple[str, list[Any]]:
    """每个词至少要命中一个字段（词之间 AND、字段之间 OR），大小写不敏感。"""
    conditions: list[str] = []
    params: list[Any] = []
    for token in tokens:
        ors = " OR ".join(
            f"instr(lower(COALESCE({column}, '')), lower(?)) > 0" for column in columns
        )
        conditions.append(f"({ors})")
        params.extend([token] * len(columns))
    return " AND ".join(conditions), params


def _where(
    extra: Sequence[str], columns: Sequence[str], tokens: Sequence[str]
) -> tuple[str, list[Any]]:
    match, params = _match_sql(columns, tokens)
    clauses = ["is_deleted = 0", *extra, match]
    return " AND ".join(clauses), params


def _count(conn: sqlite3.Connection, table: str, where: str, params: list[Any]) -> int:
    row = conn.execute(
        f"SELECT COUNT(*) AS c FROM {table} WHERE {where}", params
    ).fetchone()
    return int(row["c"])


def _order_by(tokens: Sequence[str]) -> tuple[str, list[Any]]:
    """标题命中排前面，组内再按更新时间倒序。"""
    title_match, params = _match_sql(["title"], tokens)
    return f"CASE WHEN {title_match} THEN 0 ELSE 1 END, updated_at DESC", params


def _rows(
    conn: sqlite3.Connection,
    sql: str,
    params: list[Any],
) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, params).fetchall()]


def _snippet(candidates: Iterable[tuple[str, str]], tokens: Sequence[str]) -> str:
    """从最先命中的字段截取上下文；标题/编号已在结果里展示，不重复截取。"""
    for label, text in candidates:
        normalized = " ".join((text or "").split())
        if not normalized:
            continue
        low = normalized.lower()
        hits = [(low.find(token.lower()), token) for token in tokens if token.lower() in low]
        if not hits:
            continue
        index, token = min(hits, key=lambda item: item[0])
        start = max(0, index - _SNIPPET_RADIUS)
        end = min(len(normalized), index + len(token) + _SNIPPET_RADIUS)
        prefix = "…" if start > 0 else ""
        suffix = "…" if end < len(normalized) else ""
        return f"{label}{prefix}{normalized[start:end]}{suffix}"
    return ""


#: 中文复合查询兜底：整串连续匹配失败时，拆成 2~4 字切片再 OR 召回。
#: 不拆的话「登录验证码」会被当成一个连续短语，正文里分开出现的两个词永远搜不到。
_MAX_FALLBACK_TERMS = 24
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _fallback_terms(keyword: str) -> list[str]:
    text = keyword.strip()
    if len(text) < 3 or any(char.isspace() for char in text):
        return []
    # 只对纯中文复合短语切片；英文/编号/混排查询保持原语义，
    # 否则 ZQ 这类 2 字片段会把无关知识也放大进结果。
    if not _CJK_RE.search(text) or any(char.isascii() and char.isalnum() for char in text):
        return []
    terms: list[str] = []
    for size in (2, 3, 4):
        for index in range(len(text) - size + 1):
            piece = text[index:index + size]
            if piece not in terms:
                terms.append(piece)
                if len(terms) >= _MAX_FALLBACK_TERMS:
                    return terms
    return terms


def _search_knowledge_ngrams(
    conn: sqlite3.Connection,
    keyword: str,
    limit: int,
    extra: Sequence[str],
    extra_params: list[Any],
) -> tuple[list[dict[str, Any]], int] | None:
    """知识库的 n-gram 兜底：只在整串查询 0 命中时启用，不清空原有精确匹配语义。"""
    base_terms = _fallback_terms(keyword)
    synonyms = [term for term in expand_synonyms(keyword) if term not in base_terms]
    terms = base_terms + synonyms
    if not terms:
        return None
    columns = ["title", "summary", "content", "tags"]
    ors: list[str] = []
    match_params: list[Any] = []
    for term in terms:
        for column in columns:
            ors.append(f"instr(lower(COALESCE({column}, '')), lower(?)) > 0")
            match_params.append(term)
    where = " AND ".join(["is_deleted = 0", *extra, f"({' OR '.join(ors)})"])
    params = extra_params + match_params
    total = _count(conn, "biz_knowledge_article", where, params)
    if total == 0:
        return None

    # 标题命中权重高，正文次之；同分再按更新时间倒序。
    # 原词权重高于同义词，避免同义扩展把结果带到无关条目。
    score_parts: list[str] = []
    score_params: list[Any] = []
    for term in base_terms:
        score_parts.append(
            "CASE WHEN instr(lower(COALESCE(title, '')), lower(?)) > 0 THEN 8 ELSE 0 END")
        score_params.append(term)
    for term in synonyms:
        score_parts.append(
            "CASE WHEN instr(lower(COALESCE(title, '')), lower(?)) > 0 THEN 4 ELSE 0 END")
        score_params.append(term)
    for term in terms:
        for column in ("summary", "content", "tags"):
            score_parts.append(
                f"CASE WHEN instr(lower(COALESCE({column}, '')), lower(?)) > 0 THEN 1 ELSE 0 END")
            score_params.append(term)
    sql = (
        "SELECT id, project_id, title, category, COALESCE(summary, '') AS summary,"
        " COALESCE(content, '') AS content, COALESCE(tags, '') AS tags, updated_at"
        f" FROM biz_knowledge_article WHERE {where}"
        f" ORDER BY ({' + '.join(score_parts)}) DESC, updated_at DESC LIMIT ?"
    )
    items = [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "no": str(row["category"] or ""),
            "snippet": _snippet(
                (("", row["summary"]), ("", row["content"]), ("标签：", row["tags"])),
                terms,
            ),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params + score_params + [limit])
    ]
    return items, total


def search_requirements(
    conn: sqlite3.Connection, keyword: str, project_id: int | None, limit: int
) -> tuple[list[dict[str, Any]], int]:
    tokens = _tokens(keyword)
    extra: list[str] = [_LIVE_PROJECT]
    extra_params: list[Any] = []
    if project_id is not None:
        extra.append("project_id = ?")
        extra_params.append(project_id)
    where, match_params = _where(extra, ["title", "content", "req_no", "tags"], tokens)
    params = extra_params + match_params
    total = _count(conn, "biz_requirement", where, params)
    order, order_params = _order_by(tokens)
    sql = (
        "SELECT id, project_id, title, req_no AS no, COALESCE(content, '') AS body,"
        " COALESCE(tags, '') AS tags, updated_at"
        f" FROM biz_requirement WHERE {where}"
        f" ORDER BY {order} LIMIT ?"
    )
    items = [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "no": row["no"] or "",
            "snippet": _snippet((("", row["body"]), ("标签：", row["tags"])), tokens),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params + order_params + [limit])
    ]
    return items, total


def search_cases(
    conn: sqlite3.Connection, keyword: str, project_id: int | None, limit: int
) -> tuple[list[dict[str, Any]], int]:
    tokens = _tokens(keyword)
    extra: list[str] = [_LIVE_PROJECT]
    extra_params: list[Any] = []
    if project_id is not None:
        extra.append("project_id = ?")
        extra_params.append(project_id)
    where, match_params = _where(
        extra, ["title", "expected_result", "actual_result", "tags", "case_no"], tokens
    )
    params = extra_params + match_params
    total = _count(conn, "biz_test_case", where, params)
    order, order_params = _order_by(tokens)
    sql = (
        "SELECT id, project_id, title, case_no AS no, COALESCE(expected_result, '') AS expected,"
        " COALESCE(actual_result, '') AS actual, COALESCE(tags, '') AS tags, updated_at"
        f" FROM biz_test_case WHERE {where}"
        f" ORDER BY {order} LIMIT ?"
    )
    items = [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "no": row["no"] or "",
            "snippet": _snippet(
                (("", row["expected"]), ("实际结果：", row["actual"]), ("标签：", row["tags"])),
                tokens,
            ),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params + order_params + [limit])
    ]
    return items, total


def search_bugs(
    conn: sqlite3.Connection, keyword: str, project_id: int | None, limit: int
) -> tuple[list[dict[str, Any]], int]:
    tokens = _tokens(keyword)
    extra: list[str] = [_LIVE_PROJECT]
    extra_params: list[Any] = []
    if project_id is not None:
        extra.append("project_id = ?")
        extra_params.append(project_id)
    where, match_params = _where(
        extra, ["title", "description", "actual_result", "module", "bug_no"], tokens
    )
    params = extra_params + match_params
    total = _count(conn, "biz_bug", where, params)
    order, order_params = _order_by(tokens)
    sql = (
        "SELECT id, project_id, title, bug_no AS no, COALESCE(description, '') AS body,"
        " COALESCE(actual_result, '') AS actual, COALESCE(module, '') AS module, updated_at"
        f" FROM biz_bug WHERE {where}"
        f" ORDER BY {order} LIMIT ?"
    )
    items = [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "no": row["no"] or "",
            "snippet": _snippet(
                (("", row["body"]), ("实际结果：", row["actual"]), ("模块：", row["module"])),
                tokens,
            ),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params + order_params + [limit])
    ]
    return items, total


def search_knowledge(
    conn: sqlite3.Connection, keyword: str, limit: int, project_id: int | None = None
) -> tuple[list[dict[str, Any]], int]:
    """只检索已发布的知识条目；待审池和已撤回条目不进全局搜索。"""
    tokens = _tokens(keyword)
    extra = [
        "review_status = 'published'",
        "(project_id IS NULL OR " + _LIVE_PROJECT + ")",
    ]
    extra_params: list[Any] = []
    if project_id is not None:
        extra.append("project_id = ?")
        extra_params.append(project_id)
    where, match_params = _where(extra, ["title", "summary", "content", "tags"], tokens)
    params = extra_params + match_params
    total = _count(conn, "biz_knowledge_article", where, params)
    if total == 0:
        fallback = _search_knowledge_ngrams(conn, keyword, limit, extra, extra_params)
        if fallback is not None:
            return fallback
    order, order_params = _order_by(tokens)
    sql = (
        "SELECT id, project_id, title, category, COALESCE(summary, '') AS summary,"
        " COALESCE(content, '') AS content, COALESCE(tags, '') AS tags, updated_at"
        f" FROM biz_knowledge_article WHERE {where}"
        f" ORDER BY {order} LIMIT ?"
    )
    items = [
        {
            "id": row["id"],
            "projectId": row["project_id"],
            "title": row["title"],
            "no": str(row["category"] or ""),
            "snippet": _snippet(
                (("", row["summary"]), ("", row["content"]), ("标签：", row["tags"])),
                tokens,
            ),
            "updatedAt": row["updated_at"],
        }
        for row in _rows(conn, sql, params + order_params + [limit])
    ]
    return items, total


def _project_names(conn: sqlite3.Connection, items: Iterable[dict[str, Any]]) -> dict[int, str]:
    """一次查出本页命中涉及的 project 名称，用于前端标注「这是哪个项目的数据」。"""
    ids = sorted({item["projectId"] for item in items if item.get("projectId")})
    if not ids:
        return {}
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT id, name FROM biz_project WHERE id IN ({placeholders}) AND is_deleted = 0",
        ids,
    ).fetchall()
    return {row["id"]: row["name"] for row in rows}


def search_all(
    conn: sqlite3.Connection,
    keyword: str,
    *,
    types: tuple[str, ...] = SEARCH_TYPES,
    project_id: int | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    """跨实体检索：total 是实际返回条数，matched 是真实命中总数。"""
    text = keyword.strip()
    if not text:
        return {"keyword": keyword, "groups": [], "total": 0, "matched": 0}

    picked = [item for item in types if item in SEARCH_TYPES] or list(SEARCH_TYPES)
    per_type = max(1, min(limit, 50))

    groups: list[dict[str, Any]] = []
    shown = 0
    matched = 0
    for kind in picked:
        if kind == TYPE_REQUIREMENT:
            items, type_total = search_requirements(conn, text, project_id, per_type)
        elif kind == TYPE_CASE:
            items, type_total = search_cases(conn, text, project_id, per_type)
        elif kind == TYPE_BUG:
            items, type_total = search_bugs(conn, text, project_id, per_type)
        else:
            items, type_total = search_knowledge(conn, text, per_type, project_id)
        if not items:
            continue
        for item in items:
            item["type"] = kind
            item["typeLabel"] = TYPE_LABELS[kind]
            item["route"] = TYPE_ROUTES[kind]
        groups.append({
            "type": kind,
            "typeLabel": TYPE_LABELS[kind],
            "total": type_total,
            "list": items,
        })
        shown += len(items)
        matched += type_total

    names = _project_names(conn, (item for group in groups for item in group["list"]))
    for group in groups:
        for item in group["list"]:
            item["projectName"] = names.get(item.get("projectId"), "")

    return {"keyword": text, "groups": groups, "total": shown, "matched": matched}
