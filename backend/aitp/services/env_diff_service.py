"""轮次环境差异归因：同一用例跨环境最近一次执行结果对比。

解决的问题：同一批用例在 SIT / UAT / 预发都跑过，某个用例只在当前环境失败，
到底是产品缺陷还是环境差异？这里把「当前轮次」的逐条结果，与同项目其他
环境最近一轮的同用例结果并排，标出：
  - 环境敏感：当前失败，其他环境通过（大概率环境 / 数据 / 配置差异）；
  - 一致失败：当前失败，其他环境也失败（大概率产品缺陷）；
  - 当前通过：当前通过（附其他环境结论供参考）；
  - 无基线：其他环境没跑过该用例。

只读聚合，不落新表；数据来源就是轮次用例结果本身。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.test_run_repo import RESULT_LABELS, TestRunRepository

#: 每个用例最多保留的其他环境基线数（环境再多也只展示最近几个）
MAX_BASELINE_ENVS = 8
#: 其他环境结果扫描上限，避免历史轮次极多时把内存吃满
MAX_BASELINE_ROWS = 4000

STATUS_ENV_SENSITIVE = "env_sensitive"
STATUS_CONSISTENT_FAILURE = "consistent_failure"
STATUS_CURRENT_PASSED = "current_passed"
STATUS_NO_BASELINE = "no_baseline"
STATUS_LABELS = {
    STATUS_ENV_SENSITIVE: "环境敏感",
    STATUS_CONSISTENT_FAILURE: "一致失败",
    STATUS_CURRENT_PASSED: "当前通过",
    STATUS_NO_BASELINE: "无基线",
}

_EXECUTED = {"passed", "failed", "blocked", "skipped"}


def _baselines(
    conn: sqlite3.Connection, project_id: int, run_id: int, env_id: int
) -> tuple[dict[int, list[dict[str, Any]]], dict[int, str]]:
    """同项目其他环境最近的用例结论：case_id → [环境基线...]，并按 env_id 去重取最新。"""
    rows = conn.execute(
        "SELECT rc.case_id, rc.result, r.id AS run_id, r.name AS run_name, r.env_id,"
        " e.name AS env_name"
        " FROM biz_test_run_case rc"
        " JOIN biz_test_run r ON r.id = rc.run_id"
        " LEFT JOIN biz_test_env e ON e.id = r.env_id"
        " WHERE r.project_id = ? AND r.id != ? AND r.is_deleted = 0"
        " AND r.env_id IS NOT NULL AND r.env_id != ?"
        " AND r.status = 'completed' AND rc.is_deleted = 0"
        " AND rc.result IN ('passed', 'failed', 'blocked', 'skipped')"
        " ORDER BY r.id DESC, rc.id DESC LIMIT ?",
        (int(project_id), int(run_id), int(env_id), MAX_BASELINE_ROWS),
    ).fetchall()
    seen: set[tuple[int, int]] = set()
    out: dict[int, list[dict[str, Any]]] = {}
    env_names: dict[int, str] = {}
    for row in rows:
        case_id = int(row["case_id"])
        other_env = int(row["env_id"])
        key = (case_id, other_env)
        if key in seen:
            continue
        seen.add(key)
        env_names[other_env] = str(row["env_name"] or f"环境 {other_env}")
        bucket = out.setdefault(case_id, [])
        if len(bucket) < MAX_BASELINE_ENVS:
            bucket.append({
                "envId": other_env,
                "envName": env_names[other_env],
                "runId": int(row["run_id"]),
                "runName": str(row["run_name"] or ""),
                "result": str(row["result"] or ""),
                "resultLabel": RESULT_LABELS.get(str(row["result"] or ""), ""),
            })
    return out, env_names


def compare_run_envs(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """当前轮次逐条用例 × 其他环境最近结论；没有环境绑定或没有基线时给出明确提示。"""
    run = TestRunRepository(conn).get(int(run_id))
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    project_id = int(run["project_id"])
    env_id = run.get("env_id")
    rows = conn.execute(
        "SELECT case_id, case_no, title, case_type, result, actual_result FROM biz_test_run_case"
        " WHERE run_id = ? AND is_deleted = 0 ORDER BY seq ASC, id ASC",
        (int(run_id),),
    ).fetchall()
    cases = [dict(row) for row in rows]
    env_name = conn.execute(
        "SELECT name FROM biz_test_env WHERE id = ?", (int(env_id),)
    ).fetchone() if env_id else None
    current_env_name = str((env_name["name"] if env_name else run.get("env") or ""))

    payload: dict[str, Any] = {
        "runId": int(run_id),
        "projectId": project_id,
        "envId": int(env_id) if env_id else None,
        "envName": current_env_name,
        "baselineEnvs": [],
        "summary": {
            "total": len(cases),
            "executed": 0,
            "envSensitive": 0,
            "consistentFailure": 0,
            "currentPassed": 0,
            "noBaseline": 0,
        },
        "rows": [],
        "message": "",
    }
    if not env_id:
        payload["message"] = "该轮次未绑定执行环境，无法做环境差异归因"
        return payload
    if not cases:
        payload["message"] = "该轮次还没有用例快照"
        return payload

    baselines, env_names = _baselines(conn, project_id, int(run_id), int(env_id))
    if not baselines:
        payload["message"] = "同项目其他环境还没有同用例的完成轮次，暂无可对比基线"
        for case in cases:
            payload["rows"].append({
                "caseId": case["case_id"], "caseNo": case["case_no"],
                "title": case["title"], "caseType": case["case_type"],
                "currentResult": case["result"],
                "currentResultLabel": RESULT_LABELS.get(str(case["result"] or ""), ""),
                "status": STATUS_NO_BASELINE, "statusLabel": STATUS_LABELS[STATUS_NO_BASELINE],
                "baselines": [],
            })
            payload["summary"]["noBaseline"] += 1
        return payload

    payload["baselineEnvs"] = [
        {"envId": env_id, "envName": name} for env_id, name in sorted(env_names.items())
    ]
    summary = payload["summary"]
    for case in cases:
        current = str(case["result"] or "")
        if current not in _EXECUTED:
            continue
        summary["executed"] += 1
        base = baselines.get(int(case["case_id"])) or []
        if current == "passed":
            status = STATUS_CURRENT_PASSED
        elif not base:
            status = STATUS_NO_BASELINE
        elif any(item["result"] == "passed" for item in base):
            status = STATUS_ENV_SENSITIVE
        elif all(item["result"] == "failed" for item in base):
            status = STATUS_CONSISTENT_FAILURE
        else:
            status = STATUS_NO_BASELINE
        if status == STATUS_ENV_SENSITIVE:
            summary["envSensitive"] += 1
        elif status == STATUS_CONSISTENT_FAILURE:
            summary["consistentFailure"] += 1
        elif status == STATUS_CURRENT_PASSED:
            summary["currentPassed"] += 1
        else:
            summary["noBaseline"] += 1
        payload["rows"].append({
            "caseId": case["case_id"], "caseNo": case["case_no"],
            "title": case["title"], "caseType": case["case_type"],
            "currentResult": current,
            "currentResultLabel": RESULT_LABELS.get(current, ""),
            "currentActual": str(case.get("actual_result") or ""),
            "status": status, "statusLabel": STATUS_LABELS[status],
            "baselines": base,
        })
    # 环境敏感排最前，方便直接处理最可能是环境差异的失败
    order = {STATUS_ENV_SENSITIVE: 0, STATUS_CONSISTENT_FAILURE: 1,
             STATUS_CURRENT_PASSED: 2, STATUS_NO_BASELINE: 3}
    payload["rows"].sort(key=lambda item: (order.get(item["status"], 9), str(item["caseNo"])))
    return payload
