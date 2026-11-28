"""Eval 服务：评测集/用例、配置/判定器/门禁、跑批、指标、对比、导出与字典。

设计对照（docs/AITP_Eval子包设计.md）：
  §四 32 个接口条目 / 34 条路由由 api/evals.py 暴露，本模块承载业务语义；
  §五 18 指标与 Wilson 区间在 eval/metrics.py，门禁在 eval/gate.py；
  §六 判定链与校准在 eval_runner.py / eval_calibration.py；
  §八 防污染：holdout 评测集禁止导出（160107）；
  §九 生命周期：预算 / 心跳 / 续跑 / 取消 / reaper 在 eval_runner.py。

保持「回放评测」零 token 可跑通：模型输出由调用方传入，判定链、指标、
门禁与导出一体化；真实模型接入走同一套判定链。
"""

from __future__ import annotations

import hashlib
import io
import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..eval.ablation import DEFAULT_ALPHA, assert_comparable, mde_table, paired_test
from ..eval.cases import CATEGORIES, normalize_case
from ..eval.gate import DEFAULT_THRESHOLDS, METRIC_KEYS as THRESHOLD_METRIC_KEYS, PRIMARY_METRICS, evaluate_gate
from ..eval.judges import cohen_kappa
from ..eval.metrics import METRIC_KEYS
from ..eval.rubric import validate_rubric
from ..eval.severity import SEVERITIES, SEVERITY_WEIGHTS
from ..repositories.base import dumps, loads
from ..repositories.eval_case_repo import EvalCaseRepository
from ..repositories.eval_config_repo import EvalConfigRepository
from ..repositories.eval_gate_repo import EvalGateRepository, gate_summary
from ..repositories.eval_judge_repo import EvalJudgeRepository
from ..repositories.eval_metric_repo import EvalMetricRepository
from ..repositories.eval_result_repo import EvalResultRepository
from ..repositories.eval_run_repo import EvalRunRepository
from ..repositories.eval_suite_repo import EvalSuiteRepository
from . import eval_calibration, eval_runner

VISIBILITIES = {"private", "holdout", "public"}
TARGET_TYPES = {"llm", "agent", "rag", "prompt"}
SUITE_STATUSES = {"active", "archived"}
CASE_STATUSES = {"active", "archived"}
JUDGE_STRATEGIES = {"single", "double", "panel"}
RUN_STATUSES = ("queued", "running", "finished", "failed", "cancelled")
EXPORT_FORMATS = {"md": "text/markdown", "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"}

#: failure_reason 枚举（设计 §6.2 + §九.2 infra.stale_run + 实现补充分支）
FAILURE_REASONS: tuple[str, ...] = (
    "assert.contain_missing",
    "assert.forbidden_hit",
    "assert.regex_mismatch",
    "assert.too_long",
    "assert.schema_invalid",
    "assert.end_state_mismatch",
    "assert.hallucination",
    "rubric.must_pass",
    "rubric.below_threshold",
    "judge.arbitration",
    "judge.unparseable",
    "tool.missing_required",
    "tool.forbidden_called",
    "tool.arg_mismatch",
    "trace.too_many_steps",
    "rag.no_recall",
    "refusal.should_refuse",
    "refusal.should_answer",
    "infra.timeout",
    "infra.model_unavailable",
    "infra.stale_run",
    "no_output",
)


def _int(value: Any, field: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是整数") from None


def _float(value: Any, field: str) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"参数 {field} 必须是数字") from None


def _suite_or_404(conn: sqlite3.Connection, suite_id: int) -> dict[str, Any]:
    suite = EvalSuiteRepository(conn).get(suite_id)
    if suite is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评测集不存在")
    return suite


def _case_or_404(conn: sqlite3.Connection, case_id: int) -> dict[str, Any]:
    case = EvalCaseRepository(conn).get(case_id)
    if case is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评测用例不存在")
    return case


