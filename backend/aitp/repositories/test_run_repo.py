"""测试轮次仓储。

一个轮次 = 一次具体执行。轮次创建时把用例内容复制成快照，
之后用例本身被修改或删除，都不影响历史轮次的结果与追溯。
结果写入时同步补一条 biz_case_execution 流水，并回写用例的全局状态。
"""

from __future__ import annotations

from typing import Any, Iterable

from ..common.pagination import RowDict, RowList
from ..common.time_utils import utcnow_iso
from .base import BaseRepository, dumps, loads
from .numbering import begin_claim

RUN_PENDING = "pending"
RUN_RUNNING = "running"
RUN_COMPLETED = "completed"
RUN_ABORTED = "aborted"
RUN_STATUSES = (RUN_PENDING, RUN_RUNNING, RUN_COMPLETED, RUN_ABORTED)

RUN_NOT_EXECUTED = "not_executed"
RUN_PASSED = "passed"
RUN_FAILED = "failed"
RUN_BLOCKED = "blocked"
RUN_SKIPPED = "skipped"
RUN_CASE_RESULTS = (RUN_NOT_EXECUTED, RUN_PASSED, RUN_FAILED, RUN_BLOCKED, RUN_SKIPPED)

#: 轮次结果 → 用例全局状态
CASE_STATUS_OF = {
    RUN_NOT_EXECUTED: "not_executed",
    RUN_PASSED: "pass",
    RUN_FAILED: "fail",
    RUN_BLOCKED: "blocked",
    RUN_SKIPPED: "not_executed",
}

RESULT_LABELS = {
    RUN_NOT_EXECUTED: "未执行",
    RUN_PASSED: "通过",
    RUN_FAILED: "失败",
    RUN_BLOCKED: "阻塞",
    RUN_SKIPPED: "跳过",
}


class TestRunRepository(BaseRepository):
    table = "biz_test_run"
    default_order = "created_at"
    default_order_dir = "desc"
    order_whitelist = {"id", "name", "status", "created_at", "updated_at", "finished_at"}
    searchable = ["name", "env", "owner", "remark"]

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if row is None:
            return None
        row["stats"] = self.stats_of(pk)
        return row

    def stats_of(self, pk: int) -> dict[str, Any]:
        rows = self.conn.execute(
            "SELECT result, priority, COUNT(*) AS c FROM biz_test_run_case"
            " WHERE run_id = ? AND is_deleted = 0 GROUP BY result, priority",
            (pk,),
        ).fetchall()
        counts = {result: 0 for result in RUN_CASE_RESULTS}
        by_priority: dict[str, dict[str, int]] = {}
        for row in rows:
            result = str(row["result"])
            count = int(row["c"])
            counts[result] = counts.get(result, 0) + count
            priority = str(row["priority"] or "P2")
            bucket = by_priority.setdefault(
                priority, {key: 0 for key in RUN_CASE_RESULTS}
            )
            bucket[result] = bucket.get(result, 0) + count
        total = sum(counts.values())
        executed = total - counts.get(RUN_NOT_EXECUTED, 0)
        return {
            "total": total,
            "counts": counts,
            "byPriority": by_priority,
            "passRate": round(counts.get(RUN_PASSED, 0) / executed, 4) if executed else 0.0,
        }

    def refresh_counters(self, pk: int, finish: bool = False) -> dict[str, Any]:
        stats = self.stats_of(pk)
        counts = stats["counts"]
        payload: dict[str, Any] = {
            "total": stats["total"],
            "passed": counts.get(RUN_PASSED, 0),
            "failed": counts.get(RUN_FAILED, 0),
            "blocked": counts.get(RUN_BLOCKED, 0),
            "skipped": counts.get(RUN_SKIPPED, 0),
            "not_executed": counts.get(RUN_NOT_EXECUTED, 0),
        }
        row = self.get(pk)
        status = str((row or {}).get("status") or RUN_PENDING)
        if finish:
            payload["status"] = RUN_COMPLETED
            payload["finished_at"] = utcnow_iso()
        elif stats["total"] > 0 and counts.get(RUN_NOT_EXECUTED, 0) == 0 and status in (RUN_PENDING, RUN_RUNNING):
            payload["status"] = RUN_COMPLETED
            payload["finished_at"] = utcnow_iso()
        elif stats["total"] > 0 and status == RUN_PENDING:
            payload["status"] = RUN_RUNNING
            payload["started_at"] = (row or {}).get("started_at") or utcnow_iso()
        self.update(pk, payload)
        return stats

    def latest_completed(self, project_id: int, version_id: int | None = None) -> RowDict | None:
        sql = (
            "SELECT * FROM biz_test_run WHERE project_id = ? AND is_deleted = 0"
            " AND status = 'completed'"
        )
        params: list[Any] = [project_id]
        if version_id is not None:
            sql += " AND version_id = ?"
            params.append(version_id)
        sql += " ORDER BY finished_at DESC, id DESC LIMIT 1"
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None


