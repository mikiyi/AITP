"""自动化结果回写用例 / 轮次验收脚本（GAP-07）。

在临时库上验证：
- 迁移 027：脚本映射字段、执行记录回写字段、结果明细表
- 脚本映射配置：按编号 / 按标签，非法方式被拒绝
- 按退出码兜底回写：成功与失败都能更新轮次用例与全局状态
- 轮次完成后结果冻结：再回写只把执行记录标成回写失败，不动已冻结的结果
- JUnit 解析：用例级通过/失败、缺失用例部分回写、未匹配测试被记录
- 解析失败时按退出码回写并保留原始输出提示
- 未配置映射时明确提示，不乱改用例
- 回写链路可反查：轮次 → 自动化执行记录 → 用例结果明细
- 轮次触发的异步任务入队

    python backend/scripts/check_automation_writeback.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.repositories.automation_repo import AutomationRunRepository  # noqa: E402
from aitp.services import automation_writeback  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None) -> JsonDict:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code,
              f"code={body.get('code')} msg={body.get('message')}")
        return body


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "writeback.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8765,
        debug=False,
        db_path=db_path,
        log_path=workdir / "writeback.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


PY_OK = "print('auto-pass')\n"
PY_FAIL = "import sys\nsys.stderr.write('assertion boom')\nsys.exit(2)\n"

JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites>
  <testsuite name="pytest" tests="3">
    <testcase classname="tests.test_login" name="test_ft_001_login" time="0.01"/>
    <testcase classname="tests.test_login" name="test_ft_002_wrong_code" time="0.02">
      <failure message="assert 401 == 200">AssertionError</failure>
    </testcase>
    <testcase classname="tests.test_other" name="test_unknown_case" time="0.03"/>
  </testsuite>
</testsuites>
"""


def create_case(api: Api, project_id: int, title: str, tags: str = "") -> JsonDict:
    return cast(JsonDict, api.data("POST", "/api/v1/test-cases", {
        "projectId": project_id,
        "title": title,
        "tags": tags,
        "steps": [{"action": "打开页面", "expected": "正常打开"}],
    }))