def _config_or_404(conn: sqlite3.Connection, config_id: int) -> dict[str, Any]:
    row = EvalConfigRepository(conn).get(config_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评测配置不存在")
    return row


def _judge_or_404(conn: sqlite3.Connection, judge_id: int) -> dict[str, Any]:
    row = EvalJudgeRepository(conn).get(judge_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "判定器不存在")
    return row


def _gate_or_404(conn: sqlite3.Connection, gate_id: int) -> dict[str, Any]:
    row = EvalGateRepository(conn).get(gate_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "评测门禁不存在")
    return row


def _run_or_404(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    row = EvalRunRepository(conn).get(run_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "跑批不存在")
    return row


def _require_active_suite(suite: dict[str, Any], action: str = "新增用例") -> None:
    if suite.get("status") != "active":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, f"评测集已归档，禁止{action}")


def _refresh_case_count(conn: sqlite3.Connection, suite_id: int) -> int:
    total = EvalCaseRepository(conn).count_by_suite(suite_id)
    EvalSuiteRepository(conn).update(suite_id, {"case_count": total})
    return total


def _extra_case_fields(data: dict[str, Any]) -> dict[str, str]:
    """用例里不参与文本规则的 JSON 列：相关文档 / 分级相关度 / 期望工具。"""

    relevant = data.get("relevant_docs") or []
    if not isinstance(relevant, list) or any(not isinstance(item, str) for item in relevant):
        raise AppError(ErrorCode.PARAM_ERROR, "relevant_docs 必须是字符串数组")
    graded = data.get("graded_relevance") or {}
    if not isinstance(graded, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "graded_relevance 必须是对象")
    clean_graded: dict[str, int] = {}
    for doc, grade in graded.items():
        number = int(_float(grade, f"graded_relevance.{doc}"))
        if number not in (0, 1, 2, 3):
            raise AppError(ErrorCode.PARAM_ERROR, "graded_relevance 等级只能是 0–3")
        clean_graded[str(doc)] = number
    tools = data.get("tools_expected") or {}
    if not isinstance(tools, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "tools_expected 必须是对象")
    for key in ("required", "forbidden"):
        value = tools.get(key) or []
        if not isinstance(value, list) or any(not isinstance(item, str) for item in value):
            raise AppError(ErrorCode.PARAM_ERROR, f"tools_expected.{key} 必须是字符串数组")
    if tools.get("args") is not None and not isinstance(tools.get("args"), dict):
        raise AppError(ErrorCode.PARAM_ERROR, "tools_expected.args 必须是对象")
    return {
        "relevant_docs": dumps(relevant),
        "graded_relevance": dumps(clean_graded),
        "tools_expected": dumps(tools),
    }


def _normalized_rubric(value: Any) -> str:
    rubric = validate_rubric(value)
    return dumps(rubric) if rubric["dimensions"] else "[]"


# ---------------------------------------------------------------- 评测集


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


def update_suite(conn: sqlite3.Connection, suite_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """改名 / 归档 / 调整可见性与元信息。改名不能与同项目其它评测集重名。"""

    suite = _suite_or_404(conn, suite_id)
    update: dict[str, Any] = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise AppError(ErrorCode.PARAM_ERROR, "评测集名称不能为空")
        if name != suite["name"]:
            if EvalSuiteRepository(conn).find_by_name(int(suite["project_id"]), name) is not None:
                raise AppError(ErrorCode.PARAM_ERROR, "同名评测集已存在")
            update["name"] = name
    if "description" in payload:
        update["description"] = str(payload.get("description") or "")
    if "tags" in payload:
        update["tags"] = str(payload.get("tags") or "")
    if "status" in payload:
        status = str(payload.get("status") or "")
        if status not in SUITE_STATUSES:
            raise AppError(ErrorCode.PARAM_ERROR, "status 只能是 active / archived")
        update["status"] = status
    if "visibility" in payload:
        visibility = str(payload.get("visibility") or "")
        if visibility not in VISIBILITIES:
            raise AppError(ErrorCode.PARAM_ERROR, f"visibility 取值必须是 {sorted(VISIBILITIES)} 之一")
        update["visibility"] = visibility
    if "targetType" in payload:
        target_type = str(payload.get("targetType") or "")
        if target_type not in TARGET_TYPES:
            raise AppError(ErrorCode.PARAM_ERROR, f"targetType 取值必须是 {sorted(TARGET_TYPES)} 之一")
        update["target_type"] = target_type
    if update:
        EvalSuiteRepository(conn).update(suite_id, update)
        conn.commit()
    return suite_detail(conn, suite_id)


def delete_suite(conn: sqlite3.Connection, suite_id: int) -> dict[str, Any]:
    """软删评测集；有跑批一律拒绝（160101），历史结论不能被悄悄抹掉。"""

    _suite_or_404(conn, suite_id)
    if eval_runner.suite_has_runs(conn, suite_id):
        raise AppError(ErrorCode.EVAL_SUITE_HAS_RUNS, "评测集已有跑批记录，禁止删除；可改为归档")
    now = utcnow_iso()
    conn.execute(
        "UPDATE biz_eval_case SET is_deleted = 1, updated_at = ? WHERE suite_id = ? AND is_deleted = 0",
        (now, suite_id),
    )
    EvalSuiteRepository(conn).soft_delete(suite_id)
    conn.commit()
    return {"id": suite_id, "deleted": True}


def duplicate_suite(conn: sqlite3.Connection, suite_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """复制为新版本：新集版本号 +1，用例整体拷贝，原集不动。"""

    source = _suite_or_404(conn, suite_id)
    repo = EvalSuiteRepository(conn)
    base_name = str(payload.get("name") or f"{source['name']}-副本").strip()
    if not base_name:
        raise AppError(ErrorCode.PARAM_ERROR, "新评测集名称不能为空")
    name = base_name
    suffix = 2
    while repo.find_by_name(int(source["project_id"]), name) is not None:
        name = f"{base_name} {suffix}"
        suffix += 1
        if suffix > 50:
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "同名副本过多，请换一个名称")

    new_id = repo.insert(
        {
            "project_id": int(source["project_id"]),
            "name": name,
            "target_type": source["target_type"],
            "description": source["description"],
            "tags": source["tags"],
            "status": "active",
            "visibility": source["visibility"],
            "version": int(source.get("version") or 1) + 1,
            "case_count": 0,
            "creator": str(payload.get("creator") or source.get("creator") or ""),
        }
    )
    case_repo = EvalCaseRepository(conn)
    copied = 0
    for case in case_repo.list_active(suite_id):
        case_repo.insert(
            {
                "project_id": int(case["project_id"]),
                "suite_id": new_id,
                "case_no": case_repo.next_case_no(new_id),
                "category": case["category"],
                "severity": case["severity"],
                "title": case["title"],
                "input_json": case["input_json"],
                "expected_type": case["expected_type"],
                "expected_text": case["expected_text"],
                "rubric_json": case["rubric_json"],
                "assertions_json": case["assertions_json"],
                "relevant_docs": case["relevant_docs"],
                "graded_relevance": case["graded_relevance"],
                "tools_expected": case["tools_expected"],
                "effective_date": case["effective_date"],
                "tags": case["tags"],
                "difficulty": case["difficulty"],
                "source": case["source"],
                "source_ref": case["source_ref"],
                "status": "active",
                "creator": str(payload.get("creator") or ""),
            }
        )
        copied += 1
    _refresh_case_count(conn, new_id)
    conn.commit()
    return {"id": new_id, "name": name, "version": int(source.get("version") or 1) + 1, "caseCount": copied}


def suite_detail(conn: sqlite3.Connection, suite_id: int) -> dict[str, Any]:
    suite = _suite_or_404(conn, suite_id)
    case_repo = EvalCaseRepository(conn)
    run_repo = EvalRunRepository(conn)
    recent = run_repo.list_all({"suite_id": suite_id}, limit=5)
    return {
        **suite,
        "caseCount": case_repo.count_by_suite(suite_id),
        "categories": case_repo.raw(
            "SELECT category, COUNT(*) AS total FROM biz_eval_case "
            "WHERE suite_id = ? AND is_deleted = 0 GROUP BY category",
            (suite_id,),
        ),
        "recentRuns": recent,
        "trend": [
            {
                "runId": int(row["id"]),
                "successRate": row.get("success_rate"),
                "gateConclusion": row.get("gate_conclusion"),
                "createdAt": row.get("created_at"),
            }
            for row in recent
            if row.get("status") == "finished"
        ],
    }


def suite_quality(conn: sqlite3.Connection, suite_id: int) -> dict[str, Any]:
    """评测集自身质量（设计 §5.4）：覆盖矩阵 / 冗余度 / 区分度 / 标注一致性 / 难度。"""

    _suite_or_404(conn, suite_id)
    cases = EvalCaseRepository(conn).list_active(suite_id)

    coverage_rows = EvalCaseRepository(conn).raw(
        "SELECT category, severity, COUNT(*) AS total FROM biz_eval_case "
        "WHERE suite_id = ? AND is_deleted = 0 GROUP BY category, severity ORDER BY category, severity",
        (suite_id,),
    )
    coverage = [
        {"category": str(row["category"]), "severity": str(row["severity"]), "count": int(row["total"])}
        for row in coverage_rows
    ]

    difficulty_counts: dict[int, int] = {}
    for case in cases:
        level = int(case.get("difficulty") or 2)
        difficulty_counts[level] = difficulty_counts.get(level, 0) + 1
    difficulty_labels = {1: "易", 2: "中", 3: "难"}
    difficulty = [
        {"level": level, "label": difficulty_labels.get(level, str(level)), "count": count}
        for level, count in sorted(difficulty_counts.items())
    ]

    # 冗余度：用例标题 8-gram Jaccard > 0.8（去重参考，不是硬门禁）
    grams = [_ngrams(str(case.get("title") or "")) for case in cases[:500]]
    pairs = 0
    duplicates: list[dict[str, Any]] = []
    for i in range(len(grams)):
        for j in range(i + 1, len(grams)):
            pairs += 1
            similarity = _jaccard(grams[i], grams[j])
            if similarity > 0.8:
                duplicates.append(
                    {
                        "a": cases[i].get("case_no"),
                        "b": cases[j].get("case_no"),
                        "similarity": round(similarity, 4),
                    }
                )
    duplicates.sort(key=lambda item: float(item["similarity"]), reverse=True)

    # 区分度：同一用例跨多次跑批的通过率方差（σ 越小越没区分力）
    result_rows = conn.execute(
        "SELECT r.case_id, r.case_no, r.verdict FROM biz_eval_result r "
        "JOIN biz_eval_run run ON run.id = r.run_id "
        "WHERE run.suite_id = ? AND run.status = 'finished' AND r.verdict IN ('pass', 'fail')",
        (suite_id,),
    ).fetchall()
    per_case: dict[int, list[str]] = {}
    case_no_of: dict[int, str] = {}
    for row in result_rows:
        per_case.setdefault(int(row["case_id"]), []).append(str(row["verdict"]))
        case_no_of[int(row["case_id"])] = str(row["case_no"])
    variances: list[float] = []
    low_discrimination: list[dict[str, Any]] = []
    for case_id, verdicts in per_case.items():
        if len(verdicts) < 2:
            continue
        mean = sum(1 for verdict in verdicts if verdict == "pass") / len(verdicts)
        variance = mean * (1.0 - mean)
        variances.append(variance)
        if variance < 0.1:
            low_discrimination.append(
                {"caseId": case_id, "caseNo": case_no_of.get(case_id), "passRate": round(mean, 4),
                 "variance": round(variance, 4), "runs": len(verdicts)}
            )
    low_discrimination.sort(key=lambda item: float(item["variance"]))

    # 标注一致性：同一用例被 ≥2 位标注者独立标注过的 κ
    human_rows = conn.execute(
        "SELECT r.case_id, r.human_verdict, r.human_by FROM biz_eval_result r "
        "JOIN biz_eval_run run ON run.id = r.run_id "
        "WHERE run.suite_id = ? AND r.human_verdict IN ('pass', 'fail')",
        (suite_id,),
    ).fetchall()
    by_case: dict[int, dict[str, str]] = {}
    for row in human_rows:
        annotator = str(row["human_by"] or f"anonymous:{row['human_verdict']}")
        by_case.setdefault(int(row["case_id"]), {})[annotator] = str(row["human_verdict"])
    pairs_a: list[str] = []
    pairs_b: list[str] = []
    for labels in by_case.values():
        if len(labels) >= 2:
            values = list(labels.values())[:2]
            pairs_a.append(values[0])
            pairs_b.append(values[1])
    annotation: dict[str, Any]
    if pairs_a:
        annotation = {"kappa": cohen_kappa(pairs_a, pairs_b), "sampleSize": len(pairs_a)}
    else:
        annotation = {"kappa": None, "sampleSize": 0, "reason": "insufficient_annotation"}

    return {
        "caseCount": len(cases),
        "coverage": coverage,
        "difficulty": difficulty,
        "redundancy": {
            "pairs": pairs,
            "duplicates": duplicates[:20],
            "ratio": round(len(duplicates) / pairs, 4) if pairs else 0.0,
        },
        "discrimination": {
            "casesWithRuns": len(variances),
            "meanVariance": round(sum(variances) / len(variances), 4) if variances else None,
            "lowDiscrimination": low_discrimination[:20],
        },
        "annotationConsistency": annotation,
    }


def _ngrams(text: str, size: int = 8) -> set[str]:
    compact = "".join(ch for ch in text if not ch.isspace())
    return {compact[index:index + size] for index in range(max(0, len(compact) - size + 1))}


def _jaccard(left: set[str], right: set[str]) -> float:
    if not left or not right:
        return 0.0
    union = left | right
    return len(left & right) / len(union) if union else 0.0


# ---------------------------------------------------------------- 用例


def add_case(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    suite_id = _int(payload.get("suiteId"), "suiteId")
    suite = _suite_or_404(conn, suite_id)
    _require_active_suite(suite)

    normalized = normalize_case(payload)
    project_id = _int(payload.get("projectId") or suite["project_id"], "projectId")
    if project_id != int(suite["project_id"]):
        raise AppError(ErrorCode.PARAM_ERROR, "用例所属项目与评测集不一致")

    extra = _extra_case_fields(payload)
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
            "rubric_json": _normalized_rubric(normalized["rubric_json"]),
            "assertions_json": dumps(normalized["assertions"]),
            "relevant_docs": extra["relevant_docs"],
            "graded_relevance": extra["graded_relevance"],
            "tools_expected": extra["tools_expected"],
            "effective_date": normalized["effective_date"],
            "tags": normalized["tags"],
            "difficulty": normalized["difficulty"],
            "source": normalized["source"],
            "source_ref": normalized["source_ref"],
            "status": "active",
            "creator": str(payload.get("creator") or ""),
        }
    )
    _refresh_case_count(conn, suite_id)
    conn.commit()
    return case_id


def update_case(conn: sqlite3.Connection, case_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """编辑用例；已进入任何跑批结果的用例冻结（160110），保证历史可复现。"""

    row = _case_or_404(conn, case_id)
    if eval_runner.case_is_frozen(conn, case_id):
        raise AppError(ErrorCode.EVAL_CASE_FROZEN, "用例已进入跑批结果，冻结后不可编辑；请复制评测集出新版本")

    merged: dict[str, Any] = {
        "title": row["title"],
        "category": row["category"],
        "severity": row["severity"],
        "assertions": loads(row.get("assertions_json"), {}) or {},
        "input_json": loads(row.get("input_json"), {}) or {},
        "expected_type": row["expected_type"],
        "expected_text": row["expected_text"],
        "rubric_json": loads(row.get("rubric_json"), []) or [],
        "effective_date": row["effective_date"],
        "tags": row["tags"],
        "difficulty": row["difficulty"],
        "source": row["source"],
        "source_ref": row["source_ref"],
        "relevant_docs": loads(row.get("relevant_docs"), []) or [],
        "graded_relevance": loads(row.get("graded_relevance"), {}) or {},
        "tools_expected": loads(row.get("tools_expected"), {}) or {},
    }
    for key in (
        "title", "category", "severity", "assertions", "input_json", "expected_type", "expected_text",
        "rubric_json", "effective_date", "tags", "difficulty", "source", "source_ref",
        "relevant_docs", "graded_relevance", "tools_expected",
    ):
        if key in payload:
            merged[key] = payload[key]
    normalized = normalize_case(merged)
    extra = _extra_case_fields(merged)

    update: dict[str, Any] = {
        "category": normalized["category"],
        "severity": normalized["severity"],
        "title": normalized["title"],
        "input_json": dumps(normalized["input_json"]),
        "expected_type": normalized["expected_type"],
        "expected_text": normalized["expected_text"],
        "rubric_json": _normalized_rubric(normalized["rubric_json"]),
        "assertions_json": dumps(normalized["assertions"]),
        "relevant_docs": extra["relevant_docs"],
        "graded_relevance": extra["graded_relevance"],
        "tools_expected": extra["tools_expected"],
        "effective_date": normalized["effective_date"],
        "tags": normalized["tags"],
        "difficulty": normalized["difficulty"],
        "source": normalized["source"],
        "source_ref": normalized["source_ref"],
    }
    if "status" in payload:
        status = str(payload.get("status") or "")
        if status not in CASE_STATUSES:
            raise AppError(ErrorCode.PARAM_ERROR, "用例 status 只能是 active / archived")
        update["status"] = status
    EvalCaseRepository(conn).update(case_id, update)
    _refresh_case_count(conn, int(row["suite_id"]))
    conn.commit()
    return {"id": case_id, "caseNo": row["case_no"], "updated": True}


def delete_case(conn: sqlite3.Connection, case_id: int) -> dict[str, Any]:
    row = _case_or_404(conn, case_id)
    if eval_runner.case_is_frozen(conn, case_id):
        raise AppError(ErrorCode.EVAL_CASE_FROZEN, "用例已进入跑批结果，不可删除；请改为归档")
    EvalCaseRepository(conn).soft_delete(case_id)
    total = _refresh_case_count(conn, int(row["suite_id"]))
    conn.commit()
    return {"id": case_id, "deleted": True, "caseCount": total}


def import_cases(conn: sqlite3.Connection, suite_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """批量导入：干跑预览不改库；正式导入按标题幂等（重复标题跳过）。"""

    suite = _suite_or_404(conn, suite_id)
    _require_active_suite(suite, "导入用例")
    items = payload.get("cases") if isinstance(payload.get("cases"), list) else payload.get("items")
    if not isinstance(items, list) or not items:
        raise AppError(ErrorCode.PARAM_ERROR, "cases 必须是非空数组")
    if len(items) > 500:
        raise AppError(ErrorCode.PARAM_ERROR, "cases 一次最多 500 条")
    dry_run = bool(payload.get("dryRun") or payload.get("dry_run"))

    existing = {
        str(row["title"])
        for row in conn.execute(
            "SELECT title FROM biz_eval_case WHERE suite_id = ? AND is_deleted = 0", (suite_id,)
        ).fetchall()
    }
    report: list[dict[str, Any]] = []
    accepted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            raise AppError(ErrorCode.PARAM_ERROR, f"第 {index + 1} 条用例必须是对象")
        try:
            normalized = normalize_case(raw)
            extra = _extra_case_fields(raw)
        except AppError as exc:
            skipped.append({"index": index, "title": str(raw.get("title") or ""), "reason": exc.message})
            continue
        title = normalized["title"]
        if title in existing:
            skipped.append({"index": index, "title": title, "reason": "duplicate_title"})
            continue
        existing.add(title)
        accepted.append({"normalized": normalized, "extra": extra})

    if dry_run:
        for index, item in enumerate(accepted):
            report.append({"index": index, "title": item["normalized"]["title"], "status": "accept"})
        return {
            "dryRun": True,
            "accepted": len(accepted),
            "skipped": len(skipped),
            "created": [],
            "items": report,
            "skippedItems": skipped,
        }

    case_repo = EvalCaseRepository(conn)
    created: list[dict[str, Any]] = []
    for item in accepted:
        normalized = item["normalized"]
        extra = item["extra"]
        case_id = case_repo.insert(
            {
                "project_id": int(suite["project_id"]),
                "suite_id": suite_id,
                "case_no": case_repo.next_case_no(suite_id),
                "category": normalized["category"],
                "severity": normalized["severity"],
                "title": normalized["title"],
                "input_json": dumps(normalized["input_json"]),
                "expected_type": normalized["expected_type"],
                "expected_text": normalized["expected_text"],
                "rubric_json": _normalized_rubric(normalized["rubric_json"]),
                "assertions_json": dumps(normalized["assertions"]),
                "relevant_docs": extra["relevant_docs"],
                "graded_relevance": extra["graded_relevance"],
                "tools_expected": extra["tools_expected"],
                "effective_date": normalized["effective_date"],
                "tags": normalized["tags"],
                "difficulty": normalized["difficulty"],
                "source": normalized["source"],
                "source_ref": normalized["source_ref"],
                "status": "active",
                "creator": str(payload.get("creator") or ""),
            }
        )
        created.append({"id": case_id, "title": normalized["title"]})
    _refresh_case_count(conn, suite_id)
    conn.commit()
    return {
        "dryRun": False,
        "accepted": len(created),
        "skipped": len(skipped),
        "created": created,
        "skippedItems": skipped,
    }


def case_from_badcase(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    """badcase 回流：aiRecord / bug 任一来源转评测用例。"""

    source_ref = str(payload.get("sourceRef") or "")
    if not source_ref:
        if payload.get("aiRecordId") not in (None, ""):
            source_ref = f"aiRecord:{payload['aiRecordId']}"
        elif payload.get("bugId") not in (None, ""):
            source_ref = f"bug:{payload['bugId']}"
        else:
            raise AppError(ErrorCode.PARAM_ERROR, "sourceRef / aiRecordId / bugId 至少给一个")
    data = {**payload, "source": str(payload.get("source") or "badcase"), "source_ref": source_ref}
    return add_case(conn, data)


# ---------------------------------------------------------------- 配置


def create_config(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    project_id = _int(payload.get("projectId"), "projectId")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AppError(ErrorCode.PARAM_ERROR, "配置名称不能为空")
    repo = EvalConfigRepository(conn)
    if repo.find_by_name(project_id, name) is not None:
        raise AppError(ErrorCode.PARAM_ERROR, "同名评测配置已存在")

    temperature = _float(payload.get("temperature", 0.0), "temperature")
    top_p = _float(payload.get("topP", payload.get("top_p", 1.0)), "topP")
    if not 0.0 <= temperature <= 2.0:
        raise AppError(ErrorCode.PARAM_ERROR, "temperature 取值必须在 0–2")
    if not 0.0 < top_p <= 1.0:
        raise AppError(ErrorCode.PARAM_ERROR, "topP 取值必须在 (0, 1]")
    max_tokens = int(payload.get("maxTokens") or 4096)
    if max_tokens <= 0:
        raise AppError(ErrorCode.PARAM_ERROR, "maxTokens 必须大于 0")
    is_baseline = 1 if payload.get("isBaseline") else 0
    if is_baseline:
        conn.execute("UPDATE biz_eval_config SET is_baseline = 0 WHERE project_id = ?", (project_id,))
    config_id = repo.insert(
        {
            "project_id": project_id,
            "name": name,
            "profile_id": payload.get("profileId"),
            "provider": str(payload.get("provider") or ""),
            "model": str(payload.get("model") or ""),
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "seed": payload.get("seed"),
            "prompt_code": str(payload.get("promptCode") or ""),
            "prompt_version": str(payload.get("promptVersion") or ""),
            "retrieval_json": dumps(payload.get("retrieval") or {}),
            "tools_json": dumps(payload.get("tools") or []),
            "is_baseline": is_baseline,
            "note": str(payload.get("note") or ""),
        }
    )
    conn.commit()
    return config_id


def update_config(conn: sqlite3.Connection, config_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """已引用配置不可改（160102）：配置不可变，改了就是新行，run 里存快照。"""

    row = _config_or_404(conn, config_id)
    repo = EvalConfigRepository(conn)
    if repo.reference_count(config_id) > 0:
        raise AppError(ErrorCode.EVAL_CONFIG_REFERENCED, "配置已被跑批引用，不可修改；请复制为新配置")
    update: dict[str, Any] = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise AppError(ErrorCode.PARAM_ERROR, "配置名称不能为空")
        duplicated = repo.find_by_name(int(row["project_id"]), name)
        if duplicated is not None and int(duplicated["id"]) != config_id:
            raise AppError(ErrorCode.PARAM_ERROR, "同名评测配置已存在")
        update["name"] = name
    if "temperature" in payload:
        temperature = _float(payload.get("temperature"), "temperature")
        if not 0.0 <= temperature <= 2.0:
            raise AppError(ErrorCode.PARAM_ERROR, "temperature 取值必须在 0–2")
        update["temperature"] = temperature
    if "topP" in payload or "top_p" in payload:
        top_p = _float(payload.get("topP", payload.get("top_p")), "topP")
        if not 0.0 < top_p <= 1.0:
            raise AppError(ErrorCode.PARAM_ERROR, "topP 取值必须在 (0, 1]")
        update["top_p"] = top_p
    for source, column in (
        ("provider", "provider"), ("model", "model"), ("maxTokens", "max_tokens"),
        ("seed", "seed"), ("promptCode", "prompt_code"), ("promptVersion", "prompt_version"),
        ("note", "note"), ("profileId", "profile_id"),
    ):
        if source in payload:
            update[column] = payload.get(source)
    if "retrieval" in payload:
        update["retrieval_json"] = dumps(payload.get("retrieval") or {})
    if "tools" in payload:
        update["tools_json"] = dumps(payload.get("tools") or [])
    if "isBaseline" in payload:
        baseline = 1 if payload.get("isBaseline") else 0
        if baseline:
            conn.execute(
                "UPDATE biz_eval_config SET is_baseline = 0 WHERE project_id = ? AND id <> ?",
                (int(row["project_id"]), config_id),
            )
        update["is_baseline"] = baseline
    if update:
        repo.update(config_id, update)
        conn.commit()
    return {"id": config_id, "updated": True}


# ---------------------------------------------------------------- 判定器


def create_judge(conn: sqlite3.Connection, payload: dict[str, Any]) -> int:
    project_id = _int(payload.get("projectId"), "projectId")
    name = str(payload.get("name") or "").strip()
    if not name:
        raise AppError(ErrorCode.PARAM_ERROR, "判定器名称不能为空")
    strategy = str(payload.get("strategy") or "single")
    if strategy not in JUDGE_STRATEGIES:
        raise AppError(ErrorCode.PARAM_ERROR, f"strategy 只能是 {sorted(JUDGE_STRATEGIES)}")
    panel_size = int(payload.get("panelSize") or (2 if strategy == "double" else 1))
    if panel_size < 1:
        raise AppError(ErrorCode.PARAM_ERROR, "panelSize 必须是正整数")
    if strategy == "panel" and panel_size % 2 == 0:
        raise AppError(ErrorCode.PARAM_ERROR, "panel 策略的 panelSize 必须是正奇数（取多数）")
    temperature = _float(payload.get("temperature", 0.0), "temperature")
    if not 0.0 <= temperature <= 2.0:
        raise AppError(ErrorCode.PARAM_ERROR, "temperature 取值必须在 0–2")
    judge_configs = payload.get("judgeConfigs") or []
    if not isinstance(judge_configs, list):
        raise AppError(ErrorCode.PARAM_ERROR, "judgeConfigs 必须是数组")
    judge_id = EvalJudgeRepository(conn).insert(
        {
            "project_id": project_id,
            "name": name,
            "profile_id": payload.get("profileId"),
            "prompt_code": str(payload.get("promptCode") or ""),
            "rubric_template": dumps(payload.get("rubricTemplate") or []),
            "judge_configs_json": dumps(judge_configs),
            "strategy": strategy,
            "panel_size": panel_size,
            "temperature": temperature,
            "seed": payload.get("seed"),
            "status": "active",
        }
    )
    conn.commit()
    return judge_id


# ---------------------------------------------------------------- 跑批


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


def _config_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "configId": int(row["id"]),
        "name": row["name"],
        "provider": row["provider"],
        "model": row["model"],
        "temperature": row["temperature"],
        "topP": row["top_p"],
        "maxTokens": row["max_tokens"],
        "seed": row["seed"],
        "promptCode": row["prompt_code"],
        "promptVersion": row["prompt_version"],
        "retrieval": loads(row.get("retrieval_json"), {}) or {},
        "tools": loads(row.get("tools_json"), []) or [],
    }


def _judge_snapshot(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "judgeId": int(row["id"]),
        "name": row["name"],
        "strategy": row["strategy"],
        "panelSize": row["panel_size"],
        "temperature": row["temperature"],
        "seed": row["seed"],
        "promptCode": row["prompt_code"],
        "judgeConfigs": loads(row.get("judge_configs_json"), []) or [],
    }


def create_run(conn: sqlite3.Connection, payload: dict[str, Any]) -> dict[str, Any]:
    """创建跑批：幂等 run_key（重复触发复用既有 run，不重复烧钱）。"""

    suite_id = _int(payload.get("suiteId"), "suiteId")
    suite = _suite_or_404(conn, suite_id)
    _require_active_suite(suite, "发起跑批")
    cases = EvalCaseRepository(conn).list_active(suite_id)
    if not cases:
        raise AppError(ErrorCode.PARAM_ERROR, "评测集没有可用用例，禁止发起跑批")

    config = payload.get("config") or {}
    judge = payload.get("judge") or {}
    if not isinstance(config, dict) or not isinstance(judge, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "config / judge 必须是对象")

    config_id = payload.get("configId")
    judge_id = payload.get("judgeId")
    if config_id not in (None, ""):
        stored = _config_or_404(conn, _int(config_id, "configId"))
        if int(stored["project_id"]) != int(suite["project_id"]):
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "评测配置不属于该评测集所在项目")
        config = {**_config_snapshot(stored), **config}
    if judge_id not in (None, ""):
        stored_judge = _judge_or_404(conn, _int(judge_id, "judgeId"))
        if int(stored_judge["project_id"]) != int(suite["project_id"]):
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "判定器不属于该评测集所在项目")
        judge = {**_judge_snapshot(stored_judge), **judge}

    judge_mode = str(judge.get("strategy") or "rule")
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
            "config_id": _int(config_id, "configId") if config_id not in (None, "") else None,
            "judge_id": _int(judge_id, "judgeId") if judge_id not in (None, "") else None,
            "judge_mode": judge_mode,
            "trigger_type": str(payload.get("triggerType") or "manual"),
            "ablation_group": str(payload.get("ablationGroup") or ""),
            "config_snapshot": dumps(config),
            "judge_snapshot": dumps(judge),
            "case_set_hash": case_hash,
            "run_purpose": purpose,
            "rerun_of": payload.get("rerunOf"),
            "run_key": run_key,
            "status": "queued",
            "total": len(cases),
            "cost_source": "estimated",
            "creator": str(payload.get("creator") or ""),
        }
    )
    conn.commit()
    return {"runId": run_id, "reused": False, "runKey": run_key}


def execute_replay(conn: sqlite3.Connection, run_id: int, outputs: Any) -> dict[str, Any]:
    """回放执行：委托给 eval_runner（判定链 + 心跳 + 取消 + 预算 + fencing）。"""

    _run_or_404(conn, run_id)
    return eval_runner.execute_run(conn, run_id, outputs)


def resume_run(conn: sqlite3.Connection, run_id: int, outputs: Any = None) -> dict[str, Any]:
    _run_or_404(conn, run_id)
    return eval_runner.resume_run(conn, run_id, outputs)


def cancel_run(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    _run_or_404(conn, run_id)
    return eval_runner.request_cancel(conn, run_id)


def record_human_scores(conn: sqlite3.Connection, run_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    """人评回填（难例优先的标注结果落库），随后重算人机一致/κ/false-pass 指标。"""

    _run_or_404(conn, run_id)
    items = payload.get("items") or payload.get("scores")
    if not isinstance(items, list) or not items:
        raise AppError(ErrorCode.PARAM_ERROR, "items 必须是非空数组")
    results = EvalResultRepository(conn).list_by_run(run_id)
    by_id = {int(row["id"]): row for row in results}
    by_case = {int(row["case_id"]): row for row in results}
    now = utcnow_iso()
    updated = 0
    for item in items:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "items 的每一项必须是对象")
        human = str(item.get("human") or item.get("verdict") or "").strip().lower()
        if human not in {"pass", "fail"}:
            raise AppError(ErrorCode.PARAM_ERROR, "人评结论只能是 pass / fail")
        target = None
        if item.get("resultId") not in (None, ""):
            target = by_id.get(_int(item.get("resultId"), "resultId"))
        elif item.get("caseId") not in (None, ""):
            target = by_case.get(_int(item.get("caseId"), "caseId"))
        if target is None:
            continue
        score = item.get("score")
        conn.execute(
            "UPDATE biz_eval_result SET human_verdict = ?, human_score = ?, human_by = ?, "
            "human_at = ?, updated_at = ? WHERE id = ?",
            (
                human,
                _float(score, "score") if score not in (None, "") else None,
                str(item.get("by") or payload.get("humanBy") or ""),
                now,
                now,
                int(target["id"]),
            ),
        )
        updated += 1
    if updated == 0:
        raise AppError(ErrorCode.PARAM_ERROR, "没有匹配到任何结果行，无法回填")
    conn.commit()
    metrics = eval_runner.recompute_run_metrics(conn, run_id)
    return {"updated": updated, "metrics": metrics}


def run_detail(conn: sqlite3.Connection, run_id: int) -> dict[str, Any]:
    run = _run_or_404(conn, run_id)
    metrics = EvalMetricRepository(conn).map_by_run(run_id)
    # DB 行序受索引影响会变字母序，对外一律按设计 §五 的固定 18 项顺序
    metric_order = [key for key in METRIC_KEYS if key in metrics]
    metric_order += [key for key in metrics if key not in metric_order]
    results = EvalResultRepository(conn).list_by_run(run_id)
    if results:
        ids = {int(row["case_id"]) for row in results}
        placeholders = ",".join("?" for _ in ids)
        case_rows = conn.execute(
            f"SELECT id, title, severity, category FROM biz_eval_case WHERE id IN ({placeholders})",
            list(ids),
        ).fetchall()
        case_map = {int(row["id"]): dict(row) for row in case_rows}
        for row in results:
            case = case_map.get(int(row["case_id"])) or {}
            row["severity"] = case.get("severity") or "S1"
            row["caseTitle"] = case.get("title") or ""
            row["category"] = case.get("category") or ""
    return {
        "run": {**run, "successRate": run.get("success_rate")},
        "metrics": metrics,
        "metricOrder": metric_order,
        "counts": EvalResultRepository(conn).verdict_counts(run_id),
        "results": results,
    }


# ---------------------------------------------------------------- 门禁与报告


def evaluate_run_gate(
    conn: sqlite3.Connection,
    run_id: int,
    thresholds: dict[str, Any] | None = None,
    gate_id: int | None = None,
) -> dict[str, Any]:
    run = _run_or_404(conn, run_id)
    metric_repo = EvalMetricRepository(conn)
    metrics = metric_repo.map_by_run(run_id)
    if not metrics:
        raise AppError(ErrorCode.PARAM_ERROR, "跑批还没有指标，先执行判定")

    gate_row: dict[str, Any] | None = None
    if gate_id is not None:
        gate_row = _gate_or_404(conn, gate_id)
        thresholds = loads(gate_row.get("thresholds_json"), {}) or thresholds
        if int(gate_row["suite_id"]) != int(run["suite_id"]):
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "门禁与跑批不属于同一个评测集")

    judge_calibrated: bool | None = None
    if run.get("judge_id") not in (None, ""):
        judge_calibrated = eval_calibration.is_calibrated(conn, int(run["judge_id"]))

    results = EvalResultRepository(conn).list_by_run(run_id)
    provider_cost = any(row.get("cost_source") == "provider" for row in results) if results else None

    regression: dict[str, Any] | None = None
    baseline_run_id = run.get("baseline_run_id") or (gate_row or {}).get("baseline_run_id")
    if baseline_run_id not in (None, ""):
        baseline = EvalRunRepository(conn).get(int(baseline_run_id))
        if baseline is not None:
            regression = paired_test(EvalResultRepository(conn).list_by_run(int(baseline_run_id)), results)

    result = evaluate_gate(
        metrics,
        thresholds,
        judge_calibrated=judge_calibrated,
        provider_cost=provider_cost,
        regression=regression,
    )
    conn.execute(
        "UPDATE biz_eval_run SET gate_conclusion = ?, updated_at = ? WHERE id = ?",
        (result["conclusion"], utcnow_iso(), run_id),
    )

    if gate_row is not None:
        snapshot = {
            key: {
                "value": row.get("value"),
                "ci_lower": row.get("ci_lower"),
                "ci_upper": row.get("ci_upper"),
                "sample_size": row.get("sample_size"),
            }
            for key, row in metrics.items()
        }
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
            "UPDATE biz_eval_gate SET last_run_id = ?, last_conclusion = ?, last_evaluated_at = ?, updated_at = ? "
            "WHERE id = ?",
            (run_id, result["conclusion"], utcnow_iso(), utcnow_iso(), int(gate_row["id"])),
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
        "INSERT INTO biz_eval_gate (project_id, suite_id, name, thresholds_json, is_hard, baseline_run_id, "
        "enabled, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 1, ?, ?)",
        (
            int(suite["project_id"]),
            suite_id,
            name,
            dumps(thresholds),
            1 if payload.get("isHard", True) else 0,
            payload.get("baselineRunId"),
            utcnow_iso(),
            utcnow_iso(),
        ),
    )
    conn.commit()
    return int(cursor.lastrowid or 0)


