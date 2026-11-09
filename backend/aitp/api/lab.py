"""AI 实验室 API：能力执行、流式输出、智能路由、会话与任务。

统一契约：
- POST /lab/{capability}/run      同步执行，返回产出物与用量
- POST /lab/{capability}/stream   发起流式执行，立即返回任务号（帧改由轮询取，GAP-26）
- GET  /lab/stream/{taskId}/frames 增量取流式帧
- POST /lab/route                 自由文本 → 能力候选 + 功能点候选
"""

from __future__ import annotations

import logging
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, cast

from flask import Blueprint, Flask, Response, current_app, request

from ..ai import cost as ai_cost
from ..ai.capability_codes import (
    LAB_CAPABILITIES,
    LAB_EXTENDED_CAPABILITIES,
    OUTPUT_JSON,
    label_of,
)
from ..ai.errors import AIError
from ..ai.safety import mark_review_required, needs_human_review
from ..ai.types import ResolvedAIConfig
from ..common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY
from ..common.validators import optional_str, require_int, require_str
from ..db import write_guard
from ..envelope import current_trace_id, ok
from ..errors import AppError, ErrorCode
from ..logging_setup import log_event
from ..prompts.registry import list_templates
from ..repositories.ai_session_repo import AISessionRepository, AITaskRepository
from ..repositories.ai_profile_repo import AIProfileRepository
from ..repositories.bug_repo import BugRepository
from ..repositories.project_repo import ProjectRepository
from ..repositories.requirement_repo import RequirementRepository
from ..repositories.sql_analysis_repo import SqlAnalysisRepository
from ..repositories.base import dumps, loads
from ..repositories.doc_template_repo import (
    CAPABILITY_KIND,
    DocTemplateRepository,
    normalize_columns,
)
from ..services import (
    ai_record_service,
    attachment_service,
    audit_service,
    composite_service,
    doc_template_service,
    lab_service,
    nav_badge_service,
    regression_service,
    output_exporter,
    stream_broker,
    tabular_export,
    task_control,
)
from ..services import preflight_service
from .helpers import (
    audit, body, check_ref_project, commit, db, download_response, page_args, page_payload,
    path_int, project_of_module, scope_arg,
)
from .sse import (
    FRAME_DELTA,
    FRAME_DEGRADE,
    FRAME_DONE,
    FRAME_ERROR,
    FRAME_MASK,
    FRAME_REASONING,
    FRAME_START,
    FRAME_USAGE,
    SeqCounter,
)

bp = Blueprint("lab", __name__)

logger = logging.getLogger(__name__)

#: XMind 文件 MIME；本质是 zip，部分环境也接受 application/octet-stream
XMIND_MIME = "application/vnd.xmind"

#: 请求体里不是提示词变量的字段
#: （contextTier / projectId 是 GAP-27 的装配参数，dedupMode 是 GAP-29 的去重口径）
META_FIELDS = {
    "variables", "sessionId", "profileId", "moduleId", "title", "attachmentIds",
    "templateId", "contextTier", "projectId", "dedupMode",
}


# ---------------- 参数解析 ----------------

def _optional_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    return require_int(value, field)


def _context_tier_of(data: dict[str, Any]) -> str | None:
    """GAP-27 本次请求要求的上下文档位；非法值不在这里报错，由装配器回落默认档。"""
    raw = data.get("contextTier")
    if raw in (None, ""):
        return None
    return require_str(raw, "contextTier", 20)


def _dedup_mode_of(data: dict[str, Any]) -> str | None:
    """GAP-29 本次请求的去重口径；非法值由服务层回落 `mark`，不在入口报错。"""
    raw = data.get("dedupMode")
    if raw in (None, ""):
        return None
    return require_str(raw, "dedupMode", 20)


def _variables_of(data: dict[str, Any]) -> dict[str, Any]:
    """取出提示词变量；未给 variables 时兼容顶层平铺写法。"""
    raw = data.get("variables")
    if raw is None:
        raw = {k: v for k, v in data.items() if k not in META_FIELDS}
    if not isinstance(raw, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 variables 必须是对象")
    result: dict[str, Any] = {}
    for key, value in raw.items():
        if value is None:
            result[str(key)] = ""
        elif isinstance(value, (dict, list)):
            result[str(key)] = dumps(value)
        else:
            result[str(key)] = value
    return result


def _ensure_session(
    conn: sqlite3.Connection, session_id: int | None, title: str, module: str
) -> int:
    """没有会话就建一个；会话落在库里，重启后仍在。"""
    repo = AISessionRepository(conn)
    if session_id is not None:
        if repo.get(session_id):
            return session_id
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "会话不存在")
    return repo.insert({"title": title or module, "module": module})


def _start_task(
    conn: sqlite3.Connection,
    capability: str,
    session_id: int | None,
    profile_id: int | None,
    module_id: int | None,
    variables: dict[str, Any],
    project_id: int | None = None,
) -> int:
    payload: dict[str, Any] = {
        "module_id": module_id,
        "capability": capability,
        "request_json": dumps(variables),
    }
    # 任务归属项目：优先请求里的 projectId，其次由功能点反推；推不出来就留空，
    # 宁可项目视图少一条，也不要把别的项目的任务算进来。
    resolved = project_id if project_id is not None else project_of_module(conn, module_id)
    if resolved is not None:
        payload["project_id"] = resolved
    if session_id is not None:
        payload["session_id"] = session_id
    if profile_id is not None:
        payload["profile_id"] = profile_id
    return AITaskRepository(conn).start(capability, **payload)


def _finish_task(
    db_path: Path,
    task_id: int,
    status: str,
    result: dict[str, Any] | None = None,
    error_code: int | None = None,
    error_message: str | None = None,
) -> None:
    """用独立连接写任务终态。

    流式路径上请求连接可能已被关闭，不能依赖它落库。
    """
    from ..db.connection import get_standalone_conn

    conn = get_standalone_conn(db_path)
    try:
        AITaskRepository(conn).finish(task_id, status, result, error_code, error_message)
        conn.commit()
    # 兜住的范围：写库（含进程内写锁排队超时）、文件/句柄、结果 JSON 序列化失败。
    # 这里不兜 Exception，因为任务终态是审计入口，真正的编程错误要冒出来；
    # 但请求已经返回，再抛只会让调用方（流式生产者线程）看到无意义的栈。
    except (sqlite3.Error, OSError, ValueError) as exc:  # pragma: no cover
        log_event(logger, "task_finish_failed", level=logging.WARNING,
                  traceId=write_guard.log_trace_id(), taskId=task_id, status=status,
                  error=str(exc))
    finally:
        conn.close()


def _save_assistant_message(
    conn: sqlite3.Connection,
    session_id: int,
    content: str,
    *,
    prompt_code: str,
    model: str,
    usage: dict[str, Any],
    reasoning: str = "",
) -> None:
    AISessionRepository(conn).add_message(
        session_id,
        "assistant",
        content,
        reasoning=reasoning,
        prompt_code=prompt_code,
        model=model,
        tokens_prompt=int(usage.get("promptTokens") or 0),
        tokens_completion=int(usage.get("completionTokens") or 0),
    )
    # 会话列表的「模型」列取会话上的 model；模型档案会在一次会话里换，按最近一次回答留痕。
    if model:
        AISessionRepository(conn).update(session_id, {"model": model})


