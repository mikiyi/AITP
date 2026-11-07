"""知识库 API：待审池 + 正式库 + 版本 + 引用保护。"""

from __future__ import annotations

import logging
from typing import Any

from flask import Blueprint, Response, request

from ..ai.client import chat_with_fallback
from ..ai.masking import cloud_mask_enabled
from ..ai.safety import needs_human_review
from ..common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY
from ..common.validators import check_choice, optional_int, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.knowledge_category_repo import (
    KnowledgeCategoryRepository,
    decorate as category_public,
)
from ..repositories.knowledge_draft_repo import (
    DRAFT_STATUS_PENDING,
    SOURCE_ASSISTANT,
    KnowledgeDraftRepository,
    source_type_label,
)
from ..repositories.base import loads
from ..repositories.knowledge_ref_repo import KnowledgeRefRepository
from ..repositories.knowledge_repo import KnowledgeRepository
from ..repositories.knowledge_version_repo import KnowledgeVersionRepository
from ..services import (
    audit_service,
    knowledge_assistant_service,
    knowledge_category_service,
    knowledge_storage,
    prompt_usage,
    tabular_export,
)
from ..repositories.sys_config_repo import SysConfigRepository
from .helpers import (audit, body, bool_arg, commit, db, download_response, keyword_of, page_args,
                      page_payload, path_int, project_of_module, query_filters, tabular_format_of)

bp = Blueprint("knowledge", __name__)
logger = logging.getLogger(__name__)

ARTICLE_FILTERS = {"projectId": "project_id", "category": "category",
                   "sourceType": "source_type", "moduleId": "module_id"}
DRAFT_FILTERS = {"projectId": "project_id", "reviewStatus": "review_status",
                 "sourceType": "source_type", "moduleId": "module_id"}


#: 问答助手的三种回答方式
ASSISTANT_MODES = ("kb", "llm", "hybrid")


@bp.post("/ask")
def ask_assistant() -> Response:
    """知识库问答助手：知识库直答 / 纯大模型 / 知识库 + 大模型补强。"""
    conn = db()
    data = body()
    question = require_str(data.get("question"), "question", 2000)
    mode = check_choice(str(data.get("mode") or "hybrid"), ASSISTANT_MODES, "mode", "hybrid") or "hybrid"

    from ..services import lab_service
    from ..prompts.registry import get_registry

    context = ""
    refs: list[dict[str, Any]] = []
    retrieval_error = ""
    if mode != "llm":
        context, refs, retrieval_error = lab_service.retrieve_knowledge_context(conn, question)

    if mode == "kb":
        # 纯本地：直接把检索到的原文交给用户，不调模型
        if context:
            answer = "以下内容摘自知识库，未经模型加工：" + chr(10) + chr(10) + context
        else:
            answer = retrieval_error or "知识库中没有检索到与问题相关的内容。可以换个说法，或先到「知识库管理」补充条目。"
        return ok({
            "mode": mode,
            "answer": answer,
            "refs": refs,
            "model": "",
            "latencyMs": 0,
            "usage": {},
            "egress": EGRESS_NONE,
            "egressWarning": "",
            "reviewRequired": False,
        })

    prompt = get_registry().render("knowledge-assistant", {
        "question": question,
        "context": context or retrieval_error or "（未提供）",
    })
    outcome = chat_with_fallback(
        lab_service.available_candidates(None),
        lab_service.build_messages(prompt),
        mask_enabled=cloud_mask_enabled(conn),
    )
    # 问答助手不落 AI 执行记录，模板调用次数单独计入提示词库
    prompt_usage.record(conn, "knowledge-assistant")
    answer = (outcome.result.content or "").strip()
    third_party = outcome.config.egress == EGRESS_THIRD_PARTY
    return ok({
        "mode": mode,
        "answer": answer,
        "refs": refs,
        "model": outcome.result.model or outcome.config.model,
        "latencyMs": outcome.result.latency_ms,
        "usage": outcome.result.usage.as_dict(),
        "egress": outcome.config.egress,
        "egressWarning": (
            "本次回答会把问题和检索到的知识发送到第三方模型，请注意数据脱敏。" if third_party else ""
        ),
        "reviewRequired": needs_human_review(answer),
        "degrade": outcome.degrade.as_dict(),
        "mask": outcome.mask.as_dict(),
    })


