"""知识库问答助手自演进。

三件事：
  1. 把模型回答连同溯源信息（提问、回答时间、模式、模型、引用）送进待审池；
  2. 自动整理只产出「待审版本」，不直接改正式库——否则会绕过编辑审核规则、
     也会撞上引用保护；
  3. 整理走后台任务，状态落库，进程被杀后由启动对账回退到「待自动整理」。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import Any

from ..common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY
from ..common.time_utils import utcnow_iso
from ..ai.client import chat_with_fallback
from ..ai.masking import cloud_mask_enabled
from ..errors import AppError, ErrorCode
from ..prompts.registry import get_registry
from ..repositories.knowledge_draft_repo import (
    SOURCE_ASSISTANT,
    KnowledgeDraftRepository,
    assistant_title,
    source_type_label,
)
from ..repositories.knowledge_repo import KnowledgeRepository
from ..repositories.knowledge_version_repo import KnowledgeVersionRepository
from . import job_queue, knowledge_category_service, lab_service, prompt_usage

ASSISTANT_MODES = ("llm", "hybrid")
MODE_LABELS = {"llm": "大模型回答", "kb": "知识库回答", "hybrid": "知识库+大模型补强"}

ORGANIZE_PENDING = "pending"
ORGANIZE_RUNNING = "running"
ORGANIZE_DONE = "done"

KIND_ORGANIZE = "knowledge.organize"
ORGANIZE_TASK_TITLE = "知识库自动整理"

#: 预览抽屉里单篇最多给出这么多字符，避免一次渲染几十万字卡住页面
PREVIEW_CHARS = 20000


def fingerprint(question: str, answer: str, answered_at: str) -> str:
    """同一问、同一答、同一回答时间视为同一条记录，重复点击不重复入池。"""
    raw = "\n".join(["assistant", question.strip(), answered_at.strip(), answer.strip()])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _summary_of(answer: str) -> str:
    text = " ".join((answer or "").split())
    return text[:160]


def _quote(text: str, limit: int = 2000) -> str:
    body = (text or "").strip()
    if len(body) > limit:
        body = body[:limit] + "…"
    return "\n".join(f"> {line}" if line else ">" for line in body.splitlines())


def build_content(*, question: str, answer: str, mode: str, model: str,
                  answered_at: str, refs: list[dict[str, Any]]) -> str:
    refs_text = "、".join(
        f"[{item.get('marker') or 'K'}] {item.get('title') or ''}".strip() for item in refs
    ) or "无"
    meta = " · ".join([
        f"回答模式：{MODE_LABELS.get(mode, mode)}",
        f"模型：{model or '未记录'}",
        f"回答时间：{answered_at or '未记录'}",
        f"参考条目：{refs_text}",
    ])
    return (
        f"## 提问\n\n{question.strip()}\n\n"
        f"## 回答\n\n{answer.strip()}\n\n"
        f"> 来源：知识库问答助手 · {meta}"
    )


# ---------------- 入池 ----------------

def add_answer(conn: sqlite3.Connection, *, question: str, answer: str, mode: str,
               model: str, answered_at: str, refs: list[dict[str, Any]],
               review_required: bool, category: str = "",
               module_id: int | None = None, project_id: int | None = None) -> dict[str, Any]:
    if mode not in ASSISTANT_MODES:
        raise AppError(ErrorCode.PARAM_ERROR, "知识库直答的内容来自知识库，不需要再加回知识库")
    stamp = (answered_at or "").strip() or utcnow_iso()
    digest = fingerprint(question, answer, stamp)
    repo = KnowledgeDraftRepository(conn)
    existing = repo.find_by_content_hash(digest)
    if existing:
        return {"draft": existing, "duplicated": True, "articleId": existing.get("article_id")}

    title = assistant_title(question, stamp, with_time=True)
    new_id = repo.create_draft(
        title,
        build_content(
            question=question, answer=answer, mode=mode, model=model,
            answered_at=stamp, refs=refs,
        ),
        summary=_summary_of(answer),
        category=knowledge_category_service.ensure_category(conn, category),
        tags="问答助手",
        module_id=module_id,
        project_id=project_id,
        source_type=SOURCE_ASSISTANT,
        producer=source_type_label(SOURCE_ASSISTANT),
        answered_at=stamp,
        answer_mode=mode,
        answer_model=model,
        refs_json=json.dumps(refs or [], ensure_ascii=False),
        review_required=review_required,
        content_hash=digest,
    )
    return {"draft": repo.get(new_id) or {}, "duplicated": False, "articleId": None}


# ---------------- 外发预检 ----------------

def egress_plan(mode: str) -> dict[str, Any]:
    """返回本次问答实际可能走到的模型链路。

    只看首选模型不够：本地模型失败会降级到云端，
    所以只要有任意一个候选会把数据带出本机，就必须先确认脱敏。
    """
    if mode == "kb":
        return {"externalPossible": False, "egress": EGRESS_NONE, "candidates": []}
    candidates = lab_service.available_candidates(None)
    items = [
        {
            "provider": item.provider,
            "model": item.model,
            "isLocal": bool(item.is_local),
            "egress": item.egress,
        }
        for item in candidates
    ]
    external = any(str(item["egress"]) != EGRESS_NONE for item in items)
    return {
        "externalPossible": external,
        "egress": EGRESS_THIRD_PARTY if external else EGRESS_NONE,
        "candidates": items,
    }


def _published_assistant(conn: sqlite3.Connection, limit: int = 200) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_knowledge_article WHERE is_deleted = 0 AND review_status = 'published'"
        " AND source_type = ? ORDER BY updated_at DESC LIMIT ?",
        (SOURCE_ASSISTANT, limit),
    ).fetchall()
    return [dict(row) for row in rows]


def preview_files(conn: sqlite3.Connection) -> dict[str, Any]:
    """列出问答助手来源的正式条目及其 .md 全文，供外发前的站内检查。"""
    from . import knowledge_storage

    root = knowledge_storage.storage_root(conn)
    mode = knowledge_storage.file_mode(conn)
    files: list[dict[str, Any]] = []
    for row in _published_assistant(conn):
        relative = str(row.get("file_path") or "")
        absolute = ""
        exists = False
        content = str(row.get("content") or "")
        from_file = False
        if relative:
            try:
                target = knowledge_storage.safe_join(root, relative)
            except AppError:
                target = None
            if target is not None:
                absolute = str(target)
                exists = target.exists()
                if exists:
                    try:
                        content = target.read_text(encoding="utf-8")
                        from_file = True
                    except OSError:
                        exists = False
        files.append({
            "articleId": row.get("id"),
            "title": row.get("title"),
            "relativePath": relative,
            "absolutePath": absolute,
            "exists": exists,
            "fromFile": from_file,
            "truncated": len(content) > PREVIEW_CHARS,
            "content": content[:PREVIEW_CHARS],
        })
    return {
        "fileMode": mode,
        "storagePath": str(root),
        "articleCount": len(files),
        "files": files,
    }


# ---------------- 自动整理 ----------------

def _reset_state(conn: sqlite3.Connection, status: str) -> None:
    conn.execute(
        "UPDATE biz_knowledge_article SET organize_status = ?, organize_task_id = NULL,"
        " updated_at = ? WHERE source_type = ? AND is_deleted = 0",
        (status, utcnow_iso(), SOURCE_ASSISTANT),
    )


def submit_organize(conn: sqlite3.Connection, article_id: int) -> dict[str, Any]:
    """为一篇问答助手条目发起自动整理；实际的整理范围是全部助手来源条目。"""
    article = KnowledgeRepository(conn).get(article_id)
    if not article:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    if article.get("source_type") != SOURCE_ASSISTANT:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有来源为知识库问答助手的条目才需要自动整理")
    if article.get("review_status") != "published":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有已发布的条目才能自动整理")

    running = conn.execute(
        "SELECT COUNT(*) FROM biz_knowledge_article WHERE is_deleted = 0 AND source_type = ?"
        " AND organize_status = ?",
        (SOURCE_ASSISTANT, ORGANIZE_RUNNING),
    ).fetchone()[0]
    if int(running) > 0:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "已有自动整理任务在后台执行，请等它结束")

    task = job_queue.submit(
        conn,
        kind=KIND_ORGANIZE,
        title=ORGANIZE_TASK_TITLE,
        payload={"articleId": article_id},
        priority=120,
    )
    task_id = int(task.get("id") or 0)
    conn.execute(
        "UPDATE biz_knowledge_article SET organize_status = ?, organize_task_id = ?, updated_at = ?"
        " WHERE is_deleted = 0 AND source_type = ? AND review_status = 'published'",
        (ORGANIZE_RUNNING, task_id, utcnow_iso(), SOURCE_ASSISTANT),
    )
    conn.commit()
    return {"task": task, "taskId": task_id}


def _proposals_from(text: str) -> dict[str, Any]:
    parsed = lab_service.extract_json(text)
    if not isinstance(parsed, dict):
        raise AppError(ErrorCode.AI_OUTPUT_INVALID_JSON, "自动整理没有返回可解析的整理提案")
    return parsed


def _as_int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _normalize(parsed: dict[str, Any], rows: list[dict[str, Any]],
               categories: list[str]) -> tuple[dict[int, dict[str, Any]], list[dict[str, Any]]]:
    by_id = {int(row["id"]): row for row in rows}
    groups: list[dict[str, Any]] = []
    for item in parsed.get("groups") or []:
        if not isinstance(item, dict):
            continue
        keep_id = _as_int(item.get("keepId"))
        if keep_id is None:
            continue
        merge_ids = []
        for raw in item.get("mergeIds") or []:
            value = _as_int(raw)
            if value is not None and value in by_id and value != keep_id:
                merge_ids.append(value)
        if keep_id in by_id and merge_ids:
            groups.append({"keepId": keep_id, "mergeIds": merge_ids,
                           "reason": str(item.get("reason") or "").strip()})

    merged_targets = {mid for group in groups for mid in group["mergeIds"]}
    proposals: dict[int, dict[str, Any]] = {}
    for item in parsed.get("articles") or []:
        if not isinstance(item, dict):
            continue
        article_id = _as_int(item.get("id"))
        if article_id is None:
            continue
        row = by_id.get(article_id)
        if row is None or article_id in merged_targets:
            continue
        category = str(item.get("category") or "").strip()
        proposals[article_id] = {
            "category": category if category in categories else (row.get("category") or ""),
            "tags": str(item.get("tags") or row.get("tags") or "").strip(),
            "summary": str(item.get("summary") or row.get("summary") or "").strip(),
            "title": str(item.get("title") or row.get("title") or "").strip(),
        }
    return proposals, groups


def _changes(row: dict[str, Any], proposal: dict[str, Any]) -> tuple[list[str], bool]:
    """返回变化说明与是否值得建版本。

    只改摘要措辞不值得让用户审一遍：分类/标签/标题与合并才是结构性变化。
    """
    changes: list[str] = []
    for key, label in (("title", "标题"), ("category", "分类"), ("tags", "标签")):
        old = str(row.get(key) or "").strip()
        new = str(proposal.get(key) or "").strip()
        if new and new != old:
            changes.append(f"{label}「{old or '空'}」→「{new}」")
    old_summary = str(row.get("summary") or "").strip()
    new_summary = str(proposal.get("summary") or "").strip()
    if new_summary and new_summary != old_summary:
        changes.append("摘要已重写")
    return changes, bool(changes and any(not item.startswith("摘要") for item in changes))


def _apply(conn: sqlite3.Connection, rows: list[dict[str, Any]],
           proposals: dict[int, dict[str, Any]], groups: list[dict[str, Any]]) -> dict[str, Any]:
    """把整理提案落成待审版本。

    - 与现状一致、或只改摘要措辞的提案不建版本，避免待审池被无意义条目淹没；
    - 重复问答不再复制正文到保留条目，只记录要合并的 id；审核通过后才把
      被合并条目标为 merged，退出检索与统计，正文保持原样可追溯。
    """
    versions = KnowledgeVersionRepository(conn)
    by_id = {int(row["id"]): row for row in rows}
    pending_ids = {
        int(item["article_id"])
        for item in conn.execute(
            "SELECT article_id FROM biz_knowledge_version WHERE review_status = 'pending'"
        ).fetchall()
    }

    # 已经有待审版本的保留条目不参与本轮合并，等人工先处理掉手上的提案
    merge_plans: dict[int, list[int]] = {}
    for group in groups:
        keep_id = int(group["keepId"])
        if keep_id in pending_ids:
            continue
        targets = [
            int(mid) for mid in group["mergeIds"]
            if int(mid) not in pending_ids and by_id[int(mid)].get("review_status") == "published"
        ]
        if targets:
            merge_plans[keep_id] = targets

    created = 0
    skipped = 0
    merged = 0
    for article_id, row in by_id.items():
        if article_id in pending_ids:
            skipped += 1
            continue
        proposal = proposals.get(article_id, {
            "category": row.get("category") or "",
            "tags": row.get("tags") or "",
            "summary": row.get("summary") or "",
            "title": row.get("title") or "",
        })
        change_list, structural = _changes(row, proposal)
        next_change_log = "；".join(change_list)
        merge_ids = merge_plans.get(article_id, [])
        if not structural and not merge_ids:
            skipped += 1
            continue
        if merge_ids:
            names = "、".join(
                f"《{by_id[mid].get('title') or ''}》(#{mid})" for mid in merge_ids
            )
            merge_note = f"合并 {len(merge_ids)} 条重复问答：{names}"
            change_log = (
                f"自动整理：{next_change_log}；{merge_note}"
                if next_change_log else f"自动整理：{merge_note}"
            )
            merged += len(merge_ids)
        else:
            change_log = f"自动整理：{next_change_log}"
        versions.create_version(
            article_id,
            title=proposal["title"] or str(row.get("title") or ""),
            content=str(row.get("content") or ""),
            summary=proposal["summary"],
            category=proposal["category"] or str(row.get("category") or ""),
            tags=proposal["tags"],
            change_log=change_log,
            created_by="知识库问答助手",
            merge_targets=merge_ids or None,
        )
        created += 1
    return {"versions": created, "skipped": skipped, "merged": merged, "groups": len(merge_plans)}


def run_organize(conn: sqlite3.Connection, task: dict[str, Any],
                 payload: dict[str, Any], db_path: Any) -> dict[str, Any]:
    task_id = int(task["id"])
    try:
        rows = _published_assistant(conn)
        if not rows:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "没有可整理的问答助手条目")
        blocks = [
            f"[A{row['id']}] 标题：{row.get('title') or ''}\n"
            f"分类：{row.get('category') or ''}\n摘要：{row.get('summary') or ''}\n"
            f"正文：\n{_quote(str(row.get('content') or ''), 1500)}"
            for row in rows
        ]
        categories = knowledge_category_service.list_names(conn)
        prompt = get_registry().render("knowledge-organize", {
            "articles": "\n\n".join(blocks),
            "categories": "、".join(categories),
        })
        outcome = chat_with_fallback(
            lab_service.available_candidates(None),
            lab_service.build_messages(prompt),
            mask_enabled=cloud_mask_enabled(conn),
        )
        parsed = _proposals_from(outcome.result.content or "")
        proposals, groups = _normalize(parsed, rows, categories)
        summary = _apply(conn, rows, proposals, groups)
        now = utcnow_iso()
        conn.execute(
            "UPDATE biz_knowledge_article SET organize_status = ?, organize_at = ?, updated_at = ?"
            " WHERE is_deleted = 0 AND source_type = ? AND review_status = 'published'",
            (ORGANIZE_DONE, now, now, SOURCE_ASSISTANT),
        )
        conn.commit()
        prompt_usage.record(conn, "knowledge-organize")
        summary.update({
            "articles": len(rows),
            "model": outcome.result.model or outcome.config.model,
            "taskId": task_id,
        })
        return summary
    except Exception:
        # 整理过程整体事务化；失败要把状态退回「待自动整理」，否则按钮永远卡在执行中
        conn.rollback()
        _reset_state(conn, ORGANIZE_PENDING)
        conn.commit()
        raise


def reconcile(conn: sqlite3.Connection) -> int:
    """启动对账：没有活着的整理任务时，把状态复位到「待自动整理」。"""
    cursor = conn.execute(
        "UPDATE biz_knowledge_article SET organize_status = ?, organize_task_id = NULL,"
        " updated_at = ? WHERE is_deleted = 0 AND organize_status = ?"
        " AND (organize_task_id IS NULL OR organize_task_id NOT IN"
        " (SELECT id FROM sys_task WHERE status IN ('queued', 'running')))",
        (ORGANIZE_PENDING, utcnow_iso(), ORGANIZE_RUNNING),
    )
    return int(cursor.rowcount or 0)


def latest_task(conn: sqlite3.Connection, task_id: int | None) -> dict[str, Any] | None:
    if not task_id:
        return None
    return job_queue.get_task(conn, int(task_id))


job_queue.register(KIND_ORGANIZE, run_organize)