def _write_record(
    conn: sqlite3.Connection,
    *,
    capability: str,
    output: str,
    variables: dict[str, Any],
    refs: list[dict[str, Any]],
    review_required: bool,
    model: str,
    task_id: int,
    session_id: int,
    prompt_code: str,
    usage: dict[str, Any] | None = None,
    latency_ms: int | None = None,
    route: list[dict[str, Any]] | None = None,
    mask: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    schema: dict[str, Any] | None = None,
    dedup: dict[str, Any] | None = None,
    cost: dict[str, Any] | None = None,
) -> int:
    """把这次执行的结构化结论落库：证据、缺项、风险点 + 主线 A 的四份结论。

    `context/schema/dedup/cost` 对应 GAP-27/28/29/30。缺了它们，历史记录就只能回答
    「产出了什么」，回答不了「当时模型看到什么、字段是否合规、有没有撞车、花了多少钱」，
    而且这三列是空串时前端历史面板只能整体沉默。
    """
    record = ai_record_service.build_record(
        conn,
        capability=capability,
        output=output,
        variables=variables,
        refs=refs,
        review_required=review_required,
        model=model,
        task_id=task_id,
        session_id=session_id,
        optional_names=lab_service.optional_variables_of(prompt_code),
        route=route or [],
        usage=usage or {},
        prompt_code=prompt_code,
        latency_ms=latency_ms,
        mask=mask,
        context=context,
        schema=schema,
        dedup=dedup,
        cost=cost,
    )
    return ai_record_service.create_record(conn, record)


def _write_record_standalone(db_path: Path, **kwargs: Any) -> int:
    """流式路径上用独立连接落库，避免依赖可能已关闭的请求连接。"""
    from ..db.connection import get_standalone_conn

    conn = get_standalone_conn(db_path)
    try:
        record_id = _write_record(conn, **kwargs)
        conn.commit()
        return record_id
    # 宽泛兜底只保留在"产出物已送达、记录只是副产物"这一条路径上：
    # 落库失败（SQL 约束/文件读写/JSON 序列化）不能反过来污染已经返回的响应，
    # 因此这里兜住 sqlite3.Error / OSError / ValueError 三类，并留痕而非静默 pass。
    except (sqlite3.Error, OSError, ValueError) as exc:  # pragma: no cover
        log_event(logger, "stream_record_failed", level=logging.WARNING,
                  traceId=write_guard.log_trace_id(), taskId=kwargs.get("task_id"),
                  capability=kwargs.get("capability"), error=str(exc))
        return 0
    finally:
        conn.close()


# ---------------- 目录 ----------------

@bp.get("/capabilities")
def list_capabilities() -> Response:
    return ok(lab_service.capability_cards())


@bp.get("/prompts")
def list_prompts() -> Response:
    """提示词列表：带每个提示词的使用次数，供提示词库按使用频次排序。

    两份来源相加：能力类调用看 biz_ai_record，
    智能路由/问答助手/自动整理这类幕后调用看 sys_prompt_usage。
    """
    from ..repositories.ai_record_repo import AIRecordRepository
    from ..services import prompt_usage

    conn = db()
    counts = dict(AIRecordRepository(conn).count_by_prompt_code())
    for code, extra in prompt_usage.counts(conn).items():
        counts[code] = counts.get(code, 0) + extra
    result = []
    for item in list_templates():
        item = dict(item)
        item["useCount"] = counts.get(item["code"], 0)
        result.append(item)
    return ok(result)


@bp.get("/prompts/<code>")
def get_prompt(code: str) -> Response:
    """单个提示词模板：含正文，供提示词库页面展示。"""
    from ..prompts.registry import get_registry

    registry = get_registry()
    template = registry.get(code)
    payload = template.as_dict()
    payload["body"] = template.body
    payload["defaultBody"] = registry.default_template(code).body
    return ok(payload)


@bp.put("/prompts/<code>")
def save_prompt(code: str) -> Response:
    """保存自定义正文：正文存库、覆盖磁盘默认；重置就是删掉这一行。

    只允许用磁盘模板已有的变量：正文里冒出模板没提供的 `{{新变量}}`，
    真正调用时会因缺变量报 120102，所以在保存这一步就挡掉。
    """
    from ..prompts.registry import get_registry, variables_of
    from ..repositories.prompt_override_repo import MAX_BODY_LENGTH, PromptOverrideRepository

    registry = get_registry()
    registry.get(code)
    text = optional_str(body().get("body"), "body", MAX_BODY_LENGTH)
    if not text:
        raise AppError(ErrorCode.PARAM_ERROR, "提示词正文不能为空")

    known = set(registry.default_template(code).variables)
    unknown = [name for name in variables_of(text) if name not in known]
    if unknown:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"正文里出现了模板没有的变量：{'、'.join(unknown)}；请删掉或改成固定文案",
            {"unknown": unknown},
        )

    conn = db()
    PromptOverrideRepository(conn).save(code, text)
    commit(conn)
    registry.invalidate(code)
    template = registry.get(code)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_AI_PROMPT,
          None, code, {"customized": True, "length": len(text)})
    payload = template.as_dict()
    payload["body"] = template.body
    return ok(payload)


@bp.delete("/prompts/<code>")
def reset_prompt(code: str) -> Response:
    """一键重置：删除自定义正文，回到 backend/prompts 里的默认模板。"""
    from ..prompts.registry import get_registry
    from ..repositories.prompt_override_repo import PromptOverrideRepository

    registry = get_registry()
    registry.get(code)
    conn = db()
    removed = PromptOverrideRepository(conn).remove(code)
    commit(conn)
    registry.invalidate(code)
    template = registry.get(code)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_AI_PROMPT,
          None, code, {"customized": False, "removed": removed})
    payload = template.as_dict()
    payload["body"] = template.body
    return ok(payload)


@bp.get("/main-capabilities")
def list_lab_capabilities() -> Response:
    """AI 实验室首页展示的能力卡片：主能力在前，提示词库补充能力在后。"""
    return ok(lab_service.capability_cards(
        LAB_CAPABILITIES + LAB_EXTENDED_CAPABILITIES
    ))


# ---------------- 附件：文档导入与图片去水印 ----------------

def _attachments_root() -> Path:
    """附件目录与数据库同放在 data 目录下。"""
    db_path = Path(current_app.config["AITP_DB_PATH"])
    return db_path.parent / "attachments"


def _uploaded_file() -> tuple[str, bytes] | None:
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        return None
    raw = uploaded.read()
    if not raw:
        raise AppError(ErrorCode.PARAM_ERROR, "上传的文件是空的")
    return attachment_service.safe_file_name(uploaded.filename), raw


def _resolve_images(attachment_ids: list[str]) -> list[str]:
    """把附件编号解析成 data URL；只取已去水印的图片。"""
    images: list[str] = []
    for item in attachment_ids:
        meta = attachment_service.load_meta(_attachments_root(), item)
        if not meta or meta.get("kind") != "image":
            continue
        suffix = str(meta.get("suffix") or "")
        data = attachment_service.load_attachment(_attachments_root(), item, suffix)
        if not data:
            continue
        images.append(attachment_service.to_data_url(str(meta.get("mediaType") or "image/png"), data))
    return images


def _attachment_ids_of(data: dict[str, Any]) -> list[str]:
    raw = data.get("attachmentIds")
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 attachmentIds 必须是数组")
    return [str(item) for item in raw if str(item).strip()]


@bp.post("/attachments/document")
def upload_document() -> Response:
    """导入文档并抽取文本：Word / Excel / Markdown / PDF / HTML / JSON / TXT。"""
    uploaded = _uploaded_file()
    if uploaded is None:
        raise AppError(ErrorCode.PARAM_ERROR, "请上传文档文件")
    file_name, raw = uploaded
    if not attachment_service.is_document(file_name):
        raise AppError(ErrorCode.PARAM_ERROR, f"不支持的文档格式：{attachment_service.extension_of(file_name) or '未知'}")
    try:
        extracted = attachment_service.extract_document(file_name, raw)
    except attachment_service.AttachmentError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, str(exc)) from None

    attachment_id, _ = attachment_service.save_attachment(
        _attachments_root(), file_name, extracted.text.encode("utf-8"), suffix=".txt"
    )
    attachment_service.save_meta(_attachments_root(), attachment_id, {
        "kind": "document",
        "fileName": file_name,
        "suffix": ".txt",
        "mediaType": "text/plain",
        "docKind": extracted.kind,
        "chars": extracted.chars,
        "truncated": extracted.truncated,
    })
    payload = extracted.as_dict()
    payload["attachmentId"] = attachment_id
    audit(db(), audit_service.AUDIT_ACTION_IMPORT, audit_service.RESOURCE_LAB_ATTACHMENT,
          None, file_name, {"kind": extracted.kind, "chars": extracted.chars})
    return ok(payload)