class TestRunCaseRepository(BaseRepository):
    table = "biz_test_run_case"
    default_order = "seq"
    default_order_dir = "asc"
    order_whitelist = {"id", "run_id", "seq", "priority", "result", "created_at"}
    searchable = ["case_no", "title", "module", "actual_result", "remark"]

    def list_by_run(self, run_id: int) -> RowList:
        rows = self.conn.execute(
            "SELECT * FROM biz_test_run_case WHERE run_id = ? AND is_deleted = 0"
            " ORDER BY seq ASC, id ASC",
            (run_id,),
        ).fetchall()
        return [_with_steps(dict(row)) for row in rows]

    def get_of_run(self, run_id: int, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_test_run_case WHERE id = ? AND run_id = ? AND is_deleted = 0",
            (pk, run_id),
        ).fetchone()
        return _with_steps(dict(row)) if row else None

    def existing_case_ids(self, run_id: int, case_ids: Iterable[int]) -> set[int]:
        ids = list(case_ids)
        if not ids:
            return set()
        placeholders = ",".join("?" for _ in ids)
        rows = self.conn.execute(
            "SELECT case_id FROM biz_test_run_case WHERE run_id = ? AND is_deleted = 0"
            f" AND case_id IN ({placeholders})",
            [run_id, *ids],
        ).fetchall()
        return {int(row["case_id"]) for row in rows}

    def next_seq(self, run_id: int) -> int:
        """轮次内快照序号：与取号同事务，并发追加用例不会拿到重复 seq。

        建轮次与追加用例都紧跟着一串 INSERT，写锁持到调用方 commit 为止，
        持锁时长与这批写入本身一致（GAP-26 编号口径）。
        """
        begin_claim(self.conn)
        row = self.conn.execute(
            "SELECT COALESCE(MAX(seq), 0) FROM biz_test_run_case"
            " WHERE run_id = ? AND is_deleted = 0",
            (run_id,),
        ).fetchone()
        return int(row[0]) + 1

    def add_snapshot(self, run_id: int, case_row: dict[str, Any], seq: int,
                     module_name: str = "") -> int:
        return self.insert({
            "run_id": run_id,
            "case_id": case_row["id"],
            "seq": seq,
            "case_no": str(case_row.get("case_no") or ""),
            "title": str(case_row.get("title") or ""),
            "case_type": str(case_row.get("case_type") or "functional"),
            "priority": str(case_row.get("priority") or "P2"),
            "module_id": case_row.get("module_id"),
            "module": module_name,
            "requirement_id": case_row.get("requirement_id"),
            "preconditions": str(case_row.get("preconditions") or ""),
            "steps_json": str(case_row.get("steps_json") or "[]"),
            "expected_result": str(case_row.get("expected_result") or ""),
            "extra_json": str(case_row.get("extra_json") or "{}"),
            "result": RUN_NOT_EXECUTED,
        })


def _with_steps(row: dict[str, Any]) -> dict[str, Any]:
    row["steps"] = loads(row.pop("steps_json", None), []) or []
    row["extra"] = loads(row.pop("extra_json", None), {}) or {}
    return row


def run_case_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "runId": row.get("run_id"),
        "caseId": row.get("case_id"),
        "seq": row.get("seq"),
        "caseNo": row.get("case_no"),
        "title": row.get("title"),
        "caseType": row.get("case_type"),
        "priority": row.get("priority"),
        "moduleId": row.get("module_id"),
        "module": row.get("module"),
        "requirementId": row.get("requirement_id"),
        "preconditions": row.get("preconditions"),
        "steps": row.get("steps") or [],
        "extra": row.get("extra") or {},
        "expectedResult": row.get("expected_result"),
        "result": row.get("result"),
        "actualResult": row.get("actual_result"),
        "remark": row.get("remark"),
        "bugId": row.get("bug_id"),
        "executedBy": row.get("executed_by"),
        "executedAt": row.get("executed_at"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def run_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "planId": row.get("plan_id"),
        "projectId": row.get("project_id"),
        "versionId": row.get("version_id"),
        "envId": row.get("env_id"),
        "name": row.get("name"),
        "env": row.get("env"),
        "owner": row.get("owner"),
        "status": row.get("status"),
        "remark": row.get("remark"),
        "total": row.get("total"),
        "passed": row.get("passed"),
        "failed": row.get("failed"),
        "blocked": row.get("blocked"),
        "skipped": row.get("skipped"),
        "notExecuted": row.get("not_executed"),
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
        "stats": row.get("stats"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }
