"""中文全文检索：FTS5 trigram + instr 兜底的混合检索。

背景：SQLite FTS5 的 unicode61 分词器对中文无效，trigram 分词器可匹配中文子串，
但 trigram 只处理长度 >= 3 的词。因此短词（1-2 字）必须走 instr 兜底。

混合规则：
  - 词长 >= 3  → 进 FTS 分支（MATCH，带引号防注入）
  - 词长 <  3  → 进 instr 分支（LIKE 语义，无通配符注入风险）
  - 两路结果 UNION 合并，逐行标注 hit_mode，整体标注 match_mode
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..common.pagination import MAX_PAGE_SIZE

_TOKEN_RE = re.compile(r"[^\s,，。；;、:：!！?？()（）\[\]【】\"'`]+")

FTS_MIN_LEN = 3


def tokenize(keyword: str) -> list[str]:
    """把关键词切成检索词。"""
    if not keyword:
        return []
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall(keyword):
        token = raw.strip()
        if token:
            tokens.append(token)
    return tokens


def _split_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    long_tokens = [t for t in tokens if len(t) >= FTS_MIN_LEN]
    short_tokens = [t for t in tokens if len(t) < FTS_MIN_LEN]
    return long_tokens, short_tokens


def _fts_expression(long_tokens: list[str]) -> str:
    """构造 FTS MATCH 表达式。每个词加引号，避免被当作 FTS 语法。"""
    parts = ['"' + token.replace('"', '""') + '"' for token in long_tokens]
    return " AND ".join(parts)


def hybrid_search(
    conn: sqlite3.Connection,
    *,
    fts_table: str,
    content_table: str,
    keyword: str,
    columns: list[str],
    extra_where: str = "",
    extra_params: tuple[Any, ...] = (),
    page_num: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    """执行混合检索，返回 {list, total, pageNum, pageSize, pages, matchMode}。"""
    tokens = tokenize(keyword)
    long_tokens, short_tokens = _split_tokens(tokens)

    limit = max(1, min(int(page_size or 20), MAX_PAGE_SIZE))
    offset = (max(1, int(page_num or 1)) - 1) * limit

    where_extra = f" AND ({extra_where})" if extra_where else ""
    params_extra = tuple(extra_params)

    fts_expr = _fts_expression(long_tokens) if long_tokens else None
    fts_params: list[Any] = []
    like_params: list[Any] = []

    if fts_expr:
        fts_params.append(fts_expr)
    for token in short_tokens:
        like_params.extend([token] * len(columns))

    # 两个 CTE 始终存在：结果里的 CASE 会引用它们判断命中来源
    # MATERIALIZED 必须显式加上：FTS 表的外部内容表就是 content_table，
    # 一旦外层 JOIN 到它，SQLite 会让 CTE 合并到外层查询，bm25() 随即报
    # "unable to use function bm25 in the requested context"。
    if fts_expr:
        fts_cte = f"""
    fts_hits AS MATERIALIZED (
        SELECT f.rowid AS id, -bm25({fts_table}, 8.0, 1.0, 4.0) AS score
        FROM {fts_table} f
        WHERE {fts_table} MATCH ?
    )"""
    else:
        fts_cte = """
    fts_hits AS (SELECT NULL AS id, 0.0 AS score WHERE 0)"""

    like_conditions = []
    for token in short_tokens:
        parts = " OR ".join(f"instr(a.{col}, ?) > 0" for col in columns)
        like_conditions.append(f"({parts})")

    if like_conditions:
        # where_extra 只在外层 WHERE 生效一次，避免同一组占位符被绑定两次
        like_cte = f"""
    like_hits AS (
        SELECT a.id AS id, 1.0 AS score
        FROM {content_table} a
        WHERE a.is_deleted = 0
          AND ({' AND '.join(like_conditions)})
    )"""
    else:
        like_cte = """
    like_hits AS (SELECT NULL AS id, 0.0 AS score WHERE 0)"""

    union_parts = []
    if fts_expr:
        union_parts.append("SELECT id, MAX(score) AS score FROM fts_hits GROUP BY id")
    if like_conditions:
        union_parts.append("SELECT id, MAX(score) AS score FROM like_hits GROUP BY id")
    union_sql = "\n        UNION ALL\n        ".join(union_parts) if union_parts else (
        "SELECT NULL AS id, 0.0 AS score WHERE 0"
    )

    cte_sql = f"WITH {fts_cte}, {like_cte}"

    # 同一个 id 可能同时命中 FTS 与 instr 兜底，这里按 id 归并，避免同一篇文章重复出现
    merged_sql = f"""
    merged AS (
        SELECT id, MAX(score) AS score FROM (
            {union_sql}
        ) GROUP BY id
    )
    """

    sql = f"""
    {cte_sql},
    {merged_sql}
    SELECT a.id AS id, a.*, m.score AS score,
           CASE
             WHEN EXISTS(SELECT 1 FROM fts_hits x WHERE x.id = a.id)
              AND EXISTS(SELECT 1 FROM like_hits y WHERE y.id = a.id) THEN 'hybrid'
             WHEN EXISTS(SELECT 1 FROM fts_hits x WHERE x.id = a.id) THEN 'fts'
             ELSE 'like'
           END AS hit_mode
    FROM merged m
    JOIN {content_table} a ON a.id = m.id
    WHERE a.is_deleted = 0
      {where_extra}
    ORDER BY m.score DESC, a.updated_at DESC
    LIMIT ? OFFSET ?
    """

    params = list(fts_params) + list(like_params) + list(params_extra) + [limit, offset]
    rows = conn.execute(sql, params).fetchall()

    # 总数：去掉 LIMIT/OFFSET 重跑一次
    count_sql = f"""
    {cte_sql},
    {merged_sql}
    SELECT COUNT(*) FROM merged m
    JOIN {content_table} a ON a.id = m.id
    WHERE a.is_deleted = 0
      {where_extra}
    """
    count_params = list(fts_params) + list(like_params) + list(params_extra)
    total = int(conn.execute(count_sql, count_params).fetchone()[0])

    fts_hit_ids = set()
    like_hit_ids = set()
    if fts_expr:
        fts_hit_ids = {
            int(r["id"])
            for r in conn.execute(
                f"SELECT rowid AS id FROM {fts_table} WHERE {fts_table} MATCH ?", [fts_expr]
            ).fetchall()
        }
    if like_conditions:
        like_hit_ids = {
            int(r["id"])
            for r in conn.execute(
                f"SELECT a.id AS id FROM {content_table} a "
                f"WHERE a.is_deleted = 0 AND ({' AND '.join(like_conditions)}) {where_extra}",
                list(like_params) + list(params_extra),
            ).fetchall()
        }

    if fts_hit_ids and like_hit_ids:
        match_mode = "hybrid"
    elif fts_hit_ids:
        match_mode = "fts"
    elif like_hit_ids:
        match_mode = "like"
    else:
        match_mode = "like" if short_tokens else "fts"

    return {
        "list": [dict(r) for r in rows],
        "total": total,
        "pageNum": max(1, int(page_num or 1)),
        "pageSize": limit,
        "pages": (total + limit - 1) // limit if total else 0,
        "matchMode": match_mode,
        "tokens": tokens,
    }