def update_gate(conn: sqlite3.Connection, gate_id: int, payload: dict[str, Any]) -> dict[str, Any]:
    _gate_or_404(conn, gate_id)
    update: dict[str, Any] = {}
    if "name" in payload:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise AppError(ErrorCode.PARAM_ERROR, "门禁名称不能为空")
        update["name"] = name
    if "thresholds" in payload:
        thresholds = payload.get("thresholds") or {}
        if not isinstance(thresholds, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "thresholds 必须是对象")
        update["thresholds_json"] = dumps(thresholds)
    if "isHard" in payload:
        update["is_hard"] = 1 if payload.get("isHard") else 0
    if "enabled" in payload:
        update["enabled"] = 1 if payload.get("enabled") else 0
    if "baselineRunId" in payload:
        update["baseline_run_id"] = payload.get("baselineRunId")
    if update:
        EvalGateRepository(conn).update(gate_id, update)
        conn.commit()
    return get_gate(conn, gate_id)


def get_gate(conn: sqlite3.Connection, gate_id: int) -> dict[str, Any]:
    row = _gate_or_404(conn, gate_id)
    repo = EvalGateRepository(conn)
    history = []
    for item in repo.list_results(gate_id):
        history.append(
            {
                **item,
                "failures": loads(item.get("failures_json"), []) or [],
                "ciSnapshot": loads(item.get("ci_snapshot"), {}) or {},
            }
        )
    return {**gate_summary(row), "history": history}