@bp.post("/assistant/add")
def add_assistant_answer() -> Response:
    """把问答助手的模型回答连同溯源信息送进待审池。

    只接受调用过大模型的两种模式：知识库直答的正文本来就来自正式库，
    再加回去等于自我复制。
    """
    conn = db()
    data = body()
    question = require_str(data.get("question"), "question", 2000)
    answer = require_str(data.get("answer"), "answer", 50000)
    mode = check_choice(data.get("mode"), knowledge_assistant_service.ASSISTANT_MODES, "mode")
    if not mode:
        raise AppError(ErrorCode.PARAM_ERROR, "只能把调用大模型的回答加入知识库")
    model = optional_str(data.get("model"), "model", 120)
    answered_at = optional_str(data.get("answeredAt"), "answeredAt", 40)
    refs_raw = data.get("refs")
    refs = [item for item in refs_raw if isinstance(item, dict)] if isinstance(refs_raw, list) else []
    result = knowledge_assistant_service.add_answer(
        conn,
        question=question,
        answer=answer,
        mode=mode,
        model=model,
        answered_at=answered_at,
        refs=refs,
        review_required=bool_arg(data.get("reviewRequired")),
        category=optional_str(data.get("category"), "category", 40),
        module_id=data.get("moduleId"),
        project_id=(optional_int(data.get("projectId"), "projectId")
                    or project_of_module(conn, data.get("moduleId"))),
    )
    draft = result["draft"]
    commit(conn)
    if not result["duplicated"]:
        audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_KNOWLEDGE_DRAFT,
              draft.get("id"), str(draft.get("title") or ""), {"sourceType": SOURCE_ASSISTANT, "mode": mode})
    payload = _draft_public(draft)
    payload["duplicated"] = result["duplicated"]
    return ok(payload)


@bp.get("/assistant/egress")
def assistant_egress() -> Response:
    """问答链路的实际外发评估：本地首选 + 云端兜底也算可能外发。"""
    mode = check_choice(request.args.get("mode"), knowledge_assistant_service.ASSISTANT_MODES, "mode", "hybrid")
    return ok(knowledge_assistant_service.egress_plan(mode or "hybrid"))


@bp.get("/assistant/preview")
def assistant_preview() -> Response:
    """外发前的站内检查：列出问答助手来源正式条目的 .md 全文与落盘路径。"""
    return ok(knowledge_assistant_service.preview_files(db()))


@bp.get("/categories")
def list_categories() -> Response:
    """分类清单：内置中文分类 + 用户自定义分类，按排序值升序。"""
    conn = db()
    return ok({"list": KnowledgeCategoryRepository(conn).list_categories()})


@bp.post("/categories")
def create_category() -> Response:
    """新增自定义分类；名称就是文章里的分类值。"""
    conn = db()
    data = body()
    name = require_str(data.get("name"), "name", 20)
    repo = KnowledgeCategoryRepository(conn)
    if repo.find_by_name(name):
        raise AppError(ErrorCode.UNIQUE_CONFLICT, "该分类已存在")
    row_id = repo.insert({
        "name": name,
        "sort_order": repo.next_sort_order(),
        "is_system": 0,
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE,
          audit_service.RESOURCE_KNOWLEDGE_CATEGORY, row_id, name, {})
    return ok(category_public(repo.get(row_id) or {}))


@bp.delete("/categories/<pk>")
def delete_category(pk: str) -> Response:
    """删除自定义分类；内置分类不允许删除，已归类文章不受影响。"""
    conn = db()
    repo = KnowledgeCategoryRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "分类不存在")
    if row.get("is_system"):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "内置分类不能删除")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE,
          audit_service.RESOURCE_KNOWLEDGE_CATEGORY, target, str(row.get("name") or ""))
    return ok({"id": target})
ARTICLE_TABULAR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("id", "ID"),
    ("title", "标题"),
    ("category", "分类"),
    ("tags", "标签"),
    ("summary", "摘要"),
    ("reviewStatus", "审核状态"),
    ("currentVersion", "当前版本"),
    ("sourceType", "来源类型"),
    ("viewCount", "查看次数"),
    ("filePath", "文件路径"),
    ("updatedAt", "更新时间"),
)