def create_script(api: Api, project_id: int, name: str, content: str,
                  mode: str = "case_no", value: str = "") -> JsonDict:
    return cast(JsonDict, api.data("POST", "/api/v1/automation/scripts", {
        "name": name,
        "category": "web",
        "projectId": project_id,
        "content": content,
        "caseMatchMode": mode,
        "caseMatchValue": value,
    }))


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-writeback-"))
    try:
        app = build_app(workdir)
        api = Api(app)
        db_path = workdir / "writeback.db"

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        check("迁移 027 已应用", 27 in applied_versions(conn))
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(biz_automation_script)")}
        check("脚本表有映射字段",
              {"case_match_mode", "case_match_value"} <= columns, str(sorted(columns)))
        run_columns = {row["name"] for row in conn.execute("PRAGMA table_info(biz_automation_run)")}
        check("执行记录表有回写字段",
              {"test_run_id", "writeback_status", "writeback_summary"} <= run_columns,
              str(sorted(run_columns)))
        result_table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'biz_automation_case_result'"
        ).fetchone()
        check("用例结果明细表已建", result_table is not None)

        project = api.data("POST", "/api/v1/projects", {"code": "AWB", "name": "自动化回写项目"})
        project_id = int(project["id"])
        cases = [
            create_case(api, project_id, "登录成功", tags="smoke"),
            create_case(api, project_id, "验证码错误", tags="smoke"),
            create_case(api, project_id, "退出登录", tags="regression"),
        ]
        case_ids = [int(item["id"]) for item in cases]
        case_nos = [str(item["caseNo"]) for item in cases]
        check("用例编号可读", all(case_nos), str(case_nos))

        plan = api.data("POST", "/api/v1/test-plans", {
            "projectId": project_id, "name": "自动化回写计划",
        })
        run = api.data("POST", f"/api/v1/test-plans/{plan['id']}/runs", {"name": "自动化回写轮次"})
        run_id = int(run["id"])

        # --- 映射配置 ---
        script_ok = create_script(
            api, project_id, "回写成功脚本", PY_OK, "case_no", f"{case_nos[0]},{case_nos[1]}"
        )
        check("脚本保存映射方式", script_ok.get("caseMatchMode") == "case_no", str(script_ok)[:200])
        check("脚本保存映射值",
              script_ok.get("caseMatchValue") == f"{case_nos[0]},{case_nos[1]}",
              str(script_ok)[:200])
        api.expect("非法映射方式被拒绝", "POST", "/api/v1/automation/scripts",
                   ErrorCode.PARAM_ERROR, {
                       "name": "脏映射", "category": "web", "content": PY_OK,
                       "caseMatchMode": "spaceship", "caseMatchValue": "X",
                   })

        # --- 退出码兜底回写：成功 ---
        result = api.data("POST", f"/api/v1/automation/scripts/{script_ok['id']}/run",
                          {"testRunId": run_id})
        check("脚本挂轮次执行成功", result.get("status") == "success", str(result)[:240])
        check("按退出码回写", result.get("writebackStatus") == "fallback", str(result)[:240])
        check("回写摘要可读", "回写 2 条" in str(result.get("writebackSummary") or ""),
              str(result)[:240])
        check("执行记录关联轮次", int(result.get("testRunId") or 0) == run_id, str(result)[:240])

        detail = api.data("GET", f"/api/v1/test-runs/{run_id}")
        by_case = {int(item["caseId"]): item for item in detail["cases"]}
        check("轮次用例回写为通过",
              by_case[case_ids[0]]["result"] == "passed"
              and by_case[case_ids[1]]["result"] == "passed",
              str(by_case[case_ids[0]])[:200])
        check("未映射用例保持未执行", by_case[case_ids[2]]["result"] == "not_executed",
              str(by_case[case_ids[2]])[:200])
        check("轮次统计同步", int(detail.get("passed") or 0) == 2, str(detail)[:200])

        case_detail = api.data("GET", f"/api/v1/test-cases/{case_ids[0]}")
        check("用例全局状态同步为通过", case_detail.get("status") == "pass",
              str(case_detail)[:200])

        # --- 退出码兜底回写：失败 ---
        script_fail = create_script(
            api, project_id, "回写失败脚本", PY_FAIL, "case_no", case_nos[2]
        )
        failed = api.data("POST", f"/api/v1/automation/scripts/{script_fail['id']}/run",
                          {"testRunId": run_id})
        check("失败脚本按退出码回写",
              failed.get("writebackStatus") == "fallback"
              and failed.get("status") == "failed", str(failed)[:240])
        detail = api.data("GET", f"/api/v1/test-runs/{run_id}")
        by_case = {int(item["caseId"]): item for item in detail["cases"]}
        check("失败结果写回轮次", by_case[case_ids[2]]["result"] == "failed",
              str(by_case[case_ids[2]])[:240])
        case_detail = api.data("GET", f"/api/v1/test-cases/{case_ids[2]}")
        check("失败状态可提缺陷", case_detail.get("status") == "fail", str(case_detail)[:200])
        check("失败原因保留原始输出",
              "退出码" in str(by_case[case_ids[2]].get("actualResult") or ""),
              str(by_case[case_ids[2]])[:240])

        # 三条用例都有结果后轮次自动完成；此后回写必须撞上冻结规则，且不能把执行留痕弄丢
        closed = api.data("GET", f"/api/v1/test-runs/{run_id}")
        check("全部有结果后轮次自动完成", closed["status"] == "completed", str(closed)[:200])
        frozen = api.data("POST", f"/api/v1/automation/scripts/{script_ok['id']}/run",
                          {"testRunId": run_id})
        check("已完成轮次回写记为失败", frozen.get("writebackStatus") == "failed",
              str(frozen)[:240])
        check("回写失败原因可读",
              "回写失败" in str(frozen.get("writebackSummary") or "")
              and "轮次已完成" in str(frozen.get("writebackSummary") or ""),
              str(frozen.get("writebackSummary"))[:240])
        check("冻结回写不影响执行留痕", int(frozen.get("id") or 0) > 0
              and frozen.get("status") == "success", str(frozen)[:240])
        after_frozen = api.data("GET", f"/api/v1/test-runs/{run_id}")
        check("冻结轮次结果保持不变",
              {int(item["caseId"]): item["result"] for item in after_frozen["cases"]}
              == {int(item["caseId"]): item["result"] for item in detail["cases"]},
              str(after_frozen)[:240])

        # 后续场景仍要验证「能回写」，另起一轮进行中的轮次
        run_open = api.data("POST", f"/api/v1/test-plans/{plan['id']}/runs",
                            {"name": "自动化回写第 2 轮"})
        open_id = int(run_open["id"])

        # --- JUnit 解析：部分回写 + 未匹配记录 ---
        script_junit = create_script(
            api, project_id, "JUnit 脚本", PY_OK, "case_no", ",".join(case_nos)
        )
        run_row = AutomationRunRepository(conn).insert({
            "run_no": "RUN-900",
            "script_id": int(script_junit["id"]),
            "script_no": script_junit["scriptNo"],
            "script_name": script_junit["name"],
            "category": "web",
            "status": "failed",
            "exit_code": 1,
            "started_at": "2026-11-06T00:00:00Z",
            "test_run_id": open_id,
        })
        conn.commit()
        run_dir = pathlib.Path(db_path).parent / "automation" / "runs" / "RUN-900"
        run_dir.mkdir(parents=True, exist_ok=True)
        (run_dir / "junit.xml").write_text(JUNIT, encoding="utf-8")

        script_row = dict(conn.execute(
            "SELECT * FROM biz_automation_script WHERE id = ?", (script_junit["id"],)
        ).fetchone())
        summary = automation_writeback.writeback(
            conn,
            script_row,
            AutomationRunRepository(conn).get(int(run_row)) or {},
            db_path,
            test_run_id=open_id,
            exit_code=1,
            run_status="failed",
            stdout="",
            stderr="",
            error_message="",
        )
        conn.commit()
        check("JUnit 部分回写", summary.get("status") == "partial", str(summary)[:300])
        check("未匹配测试被记录", "test_unknown_case" in "、".join(summary.get("unmatched") or []),
              str(summary.get("unmatched")))
        rows = conn.execute(
            "SELECT case_id, result, mapped_by FROM biz_automation_case_result"
            " WHERE automation_run_id = ? ORDER BY case_id", (int(run_row),)
        ).fetchall()
        check("结果明细按用例落库", len(rows) == 2, str([dict(row) for row in rows]))
        check("映射来源标为按编号",
              all(row["mapped_by"] == "case_no" for row in rows), str([dict(row) for row in rows]))
        result_map = {int(row["case_id"]): row["result"] for row in rows}
        check("JUnit 通过/失败映射正确",
              result_map.get(case_ids[0]) == "passed" and result_map.get(case_ids[1]) == "failed",
              str(result_map))
        detail = api.data("GET", f"/api/v1/test-runs/{open_id}")
        by_case = {int(item["caseId"]): item for item in detail["cases"]}
        check("JUnit 结果写回轮次",
              by_case[case_ids[1]]["result"] == "failed", str(by_case[case_ids[1]])[:200])
        check("缺失用例保持未执行", by_case[case_ids[2]]["result"] == "not_executed",
              str(by_case[case_ids[2]])[:200])

        automation_detail = api.data("GET", f"/api/v1/automation/runs/{int(run_row)}")
        check("执行记录可反查用例结果", len(automation_detail.get("caseResults") or []) == 2,
              str(automation_detail)[:300])
        check("执行记录带回写状态与摘要",
              automation_detail.get("writebackStatus") == "partial"
              and bool(automation_detail.get("writebackSummary")),
              str(automation_detail)[:200])

        run_automation = api.data("GET", f"/api/v1/test-runs/{open_id}/automation-runs")
        check("轮次可反查自动化执行记录",
              any(int(item["id"]) == int(run_row) for item in run_automation["list"]),
              str(run_automation)[:300])

        # --- 解析失败：兜底并提示 ---
        bad_dir = pathlib.Path(db_path).parent / "automation" / "runs" / "RUN-901"
        bad_dir.mkdir(parents=True, exist_ok=True)
        (bad_dir / "junit.xml").write_text("<testsuite><broken>", encoding="utf-8")
        bad_run = AutomationRunRepository(conn).insert({
            "run_no": "RUN-901",
            "script_id": int(script_junit["id"]),
            "script_no": script_junit["scriptNo"],
            "script_name": script_junit["name"],
            "category": "web",
            "status": "failed",
            "exit_code": 1,
            "started_at": "2026-11-06T00:00:00Z",
            "test_run_id": open_id,
        })
        conn.commit()
        fallback = automation_writeback.writeback(
            conn,
            script_row,
            AutomationRunRepository(conn).get(int(bad_run)) or {},
            db_path,
            test_run_id=open_id,
            exit_code=1,
            run_status="failed",
            stdout="pytest crashed",
            stderr="collection error",
            error_message="",
        )
        conn.commit()
        check("XML 损坏时按退出码回写", fallback.get("status") == "fallback",
              str(fallback)[:300])
        check("兜底提示包含原因", "退出码" in str(fallback.get("summary") or ""),
              str(fallback.get("summary")))

        # --- 未配置映射 ---
        before = api.data("GET", f"/api/v1/test-runs/{open_id}")
        before_map = {int(item["caseId"]): item["result"] for item in before["cases"]}
        unmapped = create_script(api, project_id, "未配置映射", PY_OK)
        skipped = api.data("POST", f"/api/v1/automation/scripts/{unmapped['id']}/run",
                           {"testRunId": open_id})
        check("未配置映射时明确跳过", skipped.get("writebackStatus") == "skipped",
              str(skipped)[:240])
        detail = api.data("GET", f"/api/v1/test-runs/{open_id}")
        after_map = {int(item["caseId"]): item["result"] for item in detail["cases"]}
        check("未配置映射不改轮次结果", after_map == before_map, str(after_map))

        # --- 按标签映射 ---
        tag_script = create_script(
            api, project_id, "标签映射脚本", PY_OK, "tag", "smoke"
        )
        tag_result = api.data("POST", f"/api/v1/automation/scripts/{tag_script['id']}/run",
                             {})
        check("按标签映射可回写", tag_result.get("writebackStatus") == "fallback",
              str(tag_result)[:240])
        check("标签命中两条用例", "回写 2 条" in str(tag_result.get("writebackSummary") or ""),
              str(tag_result)[:240])

        # --- 轮次发起自动化：校验与入队（另起一轮进行中的轮次，回写才有意义） ---
        run_trigger = api.data("POST", f"/api/v1/test-plans/{plan['id']}/runs",
                               {"name": "自动化回写第 3 轮"})
        trigger_id = int(run_trigger["id"])
        api.expect("不存在的脚本被拒绝", "POST", f"/api/v1/test-runs/{trigger_id}/automation-run",
                   ErrorCode.RESOURCE_NOT_FOUND, {"scriptIds": [999999]})
        api.expect("空脚本列表被拒绝", "POST", f"/api/v1/test-runs/{trigger_id}/automation-run",
                   ErrorCode.PARAM_ERROR, {"scriptIds": []})
        other_project = api.data("POST", "/api/v1/projects", {"code": "AWB2", "name": "另一个项目"})
        other_script = create_script(
            api, int(other_project["id"]), "跨项目脚本", PY_OK, "case_no", case_nos[0]
        )
        api.expect("跨项目脚本被拒绝", "POST", f"/api/v1/test-runs/{trigger_id}/automation-run",
                   ErrorCode.PARAM_ERROR, {"scriptIds": [other_script["id"]]})

        queued = api.data("POST", f"/api/v1/test-runs/{trigger_id}/automation-run",
                          {"scriptIds": [script_ok["id"]], "timeoutSeconds": 30})
        check("轮次执行自动化默认入队", queued.get("queued") is True, str(queued)[:240])
        check("任务 payload 带轮次",
              int(queued["tasks"][0]["payload"]["testRunId"]) == trigger_id, str(queued)[:300])

        sync = api.data("POST", f"/api/v1/test-runs/{trigger_id}/automation-run",
                        {"scriptIds": [script_ok["id"]], "sync": True})
        check("同步执行返回执行记录", int(sync.get("total") or 0) == 1, str(sync)[:240])
        sync_detail = api.data("GET", f"/api/v1/test-runs/{trigger_id}")
        check("同步执行结果回写到本轮",
              all(item["result"] == "passed" for item in sync_detail["cases"][:2]),
              str(sync_detail)[:240])

        conn.close()
    finally:
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    print("----------------------------------------")
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：" + "、".join(_failures))
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
