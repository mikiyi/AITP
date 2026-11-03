"""测试报告、报告分段与模板仓储。"""

from __future__ import annotations

import json
from typing import Any

from ..common.pagination import RowDict, RowList
from .base import BaseRepository, loads
from .numbering import next_no


class ReportRepository(BaseRepository):
    table = "biz_report"
    default_order = "created_at"
    order_whitelist = {"id", "report_no", "title", "report_type", "status", "created_at", "updated_at"}
    searchable = ["report_no", "title", "summary", "content"]

    def next_report_no(self, project_id: int) -> str:
        return next_no(self.conn, self.table, "report_no", "RPT", project_id)

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if not row:
            return None
        row["items"] = self.list_items(pk)
        return row

    def list_items(self, report_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_report_item WHERE report_id = ? AND is_deleted = 0"
            " ORDER BY sort_order ASC, id ASC",
            (report_id,),
        ).fetchall()
        items: RowList = []
        for row in rows:
            item = dict(row)
            item["metric"] = loads(item.get("metric_json"), {}) or {}
            item.pop("metric_json", None)
            items.append(item)
        return items

    def replace_items(self, report_id: int, items: list[dict[str, Any]]) -> None:
        self.conn.execute(
            "DELETE FROM biz_report_item WHERE report_id = ?", (report_id,)
        )
        for order, item in enumerate(items):
            metric = item.get("metric") or {}
            self.conn.execute(
                """
                INSERT INTO biz_report_item
                    (report_id, section, title, content, metric_json, sort_order, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))
                """,
                (
                    report_id,
                    item.get("section", "overview"),
                    item.get("title"),
                    item.get("content"),
                    json.dumps(metric, ensure_ascii=False),
                    order,
                ),
            )


class ReportTemplateRepository(BaseRepository):
    table = "biz_report_template"
    default_order = "is_default, id"
    default_order_dir = "asc"
    order_whitelist = {"id", "name", "code", "is_default", "created_at"}
    searchable = ["name", "code", "description"]

    def get_default(self) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_report_template"
            " WHERE is_deleted = 0 AND is_active = 1 AND is_default = 1"
            " ORDER BY id ASC LIMIT 1"
        ).fetchone()
        if not row:
            row = self.conn.execute(
                "SELECT * FROM biz_report_template"
                " WHERE is_deleted = 0 AND is_active = 1 ORDER BY id ASC LIMIT 1"
            ).fetchone()
        if not row:
            return None
        template = dict(row)
        template["sections"] = loads(template.get("sections_json"), []) or []
        template.pop("sections_json", None)
        return template
