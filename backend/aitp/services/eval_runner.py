"""Eval 跑批执行器（设计 §九 生命周期闭环）。

职责：判定链执行、单条隔离、心跳节流、reaper 僵死回收（worker_token fencing）、
断点续跑、取消、预算前置拦截、入库前脱敏。回放模式下模型输出由调用方传入，
真实模型接入走同一套判定链，保证门禁/E2E 零 token 可测。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
from pathlib import Path
from typing import Any

from ..common.pii import mask_sensitive
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..eval.cases import evaluate_assertions
from ..eval.judges import parse_judge_verdict
from ..eval.metrics import compute_metrics
from ..eval.rubric import score_rubric, validate_rubric
from ..eval.traces import evaluate_end_state, evaluate_trace
from ..repositories.base import dumps, loads
from ..repositories.eval_case_repo import EvalCaseRepository
from ..repositories.eval_metric_repo import EvalMetricRepository
from ..repositories.eval_result_repo import EvalResultRepository
from ..repositories.eval_run_repo import EvalRunRepository
from ..repositories.eval_suite_repo import EvalSuiteRepository

#: 心跳写库节流（秒）：不是每条都写，避免写热点
HEARTBEAT_THROTTLE_SECONDS = 10.0
#: 心跳超时（秒）：running 且超过该时长无心跳即判僵死
STALE_TIMEOUT_SECONDS = 300.0
#: 输出落库前的脱敏正则（密钥 / PII）
_SENSITIVE_PATTERNS = (
    re.compile(r"sk-[A-Za-z0-9\-_]{12,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"\b\d{13,19}\b"),
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"),
)
REDACTION = "[REDACTED]"

#: 跑批状态机
TERMINAL_STATUSES = {"finished", "failed", "cancelled"}


def open_connection(db_path: str | Path) -> sqlite3.Connection:
    """后台执行器独立连接：不能用请求级 get_db()。"""

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def redact_sensitive(text: str) -> str:
    """入库/外发前脱敏：先走公共 PII 规则（与 AI 外发同一套），再补评测特有密钥形态。"""

    redacted = mask_sensitive(text or "")
    for pattern in _SENSITIVE_PATTERNS:
        redacted = pattern.sub(REDACTION, redacted)
    return redacted


def _parse_case(case: dict[str, Any]) -> dict[str, Any]:
    return {
        **case,
        "assertions": loads(case.get("assertions_json"), {}) or {},
        "rubric": loads(case.get("rubric_json"), []) or [],
        "tools_expected": loads(case.get("tools_expected"), {}) or {},
        "relevant_docs": loads(case.get("relevant_docs"), []) or [],
        "graded_relevance": loads(case.get("graded_relevance"), {}) or {},
    }


def judge_case(case: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
    """单条用例判定（设计 §6.1 判定链），返回结果列与指标富字段。"""

    parsed_case = _parse_case(case)
    output = str(entry.get("output") or "")
    output = redact_sensitive(output)[:4000]
    assertions = parsed_case["assertions"]

    # 基础设施错误：模型不可用/超时，记 error 而不是 fail
    if entry.get("error"):
        return {
            "verdict": "error",
            "judge_valid": 1,
            "failure_reason": str(entry.get("error")),
            "output_text": output,
            "score": None,
        }

    reasons = list(evaluate_assertions(output, assertions))

    # Agent 端态断言：文本再漂亮，端态不符也是 fail
    raw_end_state = entry.get("endState")
    end_state: dict[str, Any] = raw_end_state if isinstance(raw_end_state, dict) else {}
    if assertions.get("end_state"):
        end_result = evaluate_end_state(assertions["end_state"], end_state)
        reasons.extend(end_result["reasons"])

    # 工具轨迹裁决
    raw_trace = entry.get("toolTrace")
    tool_trace: list[dict[str, Any]] = raw_trace if isinstance(raw_trace, list) else []
    trace_result = evaluate_trace(
        parsed_case["tools_expected"], tool_trace, int(entry.get("steps") or 0),
        assertions.get("max_steps"),
    )
    reasons.extend(trace_result["reasons"])

    # Rubric 软裁决
    rubric_detail: dict[str, Any] = {}
    rubric_score100: float | None = None
    dimension_scores = entry.get("dimensionScores")
    if parsed_case["rubric"] and isinstance(dimension_scores, dict):
        rubric = validate_rubric(parsed_case["rubric"])
        rubric_result = score_rubric(rubric, dimension_scores)
        rubric_score100 = rubric_result["score"]
        rubric_detail = {"rubric": rubric_result, "instructionFollowed": None}
        for dim in rubric_result.get("dimensions", []):
            if dim.get("key") == "instruction":
                rubric_detail["instructionFollowed"] = dim.get("normalized", 0) >= 0.6
        reasons.extend(rubric_result.get("reasons", []))

    # judge 仲裁（可选）：不可解析 → invalid，剔除分母，不判 fail
    judge_valid = 1
    judge_json: dict[str, Any] = {}
    judge_label: str | None = None
    hallucinated = bool(entry.get("hallucinated"))
    if entry.get("judgeRaw") is not None:
        parsed_judge = parse_judge_verdict(entry.get("judgeRaw"))
        if parsed_judge is None:
            return {
                "verdict": "invalid",
                "judge_valid": 0,
                "failure_reason": "judge.unparseable",
                "output_text": output,
                "score": None,
                "tool_trace": tool_trace,
                "retrieved": entry.get("retrieved") or [],
                "end_state": end_state,
                "steps": int(entry.get("steps") or 0),
            }
        judge_label = parsed_judge["verdict"]
        hallucinated = hallucinated or bool(parsed_judge.get("hallucinated"))
        judge_json = parsed_judge
        if parsed_judge["verdict"] == "fail":
            reasons.append("judge.arbitration")

    verdict = "fail" if reasons else "pass"
    rule_score = 0.0 if reasons else 100.0
    score: float | None = rule_score
    if rubric_score100 is not None:
        score = round(0.6 * rule_score + 0.4 * rubric_score100, 4) if not reasons else 0.0

    return {
        "verdict": verdict,
        "judge_valid": judge_valid,
        "failure_reason": reasons[0] if reasons else "",
        "output_text": output,
        "score": score,
        "tool_trace": tool_trace,
        "retrieved": entry.get("retrieved") or [],
        "end_state": end_state,
        "steps": int(entry.get("steps") or 0),
        "claims": entry.get("claims") or [],
        "rubric": rubric_detail,
        "judge_label": judge_label,
        "hallucinated": hallucinated,
        "trace": trace_result,
    }


def _check_budget(run: dict[str, Any], outputs: dict[str, dict[str, Any]], budget: dict[str, Any]) -> None:
    max_cases = budget.get("maxCases")
    if max_cases is not None and int(run["total"]) > int(max_cases):
        raise AppError(ErrorCode.EVAL_BUDGET_EXCEEDED, f"用例数 {run['total']} 超预算上限 {max_cases}")
    max_tokens = budget.get("maxTokens")
    if max_tokens is not None:
        tokens = sum(int(item.get("tokensIn") or 0) + int(item.get("tokensOut") or 0) for item in outputs.values())
        if tokens > int(max_tokens):
            raise AppError(ErrorCode.EVAL_BUDGET_EXCEEDED, f"token 预估 {tokens} 超预算上限 {max_tokens}")
    max_cost = budget.get("maxCostMicros")
    if max_cost is not None:
        cost = sum(int(item.get("costMicros") or 0) for item in outputs.values())
        if cost > int(max_cost):
            raise AppError(ErrorCode.EVAL_BUDGET_EXCEEDED, f"成本预估 {cost} 微币超预算上限 {max_cost}")


def _heartbeat(conn: sqlite3.Connection, run_id: int, token: str, now: str) -> None:
    conn.execute(
        "UPDATE biz_eval_run SET heartbeat_at = ?, lease_expires_at = ? "
        "WHERE id = ? AND worker_token = ? AND status = 'running'",
        (now, now, run_id, token),
    )


def _enrich_stored_row(row: dict[str, Any]) -> dict[str, Any]:
    """把落库结果行还原成 compute_metrics 需要的富字段形态。"""

    judge_json = loads(row.get("judge_json"), {}) or {}
    return {
        "case_id": int(row["case_id"]),
        "verdict": row["verdict"],
        "failure_reason": row.get("failure_reason") or "",
        "latency_ms": row.get("latency_ms") or 0,
        "cost_micros": row.get("cost_micros") or 0,
        "cost_source": row.get("cost_source") or "estimated",
        "steps": row.get("steps") or 0,
        "tool_trace": loads(row.get("tool_trace"), []) or [],
        "retrieved": loads(row.get("retrieved_json"), []) or [],
        "claims": judge_json.get("claims") or [],
        "rubric": judge_json.get("rubric") or {},
        "judge_label": judge_json.get("judge_label"),
        "hallucinated": bool(judge_json.get("hallucinated")),
        "human": row.get("human_verdict"),
    }


def _finalize(conn: sqlite3.Connection, run_id: int, token: str, cancelled: bool) -> dict[str, Any]:
    """重算指标与聚合计数，收尾 run（fencing 条件更新）。"""

    result_repo = EvalResultRepository(conn)
    stored = result_repo.list_by_run(run_id)
    run_row = EvalRunRepository(conn).get(run_id)
    if run_row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    cases = EvalCaseRepository(conn).list_active(int(run_row["suite_id"]))
    cases_by_id = {int(case["id"]): _parse_case(case) for case in cases}
    enriched = [_enrich_stored_row(dict(row)) for row in stored]
    metric_rows = compute_metrics(cases_by_id, enriched)
    serialized = [{**row, "run_id": run_id, "detail_json": dumps(row.get("detail_json") or {})} for row in metric_rows]
    EvalMetricRepository(conn).replace_all(run_id, serialized)

    counts = result_repo.verdict_counts(run_id)
    passed = counts.get("pass", 0)
    failed = counts.get("fail", 0)
    skipped = counts.get("skip", 0)
    errors = counts.get("error", 0)
    invalid = counts.get("invalid", 0)
    valid = len(stored) - errors - skipped - invalid
    total_cost = sum(int(row.get("cost_micros") or 0) for row in stored)
    duration = sum(int(row.get("latency_ms") or 0) for row in stored)
    finished = utcnow_iso()
    conn.execute(
        "UPDATE biz_eval_run SET status = ?, finished_at = ?, heartbeat_at = ?, passed = ?, failed = ?, "
        "skipped = ?, errors = ?, judge_invalid = ?, success_rate = ?, cost_micros = ?, duration_ms = ?, "
        "worker_token = '', lease_expires_at = NULL, cancel_requested = 0 "
        "WHERE id = ? AND worker_token = ?",
        (
            "cancelled" if cancelled else "finished", finished, finished,
            passed, failed, skipped, errors, invalid,
            (passed / valid) if valid > 0 else None,
            total_cost, duration, run_id, token,
        ),
    )
    conn.commit()
    from . import eval_service  # 延迟导入避免循环依赖

    return eval_service.run_detail(conn, run_id)


def execute_run(
    conn: sqlite3.Connection,
    run_id: int,
    outputs: Any,
    budget: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """执行跑批（回放模式）：单条隔离 + 心跳 + 取消检查 + 预算拦截。"""

    run_repo = EvalRunRepository(conn)
    run = run_repo.get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    if run.get("status") == "finished":
        from . import eval_service

        return eval_service.run_detail(conn, run_id)

    mapping = _index_outputs(outputs)
    _check_budget(run, mapping, budget or {})

    cases = EvalCaseRepository(conn).list_active(int(run["suite_id"]))
    if not cases:
        raise AppError(ErrorCode.PARAM_ERROR, "评测集没有可用用例")

    started = utcnow_iso()
    token = hashlib.sha256(f"{run_id}:{started}:{time.time_ns()}".encode("utf-8")).hexdigest()[:24]
    conn.execute(
        "UPDATE biz_eval_run SET status = 'running', worker_token = ?, started_at = COALESCE(started_at, ?), "
        "heartbeat_at = ?, lease_expires_at = ?, last_error = '' WHERE id = ?",
        (token, started, started, started, run_id),
    )
    conn.commit()

    result_repo = EvalResultRepository(conn)
    existing = {int(row["case_id"]) for row in result_repo.raw(
        "SELECT case_id FROM biz_eval_result WHERE run_id = ?", (run_id,))}
    last_beat = time.monotonic()
    cancelled = False

    for case in cases:
        if int(case["id"]) in existing:
            continue
        current = run_repo.get(run_id)
        if current and int(current.get("cancel_requested") or 0) == 1:
            cancelled = True
            break
        entry = mapping.get(str(case.get("case_no"))) or mapping.get(str(case.get("id")))
        judged: dict[str, Any]
        if not entry:
            judged = {"verdict": "skip", "judge_valid": 1, "failure_reason": "no_output",
                      "output_text": "", "score": None, "tool_trace": [], "retrieved": [],
                      "end_state": {}, "steps": 0, "claims": [], "rubric": {}}
        else:
            try:
                judged = judge_case(dict(case), entry)
            except Exception as exc:  # noqa: BLE001 - 单条异常不拖垮整批
                judged = {"verdict": "error", "judge_valid": 1,
                          "failure_reason": f"infra.model_unavailable:{type(exc).__name__}",
                          "output_text": "", "score": None, "tool_trace": [], "retrieved": [],
                          "end_state": {}, "steps": 0, "claims": [], "rubric": {}}
        now = time.monotonic()
        if now - last_beat >= HEARTBEAT_THROTTLE_SECONDS:
            _heartbeat(conn, run_id, token, utcnow_iso())
            last_beat = now
        judge_json = {
            "claims": judged.get("claims") or [],
            "rubric": judged.get("rubric") or {},
            "judge_label": judged.get("judge_label"),
            "hallucinated": judged.get("hallucinated", False),
            "trace": judged.get("trace") or {},
        }
        result_repo.insert(
            {
                "run_id": run_id,
                "case_id": int(case["id"]),
                "case_no": str(case["case_no"]),
                "output_text": judged.get("output_text", ""),
                "tool_trace": dumps(judged.get("tool_trace") or []),
                "retrieved_json": dumps(judged.get("retrieved") or []),
                "end_state": dumps(judged.get("end_state") or {}),
                "attempt_count": int((entry or {}).get("attemptCount") or 1),
                "fallback_used": 1 if (entry or {}).get("fallbackUsed") else 0,
                "steps": int(judged.get("steps") or 0),
                "verdict": judged["verdict"],
                "judge_valid": judged["judge_valid"],
                "score": judged.get("score"),
                "failure_reason": judged.get("failure_reason", ""),
                "judge_json": dumps(judge_json),
                "latency_ms": int((entry or {}).get("latencyMs") or 0),
                "tokens_in": int((entry or {}).get("tokensIn") or 0),
                "tokens_out": int((entry or {}).get("tokensOut") or 0),
                "cost_micros": int((entry or {}).get("costMicros") or 0),
                "cost_source": str((entry or {}).get("costSource") or "estimated"),
            }
        )
        conn.commit()
    return _finalize(conn, run_id, token, cancelled)


def resume_run(conn: sqlite3.Connection, run_id: int, outputs: Any = None) -> dict[str, Any]:
    """断点续跑：只补没有 result 的用例，已写入的结果绝不重跑。"""

    run = EvalRunRepository(conn).get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    if run.get("status") == "finished":
        from . import eval_service

        return eval_service.run_detail(conn, run_id)
    if run.get("status") == "running":
        raise AppError(ErrorCode.ILLEGAL_TRANSITION, "跑批执行中，请先取消或等待 reaper 回收")
    return execute_run(conn, run_id, outputs)


def request_cancel(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """取消（幂等收尾）：queued 直接取消；running 置标记由 worker 收尾。"""

    run = EvalRunRepository(conn).get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    status = str(run.get("status"))
    if status in TERMINAL_STATUSES:
        return {"id": run_id, "status": status, "idempotent": True}
    if status == "queued":
        conn.execute(
            "UPDATE biz_eval_run SET status = 'cancelled', finished_at = ?, heartbeat_at = ?, "
            "worker_token = '', lease_expires_at = NULL WHERE id = ?",
            (utcnow_iso(), utcnow_iso(), run_id),
        )
    else:
        conn.execute(
            "UPDATE biz_eval_run SET cancel_requested = 1 WHERE id = ? AND status = 'running'",
            (run_id,),
        )
    conn.commit()
    return {"id": run_id, "status": "cancelled" if status == "queued" else "cancelling", "idempotent": False}


def reap_stale_runs(
    conn: sqlite3.Connection, timeout_seconds: float = STALE_TIMEOUT_SECONDS, now_iso: str | None = None
) -> list[int]:
    """reaper：running 且心跳超时的 run 置 failed（infra.stale_run），清空令牌做 fencing。"""

    cutoff = _shift_iso(now_iso or utcnow_iso(), -int(timeout_seconds))
    rows = conn.execute(
        "SELECT id FROM biz_eval_run WHERE status = 'running' AND "
        "(heartbeat_at IS NULL OR heartbeat_at < ?)",
        (cutoff,),
    ).fetchall()
    reaped = [int(row["id"]) for row in rows]
    finished = utcnow_iso()
    for run_id in reaped:
        conn.execute(
            "UPDATE biz_eval_run SET status = 'failed', finished_at = ?, heartbeat_at = ?, "
            "worker_token = '', lease_expires_at = NULL, last_error = 'infra.stale_run' "
            "WHERE id = ? AND status = 'running'",
            (finished, finished, run_id),
        )
    if reaped:
        conn.commit()
    return reaped


def _shift_iso(iso_text: str, seconds: int) -> str:
    """ISO8601 UTC 字符串平移；兼容带/不带毫秒两种格式。"""

    from datetime import datetime, timedelta, timezone

    text = iso_text.replace("Z", "+00:00")
    try:
        moment = datetime.fromisoformat(text)
    except ValueError:
        return iso_text
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return (moment + timedelta(seconds=seconds)).astimezone(timezone.utc).isoformat()


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


def recompute_run_metrics(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    """人评回填等场景下只重算指标，不动 run 状态与逐条结论。"""

    run = EvalRunRepository(conn).get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    stored = EvalResultRepository(conn).list_by_run(run_id)
    cases = EvalCaseRepository(conn).list_active(int(run["suite_id"]))
    cases_by_id = {int(case["id"]): _parse_case(case) for case in cases}
    enriched = [_enrich_stored_row(row) for row in stored]
    metric_rows = compute_metrics(cases_by_id, enriched)
    serialized = [{**row, "run_id": run_id, "detail_json": dumps(row.get("detail_json") or {})} for row in metric_rows]
    EvalMetricRepository(conn).replace_all(run_id, serialized)
    conn.commit()
    return EvalMetricRepository(conn).map_by_run(run_id)


def suite_has_runs(conn: sqlite3.Connection, suite_id: int) -> bool:
    row = conn.execute(
        "SELECT COUNT(*) AS total FROM biz_eval_run WHERE suite_id = ?", (suite_id,)
    ).fetchone()
    return int(row["total"]) > 0


def case_is_frozen(conn: sqlite3.Connection, case_id: int) -> bool:
    """已进入任何跑批结果的用例冻结，禁止编辑/删除（设计：冻结跑批内只读）。"""

    row = conn.execute(
        "SELECT COUNT(*) AS total FROM biz_eval_result WHERE case_id = ?", (case_id,)
    ).fetchone()
    return int(row["total"]) > 0


def suite_visibility(conn: sqlite3.Connection, suite_id: int) -> str:
    suite = EvalSuiteRepository(conn).get(suite_id)
    return str(suite.get("visibility")) if suite else "private"