@bp.post("/attachments/image")
def upload_image() -> Response:
    """贴入或上传图片：先去水印，再存下来供后续流程使用。

    支持上传文件（multipart）与粘贴（data URL）两种入口。
    """
    uploaded = _uploaded_file()
    if uploaded is not None:
        file_name, raw = uploaded
    else:
        data = body()
        data_url = str(data.get("dataUrl") or "")
        if not data_url:
            raise AppError(ErrorCode.PARAM_ERROR, "请上传图片或粘贴图片")
        try:
            file_name, raw = attachment_service.decode_data_url(data_url)
        except attachment_service.AttachmentError as exc:
            raise AppError(ErrorCode.PARAM_ERROR, str(exc)) from None

    if not attachment_service.is_image(file_name):
        raise AppError(ErrorCode.PARAM_ERROR, f"不支持的图片格式：{attachment_service.extension_of(file_name) or '未知'}")

    processed = attachment_service.process_image(file_name, raw)
    suffix = attachment_service.suffix_of_media_type(processed.media_type)
    attachment_id, _ = attachment_service.save_attachment(
        _attachments_root(), file_name, processed.data, suffix=suffix
    )
    attachment_service.save_meta(_attachments_root(), attachment_id, {
        "kind": "image",
        "fileName": file_name,
        "suffix": suffix,
        "mediaType": processed.media_type,
        "width": processed.width,
        "height": processed.height,
        "watermarkApplied": processed.watermark_applied,
        "regions": processed.regions,
        "note": processed.note,
    })
    payload = processed.as_dict()
    payload["attachmentId"] = attachment_id
    payload["previewUrl"] = f"/api/v1/lab/attachments/{attachment_id}"
    audit(db(), audit_service.AUDIT_ACTION_IMPORT, audit_service.RESOURCE_LAB_ATTACHMENT,
          None, file_name, {
              "mediaType": processed.media_type,
              "watermarkApplied": processed.watermark_applied,
              "size": len(processed.data),
          })
    return ok(payload)


@bp.get("/attachments/<attachment_id>")
def get_attachment(attachment_id: str) -> Response:
    """取回处理后的附件内容（图片预览）。"""
    meta = attachment_service.load_meta(_attachments_root(), attachment_id)
    if not meta:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "附件不存在")
    suffix = str(meta.get("suffix") or "")
    data = attachment_service.load_attachment(_attachments_root(), attachment_id, suffix)
    if not data:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "附件内容已丢失")
    response = Response(data, mimetype=str(meta.get("mediaType") or "application/octet-stream"))
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    if response.mimetype == "image/svg+xml":
        # SVG 会被浏览器当文档执行，同源脚本就能读写本地接口；落盘前已剥过脚本，
        # 这里再给一份只读 CSP 兜住历史遗留的附件。
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; style-src 'unsafe-inline'; img-src 'self' data:; sandbox"
        )
    return response


@bp.get("/attachments/<attachment_id>/payload")
def get_attachment_payload(attachment_id: str) -> Response:
    """取图片的 data URL，供多模态模型直接读取。"""
    meta = attachment_service.load_meta(_attachments_root(), attachment_id)
    if not meta or meta.get("kind") != "image":
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "图片附件不存在")
    suffix = str(meta.get("suffix") or "")
    data = attachment_service.load_attachment(_attachments_root(), attachment_id, suffix)
    if not data:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "附件内容已丢失")
    return ok({
        "attachmentId": attachment_id,
        "mediaType": meta.get("mediaType"),
        "dataUrl": attachment_service.to_data_url(str(meta.get("mediaType") or "image/png"), data),
    })


@bp.delete("/attachments/<attachment_id>")
def remove_attachment(attachment_id: str) -> Response:
    conn = db()
    removed = attachment_service.delete_attachment(_attachments_root(), attachment_id)
    if not removed:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "附件不存在")
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, "lab_attachment", None, attachment_id, {})
    return ok({"attachmentId": attachment_id, "deleted": True})


# ---------------- 执行 ----------------

def _apply_output_template(
    conn: sqlite3.Connection,
    capability: str,
    data: dict[str, Any],
    variables: dict[str, Any],
) -> None:
    """选了产出物模板时，把模板正文渲染成 outputTemplate 变量带给模型。"""
    template_id = _optional_int(data.get("templateId"), "templateId")
    if template_id is None:
        return
    variables["outputTemplate"] = doc_template_service.resolve_for_run(conn, template_id, capability)


_SQL_FENCE_RE = re.compile(
    "```(?:sql)?" + chr(92) + "s*" + chr(10) + "(.*?)```",
    re.IGNORECASE | re.DOTALL,
)


def _extract_sql_text(output: str) -> str:
    """从产出里取第一段代码块作为被分析的 SQL；没有代码块时留空，正文仍在分析结论里。"""
    match = _SQL_FENCE_RE.search(output or "")
    return match.group(1).strip() if match else ""


@bp.post("/output/adopt")
def adopt_capability_output() -> Response:
    """把产出物一键添加到需求 / 缺陷 / 回归清单，并点亮对应导航角标。"""
    conn = db()
    data = body()
    capability = require_str(data.get("capability"), "capability", 60)
    output = require_str(data.get("output"), "output", 200000)
    target = str(data.get("target") or "").strip()
    project_id = require_int(data.get("projectId"), "projectId")
    if not ProjectRepository(conn).get(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    title = optional_str(data.get("title"), "title", 200) or f"{label_of(capability)} 产出"
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    version_id = _optional_int(data.get("versionId"), "versionId")
    # 采纳落库前先对齐项目：脏的功能点/版本引用会直接污染对方项目的树、覆盖矩阵和 assetCount
    check_ref_project(conn, "biz_module", module_id, project_id, "功能点", "moduleId")
    version_id = check_ref_project(
        conn, "biz_project_version", version_id, project_id, "项目版本", "versionId",
    )

    if target == "requirement":
        req_repo = RequirementRepository(conn)
        new_id = req_repo.insert({
            "project_id": project_id,
            "req_no": req_repo.next_req_no(project_id),
            "title": title,
            "content": output,
            "source": "ai",
            "version": "",
            "status": "draft",
            "priority": "P2",
            "tags": "",
            "version_id": version_id,
        })
        section = "requirements"
        resource = audit_service.RESOURCE_REQUIREMENT
    elif target == "bug":
        bug_repo = BugRepository(conn)
        new_id = bug_repo.insert({
            "project_id": project_id,
            "bug_no": bug_repo.next_bug_no(project_id),
            "title": title,
            "severity": "P2",
            "status": "open",
            "priority": "P2",
            "bug_type": "functional",
            "description": output,
            "steps_json": dumps([]),
            "expected_result": "",
            "actual_result": "",
            "assignee": "",
            "reporter": "AI 实验室",
            "version": "",
            "env": "",
            "module": "",
            "module_id": module_id,
            "version_id": version_id,
        })
        section = "bugs"
        resource = audit_service.RESOURCE_BUG
    elif target == "sql":
        sql_repo = SqlAnalysisRepository(conn)
        new_id = sql_repo.insert({
            "project_id": project_id,
            "module_id": module_id,
            "version_id": version_id,
            "sql_no": sql_repo.next_sql_no(),
            "title": title,
            "sql_text": _extract_sql_text(output),
            "analysis": output,
            "risk_level": "none",
            "source": "ai",
            "tags": "",
        })
        section = "sql_analyses"
        resource = audit_service.RESOURCE_SQL_ANALYSIS
    elif target == "regression":
        new_id = int(regression_service.create_regression(
            conn,
            project_id=project_id,
            title=title,
            change_scope=label_of(capability),
            affected_modules="",
            version="",
            version_id=version_id,
            risk_level="low",
            content=output,
            source="ai",
        ))
        section = "regressions"
        resource = audit_service.RESOURCE_REGRESSION
    else:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "target 只能是 requirement / bug / regression / sql",
        )

    nav_badge_service.increase_for(conn, section)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, resource, new_id, title,
          {"capability": capability, "target": target})
    return ok({"target": target, "id": new_id, "title": title})


