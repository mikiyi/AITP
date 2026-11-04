"""SQL 分析资产仓储。

一条记录 = 一段被分析的 SQL + 一份分析结论；
分析结论按 Markdown 存，和 AI 产出原文保持一致，便于回看与复制。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository
from .numbering import next_no

#: 风险等级：无 / 低 / 高
RISK_LEVELS = ("none", "low", "high")

RISK_LABELS = {"none": "无风险", "low": "低风险", "high": "高风险"}


def decorate(row: RowDict) -> dict[str, Any]:
    risk = str(row.get("risk_level") or "none")
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "sqlNo": row.get("sql_no"),
        "title": row.get("title"),
        "sqlText": row.get("sql_text") or "",
        "analysis": row.get("analysis") or "",
        "riskLevel": risk,
        "riskLabel": RISK_LABELS.get(risk, risk),
        "source": row.get("source") or "",
        "tags": row.get("tags") or "",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


class SqlAnalysisRepository(BaseRepository):
    table = "biz_sql_analysis"
    default_order = "created_at"
    order_whitelist = {"id", "sql_no", "title", "risk_level", "created_at", "updated_at"}
    searchable = ["sql_no", "title", "sql_text", "analysis", "tags"]

    def next_sql_no(self, project_id: int) -> str:
        return next_no(self.conn, self.table, "sql_no", "SQL", project_id)
