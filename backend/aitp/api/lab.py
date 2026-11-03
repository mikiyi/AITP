"""AI 实验室 API：能力执行、流式输出、智能路由、会话与任务。

统一契约：
- POST /lab/{capability}/run     同步执行，返回产出物与用量
- POST /lab/{capability}/stream  流式执行，SSE 帧带单调 seq
- POST /lab/route                自由文本 → 能力候选 + 功能点候选
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterator

from flask import Blueprint, Response, current_app, request, stream_with_context

from ..ai.capability_codes import CAPABILITIES, LAB_CAPABILITIES, OUTPUT_JSON
from ..ai.errors import AIError
from ..ai.safety import mark_review_required, needs_human_review
from ..common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY
from ..common.validators import require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..prompts.registry import list_templates
from ..repositories.ai_session_repo import AISessionRepository, AITaskRepository
from ..repositories.base import dumps
from ..services import audit_service, lab_service
from .helpers import audit, body, commit, db, page_args, page_payload
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

#: 请求体里不是提示词变量的字段
META_FIELDS = {"variables", "sessionId", "profileId", "moduleId", "title"}


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


# ---------------- 目录 ----------------

@bp.get("/capabilities")
def list_capabilities() -> Response:
    return ok([spec.as_dict() for spec in CAPABILITIES.values()])


@bp.get("/prompts")
def list_prompts() -> Response:
    return ok(list_templates())


@bp.get("/main-capabilities")
def list_lab_capabilities() -> Response:
    """AI 实验室首页展示的 10 个主能力。"""
    return ok([CAPABILITIES[code].as_dict() for code in LAB_CAPABILITIES])


# ---------------- 执行 ----------------

@bp.post("/<capability>/run")
def run_capability(capability: str) -> Response:
    conn = db()
    data = body()
    variables = _variables_of(data)
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    profile_id = _optional_int(data.get("profileId"), "profileId")
    session_id = _optional_int(data.get("sessionId"), "sessionId")

    spec = lab_service.require_capability(capability)
    # 先校验（缺变量 / 功能点未启用 / 没有可用模型），失败时不落任务
    plan = lab_service.prepare_run(
        conn, capability, variables, module_id=module_id, profile_id=profile_id
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
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_GENERATE, audit_service.RESOURCE_AI_TASK, task_id,
          spec.label, {"capability": capability, "moduleId": module_id, "latencyMs": result.latency_ms})
    return ok(payload)


@bp.post("/<capability>/stream")
def stream_capability(capability: str) -> Response:
    conn = db()
    data = body()
    variables = _variables_of(data)
    module_id = _optional_int(data.get("moduleId"), "moduleId")
    profile_id = _optional_int(data.get("profileId"), "profileId")
    session_id = _optional_int(data.get("sessionId"), "sessionId")

    spec = lab_service.require_capability(capability)
    events, plan = lab_service.stream_capability(
        conn, capability, variables, module_id=module_id, profile_id=profile_id
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
    detail = AISessionRepository(conn).get_detail(int(pk))
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
    detail = AITaskRepository(conn).get_detail(int(pk))
    if not detail:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    return ok(detail)


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
    target = int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    if row.get("status") not in ("pending", "running"):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该任务已结束，无法取消")
    repo.finish(target, "cancelled", None, None, "用户取消")
    commit(conn)
    return ok({"id": target, "status": "cancelled"})
