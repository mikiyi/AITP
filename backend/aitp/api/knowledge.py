"""知识库 API：待审池 + 正式库 + 版本 + 引用保护。"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.enums import KNOWLEDGE_CATEGORY
from ..common.validators import check_choice, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.knowledge_draft_repo import (
    DRAFT_STATUS_PENDING,
    KnowledgeDraftRepository,
)
from ..repositories.knowledge_ref_repo import KnowledgeRefRepository
from ..repositories.knowledge_repo import KnowledgeRepository
from ..repositories.knowledge_version_repo import KnowledgeVersionRepository
from ..services import audit_service, knowledge_storage, tabular_export
from ..repositories.sys_config_repo import SysConfigRepository
from .helpers import (audit, body, bool_arg, commit, db, download_response, keyword_of, page_args,
                      page_payload, query_filters, tabular_format_of)

bp = Blueprint("knowledge", __name__)

ARTICLE_FILTERS = {"category": "category", "sourceType": "source_type", "moduleId": "module_id"}
DRAFT_FILTERS = {"reviewStatus": "review_status", "sourceType": "source_type", "moduleId": "module_id"}
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
    row = KnowledgeRepository(conn).get(article_id)
    if not row:
        return ""
    try:
        relative = knowledge_storage.write_article(conn, row)
    except OSError:
        return ""
    if relative:
        KnowledgeRepository(conn).update(article_id, {"file_path": relative})
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
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "sessionId": row.get("session_id"),
        "aiTaskId": row.get("ai_task_id"),
        "refType": row.get("ref_type"),
        "refId": row.get("ref_id"),
        "viewCount": row.get("view_count"),
        "reviewStatus": row.get("review_status"),
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
        "refType": row.get("ref_type"),
        "refId": row.get("ref_id"),
        "refLabel": row.get("ref_label"),
        "producer": row.get("producer"),
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
    category = check_choice(data.get("category"), KNOWLEDGE_CATEGORY, "category", "general") or "general"
    source_type = optional_str(data.get("sourceType"), "sourceType", 40) or "ai_output"
    repo = KnowledgeDraftRepository(conn)
    new_id = repo.create_draft(
        title,
        content,
        summary=optional_str(data.get("summary"), "summary", 2000),
        category=category,
        tags=optional_str(data.get("tags"), "tags", 500),
        module_id=data.get("moduleId"),
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
    target = int(pk)
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
    for key in ("summary", "category", "tags"):
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
    target = int(pk)
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
    # 正式库默认只出已发布；reviewStatus=all 才返回全部（含已撤销）
    filters["review_status"] = "published" if not status or status == "all" else status
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
    filters["review_status"] = "published" if not status or status == "all" else status
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
    row = repo.get(int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    repo.increment_views(int(pk))
    commit(conn)
    payload = _article_public(repo.get(int(pk)) or {})
    payload["pendingVersions"] = [
        _version_public(row) for row in KnowledgeVersionRepository(conn).pending_versions(int(pk))
    ]
    payload["refs"] = KnowledgeRefRepository(conn).list_refs(int(pk))
    file_info = knowledge_storage.file_status(conn, repo.get(int(pk)) or {})
    payload["fileChanged"] = bool(file_info.get("changed"))
    payload["fileExists"] = bool(file_info.get("exists"))
    payload["fileMode"] = file_info.get("mode")
    return ok(payload)


@bp.put("/articles/<pk>")
def update_article(pk: str) -> Response:
    """编辑已发布条目 → 产生待审新版本，当前正文与索引保持不变。"""
    conn = db()
    repo = KnowledgeRepository(conn)
    target = int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    data = body()
    title = require_str(data.get("title"), "title", 500)
    content = require_str(data.get("content"), "content", 500000)
    version_id = KnowledgeVersionRepository(conn).create_version(
        target,
        title=title,
        content=content,
        summary=optional_str(data.get("summary"), "summary", 2000),
        category=optional_str(data.get("category"), "category", 100),
        tags=optional_str(data.get("tags"), "tags", 500),
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
    target = int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    repo.delete_article(target)
    commit(conn)
    try:
        knowledge_storage.remove_article_file(conn, str(row.get("file_path") or ""))
    except OSError:
        pass
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, target, "")
    return ok({"id": target, "deleted": True})


@bp.post("/articles/<pk>/revoke")
def revoke_article(pk: str) -> Response:
    conn = db()
    repo = KnowledgeRepository(conn)
    target = int(pk)
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
    target = int(pk)
    result = repo.restore(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, target, "",
          {"restored": True})
    return ok(result)


# ---------------- 版本 ----------------

@bp.get("/articles/<pk>/versions")
def list_versions(pk: str) -> Response:
    conn = db()
    target = int(pk)
    if not KnowledgeRepository(conn).get(target):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "知识条目不存在")
    versions = KnowledgeVersionRepository(conn).list_versions(target)
    return ok([_version_public(row) for row in versions])


@bp.get("/articles/<pk>/versions/<version>")
def get_version(pk: str, version: str) -> Response:
    conn = db()
    target = int(pk)
    snapshot = KnowledgeVersionRepository(conn).get_version(target, int(version))
    if not snapshot:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "版本不存在")
    return ok(_version_public(snapshot))


@bp.get("/articles/<pk>/diff")
def diff_versions(pk: str) -> Response:
    conn = db()
    target = int(pk)
    from_version = require_int(request.args.get("from"), "from")
    to_version = require_int(request.args.get("to"), "to")
    return ok(KnowledgeVersionRepository(conn).diff(target, from_version, to_version))


@bp.post("/articles/<pk>/versions/<version>/review")
def review_version(pk: str, version: str) -> Response:
    conn = db()
    target = int(pk)
    data = body()
    action = check_choice(data.get("action"), ("approve", "reject"), "action")
    if not action:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 action 不能为空")
    reviewer = optional_str(data.get("reviewer"), "reviewer", 64)
    comment = optional_str(data.get("comment"), "comment", 2000)
    repo = KnowledgeVersionRepository(conn)
    if action == "approve":
        result = repo.apply_version(target, int(version), reviewer=reviewer, review_comment=comment)
        commit(conn)
        _sync_file(conn, target)
        commit(conn)
    else:
        result = repo.reject_version(target, int(version), reviewer=reviewer, review_comment=comment)
        commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_REVIEW, audit_service.RESOURCE_KNOWLEDGE_VERSION,
          result.get("id"), "", {"action": action, "articleId": target, "version": int(version)})
    return ok(_version_public(result))


# ---------------- 引用 ----------------

@bp.get("/articles/<pk>/refs")
def list_refs(pk: str) -> Response:
    conn = db()
    target = int(pk)
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
    path = optional_str(data.get("storagePath"), "storagePath", 400)
    if path:
        knowledge_storage.set_storage_path(conn, path)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_KNOWLEDGE_ARTICLE, None,
          path or "", {"fileMode": mode})
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
