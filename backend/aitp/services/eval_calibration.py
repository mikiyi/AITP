"""人评对拍与 judge 校准（设计 §6.3 / §6.6）。

难例优先采样 → 人评回填 → 一致率 / Cohen's κ / false-pass / 位置与冗长偏差，
落 biz_eval_calibration 并回写 judge 档案统计。校准准入：false-pass ≤ 5%、
κ ≥ 0.6、一致率 ≥ 0.8，三者同时满足才算「校准可用」。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..eval.judges import calibration_summary, position_bias, verbosity_bias
from ..repositories.base import dumps, loads
from ..repositories.eval_judge_repo import EvalJudgeRepository
from ..repositories.eval_result_repo import EvalResultRepository


def hard_case_sample(conn: sqlite3.Connection, run_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """难例优先：S0/S1、历史 fail、低分、judge 分歧的用例先入样。"""

    rows = conn.execute(
        """
        SELECT r.id AS result_id, r.case_id, r.case_no, r.verdict, r.score, r.failure_reason,
               r.judge_json, c.severity, c.title
        FROM biz_eval_result r
        JOIN biz_eval_case c ON c.id = r.case_id
        WHERE r.run_id = ?
        ORDER BY CASE c.severity WHEN 'S0' THEN 0 WHEN 'S1' THEN 1 ELSE 2 END,
                 CASE r.verdict WHEN 'fail' THEN 0 WHEN 'invalid' THEN 1 ELSE 2 END,
                 r.score ASC
        LIMIT ?
        """,
        (run_id, max(1, min(int(limit), 200))),
    ).fetchall()
    sample = []
    for row in rows:
        item = dict(row)
        judge_json = loads(item.get("judge_json"), {}) or {}
        item["judgeLabel"] = judge_json.get("judge_label") or (
            "pass" if item.get("verdict") == "pass" else "fail"
        )
        sample.append(item)
    return sample


def _judge_label_of(row: sqlite3.Row | dict[str, Any]) -> str | None:
    judge_json = loads(dict(row).get("judge_json"), {}) or {}
    label = judge_json.get("judge_label")
    if label in {"pass", "fail"}:
        return str(label)
    verdict = dict(row).get("verdict")
    return str(verdict) if verdict in {"pass", "fail"} else None


def calibrate(
    conn: sqlite3.Connection,
    judge_id: int,
    run_id: int,
    items: list[dict[str, Any]],
    bias_samples: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """按人评回填项做一次校准实验并落库。

    items: [{"resultId"|"caseId": int, "human": "pass"|"fail"}]
    bias_samples: 可选 {"position": [...], "verbosity": [...]}，用于偏差归因。
    """

    judge = EvalJudgeRepository(conn).get(judge_id)
    if judge is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "判定器不存在")

    result_rows = EvalResultRepository(conn).list_by_run(run_id)
    by_case = {int(row["case_id"]): row for row in result_rows}
    by_id = {int(row["id"]): row for row in result_rows}

    human_labels: list[str] = []
    judge_labels: list[str] = []
    used = 0
    for item in items:
        human = str(item.get("human") or "").strip().lower()
        if human not in {"pass", "fail"}:
            raise AppError(ErrorCode.PARAM_ERROR, "人评结论只能是 pass / fail")
        row = by_id.get(int(item["resultId"])) if item.get("resultId") else by_case.get(int(item.get("caseId") or 0))
        if row is None:
            continue
        label = _judge_label_of(row)
        if label is None:
            continue
        human_labels.append(human)
        judge_labels.append(label)
        used += 1

    if not human_labels:
        raise AppError(ErrorCode.PARAM_ERROR, "没有可对拍的样本，无法校准")

    summary = calibration_summary(human_labels, judge_labels)
    detail: dict[str, Any] = {"matrix": summary["matrix"]}
    bias_samples = bias_samples or {}
    if bias_samples.get("position"):
        detail["positionBias"] = position_bias(bias_samples["position"])
    if bias_samples.get("verbosity"):
        detail["verbosityBias"] = verbosity_bias(bias_samples["verbosity"])

    now = utcnow_iso()
    cursor = conn.execute(
        """
        INSERT INTO biz_eval_calibration
            (judge_id, run_id, sample_size, agreement_before, agreement_after, kappa_after,
             false_pass_rate, false_fail_rate, position_bias, verbosity_bias, detail_json,
             schema_version, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
        """,
        (
            judge_id, run_id, used, None, summary["agreement"], summary["kappa"],
            summary["falsePassRate"], summary["falseFailRate"],
            detail.get("positionBias"), detail.get("verbosityBias"),
            dumps(detail), now,
        ),
    )
    conn.execute(
        "UPDATE biz_eval_judge SET agreement_rate = ?, kappa = ?, false_pass_rate = ?, "
        "calibrated_at = ?, calibration_size = ? WHERE id = ?",
        (summary["agreement"], summary["kappa"], summary["falsePassRate"], now, used, judge_id),
    )
    conn.commit()
    return {
        "calibrationId": int(cursor.lastrowid or 0),
        "judgeId": judge_id,
        "runId": run_id,
        **summary,
        "detail": detail,
    }


def list_calibrations(conn: sqlite3.Connection, judge_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_eval_calibration WHERE judge_id = ? ORDER BY id DESC",
        (judge_id,),
    ).fetchall()
    result = []
    for row in rows:
        item = dict(row)
        item["detail"] = loads(item.pop("detail_json", None), {}) or {}
        result.append(item)
    return result


def is_calibrated(conn: sqlite3.Connection, judge_id: int) -> bool:
    """门禁准入判断：校准三件套同时达标。"""

    judge = EvalJudgeRepository(conn).get(judge_id)
    if judge is None or not judge.get("calibrated_at"):
        return False
    agreement = judge.get("agreement_rate")
    kappa = judge.get("kappa")
    false_pass = judge.get("false_pass_rate")
    return (
        agreement is not None and float(agreement) >= 0.8
        and kappa is not None and float(kappa) >= 0.6
        and (false_pass is None or float(false_pass) <= 0.05)
    )
