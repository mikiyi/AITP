"""追溯矩阵 API：需求 → 用例 → 执行 → 缺陷，支持按轮次查看与导出。

GAP-36 追加功能点维度：``/modules`` 给「功能点 × 需求口径 × 用例口径」的覆盖矩阵，
``/module/<id>`` 给某一功能点（含子树）下的需求与用例清单——追溯从需求层下钻到功能点层
就靠这两条，前端两处跳转共用同一份口径。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response, request

from ..common.validators import optional_int, require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import audit_service, tabular_export, traceability_service
from .helpers import audit, db, download_response, require_project, tabular_format_of

bp = Blueprint("traceability", __name__)

EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("reqNo", "需求编号"),
    ("title", "需求标题"),
    ("requirementStatus", "需求状态"),
    ("priority", "优先级"),
    ("caseCount", "关联用例"),
    ("executedCount", "已执行"),
    ("passed", "通过"),
    ("failed", "失败"),
    ("blocked", "阻塞"),
    ("notExecuted", "未执行"),
    ("passRate", "通过率"),
    ("openBugs", "未闭环缺陷"),
    ("coverageLabel", "覆盖状态"),
    ("lastExecutedAt", "最近执行时间"),
)

MODULE_EXPORT_COLUMNS: tuple[tuple[str, str], ...] = (
    ("modulePath", "功能点路径"),
    ("name", "功能点"),
    ("requirementCount", "本节点需求"),
    ("subtreeRequirementCount", "子树需求"),
    ("caseCount", "本节点用例"),
    ("subtreeCaseCount", "子树用例"),
    ("passed", "通过"),
    ("failed", "失败"),
    ("blocked", "阻塞"),
    ("notExecuted", "未执行"),
    ("executedRate", "执行覆盖率"),
    ("coverageLabel", "覆盖状态"),
)


def _args() -> tuple[int, int | None, int | None]:
    project_id = require_int(request.args.get("projectId"), "projectId")
    version_id = optional_int(request.args.get("versionId"), "versionId")
    run_id = optional_int(request.args.get("runId"), "runId")
    if run_id is not None:
        row = db().execute(
            "SELECT project_id FROM biz_test_run WHERE id = ? AND is_deleted = 0", (run_id,)
        ).fetchone()
        if row is None or int(row["project_id"] or -1) != project_id:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不属于该项目")
    return project_id, version_id, run_id


@bp.get("")
def get_traceability() -> Response:
    conn = db()
    project_id, version_id, run_id = _args()
    return ok(traceability_service.build_traceability(conn, project_id, version_id, run_id))


@bp.get("/export")
def export_traceability() -> Response:
    conn = db()
    project_id, version_id, run_id = _args()
    fmt = tabular_format_of(request.args.get("format"))
    data = traceability_service.build_traceability(conn, project_id, version_id, run_id)
    rows = []
    for row in data["list"]:
        item = dict(row)
        item["passRate"] = f"{round(float(item.get('passRate') or 0) * 100, 2)}%"
        rows.append(item)
    content, mimetype, extension = tabular_export.render(fmt, EXPORT_COLUMNS, rows)
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_TEST_RUN, run_id,
          "追溯矩阵", {"projectId": project_id, "format": fmt, "count": len(rows)})
    return download_response(
        content, tabular_export.safe_filename("需求追溯矩阵", extension), mimetype, count=len(rows)
    )


# --------------------------------------------------------------------- 功能点维度（GAP-36）


def _module_nodes(conn: Any, project_id: int) -> dict[int, dict[str, Any]]:
    """取项目功能点树并在内存拼路径：节点量级远小于用例，一次读全量不产生读放大。"""
    rows = conn.execute(
        "SELECT id, parent_id, name, node_type FROM biz_module"
        " WHERE project_id = ? AND is_deleted = 0 ORDER BY sort_order ASC, id ASC",
        (project_id,),
    ).fetchall()
    nodes: dict[int, dict[str, Any]] = {
        int(row["id"]): {
            "moduleId": int(row["id"]),
            "parentId": int(row["parent_id"]) if row["parent_id"] is not None else None,
            "name": str(row["name"] or ""),
            "nodeType": str(row["node_type"] or "module"),
            "children": [],
            "path": "",
        }
        for row in rows
    }
    for node in nodes.values():
        parent = node["parentId"]
        if parent in nodes:
            nodes[parent]["children"].append(node["moduleId"])

    def walk(node_id: int, prefix: list[str]) -> None:
        chain = prefix + [nodes[node_id]["name"]]
        nodes[node_id]["path"] = "/".join(chain)
        for child in nodes[node_id]["children"]:
            walk(child, chain)

    for root_id, node in nodes.items():
        if node["parentId"] not in nodes:
            walk(root_id, [])
    return nodes


def _subtree_ids(nodes: dict[int, dict[str, Any]], root_id: int) -> set[int]:
    seen: set[int] = set()
    stack = [root_id]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        stack.extend(nodes.get(current, {}).get("children") or [])
    return seen


def _requirement_counts(conn: Any, project_id: int) -> dict[int, int]:
    rows = conn.execute(
        "SELECT module_id, COUNT(*) AS total FROM biz_requirement"
        " WHERE project_id = ? AND is_deleted = 0 AND module_id IS NOT NULL GROUP BY module_id",
        (project_id,),
    ).fetchall()
    return {int(row["module_id"]): int(row["total"]) for row in rows}


def _case_counts(conn: Any, project_id: int, run_id: int | None) -> dict[int, dict[str, int]]:
    """用例口径按功能点汇总；带 runId 时执行结论取轮次快照，不然后退用全局用例状态。"""
    if run_id is None:
        sql = (
            "SELECT module_id, COUNT(*) AS total,"
            " SUM(CASE WHEN status IN ('pass', 'passed') THEN 1 ELSE 0 END) AS passed,"
            " SUM(CASE WHEN status IN ('fail', 'failed') THEN 1 ELSE 0 END) AS failed,"
            " SUM(CASE WHEN status = 'blocked' THEN 1 ELSE 0 END) AS blocked,"
            " SUM(CASE WHEN status IS NULL OR status IN ('not_executed', '') THEN 1 ELSE 0 END)"
            " AS not_executed"
            " FROM biz_test_case WHERE project_id = ? AND is_deleted = 0"
            " AND module_id IS NOT NULL GROUP BY module_id"
        )
        params: tuple[Any, ...] = (project_id,)
    else:
        sql = (
            "SELECT c.module_id, COUNT(*) AS total,"
            " SUM(CASE WHEN c.result = 'passed' THEN 1 ELSE 0 END) AS passed,"
            " SUM(CASE WHEN c.result = 'failed' THEN 1 ELSE 0 END) AS failed,"
            " SUM(CASE WHEN c.result = 'blocked' THEN 1 ELSE 0 END) AS blocked,"
            " SUM(CASE WHEN c.result = 'not_executed' THEN 1 ELSE 0 END) AS not_executed"
            " FROM biz_test_run_case c JOIN biz_test_run r ON r.id = c.run_id"
            " WHERE c.run_id = ? AND c.is_deleted = 0 AND r.project_id = ?"
            " AND c.module_id IS NOT NULL GROUP BY c.module_id"
        )
        params = (run_id, project_id)
    rows = conn.execute(sql, params).fetchall()
    return {
        int(row["module_id"]): {
            "total": int(row["total"] or 0),
            "passed": int(row["passed"] or 0),
            "failed": int(row["failed"] or 0),
            "blocked": int(row["blocked"] or 0),
            "notExecuted": int(row["not_executed"] or 0),
        }
        for row in rows
    }


def _module_matrix(conn: Any, project_id: int, run_id: int | None) -> list[dict[str, Any]]:
    """功能点覆盖矩阵行：需求口径与用例口径同行并列（GAP-36 验收标准 2）。"""
    nodes = _module_nodes(conn, project_id)
    requirements = _requirement_counts(conn, project_id)
    cases = _case_counts(conn, project_id, run_id)
    rows: list[dict[str, Any]] = []
    for module_id, node in nodes.items():
        subtree = _subtree_ids(nodes, module_id)
        own_cases = cases.get(module_id, {})
        subtree_case_total = sum(cases.get(item, {}).get("total", 0) for item in subtree)
        subtree_executed = sum(
            cases.get(item, {}).get("total", 0) - cases.get(item, {}).get("notExecuted", 0)
            for item in subtree
        )
        subtree_requirements = sum(requirements.get(item, 0) for item in subtree)
        own_requirements = requirements.get(module_id, 0)
        if subtree_requirements == 0 and subtree_case_total == 0:
            coverage = "未覆盖"
        elif subtree_case_total == 0:
            coverage = "有需求无用例"
        elif subtree_executed < subtree_case_total:
            coverage = "执行未覆盖"
        else:
            coverage = "已覆盖"
        rows.append({
            "moduleId": module_id,
            "parentId": node["parentId"],
            "name": node["name"],
            "nodeType": node["nodeType"],
            "modulePath": node["path"],
            "requirementCount": own_requirements,
            "subtreeRequirementCount": subtree_requirements,
            "caseCount": own_cases.get("total", 0),
            "subtreeCaseCount": subtree_case_total,
            "passed": own_cases.get("passed", 0),
            "failed": own_cases.get("failed", 0),
            "blocked": own_cases.get("blocked", 0),
            "notExecuted": own_cases.get("notExecuted", 0),
            "executedRate": (
                round(subtree_executed * 100.0 / subtree_case_total, 2) if subtree_case_total else 0.0
            ),
            "coverageLabel": coverage,
        })
    rows.sort(key=lambda item: (item["modulePath"], item["moduleId"]))
    return rows


@bp.get("/modules")
def module_traceability() -> Response:
    """功能点覆盖矩阵：同一行里既看需求口径（挂了几条需求）也看用例口径。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    require_project(conn, project_id)
    run_id = optional_int(request.args.get("runId"), "runId")
    if run_id is not None:
        row = conn.execute(
            "SELECT project_id FROM biz_test_run WHERE id = ? AND is_deleted = 0", (run_id,)
        ).fetchone()
        if row is None or int(row["project_id"] or -1) != project_id:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不属于该项目")
    rows = _module_matrix(conn, project_id, run_id)
    return ok({
        "list": rows,
        "total": len(rows),
        "runId": run_id,
        "summary": {
            "requirementTotal": sum(item["requirementCount"] for item in rows),
            "caseTotal": sum(item["caseCount"] for item in rows),
            "unboundRequirements": int(conn.execute(
                "SELECT COUNT(*) FROM biz_requirement WHERE project_id = ?"
                " AND is_deleted = 0 AND module_id IS NULL", (project_id,)
            ).fetchone()[0]),
            "unboundCases": int(conn.execute(
                "SELECT COUNT(*) FROM biz_test_case WHERE project_id = ?"
                " AND is_deleted = 0 AND module_id IS NULL", (project_id,)
            ).fetchone()[0]),
        },
    })