def evaluate_gate_by_id(
    conn: sqlite3.Connection, gate_id: int, run_id: int | None = None
) -> dict[str, Any]:
    gate = _gate_or_404(conn, gate_id)
    target_run_id = run_id
    if target_run_id is None:
        if gate.get("last_run_id") in (None, ""):
            latest = conn.execute(
                "SELECT id FROM biz_eval_run WHERE suite_id = ? AND status = 'finished' ORDER BY id DESC LIMIT 1",
                (int(gate["suite_id"]),),
            ).fetchone()
            if latest is None:
                raise AppError(ErrorCode.PARAM_ERROR, "该评测集还没有已完成的跑批可供求值")
            target_run_id = int(latest["id"])
    return evaluate_run_gate(conn, _int(target_run_id, "runId"), gate_id=gate_id)


def _metric_text(unit: str, value: float) -> str:
    """指标值按单位渲染：只有 ratio 才是百分比，时延/成本/步数不能乘 100。"""
    if unit == "ratio":
        return f"{value * 100:.2f}%"
    if unit == "ms":
        return f"{value:.0f} ms"
    if unit == "micros":
        return f"{value / 1_000_000:.4f} 元"
    if unit == "step":
        return f"{value:.2f}"
    return f"{value:.3f}"


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
        "## 指标（比率指标附 95% Wilson 区间）",
        "",
        "| 指标 | 值 | 95% 区间 | 样本量 |",
        "|---|---|---|---|",
    ]
    for key in METRIC_KEYS:
        row = metrics.get(key)
        if row is None:
            continue
        value = row.get("value")
        lower, upper = row.get("ci_lower"), row.get("ci_upper")
        unit = str(row.get("unit") or "")
        big = _metric_text(unit, float(value)) if value is not None else "-"
        ci = (
            f"[{_metric_text(unit, float(lower))}, {_metric_text(unit, float(upper))}]"
            if lower is not None and upper is not None
            else "-"
        )
        lines.append(f"| {key} | {big} | {ci} | {row.get('sample_size')} |")
    for key, row in sorted(metrics.items()):
        if key in METRIC_KEYS:
            continue
        value = row.get("value")
        big = _metric_text(str(row.get("unit") or ""), float(value)) if value is not None else "-"
        lines.append(f"| {key} | {big} | - | {row.get('sample_size')} |")

    sample = int(metrics.get("success_rate", {}).get("sample_size") or 0)
    if sample < 30:
        lines += ["", f"> **样本量 {sample} < 30：只出逐条证据，不出成败结论。**"]

    lines += [
        "",
        "## 逐条结果",
        "",
        "| 用例 | 级别 | 判定 | 失败原因 | 时延(ms) |",
        "|---|---|---|---|---|",
    ]
    for item in results:
        lines.append(
            f"| {item.get('case_no')} | {item.get('severity') or '-'} | {item.get('verdict')} | "
            f"{item.get('failure_reason') or '-'} | {item.get('latency_ms')} |"
        )
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- 对比 / 趋势


