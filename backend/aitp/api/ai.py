"""AI 工具 API：提示词优化、外发脱敏预览、模板预览。

提示词优化只回填文本给用户确认，不直接执行，避免用户措辞被静默改写。
"""

from __future__ import annotations

from flask import Blueprint, Response

from ..ai.masking import cloud_mask_enabled, preview_mask
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
    result = lab_service.optimize_prompt(capability, raw_text, conn=db())
    return ok(result)


@bp.post("/mask/preview")
def mask_preview() -> Response:
    """外发前的站内检查：识别待发送文本里的敏感信息，只回类型与数量。"""
    conn = db()
    data = body()
    text = require_str(data.get("text"), "text", 200000)
    info = preview_mask(text, enabled=cloud_mask_enabled(conn))
    return ok(info.as_dict())


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