def _sync_file(conn: Any, article_id: int) -> str:
    """把条目写入知识库 Markdown 文件，返回相对路径。

    DB 提交后再落盘；文件写失败不回滚 DB，只在响应里提示可重建索引。
    """
    repo = KnowledgeRepository(conn)
    row = repo.get(article_id)
    if not row:
        return ""
    previous = str(row.get("file_path") or "")
    try:
        relative = knowledge_storage.write_article(conn, row)
    except OSError:
        return ""
    if relative:
        repo.update(article_id, {"file_path": relative})
        if previous and previous != relative:
            # 文件名带标题，改名等于换了一个文件；旧文件不删就成了同 id 的残留，
            # 重建索引时会被当成另一份内容读回来。删不掉不影响本次落盘。
            try:
                knowledge_storage.remove_article_file(conn, previous)
            except (OSError, AppError):
                logger.warning("知识条目 %s 改名后旧文件未删除：%s", article_id, previous)
    return relative



def _article_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "content": row.get("content"),
        "summary": row.get("summary"),
        "category": row.get("category"),
        "tags": row.get("tags"),
        "sourceType": row.get("source_type"),
        "sourceTypeLabel": source_type_label(str(row.get("source_type") or "")),
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "sessionId": row.get("session_id"),
        "aiTaskId": row.get("ai_task_id"),
        "refType": row.get("ref_type"),
        "refId": row.get("ref_id"),
        "answeredAt": row.get("answered_at"),
        "answerMode": row.get("answer_mode"),
        "answerModel": row.get("answer_model"),
        "reviewRequired": bool(row.get("review_required")),
        "refs": loads(row.get("refs_json"), []) or [],
        "organizeStatus": row.get("organize_status") or "",
        "organizeTaskId": row.get("organize_task_id"),
        "organizeAt": row.get("organize_at"),
        "viewCount": row.get("view_count"),
        "reviewStatus": row.get("review_status"),
        "mergedInto": row.get("merged_into"),
        "currentVersion": row.get("current_version"),
        "draftVersion": row.get("draft_version"),
        "filePath": row.get("file_path"),
        "contentChecksum": row.get("content_checksum"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def _version_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "articleId": row.get("article_id"),
        "version": row.get("version"),
        "title": row.get("title"),
        "content": row.get("content"),
        "summary": row.get("summary"),
        "category": row.get("category"),
        "tags": row.get("tags"),
        "reviewStatus": row.get("review_status"),
        "reviewer": row.get("reviewer"),
        "reviewComment": row.get("review_comment"),
        "reviewedAt": row.get("reviewed_at"),
        "changeLog": row.get("change_log"),
        "checksum": row.get("checksum"),
        "createdBy": row.get("created_by"),
        "mergeTargets": loads(row.get("merge_targets_json"), []) or [],
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
        "contentLength": row.get("content_length"),
    }


def _draft_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "title": row.get("title"),
        "content": row.get("content"),
        "summary": row.get("summary"),
        "category": row.get("category"),
        "tags": row.get("tags"),
        "moduleId": row.get("module_id"),
        "sourceType": row.get("source_type"),
        "sourceTypeLabel": source_type_label(str(row.get("source_type") or "")),
        "refType": row.get("ref_type"),
        "refId": row.get("ref_id"),
        "refLabel": row.get("ref_label"),
        "producer": row.get("producer"),
        "answeredAt": row.get("answered_at"),
        "answerMode": row.get("answer_mode"),
        "answerModel": row.get("answer_model"),
        "reviewRequired": bool(row.get("review_required")),
        "refs": loads(row.get("refs_json"), []) or [],
        "reviewStatus": row.get("review_status"),
        "reviewer": row.get("reviewer"),
        "reviewComment": row.get("review_comment"),
        "reviewedAt": row.get("reviewed_at"),
        "articleId": row.get("article_id"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


# ---------------- 待审池 ----------------

@bp.get("/drafts")
def list_drafts() -> Response:
    conn = db()
    page_num, page_size = page_args()
    status = request.args.get("reviewStatus") or DRAFT_STATUS_PENDING
    repo = KnowledgeDraftRepository(conn)
    filters = query_filters(DRAFT_FILTERS)
    filters.pop("review_status", None)
    payload = repo.list_drafts(status, page_num=page_num, page_size=page_size, filters=filters)
    payload["list"] = [_draft_public(row) for row in payload["list"]]
    payload["stats"] = repo.stats()
    return ok(payload)


@bp.post("/drafts")
def create_draft() -> Response:
    conn = db()
    data = body()
    title = require_str(data.get("title"), "title", 500)
    content = require_str(data.get("content"), "content", 500000)
    category = knowledge_category_service.ensure_category(conn, data.get("category"))
    source_type = optional_str(data.get("sourceType"), "sourceType", 40) or "ai_output"
    module_id = data.get("moduleId")
    # 待审产出的项目维度：显式 projectId 优先，其次由功能点反推，供项目视图的待审核数字使用
    project_id = optional_int(data.get("projectId"), "projectId") or project_of_module(conn, module_id)
    repo = KnowledgeDraftRepository(conn)
    new_id = repo.create_draft(
        title,
        content,
        summary=optional_str(data.get("summary"), "summary", 2000),
        category=category,
        tags=optional_str(data.get("tags"), "tags", 500),
        module_id=module_id,
        project_id=project_id,
        source_type=source_type,
        ref_type=optional_str(data.get("refType"), "refType", 40),
        ref_id=data.get("refId"),
        ref_label=optional_str(data.get("refLabel"), "refLabel", 300),
        producer=optional_str(data.get("producer"), "producer", 64),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_KNOWLEDGE_DRAFT, new_id, title,
          {"sourceType": source_type})
    return ok(_draft_public(KnowledgeDraftRepository(conn).get(new_id) or {}))


@bp.put("/drafts/<pk>")
def update_draft(pk: str) -> Response:
    conn = db()
    repo = KnowledgeDraftRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "待审条目不存在")
    if row.get("review_status") != DRAFT_STATUS_PENDING:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该条目已审核，无法再编辑")
    data = body()
    payload = {
        "title": require_str(data.get("title"), "title", 500) if "title" in data else row["title"],
        "content": require_str(data.get("content"), "content", 500000) if "content" in data else row["content"],
    }
    if "category" in data:
        payload["category"] = knowledge_category_service.ensure_category(conn, data["category"])
    for key in ("summary", "tags"):
        if key in data:
            payload[key] = optional_str(data[key], key, 2000 if key == "summary" else 500)
    if "moduleId" in data:
        payload["module_id"] = data.get("moduleId")
    repo.update_draft(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_KNOWLEDGE_DRAFT, target,
          payload.get("title", ""))
    return ok(_draft_public(repo.get(target) or {}))


@bp.post("/drafts/<pk>/review")
def review_draft(pk: str) -> Response:
    conn = db()
    repo = KnowledgeDraftRepository(conn)
    target = path_int(pk)
    data = body()
    action = check_choice(data.get("action"), ("approve", "reject"), "action")
    if not action:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 action 不能为空")
    reviewer = optional_str(data.get("reviewer"), "reviewer", 64)
    comment = optional_str(data.get("comment"), "comment", 2000)
    result = repo.review(target, action, reviewer=reviewer, review_comment=comment)
    commit(conn)
    file_path = ""
    if action == "approve" and result.get("articleId"):
        file_path = _sync_file(conn, int(result["articleId"]))
        commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_REVIEW, audit_service.RESOURCE_KNOWLEDGE_DRAFT, target, "",
          {"action": action, "articleId": result.get("articleId")})
    result["filePath"] = file_path
    return ok(result)


@bp.post("/drafts/batch-review")
def batch_review_drafts() -> Response:
    conn = db()
    data = body()
    ids = data.get("draftIds")
    if not isinstance(ids, list) or not ids:
        raise AppError(ErrorCode.PARAM_ERROR, "draftIds 必须是非空数组")
    action = check_choice(data.get("action"), ("approve", "reject"), "action")
    if not action:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 action 不能为空")
    reviewer = optional_str(data.get("reviewer"), "reviewer", 64)
    comment = optional_str(data.get("comment"), "comment", 2000)
    result = KnowledgeDraftRepository(conn).batch_review(
        [require_int(i, "draftIds[]") for i in ids], action, reviewer=reviewer, review_comment=comment
    )
    commit(conn)
    if action == "approve":
        for article_id in result.get("succeeded", []):
            _sync_file(conn, int(article_id))
        commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_REVIEW, audit_service.RESOURCE_KNOWLEDGE_DRAFT, None, "",
          {"action": action, "successCount": result["successCount"], "failCount": result["failCount"]})
    return ok(result)


