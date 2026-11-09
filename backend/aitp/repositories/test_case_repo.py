"""测试用例仓储。"""

from __future__ import annotations

import json
from typing import Any

from ..common.enums import CASE_PREFIX
from ..common.pagination import Page, RowDict, RowList
from .base import BaseRepository, dumps, loads
from .numbering import next_case_no


class TestCaseRepository(BaseRepository):
    table = "biz_test_case"
    default_order = "created_at"
    order_whitelist = {
        "id", "case_no", "title", "case_type", "priority", "status",
        "created_at", "updated_at",
    }
    searchable = ["case_no", "title", "preconditions", "expected_result", "tags"]

    def next_case_no(self, project_id: int, case_type: str = "functional") -> str:
        prefix = CASE_PREFIX.get(case_type, "FT")
        return next_case_no(
            self.conn, self.table, "case_no", project_id, prefix, case_type
        )

    def get_detail(self, pk: int) -> RowDict | None:
        row = self.get(pk)
        if not row:
            return None
        row["steps"] = loads(row.get("steps_json"), []) or []
        row.pop("steps_json", None)
        row["extra"] = loads(row.get("extra_json"), {}) or {}
        row.pop("extra_json", None)
        return row

    def list_detail(self, filters: dict[str, Any] | None = None, keyword: str | None = None,
                    page_num: int = 1, page_size: int = 20,
                    order_by: str | None = None, order: str = "desc") -> Page:
        page = self.list(filters, keyword, page_num, page_size, order_by, order)
        for row in page.list:
            row["steps"] = loads(row.get("steps_json"), []) or []
            row.pop("steps_json", None)
            row["extra"] = loads(row.get("extra_json"), {}) or {}
            row.pop("extra_json", None)
        return page

    def tree_counts(self, filters: dict[str, Any] | None = None) -> dict[str, Any]:
        """用例树的节点数字：按「功能点 × 执行状态」一次汇总。

        万级用例下不能再把整表读进内存数数（原 list_all(limit=20000) 的读放大就在这里），
        SQL 汇总出来的行数只跟「功能点数 × 状态数」有关。
        """
        rows = self.group_counts(("module_id", "status"), filters)
        by_module: dict[int, dict[str, int]] = {}
        orphans: dict[str, int] = {}
        total = 0
        for row in rows:
            count = int(row["row_count"] or 0)
            status = str(row["status"] or "")
            module_id = row["module_id"]
            bucket = orphans if module_id is None else by_module.setdefault(int(module_id), {})
            bucket["total"] = bucket.get("total", 0) + count
            bucket[status] = bucket.get(status, 0) + count
            total += count
        return {"byModule": by_module, "orphans": orphans, "total": total}

    def coverage_matrix(self, project_id: int, version_id: int | None = None) -> dict[str, Any]:
        """覆盖矩阵：需求 -> 用例数量与状态分布。

        join 用例时必须同时限 project_id，否则别的项目里同名 requirement_id 的用例会记进来；
        给了 version_id 就只统计该版本的需求和该版本的用例。
        """
        case_version = " AND c.version_id = :version_id" if version_id else ""
        req_version = " AND r.version_id = :version_id" if version_id else ""
        params: dict[str, Any] = {"project_id": int(project_id)}
        if version_id:
            params["version_id"] = int(version_id)
        rows = self.conn.execute(
            f"""
            SELECT r.id AS requirement_id, r.req_no, r.title,
                   COUNT(c.id) AS case_count,
                   SUM(CASE WHEN c.priority = 'P0' THEN 1 ELSE 0 END) AS p0_count,
                   SUM(CASE WHEN c.status = 'approved' THEN 1 ELSE 0 END) AS approved_count
            FROM biz_requirement r
            LEFT JOIN biz_test_case c
                   ON c.requirement_id = r.id AND c.is_deleted = 0 AND c.project_id = r.project_id
                   {case_version}
            WHERE r.project_id = :project_id AND r.is_deleted = 0
                   {req_version}
            GROUP BY r.id
            ORDER BY r.req_no
            """,
            params,
        ).fetchall()
        return {"list": [dict(r) for r in rows]}

    _COVERAGE_KEYS = (
        "case_count", "p0_count", "p1_count",
        "pass_count", "fail_count", "blocked_count", "not_executed_count",
    )

    def module_coverage_matrix(
        self, project_id: int, version_id: int | None = None
    ) -> dict[str, Any]:
        """功能点覆盖矩阵：每个节点的用例数与状态分布（含全部下级）。

        口径与用例列表左侧功能点面板一致：点节点筛的是整棵子树，所以父节点
        数字包含下级。没挂到存活功能点的用例（含功能点已被删除的）放进
        unassigned，保证「根节点数字之和 + unassigned」等于当前范围用例总数。
        """
        from .module_repo import ModuleRepository

        module_repo = ModuleRepository(self.conn)
        nodes = module_repo.flat_tree(project_id)
        version_sql = " AND version_id = ?" if version_id else ""
        params: list[Any] = [project_id]
        if version_id:
            params.append(int(version_id))
        rows = self.conn.execute(
            f"""
            SELECT module_id,
                   COUNT(*) AS case_count,
                   SUM(CASE WHEN priority = 'P0' THEN 1 ELSE 0 END) AS p0_count,
                   SUM(CASE WHEN priority = 'P1' THEN 1 ELSE 0 END) AS p1_count,
                   SUM(CASE WHEN status = 'pass' THEN 1 ELSE 0 END) AS pass_count,
                   SUM(CASE WHEN status = 'fail' THEN 1 ELSE 0 END) AS fail_count,
                   SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked_count,
                   SUM(CASE WHEN status = 'not_executed' THEN 1 ELSE 0 END) AS not_executed_count
            FROM biz_test_case
            WHERE project_id = ? AND is_deleted = 0
                   {version_sql}
            GROUP BY module_id
            """,
            params,
        ).fetchall()

        keys = self._COVERAGE_KEYS
        alive = {int(node["id"]) for node in nodes}
        direct: dict[int, dict[str, int]] = {}
        unassigned: dict[str, int] = dict.fromkeys(keys, 0)
        for row in rows:
            bucket = {key: int(row[key] or 0) for key in keys}
            module_id = row["module_id"]
            if module_id is not None and int(module_id) in alive:
                direct[int(module_id)] = bucket
                continue
            for key in keys:
                unassigned[key] += bucket[key]

        rollup: dict[int, dict[str, int]] = {
            node_id: dict(direct.get(node_id) or dict.fromkeys(keys, 0)) for node_id in alive
        }
        # flat_tree 是先序遍历，逆序处理时子节点的汇总已经算完
        for node in reversed(nodes):
            parent_id = node.get("parent_id")
            if parent_id is None or int(parent_id) not in rollup:
                continue
            parent = rollup[int(parent_id)]
            current = rollup[int(node["id"])]
            for key in keys:
                parent[key] += current[key]

        result: list[dict[str, Any]] = []
        for node in nodes:
            node_id = int(node["id"])
            stat = rollup[node_id]
            result.append({
                "moduleId": node_id,
                "name": node.get("name"),
                "nodeType": node.get("node_type"),
                "parentId": node.get("parent_id"),
                "path": module_repo.path_text(node_id, project_name=""),
                "caseCount": stat["case_count"],
                "p0Count": stat["p0_count"],
                "p1Count": stat["p1_count"],
                "passCount": stat["pass_count"],
                "failCount": stat["fail_count"],
                "blockedCount": stat["blocked_count"],
                "notExecutedCount": stat["not_executed_count"],
            })
        return {
            "list": result,
            "unassigned": {
                "caseCount": unassigned["case_count"],
                "p0Count": unassigned["p0_count"],
                "p1Count": unassigned["p1_count"],
                "passCount": unassigned["pass_count"],
                "failCount": unassigned["fail_count"],
                "blockedCount": unassigned["blocked_count"],
                "notExecutedCount": unassigned["not_executed_count"],
            },
        }

    def linked_bug_ids(self, case_ids: list[int]) -> dict[int, RowList]:
        """用例 -> 关联缺陷列表（用于 xlsx 导出「关联缺陷」列）。"""
        if not case_ids:
            return {}
        placeholders = ", ".join("?" for _ in case_ids)
        rows = self.conn.execute(
            f"""
            SELECT rel.case_id, b.id AS bug_id, b.bug_no, b.title, b.status, b.severity
            FROM biz_case_bug_rel rel
            JOIN biz_bug b ON b.id = rel.bug_id
            WHERE rel.case_id IN ({placeholders}) AND b.is_deleted = 0
            ORDER BY b.bug_no
            """,
            case_ids,
        ).fetchall()
        grouped: dict[int, RowList] = {}
        for row in rows:
            grouped.setdefault(int(row["case_id"]), []).append(dict(row))
        return grouped

    def set_steps(self, pk: int, steps: list[dict[str, Any]]) -> bool:
        return self.update(pk, {"steps_json": dumps(steps)})
