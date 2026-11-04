"""元数据 API：枚举字典、能力目录，供前端下拉与表单使用。"""

from __future__ import annotations

from flask import Blueprint, Response

from ..ai.capability_codes import CAPABILITIES
from ..common import enums
from ..common.constants import PROVIDERS
from ..db.connection import get_db
from ..envelope import ok
from ..repositories.knowledge_category_repo import KnowledgeCategoryRepository

bp = Blueprint("meta", __name__)


@bp.get("/enums")
def list_enums() -> Response:
    return ok({
        "projectStatus": list(enums.PROJECT_STATUS),
        "requirementStatus": list(enums.REQUIREMENT_STATUS),
        "caseType": list(enums.CASE_TYPE),
        "casePrefix": dict(enums.CASE_PREFIX),
        "caseStatus": list(enums.CASE_STATUS),
        "caseStatusLabel": dict(enums.CASE_STATUS_LABEL),
        "casePriority": list(enums.CASE_PRIORITY),
        "caseSource": list(enums.CASE_SOURCE),
        "bugStatus": list(enums.BUG_STATUS),
        "bugSeverity": list(enums.BUG_SEVERITY),
        "bugType": list(enums.BUG_TYPE),
        "bugRelation": list(enums.BUG_RELATION),
        "bugTransitions": {k: list(v) for k, v in enums.BUG_TRANSITIONS.items()},
        "knowledgeCategory": _knowledge_categories(),
        "knowledgeSource": list(enums.KNOWLEDGE_SOURCE),
        "reportType": list(enums.REPORT_TYPE),
        "reportStatus": list(enums.REPORT_STATUS),
        "reportSection": list(enums.REPORT_SECTION),
        "aiTaskStatus": list(enums.AI_TASK_STATUS),
        "aiTaskType": list(enums.AI_TASK_TYPE),
    })


def _knowledge_categories() -> list[str]:
    """分类以库里的清单为准（含自定义分类），查不到时退回内置中文分类。"""
    try:
        names = [item["name"] for item in KnowledgeCategoryRepository(get_db()).list_categories()]
    except Exception:  # noqa: BLE001 - 元数据接口不该因为分类表异常整体失败
        names = []
    return names or list(enums.KNOWLEDGE_CATEGORY)


@bp.get("/capabilities")
def list_capabilities() -> Response:
    return ok([
        {
            "code": spec.code,
            "label": spec.label,
            "goal": spec.goal,
            "keywords": list(spec.keywords),
        }
        for spec in CAPABILITIES.values()
    ])


@bp.get("/providers")
def list_providers() -> Response:
    return ok([
        {
            "name": spec.name,
            "label": spec.label,
            "protocol": spec.protocol,
            "defaultBaseUrl": spec.default_base_url,
            "defaultModel": spec.default_model,
            "requiresApiKey": spec.requires_api_key,
            "envKeys": list(spec.env_key),
        }
        for spec in PROVIDERS.values()
    ])
