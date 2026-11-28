"""Eval 门禁与消融门禁（设计 §八 check_eval_gate，11 项断言）。

覆盖：阈值边界等号、区间下界/上界方向、三档结论、主指标与描述性指标、
McNemar 翻转、未达标清单、同组可比性、配置快照不可变、跨组拒绝、
导出字段一致、holdout 导出拒绝、CLI 退出码。全程临时库，零 token。

用法：python backend/scripts/check_eval_gate.py
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import subprocess
import sys
import tempfile
from collections.abc import Callable
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.eval.ablation import assert_comparable, paired_test  # noqa: E402
from aitp.eval.gate import evaluate_gate  # noqa: E402
from aitp.eval.metrics import METRIC_KEYS  # noqa: E402
from aitp.repositories.base import loads  # noqa: E402
from aitp.services import eval_service  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, func: Callable[[], None]) -> None:
    try:
        func()
    except Exception as exc:  # noqa: BLE001 - 门禁脚本需要把任何异常都记成失败
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
    else:
        RESULTS.append((name, True, ""))


def _new_conn() -> sqlite3.Connection:
    directory = pathlib.Path(tempfile.mkdtemp(prefix="aitp_eval_gate_"))
    db_path = directory / "eval.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("EVAL-GATE", "评测门禁自检项目", now, now),
    )
    conn.commit()
    return conn


def _db_path(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA database_list").fetchone()[2])


def _project_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT id FROM biz_project LIMIT 1").fetchone()["id"])


def _suite(conn: sqlite3.Connection, name: str, visibility: str = "private") -> int:
    return eval_service.create_suite(
        conn,
        {"projectId": _project_id(conn), "name": name, "visibility": visibility, "targetType": "llm"},
    )


def _case(conn: sqlite3.Connection, suite_id: int, title: str) -> int:
    return eval_service.add_case(
        conn,
        {
            "suiteId": suite_id,
            "projectId": _project_id(conn),
            "title": title,
            "severity": "S1",
            "assertions": {"must_contain": [title]},
        },
    )


def _run(conn: sqlite3.Connection, suite_id: int, purpose: str, **extra: Any) -> int:
    payload: dict[str, Any] = {
        "projectId": _project_id(conn),
        "suiteId": suite_id,
        "purpose": purpose,
    }
    payload.update(extra)
    payload.setdefault("config", {"temperature": 0})
    return int(eval_service.create_run(conn, payload)["runId"])


def _pass_outputs(conn: sqlite3.Connection, suite_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT case_no, title FROM biz_eval_case WHERE suite_id = ? AND is_deleted = 0 ORDER BY case_no",
        (suite_id,),
    ).fetchall()
    return [{"caseNo": str(row["case_no"]), "output": str(row["title"])} for row in rows]


def _expect_error(code: int, func: Callable[[], object]) -> None:
    try:
        func()
    except AppError as exc:
        assert exc.code == code, f"错误码 {exc.code} != 期望 {code}：{exc.message}"
        return
    raise AssertionError(f"期望抛出 {code}，实际未抛出")


def _metric_row(value: float, lower: float | None, upper: float | None, sample: int = 200) -> dict[str, Any]:
    return {"value": value, "ci_lower": lower, "ci_upper": upper, "sample_size": sample}


def test_threshold_equality() -> None:
    metrics = {
        "success_rate": _metric_row(0.90, 0.85, 0.95),
        "hallucination_rate": _metric_row(0.04, 0.01, 0.05),
    }
    result = evaluate_gate(metrics, {"successRate": {"min": 0.85}, "hallucinationRate": {"max": 0.05}})
    assert result["conclusion"] == "pass", result
    assert all(item["passed"] for item in result["checks"]), result["checks"]


def test_ci_lower_direction() -> None:
    primary = evaluate_gate(
        {"success_rate": _metric_row(0.90, 0.80, 0.95)},
        {"successRate": {"min": 0.85}},
    )
    assert primary["conclusion"] == "fail", primary
    assert primary["checks"][0]["basis"] == "ci_lower" and primary["checks"][0]["judged"] == 0.80
    descriptive = evaluate_gate(
        {"success_rate": _metric_row(0.90, 0.88, 0.95), "tool_call_f1": _metric_row(0.95, 0.88, 0.99)},
        {"toolCallF1": {"min": 0.90}},
    )
    assert descriptive["conclusion"] == "warn", descriptive
    assert descriptive["primaryFailures"] == [] and len(descriptive["failures"]) == 1


def test_three_tiers() -> None:
    insufficient = evaluate_gate(
        {"success_rate": _metric_row(0.90, 0.60, 0.98, sample=10)},
        {"minExplore": 30, "minPublish": 200, "successRate": {"min": 0.85}},
    )
    assert insufficient["conclusion"] == "insufficient", insufficient
    warn = evaluate_gate(
        {"success_rate": _metric_row(0.90, 0.85, 0.95, sample=60)},
        {"minExplore": 30, "minPublish": 200, "successRate": {"min": 0.85}},
    )
    assert warn["conclusion"] == "warn", warn
    passed = evaluate_gate(
        {"success_rate": _metric_row(0.90, 0.86, 0.95, sample=200)},
        {"minExplore": 30, "minPublish": 200, "successRate": {"min": 0.85}},
    )
    assert passed["conclusion"] == "pass", passed


def test_mcnemar_and_hard_flip() -> None:
    baseline = [
        {"case_id": 1, "case_no": "EC-001", "verdict": "pass"},
        {"case_id": 2, "case_no": "EC-002", "verdict": "pass"},
    ]
    current = [
        {"case_id": 1, "case_no": "EC-001", "verdict": "fail", "failure_reason": "assert.contain_missing"},
        {"case_id": 2, "case_no": "EC-002", "verdict": "pass"},
    ]
    paired = paired_test(baseline, current)
    assert paired["regressedCount"] == 1 and paired["improvedCount"] == 0, paired
    assert paired["hardAssertionRegression"] is True, paired
    assert paired["regressed"][0]["caseNo"] == "EC-001", paired["regressed"]
    assert paired["significant"] is False, "b=1,c=0 时 McNemar p=1.0，单条翻转不显著但硬断言必报"
    gate = evaluate_gate(
        {"success_rate": _metric_row(0.90, 0.86, 0.95)},
        {"successRate": {"min": 0.85}},
        regression=paired,
    )
    assert gate["conclusion"] == "warn", gate
    assert any("硬断言" in note for note in gate["notes"]), gate["notes"]


def test_failure_list_completeness() -> None:
    metrics = {
        "success_rate": _metric_row(0.90, 0.85, 0.95),
        "hallucination_rate": _metric_row(0.04, 0.01, 0.08),
    }
    result = evaluate_gate(
        metrics,
        {"successRate": {"min": 0.95}, "hallucinationRate": {"max": 0.05}},
    )
    assert {item["key"] for item in result["failures"]} == {"successRate", "hallucinationRate"}, result["failures"]
    assert {item["key"] for item in result["primaryFailures"]} == {
        "successRate",
        "hallucinationRate",
    }, result["primaryFailures"]
    for item in result["checks"]:
        assert {"value", "judged", "basis", "limit", "primary"} <= set(item), item
    assert result["conclusion"] == "fail"


def test_comparability() -> None:
    base = {
        "suite_id": 1,
        "suite_version": 1,
        "case_set_hash": "hash-1",
        "judge_snapshot": "{}",
        "config_snapshot": '{"temperature": 0.1}',
    }
    current = {**base, "config_snapshot": '{"temperature": 0.2}'}
    assert_comparable(base, current)
    _expect_error(
        ErrorCode.ILLEGAL_OPERATION,
        lambda: assert_comparable(base, {**current, "suite_id": 2}),
    )
    _expect_error(
        ErrorCode.ILLEGAL_OPERATION,
        lambda: assert_comparable(base, {**current, "case_set_hash": "hash-2"}),
    )
    _expect_error(ErrorCode.ILLEGAL_OPERATION, lambda: assert_comparable(base, dict(base)))


def test_config_snapshot_immutable() -> None:
    conn = _new_conn()
    project_id = _project_id(conn)
    suite_id = _suite(conn, "快照集")
    _case(conn, suite_id, "快照用例")
    config_id = eval_service.create_config(
        conn, {"projectId": project_id, "name": "基线参数", "temperature": 0.2}
    )
    run_id = _run(conn, suite_id, "snapshot", configId=config_id, config={})
    snapshot_before = loads(
        conn.execute("SELECT config_snapshot FROM biz_eval_run WHERE id = ?", (run_id,)).fetchone()[0], {}
    )
    assert snapshot_before["temperature"] == 0.2 and snapshot_before["configId"] == config_id, snapshot_before
    _expect_error(
        ErrorCode.EVAL_CONFIG_REFERENCED,
        lambda: eval_service.update_config(conn, config_id, {"temperature": 0.9}),
    )
    conn.execute("UPDATE biz_eval_config SET temperature = 1.1 WHERE id = ?", (config_id,))
    conn.commit()
    snapshot_after = loads(
        conn.execute("SELECT config_snapshot FROM biz_eval_run WHERE id = ?", (run_id,)).fetchone()[0], {}
    )
    assert snapshot_after["temperature"] == 0.2, "run 快照必须冻结，不随配置行漂移"
    second_run = _run(conn, suite_id, "snapshot-2", configId=config_id, config={})
    snapshot_second = loads(
        conn.execute("SELECT config_snapshot FROM biz_eval_run WHERE id = ?", (second_run,)).fetchone()[0], {}
    )
    assert second_run != run_id and snapshot_second["temperature"] == 1.1, snapshot_second


def test_cross_group_compare_rejected() -> None:
    conn = _new_conn()
    project_id = _project_id(conn)
    suite_a = _suite(conn, "对比集A")
    _case(conn, suite_a, "对比例A")
    suite_b = _suite(conn, "对比集B")
    _case(conn, suite_b, "对比例B")
    config_a = eval_service.create_config(conn, {"projectId": project_id, "name": "参数A", "temperature": 0.1})
    config_b = eval_service.create_config(conn, {"projectId": project_id, "name": "参数B", "temperature": 0.3})
    run_a = _run(conn, suite_a, "compare-a", configId=config_a)
    run_b = _run(conn, suite_a, "compare-b", configId=config_b)
    eval_service.execute_replay(conn, run_a, _pass_outputs(conn, suite_a))
    eval_service.execute_replay(conn, run_b, _pass_outputs(conn, suite_a))
    compared = eval_service.compare_runs(conn, run_a, run_b)
    assert len(compared["metrics"]) == len(METRIC_KEYS) == 18, compared["metrics"]
    assert compared["paired"]["hardAssertionRegression"] is False, compared["paired"]
    run_c = _run(conn, suite_b, "compare-c")
    eval_service.execute_replay(conn, run_c, _pass_outputs(conn, suite_b))
    _expect_error(
        ErrorCode.ILLEGAL_OPERATION,
        lambda: eval_service.compare_runs(conn, run_a, run_c),
    )


def test_export_matches_metrics() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "导出一致集")
    _case(conn, suite_id, "导出用例")
    run_id = _run(conn, suite_id, "export")
    eval_service.execute_replay(conn, run_id, _pass_outputs(conn, suite_id))
    exported = eval_service.export_run(conn, run_id, "md")
    text = bytes(exported["content"]).decode("utf-8")
    assert exported["format"] == "md" and exported["mimetype"].startswith("text/markdown"), exported["format"]
    missing = [key for key in METRIC_KEYS if key not in text]
    assert not missing, f"导出缺指标：{missing}"
    assert "95% Wilson" in text, "导出报告必须标注区间口径"


def test_holdout_export_denied() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "holdout 集", visibility="holdout")
    _case(conn, suite_id, "防污染用例")
    run_id = _run(conn, suite_id, "holdout")
    eval_service.execute_replay(conn, run_id, _pass_outputs(conn, suite_id))
    _expect_error(
        ErrorCode.EVAL_HOLDOUT_EXPORT_DENIED,
        lambda: eval_service.export_run(conn, run_id, "md"),
    )
    _expect_error(
        ErrorCode.EVAL_HOLDOUT_EXPORT_DENIED,
        lambda: eval_service.export_run(conn, run_id, "xlsx"),
    )


def test_cli_exit_codes() -> None:
    conn = _new_conn()
    db_path = _db_path(conn)
    suite_id = _suite(conn, "CLI 集")
    for index in range(1, 9):
        _case(conn, suite_id, f"CLI用例{index}")
    run_id = _run(conn, suite_id, "cli")
    eval_service.execute_replay(conn, run_id, _pass_outputs(conn, suite_id))
    fail_gate = eval_service.create_gate(
        conn,
        {
            "suiteId": suite_id,
            "name": "门禁-fail",
            "thresholds": {"minExplore": 1, "minPublish": 1, "successRate": {"min": 0.99}},
        },
    )
    warn_gate = eval_service.create_gate(
        conn,
        {
            "suiteId": suite_id,
            "name": "门禁-warn",
            "thresholds": {
                "minExplore": 1,
                "minPublish": 1,
                "successRate": {"min": 0.5},
                "toolCallF1": {"min": 0.9},
            },
        },
    )
    pass_gate = eval_service.create_gate(
        conn,
        {
            "suiteId": suite_id,
            "name": "门禁-pass",
            "thresholds": {"minExplore": 1, "minPublish": 1, "successRate": {"min": 0.5}},
        },
    )

    script = str(ROOT / "backend" / "scripts" / "eval_gate_cli.py")

    def run_cli(gate_id: int, json_path: str = "") -> subprocess.CompletedProcess[str]:
        command = [sys.executable, script, "--gate", str(gate_id), "--run", str(run_id), "--db", db_path]
        if json_path:
            command += ["--json", json_path]
        return subprocess.run(
            command, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=120
        )

    json_path = str(pathlib.Path(tempfile.mkdtemp(prefix="aitp_eval_cli_")) / "gate.json")
    failed = run_cli(int(fail_gate), json_path)
    assert failed.returncode == 1 and "::error::" in failed.stdout, (failed.returncode, failed.stdout[-400:])
    payload = json.loads(pathlib.Path(json_path).read_text(encoding="utf-8"))
    assert payload["result"]["conclusion"] == "fail", payload
    warned = run_cli(int(warn_gate))
    assert warned.returncode == 0 and "::warning::" in warned.stdout, (warned.returncode, warned.stdout[-400:])
    passed = run_cli(int(pass_gate))
    assert passed.returncode == 0 and "::notice::" in passed.stdout, (passed.returncode, passed.stdout[-400:])


def main() -> int:
    check("阈值边界含等号", test_threshold_equality)
    check("区间方向：主指标卡下界→fail、描述性→warn", test_ci_lower_direction)
    check("三档结论：insufficient / warn / pass", test_three_tiers)
    check("配对 McNemar：硬断言翻转必报", test_mcnemar_and_hard_flip)
    check("未达标项清单完整", test_failure_list_completeness)
    check("同组可比性（suite/版本/用例/judge 一致）", test_comparability)
    check("配置快照不可变（引用后禁改）", test_config_snapshot_immutable)
    check("跨组对比被拒", test_cross_group_compare_rejected)
    check("导出字段与指标表一致", test_export_matches_metrics)
    check("holdout 集导出被拒（160107）", test_holdout_export_denied)
    check("CLI 退出码：fail=1 / warn=0 / pass=0", test_cli_exit_codes)

    failed = [item for item in RESULTS if not item[1]]
    for name, passed, message in RESULTS:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" —— {message}" if message else ""))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())