"""测试轮次业务化（一期：类型 / 计划时间窗 / 范围预设 / 4-3 轮模板）。

把「一个版本迭代的多轮测试节奏」当作编排对象：
- 轮次类型：冒烟 / 全覆盖 / 专项深挖 / 回归，各带默认核心目标与范围预设；
- 范围预设：按类型一键圈选用例（P1 主流程 / 全部迭代 / 上轮 fail+关联+异常兼容 / 回归 P1+P2）；
- 模板：4 轮 / 3 轮 一键生成轮次序列（名称/目标/范围/计划时间窗/顺序号）。

门禁联动（冒烟未过锁定后续）与 checklist 必测项集为二期，不在本模块。
"""

from __future__ import annotations

import sqlite3
from datetime import date, timedelta
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.test_run_repo import TestRunRepository
from . import test_run_service

ROUND_SMOKE = "smoke"
ROUND_FULL = "full"
ROUND_SPECIAL = "special"
ROUND_REGRESSION = "regression"

#: 轮次类型：value / 中文 / 默认核心目标 / 默认范围预设
ROUND_TYPES: tuple[dict[str, str], ...] = (
    {"value": ROUND_SMOKE, "label": "冒烟测试", "goal": "判断版本「可不可测」", "preset": "smoke_p1"},
    {"value": ROUND_FULL, "label": "全覆盖测试", "goal": "迭代功能点全覆盖，先把流程级 bug 挖出来", "preset": "version_all"},
    {"value": ROUND_SPECIAL, "label": "专项/深挖测试", "goal": "验证已修 bug + 发现新 bug，让版本趋于稳定", "preset": "prev_fail_plus"},
    {"value": ROUND_REGRESSION, "label": "回归测试", "goal": "确认改代码没带来副作用、需求无遗漏", "preset": "regression_p1p2"},
)
ROUND_TYPE_VALUES = tuple(item["value"] for item in ROUND_TYPES)
ROUND_TYPE_LABELS = {item["value"]: item["label"] for item in ROUND_TYPES}
ROUND_TYPE_GOALS = {item["value"]: item["goal"] for item in ROUND_TYPES}
ROUND_TYPE_PRESETS = {item["value"]: item["preset"] for item in ROUND_TYPES}

#: 范围预设：value / 中文 / 说明
SCOPE_PRESETS: tuple[dict[str, str], ...] = (
    {"value": "smoke_p1", "label": "仅 P1 主流程用例", "desc": "优先级 P1 的功能（主流程）用例"},
    {"value": "version_all", "label": "全部迭代用例", "desc": "本版本全部用例，流程性用例排在前面"},
    {"value": "prev_fail_plus", "label": "上轮 fail + 关联 + 异常/兼容", "desc": "上轮失败用例 + 其关联模块 + 异常/边界/兼容用例"},
    {"value": "version_all_plus_prev", "label": "全部迭代 + 上轮 fail 关联", "desc": "全部迭代用例并上轮失败关联（3 轮制的第二轮）"},
    {"value": "regression_p1p2", "label": "回归 P1+P2", "desc": "本版本 P1+P2 用例为主回归"},
    {"value": "regression_all", "label": "全量回归", "desc": "本版本全部用例回归"},
)
SCOPE_PRESET_VALUES = tuple(item["value"] for item in SCOPE_PRESETS)

#: 轮次模板：4 轮 / 3 轮，含默认天数与（3 轮制第二轮的）合并目标
TEMPLATES: dict[str, tuple[dict[str, Any], ...]] = {
    "four": (
        {"type": ROUND_SMOKE, "preset": "smoke_p1", "days": 2},
        {"type": ROUND_FULL, "preset": "version_all", "days": 5},
        {"type": ROUND_SPECIAL, "preset": "prev_fail_plus", "days": 3},
        {"type": ROUND_REGRESSION, "preset": "regression_p1p2", "days": 4},
    ),
    "three": (
        {"type": ROUND_SMOKE, "preset": "smoke_p1", "days": 2},
        {
            "type": ROUND_FULL,
            "preset": "version_all_plus_prev",
            "days": 5,
            "goal": "迭代功能点全覆盖挖流程级 bug；+验证已修 bug + 发现新 bug，让版本趋于稳定",
        },
        {"type": ROUND_REGRESSION, "preset": "regression_p1p2", "days": 3},
    ),
}
TEMPLATE_LABELS = {"four": "四轮（冒烟/全覆盖/专项/回归）", "three": "三轮（冒烟/版本/回归）"}


