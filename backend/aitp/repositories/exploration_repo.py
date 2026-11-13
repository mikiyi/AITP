"""Web 探索性测试仓储（三期 GAP-45）。

会话与步骤两张表只留痕：会话承载目标/驱动/收敛统计，步骤承载
「读快照→决策→动作→观测」的每一步与异常锚点；证据与待审产物分别
挂在 biz_execution_evidence / biz_knowledge_draft，这里只存引用 id。
"""

from __future__ import annotations

from ..common.pagination import RowDict
from .base import BaseRepository
from .numbering import next_no


class ExplorationSessionRepository(BaseRepository):
    table = "biz_exploration_session"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "status", "driver", "created_at", "updated_at"}
    searchable = ["session_no", "name", "target_url"]

    def next_session_no(self) -> str:
        # 取号与紧随其后的插入同处一个写事务，避免并发撞号（见 numbering.begin_claim）
        return next_no(self.conn, self.table, "session_no", "EX")


class ExplorationStepRepository(BaseRepository):
    table = "biz_exploration_step"
    default_order = "step_no"
    order_whitelist = {"id", "step_no", "is_anomaly", "created_at"}

    def list_by_session(self, session_id: int) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM biz_exploration_step"
            " WHERE session_id = ? AND is_deleted = 0 ORDER BY step_no ASC, id ASC",
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def anomalies(self, session_id: int) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM biz_exploration_step"
            " WHERE session_id = ? AND is_anomaly = 1 AND is_deleted = 0"
            " ORDER BY step_no ASC, id ASC",
            (int(session_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def reset_for_run(self, session_id: int) -> None:
        """重跑前作废旧步骤，避免上一轮观测混进本轮。"""
        self.conn.execute(
            "UPDATE biz_exploration_step SET is_deleted = 1"
            " WHERE session_id = ? AND is_deleted = 0",
            (int(session_id),),
        )


def session_public(row: RowDict) -> RowDict:
    import json

    def _json(column: str, default: object) -> object:
        try:
            return json.loads(str(row.get(column) or "") or ("[]" if default == [] else "{}"))
        except (TypeError, ValueError):
            return default

    return {
        "id": int(row["id"]),
        "sessionNo": str(row["session_no"] or ""),
        "projectId": int(row["project_id"]),
        "name": str(row["name"] or ""),
        "targetUrl": str(row["target_url"] or ""),
        "driver": str(row["driver"] or "scripted"),
        "status": str(row["status"] or "draft"),
        "taskId": row.get("task_id"),
        "maxSteps": int(row["max_steps"] or 0),
        "maxSeconds": int(row["max_seconds"] or 0),
        "settings": _json("settings_json", {}),
        "plan": _json("plan_json", []),
        "stepTotal": int(row["step_total"] or 0),
        "stepDone": int(row["step_done"] or 0),
        "anomalyCount": int(row["anomaly_count"] or 0),
        "draftCount": int(row["draft_count"] or 0),
        "degraded": bool(row.get("degraded")),
        "summary": _json("summary_json", {}),
        "errorMessage": str(row.get("error_message") or ""),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
        "createdBy": str(row.get("created_by") or ""),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def step_public(row: RowDict) -> RowDict:
    import json

    try:
        detail = json.loads(str(row.get("detail_json") or "{}"))
    except (TypeError, ValueError):
        detail = {}
    return {
        "id": int(row["id"]),
        "sessionId": int(row["session_id"]),
        "stepNo": int(row["step_no"] or 0),
        "action": str(row["action"] or ""),
        "selector": str(row.get("selector") or ""),
        "pageUrl": str(row.get("page_url") or ""),
        "inputMasked": str(row.get("input_masked") or ""),
        "snapshotSummary": str(row.get("snapshot_summary") or ""),
        "observation": str(row.get("observation") or ""),
        "isAnomaly": bool(row.get("is_anomaly")),
        "anomalyType": str(row.get("anomaly_type") or ""),
        "detectedAt": str(row.get("detected_at") or ""),
        "lastOkAt": str(row.get("last_ok_at") or ""),
        "evidenceId": row.get("evidence_id"),
        "draftId": row.get("draft_id"),
        "traceId": str(row.get("trace_id") or ""),
        "durationMs": int(row.get("duration_ms") or 0),
        "detail": detail if isinstance(detail, dict) else {},
        "createdAt": row.get("created_at"),
    }