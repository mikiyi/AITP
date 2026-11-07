"""中文全文检索：FTS5 trigram + instr 兜底的混合检索。

背景：SQLite FTS5 的 unicode61 分词器对中文无效，trigram 分词器可匹配中文子串，
但 trigram 只处理长度 >= 3 的词。因此短词（1-2 字）必须走 instr 兜底。

混合规则：
  - 词长 >= 3  → 进 FTS 分支（MATCH，带引号防注入）
  - 词长 <  3  → 进 instr 分支（LIKE 语义，无通配符注入风险）
  - 两路结果 UNION 合并，逐行标注 hit_mode，整体标注 matchMode

兜底（fallback_on_empty，默认关闭）：trigram 短语查询要求整段原文连续出现，
自然语言问句（如「支付回调幂等排查结论是什么样子？」）会被切成一个长 token 当成
短语 AND 检索，除非正文逐字包含该句否则必然 0 命中。问答链路（retrieve_knowledge_context）
显式开启兜底：剥掉疑问词后再检索、长 token 滑窗切成 3~5 字 n-gram 用 OR 检索、
仍为空则退回 instr 关键词 OR。管理页 / 全局搜索不传该开关，语义完全不变。
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from ..common.pagination import MAX_PAGE_SIZE

_TOKEN_RE = re.compile(r"[^\s,，。；;、:：!！?？()（）\[\]【】\"'`]+")

FTS_MIN_LEN = 3

#: trigram 短语的可信上限：超过该长度的 token 大概率是整句，需要再切 n-gram
PHRASE_MAX_LEN = 6

#: 兜底时单次检索的候选抓取上限（真正分页在归并后做）
FALLBACK_FETCH_CAP = 200

#: 疑问句式尾巴与虚词：命中即替换为分隔符，避免把疑问词并进检索 token
_STOPWORD_RE = re.compile(
    r"(是什么样子的|是什么样的|是什么样|是什么|有什么|有哪些|有没有|有什么样|怎么样|怎么办|怎么样的|怎么了|"
    r"为什么|为啥|如何|可否|能否|可不可以|能不能|是否|哪些|哪个|什么样|多少|几点|怎样|怎么|啥|"
    r"请问|请说说|告诉我|解释一下|解释|介绍一下|介绍|描述一下|描述|查一下|查下|查询一下|查询|"
    r"帮我|请|一下|的话|呢|吗|吧|啊|呀|哦|的|了|吗)"
)


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


def strip_question_words(text: str) -> str:
    """把问句里的疑问词与虚词替换成空格，返回剩余的可检索片段。"""
    if not text:
        return ""
    cleaned = _STOPWORD_RE.sub(" ", text)
    return cleaned.strip()


def split_to_ngrams(token: str) -> list[str]:
    """把过长的 token 滑窗切成 3~5 字的 n-gram；短 token 原样返回。

    n-gram 让「支付回调幂等排查结论」这类长 token 能匹配到正文里任意连续的 3~5 字，
    配合 OR 检索与 bm25 归并排序找回自然语言问句。
    """
    grams: list[str] = []
    seen: set[str] = set()
    for size in (3, 4, 5):
        if len(token) < size:
            continue
        for i in range(len(token) - size + 1):
            piece = token[i:i + size]
            if piece not in seen:
                seen.add(piece)
                grams.append(piece)
    return grams


def _split_tokens(tokens: list[str]) -> tuple[list[str], list[str]]:
    long_tokens = [t for t in tokens if len(t) >= FTS_MIN_LEN]
    short_tokens = [t for t in tokens if len(t) < FTS_MIN_LEN]
    return long_tokens, short_tokens


def _fts_expression(long_tokens: list[str], *, or_mode: bool = False) -> str:
    """构造 FTS MATCH 表达式。每个词加引号，避免被当作 FTS 语法。

    默认 AND（管理页 / 全局搜索语义）；兜底时 or_mode=True 用 OR 合并多路切词。
    """
    parts = ['"' + token.replace('"', '""') + '"' for token in long_tokens]
    return (" OR " if or_mode else " AND ").join(parts)


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
    fallback_on_empty: bool = False,
) -> dict[str, Any]:
    """执行混合检索，返回 {list, total, pageNum, pageSize, pages, matchMode}。

    fallback_on_empty 默认关闭：管理页 / 全局搜索 / check_fts 走的都是这条原始路径，
    行为逐字节不变。只有问答链路显式传 True 时，命中 0 才启用兜底切词检索。
    """
    tokens = tokenize(keyword)
    long_tokens, short_tokens = _split_tokens(tokens)

    limit = max(1, min(int(page_size or 20), MAX_PAGE_SIZE))
    page = max(1, int(page_num or 1))
    offset = (page - 1) * limit

    where_extra = f" AND ({extra_where})" if extra_where else ""
    params_extra = tuple(extra_params)

    result = _run_hybrid(
        conn,
        fts_table=fts_table,
        content_table=content_table,
        columns=columns,
        long_tokens=long_tokens,
        short_tokens=short_tokens,
        or_mode=False,
        where_extra=where_extra,
        params_extra=params_extra,
        limit=limit,
        offset=offset,
        tokens=tokens,
        short_tokens_for_mode=short_tokens,
    )
    if result["total"] > 0 or not fallback_on_empty:
        return result

    fallback = _fallback_hybrid(
        conn,
        fts_table=fts_table,
        content_table=content_table,
        columns=columns,
        keyword=keyword,
        where_extra=where_extra,
        params_extra=params_extra,
        limit=limit,
        offset=offset,
        tokens=tokens,
    )
    return fallback if fallback is not None else result


def _fallback_hybrid(
    conn: sqlite3.Connection,
    *,
    fts_table: str,
    content_table: str,
    columns: list[str],
    keyword: str,
    where_extra: str,
    params_extra: tuple[Any, ...],
    limit: int,
    offset: int,
    tokens: list[str],
) -> dict[str, Any] | None:
    """问句兜底检索：多路切词 UNION 后按分数归并排序，仍走外层 where_extra。

    ① 剥掉疑问词后的 token 做一次原有 AND 检索（精确，优先）；
    ② 剥掉疑问词后的长 token 连同 n-gram 用 OR 检索（召回）；①②按 id 归并取最高分。
    ③ 仍为空则把切词后的关键词对正文列做 instr OR 兜底。
    全部命中 0 时返回 None，让调用方保留原始空结果。
    """
    stripped = tokenize(strip_question_words(keyword)) or tokens
    stripped_long, stripped_short = _split_tokens(stripped)

    # ① 原有 AND 检索
    variants: list[tuple[list[str], list[str], bool]] = [
        (stripped_long, stripped_short, False),
    ]
    # ② 长 token 滑窗切 n-gram，连同原 token 一起 OR
    or_long: list[str] = []
    seen: set[str] = set()
    for token in stripped_long:
        pieces = split_to_ngrams(token) if len(token) > PHRASE_MAX_LEN else [token]
        for piece in pieces + [token]:
            if piece not in seen:
                seen.add(piece)
                or_long.append(piece)
    if or_long or stripped_short:
        variants.append((or_long, stripped_short, True))

    merged: dict[int, dict[str, Any]] = {}
    for long_part, short_part, or_mode in variants:
        if not long_part and not short_part:
            continue
        page_result = _run_hybrid(
            conn,
            fts_table=fts_table,
            content_table=content_table,
            columns=columns,
            long_tokens=long_part,
            short_tokens=short_part,
            or_mode=or_mode,
            where_extra=where_extra,
            params_extra=params_extra,
            limit=FALLBACK_FETCH_CAP,
            offset=0,
            tokens=tokens,
            short_tokens_for_mode=short_part,
        )
        for row in page_result["list"]:
            row_id = int(row["id"])
            score = float(row.get("score") or 0.0)
            current = merged.get(row_id)
            if current is None or score > float(current.get("score") or 0.0):
                merged[row_id] = row

    # ③ instr 关键词 OR 兜底
    if not merged:
        instr_terms = stripped or tokens
        if instr_terms:
            page_result = _run_hybrid(
                conn,
                fts_table=fts_table,
                content_table=content_table,
                columns=columns,
                long_tokens=[],
                short_tokens=instr_terms,
                or_mode=True,
                where_extra=where_extra,
                params_extra=params_extra,
                limit=FALLBACK_FETCH_CAP,
                offset=0,
                tokens=tokens,
                short_tokens_for_mode=instr_terms,
            )
            for row in page_result["list"]:
                merged[int(row["id"])] = row

    if not merged:
        return None

    ordered = sorted(
        merged.values(),
        key=lambda r: (-float(r.get("score") or 0.0), str(r.get("updated_at") or "")),
    )
    total = len(ordered)
    return {
        "list": ordered[offset:offset + limit],
        "total": total,
        "pageNum": offset // max(limit, 1) + 1,
        "pageSize": limit,
        "pages": (total + limit - 1) // limit if total else 0,
        "matchMode": "fallback",
        "tokens": tokens,
    }


def _run_hybrid(
    conn: sqlite3.Connection,
    *,
    fts_table: str,
    content_table: str,
    columns: list[str],
    long_tokens: list[str],
    short_tokens: list[str],
    or_mode: bool,
    where_extra: str,
    params_extra: tuple[Any, ...],
    limit: int,
    offset: int,
    tokens: list[str],
    short_tokens_for_mode: list[str],
) -> dict[str, Any]:
    """执行一次混合检索（不含兜底分支），供主路径与兜底路径复用。"""
    fts_expr = _fts_expression(long_tokens, or_mode=or_mode) if long_tokens else None
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

    joiner = " OR " if or_mode else " AND "
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
          AND ({joiner.join(like_conditions)})
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
                f"WHERE a.is_deleted = 0 AND ({joiner.join(like_conditions)}) {where_extra}",
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
        match_mode = "like" if short_tokens_for_mode else "fts"

    return {
        "list": [dict(r) for r in rows],
        "total": total,
        "pageNum": max(1, offset // max(limit, 1) + 1),
        "pageSize": limit,
        "pages": (total + limit - 1) // limit if total else 0,
        "matchMode": match_mode,
        "tokens": tokens,
    }