# ---------------- 正式库 ----------------

@bp.get("/articles")
def list_articles() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = KnowledgeRepository(conn)
    filters = query_filters(ARTICLE_FILTERS)
    status = request.args.get("reviewStatus")
    # 正式库未指定状态时只出已发布；reviewStatus=all 才返回全部（含已撤销）
    if status and status != "all":
        filters["review_status"] = status
    elif not status:
        filters["review_status"] = "published"
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [_article_public(row) for row in payload["list"]]
    return ok(payload)


@bp.get("/articles/export")
def export_articles() -> Response:
    """导出正式库清单为 CSV / JSON，筛选条件与列表接口一致。"""
    conn = db()
    fmt = tabular_format_of(request.args.get("format"))
    repo = KnowledgeRepository(conn)
    filters = query_filters(ARTICLE_FILTERS)
    status = request.args.get("reviewStatus")
    if status and status != "all":
        filters["review_status"] = status
    elif not status:
        filters["review_status"] = "published"
    rows = repo.list_all(filters, keyword_of() or None, limit=10000)
    content, mimetype, extension = tabular_export.render(
        fmt, ARTICLE_TABULAR_COLUMNS, [_article_public(row) for row in rows]
    )
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_KNOWLEDGE_ARTICLE,
          None, "", {"format": fmt, "count": len(rows)})
    return download_response(
        content, tabular_export.safe_filename("知识库正式库", extension), mimetype, count=len(rows)
    )


@bp.get("/articles/<pk>")
def get_article(pk: str) -> Response:
    conn = db()
    repo = KnowledgeRepository(conn)
    row = repo.get(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    repo.increment_views(path_int(pk))
    commit(conn)
    payload = _article_public(repo.get(path_int(pk)) or {})
    if payload.get("mergedInto"):
        keeper = repo.get(int(payload["mergedInto"]))
        payload["mergedIntoTitle"] = (keeper or {}).get("title") or ""
    payload["pendingVersions"] = [
        _version_public(row) for row in KnowledgeVersionRepository(conn).pending_versions(path_int(pk))
    ]
    payload["refs"] = KnowledgeRefRepository(conn).list_refs(path_int(pk))
    payload["organizeTask"] = knowledge_assistant_service.latest_task(
        conn, payload.get("organizeTaskId")
    )
    file_info = knowledge_storage.file_status(conn, repo.get(path_int(pk)) or {})
    payload["fileChanged"] = bool(file_info.get("changed"))
    payload["fileExists"] = bool(file_info.get("exists"))
    payload["fileMode"] = file_info.get("mode")
    return ok(payload)


@bp.put("/articles/<pk>")
def update_article(pk: str) -> Response:
    """编辑已发布条目 → 产生待审新版本，当前正文与索引保持不变。"""
    conn = db()
    repo = KnowledgeRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    data = body()
    title = require_str(data.get("title"), "title", 500)
    content = require_str(data.get("content"), "content", 500000)
    # 只改正文时保留原分类与标签，避免新版本把元信息清空
    category = str(
        knowledge_category_service.ensure_category(conn, data.get("category"))
        if "category" in data
        else row.get("category") or ""
    )
    tags = str(
        optional_str(data.get("tags"), "tags", 500) if "tags" in data else row.get("tags") or ""
    )
    version_id = KnowledgeVersionRepository(conn).create_version(
        target,
        title=title,
        content=content,
        summary=optional_str(data.get("summary"), "summary", 2000),
        category=category,
        tags=tags,
        change_log=optional_str(data.get("changeLog"), "changeLog", 1000),
        created_by=optional_str(data.get("operator"), "operator", 64),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_KNOWLEDGE_VERSION, version_id,
          title, {"articleId": target})
    return ok({"articleId": target, "versionId": version_id, "status": "pending"})


@bp.delete("/articles/<pk>")
def delete_article(pk: str) -> Response:
    conn = db()
    repo = KnowledgeRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    repo.delete_article(target)
    commit(conn)
    try:
        knowledge_storage.remove_article_file(conn, str(row.get("file_path") or ""))
    except (OSError, AppError) as exc:
        # 条目已经删掉了，文件删不掉不能让接口报错；记进日志，
        # 重建索引时这类孤儿文件会按「以文件为准」把条目复活。
        logger.warning("知识条目 %s 落盘文件删除失败：%s", target, exc)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, target, "")
    return ok({"id": target, "deleted": True})