def compare_runs(
    conn: sqlite3.Connection, baseline_run_id: int, current_run_id: int, alpha: float = DEFAULT_ALPHA
) -> dict[str, Any]:
    """消融对比：同组校验 + 配对 McNemar + 指标差异（点估计 + 区间）。"""

    baseline = _run_or_404(conn, baseline_run_id)
    current = _run_or_404(conn, current_run_id)
    for run in (baseline, current):
        if run.get("status") != "finished":
            raise AppError(ErrorCode.EVAL_RUN_NOT_FINISHED, "跑批未结束，禁止对比")
    assert_comparable(baseline, current)

    baseline_results = EvalResultRepository(conn).list_by_run(baseline_run_id)
    current_results = EvalResultRepository(conn).list_by_run(current_run_id)
    paired = paired_test(baseline_results, current_results, alpha=alpha)

    baseline_metrics = EvalMetricRepository(conn).map_by_run(baseline_run_id)
    current_metrics = EvalMetricRepository(conn).map_by_run(current_run_id)
    diffs = []
    for key in METRIC_KEYS:
        before = baseline_metrics.get(key) or {}
        after = current_metrics.get(key) or {}
        before_value = before.get("value")
        after_value = after.get("value")
        delta = (
            float(after_value) - float(before_value)
            if before_value is not None and after_value is not None
            else None
        )
        diffs.append(
            {
                "key": key,
                "baseline": before_value,
                "current": after_value,
                "delta": round(delta, 6) if delta is not None else None,
                "baselineCi": [before.get("ci_lower"), before.get("ci_upper")],
                "currentCi": [after.get("ci_lower"), after.get("ci_upper")],
                "sampleSize": after.get("sample_size"),
            }
        )

    return {
        "baselineRunId": baseline_run_id,
        "currentRunId": current_run_id,
        "alpha": alpha,
        "paired": paired,
        "metrics": diffs,
        "mdeTable": mde_table(),
    }


