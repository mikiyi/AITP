"""AI 工具 API：提示词优化、引用式知识问答。

提示词优化只回填文本给用户确认，不直接执行，避免用户措辞被静默改写。
知识问答的答案里带 [K1] 引用标记，可映射回知识条目。
"""

from __future__ import annotations

from flask import Blueprint, Response

from ..ai.capability_codes import CAP_KNOWLEDGE_QA
from ..common.validators import require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import lab_service
from .helpers import body, db

bp = Blueprint("ai", __name__)


@bp.post("/prompts/optimize")
def optimize_prompt() -> Response:
    """把粗略输入改写成结构清晰的提示词；失败时保留原文。"""
    data = body()
    capability = require_str(data.get("capability"), "capability", 64)
    raw_text = require_str(data.get("rawText"), "rawText", 20000)
    result = lab_service.optimize_prompt(capability, raw_text)
    return ok(result)


@bp.post("/ask")
def ask_knowledge() -> Response:
    """基于正式知识库回答问题，答案带 [K1] 引用。"""
    conn = db()
    data = body()
    question = require_str(data.get("question"), "question", 2000)

    context, refs = lab_service.retrieve_knowledge_context(conn, question)
    if not context:
        return ok({
            "answer": "知识库中未检索到相关内容，请先补充知识条目或调整提问方式。",
            "refs": [],
            "contextFound": False,
        })

    result = lab_service.run_capability(
        conn,
        CAP_KNOWLEDGE_QA,
        {"question": question, "context": context},
    )
    return ok({
        "answer": result.output,
        "refs": refs,
        "model": result.model,
        "usage": result.usage,
        "degrade": result.degrade,
        "egress": result.egress,
        "egressWarning": result.egress_warning,
        "contextFound": True,
    })


@bp.get("/prompts")
def list_prompts() -> Response:
    from ..prompts.registry import list_templates

    return ok(list_templates())


@bp.post("/prompts/preview")
def preview_prompt() -> Response:
    """预览提示词渲染结果，便于调试变量是否齐全。"""
    from ..prompts.registry import get_registry

    data = body()
    code = require_str(data.get("code"), "code", 64)
    variables = data.get("variables")
    if variables is not None and not isinstance(variables, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "参数 variables 必须是对象")
    registry = get_registry()
    filled = registry.fill_defaults(code, variables or {})
    text = registry.render(code, filled)
    return ok({"code": code, "prompt": text})
