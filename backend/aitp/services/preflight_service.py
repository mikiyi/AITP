"""执行前预检。

跑一次 AI 之前先说清楚三件事：

1. **缺什么**：哪些必填变量没给、哪些可选变量没给。
2. **完整度**：必填信息填了几成，结论会被削到多细。
3. **按什么顺序跑**：谁是主任务、谁是辅助任务，步骤怎么排。

预检不调用模型，纯本地计算，缺项直接指出来，不让用户等到模型返回才发现漏填。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from typing import Any

from ..ai.capability_codes import CAPABILITIES, LAB_CAPABILITIES, label_of, spec_of
from ..errors import AppError, ErrorCode
from ..prompts.registry import get_registry
from ..repositories.module_repo import ModuleCapabilityRepository, ModuleRepository
from . import lab_service

#: 主任务 / 辅助任务
ROLE_PRIMARY = "primary"
ROLE_AUXILIARY = "auxiliary"

#: 完整度低于该值视为信息严重不足
LOW_COMPLETENESS = 0.5


@dataclass(slots=True)
class PreflightTask:
    """一个能力的预检结论。"""
    capability: str
    label: str
    role: str
    order: int
    score: float
    required: list[str] = field(default_factory=list)
    optional: list[str] = field(default_factory=list)
    provided: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    missing_optional: list[str] = field(default_factory=list)
    completeness: float = 1.0
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "label": self.label,
            "role": self.role,
            "order": self.order,
            "score": self.score,
            "required": self.required,
            "optional": self.optional,
            "provided": self.provided,
            "missing": self.missing,
            "missingOptional": self.missing_optional,
            "completeness": round(self.completeness, 3),
            "blockers": self.blockers,
            "warnings": self.warnings,
            "ready": not self.blockers,
        }


def _variables_of_prompt(prompt_code: str) -> tuple[list[str], list[str]]:
    """取提示词的必填变量与可选变量。"""
    if not prompt_code:
        return [], []
    try:
        template = get_registry().get(prompt_code)
    except AppError:
        return [], []
    optional = list(template.optional)
    required = [name for name in template.variables if name not in optional]
    return required, optional


def _filled(variables: dict[str, Any], name: str) -> bool:
    value = variables.get(name)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, dict)):
        return bool(value)
    return True


def _module_blocker(
    conn: sqlite3.Connection, capability: str, module_id: int | None
) -> tuple[str, str]:
    """功能点维度的前置条件：能力没启用就不能在它上面跑。

    返回 (阻断原因, 提示语)，没有阻断返回空串。
    """
    if module_id is None:
        return "", ""
    module_repo = ModuleRepository(conn)
    node = module_repo.get_node(module_id)
    if not node:
        return "blocker", "功能点不存在"
    binding = ModuleCapabilityRepository(conn).get_binding(module_id, capability)
    if binding is None:
        return (
            "blocker",
            f"功能点「{module_repo.path_text(module_id, project_name='')}」未启用"
            f"「{label_of(capability)}」能力",
        )
    if not binding.get("enabled"):
        return (
            "blocker",
            f"功能点「{module_repo.path_text(module_id, project_name='')}」已停用"
            f"「{label_of(capability)}」能力",
        )
    return "", ""


def _task_for(
    conn: sqlite3.Connection,
    capability: str,
    variables: dict[str, Any],
    module_id: int | None,
    role: str,
    order: int,
    score: float,
) -> PreflightTask:
    spec = spec_of(capability)
    prompt_code = spec.prompt_code if spec else ""
    required, optional = _variables_of_prompt(prompt_code)
    provided = [name for name in required if _filled(variables, name)]
    missing = [name for name in required if not _filled(variables, name)]
    missing_optional = [name for name in optional if not _filled(variables, name)]
    completeness = 1.0 if not required else (len(required) - len(missing)) / len(required)

    task = PreflightTask(
        capability=capability,
        label=label_of(capability),
        role=role,
        order=order,
        score=score,
        required=required,
        optional=optional,
        provided=provided,
        missing=missing,
        missing_optional=missing_optional,
        completeness=completeness,
    )

    if missing:
        task.blockers.append(f"缺少必填变量：{'、'.join(missing)}")
    kind, message = _module_blocker(conn, capability, module_id)
    if kind == "blocker":
        task.blockers.append(message)
    if completeness < LOW_COMPLETENESS and required:
        task.warnings.append(f"必填完整度仅 {round(completeness * 100)}%，产出结论可能过于笼统")
    if missing_optional:
        task.warnings.append(f"未提供可选信息：{'、'.join(missing_optional)}")
    return task


def preflight(
    conn: sqlite3.Connection,
    *,
    text: str = "",
    capabilities: list[str] | None = None,
    variables: dict[str, Any] | None = None,
    module_id: int | None = None,
    project_id: int | None = None,
    top_k: int = 3,
) -> dict[str, Any]:
    """给一次执行做事前体检。

    - 传 `capabilities` 时按给定顺序预检；
    - 传 `text` 时先智能路由，用路由结果当候选；
    - 两者都不传则预检全部实验室主能力。
    """
    values = variables or {}
    source = "manual"
    routes: list[dict[str, Any]] = []
    candidates: list[tuple[str, float]] = []

    if capabilities:
        for code in capabilities:
            if code not in CAPABILITIES:
                raise AppError(ErrorCode.PARAM_ERROR, f"未知 AI 能力：{code}")
            candidates.append((code, 1.0))
        source = "manual"
    elif text.strip():
        routed = lab_service.route_text(conn, text, project_id=project_id, top_k=top_k)
        routes = list(routed.get("capabilityRoutes") or [])
        candidates = [(str(item.get("capability")), float(item.get("score") or 0)) for item in routes]
        source = str(routed.get("source") or "fallback")
        if not candidates:
            candidates = [(code, 0.0) for code in LAB_CAPABILITIES]
    else:
        candidates = [(code, 0.0) for code in LAB_CAPABILITIES]

    tasks: list[PreflightTask] = []
    for index, (code, score) in enumerate(candidates):
        role = ROLE_PRIMARY if index == 0 else ROLE_AUXILIARY
        tasks.append(_task_for(conn, code, values, module_id, role, index + 1, score))

    # 没有可用模型时，所有任务都阻断——这是全局前提
    model_ready = bool(lab_service.available_candidates(None))
    if not model_ready:
        for task in tasks:
            task.blockers.append("没有可用的 AI 模型，请先在「工作台配置 → AI配置」中设置")

    overall = 0.0
    if tasks:
        overall = sum(task.completeness for task in tasks) / len(tasks)

    blockers = [f"{task.label}：{item}" for task in tasks for item in task.blockers]
    warnings = [f"{task.label}：{item}" for task in tasks for item in task.warnings]

    return {
        "source": source,
        "routes": routes,
        "tasks": [task.as_dict() for task in tasks],
        "order": [task.capability for task in tasks],
        "primary": tasks[0].capability if tasks else "",
        "auxiliary": [task.capability for task in tasks[1:]],
        "overallCompleteness": round(overall, 3),
        "modelReady": model_ready,
        "ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
    }