@bp.get("/modules/export")
def export_module_traceability() -> Response:
    """功能点矩阵导出：口径与 /modules 完全一致，不允许两份算法各算各的。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    require_project(conn, project_id)
    run_id = optional_int(request.args.get("runId"), "runId")
    fmt = tabular_format_of(request.args.get("format"))
    rows = _module_matrix(conn, project_id, run_id)
    content, mimetype, extension = tabular_export.render(fmt, MODULE_EXPORT_COLUMNS, rows)
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_TEST_RUN, run_id,
          "功能点追溯矩阵", {"projectId": project_id, "format": fmt, "count": len(rows)})
    return download_response(
        content, tabular_export.safe_filename("功能点追溯矩阵", extension), mimetype,
        count=len(rows),
    )


@bp.get("/module/<module_id>")
def module_drill(module_id: str) -> Response:
    """功能点下钻：本节点（默认含子树）挂了哪些需求、哪些用例、用例最近一份证据在哪。"""
    conn = db()
    target = require_int(module_id, "moduleId")
    node_row = conn.execute(
        "SELECT id, project_id, name, parent_id FROM biz_module WHERE id = ? AND is_deleted = 0",
        (target,),
    ).fetchone()
    if node_row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "功能点不存在或已删除")
    include_subtree = str(request.args.get("includeSubtree") or "1") not in ("0", "false")
    nodes = _module_nodes(conn, int(node_row["project_id"]))
    ids = _subtree_ids(nodes, target) if include_subtree else {target}
    marks = ",".join("?" for _ in ids)
    params = list(ids)
    requirements = [
        {
            "id": int(row["id"]),
            "reqNo": row["req_no"],
            "title": row["title"],
            "status": row["status"],
            "priority": row["priority"],
            "moduleId": int(row["module_id"]),
            "caseCount": int(row["case_count"] or 0),
        }
        for row in conn.execute(
            f"SELECT r.id, r.req_no, r.title, r.status, r.priority, r.module_id,"
            f" (SELECT COUNT(*) FROM biz_test_case c"
            f"   WHERE c.requirement_id = r.id AND c.is_deleted = 0) AS case_count"
            f" FROM biz_requirement r"
            f" WHERE r.module_id IN ({marks}) AND r.is_deleted = 0 ORDER BY r.req_no ASC LIMIT 500",
            params,
        ).fetchall()
    ]
    cases = [
        {
            "id": int(row["id"]),
            "caseNo": row["case_no"],
            "title": row["title"],
            "caseType": row["case_type"],
            "priority": row["priority"],
            "status": row["status"],
            "requirementId": int(row["requirement_id"]) if row["requirement_id"] else None,
            "moduleId": int(row["module_id"]),
            "evidenceId": int(row["evidence_id"]) if row["evidence_id"] else None,
        }
        for row in conn.execute(
            f"SELECT c.id, c.case_no, c.title, c.case_type, c.priority, c.status,"
            f" c.requirement_id, c.module_id,"
            f" (SELECT MAX(e.id) FROM biz_execution_evidence e"
            f"    WHERE e.case_id = c.id AND e.is_deleted = 0) AS evidence_id"
            f" FROM biz_test_case c WHERE c.module_id IN ({marks}) AND c.is_deleted = 0"
            f" ORDER BY c.case_no ASC LIMIT 500",
            params,
        ).fetchall()
    ]
    return ok({
        "module": {
            "moduleId": target,
            "name": node_row["name"],
            "parentId": int(node_row["parent_id"]) if node_row["parent_id"] else None,
            "projectId": int(node_row["project_id"]),
            "modulePath": nodes.get(target, {}).get("path", ""),
        },
        "requirements": requirements,
        "cases": cases,
        "counts": {"requirement": len(requirements), "case": len(cases)},
    })
