"""评测 API：/api/v1/evals。

MVP 切片：评测集/用例 CRUD + 回放跑批 + 指标 + 门禁 + 报告。
真实模型调用与 judge 在后续阶段接（回放评测先让判定链、指标与门禁跑通）。
"""

from __future__ import annotations

from flask import Blueprint, Response

from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.eval_case_repo import EvalCaseRepository, case_summary
from ..repositories.eval_run_repo import EvalRunRepository
from ..repositories.eval_suite_repo import EvalSuiteRepository, suite_summary
from ..services import eval_service
from .helpers import audit, body, commit, db, keyword_of, page_args, page_payload, path_int, query_filters

bp = Blueprint("evals", __name__)

SUITE_FILTERS = {
    "projectId": "project_id",
    "status": "status",
    "visibility": "visibility",
    "targetType": "target_type",
}
RUN_FILTERS = {
    "projectId": "project_id",
    "suiteId": "suite_id",
    "status": "status",
    "gateConclusion": "gate_conclusion",
}


@bp.get("")
def list_suites() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = EvalSuiteRepository(conn)
    page = repo.list(query_filters(SUITE_FILTERS), keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [suite_summary(row) for row in payload["list"]]
    return ok(payload)


@bp.post("")
def create_suite() -> Response:
    conn = db()
    suite_id = eval_service.create_suite(conn, body())
    audit(conn, "eval.suite.create", "eval_suite", suite_id, detail={"name": body().get("name")})
    return ok({"id": suite_id})


@bp.get("/<int:pk>")
def get_suite(pk: int) -> Response:
    conn = db()
    suite = eval_service.suite_detail(conn, pk)
    return ok(suite_summary(suite) | {"categories": suite["categories"], "recentRuns": suite["recentRuns"]})


@bp.get("/<int:pk>/cases")
def list_cases(pk: int) -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = EvalCaseRepository(conn)
    page = repo.list({"suite_id": pk}, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = [case_summary(row) for row in payload["list"]]
    return ok(payload)


@bp.post("/<int:pk>/cases")
def create_case(pk: int) -> Response:
    conn = db()
    data = body()
    data["suiteId"] = pk
    case_id = eval_service.add_case(conn, data)
    audit(conn, "eval.case.create", "eval_case", case_id, detail={"suiteId": pk})
    return ok({"id": case_id})


@bp.get("/runs")
def list_runs() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = EvalRunRepository(conn)
    page = repo.list(query_filters(RUN_FILTERS), keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    run_repo = EvalRunRepository(conn)
    payload["list"] = [run_repo.summary(int(row["id"])) for row in payload["list"]]
    return ok(payload)


@bp.post("/runs")
def create_run() -> Response:
    conn = db()
    result = eval_service.create_run(conn, body())
    audit(conn, "eval.run.create", "eval_run", result["runId"], detail={"reused": result["reused"]})
    return ok(result)


@bp.get("/runs/<int:pk>")
def get_run(pk: int) -> Response:
    conn = db()
    detail = eval_service.run_detail(conn, pk)
    return ok(
        {
            "run": EvalRunRepository(conn).summary(pk),
            "metrics": detail["metrics"],
            "counts": detail["counts"],
            "results": detail["results"],
        }
    )


@bp.get("/runs/<int:pk>/results")
def get_run_results(pk: int) -> Response:
    conn = db()
    detail = eval_service.run_detail(conn, pk)
    return ok({"list": detail["results"], "counts": detail["counts"]})


@bp.post("/runs/<int:pk>/execute")
def execute_run(pk: int) -> Response:
    conn = db()
    payload = body()
    detail = eval_service.execute_replay(conn, pk, payload.get("outputs"))
    audit(conn, "eval.run.execute", "eval_run", pk, detail={"total": detail["run"].get("total")})
    return ok({"run": EvalRunRepository(conn).summary(pk), "metrics": detail["metrics"], "counts": detail["counts"]})


@bp.post("/runs/<int:pk>/gate")
def evaluate_gate_route(pk: int) -> Response:
    conn = db()
    payload = body()
    gate_id = payload.get("gateId")
    result = eval_service.evaluate_run_gate(
        conn,
        pk,
        thresholds=payload.get("thresholds") if isinstance(payload.get("thresholds"), dict) else None,
        gate_id=path_int(gate_id) if gate_id not in (None, "") else None,
    )
    audit(conn, "eval.run.gate", "eval_run", pk, detail={"conclusion": result["conclusion"]})
    return ok(result)


@bp.post("/gates")
def create_gate() -> Response:
    conn = db()
    gate_id = eval_service.create_gate(conn, body())
    return ok({"id": gate_id})


@bp.get("/runs/<int:pk>/report")
def run_report(pk: int) -> Response:
    conn = db()
    return ok({"markdown": eval_service.run_report(conn, pk)})
