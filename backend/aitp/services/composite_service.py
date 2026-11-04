"""多入口顺序执行。

一次输入可能要跑多个能力：先评审需求，再拆测试点，再生成用例。
这里把这些能力排成一条流水线：

- 父任务（composite）代表整条流水线，子任务代表每个入口；
- 每一步都真实调用模型，`biz_ai_step` 记录这一步的输入摘要、输出摘要、模型、耗时、状态；
- 任一步失败时默认停下，标清失败原因；也可以选择继续跑完剩下的步骤。

步骤轨迹来自真实执行结果，不做执行前预填。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..ai.capability_codes import label_of, spec_of
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..prompts.registry import get_registry
from ..repositories.ai_record_repo import AIRecordRepository
from ..repositories.ai_session_repo import AITaskRepository
from ..repositories.ai_step_repo import AIStepRepository, step_summary
from ..repositories.base import dumps
from . import ai_record_service, lab_service

#: 父任务类型
COMPOSITE_TASK = "composite"
#: 父任务记录的能力标记
COMPOSITE_CAPABILITY = "composite"

#: 单次流水线最多多少步
MAX_STEPS = 12


def _summarize(text: str, limit: int = 200) -> str:
    for line in (text or "").splitlines():
        clean = line.strip().lstrip("#").strip()
        if clean:
            return clean[:limit]
    return (text or "")[:limit]


def _input_summary(variables: dict[str, Any]) -> str:
    """把变量压成一行摘要，够看出这一步喂了什么。"""
    parts: list[str] = []
    for key, value in variables.items():
        if value in (None, ""):
            continue
        text = str(value).replace("\n", " ").strip()
        if not text:
            continue
        parts.append(f"{key}={text[:60]}")
    return "；".join(parts)[:400]


def _missing_variables(prompt_code: str, variables: dict[str, Any]) -> list[str]:
    """这一步还缺哪些必填变量。

    判定口径与提示词渲染（registry.render）保持一致：空串算没填，
    可选变量不参与拦截。
    """
    if not prompt_code:
        return []
    try:
        template = get_registry().get(prompt_code)
    except AppError:
        return []
    optional = set(template.optional)
    missing: list[str] = []
    for name in template.variables:
        if name in optional:
            continue
        if not str(variables.get(name, "") or "").strip():
            missing.append(name)
    return missing


def _validate_steps(steps: list[dict[str, Any]]) -> None:
    """执行前体检：能力存在、必填变量齐全。

    两条入口（直接跑流水线、按编排入口跑）都走这道校验，
    保证「缺料」在开跑前就被指出来，而不是跑到一半才失败。
    """
    for index, step in enumerate(steps, start=1):
        capability = str(step.get("capability") or "")
        spec = spec_of(capability)
        if spec is None:
            raise AppError(ErrorCode.PARAM_ERROR, f"steps[{index}] 的能力不存在：{capability}")
        variables = step.get("variables") or {}
        missing = _missing_variables(spec.prompt_code, variables)
        if missing:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"steps[{index}]「{label_of(capability)}」缺少必填变量：{'、'.join(missing)}",
                {"missing": missing, "index": index, "capability": capability},
            )


def normalize_steps(raw: Any) -> list[dict[str, Any]]:
    """校验并规整前端传来的步骤表。

    校验放在执行前：能力不存在、必填变量没给，都在开跑之前就拦下来，
    不让用户等到流水线跑了一半才发现第一步就缺料。
    """
    if not isinstance(raw, list) or not raw:
        raise AppError(ErrorCode.PARAM_ERROR, "参数 steps 必须是非空数组")
    if len(raw) > MAX_STEPS:
        raise AppError(ErrorCode.PARAM_ERROR, f"steps 一次最多 {MAX_STEPS} 步")
    steps: list[dict[str, Any]] = []
    for index, item in enumerate(raw, start=1):
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, f"steps[{index}] 必须是对象")
        capability = str(item.get("capability") or "")
        if not capability:
            raise AppError(ErrorCode.PARAM_ERROR, f"steps[{index}] 缺少 capability")
        variables = item.get("variables")
        if variables is None:
            variables = {k: v for k, v in item.items()
                         if k not in ("capability", "name", "moduleId", "title")}
        if not isinstance(variables, dict):
            raise AppError(ErrorCode.PARAM_ERROR, f"steps[{index}].variables 必须是对象")
        steps.append({
            "capability": capability,
            "name": str(item.get("name") or label_of(capability)),
            "moduleId": item.get("moduleId"),
            "variables": {str(k): v for k, v in variables.items()},
        })
    _validate_steps(steps)
    return steps


def run_composite(
    conn: sqlite3.Connection,
    *,
    steps: list[dict[str, Any]],
    session_id: int | None = None,
    profile_id: int | None = None,
    module_id: int | None = None,
    title: str = "",
    route: list[dict[str, Any]] | None = None,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    """按顺序跑一条能力流水线，返回父任务与每一步的结果。"""
    _validate_steps(steps)
    task_repo = AITaskRepository(conn)
    step_repo = AIStepRepository(conn)

    parent_task_id = task_repo.start(
        COMPOSITE_TASK,
        session_id=session_id,
        profile_id=profile_id,
        module_id=module_id,
        capability=COMPOSITE_CAPABILITY,
        request_json=dumps({"steps": steps, "title": title}),
    )
    conn.commit()

    trajectories: list[dict[str, Any]] = []
    records: list[int] = []
    outputs: list[str] = []
    failed = False
    error_message = ""

    for index, step in enumerate(steps, start=1):
        capability = str(step["capability"])
        variables = dict(step.get("variables") or {})
        step_module_id = step.get("moduleId") or module_id
        step_name = str(step.get("name") or label_of(capability))

        child_task_id = task_repo.start(
            capability,
            session_id=session_id,
            profile_id=profile_id,
            module_id=step_module_id,
            parent_id=parent_task_id,
            capability=capability,
            request_json=dumps(variables),
        )
        step_id = step_repo.insert({
            "task_id": child_task_id,
            "parent_task_id": parent_task_id,
            "capability": capability,
            "step_order": index,
            "step_name": step_name,
            "status": "pending",
            "input_summary": _input_summary(variables),
        })
        conn.commit()

        try:
            result = lab_service.run_capability(
                conn, capability, variables,
                module_id=step_module_id, profile_id=profile_id,
            )
        except Exception as exc:  # noqa: BLE001 - 单步失败不让整条流水线崩掉
            code = getattr(exc, "code", ErrorCode.INTERNAL_ERROR)
            message = str(exc)
            step_repo.mark_failed(step_id, error_code=int(code), error_message=message)
            task_repo.finish(child_task_id, "failed", None, int(code), message)
            conn.commit()
            trajectories.append({
                "order": index,
                "capability": capability,
                "label": label_of(capability),
                "stepName": step_name,
                "status": "failed",
                "taskId": child_task_id,
                "errorCode": int(code),
                "errorMessage": message,
            })
            failed = True
            error_message = message
            if not continue_on_error:
                break
            continue

        step_repo.mark_success(
            step_id,
            output_summary=_summarize(result.output),
            model=result.model,
            latency_ms=result.latency_ms,
        )
        task_repo.finish(child_task_id, "success", {
            "output": result.output,
            "model": result.model,
            "usage": result.usage,
            "latencyMs": result.latency_ms,
        })
        record_id = ai_record_service.create_record(conn, ai_record_service.build_record(
            conn,
            capability=capability,
            output=result.output,
            variables=variables,
            refs=result.refs,
            review_required=result.review_required,
            model=result.model,
            task_id=child_task_id,
            session_id=session_id,
            optional_names=lab_service.optional_variables_of(result.prompt_code),
            route=route or [],
            usage=result.usage,
            prompt_code=result.prompt_code,
            latency_ms=result.latency_ms,
            steps=[{
                "order": index,
                "capability": capability,
                "label": label_of(capability),
                "stepName": step_name,
                "status": "success",
                "model": result.model,
                "latencyMs": result.latency_ms,
                "summary": _summarize(result.output),
            }],
        ))
        records.append(record_id)
        outputs.append(f"【{step_name}】\n{result.output}")
        trajectories.append({
            "order": index,
            "capability": capability,
            "label": label_of(capability),
            "stepName": step_name,
            "status": "success",
            "taskId": child_task_id,
            "recordId": record_id,
            "model": result.model,
            "latencyMs": result.latency_ms,
            "summary": _summarize(result.output),
            "reviewRequired": result.review_required,
        })
        conn.commit()

    skipped = len(steps) - len(trajectories)
    if skipped > 0:
        for order in range(len(trajectories) + 1, len(steps) + 1):
            pending = steps[order - 1]
            trajectories.append({
                "order": order,
                "capability": str(pending["capability"]),
                "label": label_of(str(pending["capability"])),
                "stepName": str(pending.get("name") or label_of(str(pending["capability"]))),
                "status": "skipped",
                "errorMessage": "前序步骤失败，已跳过",
            })

    output_text = "\n\n".join(outputs)
    parent_status = "failed" if failed else "success"
    task_repo.finish(
        parent_task_id,
        parent_status,
        {
            "steps": trajectories,
            "records": records,
            "output": output_text,
        },
        ErrorCode.INTERNAL_ERROR if failed else None,
        error_message or None,
    )

    parent_record_id = 0
    if output_text:
        parent_record_id = ai_record_service.create_record(
            conn, ai_record_service.build_record(
                conn,
                capability=COMPOSITE_CAPABILITY,
                output=output_text,
                variables={"steps": [str(item["capability"]) for item in steps]},
                review_required=any(bool(item.get("reviewRequired")) for item in trajectories),
                model="",
                task_id=parent_task_id,
                session_id=session_id,
                route=route or [],
                steps=trajectories,
            ),
        )
    conn.commit()

    return {
        "parentTaskId": parent_task_id,
        "sessionId": session_id,
        "status": parent_status,
        "steps": trajectories,
        "records": records,
        "parentRecordId": parent_record_id,
        "output": output_text,
        "errorMessage": error_message,
    }


def task_trajectory(conn: sqlite3.Connection, task_id: int) -> dict[str, Any]:
    """取一个任务的完整轨迹：子任务 + 步骤 + 关联执行记录。"""
    task_repo = AITaskRepository(conn)
    task = task_repo.get_detail(task_id)
    if task is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "任务不存在")
    step_repo = AIStepRepository(conn)
    # 父任务的步骤挂在 parent_task_id 上，叶子任务的步骤挂在 task_id 上
    steps = step_repo.list_by_parent(task_id)
    if not steps:
        steps = step_repo.list_by_task(task_id)
    children = conn.execute(
        "SELECT * FROM biz_ai_task WHERE parent_id = ? AND is_deleted = 0 ORDER BY id ASC",
        (task_id,),
    ).fetchall()
    child_ids = [int(row["id"]) for row in children]
    records = AIRecordRepository(conn).list({"task_id": task_id}, None, 1, 100)
    record_items = [AIRecordRepository(conn).decorate(row) for row in records.list]
    for child_id in child_ids:
        rows = conn.execute(
            "SELECT * FROM biz_ai_record WHERE task_id = ? AND is_deleted = 0"
            " ORDER BY id ASC",
            (child_id,),
        ).fetchall()
        record_items.extend(
            AIRecordRepository(conn).decorate(dict(row)) for row in rows
        )
    return {
        "task": task,
        "steps": [step_summary(row) for row in steps],
        "children": [dict(row) for row in children],
        "records": record_items,
        "finishedAt": utcnow_iso(),
    }