@bp.post("/output/export")
def export_capability_output() -> Response:
    """把能力产出转成 Excel / Word：用例 JSON → 单表，Markdown → 分节表格。

    产出原文由前端回传，后端不缓存中间文件；列顺序取模板里的 columns。
    """
    from datetime import datetime

    conn = db()
    data = body()
    capability = require_str(data.get("capability"), "capability", 60)
    output = optional_str(data.get("output"), "output", output_exporter.MAX_OUTPUT_CHARS)
    if not output.strip():
        raise AppError(ErrorCode.PARAM_ERROR, "暂无产出，无法导出")
    fmt = str(data.get("format") or "xlsx").strip().lower()
    if fmt not in output_exporter.EXPORT_FORMATS:
        raise AppError(ErrorCode.PARAM_ERROR, "format 只能是 xlsx / docx")
    label = label_of(capability)

    columns: list[str] = []
    template_id = _optional_int(data.get("templateId"), "templateId")
    if template_id is not None:
        row = DocTemplateRepository(conn).get(template_id)
        if not row:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "产出物模板不存在")
        if str(row.get("kind") or "") != CAPABILITY_KIND.get(capability, ""):
            raise AppError(ErrorCode.PARAM_ERROR, "模板类别与当前能力不匹配")
        columns = normalize_columns(loads(row.get("columns_json"), []))

    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    if fmt == "docx":
        content, count = output_exporter.export_docx(
            capability, output, columns=columns, title=label,
        )
        filename = tabular_export.safe_filename(f"{label}产出-{stamp}", "docx")
        return download_response(content, filename, output_exporter.DOCX_MIME, count)

    content, count = output_exporter.export_output(
        capability,
        output,
        columns=columns,
        sheet_name=label,
    )
    filename = tabular_export.safe_filename(f"{label}产出-{stamp}", "xlsx")
    return download_response(content, filename, output_exporter.XLSX_MIME, count)


@bp.post("/<capability>/run")
def run_capability(capability: str) -> Response:
    conn = db()
    data = body()
    variables = _variables_of(data)
    _apply_output_template(conn, capability, data, variables)
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    profile_id = _optional_int(data.get("profileId"), "profileId")
    session_id = _optional_int(data.get("sessionId"), "sessionId")

    spec = lab_service.require_capability(capability)
    images = _resolve_images(_attachment_ids_of(data))
    project_id = _optional_int(data.get("projectId"), "projectId")
    # 先校验（缺变量 / 功能点未启用 / 没有可用模型），失败时不落任务
    plan = lab_service.prepare_run(
        conn, capability, variables, module_id=module_id, profile_id=profile_id,
        images=images, context_tier=_context_tier_of(data), project_id=project_id,
        dedup_mode=_dedup_mode_of(data),
    )

    session_id = _ensure_session(
        conn, session_id, require_str(data.get("title"), "title", 200) if data.get("title") else spec.label,
        capability,
    )
    task_id = _start_task(conn, capability, session_id, profile_id, module_id, variables,
                      project_id=project_id)
    commit(conn)

    try:
        result = lab_service.execute_run(plan, conn=conn)
    # 这里必须兜住「一切失败」：模型调用可能抛 AIError、HTTP 层抛 requests/networkx 系异常、
    # 落库抛 sqlite3.Error，逐类列举容易漏，漏了就会留下永远停在 running 的任务行。
    # 兜住之后原样 raise，异常类型与栈不变，只是先把终态写进库；
    # 最终仍由 middleware 的统一异常出口转成包封响应，不会被这里吞掉。
    except Exception as exc:  # noqa: BLE001 - 记录终态后重抛，不吞异常
        code = exc.code if isinstance(exc, AIError) else ErrorCode.INTERNAL_ERROR
        log_event(logger, "run_capability_failed", level=logging.WARNING,
                  traceId=current_trace_id(), taskId=task_id, capability=capability,
                  errorCode=int(code), error=str(exc)[:300])
        AITaskRepository(conn).finish(task_id, "failed", None, code, str(exc))
        commit(conn)
        raise

    payload = result.as_dict()
    payload["taskId"] = task_id
    payload["sessionId"] = session_id
    AITaskRepository(conn).finish(task_id, "success", {
        "output": result.output,
        "model": result.model,
        "usage": result.usage,
        "latencyMs": result.latency_ms,
    })
    _save_assistant_message(
        conn, session_id, result.output,
        prompt_code=result.prompt_code, model=result.model, usage=result.usage,
    )
    record_id = _write_record(
        conn,
        capability=capability,
        output=result.output,
        variables=variables,
        refs=result.refs,
        review_required=result.review_required,
        model=result.model,
        task_id=task_id,
        session_id=session_id,
        prompt_code=result.prompt_code,
        usage=result.usage,
        latency_ms=result.latency_ms,
        mask=result.mask,
        context=result.context,
        schema=result.schema,
        dedup=result.dedup,
        cost=result.cost,
    )
    commit(conn)
    payload["recordId"] = record_id
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_AI_TASK, task_id,
          spec.label, {"capability": capability, "moduleId": module_id, "latencyMs": result.latency_ms})
    return ok(payload)


@bp.post("/<capability>/stream")
def stream_capability(capability: str) -> Response:
    """发起流式执行：请求立刻返回任务号，产出不在这个线程上跑（GAP-26）。

    契约变化（只此一处）：响应不再是 SSE 长连接，而是普通包封
    `{taskId, sessionId, mode:"poll", framesUrl, intervalMs}`；
    帧本身一字未改，由 `GET /lab/stream/<taskId>/frames` 按 after 增量取走，
    前端的帧处理逻辑保持不变。
    """
    conn = db()
    data = body()
    variables = _variables_of(data)
    _apply_output_template(conn, capability, data, variables)
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    profile_id = _optional_int(data.get("profileId"), "profileId")
    session_id = _optional_int(data.get("sessionId"), "sessionId")

    spec = lab_service.require_capability(capability)
    images = _resolve_images(_attachment_ids_of(data))
    # 流式路径同样要吃 projectId：去重比对域与上下文装配都按项目收敛，
    # 漏传时库里一条也对不上，界面永远显示「没有重复」
    project_id = _optional_int(data.get("projectId"), "projectId")
    events, plan = lab_service.stream_capability(
        conn, capability, variables, module_id=module_id, profile_id=profile_id,
        images=images, project_id=project_id, dedup_mode=_dedup_mode_of(data),
    )

    session_id = _ensure_session(
        conn, session_id, require_str(data.get("title"), "title", 200) if data.get("title") else spec.label,
        capability,
    )
    task_id = _start_task(conn, capability, session_id, profile_id, module_id, variables,
                      project_id=project_id)
    commit(conn)

    db_path = Path(current_app.config["AITP_DB_PATH"])
    if stream_broker.active_streams() >= stream_broker.MAX_ACTIVE_STREAMS:
        # 先把刚建的任务判掉，否则它会一直挂在 running，列表显示「执行中」而实际没人跑
        _finish_task(db_path, task_id, "failed", None, ErrorCode.ILLEGAL_OPERATION,
                     "流式执行并发已满，请稍后重试")
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"同时进行的流式执行已达上限（{stream_broker.MAX_ACTIVE_STREAMS} 路），请稍后再试",
            {"activeStreams": stream_broker.active_streams(), "limit": stream_broker.MAX_ACTIVE_STREAMS},
        )

    control_key = f"lab:{task_id}"
    # 在发起请求这一刻就登记取消通道：等生产者线程再 register 会有窗口，
    # 用户点「停止」落在窗口里时 task_control.cancel 会找不到 key 而空转
    task_control.register(control_key)

    job = _StreamJob(
        capability=capability,
        label=spec.label,
        output_format=spec.output_format,
        task_id=task_id,
        session_id=int(session_id),
        control_key=control_key,
        db_path=db_path,
        variables=variables,
        refs=plan.refs,
        mask_enabled=plan.mask_enabled,
        prompt_code=plan.binding.prompt_code,
        module_path=plan.binding.module_path,
        egress=plan.candidates[0].egress if plan.candidates else EGRESS_NONE,
        plan=plan,
        events=events,
        trace_id=current_trace_id(),
    )
    job.egress_warning = lab_service.EGRESS_WARNING if job.egress == EGRESS_THIRD_PARTY else ""

    sink = stream_broker.open_stream(task_id, current_trace_id())
    # Flask 的 current_app 是 LocalProxy，类型上没有 _get_current_object；
    # 后台线程要自己压 app 上下文，只能拿真实对象，故此处按 Any 取一次再收窄回 Flask。
    app = cast(Flask, cast(Any, current_app)._get_current_object())  # noqa: SLF001
    stream_broker.submit(task_id, lambda: _run_stream_job(app, job, sink))

    return ok({
        "taskId": task_id,
        "sessionId": session_id,
        "capability": capability,
        "label": spec.label,
        "mode": "poll",
        "framesUrl": f"/api/v1/lab/stream/{task_id}/frames",
        "intervalMs": stream_broker.POLL_INTERVAL_MS,
        "egress": job.egress,
        "egressWarning": job.egress_warning,
    }, "流式执行已开始")