def metrics_trends(
    conn: sqlite3.Connection, suite_id: int, metric_keys: list[str] | None = None, limit: int = 20
) -> dict[str, Any]:
    _suite_or_404(conn, suite_id)
    keys = [key for key in (metric_keys or list(METRIC_KEYS)) if key in METRIC_KEYS]
    if not keys:
        raise AppError(ErrorCode.PARAM_ERROR, "metric_keys 不在指标字典内")
    capped = max(1, min(int(limit or 20), 100))
    runs = conn.execute(
        "SELECT id, created_at, finished_at, success_rate, gate_conclusion FROM biz_eval_run "
        "WHERE suite_id = ? AND status = 'finished' ORDER BY id DESC LIMIT ?",
        (suite_id, capped),
    ).fetchall()
    ordered = list(reversed([dict(row) for row in runs]))
    if not ordered:
        return {"points": [], "series": {key: [] for key in keys}, "mdeTable": mde_table()}

    run_ids = [int(row["id"]) for row in ordered]
    placeholders = ",".join("?" for _ in run_ids)
    key_placeholders = ",".join("?" for _ in keys)
    rows = conn.execute(
        f"SELECT run_id, metric_key, value, ci_lower, ci_upper, sample_size FROM biz_eval_metric "
        f"WHERE run_id IN ({placeholders}) AND metric_key IN ({key_placeholders})",
        [*run_ids, *keys],
    ).fetchall()
    by_run: dict[int, dict[str, dict[str, Any]]] = {}
    for row in rows:
        by_run.setdefault(int(row["run_id"]), {})[str(row["metric_key"])] = {
            "value": row["value"],
            "lower": row["ci_lower"],
            "upper": row["ci_upper"],
            "sampleSize": row["sample_size"],
        }

    points = []
    series: dict[str, list[dict[str, Any]]] = {key: [] for key in keys}
    for run in ordered:
        run_id = int(run["id"])
        snapshot = by_run.get(run_id, {})
        points.append(
            {
                "runId": run_id,
                "createdAt": run.get("finished_at") or run.get("created_at"),
                "successRate": run.get("success_rate"),
                "gateConclusion": run.get("gate_conclusion"),
                "metrics": {key: snapshot.get(key) for key in keys},
            }
        )
        for key in keys:
            row = snapshot.get(key)
            if row is None:
                continue
            series[key].append({"runId": run_id, "createdAt": run.get("finished_at") or run.get("created_at"), **row})
    return {"points": points, "series": series, "mdeTable": mde_table()}


