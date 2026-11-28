"""生成 golden fixture（golden_metrics.json）。

数据集是人工设计的固定输入，期望值由纯领域函数计算一次后冻结；
check_eval_metrics 读 fixture 复算并比对，公式一旦漂移立即红。
手算可验证的关键值在 check 脚本里另写硬断言，不全部依赖冻结值。

用法：python backend/tests/fixtures/eval/build_fixture.py
"""

from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.eval.ablation import mcnemar_pvalue, minimum_detectable_effect  # noqa: E402
from aitp.eval.judges import calibration_summary, cohen_kappa, parse_judge_verdict  # noqa: E402
from aitp.eval.metrics import compute_metrics, percentile, wilson_interval  # noqa: E402
from aitp.eval.rag import ndcg_at_k, recall_at_k  # noqa: E402
from aitp.eval.rubric import score_rubric, validate_rubric  # noqa: E402
from aitp.eval.traces import evaluate_end_state, tool_prf  # noqa: E402

CASES: dict[int, dict] = {
    1: {
        "severity": "S1",
        "tools_expected": {"required": ["search"], "args": {"search": {"q": "费率"}}},
        "assertions": {"expected_min": 2},
        "relevant_docs": ["d1", "d2"],
        "graded_relevance": {"d1": 3, "d2": 2},
    },
    2: {
        "severity": "S0",
        "tools_expected": {"required": ["trade"], "forbidden": ["email"], "args": {"trade": {"order": 1}}},
        "assertions": {},
    },
    3: {"severity": "S2", "assertions": {}, "relevant_docs": ["d3"], "graded_relevance": {"d3": 1}},
    4: {"severity": "S1", "assertions": {"should_refuse": True}},
    5: {"severity": "S2", "assertions": {"should_refuse": False}},
    6: {"severity": "S1", "assertions": {}},
    7: {"severity": "S0", "assertions": {}},
    8: {"severity": "S2", "assertions": {"expected_min": 3}},
}

RESULTS: list[dict] = [
    {
        "case_id": 1, "verdict": "pass", "failure_reason": "", "latency_ms": 100, "cost_micros": 1000,
        "steps": 3,
        "tool_trace": [{"tool": "search", "args": {"q": "费率"}}, {"tool": "calc", "args": {}}],
        "retrieved": ["d1", "d3", "d2"],
        "claims": [{"supported": True, "cited": True}, {"supported": True, "cited": True}],
        "rubric": {"instructionFollowed": True}, "judge_label": "pass", "human": "pass",
    },
    {
        "case_id": 2, "verdict": "fail", "failure_reason": "tool.forbidden_called:email",
        "latency_ms": 150, "cost_micros": 0,
        "tool_trace": [{"tool": "trade", "args": {"order": 1}}, {"tool": "email", "args": {}}],
        "judge_label": "fail", "human": "fail",
    },
    {
        "case_id": 3, "verdict": "pass", "failure_reason": "", "latency_ms": 200, "cost_micros": 2000,
        "retrieved": ["d3", "d9"], "rubric": {"instructionFollowed": True},
        "judge_label": "pass", "human": "pass",
    },
    {"case_id": 4, "verdict": "pass", "failure_reason": "", "latency_ms": 300, "cost_micros": 0,
     "judge_label": "pass", "human": "pass"},
    {"case_id": 5, "verdict": "fail", "failure_reason": "refusal.should_answer",
     "latency_ms": 350, "cost_micros": 0, "judge_label": "fail", "human": "fail"},
    {
        "case_id": 6, "verdict": "pass", "failure_reason": "", "latency_ms": 400, "cost_micros": 3000,
        "hallucinated": True,
        "claims": [{"supported": True, "cited": True}, {"supported": False, "cited": False}],
        "rubric": {"instructionFollowed": False}, "judge_label": "pass", "human": "pass",
    },
    {"case_id": 7, "verdict": "fail", "failure_reason": "assert.hallucination:no_source",
     "latency_ms": 450, "cost_micros": 0, "judge_label": "pass", "human": "fail"},
    {"case_id": 8, "verdict": "pass", "failure_reason": "", "latency_ms": 500, "cost_micros": 4000,
     "steps": 5, "judge_label": "pass", "human": "pass"},
]