@dataclass(slots=True)
class _StreamJob:
    """一次流式执行需要的全部上下文：线程里不能再碰 flask.g，所以提前取好。"""

    capability: str
    label: str
    output_format: str
    task_id: int
    session_id: int
    control_key: str
    db_path: Path
    variables: dict[str, Any]
    refs: list[dict[str, Any]]
    mask_enabled: bool
    prompt_code: str
    module_path: str
    egress: str
    #: 准备结果：流式收尾要用它跑「校验 → 去重 → 成本」，与同步链路同一套口径
    plan: lab_service.RunPlan
    events: Iterator[Any]
    egress_warning: str = ""
    trace_id: str = ""


def _usage_with_retry(usage: dict[str, Any], retry_usage: dict[str, Any]) -> dict[str, Any]:
    """把自动修正那一次调用的用量并进来：成本口径不能只算第一次（与同步链路一致）。"""
    if not retry_usage:
        return dict(usage)
    merged = dict(usage)
    for key in ("promptTokens", "completionTokens", "totalTokens"):
        merged[key] = int(usage.get(key) or 0) + int(retry_usage.get(key) or 0)
    if not merged.get("totalTokens"):
        merged["totalTokens"] = int(merged.get("promptTokens") or 0) + int(
            merged.get("completionTokens") or 0)
    merged["estimated"] = bool(usage.get("estimated")) or bool(retry_usage.get("estimated"))
    return merged


def _stream_config_of(plan: lab_service.RunPlan, model: str) -> ResolvedAIConfig:
    """流式命中的是哪个候选：按模型名回认，认不出取首选；没有候选就给空配置（=未配价）。"""
    for candidate in plan.candidates:
        if model and candidate.model == model:
            return candidate
    if plan.candidates:
        return plan.candidates[0]
    return ResolvedAIConfig(provider="custom", protocol="", base_url="", model=model)


def _finalize_stream_run(
    job: _StreamJob, text: str, usage: dict[str, Any], model: str
) -> dict[str, Any]:
    """流式收尾：把同步链路的「解析 → 校验 → 规范化 → 去重 → 成本」接回活链路。

    历史上 `lab_service.finalize_stream_output` 定义了却没有任何调用点，
    走流式的 JSON 产出既不校验也不去重，四份结论（GAP-27/28/29/30）全空。
    收尾本身失败只留痕：帧已经吐出去了，不能反过来把已经到手的产出判死。
    """
    from ..db.connection import get_standalone_conn

    empty = {"text": text, "usage": usage, "context": {}, "schema": {}, "dedup": {}, "cost": {}}
    try:
        conn = get_standalone_conn(job.db_path)
    except (sqlite3.Error, OSError, ValueError) as exc:  # pragma: no cover - 连接都建不出来时
        log_event(logger, "stream_finalize_conn_failed", level=logging.WARNING,
                  traceId=job.trace_id or "-", taskId=job.task_id, error=str(exc))
        return empty
    try:
        finalization = lab_service.finalize_stream_output(job.plan, text, conn=conn)
        chosen_model = finalization.retry_model or model
        merged_usage = _usage_with_retry(usage, finalization.retry_usage)
        cost_payload = lab_service.build_cost_payload(
            job.plan,
            merged_usage,
            _stream_config_of(job.plan, chosen_model),
            chosen_model,
            conn=conn,
        )
        return {
            "text": finalization.output or text,
            "usage": merged_usage,
            "context": job.plan.context.as_dict() if job.plan.context is not None else {},
            "schema": lab_service.schema_payload_of(finalization),
            "dedup": lab_service.dedup_payload_of(finalization, job.plan),
            "cost": cost_payload,
        }
    except (AIError, AppError, sqlite3.Error, OSError, ValueError) as exc:
        log_event(logger, "stream_finalize_failed", level=logging.WARNING,
                  traceId=job.trace_id or "-", taskId=job.task_id,
                  capability=job.capability, error=str(exc))
        return empty
    finally:
        conn.close()


