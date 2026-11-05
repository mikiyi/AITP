"""AI 实验室 API：能力执行、流式输出、智能路由、会话与任务。

统一契约：
- POST /lab/{capability}/run     同步执行，返回产出物与用量
- POST /lab/{capability}/stream  流式执行，SSE 帧带单调 seq
- POST /lab/route                自由文本 → 能力候选 + 功能点候选
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any, Iterator

from flask import Blueprint, Response, current_app, request, stream_with_context

from ..ai.capability_codes import (
    LAB_CAPABILITIES,
    LAB_EXTENDED_CAPABILITIES,
    OUTPUT_JSON,
    label_of,
)
from ..ai.errors import AIError
from ..ai.safety import mark_review_required, needs_human_review
from ..common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY
from ..common.validators import optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..prompts.registry import list_templates
from ..repositories.ai_session_repo import AISessionRepository, AITaskRepository
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
    tabular_export,
)
from ..services import preflight_service
from .helpers import audit, body, commit, db, download_response, page_args, page_payload, path_int
from .sse import (
    FRAME_DELTA,
    FRAME_DEGRADE,
    FRAME_DONE,
    FRAME_ERROR,
    FRAME_REASONING,
    FRAME_START,
    FRAME_USAGE,
    SSE_MIME,
    SeqCounter,
)

bp = Blueprint("lab", __name__)

#: XMind 文件 MIME；本质是 zip，部分环境也接受 application/octet-stream
XMIND_MIME = "application/vnd.xmind"

#: 请求体里不是提示词变量的字段
META_FIELDS = {"variables", "sessionId", "profileId", "moduleId", "title", "attachmentIds", "templateId"}


# ---------------- 参数解析 ----------------

def _optional_int(value: Any, field: str) -> int | None:
    if value in (None, ""):
        return None
    return require_int(value, field)


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
) -> int:
    payload: dict[str, Any] = {
        "module_id": module_id,
        "capability": capability,
        "request_json": dumps(variables),
    }
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
    except Exception:  # pragma: no cover - 落库失败不应影响已返回的响应
        pass
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
) -> int:
    """把这次执行的结构化结论落库：证据、缺项、风险点。"""
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
    except Exception:  # pragma: no cover - 落库失败不应影响已返回的响应
        return 0
    finally:
        conn.close()


# ---------------- 目录 ----------------

@bp.get("/capabilities")
def list_capabilities() -> Response:
    return ok(lab_service.capability_cards())


@bp.get("/prompts")
def list_prompts() -> Response:
    """提示词列表：带每个提示词的使用次数，供提示词库按使用频次排序。"""
    from ..repositories.ai_record_repo import AIRecordRepository

    counts = AIRecordRepository(db()).count_by_prompt_code()
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

    try:
        template = get_registry().get(code)
    except AppError:
        raise
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
        })
        section = "bugs"
        resource = audit_service.RESOURCE_BUG
    elif target == "sql":
        sql_repo = SqlAnalysisRepository(conn)
        new_id = sql_repo.insert({
            "project_id": project_id,
            "module_id": module_id,
            "sql_no": sql_repo.next_sql_no(project_id),
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
    # 先校验（缺变量 / 功能点未启用 / 没有可用模型），失败时不落任务
    plan = lab_service.prepare_run(
        conn, capability, variables, module_id=module_id, profile_id=profile_id,
        images=images,
    )

    session_id = _ensure_session(
        conn, session_id, require_str(data.get("title"), "title", 200) if data.get("title") else spec.label,
        capability,
    )
    task_id = _start_task(conn, capability, session_id, profile_id, module_id, variables)
    commit(conn)

    try:
        result = lab_service.execute_run(plan)
    except Exception as exc:
        code = exc.code if isinstance(exc, AIError) else ErrorCode.INTERNAL_ERROR
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
    )
    commit(conn)
    payload["recordId"] = record_id
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_AI_TASK, task_id,
          spec.label, {"capability": capability, "moduleId": module_id, "latencyMs": result.latency_ms})
    return ok(payload)


@bp.post("/<capability>/stream")
def stream_capability(capability: str) -> Response:
    conn = db()
    data = body()
    variables = _variables_of(data)
    _apply_output_template(conn, capability, data, variables)
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    profile_id = _optional_int(data.get("profileId"), "profileId")
    session_id = _optional_int(data.get("sessionId"), "sessionId")

    spec = lab_service.require_capability(capability)
    images = _resolve_images(_attachment_ids_of(data))
    events, plan = lab_service.stream_capability(
        conn, capability, variables, module_id=module_id, profile_id=profile_id,
        images=images,
    )

    session_id = _ensure_session(
        conn, session_id, require_str(data.get("title"), "title", 200) if data.get("title") else spec.label,
        capability,
    )
    task_id = _start_task(conn, capability, session_id, profile_id, module_id, variables)
    commit(conn)

    db_path = Path(current_app.config["AITP_DB_PATH"])
    prompt_code = plan.binding.prompt_code
    module_path = plan.binding.module_path
    # 数据外发边界：首选模型是第三方时，输入会离开本机，前端据此提示用户注意敏感数据
    first_candidate = plan.candidates[0] if plan.candidates else None
    egress = first_candidate.egress if first_candidate else EGRESS_NONE
    egress_warning = lab_service.EGRESS_WARNING if egress == EGRESS_THIRD_PARTY else ""

    def generate() -> Iterator[str]:
        counter = SeqCounter()
        collected: list[str] = []
        usage: dict[str, Any] = {}
        model = ""
        output = ""
        status = "success"
        error_code: int | None = None
        error_message: str | None = None

        try:
            yield counter.frame(
                FRAME_START,
                taskId=task_id,
                sessionId=session_id,
                capability=capability,
                label=spec.label,
                promptCode=prompt_code,
                modulePath=module_path,
                egress=egress,
                egressWarning=egress_warning,
                refs=plan.refs,
            )
            for event in events:
                if event.type == FRAME_DELTA:
                    if event.content:
                        collected.append(event.content)
                    yield counter.frame(FRAME_DELTA, content=event.content)
                elif event.type == FRAME_REASONING:
                    yield counter.frame(FRAME_REASONING, content=event.content)
                elif event.type == FRAME_USAGE:
                    if event.usage is not None:
                        usage = event.usage.as_dict()
                    if event.model:
                        model = event.model
                    yield counter.frame(FRAME_USAGE, usage=usage, model=model)
                elif event.type == FRAME_DEGRADE:
                    yield counter.frame(FRAME_DEGRADE, **event.extra)
                elif event.type == FRAME_ERROR:
                    status = "failed"
                    error_code = event.error_code
                    error_message = event.error_message or "AI 调用失败"
                    yield counter.frame(
                        FRAME_ERROR,
                        errorCode=error_code,
                        errorMessage=error_message,
                        extra=event.extra,
                    )
                    break
                elif event.type == FRAME_DONE:
                    if event.usage is not None:
                        usage = event.usage.as_dict()
                    if event.model:
                        model = event.model
        except GeneratorExit:
            # 客户端断开：任务标记为已取消
            status = "cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 - 错误转成单个 error 帧
            status = "failed"
            error_code = exc.code if isinstance(exc, AIError) else ErrorCode.INTERNAL_ERROR
            error_message = str(exc)
            yield counter.frame(FRAME_ERROR, errorCode=error_code, errorMessage=error_message)
        else:
            if status == "success":
                output = "".join(collected)
                review_required = needs_human_review(output)
                # JSON 产出物不能追加复核标记，否则前端无法解析结构化结果
                if review_required and spec.output_format != OUTPUT_JSON:
                    output = mark_review_required(output)
                # 记录先落库再发 done 帧：前端收到 done 后会立刻刷新「历史产出」，
                # 放到 finally 里会晚于 done 帧，列表刷新时还看不到这一条。
                _write_record_standalone(
                    db_path,
                    capability=capability,
                    output=output,
                    variables=variables,
                    refs=plan.refs,
                    review_required=review_required,
                    model=model,
                    task_id=task_id,
                    session_id=session_id,
                    prompt_code=prompt_code,
                    usage=usage,
                )
                yield counter.frame(
                    FRAME_DONE,
                    output=output,
                    usage=usage,
                    model=model,
                    refs=plan.refs,
                    reviewRequired=review_required,
                )
        finally:
            if not output:
                output = "".join(collected)
            _finish_task(
                db_path, task_id, status,
                result={"output": output, "usage": usage, "model": model},
                error_code=error_code,
                error_message=error_message,
            )
            _record_message(db_path, session_id, output, prompt_code, model, usage, status)

    response = Response(stream_with_context(generate()), mimetype=SSE_MIME)
    response.headers["Cache-Control"] = "no-cache"
    response.headers["X-Accel-Buffering"] = "no"
    response.headers["X-AITP-Task-Id"] = str(task_id)
    return response


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
    except Exception:  # pragma: no cover
        pass
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
    page = repo.list(filters, None, page_num=page_num, page_size=page_size)
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
    for key, column in (("taskType", "task_type"), ("status", "status"), ("capability", "capability")):
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
    return ok({"id": target, "status": "cancelled"})