def main() -> int:
    metric_rows = compute_metrics(CASES, RESULTS)
    paired = [(str(item["human"]), str(item["judge_label"])) for item in RESULTS]
    fixture = {
        "schemaVersion": 1,
        "description": "AITP eval 领域层 golden fixture：固定输入 + 冻结期望值",
        "cases": CASES,
        "results": RESULTS,
        "expectedMetrics": {row["metric_key"]: row for row in metric_rows},
        "wilsonCases": {
            "85_of_100": {"lower": wilson_interval(85, 100)[0], "upper": wilson_interval(85, 100)[1]},
            "3_of_5": {"lower": wilson_interval(3, 5)[0], "upper": wilson_interval(3, 5)[1]},
            "zero": {"lower": wilson_interval(0, 0)[0], "upper": wilson_interval(0, 0)[1]},
        },
        "percentileCases": {
            "p95_10_50": percentile([10, 20, 30, 40, 50], 95),
            "p50_8_latencies": percentile([100, 150, 200, 300, 350, 400, 450, 500], 50),
            "p95_8_latencies": percentile([100, 150, 200, 300, 350, 400, 450, 500], 95),
        },
        "judgeCases": {
            "parseValid": parse_judge_verdict('{"verdict": "pass", "score": 90}'),
            "parseInvalid": parse_judge_verdict("not-a-json"),
            "kappaPerfect": cohen_kappa(["pass", "fail", "pass"], ["pass", "fail", "pass"]),
            "calibration": calibration_summary(
                [pair[0] for pair in paired], [pair[1] for pair in paired]
            ),
        },
        "ragCases": {
            "recallCase1": recall_at_k(["d1", "d3", "d2"], ["d1", "d2"], 5),
            "recallCase3": recall_at_k(["d3", "d9"], ["d3"], 5),
            "ndcgCase1": ndcg_at_k(["d1", "d3", "d2"], {"d1": 3, "d2": 2}, 5),
            "ndcgCase3": ndcg_at_k(["d3", "d9"], {"d3": 1}, 5),
        },
        "rubricCases": {
            "score": score_rubric(
                validate_rubric(
                    {
                        "dimensions": [
                            {"key": "factuality", "weight": 0.4, "scale": 5},
                            {"key": "instruction", "weight": 0.6, "scale": 5},
                        ],
                        "must_pass": ["factuality"],
                        "pass_threshold": 3.5,
                    }
                ),
                {"factuality": 4.0, "instruction": 3.0},
            )
        },
        "traceCases": {
            "prf": tool_prf(["search"], ["search", "calc"]),
            "endStatePassed": evaluate_end_state(
                {"kind": "http", "checks": [{"request": "GET /o/1", "expect": {"status": "filled"}}]},
                {"GET /o/1": {"status": "filled", "amount": 1000}},
            ),
            "endStateFailed": evaluate_end_state(
                {"kind": "http", "checks": [{"request": "GET /o/1", "expect": {"status": "filled"}}]},
                {"GET /o/1": {"status": "pending"}},
            ),
        },
        "ablationCases": {
            "mcnemarNoFlip": mcnemar_pvalue(0, 0),
            "mcnemarSixVsOne": mcnemar_pvalue(6, 1),
            "mdeTable": {
                str(n): minimum_detectable_effect(0.85, n) for n in (50, 100, 200, 400, 2200)
            },
        },
    }

    out = pathlib.Path(__file__).with_name("golden_metrics.json")
    out.write_text(json.dumps(fixture, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"golden fixture written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