@bp.post("/articles/<pk>/revoke")
def revoke_article(pk: str) -> Response:
    conn = db()
    repo = KnowledgeRepository(conn)
    target = path_int(pk)
    data = body()
    operator = optional_str(data.get("operator"), "operator", 64)
    result = repo.revoke(target, operator=operator)
    commit(conn)
    # 撤销只改元信息，文件保留但标注为 revoked，重建索引时不会把它算成正式资产
    _sync_file(conn, target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_REVOKE, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, target, "",
          {"operator": operator})
    return ok(result)


@bp.post("/articles/<pk>/restore")
def restore_article(pk: str) -> Response:
    conn = db()
    repo = KnowledgeRepository(conn)
    target = path_int(pk)
    result = repo.restore(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, target, "",
          {"restored": True})
    return ok(result)


@bp.get("/articles/<pk>/organize")
def organize_status(pk: str) -> Response:
    """自动整理进度：前端轮询这个接口，避免为了看状态反复拉详情（详情会累加查看次数）。"""
    conn = db()
    target = path_int(pk)
    row = KnowledgeRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    return ok({
        "articleId": target,
        "organizeStatus": row.get("organize_status") or "",
        "organizeAt": row.get("organize_at"),
        "task": knowledge_assistant_service.latest_task(conn, row.get("organize_task_id")),
    })


@bp.post("/articles/<pk>/organize")
def organize_article(pk: str) -> Response:
    """发起问答助手来源条目的自动整理；整理只产出待审版本，不改正式正文。"""
    conn = db()
    target = path_int(pk)
    data = body()
    egress = knowledge_assistant_service.egress_plan("hybrid")
    if egress["externalPossible"] and not bool_arg(data.get("desensitized")):
        raise AppError(ErrorCode.PARAM_ERROR, "调用外部大模型前需要先确认数据已脱敏")
    result = knowledge_assistant_service.submit_organize(conn, target)
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_KNOWLEDGE_ORGANIZE, target,
          "", {"taskId": result.get("taskId"), "desensitized": bool_arg(data.get("desensitized"))})
    return ok(result)


# ---------------- 版本 ----------------

@bp.get("/articles/<pk>/versions")
def list_versions(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    if not KnowledgeRepository(conn).get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    versions = KnowledgeVersionRepository(conn).list_versions(target)
    return ok([_version_public(row) for row in versions])


@bp.get("/articles/<pk>/versions/<version>")
def get_version(pk: str, version: str) -> Response:
    conn = db()
    target = path_int(pk)
    snapshot = KnowledgeVersionRepository(conn).get_version(target, path_int(version))
    if not snapshot:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "版本不存在")
    return ok(_version_public(snapshot))


@bp.get("/articles/<pk>/diff")
def diff_versions(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    from_version = require_int(request.args.get("from"), "from")
    to_version = require_int(request.args.get("to"), "to")
    return ok(KnowledgeVersionRepository(conn).diff(target, from_version, to_version))


@bp.post("/articles/<pk>/versions/<version>/review")
def review_version(pk: str, version: str) -> Response:
    conn = db()
    target = path_int(pk)
    data = body()
    action = check_choice(data.get("action"), ("approve", "reject"), "action")
    if not action:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 action 不能为空")
    reviewer = optional_str(data.get("reviewer"), "reviewer", 64)
    comment = optional_str(data.get("comment"), "comment", 2000)
    repo = KnowledgeVersionRepository(conn)
    if action == "approve":
        result = repo.apply_version(target, path_int(version), reviewer=reviewer, review_comment=comment)
        commit(conn)
        _sync_file(conn, target)
        # 被合并条目也要落一份 merged 状态的 .md，否则重建索引会把它恢复成正式条目
        for merged_id in result.get("mergedArticleIds") or []:
            _sync_file(conn, int(merged_id))
        commit(conn)
    else:
        result = repo.reject_version(target, path_int(version), reviewer=reviewer, review_comment=comment)
        commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_REVIEW, audit_service.RESOURCE_KNOWLEDGE_VERSION,
          result.get("id"), "", {"action": action, "articleId": target, "version": path_int(version)})
    return ok(_version_public(result))


