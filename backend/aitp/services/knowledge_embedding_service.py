"""知识库向量索引与混合召回（四期专项 B）。

职责三件：
  1. **分块**：按 Markdown 标题层级 + 约 800 字窗口切块，块文本带标题路径，便于命中后直接喂提示词；
  2. **索引**：条目审核通过 / 版本更新时增量重嵌（内容哈希不变则跳过），支持整库重建；
  3. **检索**：向量召回与既有字面召回按 RRF（倒数排名融合）合并，缺模型时自动回落纯关键词。

口径：
  - 不引入向量数据库：SQLite 存 BLOB，内存余弦召回；
  - 跨项目隔离：条目 project_id 为空（全局知识）或等于请求项目才参与；
  - 不阻塞：任何异常都只让向量路为空，关键词路照常出结果。
"""

from __future__ import annotations

import hashlib
import logging
import re
import sqlite3
from typing import Any

from ..ai import embeddings
from ..common.time_utils import utcnow_iso
from ..logging_setup import log_event

logger = logging.getLogger(__name__)

#: 单块目标字数（约 800 字，与知识落盘格式对齐）
CHUNK_CHARS = 800
#: 单条目最多切多少块，防止超长文档把索引撑爆
MAX_CHUNKS_PER_ARTICLE = 40
#: RRF 常数（经验值 60，业界通行）
RRF_K = 60

STATE_NONE = "none"
STATE_PARTIAL = "partial"
STATE_READY = "ready"
STATE_STALE = "stale"

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$", re.MULTILINE)


def _hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def chunk_article(title: str, content: str) -> list[dict[str, str]]:
    """按 Markdown 标题切节，节内再按 ~800 字滑窗；每块带标题路径。"""
    text = str(content or "")
    base_title = str(title or "").strip()
    chunks: list[dict[str, str]] = []
    sections: list[tuple[str, str]] = []
    matches = list(_HEADING_RE.finditer(text))
    if not matches:
        sections.append((base_title, text))
    else:
        if matches[0].start() > 0:
            sections.append((base_title, text[:matches[0].start()]))
        stack: list[tuple[int, str]] = []
        for index, match in enumerate(matches):
            level = len(match.group(1))
            heading = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, heading))
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            body = text[match.end():end]
            path = " > ".join(item[1] for item in stack)
            sections.append((path or base_title, body))

    for heading_path, body in sections:
        clean = " ".join(str(body or "").split())
        if not clean:
            continue
        start = 0
        while start < len(clean) and len(chunks) < MAX_CHUNKS_PER_ARTICLE:
            piece = clean[start:start + CHUNK_CHARS]
            chunks.append({
                "headingPath": heading_path,
                "title": base_title,
                "text": f"{heading_path}\n{piece}" if heading_path else piece,
                "contentHash": _hash(f"{heading_path}\n{piece}"),
            })
            start += CHUNK_CHARS
    return chunks


def _article(conn: sqlite3.Connection, article_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, project_id, title, content, review_status, embedding_state, is_deleted"
        " FROM biz_knowledge_article WHERE id = ?",
        (int(article_id),),
    ).fetchone()
    return dict(row) if row is not None else None


def _existing(conn: sqlite3.Connection, article_id: int) -> dict[int, dict[str, Any]]:
    rows = conn.execute(
        "SELECT chunk_index, heading_path, text, content_hash, model, dim, vector"
        " FROM biz_kb_embedding WHERE article_id = ?",
        (int(article_id),),
    ).fetchall()
    return {int(row["chunk_index"]): dict(row) for row in rows}


def _set_state(conn: sqlite3.Connection, article_id: int, state: str) -> None:
    conn.execute(
        "UPDATE biz_knowledge_article SET embedding_state = ? WHERE id = ?",
        (state, int(article_id)),
    )