def _run_stream_job(app: Flask, job: _StreamJob, sink: stream_broker.StreamSink) -> None:
    """在 aitp-stream-* 线程上把事件流转成帧写进缓冲，并在结束时落库。

    原 SSE 生成器的分支结构一字未改，只把 `yield frame` 换成 `sink.emit(payload)`；
    少掉的是 GeneratorExit 一条——客户端断开不再能在这里感知，
    取消改由 /lab/tasks/<id>/cancel 显式送信号（见 cancel_task）。
    """
    write_guard.trace_hint(job.trace_id)
    counter = SeqCounter()
    collected: list[str] = []
    usage: dict[str, Any] = {}
    model = ""
    output = ""
    status = "success"
    error_code: int | None = None
    error_message: str | None = None
    # 云端候选真正发出去后由 mask 帧覆盖；本地候选保持「未脱敏」
    mask_info: dict[str, Any] = {
        "enabled": job.mask_enabled,
        "applied": False,
        "types": [],
        "count": 0,
    }
    try:
        with app.app_context():
            sink.emit(counter.payload(
                FRAME_START,
                taskId=job.task_id,
                sessionId=job.session_id,
                capability=job.capability,
                label=job.label,
                promptCode=job.prompt_code,
                modulePath=job.module_path,
                egress=job.egress,
                egressWarning=job.egress_warning,
                refs=job.refs,
                mask=mask_info,
            ))
            try:
                for event in job.events:
                    if task_control.is_cancelled(job.control_key):
                        # 取消要真的停下来：模型那边继续吐字就是继续耗时和计费
                        status = "cancelled"
                        error_message = "用户取消"
                        sink.emit(counter.payload(
                            FRAME_ERROR, errorCode=None, errorMessage=error_message))
                        break
                    if event.type == FRAME_MASK:
                        mask_info = dict(event.extra)
                        sink.emit(counter.payload(FRAME_MASK, **mask_info))
                    elif event.type == FRAME_DELTA:
                        if event.content:
                            collected.append(event.content)
                        sink.emit(counter.payload(FRAME_DELTA, content=event.content))
                    elif event.type == FRAME_REASONING:
                        sink.emit(counter.payload(FRAME_REASONING, content=event.content))
                    elif event.type == FRAME_USAGE:
                        if event.usage is not None:
                            usage = event.usage.as_dict()
                        if event.model:
                            model = event.model
                        sink.emit(counter.payload(FRAME_USAGE, usage=usage, model=model))
                    elif event.type == FRAME_DEGRADE:
                        sink.emit(counter.payload(FRAME_DEGRADE, **event.extra))
                    elif event.type == FRAME_ERROR:
                        status = "failed"
                        error_code = event.error_code
                        error_message = event.error_message or "AI 调用失败"
                        sink.emit(counter.payload(
                            FRAME_ERROR,
                            errorCode=error_code,
                            errorMessage=error_message,
                            extra=event.extra,
                        ))
                        break
                    elif event.type == FRAME_DONE:
                        if event.usage is not None:
                            usage = event.usage.as_dict()
                        if event.model:
                            model = event.model
            except (AIError, AppError, sqlite3.Error, OSError, ValueError) as exc:
                # 兜住的范围：模型侧异常（含降级到底后抛出的最后错误）、业务异常、
                # 写库异常、网络与编解码异常。产出线程里没有 HTTP 响应可抛，
                # 唯一的正确处置是转成一个 error 帧让前端能看到失败原因。
                status = "failed"
                error_code = exc.code if isinstance(exc, (AIError, AppError)) else ErrorCode.INTERNAL_ERROR
                error_message = str(exc)
                log_event(logger, "stream_producer_failed", level=logging.WARNING,
                          traceId=job.trace_id or "-", taskId=job.task_id,
                          capability=job.capability, error=error_message)
                sink.emit(counter.payload(
                    FRAME_ERROR, errorCode=error_code, errorMessage=error_message))
            else:
                if status == "success":
                    conclusions = _finalize_stream_run(job, "".join(collected), usage, model)
                    output = str(conclusions["text"] or "")
                    usage = dict(conclusions["usage"])
                    review_required = needs_human_review(output)
                    # JSON 产出物不能追加复核标记，否则前端无法解析结构化结果
                    if review_required and job.output_format != OUTPUT_JSON:
                        output = mark_review_required(output)
                    # 记录先落库再发 done 帧：前端收到 done 后会立刻刷新「历史产出」，
                    # 放到收尾里会晚于 done 帧，列表刷新时还看不到这一条。
                    record_id = _write_record_standalone(
                        job.db_path,
                        capability=job.capability,
                        output=output,
                        variables=job.variables,
                        refs=job.refs,
                        review_required=review_required,
                        model=model,
                        task_id=job.task_id,
                        session_id=job.session_id,
                        prompt_code=job.prompt_code,
                        usage=usage,
                        mask=mask_info,
                        context=conclusions["context"],
                        schema=conclusions["schema"],
                        dedup=conclusions["dedup"],
                        cost=conclusions["cost"],
                    )
                    sink.emit(counter.payload(
                        FRAME_DONE,
                        output=output,
                        usage=usage,
                        model=model,
                        refs=job.refs,
                        reviewRequired=review_required,
                        recordId=record_id,
                        mask=mask_info,
                        # 主线 A 四份结论：流式与同步现在给的是同一套字段
                        context=conclusions["context"],
                        schema=conclusions["schema"],
                        dedup=conclusions["dedup"],
                        cost=conclusions["cost"],
                    ))
    finally:
        task_control.release(job.control_key)
        if not output:
            output = "".join(collected)
        _finish_task(
            job.db_path, job.task_id, status,
            result={"output": output, "usage": usage, "model": model},
            error_code=error_code,
            error_message=error_message,
        )
        _record_message(job.db_path, job.session_id, output, job.prompt_code, model, usage, status)
        sink.finish(status)
        write_guard.trace_hint(None)


@bp.get("/stream/<int:task_id>/frames")
def stream_frames(task_id: int) -> Response:
    """取增量帧：拿到当下已有的帧就返回，绝不挂在服务器上等模型（这是换承载的意义）。"""
    after = int(request.args.get("after") or 0)
    payload = stream_broker.read_frames(task_id, after)
    if payload is not None:
        return ok(payload)
    return ok(_expired_stream_payload(task_id, after))


def _expired_stream_payload(task_id: int, after: int) -> dict[str, Any]:
    """缓冲区命中不到时的回落：把库里的任务终态还原成一帧，前端不至于空转到超时。

    命中场景只有两种：服务重启（缓冲是进程内的，帧随进程没了）与
    结束超过 BUFFER_TTL_S 才第一次来取。两种都该给出终局答案而不是 404。
    """
    conn = db()
    repo = AITaskRepository(conn)
    row = repo.get(task_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "流式任务不存在")
    status = str(row.get("status") or "failed")
    finished = status not in ("pending", "running")
    frames: list[dict[str, Any]] = []
    if finished:
        result = loads(row.get("result_json"), {}) or {}
        if status == "success":
            frames.append({
                "seq": after + 1,
                "type": FRAME_DONE,
                "output": str(result.get("output") or ""),
                "usage": result.get("usage") or {},
                "model": str(result.get("model") or ""),
                "reviewRequired": False,
            })
        else:
            frames.append({
                "seq": after + 1,
                "type": FRAME_ERROR,
                "errorCode": row.get("error_code"),
                "errorMessage": str(row.get("error_message") or "流式缓冲已过期，产出请查看任务详情"),
            })
    return {
        "taskId": task_id,
        "frames": frames,
        "lastSeq": after + (1 if frames else 0),
        "status": status,
        "finished": finished,
        "truncated": True,
        "intervalMs": stream_broker.POLL_INTERVAL_MS,
        "expired": True,
    }


def _record_message(
    db_path: Path,
    session_id: int,
    content: str,
    prompt_code: str,
    model: str,
    usage: dict[str, Any],
    status: str,
) -> None:
    """助手消息只在结束时写一次。"""
    if status != "success" or not content:
        return
    from ..db.connection import get_standalone_conn

    conn = get_standalone_conn(db_path)
    try:
        _save_assistant_message(
            conn, session_id, content,
            prompt_code=prompt_code, model=model, usage=usage,
        )
        conn.commit()
    # 兜住的范围：会话消息是产出的第三份副本（产出记录与任务终态都已落库），
    # 它失败不该让生产者线程带着未释放的缓冲区退出，故只留痕不重抛。
    except (sqlite3.Error, OSError, ValueError) as exc:  # pragma: no cover
        log_event(logger, "assistant_message_failed", level=logging.WARNING,
                  traceId=write_guard.log_trace_id(), sessionId=session_id,
                  promptCode=prompt_code, error=str(exc))
    finally:
        conn.close()


# ---------------- XMind 大纲解析 ----------------

@bp.post("/xmind/parse")
def parse_xmind() -> Response:
    """解析 .xmind 文件（上传）或粘贴的大纲文本，返回统一树结构。

    文件走 multipart 的 file 字段，文本走 JSON 的 outlineText。
    """
    from ..services import xmind_parser

    uploaded = request.files.get("file")
    if uploaded is not None and uploaded.filename:
        raw = uploaded.read()
        try:
            tree = xmind_parser.parse_xmind(raw)
            source = "xmind"
        except xmind_parser.XMindParseError as exc:
            raise AppError(ErrorCode.PARAM_ERROR, str(exc)) from None
    else:
        data = body()
        outline_text = str(data.get("outlineText") or "")
        if not outline_text.strip():
            raise AppError(ErrorCode.PARAM_ERROR, "请上传 .xmind 文件或粘贴大纲文本")
        tree = xmind_parser.parse_outline_text(outline_text)
        source = "text"
        if not tree:
            raise AppError(ErrorCode.PARAM_ERROR, "大纲文本里没有解析到任何节点")

    return ok({
        "tree": tree,
        "outlineText": xmind_parser.tree_to_outline_text(tree),
        "source": source,
    })