def meta() -> dict[str, Any]:
    return {
        "types": [dict(item) for item in ROUND_TYPES],
        "presets": [dict(item) for item in SCOPE_PRESETS],
        "templates": [
            {"value": key, "label": TEMPLATE_LABELS[key], "rounds": len(rounds)}
            for key, rounds in TEMPLATES.items()
        ],
    }


def _ids(conn: sqlite3.Connection, sql: str, params: list[Any]) -> list[int]:
    return [int(row[0]) for row in conn.execute(sql, params)]


def _version_cases(conn: sqlite3.Connection, project_id: int, version_id: int | None) -> tuple[str, list[Any]]:
    """版本内用例的基础 WHERE；没挂版本时退化为项目全量。"""
    if version_id:
        return "project_id = ? AND version_id = ? AND is_deleted = 0", [project_id, version_id]
    return "project_id = ? AND is_deleted = 0", [project_id]


def _prev_fail_ids(conn: sqlite3.Connection, prev_run_id: int | None) -> list[int]:
    if not prev_run_id:
        return []
    return _ids(
        conn,
        "SELECT case_id FROM biz_test_run_case"
        " WHERE run_id = ? AND result = 'failed' AND is_deleted = 0",
        [prev_run_id],
    )


def resolve_scope(
    conn: sqlite3.Connection,
    project_id: int,
    version_id: int | None,
    preset: str,
    prev_run_id: int | None = None,
) -> list[int]:
    """按范围预设圈选用例 id（保持业务顺序：流程优先 / 上轮 fail 优先）。"""
    if preset not in SCOPE_PRESET_VALUES:
        raise AppError(ErrorCode.PARAM_ERROR, f"未知范围预设：{preset}")
    where, params = _version_cases(conn, project_id, version_id)

    if preset == "smoke_p1":
        return _ids(
            conn,
            f"SELECT id FROM biz_test_case WHERE {where} AND priority = 'P1' AND case_type = 'functional' ORDER BY id",  # noqa: S608
            params,
        )
    if preset == "version_all":
        return _ids(
            conn,
            f"SELECT id FROM biz_test_case WHERE {where} ORDER BY (case_type = 'functional') DESC, id",  # noqa: S608
            params,
        )
    if preset == "regression_p1p2":
        return _ids(
            conn,
            f"SELECT id FROM biz_test_case WHERE {where} AND priority IN ('P1','P2') ORDER BY priority, id",  # noqa: S608
            params,
        )
    if preset == "regression_all":
        return _ids(conn, f"SELECT id FROM biz_test_case WHERE {where} ORDER BY id", params)  # noqa: S608

    # prev_fail_plus / version_all_plus_prev：上轮 fail + 关联模块 + 异常/边界/兼容
    fail_ids = _prev_fail_ids(conn, prev_run_id)
    ordered: list[int] = list(fail_ids)
    if fail_ids:
        marks = ",".join("?" for _ in fail_ids)
        modules = _ids(
            conn,
            f"SELECT DISTINCT module_id FROM biz_test_case WHERE id IN ({marks}) AND module_id IS NOT NULL",  # noqa: S608
            fail_ids,
        )
        if modules:
            mmarks = ",".join("?" for _ in modules)
            ordered += _ids(
                conn,
                f"SELECT id FROM biz_test_case WHERE {where} AND module_id IN ({mmarks}) ORDER BY id",  # noqa: S608
                params + modules,
            )
    ordered += _ids(
        conn,
        f"SELECT id FROM biz_test_case WHERE {where} AND (case_type IN ('exception','boundary') OR tags LIKE '%兼容%') ORDER BY id",  # noqa: S608
        params,
    )
    if preset == "version_all_plus_prev":
        ordered += _ids(
            conn,
            f"SELECT id FROM biz_test_case WHERE {where} ORDER BY (case_type = 'functional') DESC, id",  # noqa: S608
            params,
        )
    seen: set[int] = set()
    result: list[int] = []
    for case_id in ordered:
        if case_id not in seen:
            seen.add(case_id)
            result.append(case_id)
    return result


