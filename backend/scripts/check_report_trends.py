"""验证 GAP-12：报告趋势与版本对比。

    python backend/scripts/check_report_trends.py

全部在临时库上运行，不读写正式数据。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from datetime import datetime, time, timedelta, timezone
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

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

    def call(self, method: str, path: str,
             payload: JsonDict | None = None) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")

    def code(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        return body.get("code")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "trends.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8765,
        debug=False,
        db_path=db_path,
        log_path=workdir / "trends.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def local_noon(days_ago: int) -> str:
    """本机时区中午的时间戳，转成 UTC ISO，保证按本地日期归档时不跨天。"""
    today = datetime.now().date()
    moment = datetime.combine(today - timedelta(days=days_ago), time(12, 0)).astimezone()
    return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def seed(conn: sqlite3.Connection, project_id: int, v1: int, v2: int) -> dict[str, int]:
    now = local_noon(0)
    cases: dict[str, int] = {}
    case_rows = [
        ("C1", "v1 用例 1", v1, "P0"),
        ("C2", "v1 用例 2", v1, "P2"),
        ("C3", "v1 用例 3", v1, "P1"),
        ("C4", "v1 用例 4", v1, "P2"),
        ("C5", "v2 用例 1", v2, "P1"),
        ("C6", "v2 用例 2", v2, "P2"),
        ("C7", "v2 用例 3", v2, "P2"),
    ]
    for case_no, title, version_id, priority in case_rows:
        cursor = conn.execute(
            "INSERT INTO biz_test_case"
            " (project_id, case_no, title, case_type, priority, status, version_id,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, 'functional', ?, 'draft', ?, ?, ?)",
            (project_id, case_no, title, priority, version_id, now, now),
        )
        cases[case_no] = int(cursor.lastrowid or 0)

    executions = [
        ("C1", "pass", 0),
        ("C2", "fail", 0),
        ("C3", "pass", 3),
    ]
    for case_no, result, days_ago in executions:
        executed_at = local_noon(days_ago)
        conn.execute(
            "INSERT INTO biz_case_execution"
            " (case_id, result, executed_at, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (cases[case_no], result, executed_at, executed_at, executed_at),
        )

    bugs: dict[str, int] = {}
    bug_rows = [
        ("B1", "v1 P0 未闭环", "P0", "open", v1, 0),
        ("B2", "v1 P2 已关闭", "P2", "closed", v1, 5),
        ("B3", "v2 P1 未闭环", "P1", "open", v2, 0),
    ]
    for bug_no, title, severity, status, version_id, days_ago in bug_rows:
        created_at = local_noon(days_ago)
        cursor = conn.execute(
            "INSERT INTO biz_bug"
            " (project_id, bug_no, title, severity, status, priority, bug_type,"
            "  version_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'functional', ?, ?, ?)",
            (project_id, bug_no, title, severity, status, severity, version_id,
             created_at, created_at),
        )
        bugs[bug_no] = int(cursor.lastrowid or 0)
    conn.execute(
        "INSERT INTO biz_bug_status_log (bug_id, from_status, to_status, created_at, updated_at)"
        " VALUES (?, 'open', 'closed', ?, ?)",
        (bugs["B2"], now, now),
    )

    runs: dict[str, int] = {}
    for name, version_id, results in (
        ("v1 轮次", v1, (("C1", "passed"), ("C2", "failed"))),
        ("v2 轮次", v2, (("C5", "passed"), ("C6", "passed"), ("C7", "failed"))),
    ):
        cursor = conn.execute(
            "INSERT INTO biz_test_run"
            " (project_id, version_id, name, status, total, created_at, updated_at)"
            " VALUES (?, ?, ?, 'done', ?, ?, ?)",
            (project_id, version_id, name, len(results), now, now),
        )
        run_id = int(cursor.lastrowid or 0)
        runs[name] = run_id
        for case_no, result in results:
            conn.execute(
                "INSERT INTO biz_test_run_case"
                " (run_id, case_id, case_no, title, result, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (run_id, cases[case_no], case_no, f"快照 {case_no}", result, now, now),
            )

    for version_id, status, conclusion in ((v1, "passed", "质量达标"), (v2, "blocked", "P1 未闭环")):
        conn.execute(
            "INSERT INTO biz_release_gate"
            " (project_id, version_id, run_id, name, status, conclusion, created_by,"
            "  created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, 'check', ?, ?)",
            (project_id, version_id, runs["v1 轮次" if version_id == v1 else "v2 轮次"],
             f"准入 {version_id}", status, conclusion, now, now),
        )
    conn.commit()
    return cases


def series_map(trend: JsonDict) -> dict[str, list[float]]:
    return {str(item["key"]): list(item["values"]) for item in trend.get("series") or []}


def check_trend(api: Api, project_id: int) -> None:
    today = datetime.now().date()
    trend = api.data(
        "GET", f"/api/v1/reports/trends?projectId={project_id}&granularity=day&periods=7"
    )
    check("趋势返回 7 个时间桶", len(trend.get("buckets") or []) == 7, str(trend)[:200])
    check("趋势按天标注", trend.get("granularityLabel") == "按天", str(trend.get("granularityLabel")))
    check("趋势首桶是最早一天",
          trend["buckets"][0] == (today - timedelta(days=6)).strftime("%m-%d"),
          str(trend["buckets"]))
    check("趋势末桶是今天", trend["buckets"][-1] == today.strftime("%m-%d"), str(trend["buckets"]))
    check("趋势截止日期是今天", trend.get("endDate") == today.isoformat(), str(trend.get("endDate")))

    values = series_map(trend)
    check("今日执行数逐条对得上", values["executions"][-1] == 2, str(values["executions"]))
    check("三天前执行数逐条对得上", values["executions"][3] == 1, str(values["executions"]))
    check("空档执行数补 0", values["executions"][5] == 0, str(values["executions"]))
    check("今日通过率正确", values["passRate"][-1] == 50.0, str(values["passRate"]))
    check("无执行时通过率为 0", values["passRate"][5] == 0.0, str(values["passRate"]))
    check("今日新增缺陷数正确", values["newBugs"][-1] == 2, str(values["newBugs"]))
    check("五天前新增缺陷数正确", values["newBugs"][1] == 1, str(values["newBugs"]))
    check("今日关闭缺陷数正确", values["closedBugs"][-1] == 1, str(values["closedBugs"]))
    check("P0/P1 遗留按当天快照统计", values["p0p1Open"][-1] == 2, str(values["p0p1Open"]))
    check("P0/P1 遗留历史快照为 0", values["p0p1Open"][1] == 0, str(values["p0p1Open"]))

    totals = trend.get("totals") or {}
    check("趋势合计执行数", totals.get("executions") == 3, str(totals))
    check("趋势合计通过率", totals.get("passRate") == 66.67, str(totals))
    check("趋势合计新增缺陷", totals.get("newBugs") == 3, str(totals))
    check("趋势合计关闭缺陷", totals.get("closedBugs") == 1, str(totals))
    check("趋势合计 P0/P1 遗留", totals.get("p0p1Open") == 2, str(totals))

    weekly = api.data(
        "GET", f"/api/v1/reports/trends?projectId={project_id}&granularity=week&periods=4"
    )
    check("按周返回 4 个时间桶", len(weekly.get("buckets") or []) == 4, str(weekly)[:200])
    check("按周标注", weekly.get("granularityLabel") == "按周", str(weekly.get("granularityLabel")))
    check("按周时间桶连续",
          all("起" in str(label) for label in weekly.get("buckets") or []),
          str(weekly.get("buckets")))
    check("按周合计执行数", sum(series_map(weekly)["executions"]) == 3,
          str(series_map(weekly)["executions"]))
    check("按周通过率", (weekly.get("totals") or {}).get("passRate") == 66.67,
          str(weekly.get("totals")))

    check("非法粒度被拒绝",
          api.code("GET", f"/api/v1/reports/trends?projectId={project_id}&granularity=monthly")
          == ErrorCode.PARAM_ERROR)
    check("时间桶过少被拒绝",
          api.code("GET", f"/api/v1/reports/trends?projectId={project_id}&periods=1")
          == ErrorCode.PARAM_ERROR)
    check("时间桶过多被拒绝",
          api.code("GET", f"/api/v1/reports/trends?projectId={project_id}&periods=999")
          == ErrorCode.PARAM_ERROR)
    check("项目不存在时返回未找到",
          api.code("GET", "/api/v1/reports/trends?projectId=999999")
          == ErrorCode.RESOURCE_NOT_FOUND)


def check_empty_project(api: Api) -> None:
    project = api.data("POST", "/api/v1/projects", {"code": "EMPTY", "name": "空项目"})
    trend = api.data(
        "GET", f"/api/v1/reports/trends?projectId={project['id']}&granularity=day&periods=3"
    )
    values = series_map(trend)
    check("空项目也返回完整时间桶", len(trend.get("buckets") or []) == 3, str(trend)[:160])
    check("空项目执行数为 0", values["executions"] == [0.0, 0.0, 0.0], str(values["executions"]))
    check("空项目通过率为 0", values["passRate"] == [0.0, 0.0, 0.0], str(values["passRate"]))
    check("空项目缺陷数为 0", values["newBugs"] == [0.0, 0.0, 0.0], str(values["newBugs"]))


def check_compare(api: Api, project_id: int, v1: int, v2: int) -> None:
    payload = api.data(
        "GET",
        f"/api/v1/reports/version-compare?projectId={project_id}"
        f"&baseVersionId={v1}&targetVersionId={v2}",
    )
    base = payload.get("base") or {}
    target = payload.get("target") or {}
    check("对比返回基准版本", int(base.get("versionId") or 0) == v1, str(base)[:200])
    check("对比返回目标版本", int(target.get("versionId") or 0) == v2, str(target)[:200])
    check("基准版本用例数", base.get("caseTotal") == 4, str(base))
    check("目标版本用例数", target.get("caseTotal") == 3, str(target))
    check("基准版本只统计本版本轮次", base.get("executed") == 2 and base.get("runs") == 1, str(base))
    check("目标版本只统计本版本轮次", target.get("executed") == 3 and target.get("runs") == 1,
          str(target))
    check("基准版本执行率", base.get("executeRate") == 50.0, str(base))
    check("目标版本执行率", target.get("executeRate") == 100.0, str(base))
    check("基准版本通过率", base.get("passRate") == 50.0, str(base))
    check("目标版本通过率", target.get("passRate") == 66.67, str(target))
    check("基准版本缺陷数", base.get("bugTotal") == 2 and base.get("openBugs") == 1, str(base))
    check("目标版本缺陷数", target.get("bugTotal") == 1 and target.get("openBugs") == 1, str(target))
    check("P0/P1 遗留数", base.get("p0p1Open") == 1 and target.get("p0p1Open") == 1, str(base))
    check("缺陷密度", base.get("defectDensity") == 0.5 and target.get("defectDensity") == 0.3333,
          f"{base.get('defectDensity')} / {target.get('defectDensity')}")
    check("准入结论中文标注",
          base.get("gateStatusLabel") == "通过" and target.get("gateStatusLabel") == "阻断",
          f"{base.get('gateStatusLabel')} / {target.get('gateStatusLabel')}")

    metrics = {item["key"]: item for item in payload.get("metrics") or []}
    check("对比指标带变化量", metrics["passRate"]["delta"] == 16.67, str(metrics["passRate"]))
    check("对比指标带方向", metrics["passRate"]["higherIsBetter"] is True
          and metrics["openBugs"]["higherIsBetter"] is False, str(metrics["passRate"]))
    check("上下文指标标为中性", metrics["caseTotal"].get("neutral") is True
          and metrics["caseTotal"]["higherIsBetter"] is False, str(metrics["caseTotal"]))
    check("对比指标带单位", metrics["executeRate"]["unit"] == "%", str(metrics["executeRate"]))

    check("相同版本不能对比",
          api.code("GET", f"/api/v1/reports/version-compare?projectId={project_id}"
                          f"&baseVersionId={v1}&targetVersionId={v1}")
          == ErrorCode.PARAM_ERROR)
    check("缺少版本参数被拒绝",
          api.code("GET", f"/api/v1/reports/version-compare?projectId={project_id}")
          == ErrorCode.PARAM_ERROR)

    other = api.data("POST", "/api/v1/projects", {"code": "OTHER", "name": "其他项目"})
    other_version = api.data("POST", "/api/v1/project-versions", {
        "projectId": other["id"], "versionCode": "v9.9", "name": "其他版本",
    })
    check("跨项目版本被拒绝",
          api.code("GET", f"/api/v1/reports/version-compare?projectId={project_id}"
                          f"&baseVersionId={v1}&targetVersionId={other_version['id']}")
          == ErrorCode.RESOURCE_NOT_FOUND)


def check_trend_item(api: Api, project_id: int) -> None:
    report = api.data("POST", "/api/v1/reports", {
        "projectId": project_id, "title": "趋势回归报告",
    })
    report_id = int(report["id"])

    inserted = api.data("POST", f"/api/v1/reports/{report_id}/trend-item", {
        "granularity": "week", "periods": 4,
    })
    items = inserted.get("items") or []
    trend_items = [item for item in items if item.get("section") == "trend"]
    check("趋势章节已插入", len(trend_items) == 1, str(items)[:240])
    trend_item = trend_items[0] if trend_items else {}
    check("趋势章节标题标注粒度", trend_item.get("title") == "指标趋势（按周）", str(trend_item))
    check("趋势章节带 Markdown 表格", "| 周期 | 执行数 |" in str(trend_item.get("content")),
          str(trend_item.get("content"))[:160])
    check("趋势章节保留合计指标",
          int((trend_item.get("metric") or {}).get("executions") or 0) == 3,
          str(trend_item.get("metric")))

    again = api.data("POST", f"/api/v1/reports/{report_id}/trend-item", {
        "granularity": "day", "periods": 7,
    })
    trend_items = [item for item in again.get("items") or [] if item.get("section") == "trend"]
    check("重复插入覆盖趋势章节", len(trend_items) == 1, str(again.get("items"))[:240])
    check("覆盖后粒度更新", trend_items[0].get("title") == "指标趋势（按天）",
          str(trend_items[0].get("title")))

    detail = api.data("GET", f"/api/v1/reports/{report_id}")
    check("报告详情带趋势章节",
          any(item.get("section") == "trend" for item in detail.get("items") or []),
          str(detail.get("items"))[:240])

    saved = api.data("PUT", f"/api/v1/reports/{report_id}/items", {
        "items": [{
            "section": "trend",
            "title": trend_items[0].get("title"),
            "content": trend_items[0].get("content"),
            "metric": trend_items[0].get("metric"),
            "sortOrder": 0,
        }],
    })
    check("趋势章节可通过章节接口保存",
          [item.get("section") for item in saved.get("items") or []] == ["trend"],
          str(saved)[:200])

    resp, _body = api.call("GET", f"/api/v1/reports/{report_id}/export?format=md")
    text = resp.data.decode("utf-8", errors="replace")
    check("导出包含趋势章节标题", "指标趋势（按天）" in text, text[-300:])
    check("导出包含趋势表格", "| 周期 | 执行数 |" in text, text[-300:])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-trends-"))
    try:
        app = build_app(workdir)
        api = Api(app)
        project = api.data("POST", "/api/v1/projects", {"code": "TREND", "name": "趋势项目"})
        project_id = int(project["id"])
        v1 = int(api.data("POST", "/api/v1/project-versions", {
            "projectId": project_id, "versionCode": "v1.0", "name": "一版",
        })["id"])
        v2 = int(api.data("POST", "/api/v1/project-versions", {
            "projectId": project_id, "versionCode": "v2.0", "name": "二版",
        })["id"])

        conn = sqlite3.connect(workdir / "trends.db")
        conn.row_factory = sqlite3.Row
        try:
            seed(conn, project_id, v1, v2)
        finally:
            conn.close()

        check_trend(api, project_id)
        check_empty_project(api)
        check_compare(api, project_id, v1, v2)
        check_trend_item(api, project_id)
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    print("----------------------------------------")
    print(f"PASS={_passed} FAIL={_failed}")
    if _failed:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
