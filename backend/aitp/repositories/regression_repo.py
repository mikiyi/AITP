"""回归清单仓储。

回归清单是一份「改了什么、要回归什么」的清单：主表是清单本身，
明细表是逐条待验证项。清单可以由 AI 生成，也可以手工维护；
执行时逐条回写结果，整单状态随之推进。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import Page, RowDict, RowList
from .base import BaseRepository
from .numbering import begin_claim

#: 清单状态
REGRESSION_DRAFT = "draft"
REGRESSION_READY = "ready"
REGRESSION_EXECUTING = "executing"
REGRESSION_DONE = "done"

REGRESSION_STATUSES = (REGRESSION_DRAFT, REGRESSION_READY, REGRESSION_EXECUTING, REGRESSION_DONE)

#: 明细执行结果
ITEM_NOT_EXECUTED = "not_executed"
ITEM_PASSED = "passed"
ITEM_FAILED = "failed"
ITEM_BLOCKED = "blocked"
ITEM_SKIPPED = "skipped"

ITEM_RESULTS = (ITEM_NOT_EXECUTED, ITEM_PASSED, ITEM_FAILED, ITEM_BLOCKED, ITEM_SKIPPED)

#: 风险等级
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

RISK_LEVELS = (RISK_LOW, RISK_MEDIUM, RISK_HIGH)


class RegressionRepository(BaseRepository):
    table = "biz_regression"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {
        "id", "title", "status", "risk_level", "version", "created_at", "updated_at",
    }
    searchable = ["title", "change_scope", "affected_modules", "content"]

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if row is None:
            return None
        row["items"] = RegressionItemRepository(self.conn).list_by_regression(pk)
        return row

    def stats_of(self, pk: int) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT result, COUNT(*) AS c FROM biz_regression_item"
            " WHERE regression_id = ? AND is_deleted = 0 GROUP BY result",
            (pk,),
        ).fetchall()
        counts = {result: 0 for result in ITEM_RESULTS}
        for row in rows:
            counts[str(row["result"])] = int(row["c"])
        # GAP-33：自动执行占比要能统计——分子是「已真跑的自动条目」，分母是全部条目
        auto = self.conn.execute(
            "SELECT COUNT(*) AS total,"
            " SUM(CASE WHEN result <> 'not_executed' THEN 1 ELSE 0 END) AS ran,"
            " SUM(CASE WHEN evidence_id IS NOT NULL THEN 1 ELSE 0 END) AS evidenced"
            " FROM biz_regression_item"
            " WHERE regression_id = ? AND is_deleted = 0 AND exec_mode = 'auto'",
            (pk,),
        ).fetchone()
        counts["total"] = sum(counts[key] for key in ITEM_RESULTS)
        counts["autoTotal"] = int(auto["total"] or 0)
        counts["autoExecuted"] = int(auto["ran"] or 0)
        counts["autoEvidenced"] = int(auto["evidenced"] or 0)
        counts["manualTotal"] = counts["total"] - counts["autoTotal"]
        counts["executed"] = counts[ITEM_PASSED] + counts[ITEM_FAILED] + counts[ITEM_BLOCKED]
        counts["evidenced"] = int(self.conn.execute(
            "SELECT COUNT(*) FROM biz_regression_item"
            " WHERE regression_id = ? AND is_deleted = 0 AND evidence_id IS NOT NULL",
            (pk,),
        ).fetchone()[0])
        return counts

    def sync_status(self, pk: int) -> str:
        """按明细结果回推整单状态。"""
        counts = self.stats_of(pk)
        total = sum(counts[result] for result in ITEM_RESULTS)
        if total == 0:
            status = REGRESSION_DRAFT
        elif counts[ITEM_NOT_EXECUTED] > 0:
            status = REGRESSION_EXECUTING
        elif counts[ITEM_FAILED] > 0:
            status = REGRESSION_DONE
        else:
            status = REGRESSION_DONE
        self.update(pk, {"status": status})
        return status


class RegressionItemRepository(BaseRepository):
    table = "biz_regression_item"
    default_order = "seq"
    default_order_dir = "asc"
    order_whitelist = {"id", "regression_id", "seq", "priority", "result", "created_at"}
    searchable = ["title", "module", "reason", "verify_target"]

    def list_by_regression(self, regression_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_regression_item WHERE regression_id = ? AND is_deleted = 0"
            " ORDER BY seq ASC, id ASC",
            (regression_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def next_seq(self, regression_id: int) -> int:
        """明细序号取号：读放进写事务，避免并发追加拿到同一个 seq（同 GAP-26 编号口径）。

        seq 本身没有唯一索引，撞号不会报错，但会让两条回归点排在同一位、
        前端按 seq 定位时丢一条——所以按「不重复、不丢号」同标准处理。
        """
        begin_claim(self.conn)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM biz_regression_item"
            " WHERE regression_id = ? AND is_deleted = 0",
            (regression_id,),
        ).fetchone()
        return int(row[0]) + 1

    def replace_all(self, regression_id: int, items: list[dict[str, Any]]) -> int:
        """用新清单覆盖旧明细（AI 重新生成时用）。"""
        self.conn.execute(
            "UPDATE biz_regression_item SET is_deleted = 1 WHERE regression_id = ?", (regression_id,)
        )
        created = 0
        for index, item in enumerate(items, start=1):
            self.insert({
                "regression_id": regression_id,
                "seq": int(item.get("seq") or index),
                "title": str(item.get("title") or ""),
                "module": str(item.get("module") or ""),
                "priority": str(item.get("priority") or "P1"),
                "reason": str(item.get("reason") or ""),
                "verify_target": str(item.get("verifyTarget") or item.get("verify_target") or ""),
                "result": ITEM_NOT_EXECUTED,
                "remark": "",
            })
            created += 1
        return created

    def list_detail(self, filters: dict[str, Any] | None = None,
                    keyword: str | None = None,
                    page_num: int = 1, page_size: int = 20) -> Page:
        return self.list(filters, keyword, page_num=page_num, page_size=page_size)


def regression_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "title": row.get("title"),
        "changeScope": row.get("change_scope"),
        "affectedModules": row.get("affected_modules"),
        "version": row.get("version"),
        "versionId": row.get("version_id"),
        "riskLevel": row.get("risk_level"),
        "status": row.get("status"),
        "source": row.get("source"),
        "aiTaskId": row.get("ai_task_id"),
        "content": row.get("content"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def item_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "regressionId": row.get("regression_id"),
        "seq": row.get("seq"),
        "title": row.get("title"),
        "module": row.get("module"),
        "priority": row.get("priority"),
        "reason": row.get("reason"),
        "verifyTarget": row.get("verify_target"),
        "result": row.get("result"),
        "remark": row.get("remark"),
        "executedBy": row.get("executed_by"),
        "executedAt": row.get("executed_at"),
        "createdAt": row.get("created_at"),
        # GAP-33：执行方式与真跑定位字段（execMode=auto 的条目由服务端真跑回写）
        "execMode": row.get("exec_mode") or "manual",
        "caseId": row.get("case_id"),
        "envId": row.get("env_id"),
        "evidenceId": row.get("evidence_id"),
    }
