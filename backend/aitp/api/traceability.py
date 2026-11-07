"""追溯矩阵 API：需求 → 用例 → 执行 → 缺陷，支持按轮次查看与导出。"""

from __future__ import annotations

from flask import Blueprint, Response, request

from ..common.validators import optional_int, require_int
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..services import audit_service, tabular_export, traceability_service
from .helpers import audit, db, download_response, tabular_format_of

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