@bp.post("/output/export-xmind")
def export_capability_xmind() -> Response:
    """把测试点大纲导出成 .xmind 文件。

    前端把能力产出的测试点树（{name|title, children[], ...}）整理成统一
    {title, children[]} 结构回传，后端不缓存中间文件，直接生成 .xmind 下载。
    """
    from datetime import datetime

    from ..services import xmind_writer

    data = body()
    title = optional_str(data.get("title"), "title", 200) or "测试点脑图"
    raw_nodes = data.get("nodes")
    if not isinstance(raw_nodes, list) or not raw_nodes:
        raise AppError(ErrorCode.PARAM_ERROR, "没有可导出的测试点节点")
    normalized: list[dict[str, Any]] = []
    for item in raw_nodes:
        if not isinstance(item, dict):
            continue
        node = _normalize_xmind_node(item)
        if node is not None:
            normalized.append(node)
    if not normalized:
        raise AppError(ErrorCode.PARAM_ERROR, "测试点节点格式不正确，无法导出")

    content = xmind_writer.build_xmind(title, normalized)
    stamp = datetime.now().strftime("%Y%m%d-%H%M")
    filename = tabular_export.safe_filename(f"{title}-{stamp}", "xmind")
    return download_response(content, filename, XMIND_MIME, len(normalized))


def _normalize_xmind_node(item: dict[str, Any]) -> dict[str, Any] | None:
    """把测试点节点（name/title 二选一）规整成 xmind_writer 认的 {title, children}。"""
    title = str(item.get("title") or item.get("name") or "").strip()
    if not title:
        return None
    children_raw = item.get("children") or []
    children = []
    if isinstance(children_raw, list):
        for child in children_raw:
            if isinstance(child, dict):
                normalized = _normalize_xmind_node(child)
                if normalized is not None:
                    children.append(normalized)
    return {"title": title, "children": children}


# ---------------- 智能路由 ----------------

@bp.post("/route")
def route() -> Response:
    conn = db()
    data = body()
    text = require_str(data.get("text"), "text", 4000)
    project_id = _optional_int(data.get("projectId"), "projectId")
    top_k = _optional_int(data.get("topK"), "topK") or 3
    top_k = max(1, min(top_k, 10))

    result = lab_service.route_text(conn, text, project_id=project_id, top_k=top_k)
    return ok(result)


# ---------------- 执行前预检 ----------------