def ensure_index(conn: sqlite3.Connection, article_id: int, *,
                 settings_map: dict[str, Any] | None = None) -> dict[str, Any]:
    """增量重建单条目的向量：只重嵌内容哈希变化的块。

    返回 {state, chunks, embedded, skipped, reason}；无模型时 state=none 且不报错。
    """
    settings_map = settings_map if settings_map is not None else embeddings.settings(conn)
    article = _article(conn, article_id)
    if article is None or int(article.get("is_deleted") or 0):
        return {"state": STATE_NONE, "chunks": 0, "embedded": 0, "skipped": 0,
                "reason": "条目不存在或已删除"}

    chunks = chunk_article(str(article.get("title") or ""), str(article.get("content") or ""))
    if not chunks:
        _set_state(conn, article_id, STATE_NONE)
        return {"state": STATE_NONE, "chunks": 0, "embedded": 0, "skipped": 0,
                "reason": "条目没有可索引的正文"}

    if not embeddings.usable(settings_map):
        _set_state(conn, article_id, STATE_NONE)
        return {"state": STATE_NONE, "chunks": len(chunks), "embedded": 0, "skipped": 0,
                "reason": embeddings.describe(settings_map)["reason"]}

    model = str(settings_map.get("model") or "")
    existing = _existing(conn, article_id)
    pending: list[int] = []
    pending_texts: list[str] = []
    for index, chunk in enumerate(chunks):
        old = existing.get(index)
        if old and str(old["content_hash"]) == chunk["contentHash"] \
                and str(old["model"]) == model and embeddings.unpack(old["vector"]):
            continue
        pending.append(index)
        pending_texts.append(chunk["text"])

    embedded = 0
    if pending_texts:
        vectors = embeddings.embed_texts(pending_texts, settings_map)
        if vectors is None:
            _set_state(conn, article_id, STATE_STALE if existing else STATE_NONE)
            return {"state": STATE_STALE if existing else STATE_NONE, "chunks": len(chunks),
                    "embedded": 0, "skipped": 0, "reason": "嵌入模型调用失败，已回落关键词检索"}
        now = utcnow_iso()
        project_id = article.get("project_id")
        for index, vector in zip(pending, vectors):
            chunk = chunks[index]
            conn.execute(
                "INSERT INTO biz_kb_embedding (article_id, project_id, chunk_index,"
                " heading_path, title, text, content_hash, model, dim, vector,"
                " created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
                " ON CONFLICT(article_id, chunk_index) DO UPDATE SET"
                " project_id=excluded.project_id, heading_path=excluded.heading_path,"
                " title=excluded.title, text=excluded.text,"
                " content_hash=excluded.content_hash, model=excluded.model,"
                " dim=excluded.dim, vector=excluded.vector, updated_at=excluded.updated_at",
                (int(article_id), project_id, index, chunk["headingPath"], chunk["title"],
                 chunk["text"], chunk["contentHash"], model,
                 len(vector), embeddings.pack(vector), now, now),
            )
            embedded += 1

    # 旧版本遗留的多余块（例如正文被删短）一并清掉
    conn.execute(
        "DELETE FROM biz_kb_embedding WHERE article_id = ? AND chunk_index >= ?",
        (int(article_id), len(chunks)),
    )
    _set_state(conn, article_id, STATE_READY)
    return {"state": STATE_READY, "chunks": len(chunks), "embedded": embedded,
            "skipped": len(chunks) - len(pending_texts), "reason": ""}


def rebuild_all(conn: sqlite3.Connection, *,
                project_id: int | None = None) -> dict[str, Any]:
    """整库重建（「启动重建索引」扩展项）：逐条增量重嵌，单条失败不影响其余。"""
    settings_map = embeddings.settings(conn)
    if not embeddings.usable(settings_map):
        return {"state": STATE_NONE, "articles": 0, "embedded": 0,
                "reason": embeddings.describe(settings_map)["reason"]}
    where = ["is_deleted = 0"]
    params: list[Any] = []
    if project_id is not None:
        where.append("(project_id IS NULL OR project_id = ?)")
        params.append(int(project_id))
    rows = conn.execute(
        f"SELECT id FROM biz_knowledge_article WHERE {' AND '.join(where)} ORDER BY id",
        params,
    ).fetchall()
    embedded = 0
    for row in rows:
        try:
            result = ensure_index(conn, int(row["id"]), settings_map=settings_map)
            embedded += int(result.get("embedded") or 0)
        except sqlite3.Error as exc:  # noqa: BLE001 - 单条失败不影响其余
            log_event(logger, "embedding_rebuild_failed", level=logging.WARNING,
                      articleId=int(row["id"]), error=str(exc)[:200])
    return {"state": STATE_READY, "articles": len(rows), "embedded": embedded, "reason": ""}


