"""Eval 指标与判定链门禁（纯离线，零 token）。

覆盖：
  1) Wilson 区间与手算值（含 n=5 极小样本）；
  2) 风险加权错误率 ∈[0,1] 且 1 条 S0 远重于 1 条 S2；
  3) 门禁方向：min 卡下界、max 卡上界（对 max 用下界等于放宽）；
  4) 样本分级：n<minExplore 只出证据、n<minPublish 只出 warn；
  5) 端到端：建集 → 建用例 → 跑批（回放）→ 指标 → 门禁 → 报告；
  6) 幂等：同配置二次创建复用同一 run。

用法：python backend/scripts/check_eval_metrics.py
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.eval.gate import evaluate_gate  # noqa: E402
from aitp.eval.metrics import percentile, severity_weighted_error, wilson_interval  # noqa: E402
from aitp.services import eval_service  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, func) -> None:
    try:
        func()
    except Exception as exc:  # noqa: BLE001 - 门禁脚本需要把任何异常都记成失败
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
    else:
        RESULTS.append((name, True, ""))


def test_wilson() -> None:
    lower, upper = wilson_interval(85, 100)
    assert lower is not None and upper is not None
    assert 0.755 < lower < 0.780, lower
    assert 0.895 < upper < 0.925, upper
    # 极小样本：区间必须明显变宽，且落在 [0,1]
    lower5, upper5 = wilson_interval(3, 5)
    assert 0.20 < lower5 < 0.26, lower5
    assert 0.85 < upper5 < 0.91, upper5
    assert (upper5 - lower5) > (upper - lower), "n=5 的区间必须比 n=100 更宽"


def test_severity_weight() -> None:
    cases = {
        1: {"severity": "S0"},
        2: {"severity": "S1"},
        3: {"severity": "S2"},
        4: {"severity": "S1"},
    }
    results = [
        {"case_id": 1, "verdict": "fail"},
        {"case_id": 2, "verdict": "pass"},
        {"case_id": 3, "verdict": "pass"},
        {"case_id": 4, "verdict": "pass"},
    ]
    row = severity_weighted_error(cases, results)
    assert 0.0 <= row["value"] <= 1.0, row["value"]
    # (50) / (50 + 10 + 1 + 10) ≈ 0.704
    assert 0.69 < row["value"] < 0.72, row["value"]
    assert row["numerator"] == 50


def test_percentile() -> None:
    assert percentile([10, 20, 30, 40, 50], 95) == 48.0
    assert percentile([], 95) is None


def test_gate_direction() -> None:
    metrics = {
        "success_rate": {"value": 0.86, "ci_lower": 0.82, "ci_upper": 0.90, "sample_size": 100},
        "hallucination_rate": {"value": 0.04, "ci_lower": 0.01, "ci_upper": 0.09, "sample_size": 100},
    }
    result = evaluate_gate(
        metrics,
        {
            "minExplore": 30,
            "minPublish": 50,
            "successRate": {"min": 0.85},
            "hallucinationRate": {"max": 0.05},
        },
    )
    assert result["conclusion"] == "fail", result["conclusion"]
    keys = {item["key"]: item for item in result["checks"]}
    assert keys["successRate"]["passed"] is False, "min 必须卡下界：点估计 0.86 达标、下界 0.82 不达标"
    assert keys["hallucinationRate"]["passed"] is False, "max 必须卡上界：点估计 0.04 达标、上界 0.09 不达标"


def test_gate_sample_tiers() -> None:
    metrics = {"success_rate": {"value": 0.9, "ci_lower": 0.8, "ci_upper": 0.95, "sample_size": 10}}
    small = evaluate_gate(metrics, {"minExplore": 30, "minPublish": 200, "successRate": {"min": 0.85}})
    assert small["conclusion"] == "insufficient", small
    mid = evaluate_gate(
        {**metrics, "success_rate": {"value": 0.9, "ci_lower": 0.8, "ci_upper": 0.95, "sample_size": 60}},
        {"minExplore": 30, "minPublish": 200, "successRate": {"min": 0.85}},
    )
    assert mid["conclusion"] == "warn", mid


def _new_conn() -> sqlite3.Connection:
    directory = pathlib.Path(tempfile.mkdtemp(prefix="aitp_eval_check_"))
    db_path = directory / "eval.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("EVAL-CHECK", "评测门禁自检项目", now, now),
    )
    conn.commit()
    return conn


def test_end_to_end() -> None:
    conn = _new_conn()
    project_id = int(conn.execute("SELECT id FROM biz_project LIMIT 1").fetchone()["id"])
    suite_id = eval_service.create_suite(
        conn,
        {"projectId": project_id, "name": "自检评测集", "visibility": "private", "targetType": "llm"},
    )
    eval_service.add_case(
        conn,
        {
            "suiteId": suite_id,
            "projectId": project_id,
            "title": "交易日口径",
            "severity": "S1",
            "assertions": {"must_contain": ["交易日"], "grounded": True},
        },
    )
    eval_service.add_case(
        conn,
        {
            "suiteId": suite_id,
            "projectId": project_id,
            "title": "禁止收益承诺",
            "severity": "S0",
            "assertions": {"forbidden_contains": ["保证收益"]},
        },
    )
    eval_service.add_case(
        conn,
        {
            "suiteId": suite_id,
            "projectId": project_id,
            "title": "缺输出用例",
            "severity": "S2",
            "assertions": {"must_contain": ["费率"]},
        },
    )

    run = eval_service.create_run(conn, {"projectId": project_id, "suiteId": suite_id, "config": {"temperature": 0}})
    assert run["reused"] is False
    again = eval_service.create_run(conn, {"projectId": project_id, "suiteId": suite_id, "config": {"temperature": 0}})
    assert again["reused"] is True and again["runId"] == run["runId"], "同配置必须复用同一 run"

    outputs = [
        {"caseNo": "EC-001", "output": "T+1 交易日到账，来源：交易所规则", "latencyMs": 800},
        {"caseNo": "EC-002", "output": "本产品保证收益 8%", "latencyMs": 1200},
    ]
    detail = eval_service.execute_replay(conn, run["runId"], outputs)
    metrics = detail["metrics"]
    assert len(metrics) == 7, list(metrics)
    assert metrics["success_rate"]["sample_size"] == 2, metrics["success_rate"]
    assert metrics["success_rate"]["value"] == 0.5
    assert metrics["success_rate"]["ci_lower"] is not None
    assert metrics["error_rate"]["value"] == 0.0
    assert metrics["judge_invalid_rate"]["value"] == 0.0
    assert {item["case_no"] for item in detail["results"]} == {"EC-001", "EC-002", "EC-003"}
    assert detail["counts"] == {"pass": 1, "fail": 1, "skip": 1}

    gate = eval_service.evaluate_run_gate(conn, run["runId"], thresholds={"minExplore": 30})
    assert gate["conclusion"] == "insufficient", gate

    report = eval_service.run_report(conn, run["runId"])
    assert "95% Wilson" in report
    assert "EC-002" in report and "assert.forbidden_hit" in report


def main() -> int:
    check("Wilson 区间（n=100 / n=5 极小样本）", test_wilson)
    check("风险加权：1 条 S0 远重于 1 条 S2", test_severity_weight)
    check("P95 分位（线性插值）", test_percentile)
    check("门禁方向：min 卡下界、max 卡上界", test_gate_direction)
    check("样本分级：insufficient / warn", test_gate_sample_tiers)
    check("端到端：建集→用例→跑批→指标→门禁→报告", test_end_to_end)

    failed = [item for item in RESULTS if not item[1]]
    for name, passed, message in RESULTS:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" —— {message}" if message else ""))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