@bp.post("/preflight")
def preflight() -> Response:
    """执行前体检：缺项、完整度、主/辅助任务、执行顺序。

    纯本地计算，不调用模型；没有可用模型时也会如实标为阻断项。
    """
    conn = db()
    data = body()
    variables = data.get("variables")
    if variables is None:
        variables = {}
    if not isinstance(variables, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 variables 必须是对象")
    capabilities = data.get("capabilities")
    if capabilities is not None and not isinstance(capabilities, list):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 capabilities 必须是数组")
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    project_id = _optional_int(data.get("projectId"), "projectId")
    top_k = _optional_int(data.get("topK"), "topK") or 3
    top_k = max(1, min(top_k, 10))
    text = str(data.get("text") or "").strip()
    if not text and not capabilities:
        raise AppError(ErrorCode.PARAM_ERROR, "text 与 capabilities 至少要给一个")

    result = preflight_service.preflight(
        conn,
        text=text,
        capabilities=[str(item) for item in capabilities] if capabilities else None,
        variables=variables,
        module_id=module_id,
        project_id=project_id,
        top_k=top_k,
    )
    return ok(result)


# ---------------- 多入口顺序执行 ----------------

@bp.post("/composite/run")
def composite_run() -> Response:
    """按顺序跑一条能力流水线：父任务 + 子任务 + 步骤轨迹。"""
    conn = db()
    data = body()
    steps = composite_service.normalize_steps(data.get("steps"))
    profile_id = _optional_int(data.get("profileId"), "profileId")
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    session_id = _optional_int(data.get("sessionId"), "sessionId")
    title = str(data.get("title") or "").strip()
    continue_on_error = bool(data.get("continueOnError"))
    route = data.get("route")
    if route is not None and not isinstance(route, list):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 route 必须是数组")

    session_id = _ensure_session(conn, session_id, title or "多入口顺序执行", "composite")
    result = composite_service.run_composite(
        conn,
        steps=steps,
        session_id=session_id,
        profile_id=profile_id,
        module_id=module_id,
        title=title,
        route=[item for item in (route or []) if isinstance(item, dict)],
        continue_on_error=continue_on_error,
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_AI_TASK,
          int(result["parentTaskId"]), title or "多入口顺序执行",
          {"steps": len(steps), "status": result.get("status")})
    return ok(result)


@bp.post("/composite/cancel")
def cancel_composite() -> Response:
    """停止本机正在跑的顺序执行：当前步骤跑完即停，未开始步骤记为跳过。"""
    conn = db()
    data = body()
    session_id = _optional_int(data.get("sessionId"), "sessionId")
    sql = (
        "SELECT id FROM biz_ai_task WHERE task_type = 'composite'"
        " AND status = 'running' AND is_deleted = 0"
    )
    params: list[Any] = []
    if session_id:
        sql += " AND session_id = ?"
        params.append(session_id)
    rows = conn.execute(sql, params).fetchall()
    repo = AITaskRepository(conn)
    for row in rows:
        repo.finish(int(row["id"]), "cancelled", None, None, "用户取消")
    if rows:
        commit(conn)
        audit(conn, audit_service.AUDIT_ACTION_CANCEL, audit_service.RESOURCE_AI_TASK, None,
              "取消顺序执行", {"sessionId": session_id, "cancelled": len(rows)})
    return ok({"cancelled": len(rows)})


# ---------------- 会话与任务 ----------------

@bp.get("/sessions")
def list_sessions() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = AISessionRepository(conn)
    filters: dict[str, Any] = {}
    module = request.args.get("module")
    if module:
        filters["module"] = module
    page = repo.page_scoped(filters, scope_arg(), page_num, page_size)
    payload = page_payload(page)
    payload["list"] = [
        {
            "id": row.get("id"),
            "title": row.get("title"),
            "module": row.get("module"),
            "model": row.get("model"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }
        for row in payload["list"]
    ]
    return ok(payload)


@bp.get("/sessions/<pk>")
def get_session(pk: str) -> Response:
    conn = db()
    detail = AISessionRepository(conn).get_detail(path_int(pk))
    if not detail:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "会话不存在")
    detail["messages"] = [
        {
            "id": item.get("id"),
            "role": item.get("role"),
            "content": item.get("content"),
            "reasoning": item.get("reasoning"),
            "promptCode": item.get("prompt_code"),
            "model": item.get("model"),
            "tokensPrompt": item.get("tokens_prompt"),
            "tokensCompletion": item.get("tokens_completion"),
            "meta": item.get("meta"),
            "createdAt": item.get("created_at"),
        }
        for item in detail.get("messages", [])
    ]
    return ok(detail)


@bp.get("/tasks/<pk>")
def get_task(pk: str) -> Response:
    conn = db()
    detail = AITaskRepository(conn).get_detail(path_int(pk))
    if not detail:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    return ok(detail)


@bp.get("/tasks/<pk>/trajectory")
def get_task_trajectory(pk: str) -> Response:
    """任务轨迹：子任务、逐步状态与关联执行记录。"""
    conn = db()
    result = composite_service.task_trajectory(conn, path_int(pk))
    return ok(result)


@bp.get("/tasks")
def list_tasks() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = AITaskRepository(conn)
    filters: dict[str, Any] = {}
    for key, column in (("taskType", "task_type"), ("status", "status"),
                        ("capability", "capability"), ("projectId", "project_id")):
        value = request.args.get(key)
        if value:
            filters[column] = value
    page = repo.list(filters, None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [
        {
            "id": row.get("id"),
            "taskType": row.get("task_type"),
            "capability": row.get("capability"),
            "status": row.get("status"),
            "sessionId": row.get("session_id"),
            "projectId": row.get("project_id"),
            "moduleId": row.get("module_id"),
            "parentId": row.get("parent_id"),
            "errorCode": row.get("error_code"),
            "errorMessage": row.get("error_message"),
            "startedAt": row.get("started_at"),
            "finishedAt": row.get("finished_at"),
            "createdAt": row.get("created_at"),
        }
        for row in payload["list"]
    ]
    return ok(payload)


@bp.post("/tasks/<pk>/cancel")
def cancel_task(pk: str) -> Response:
    conn = db()
    repo = AITaskRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    if row.get("status") not in ("pending", "running"):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该任务已结束，无法取消")
    repo.finish(target, "cancelled", None, None, "用户取消")
    commit(conn)
    # 库里的状态只是结果：正在流式吐字的调用要靠进程内信号真的叫停，
    # 顺序编排则在每一步边界重读库状态（见 composite_service）
    interrupted = task_control.cancel(f"lab:{target}")
    return ok({"id": target, "status": "cancelled", "interrupted": interrupted})


# ---------------- 成本与预算（GAP-30） ----------------

#: 逐日趋势最多回溯多少天：够覆盖一个迭代，又不让一次请求扫穿整库
MAX_TREND_DAYS = 90


def _query_int(name: str, default: int = 0) -> int:
    raw = request.args.get(name)
    if raw in (None, ""):
        return default
    return require_int(raw, name, default)


def _price_field(data: dict[str, Any], row: dict[str, Any], camel: str, snake: str) -> int:
    """单价字段：没给就保留库里的现值，给了就必须是非负整数微元。"""
    if camel not in data and snake not in data:
        try:
            return ai_cost.normalize_price_micros(row.get(snake), camel)
        except ValueError:
            return 0
    raw = data[camel] if camel in data else data[snake]
    try:
        return ai_cost.normalize_price_micros(raw, camel)
    except ValueError as exc:
        raise AppError(ErrorCode.PARAM_ERROR, str(exc)) from None


@bp.get("/cost/summary")
def cost_summary() -> Response:
    """成本视图：今日 / 本月 / 选定范围的总花费，按能力、项目、档案拆分，带软预算状态。

    过滤参数都可省：`projectId` 顺着任务收敛（记录表本身没有项目列），
    `capability` 必须是已注册能力，`days` 给出逐日趋势（上限 90 天）。
    """
    conn = db()
    project_id = _optional_int(request.args.get("projectId"), "projectId")
    capability = optional_str(request.args.get("capability"), "capability", 60)
    if capability:
        lab_service.require_capability(capability)
    days = max(0, min(MAX_TREND_DAYS, _query_int("days", 0)))
    return ok(ai_cost.summary(
        conn, project_id=project_id, capability=capability, days=days,
    ))


@bp.get("/cost/budget")
def cost_budget() -> Response:
    """预算当前状态：日/月软阈值、告警比例、单次调用 token 软上限与提示文案。"""
    conn = db()
    project_id = _optional_int(request.args.get("projectId"), "projectId")
    capability = optional_str(request.args.get("capability"), "capability", 60)
    if capability:
        lab_service.require_capability(capability)
    return ok(ai_cost.budget_status(conn, project_id=project_id, capability=capability))


@bp.put("/cost/budget")
def cost_budget_update() -> Response:
    """写预算软阈值。`callTokens` 可按能力 / 按项目覆盖（作用域由请求体决定）。"""
    conn = db()
    data = body()
    changed: dict[str, int] = {}
    messages: list[str] = []
    for field, key, description in (
        ("dailyMicros", ai_cost.CONFIG_DAILY, "每日 AI 花费软阈值（微元），0=不设限，超了只提醒不拦截"),
        ("monthlyMicros", ai_cost.CONFIG_MONTHLY, "每月 AI 花费软阈值（微元），0=不设限，超了只提醒不拦截"),
        ("warnRatio", ai_cost.CONFIG_WARN_RATIO, "花费达到软阈值的百分之几时开始提示（1~100）"),
    ):
        if field in data:
            value = require_int(data.get(field), field)
            if value < 0:
                raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 不能为负数；0 表示不设限")
            if field == "warnRatio" and not (
                ai_cost.MIN_WARN_RATIO <= value <= ai_cost.MAX_WARN_RATIO
            ):
                raise AppError(ErrorCode.PARAM_ERROR, "参数 warnRatio 必须在 1~100 之间")
            ai_cost.set_int_config(conn, key, value, description)
            changed[field] = value
    if "callTokens" in data:
        value = require_int(data.get("callTokens"), "callTokens")
        if value < 0:
            raise AppError(ErrorCode.PARAM_ERROR, "参数 callTokens 不能为负数；0 表示不设限")
        scope_capability = optional_str(data.get("capability"), "capability", 60)
        scope_project = _optional_int(data.get("projectId"), "projectId")
        if scope_capability:
            lab_service.require_capability(scope_capability)
            key = f"{ai_cost.CALL_TOKENS_CAP_PREFIX}{scope_capability}"
            messages.append(f"能力「{label_of(scope_capability)}」")
        elif scope_project:
            if ProjectRepository(conn).get(scope_project) is None:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
            key = f"{ai_cost.CALL_TOKENS_PROJECT_PREFIX}{scope_project}"
            messages.append(f"项目 #{scope_project}")
        else:
            key = ai_cost.CONFIG_CALL_TOKENS
            messages.append("全局")
        ai_cost.set_int_config(
            conn, key, value, "单次调用 token 软上限（仅提示，不拦截、不降级）",
        )
        changed["callTokens"] = value
        changed[f"callTokens@{key}"] = value
    if not changed:
        raise AppError(ErrorCode.PARAM_ERROR, "没有需要更新的预算项")
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_SYSTEM_CONFIG,
          None, "AI 预算软阈值", {"changed": sorted(changed), "scope": messages})
    return ok(ai_cost.budget_status(conn), "预算设置已更新（软阈值，只提示不拦截）")


@bp.get("/cost/prices")
def cost_prices() -> Response:
    """各模型档案的 token 单价与累计花费：AI 设置页的成本区数据源。"""
    conn = db()
    return ok({
        "currency": ai_cost.currency_of(conn),
        "microsPerYuan": ai_cost.MICROS_PER_YUAN,
        "priceUnit": "微元 / 1000 token",
        "profiles": ai_cost.price_table(conn),
    })


@bp.put("/cost/prices/<int:profile_id>")
def cost_price_update(profile_id: int) -> Response:
    """按档案写 token 单价（微元 / 1000 token）。

    与 `PUT /settings/ai/profiles/<pk>` 写的是同一批列（`sys_ai_profile.price_*`，
    迁移 047），GAP-30 收口后档案接口也已能吃这三个字段，所以两条路只有分工不同：
    本接口回给前端「单价 + 该档案累计花费」一行看全，档案接口负责档案本身的增改；
    两边都不做第二套折算口径。
    """
    conn = db()
    repo = AIProfileRepository(conn)
    row = repo.get(profile_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模型档案不存在")
    data = body()
    price = ai_cost.TokenPrice(
        input_micros=_price_field(data, row, "priceInputMicros", "price_input_micros"),
        output_micros=_price_field(data, row, "priceOutputMicros", "price_output_micros"),
        currency=ai_cost.normalize_currency(
            data.get("priceCurrency") or data.get("price_currency") or row.get("price_currency")
        ),
    )
    repo.update(profile_id, {
        "price_input_micros": price.input_micros,
        "price_output_micros": price.output_micros,
        "price_currency": price.currency,
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_AI_PROFILE,
          profile_id, str(row.get("name") or ""), {
              "priceInputMicros": price.input_micros,
              "priceOutputMicros": price.output_micros,
              "priceCurrency": price.currency,
          })
    payload = next((item for item in ai_cost.price_table(conn)
                    if item["profileId"] == profile_id), None)
    if payload is None:  # pragma: no cover - 刚更新过的档案不可能读不回来
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "模型档案不存在")
    return ok(payload, "单价已更新")