# ---------------- 引用 ----------------

@bp.get("/articles/<pk>/refs")
def list_refs(pk: str) -> Response:
    conn = db()
    target = path_int(pk)
    if not KnowledgeRepository(conn).get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    return ok(KnowledgeRefRepository(conn).list_refs(target))


# ---------------- 检索 ----------------

@bp.get("/search")
def search_articles() -> Response:
    conn = db()
    keyword = require_str(request.args.get("keyword"), "keyword", 200)
    page_num, page_size = page_args()
    filters = query_filters(ARTICLE_FILTERS)
    result = KnowledgeRepository(conn).search(keyword, filters, page_num=page_num, page_size=page_size)
    result["list"] = [_article_public(row) for row in result.get("list", [])]
    return ok(result)


@bp.get("/tags")
def knowledge_tags() -> Response:
    conn = db()
    return ok(KnowledgeRepository(conn).top_tags(30))


# ---------------- 存储与索引 ----------------

def _storage_payload(conn: Any) -> dict[str, Any]:
    root = knowledge_storage.storage_root(conn)
    file_count = len(list(root.glob("*.md"))) if root.exists() else 0
    return {
        "storagePath": str(root),
        "fileMode": knowledge_storage.file_mode(conn),
        "autoReindex": knowledge_storage.auto_reindex_enabled(conn),
        "assistantDefaultMode": knowledge_storage.assistant_default_mode(conn),
        "articleCount": KnowledgeRepository(conn).published_count(),
        "fileCount": file_count,
        "exists": root.exists(),
    }


@bp.get("/storage")
def storage_info() -> Response:
    return ok(_storage_payload(db()))


@bp.put("/storage")
def update_storage() -> Response:
    """修改知识库存放目录与写入模式；只改配置，不搬动文件。"""
    conn = db()
    data = body()
    config_repo = SysConfigRepository(conn)
    mode = optional_str(data.get("fileMode"), "fileMode", 20)
    if mode:
        if mode not in knowledge_storage.FILE_MODES:
            raise AppError(ErrorCode.PARAM_ERROR, "fileMode 只能是 mirror 或 db_only")
        config_repo.set_value(knowledge_storage.CONFIG_MODE, mode, "string", "知识库文件写入模式")
    if "autoReindex" in data:
        flag = "true" if bool_arg(data.get("autoReindex")) else "false"
        config_repo.set_value(knowledge_storage.CONFIG_AUTO_REINDEX, flag, "bool", "启动时自动重建知识库索引")
    assistant_mode = optional_str(data.get("assistantDefaultMode"), "assistantDefaultMode", 20)
    if assistant_mode:
        if assistant_mode not in knowledge_storage.ASSISTANT_MODES:
            raise AppError(ErrorCode.PARAM_ERROR, "assistantDefaultMode 只能是 kb、llm 或 hybrid")
        config_repo.set_value(
            knowledge_storage.CONFIG_ASSISTANT_MODE,
            assistant_mode,
            "string",
            "知识库问答助手默认回答模式",
        )
    path = optional_str(data.get("storagePath"), "storagePath", 400)
    if path:
        knowledge_storage.set_storage_path(conn, path)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, None,
          path or "", {"fileMode": mode, "assistantDefaultMode": assistant_mode})
    return ok(_storage_payload(conn))


@bp.post("/storage/migrate")
def migrate_storage() -> Response:
    """把落盘文件迁移到新目录，并以新目录重建索引。"""
    conn = db()
    data = body()
    target = require_str(data.get("targetPath"), "targetPath", 400)
    result = knowledge_storage.migrate(conn, target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, None, target,
          {"copied": result.get("copied")})
    return ok(result)


@bp.post("/reindex")
def reindex_knowledge() -> Response:
    """扫描存放目录，以文件为准回填数据库并重建全文索引。"""
    conn = db()
    result = knowledge_storage.reindex(conn)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, None, "",
          {"scanned": result.get("scanned")})
    return ok(result)