def _next_seq(conn: sqlite3.Connection, plan_id: int) -> int:
    row = conn.execute(
        "SELECT MAX(seq) FROM biz_test_run WHERE plan_id = ? AND is_deleted = 0", (plan_id,)
    ).fetchone()
    return int(row[0] or 0) + 1


def generate_template(
    conn: sqlite3.Connection,
    plan: dict[str, Any],
    template: str,
    start_date: str,
    *,
    env_id: int | None = None,
    owner: str = "",
    durations: list[int] | None = None,
) -> list[dict[str, Any]]:
    """按 4/3 轮模板为一个计划生成轮次序列（计划中状态，含计划时间窗）。"""
    if template not in TEMPLATES:
        raise AppError(ErrorCode.PARAM_ERROR, f"未知轮次模板：{template}")
    if str(plan.get("status") or "") == "archived":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "计划已归档，请先启用后再生成轮次")
    try:
        cursor = date.fromisoformat(start_date)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "startDate 必须是 YYYY-MM-DD") from None
    rounds = TEMPLATES[template]
    if durations is not None:
        if len(durations) != len(rounds) or any(int(d) < 1 for d in durations):
            raise AppError(ErrorCode.PARAM_ERROR, "durations 需与模板轮数一致且每轮 ≥1 天")
    project_id = int(plan["project_id"])
    version_id = plan.get("version_id")
    seq = _next_seq(conn, int(plan["id"]))
    created: list[dict[str, Any]] = []
    prev_run_id: int | None = None
    for index, round_def in enumerate(rounds):
        days = int(durations[index]) if durations else int(round_def["days"])
        planned_start = cursor.isoformat()
        planned_end = (cursor + timedelta(days=days - 1)).isoformat()
        cursor = cursor + timedelta(days=days)
        round_type = str(round_def["type"])
        preset = str(round_def["preset"])
        goal = str(round_def.get("goal") or ROUND_TYPE_GOALS[round_type])
        ids = resolve_scope(conn, project_id, version_id, preset, prev_run_id)
        payload = test_run_service.create_run(
            conn,
            project_id=project_id,
            name=f"第{index + 1}轮 · {ROUND_TYPE_LABELS[round_type]}",
            case_ids=ids,
            plan_id=int(plan["id"]),
            version_id=version_id,
            env_id=env_id if env_id is not None else plan.get("default_env_id"),
            owner=owner or str(plan.get("owner") or ""),
            round_type=round_type,
            goal=goal,
            planned_start=planned_start,
            planned_end=planned_end,
            scope_preset=preset,
            seq=seq,
            status="pending",
            allow_empty=True,
        )
        seq += 1
        prev_run_id = int(payload["id"])
        created.append(payload)
    return created


def refresh_scope(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """按轮次范围预设复算用例（专项轮带上上一轮 fail），仅待开始轮次可用。"""
    run = TestRunRepository(conn).get(run_id)
    if not run:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    preset = str(run.get("scope_preset") or "")
    if not preset:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "该轮次没有范围预设，无法刷新")
    prev_run_id = None
    if run.get("plan_id"):
        row = conn.execute(
            "SELECT id FROM biz_test_run WHERE plan_id = ? AND is_deleted = 0 AND seq < ?"
            " ORDER BY seq DESC, id DESC LIMIT 1",
            (run["plan_id"], int(run.get("seq") or 0)),
        ).fetchone()
        prev_run_id = int(row[0]) if row else None
    ids = resolve_scope(conn, int(run["project_id"]), run.get("version_id"), preset, prev_run_id)
    result = test_run_service.replace_cases(conn, run_id, ids)
    return {"runId": run_id, "preset": preset, "count": result["count"], "prevRunId": prev_run_id}