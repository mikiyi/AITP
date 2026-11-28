"""判定器仓储：双评/仲裁配置与校准统计。"""

from __future__ import annotations

from typing import Any

from .base import BaseRepository, loads


class EvalJudgeRepository(BaseRepository):
    table = "biz_eval_judge"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "strategy", "calibrated_at", "created_at"}
    searchable = ["name", "prompt_code", "strategy"]

    def list_active(self, project_id: int) -> list[dict[str, Any]]:
        rows = self.raw(
            "SELECT * FROM biz_eval_judge WHERE project_id = ? AND is_deleted = 0 "
            "AND status = 'active' ORDER BY id",
            (project_id,),
        )
        return [dict(row) for row in rows]

    def calibration_stats(self, judge_id: int) -> dict[str, Any]:
        row = self.raw_one(
            "SELECT agreement_rate, kappa, false_pass_rate, calibrated_at, calibration_size "
            "FROM biz_eval_judge WHERE id = ?",
            (judge_id,),
        )
        if row is None:
            return {}
        return {
            "agreementRate": row["agreement_rate"],
            "kappa": row["kappa"],
            "falsePassRate": row["false_pass_rate"],
            "calibratedAt": row["calibrated_at"],
            "calibrationSize": row["calibration_size"],
        }


def judge_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "name": row.get("name"),
        "profileId": row.get("profile_id"),
        "promptCode": row.get("prompt_code"),
        "strategy": row.get("strategy"),
        "panelSize": row.get("panel_size"),
        "temperature": row.get("temperature"),
        "seed": row.get("seed"),
        "judgeConfigs": loads(row.get("judge_configs_json"), []) or [],
        "agreementRate": row.get("agreement_rate"),
        "kappa": row.get("kappa"),
        "falsePassRate": row.get("false_pass_rate"),
        "calibratedAt": row.get("calibrated_at"),
        "calibrationSize": row.get("calibration_size"),
        "status": row.get("status"),
        "createdAt": row.get("created_at"),
    }
