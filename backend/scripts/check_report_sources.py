"""验证报告按轮次 / 矩阵 / 场景批次取数（P1-8）。

    python backend/scripts/check_report_sources.py

临时库 + Flask 测试客户端直打 API；用例/执行结果直接落库以缩短门禁时间，
重点验证：来源解析、章节指标、统计接口、失败样本隔离与非法来源拒绝。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402

JsonDict = dict[str, Any]
V1 = "/api/v1"
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()
        self.scope_project_id: int | None = None

    def _scoped(self, path: str) -> str:
        if self.scope_project_id and "projectId=" not in path:
            sep = "&" if "?" in path else "?"
            return f"{path}{sep}projectId={self.scope_project_id}"
        return path

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[Any, JsonDict]:
        path = self._scoped(path)
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        if method == "POST" and path.split("?")[0].rstrip("/").endswith("/projects"):
            data = body.get("data")
            if isinstance(data, dict) and data.get("id"):
                self.scope_project_id = int(data["id"])
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} 失败: code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None) -> JsonDict:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code, f"code={body.get('code')} msg={body.get('message')}")
        return body


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "reports.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8773, debug=False, db_path=db_path,
        log_path=workdir / "reports.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def add_case(conn: sqlite3.Connection, project_id: int, no: str, title: str,
             priority: str = "P2", case_type: str = "functional") -> int:
    now = utcnow_iso()
    cur = conn.execute(
        "INSERT INTO biz_test_case"
        " (project_id, case_no, title, case_type, priority, status, steps_json, source,"
        "  created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, 'draft', '[]', 'manual', ?, ?)",
        (project_id, no, title, case_type, priority, now, now),
    )
    return int(cur.lastrowid or 0)


def add_bug(conn: sqlite3.Connection, project_id: int, no: str, title: str,
            status: str = "open", severity: str = "P1") -> int:
    now = utcnow_iso()
    cur = conn.execute(
        "INSERT INTO biz_bug"
        " (project_id, bug_no, title, severity, status, priority, bug_type, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, ?, ?, 'functional', ?, ?)",
        (project_id, no, title, severity, status, severity, now, now),
    )
    return int(cur.lastrowid or 0)


def set_run_results(conn: sqlite3.Connection, run_id: int, results: list[str],
                    bug_id: int | None = None) -> list[int]:
    rows = conn.execute(
        "SELECT id FROM biz_test_run_case WHERE run_id = ? AND is_deleted = 0 ORDER BY seq ASC",
        (run_id,),
    ).fetchall()
    ids = [int(row["id"]) for row in rows]
    now = utcnow_iso()
    for index, result in enumerate(results):
        if index >= len(ids):
            break
        conn.execute(
            "UPDATE biz_test_run_case SET result = ?, executed_at = ? WHERE id = ?",
            (result, now, ids[index]),
        )
    if bug_id and len(ids) >= 2:
        conn.execute("UPDATE biz_test_run_case SET bug_id = ? WHERE id = ?", (bug_id, ids[1]))
    conn.commit()
    return ids


def section_items(report: JsonDict) -> dict[str, JsonDict]:
    return {str(item.get("section")): item for item in report.get("items") or []}


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-report-source-"))
    db_path = workdir / "reports.db"
    try:
        app = build_app(workdir)
        api = Api(app)
        project_id = int(api.data("POST", f"{V1}/projects",
                                  {"code": "RPT", "name": "报告范围项目"})["id"])
        other_project = int(api.data("POST", f"{V1}/projects",
                                     {"code": "RPT2", "name": "其它项目"})["id"])
        # 造隔壁项目后切回主项目，后续报告统计/列表仍按 RPT 取数
        api.scope_project_id = project_id

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        check("迁移 069 已应用", 69 in applied_versions(conn))
        check("报告带来源列", {"source_type", "source_id", "source_run_id"} <= {
            r[1] for r in conn.execute("PRAGMA table_info(biz_report)").fetchall()})

        case1 = add_case(conn, project_id, "TC-RPT-1", "登录成功", "P0")
        case2 = add_case(conn, project_id, "TC-RPT-2", "下单金额校验", "P1")
        case3 = add_case(conn, project_id, "TC-RPT-3", "并发查询", "P2", "interface")
        case4 = add_case(conn, project_id, "TC-RPT-4", "其它轮次专用", "P3")
        bug_id = add_bug(conn, project_id, "BUG-RPT-1", "下单金额不一致")
        conn.commit()

        run = api.data("POST", f"{V1}/test-runs", {
            "projectId": project_id, "name": "RPT 范围轮次",
            "caseIds": [case1, case2, case3],
        })
        run_id = int(run["id"])
        set_run_results(conn, run_id, ["passed", "failed", "blocked"], bug_id=bug_id)
        other_run = api.data("POST", f"{V1}/test-runs", {
            "projectId": project_id, "name": "RPT 另一轮次",
            "caseIds": [case4, case1],
        })
        set_run_results(conn, int(other_run["id"]), ["failed", "passed"])
        conn.close()

        # ---- 轮次范围报告 ----
        report = api.data("POST", f"{V1}/reports/generate", {
            "projectId": project_id, "reportType": "iteration",
            "sourceType": "run", "sourceId": run_id,
        })
        check("轮次报告落来源字段",
              report["sourceType"] == "run" and int(report["sourceId"]) == run_id
              and "RPT 范围轮次" in report["sourceLabel"], str(report)[:300])
        check("轮次报告标题带范围", "RPT 范围轮次" in report["title"], report["title"])
        items = section_items(report)
        case_metric = items["case"]["metric"]
        check("轮次指标只算本轮用例",
              case_metric["totalCases"] == 3 and case_metric["passed"] == 1
              and case_metric["failed"] == 1 and case_metric["blocked"] == 1,
              str(case_metric))
        check("概览文案标明轮次范围",
              "条用例" in items["overview"]["content"]
              and "RPT 范围轮次" in items["overview"]["content"],
              items["overview"]["content"])
        check("轮次缺陷指标只算本轮关联缺陷",
              items["bug"]["metric"]["totalBugs"] == 1, str(items["bug"]["metric"]))

        stats = api.data("GET", f"{V1}/reports/{report['id']}/stats")
        check("统计接口跟随轮次范围",
              stats["metrics"]["caseTotal"] == 3 and stats["metrics"]["bugTotal"] == 1
              and stats.get("sourceType") == "run", str(stats)[:300])
        failure = api.data("GET", f"{V1}/reports/{report['id']}/failure-samples")
        check("失败样本只取本轮", failure["total"] == 1 and len(failure["list"]) == 1,
              str(failure["total"]))
        check("失败样本带轮次与缺陷",
              int(failure["list"][0]["runId"]) == run_id
              and failure["list"][0]["bugNo"] == "BUG-RPT-1", str(failure["list"][0])[:300])
        check("失败样本范围内含 runId", failure["scope"].get("runId") == run_id,
              str(failure["scope"]))

        # ---- 矩阵范围报告 ----
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        now = utcnow_iso()
        matrix_id = int(conn.execute(
            "INSERT INTO biz_execution_matrix"
            " (project_id, matrix_no, name, mode, status, cell_total, cell_done, created_at, updated_at)"
            " VALUES (?, 'MX-RPT-1', 'RPT 矩阵', 'interface', 'done', 2, 2, ?, ?)",
            (project_id, now, now),
        ).lastrowid or 0)
        for case_id, result, matrix_bug in (
            (case1, "passed", None), (case2, "failed", bug_id), (case3, "blocked", None),
        ):
            conn.execute(
                "INSERT INTO biz_execution_matrix_result"
                " (matrix_id, cell_id, case_id, result, bug_id, created_at, updated_at)"
                " VALUES (?, 0, ?, ?, ?, ?, ?)",
                (matrix_id, case_id, result, matrix_bug, now, now),
            )
        conn.commit()
        conn.close()

        matrix_report = api.data("POST", f"{V1}/reports/generate", {
            "projectId": project_id, "reportType": "iteration",
            "sourceType": "matrix", "sourceId": matrix_id,
        })
        matrix_items = section_items(matrix_report)
        check("矩阵报告指标按矩阵结果",
              matrix_items["case"]["metric"]["totalCases"] == 3
              and matrix_items["case"]["metric"]["passed"] == 1
              and matrix_items["case"]["metric"]["blocked"] == 1,
              str(matrix_items["case"]["metric"]))
        check("矩阵报告带矩阵编号", "MX-RPT-1" in matrix_report["sourceLabel"],
              matrix_report["sourceLabel"])
        matrix_failure = api.data("GET", f"{V1}/reports/{matrix_report['id']}/failure-samples")
        check("矩阵报告不套用项目失败样本", matrix_failure["total"] == 0,
              str(matrix_failure["total"]))

        # ---- 场景批次报告 ----
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        scenario_id = int(conn.execute(
            "INSERT INTO biz_api_scenario"
            " (scenario_no, project_id, name, steps_json, status, created_at, updated_at)"
            " VALUES ('SC-RPT-1', ?, 'RPT 场景', '[]', 'ready', ?, ?)",
            (project_id, now, now),
        ).lastrowid or 0)
        runs = [
            ("SR-RPT-1", 1, 2, {"runs": [{"passed": True}, {"passed": False}]}),
            ("SR-RPT-2", 1, 1, {"runs": [{"passed": True}], "autoBugId": bug_id}),
        ]
        scenario_run_ids: list[int] = []
        for run_no, passed, record_count, summary in runs:
            scenario_run_ids.append(int(conn.execute(
                "INSERT INTO biz_api_scenario_run"
                " (run_no, scenario_id, project_id, passed, record_count, summary_json,"
                "  duration_ms, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, 12, ?, ?)",
                (run_no, scenario_id, project_id, passed, record_count,
                 json.dumps(summary, ensure_ascii=False), now, now),
            ).lastrowid or 0))
        conn.commit()
        conn.close()

        single = api.data("POST", f"{V1}/reports/generate", {
            "projectId": project_id, "reportType": "iteration",
            "sourceType": "scenario", "sourceId": scenario_id,
            "sourceRunId": scenario_run_ids[1],
        })
        single_metric = section_items(single)["case"]["metric"]
        check("单次场景报告按记录取数",
              single_metric["totalCases"] == 1 and single_metric["passed"] == 1,
              str(single_metric))
        check("单次场景报告带执行编号", "SR-RPT-2" in single["sourceLabel"], single["sourceLabel"])
        single_stats = api.data("GET", f"{V1}/reports/{single['id']}/stats")
        check("统计接口保留单次场景范围",
              single_stats["metrics"]["caseTotal"] == 1
              and single_stats.get("sourceType") == "scenario", str(single_stats)[:300])

        batch = api.data("POST", f"{V1}/reports/generate", {
            "projectId": project_id, "reportType": "iteration",
            "sourceType": "scenario", "sourceId": scenario_id,
        })
        batch_metric = section_items(batch)["case"]["metric"]
        check("场景批次报告汇总全部执行",
              batch_metric["totalCases"] == 3 and batch_metric["passed"] == 2
              and batch_metric["failed"] == 1, str(batch_metric))
        check("场景批次报告带执行次数", "近 2 次执行" in batch["sourceLabel"], batch["sourceLabel"])

        # ---- 非法/越权来源 ----
        api.expect("非法来源类型被拒", "POST", f"{V1}/reports/generate", ErrorCode.PARAM_ERROR,
                   {"projectId": project_id, "sourceType": "bogus", "sourceId": 1})
        api.expect("来源缺少 id 被拒", "POST", f"{V1}/reports/generate", ErrorCode.PARAM_ERROR,
                   {"projectId": project_id, "sourceType": "run"})
        api.expect("来源缺少类型被拒", "POST", f"{V1}/reports/generate", ErrorCode.PARAM_ERROR,
                   {"projectId": project_id, "sourceId": run_id})
        api.expect("非场景来源不能带执行 id", "POST", f"{V1}/reports/generate",
                   ErrorCode.PARAM_ERROR,
                   {"projectId": project_id, "sourceType": "run", "sourceId": run_id,
                    "sourceRunId": 1})
        api.expect("跨项目轮次来源按不存在处理", "POST", f"{V1}/reports/generate",
                   ErrorCode.RESOURCE_NOT_FOUND,
                   {"projectId": other_project, "sourceType": "run", "sourceId": run_id})

        # ---- 报告列表带来源，便于筛选与复盘 ----
        page = api.data("GET", f"{V1}/reports?projectId={project_id}&pageSize=50")
        by_id = {int(item["id"]): item for item in page["list"]}
        check("报告列表带来源类型与标签",
              by_id[int(report["id"])]["sourceType"] == "run"
              and "RPT 范围轮次" in by_id[int(report["id"])]["sourceLabel"], str(page["list"][:1]))
        check("项目全量报告来源为空",
              all(not item["sourceType"] for item in page["list"]
                  if int(item["id"]) not in {int(report["id"]), int(matrix_report["id"]),
                                             int(single["id"]), int(batch["id"])}))

    finally:
        pass
    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
