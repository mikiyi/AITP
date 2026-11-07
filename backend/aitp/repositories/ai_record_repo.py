"""AI 执行记录仓储。"""

from __future__ import annotations

from typing import Any

from ..ai.capability_codes import label_of
from ..common.pagination import MAX_PAGE_SIZE, Page, RowDict, build_page
from .base import BaseRepository, loads


def mask_of(raw: Any) -> dict[str, Any]:
    """脱敏信息补全成前端可直接用的完整结构；旧记录没有这一列就是未脱敏。"""
    data = raw if isinstance(raw, dict) else {}
    return {
        "enabled": bool(data.get("enabled", True)),
        "applied": bool(data.get("applied", False)),
        "types": list(data.get("types") or []),
        "count": int(data.get("count") or 0),
    }


class AIRecordRepository(BaseRepository):
    table = "biz_ai_record"
    default_order = "created_at"
    order_whitelist = {
        "id", "capability", "risk_level", "review_status", "created_at", "updated_at",
    }
    searchable = ["summary", "capability"]

    #: 记录本身没有 project_id，项目维度只能顺着产生它的任务收敛
    PROJECT_SCOPE_SQL = (
        " EXISTS (SELECT 1 FROM biz_ai_task t WHERE t.id = biz_ai_record.task_id"
        " AND t.is_deleted = 0 AND t.project_id = ?)"
    )

    def page_scoped(
        self,
        filters: dict[str, Any] | None,
        keyword: str | None,
        project_id: int | None,
        page_num: int,
        page_size: int,
    ) -> Page:
        where, params = self._build_where(filters, keyword)
        if project_id:
            where += " AND" + self.PROJECT_SCOPE_SQL
            params = [*params, int(project_id)]
        total = int(self.conn.execute(
            f"SELECT COUNT(*) FROM {self.table} WHERE {where}", params
        ).fetchone()[0])
        limit = max(1, min(int(page_size or 20), MAX_PAGE_SIZE))
        offset = (max(1, int(page_num or 1)) - 1) * limit
        rows = self.conn.execute(
            f"SELECT * FROM {self.table} WHERE {where} ORDER BY {self.default_order}"
            f" {self.default_order_dir.upper()} LIMIT ? OFFSET ?",
            [*params, limit, offset],
        ).fetchall()
        return build_page([dict(row) for row in rows], total, page_num, limit)

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if row is None:
            return None
        row["evidence"] = loads(row.pop("evidence_json", None), []) or []
        row["missing"] = loads(row.pop("missing_json", None), []) or []
        row["risks"] = loads(row.pop("risk_json", None), []) or []
        row["input"] = loads(row.pop("input_json", None), {}) or {}
        row["route"] = loads(row.pop("route_json", None), []) or []
        row["usage"] = loads(row.pop("usage_json", None), {}) or {}
        row["steps"] = loads(row.pop("steps_json", None), []) or []
        row["mask"] = mask_of(loads(row.pop("mask_json", None), {}))
        return row

    def decorate(self, row: RowDict, *, full: bool = False) -> dict[str, Any]:
        """把一行记录转成前端用的结构。

        列表只需要摘要，产出原文可能很长，默认不带；
        详情页和能力页的历史回看要走 ``full=True``。
        """
        capability = str(row.get("capability") or "")
        stored_label = str(row.get("capability_label") or "").strip()
        return {
            "id": row.get("id"),
            "taskId": row.get("task_id"),
            "sessionId": row.get("session_id"),
            "capability": capability,
            # 旧记录可能把能力码原样存成了标签（如 composite），读的时候补回中文名
            "capabilityLabel": label_of(capability) if not stored_label or stored_label == capability else stored_label,
            "model": row.get("model"),
            "summary": row.get("summary"),
            "outputText": str(row.get("output_text") or "") if full else "",
            "evidence": row.get("evidence") or loads(row.get("evidence_json"), []) or [],
            "missing": row.get("missing") or loads(row.get("missing_json"), []) or [],
            "risks": row.get("risks") or loads(row.get("risk_json"), []) or [],
            "riskLevel": row.get("risk_level"),
            "reviewRequired": bool(row.get("review_required")),
            "reviewStatus": row.get("review_status"),
            "reviewNote": row.get("review_note"),
            "reviewedAt": row.get("reviewed_at"),
            "input": row.get("input") or loads(row.get("input_json"), {}) or {},
            "route": row.get("route") or loads(row.get("route_json"), []) or [],
            "steps": row.get("steps") or loads(row.get("steps_json"), []) or [],
            "usage": row.get("usage") or loads(row.get("usage_json"), {}) or {},
            "promptCode": row.get("prompt_code"),
            "latencyMs": row.get("latency_ms"),
            "mask": mask_of(row.get("mask") or loads(row.get("mask_json"), {})),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    def count_by_review(
        self,
        status: str,
        project_id: int | None = None,
        review_required: bool | None = None,
    ) -> int:
        where = "review_status = ? AND is_deleted = 0"
        params: list[Any] = [status]
        if review_required is not None:
            where += " AND review_required = ?"
            params.append(1 if review_required else 0)
        if project_id:
            where += " AND" + self.PROJECT_SCOPE_SQL
            params.append(int(project_id))
        row = self.conn.execute(
            f"SELECT COUNT(*) FROM biz_ai_record WHERE {where}",
            params,
        ).fetchone()
        return int(row[0])

    def count_by_prompt_code(self) -> dict[str, int]:
        """统计每个提示词编号被使用的次数，用于提示词库按使用频次排序。"""
        rows = self.conn.execute(
            "SELECT prompt_code, COUNT(*) AS c FROM biz_ai_record"
            " WHERE is_deleted = 0 AND prompt_code IS NOT NULL AND prompt_code != ''"
            " GROUP BY prompt_code",
        ).fetchall()
        return {str(r["prompt_code"]): int(r["c"]) for r in rows}

    def count_by_capability(self) -> dict[str, int]:
        """统计每个能力被使用的次数，用于能力总览卡片标注使用频次。"""
        rows = self.conn.execute(
            "SELECT capability, COUNT(*) AS c FROM biz_ai_record"
            " WHERE is_deleted = 0 AND capability IS NOT NULL AND capability != ''"
            " GROUP BY capability",
        ).fetchall()
        return {str(r["capability"]): int(r["c"]) for r in rows}