# ---------------------------------------------------------------- 导出 / 字典


def export_run(conn: sqlite3.Connection, run_id: int, fmt: str = "md") -> dict[str, Any]:
    """导出 md / xlsx；holdout 评测集一律拒绝（160107）。"""

    detail = run_detail(conn, run_id)
    run = detail["run"]
    suite = _suite_or_404(conn, int(run["suite_id"]))
    if suite.get("visibility") == "holdout":
        raise AppError(ErrorCode.EVAL_HOLDOUT_EXPORT_DENIED, "holdout 评测集禁止导出（防污染）")
    normalized_fmt = str(fmt or "md").strip().lower()
    if normalized_fmt not in EXPORT_FORMATS:
        raise AppError(ErrorCode.PARAM_ERROR, "format 只能是 md / xlsx")

    project = conn.execute(
        "SELECT name FROM biz_project WHERE id = ?", (int(run["project_id"]),)
    ).fetchone()
    project_name = str(project["name"]) if project else "AITP"
    if normalized_fmt == "md":
        content = run_report(conn, run_id).encode("utf-8")
    else:
        content = _export_xlsx(detail)
    filename = _safe_filename(f"{project_name}-评测报告-run{run_id}.{normalized_fmt}")
    return {
        "content": content,
        "filename": filename,
        "mimetype": EXPORT_FORMATS[normalized_fmt],
        "format": normalized_fmt,
        "count": len(detail["results"]),
    }


