"""评测 API：/api/v1/evals（设计 §四：32 个接口条目 / 34 条路由）。

P1：评测集/用例 CRUD 与导入、配置、跑批（幂等/续跑/取消/人评）、门禁求值、导出、
字典；P2：复制评测集、badcase 回流、质量指标、消融对比、趋势、判定器校准、门禁管理。
"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.eval_case_repo import EvalCaseRepository, case_detail, case_summary
from ..repositories.eval_config_repo import EvalConfigRepository, config_summary
from ..repositories.eval_gate_repo import EvalGateRepository, gate_summary
from ..repositories.eval_judge_repo import EvalJudgeRepository, judge_summary
from ..repositories.eval_result_repo import EvalResultRepository
from ..repositories.eval_run_repo import EvalRunRepository, run_brief
from ..repositories.eval_suite_repo import EvalSuiteRepository, suite_summary
from ..services import eval_calibration, eval_service
from .helpers import (
    audit,
    body,
    db,
    download_response,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
)

bp = Blueprint("evals", __name__)

SUITE_FILTERS = {
    "projectId": "project_id",
    "status": "status",
    "visibility": "visibility",
    "targetType": "target_type",
}
CASE_FILTERS = {
    "category": "category",
    "severity": "severity",
    "status": "status",
}
RUN_FILTERS = {
    "projectId": "project_id",
    "suiteId": "suite_id",
    "status": "status",
    "gateConclusion": "gate_conclusion",
    "triggerType": "trigger_type",
}
RESULT_FILTERS = {"verdict": "verdict"}


def _int_arg(value: object, field: str, default: int | None = None) -> int:
    if value in (None, ""):
        if default is None:
            raise AppError(ErrorCode.PARAM_ERROR, f"缺少参数 {field}")
        return default
    try:
        return int(str(value))
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是整数") from None


# ---------------------------------------------------------------- 评测集


@bp.get("/suites")
def list_suites() -> Response:
    conn = db()
    page_num, page_size = page_args()
    page = EvalSuiteRepository(conn).list(
        query_filters(SUITE_FILTERS), keyword_of() or None, page_num=page_num, page_size=page_size
    )
    payload = page_payload(page)
    rows = payload["list"]
    latest = EvalRunRepository(conn).latest_by_suites([int(row["id"]) for row in rows])
    payload["list"] = [
        suite_summary(row) | {"latestRun": run_brief(latest.get(int(row["id"])))} for row in rows
    ]
    return ok(payload)


@bp.post("/suites")
def create_suite() -> Response:
    conn = db()
    data = body()
    suite_id = eval_service.create_suite(conn, data)
    audit(conn, "eval.suite.create", "eval_suite", suite_id, detail={"name": data.get("name")})
    return ok({"id": suite_id})


@bp.get("/suites/<int:pk>")
def get_suite(pk: int) -> Response:
    conn = db()
    detail = eval_service.suite_detail(conn, pk)
    return ok(
        suite_summary(detail)
        | {
            "caseCount": detail["caseCount"],
            "categories": detail["categories"],
            "recentRuns": [EvalRunRepository(conn).summary(int(row["id"])) for row in detail["recentRuns"]],
            "trend": detail["trend"],
        }
    )


@bp.put("/suites/<int:pk>")
def update_suite(pk: int) -> Response:
    conn = db()
    detail = eval_service.update_suite(conn, pk, body())
    audit(conn, "eval.suite.update", "eval_suite", pk, resource_label=str(detail.get("name") or ""))
    return ok(suite_summary(detail) | {"caseCount": detail["caseCount"]})


@bp.delete("/suites/<int:pk>")
def delete_suite(pk: int) -> Response:
    conn = db()
    result = eval_service.delete_suite(conn, pk)
    audit(conn, "eval.suite.delete", "eval_suite", pk)
    return ok(result)


@bp.post("/suites/<int:pk>/duplicate")
def duplicate_suite(pk: int) -> Response:
    conn = db()
    result = eval_service.duplicate_suite(conn, pk, body())
    audit(conn, "eval.suite.duplicate", "eval_suite", int(result["id"]), detail={"sourceSuiteId": pk})
    return ok(result)


@bp.get("/suites/<int:pk>/quality")
def suite_quality(pk: int) -> Response:
    conn = db()
    return ok(eval_service.suite_quality(conn, pk))


# ---------------------------------------------------------------- 用例


@bp.get("/suites/<int:pk>/cases")
def list_cases(pk: int) -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = {"suite_id": pk, **query_filters(CASE_FILTERS)}
    page = EvalCaseRepository(conn).list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    with_detail = (request.args.get("detail") or "").strip().lower() in {"1", "true", "yes"}
    payload["list"] = [
        case_detail(row) if with_detail else case_summary(row) for row in payload["list"]
    ]
    return ok(payload)


@bp.post("/suites/<int:pk>/cases")
def create_case(pk: int) -> Response:
    conn = db()
    data = body()
    data["suiteId"] = pk
    case_id = eval_service.add_case(conn, data)
    audit(conn, "eval.case.create", "eval_case", case_id, detail={"suiteId": pk})
    return ok({"id": case_id})


@bp.put("/cases/<int:pk>")
def update_case(pk: int) -> Response:
    conn = db()
    result = eval_service.update_case(conn, pk, body())
    audit(conn, "eval.case.update", "eval_case", pk, resource_label=str(result.get("caseNo") or ""))
    return ok(result)


@bp.delete("/cases/<int:pk>")
def delete_case(pk: int) -> Response:
    conn = db()
    result = eval_service.delete_case(conn, pk)
    audit(conn, "eval.case.delete", "eval_case", pk)
    return ok(result)


@bp.post("/cases/import")
def import_cases() -> Response:
    conn = db()
    data = body()
    suite_id = path_int(data.get("suiteId"), "suiteId")
    result = eval_service.import_cases(conn, suite_id, data)
    if not result.get("dryRun"):
        audit(conn, "eval.case.import", "eval_suite", suite_id,
              detail={"accepted": result["accepted"], "skipped": result["skipped"]})
    return ok(result)


@bp.post("/cases/from-badcase")
def case_from_badcase() -> Response:
    conn = db()
    case_id = eval_service.case_from_badcase(conn, body())
    audit(conn, "eval.case.badcase", "eval_case", case_id)
    return ok({"id": case_id})


# ---------------------------------------------------------------- 配置


@bp.get("/configs")
def list_configs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = EvalConfigRepository(conn)
    filters = query_filters({"projectId": "project_id", "isBaseline": "is_baseline"})
    page = repo.list(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    items = []
    for row in payload["list"]:
        summary = config_summary(row)
        summary["referenced"] = repo.reference_count(int(row["id"])) > 0
        items.append(summary)
    payload["list"] = items
    return ok(payload)


@bp.post("/configs")
def create_config() -> Response:
    conn = db()
    data = body()
    config_id = eval_service.create_config(conn, data)
    audit(conn, "eval.config.create", "eval_config", config_id, detail={"name": data.get("name")})
    return ok({"id": config_id})


@bp.put("/configs/<int:pk>")
def update_config(pk: int) -> Response:
    conn = db()
    result = eval_service.update_config(conn, pk, body())
    audit(conn, "eval.config.update", "eval_config", pk)
    return ok(result)


# ---------------------------------------------------------------- 判定器


@bp.get("/judges")
def list_judges() -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = query_filters({"projectId": "project_id", "status": "status"})
    page = EvalJudgeRepository(conn).list(
        filters, keyword_of() or None, page_num=page_num, page_size=page_size
    )
    payload = page_payload(page)
    payload["list"] = [judge_summary(row) for row in payload["list"]]
    return ok(payload)


@bp.post("/judges/<int:pk>/calibrate")
def calibrate_judge(pk: int) -> Response:
    conn = db()
    data = body()
    run_id = path_int(data.get("runId"), "runId")
    items = data.get("items") or []
    if not isinstance(items, list):
        raise AppError(ErrorCode.PARAM_ERROR, "items 必须是数组")
    result = eval_calibration.calibrate(conn, pk, run_id, items, data.get("biasSamples"))
    audit(conn, "eval.judge.calibrate", "eval_judge", pk,
          detail={"runId": run_id, "sampleSize": result.get("sampleSize")})
    return ok(result)


@bp.get("/judges/<int:pk>/calibrations")
def judge_calibrations(pk: int) -> Response:
    conn = db()
    return ok({"list": eval_calibration.list_calibrations(conn, pk)})


# ---------------------------------------------------------------- 跑批


@bp.get("/runs")
def list_runs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = EvalRunRepository(conn)
    page = repo.list(query_filters(RUN_FILTERS), keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [repo.summary(int(row["id"])) for row in payload["list"]]
    return ok(payload)


@bp.post("/runs")
def create_run() -> Response:
    conn = db()
    result = eval_service.create_run(conn, body())
    audit(conn, "eval.run.create", "eval_run", int(result["runId"]), detail={"reused": result["reused"]})
    return ok(result)


@bp.get("/runs/<int:pk>")
def get_run(pk: int) -> Response:
    conn = db()
    detail = eval_service.run_detail(conn, pk)
    return ok(
        {
            "run": EvalRunRepository(conn).summary(pk),
            "metrics": detail["metrics"],
            "metricOrder": detail["metricOrder"],
            "counts": detail["counts"],
            "results": detail["results"],
        }
    )


@bp.post("/runs/<int:pk>/resume")
def resume_run(pk: int) -> Response:
    conn = db()
    payload = body()
    detail = eval_service.resume_run(conn, pk, payload.get("outputs"))
    audit(conn, "eval.run.resume", "eval_run", pk, detail={"status": detail["run"].get("status")})
    return ok(
        {
            "run": EvalRunRepository(conn).summary(pk),
            "metrics": detail["metrics"],
            "counts": detail["counts"],
        }
    )


@bp.post("/runs/<int:pk>/cancel")
def cancel_run(pk: int) -> Response:
    conn = db()
    result = eval_service.cancel_run(conn, pk)
    audit(conn, "eval.run.cancel", "eval_run", pk, detail={"status": result.get("status")})
    return ok(result)


@bp.get("/runs/<int:pk>/results")
def list_results(pk: int) -> Response:
    conn = db()
    page_num, page_size = page_args()
    filters = {"run_id": pk, **query_filters(RESULT_FILTERS)}
    page = EvalResultRepository(conn).list(
        filters, keyword_of() or None, page_num=page_num, page_size=page_size
    )
    payload = page_payload(page)
    return ok({"list": payload["list"], "total": payload["total"],
               "counts": EvalResultRepository(conn).verdict_counts(pk)})


@bp.post("/runs/<int:pk>/human-scores")
def human_scores(pk: int) -> Response:
    conn = db()
    result = eval_service.record_human_scores(conn, pk, body())
    audit(conn, "eval.run.humanScores", "eval_run", pk, detail={"updated": result["updated"]})
    return ok(result)


@bp.get("/runs/<int:pk>/export")
def export_run(pk: int) -> Response:
    conn = db()
    fmt = query_filters({"format": "format"}).get("format") or "md"
    result = eval_service.export_run(conn, pk, str(fmt))
    audit(conn, "eval.run.export", "eval_run", pk, detail={"format": result["format"]})
    return download_response(
        result["content"], result["filename"], result["mimetype"], count=int(result["count"])
    )


# ---------------------------------------------------------------- 对比 / 趋势 / 门禁 / 字典


@bp.get("/compare")
def compare_runs() -> Response:
    conn = db()
    params = query_filters({"baselineRunId": "baseline_run_id", "currentRunId": "current_run_id",
                            "alpha": "alpha"})
    baseline = _int_arg(params.get("baseline_run_id"), "baselineRunId")
    current = _int_arg(params.get("current_run_id"), "currentRunId")
    alpha_raw = params.get("alpha")
    try:
        alpha = float(str(alpha_raw)) if alpha_raw not in (None, "") else 0.05
    except ValueError:
        raise AppError(ErrorCode.PARAM_ERROR, "alpha 必须是数字") from None
    if not 0.0 < alpha < 1.0:
        raise AppError(ErrorCode.PARAM_ERROR, "alpha 取值必须在 (0, 1)")
    return ok(eval_service.compare_runs(conn, baseline, current, alpha=alpha))


@bp.get("/metrics/trends")
def metrics_trends() -> Response:
    conn = db()
    params = query_filters({"suiteId": "suite_id", "keys": "keys", "limit": "limit"})
    suite_id = _int_arg(params.get("suite_id"), "suiteId")
    raw_keys = str(params.get("keys") or "").strip()
    keys = [item for item in raw_keys.split(",") if item] if raw_keys else None
    limit = _int_arg(params.get("limit"), "limit", default=20)
    return ok(eval_service.metrics_trends(conn, suite_id, keys, limit=limit))


@bp.get("/gates")
def list_gates() -> Response:
    conn = db()
    filters = query_filters({"projectId": "project_id", "suiteId": "suite_id", "enabled": "enabled"})
    rows = EvalGateRepository(conn).list_all(filters, keyword_of() or None, limit=200)
    return ok({"list": [gate_summary(row) for row in rows]})


@bp.post("/gates")
def create_gate() -> Response:
    conn = db()
    data = body()
    gate_id = eval_service.create_gate(conn, data)
    audit(conn, "eval.gate.create", "eval_gate", gate_id, detail={"suiteId": data.get("suiteId")})
    return ok({"id": gate_id})


@bp.put("/gates")
def update_gate() -> Response:
    conn = db()
    data = body()
    gate_id = path_int(data.get("id"), "id")
    result = eval_service.update_gate(conn, gate_id, data)
    audit(conn, "eval.gate.update", "eval_gate", gate_id)
    return ok(result)


@bp.post("/gates/<int:pk>/evaluate")
def evaluate_gate_route(pk: int) -> Response:
    conn = db()
    data = body()
    run_id = data.get("runId")
    result = eval_service.evaluate_gate_by_id(
        conn, pk, path_int(run_id, "runId") if run_id not in (None, "") else None
    )
    audit(conn, "eval.gate.evaluate", "eval_gate", pk, detail={"conclusion": result["conclusion"]})
    return ok(result)


@bp.get("/meta")
def meta() -> Response:
    return ok(eval_service.meta_dictionary())