"""Eval 服务：评测集/用例 CRUD、回放跑批、指标计算、门禁与报告。

MVP 只做「回放评测」：模型输出由调用方传入（真实调用、线上留痕、人工构造都行），
本服务负责判定、指标与结论。真实调用接入（judge 与模型跑批）在后续阶段。
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..eval.cases import evaluate_assertions, normalize_case
from ..eval.gate import evaluate_gate
from ..eval.metrics import compute_metrics
from ..repositories.base import dumps, loads
from ..repositories.eval_case_repo import EvalCaseRepository
from ..repositories.eval_metric_repo import EvalMetricRepository
from ..repositories.eval_result_repo import EvalResultRepository
from ..repositories.eval_run_repo import EvalRunRepository
from ..repositories.eval_suite_repo import EvalSuiteRepository

VISIBILITIES = {"private", "holdout", "public"}
TARGET_TYPES = {"llm", "agent", "rag", "prompt"}


def _int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是整数") from None


def _suite_or_404(conn: sqlite3.Connection, suite_id: int) -> dict[str, Any]:
    suite = EvalSuiteRepository(conn).get(suite_id)
    if suite is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评测集不存在")
    return suite


# ---------------------------------------------------------------- 评测集 / 用例


def create_suite(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    project_id = _int(payload.get("projectId"), "projectId")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AppError(ErrorCode.PARAM_ERROR, "评测集名称不能为空")
    visibility = str(payload.get("visibility") or "private")
    if visibility not in VISIBILITIES:
        raise AppError(ErrorCode.PARAM_ERROR, f"visibility 取值必须是 {sorted(VISIBILITIES)} 之一")
    target_type = str(payload.get("targetType") or "llm")
    if target_type not in TARGET_TYPES:
        raise AppError(ErrorCode.PARAM_ERROR, f"targetType 取值必须是 {sorted(TARGET_TYPES)} 之一")

    repo = EvalSuiteRepository(conn)
    if repo.find_by_name(project_id, name) is not None:
        raise AppError(ErrorCode.PARAM_ERROR, "同名评测集已存在")

    suite_id = repo.insert(
        {
            "project_id": project_id,
            "name": name,
            "target_type": target_type,
            "description": str(payload.get("description") or ""),
            "tags": str(payload.get("tags") or ""),
            "visibility": visibility,
            "creator": str(payload.get("creator") or ""),
        }
    )
    conn.commit()
    return suite_id


def add_case(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    suite_id = _int(payload.get("suiteId"), "suiteId")
    suite = _suite_or_404(conn, suite_id)
    if suite.get("status") != "active":
        raise AppError(ErrorCode.PARAM_ERROR, "评测集已归档，禁止新增用例")

    normalized = normalize_case(payload)
    project_id = _int(payload.get("projectId") or suite["project_id"], "projectId")
    if project_id != int(suite["project_id"]):
        raise AppError(ErrorCode.PARAM_ERROR, "用例所属项目与评测集不一致")

    case_repo = EvalCaseRepository(conn)
    case_no = case_repo.next_case_no(suite_id)
    case_id = case_repo.insert(
        {
            "project_id": project_id,
            "suite_id": suite_id,
            "case_no": case_no,
            "category": normalized["category"],
            "severity": normalized["severity"],
            "title": normalized["title"],
            "input_json": dumps(normalized["input_json"]),
            "expected_type": normalized["expected_type"],
            "expected_text": normalized["expected_text"],
            "rubric_json": dumps(normalized["rubric_json"]),
            "assertions_json": dumps(normalized["assertions"]),
            "effective_date": normalized["effective_date"],
            "tags": normalized["tags"],
            "difficulty": normalized["difficulty"],
            "source": normalized["source"],
            "source_ref": normalized["source_ref"],
            "status": "active",
            "creator": str(payload.get("creator") or ""),
        }
    )
    suite_repo = EvalSuiteRepository(conn)
    suite_repo.update(suite_id, {"case_count": case_repo.count_by_suite(suite_id)})
    conn.commit()
    return case_id


def suite_detail(conn: sqlite3.Connection, suite_id: int) -> dict[str, Any]:
    suite = _suite_or_404(conn, suite_id)
    case_repo = EvalCaseRepository(conn)
    run_repo = EvalRunRepository(conn)
    return {
        **suite,
        "caseCount": case_repo.count_by_suite(suite_id),
        "categories": case_repo.raw(
            "SELECT category, COUNT(*) AS total FROM biz_eval_case "
            "WHERE suite_id = ? AND is_deleted = 0 GROUP BY category",
            (suite_id,),
        ),
        "recentRuns": run_repo.list_all({"suite_id": suite_id}, limit=5),
    }


# ---------------------------------------------------------------- 跑批（回放）


def _case_set_hash(cases: list[dict[str, Any]]) -> str:
    payload = [[case.get("case_no"), case.get("updated_at")] for case in cases]
    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()


def _run_key(
    suite: dict[str, Any],
    case_hash: str,
    config: dict[str, Any],
    judge: dict[str, Any],
    seed: Any,
    purpose: str,
) -> str:
    payload = [
        suite.get("id"),
        suite.get("version"),
        case_hash,
        config,
        judge,
        seed,
        purpose,
    ]
    return hashlib.sha256(dumps(payload).encode("utf-8")).hexdigest()


def create_run(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    suite_id = _int(payload.get("suiteId"), "suiteId")
    suite = _suite_or_404(conn, suite_id)
    cases = EvalCaseRepository(conn).list_active(suite_id)
    if not cases:
        raise AppError(ErrorCode.PARAM_ERROR, "评测集没有可用用例，禁止发起跑批")

    config = payload.get("config") or {}
    judge = payload.get("judge") or {}
    if not isinstance(config, dict) or not isinstance(judge, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "config / judge 必须是对象")
    purpose = str(payload.get("purpose") or "run")
    case_hash = _case_set_hash(cases)
    run_key = _run_key(suite, case_hash, config, judge, config.get("seed"), purpose)

    run_repo = EvalRunRepository(conn)
    existing = run_repo.find_by_run_key(run_key)
    if existing is not None:
        return {"runId": int(existing["id"]), "reused": True, "runKey": run_key}

    run_id = run_repo.insert(
        {
            "project_id": int(suite["project_id"]),
            "suite_id": suite_id,
            "suite_version": int(suite.get("version") or 1),
            "judge_mode": "rule",
            "trigger_type": str(payload.get("triggerType") or "manual"),
            "config_snapshot": dumps(config),
            "judge_snapshot": dumps(judge),
            "case_set_hash": case_hash,
            "run_purpose": purpose,
            "run_key": run_key,
            "status": "queued",
            "total": len(cases),
            "cost_source": "estimated",
            "creator": str(payload.get("creator") or ""),
        }
    )
    conn.commit()
    return {"runId": run_id, "reused": False, "runKey": run_key}


def _index_outputs(outputs: Any) -> dict[str, dict[str, Any]]:
    if not outputs:
        return {}
    if isinstance(outputs, dict):
        return {str(key): dict(value or {}) for key, value in outputs.items()}
    if not isinstance(outputs, list):
        raise AppError(ErrorCode.PARAM_ERROR, "outputs 必须是对象或数组")
    mapping: dict[str, dict[str, Any]] = {}
    for item in outputs:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "outputs 的每一项必须是对象")
        key = item.get("caseNo") or item.get("caseId")
        if key is None:
            raise AppError(ErrorCode.PARAM_ERROR, "outputs 每项必须带 caseNo 或 caseId")
        mapping[str(key)] = item
    return mapping


def execute_replay(conn: sqlite3.Connection, run_id: int, outputs: Any) -> dict[str, Any]:
    """把一个 run 的逐条输出判成 pass/fail，算出指标并收尾。"""

    run_repo = EvalRunRepository(conn)
    run = run_repo.get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    if run.get("status") == "finished":
        return run_detail(conn, run_id)

    cases = EvalCaseRepository(conn).list_active(int(run["suite_id"]))
    if not cases:
        raise AppError(ErrorCode.PARAM_ERROR, "评测集没有可用用例")

    mapping = _index_outputs(outputs)
    started = utcnow_iso()
    token = hashlib.sha256(f"{run_id}:{started}".encode("utf-8")).hexdigest()[:24]
    conn.execute(
        "UPDATE biz_eval_run SET status = 'running', worker_token = ?, started_at = ?, "
        "heartbeat_at = ?, lease_expires_at = ? WHERE id = ?",
        (token, started, started, started, run_id),
    )

    result_repo = EvalResultRepository(conn)
    results: list[dict[str, Any]] = []
    total_cost = 0
    for case in cases:
        entry = mapping.get(str(case.get("case_no"))) or mapping.get(str(case.get("id")))
        if not entry:
            verdict = "skip"
            reason = "no_output"
            output_text = ""
            latency_ms = 0
            cost_micros = 0
        else:
            output_text = str(entry.get("output") or "")
            latency_ms = int(entry.get("latencyMs") or 0)
            cost_micros = int(entry.get("costMicros") or 0)
            reasons = evaluate_assertions(output_text, loads(case.get("assertions_json"), {}) or {})
            verdict = "fail" if reasons else "pass"
            reason = reasons[0] if reasons else ""
        result_repo.insert(
            {
                "run_id": run_id,
                "case_id": int(case["id"]),
                "case_no": str(case["case_no"]),
                "output_text": output_text[:4000],
                "verdict": verdict,
                "failure_reason": reason,
                "latency_ms": latency_ms,
                "cost_micros": cost_micros,
                "cost_source": "estimated",
            }
        )
        results.append(
            {
                "case_id": int(case["id"]),
                "verdict": verdict,
                "failure_reason": reason,
                "latency_ms": latency_ms,
                "cost_micros": cost_micros,
            }
        )
        total_cost += cost_micros

    counts = {"pass": 0, "fail": 0, "skip": 0, "error": 0, "invalid": 0}
    for item in results:
        counts[item["verdict"]] = counts.get(item["verdict"], 0) + 1

    cases_by_id = {int(case["id"]): case for case in cases}
    metric_rows = compute_metrics(cases_by_id, results)
    metric_repo = EvalMetricRepository(conn)
    stored = []
    for row in metric_rows:
        stored.append(
            {
                **row,
                "run_id": run_id,
                "detail_json": dumps(row.get("detail_json") or {}),
            }
        )
    metric_repo.replace_all(run_id, stored)

    valid = len(results) - counts["error"] - counts["skip"] - counts["invalid"]
    finished = utcnow_iso()
    conn.execute(
        "UPDATE biz_eval_run SET status = 'finished', finished_at = ?, heartbeat_at = ?, "
        "passed = ?, failed = ?, skipped = ?, errors = ?, judge_invalid = ?, success_rate = ?, "
        "cost_micros = ?, duration_ms = ?, worker_token = '', lease_expires_at = NULL WHERE id = ?",
        (
            finished,
            finished,
            counts["pass"],
            counts["fail"],
            counts["skip"],
            counts["error"],
            counts["invalid"],
            (counts["pass"] / valid) if valid > 0 else None,
            total_cost,
            sum(int(item["latency_ms"]) for item in results),
            run_id,
        ),
    )
    conn.commit()
    return run_detail(conn, run_id)


# ---------------------------------------------------------------- 门禁与报告


def evaluate_run_gate(
    conn: sqlite3.Connection,
    run_id: int,
    thresholds: dict[str, Any] | None = None,
    gate_id: int | None = None,
) -> dict[str, Any]:
    metric_repo = EvalMetricRepository(conn)
    metrics = metric_repo.map_by_run(run_id)
    if not metrics:
        raise AppError(ErrorCode.PARAM_ERROR, "跑批还没有指标，先执行判定")

    gate_row = None
    if gate_id is not None:
        gate_row = conn.execute("SELECT * FROM biz_eval_gate WHERE id = ?", (gate_id,)).fetchone()
        if gate_row is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评测门禁不存在")
        gate_row = dict(gate_row)
        thresholds = loads(gate_row.get("thresholds_json"), {}) or thresholds

    result = evaluate_gate(metrics, thresholds)
    conn.execute(
        "UPDATE biz_eval_run SET gate_conclusion = ? WHERE id = ?",
        (result["conclusion"], run_id),
    )

    if gate_row is not None:
        snapshot = {key: {"value": row.get("value"), "ci_lower": row.get("ci_lower"), "ci_upper": row.get("ci_upper")}
                    for key, row in metrics.items()}
        conn.execute(
            "INSERT INTO biz_eval_gate_result (gate_id, run_id, conclusion, dimension, failures_json, ci_snapshot, created_at) "
            "VALUES (?, ?, ?, 'eval', ?, ?, ?)",
            (
                int(gate_row["id"]),
                run_id,
                result["conclusion"],
                dumps(result["failures"]),
                dumps(snapshot),
                utcnow_iso(),
            ),
        )
        conn.execute(
            "UPDATE biz_eval_gate SET last_run_id = ?, last_conclusion = ?, last_evaluated_at = ? WHERE id = ?",
            (run_id, result["conclusion"], utcnow_iso(), int(gate_row["id"])),
        )
    conn.commit()
    return result


def create_gate(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    suite_id = _int(payload.get("suiteId"), "suiteId")
    suite = _suite_or_404(conn, suite_id)
    name = str(payload.get("name") or f"{suite['name']}-门禁").strip()
    thresholds = payload.get("thresholds") or {}
    if not isinstance(thresholds, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "thresholds 必须是对象")
    cursor = conn.execute(
        "INSERT INTO biz_eval_gate (project_id, suite_id, name, thresholds_json, is_hard, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            int(suite["project_id"]),
            suite_id,
            name,
            dumps(thresholds),
            1 if payload.get("isHard", True) else 0,
            utcnow_iso(),
            utcnow_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def run_detail(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    run_repo = EvalRunRepository(conn)
    run = run_repo.get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    metrics = EvalMetricRepository(conn).map_by_run(run_id)
    results = EvalResultRepository(conn).list_by_run(run_id)
    return {
        "run": {**run, "successRate": run.get("success_rate")},
        "metrics": metrics,
        "counts": EvalResultRepository(conn).verdict_counts(run_id),
        "results": results,
    }


def run_report(conn: sqlite3.Connection, run_id: int) -> str:
    detail = run_detail(conn, run_id)
    run = detail["run"]
    metrics = detail["metrics"]
    results = detail["results"]

    lines = [
        f"# 评测报告 · run #{run_id}",
        "",
        f"- 评测集：#{run['suite_id']}（版本 {run.get('suite_version')}）",
        f"- 用例集哈希：`{run.get('case_set_hash') or ''}`",
        f"- 状态：{run.get('status')} ｜ 用例 {run.get('total')} ｜ 通过 {run.get('passed')} ｜ "
        f"失败 {run.get('failed')} ｜ 跳过 {run.get('skipped')} ｜ 错误 {run.get('errors')} ｜ 判定失效 {run.get('judge_invalid')}",
        f"- 门禁结论：{run.get('gate_conclusion') or '未求值'}",
        "",
        "## 指标（95% Wilson 区间）",
        "",
        "| 指标 | 值 | 95% 区间 | 样本量 |",
        "|---|---|---|---|",
    ]
    for key, row in sorted(metrics.items()):
        value = row.get("value")
        lower, upper = row.get("ci_lower"), row.get("ci_upper")
        big = f"{float(value):.3f}" if value is not None else "-"
        ci = f"[{float(lower):.3f}, {float(upper):.3f}]" if lower is not None and upper is not None else "-"
        lines.append(f"| {key} | {big} | {ci} | {row.get('sample_size')} |")

    sample = int(metrics.get("success_rate", {}).get("sample_size") or 0)
    if sample < 30:
        lines += ["", f"> **样本量 {sample} < 30：只出逐条证据，不出成败结论。**"]

    lines += ["", "## 逐条结果", "", "| 用例 | 级别 | 判定 | 失败原因 | 时延(ms) |", "|---|---|---|---|---|"]
    for item in results:
        lines.append(
            f"| {item.get('case_no')} | - | {item.get('verdict')} | "
            f"{item.get('failure_reason') or '-'} | {item.get('latency_ms')} |"
        )
    return "\n".join(lines) + "\n"