def _export_xlsx(detail: dict[str, Any]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill

    run = detail["run"]
    header_fill = PatternFill(start_color="DAEEF3", end_color="DAEEF3", fill_type="solid")
    header_font = Font(bold=True)

    def write_sheet(ws: Any, headers: tuple[str, ...], rows: list[list[Any]]) -> None:
        for index, header in enumerate(headers, start=1):
            cell = ws.cell(row=1, column=index, value=header)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        ws.freeze_panes = "A2"
        for row_index, row in enumerate(rows, start=2):
            for col_index, value in enumerate(row, start=1):
                ws.cell(row=row_index, column=col_index, value=value)

    workbook = Workbook()
    overview = workbook.active
    overview.title = "概览"
    write_sheet(
        overview,
        ("字段", "值"),
        [
            ["runId", int(run["id"])],
            ["评测集", int(run["suite_id"])],
            ["评测集版本", run.get("suite_version")],
            ["状态", run.get("status")],
            ["用例总数", run.get("total")],
            ["通过", run.get("passed")],
            ["失败", run.get("failed")],
            ["跳过", run.get("skipped")],
            ["错误", run.get("errors")],
            ["判定失效", run.get("judge_invalid")],
            ["成功率", run.get("success_rate")],
            ["门禁结论", run.get("gate_conclusion")],
            ["用例集哈希", run.get("case_set_hash")],
            ["创建时间", run.get("created_at")],
            ["完成时间", run.get("finished_at")],
        ],
    )
    metrics_sheet = workbook.create_sheet("指标")
    write_sheet(
        metrics_sheet,
        ("指标", "值", "下界", "上界", "方法", "单位", "样本量"),
        [
            [
                key,
                row.get("value"),
                row.get("ci_lower"),
                row.get("ci_upper"),
                row.get("ci_method"),
                row.get("unit"),
                row.get("sample_size"),
            ]
            for key, row in sorted(detail["metrics"].items())
        ],
    )
    results_sheet = workbook.create_sheet("逐条结果")
    write_sheet(
        results_sheet,
        ("用例", "标题", "级别", "判定", "失败原因", "得分", "时延(ms)", "成本(micros)"),
        [
            [
                row.get("case_no"),
                row.get("caseTitle"),
                row.get("severity"),
                row.get("verdict"),
                row.get("failure_reason"),
                row.get("score"),
                row.get("latency_ms"),
                row.get("cost_micros"),
            ]
            for row in detail["results"]
        ],
    )
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def _safe_filename(name: str) -> str:
    return "".join("-" if ch in '\\/:*?"<>|' else ch for ch in name).strip() or "eval-report"


def meta_dictionary() -> dict[str, Any]:
    """字典（分类 / 原因 / 结论 / 指标 key），供前端与 CLI 同一口径取枚举。"""

    return {
        "categories": sorted(CATEGORIES),
        "severities": [
            {"key": severity, "weight": SEVERITY_WEIGHTS[severity]}
            for severity in sorted(SEVERITIES)
        ],
        "targetTypes": sorted(TARGET_TYPES),
        "visibilities": sorted(VISIBILITIES),
        "suiteStatuses": ["active", "archived"],
        "caseStatuses": ["active", "archived"],
        "runStatuses": list(RUN_STATUSES),
        "judgeStrategies": ["single", "double", "panel"],
        "gateConclusions": ["pass", "warn", "insufficient", "fail"],
        "failureReasons": list(FAILURE_REASONS),
        "metricKeys": list(METRIC_KEYS),
        "thresholdKeys": list(THRESHOLD_METRIC_KEYS.keys()),
        "primaryMetrics": sorted(PRIMARY_METRICS),
        "exportFormats": sorted(EXPORT_FORMATS),
        "thresholdDefaults": DEFAULT_THRESHOLDS,
    }