def vector_hits(
    conn: sqlite3.Connection,
    query: str,
    *,
    project_id: int | None = None,
    limit: int = 8,
    settings_map: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """向量召回：返回按相似度倒序的 chunk 命中（articleId / score / text）。"""
    settings_map = settings_map if settings_map is not None else embeddings.settings(conn)
    if not embeddings.usable(settings_map):
        return []
    text = str(query or "").strip()
    if not text:
        return []
    vectors = embeddings.embed_texts([text], settings_map)
    if not vectors:
        return []
    query_vector = vectors[0]
    where = ["a.is_deleted = 0", "a.review_status = 'published'", "e.vector IS NOT NULL"]
    params: list[Any] = []
    if project_id is not None:
        where.append("(a.project_id IS NULL OR a.project_id = ?)")
        params.append(int(project_id))
    rows = conn.execute(
        "SELECT e.article_id, e.chunk_index, e.heading_path, e.title, e.text, e.vector"
        " FROM biz_kb_embedding e JOIN biz_knowledge_article a ON a.id = e.article_id"
        f" WHERE {' AND '.join(where)}",
        params,
    ).fetchall()
    scored: list[tuple[float, dict[str, Any]]] = []
    for row in rows:
        score = embeddings.cosine(query_vector, embeddings.unpack(row["vector"]))
        if score <= 0:
            continue
        scored.append((score, dict(row)))
    scored.sort(key=lambda item: (-item[0], int(item[1]["article_id"]),
                                  int(item[1]["chunk_index"])))
    out: list[dict[str, Any]] = []
    seen: set[int] = set()
    for score, row in scored:
        article_id = int(row["article_id"])
        if article_id in seen:
            continue
        seen.add(article_id)
        out.append({
            "articleId": article_id,
            "score": round(score, 6),
            "headingPath": str(row["heading_path"] or ""),
            "text": str(row["text"] or ""),
        })
        if len(out) >= max(1, int(limit)):
            break
    return out


def rrf_fuse(
    literal_ids: list[int],
    vector_ids: list[int],
    *,
    k: int = RRF_K,
) -> list[tuple[int, float]]:
    """倒数排名融合：score = Σ 1/(k + rank)，返回按分倒序的 (id, score)。"""
    scores: dict[int, float] = {}
    for ids in (literal_ids, vector_ids):
        for rank, article_id in enumerate(ids):
            scores[int(article_id)] = scores.get(int(article_id), 0.0) + 1.0 / (k + rank + 1)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


def fuse_with_literal(
    conn: sqlite3.Connection,
    keyword: str,
    literal_rows: list[dict[str, Any]],
    *,
    project_id: int | None = None,
    limit: int = 20,
) -> tuple[list[dict[str, Any]], bool]:
    """把字面召回与向量召回按 RRF 合并；返回（合并结果, 是否走了向量路）。

    无可用嵌入 / 无向量命中时原样返回字面结果，调用方语义不变。
    """
    try:
        hits = vector_hits(conn, keyword, project_id=project_id, limit=limit)
    except (sqlite3.Error, ValueError, TypeError) as exc:  # noqa: BLE001 - 降级
        log_event(logger, "embedding_fuse_failed", level=logging.WARNING,
                  error=str(exc)[:200])
        return literal_rows[:limit], False
    if not hits:
        return literal_rows[:limit], False

    by_id: dict[int, dict[str, Any]] = {
        int(row["id"]): row for row in literal_rows if row.get("id") is not None
    }
    missing = [hit["articleId"] for hit in hits if int(hit["articleId"]) not in by_id]
    if missing:
        placeholders = ",".join("?" * len(missing))
        for row in conn.execute(
            "SELECT * FROM biz_knowledge_article WHERE is_deleted = 0"
            f" AND review_status = 'published' AND id IN ({placeholders})",
            missing,
        ).fetchall():
            by_id[int(row["id"])] = dict(row)

    order = rrf_fuse([int(row["id"]) for row in literal_rows], [h["articleId"] for h in hits])
    text_by_id = {int(hit["articleId"]): hit["text"] for hit in hits}
    out: list[dict[str, Any]] = []
    for article_id, _score in order:
        row = by_id.get(int(article_id))
        if row is None:
            continue
        if not row.get("snippet"):
            text = " ".join(str(text_by_id.get(int(article_id)) or "").split())
            row["snippet"] = text[:240] + ("…" if len(text) > 240 else "")
        if "no" not in row:
            row["no"] = str(row.get("category") or "")
        out.append(row)
        if len(out) >= max(1, int(limit)):
            break
    return out, True


def status(conn: sqlite3.Connection) -> dict[str, Any]:
    """索引概览：供设置页与门禁使用。"""
    settings_map = embeddings.settings(conn)
    row = conn.execute(
        "SELECT COUNT(*) AS c, COUNT(DISTINCT article_id) AS a FROM biz_kb_embedding"
    ).fetchone()
    states = {key: 0 for key in (STATE_NONE, STATE_PARTIAL, STATE_READY, STATE_STALE)}
    for item in conn.execute(
        "SELECT embedding_state AS s, COUNT(*) AS c FROM biz_knowledge_article"
        " WHERE is_deleted = 0 GROUP BY embedding_state"
    ).fetchall():
        key = str(item["s"] or STATE_NONE)
        if key in states:
            states[key] = int(item["c"])
    return {
        "embedding": embeddings.describe(settings_map),
        "chunkCount": int(row["c"]) if row is not None else 0,
        "indexedArticles": int(row["a"]) if row is not None else 0,
        "states": states,
    }
