"""验证执行闭环：测试计划 → 轮次 → 批量结果 → 失败转缺陷 → 追溯矩阵 → 发布准入。

    python backend/scripts/check_test_execution.py

全部在临时库上运行，不读写正式数据。
"""

from __future__ import annotations

import pathlib
import shutil
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
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services.knowledge_storage import CONFIG_PATH  # noqa: E402
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

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} 失败: code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None) -> JsonDict:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code,
              f"code={body.get('code')} msg={body.get('message')}")
        return body


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "execution.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    SysConfigRepository(conn).set_value(
        CONFIG_PATH, str(workdir / "knowledge"), "string", "执行闭环检查临时目录")
    conn.commit()
    conn.close()
    cfg = AppConfig(
        host="127.0.0.1",
        port=8764,
        debug=False,
        db_path=db_path,
        log_path=workdir / "execution.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-exec-"))
    try:
        app = build_app(workdir)
        api = Api(app)

        conn = sqlite3.connect(workdir / "execution.db")
        conn.row_factory = sqlite3.Row
        check("迁移 024 已应用", 24 in applied_versions(conn))
        check("迁移 025 已应用", 25 in applied_versions(conn))

        env = api.data("POST", "/api/v1/test-envs", {
            "name": "执行闭环生产环境",
            "kind": "prod",
            "db": {"type": "sqlite", "host": ""},
        })
        env_id = int(env["id"])
        check("环境类型落库", env["kind"] == "prod" and env["kindLabel"] == "生产", str(env)[:160])
        env_list = api.data("GET", "/api/v1/test-envs")
        check("环境清单带类型选项",
              any(item["value"] == "prod" for item in env_list.get("kinds", [])), str(env_list)[:160])
        api.expect("非法环境类型被拒绝", "PUT", f"/api/v1/test-envs/{env_id}",
                   ErrorCode.PARAM_ERROR, {"kind": "spaceship"})

        project = api.data("POST", "/api/v1/projects", {"code": "EXEC", "name": "执行闭环项目"})
        project_id = int(project["id"])
        requirement = api.data("POST", "/api/v1/requirements", {
            "projectId": project_id, "title": "登录能力",
        })
        requirement_id = int(requirement["id"])

        case_ids: list[int] = []
        for index, priority in enumerate(("P0", "P1", "P2"), start=1):
            case = api.data("POST", "/api/v1/test-cases", {
                "projectId": project_id,
                "requirementId": requirement_id,
                "title": f"登录用例 {index}",
                "priority": priority,
                "preconditions": "已打开登录页",
                "steps": [{"action": f"输入账号 {index}", "expected": "输入成功"}],
                "expectedResult": "登录成功",
            })
            case_ids.append(int(case["id"]))
        check("用例创建成功", len(case_ids) == 3)

        plan = api.data("POST", "/api/v1/test-plans", {
            "projectId": project_id,
            "name": "登录回归计划",
            "description": "覆盖登录主链路",
            "owner": "local",
            "defaultEnvId": env_id,
            "scope": {"requirementId": requirement_id},
        })
        plan_id = int(plan["id"])
        check("测试计划创建成功", plan_id > 0 and plan["scope"]["requirementId"] == requirement_id)
        check("计划默认环境落库", plan["defaultEnvId"] == env_id, str(plan)[:160])
        api.expect("不存在的环境不能绑定", "POST", "/api/v1/test-plans", ErrorCode.PARAM_ERROR, {
            "projectId": project_id, "name": "脏环境计划", "defaultEnvId": 999999,
        })

        plan_list = api.data("GET", f"/api/v1/test-plans?projectId={project_id}")
        check("计划列表可读", plan_list["total"] == 1)
        plan_detail = api.data("GET", f"/api/v1/test-plans/{plan_id}")
        check("计划详情可读", plan_detail["name"] == "登录回归计划")

        run = api.data("POST", f"/api/v1/test-plans/{plan_id}/runs", {
            "name": "登录回归第 1 轮",
        })
        run_id = int(run["id"])
        check("按计划创建轮次", run_id > 0 and run["planId"] == plan_id)
        check("轮次冻结全部用例", run["total"] == 3, str(run))
        check("轮次继承计划默认环境",
              run["envId"] == env_id and run["envName"] == "执行闭环生产环境"
              and run["envKind"] == "prod", str(run)[:200])

        api.expect("不存在的环境不能发起轮次", "POST",
                   f"/api/v1/test-plans/{plan_id}/runs", ErrorCode.PARAM_ERROR,
                   {"name": "脏环境轮次", "envId": 999999})
        api.expect("被轮次引用的环境不能删除", "DELETE",
                   f"/api/v1/test-envs/{env_id}", ErrorCode.RESOURCE_REFERENCED)

        detail = api.data("GET", f"/api/v1/test-runs/{run_id}")
        check("轮次详情带用例快照", len(detail["cases"]) == 3)
        check("快照保留步骤与预期",
              detail["cases"][0]["steps"][0]["action"].startswith("输入账号")
              and detail["cases"][0]["expectedResult"] == "登录成功")
        check("轮次初始为执行中", detail["status"] == "running")

        # 追加用例只在轮次进行中可做：按范围再次拉取时已在轮次内的跳过
        added = api.data("POST", f"/api/v1/test-runs/{run_id}/cases", {
            "caseIds": case_ids,
        })
        check("重复追加用例被跳过", added["count"] == 0 and len(added["skipped"]) == 3)

        first_id = int(detail["cases"][0]["id"])
        second_id = int(detail["cases"][1]["id"])
        third_id = int(detail["cases"][2]["id"])
        api.data("PUT", f"/api/v1/test-runs/{run_id}/cases/{first_id}", {
            "result": "passed", "executedBy": "local",
        })
        batch = api.data("POST", f"/api/v1/test-runs/{run_id}/cases/batch", {
            "entries": [
                {"id": second_id, "result": "failed", "actualResult": "提示账号不存在",
                 "remark": "必现"},
                {"id": third_id, "result": "blocked", "remark": "依赖服务未就绪"},
            ],
            "executedBy": "local",
        })
        check("批量回写成功", batch["updated"] == [second_id, third_id], str(batch))

        detail = api.data("GET", f"/api/v1/test-runs/{run_id}")
        check("轮次计数正确",
              detail["total"] == 3 and detail["passed"] == 1
              and detail["failed"] == 1 and detail["blocked"] == 1,
              str(detail))
        check("全部执行后自动完成", detail["status"] == "completed")
        check("轮次通过率按已执行口径", detail["stats"]["passRate"] == round(1 / 3, 4))

        case_row = api.data("GET", f"/api/v1/test-cases/{case_ids[1]}")
        check("用例全局状态回写为失败", case_row["status"] == "fail", str(case_row.get("status")))
        execution_count = conn.execute(
            "SELECT COUNT(*) FROM biz_case_execution WHERE run_id = ?", (run_id,)
        ).fetchone()[0]
        check("执行流水按轮次落库", int(execution_count) == 3)

        bug = api.data("POST", f"/api/v1/test-runs/{run_id}/cases/{second_id}/bug", {
            "assignee": "local",
        })
        check("失败用例可转缺陷", bug["created"] is True and int(bug["bugId"]) > 0, str(bug))
        bug_id = int(bug["bugId"])
        bug_detail = api.data("GET", f"/api/v1/bugs/{bug_id}")
        check("缺陷预填漏洞场景信息", "账号不存在" in (bug_detail["actualResult"] or ""))
        check("缺陷反向关联用例", case_ids[1] in bug_detail["linkedCaseIds"])
        check("缺陷详情带关联用例标题",
              any(item["id"] == case_ids[1] for item in bug_detail["linkedCases"]))

        again = api.data("POST", f"/api/v1/test-runs/{run_id}/cases/{second_id}/bug", {})
        check("重复转缺陷不重复创建", again["created"] is False and int(again["bugId"]) == bug_id)
        api.expect("通过用例不能转缺陷", "POST",
                   f"/api/v1/test-runs/{run_id}/cases/{first_id}/bug", ErrorCode.ILLEGAL_OPERATION)

        trace = api.data("GET",
                         f"/api/v1/traceability?projectId={project_id}&runId={run_id}")
        row = trace["list"][0]
        check("追溯矩阵关联需求", row["reqNo"] and row["caseCount"] == 3, str(row))
        check("追溯矩阵执行口径", row["executedCount"] == 3 and row["failed"] == 1)
        check("追溯矩阵带未闭环缺陷", row["openBugs"] == 1)
        check("有失败的覆盖状态", row["coverageStatus"] == "failed")
        check("追溯汇总通过率", trace["summary"]["passRate"] == round(1 / 3 * 100, 2))

        export = api.client.get(f"/api/v1/traceability/export?projectId={project_id}&format=csv")
        check("追溯矩阵可导出 CSV",
              export.status_code == 200 and "需求编号" in export.data.decode("utf-8-sig"),
              f"status={export.status_code}")

        preview = api.data("POST", "/api/v1/release-gates/preview", {
            "projectId": project_id, "runId": run_id,
            "thresholds": {"overallPassRate": 90, "p0PassRate": 100,
                           "p1PassRate": 100, "maxOpenP0": 0, "maxOpenP1": 0,
                           "minCoverageRate": 100},
        })
        check("门禁返回六项检查", len(preview["checks"]) == 6)
        check("存在失败用例时门禁阻止发布", preview["status"] == "blocked")
        check("门禁列出阻断项", "overallPassRate" in preview["blocking"])

        gate = api.data("POST", "/api/v1/release-gates", {
            "projectId": project_id, "runId": run_id, "name": "v1 发布准入",
            "thresholds": {"overallPassRate": 90},
        })
        check("发布准入可留档", gate["status"] == "blocked" and gate["conclusion"])
        gate_list = api.data("GET", f"/api/v1/release-gates?projectId={project_id}")
        check("发布准入列表可读", gate_list["total"] == 1)

        export_run = api.client.get(f"/api/v1/test-runs/{run_id}/export?format=csv")
        check("轮次结果可导出", export_run.status_code == 200
              and "关联缺陷" in export_run.data.decode("utf-8-sig"))

        # 轮次完成后进入冻结：不能追加用例，也不能再改已记录的结果
        api.expect("已完成轮次不能再追加用例", "POST",
                   f"/api/v1/test-runs/{run_id}/cases", ErrorCode.ILLEGAL_OPERATION,
                   {"caseIds": case_ids})
        api.expect("已完成轮次结果已冻结", "PUT",
                   f"/api/v1/test-runs/{run_id}/cases/{first_id}", ErrorCode.ILLEGAL_OPERATION,
                   {"result": "failed", "executedBy": "local"})

        api.data("DELETE", f"/api/v1/test-runs/{run_id}")
        remaining = conn.execute(
            "SELECT COUNT(*) FROM biz_test_run_case WHERE run_id = ? AND is_deleted = 0", (run_id,)
        ).fetchone()[0]
        check("删除轮次同时下线快照", int(remaining) == 0)
        trace2 = api.data("GET", f"/api/v1/traceability?projectId={project_id}")
        check("无轮次时按用例当前状态追溯", trace2["summary"]["caseCount"] == 3)

        # 任务队列：表结构、列表统计、取消与删除。合成任务不启动 worker，状态可控。
        now = "2026-11-06T00:00:00Z"
        cur = conn.execute(
            "INSERT INTO sys_task (kind, title, status, priority, payload_json, result_json,"
            " error_message, attempts, max_attempts, progress, cancel_requested,"
            " created_at, updated_at)"
            " VALUES ('check.synthetic', '门禁合成任务', 'queued', 100, '{}', '{}', '', 0, 1, 0, 0, ?, ?)",
            (now, now),
        )
        task_id = int(cur.lastrowid or 0)
        conn.commit()
        task_page = api.data("GET", "/api/v1/tasks")
        check("任务队列可读", task_page["total"] >= 1 and task_page["stats"]["queued"] >= 1,
              str(task_page.get("stats")))
        check("任务类型带中文标签",
              any(item["value"] == "automation.run" and item["label"]
                  for item in task_page["kinds"]), str(task_page.get("kinds")))
        task = api.data("GET", f"/api/v1/tasks/{task_id}")
        check("任务详情带中文状态", task["status"] == "queued" and task["statusLabel"] == "排队中",
              str(task)[:160])
        api.expect("排队任务不能重试", "POST", f"/api/v1/tasks/{task_id}/retry",
                   ErrorCode.ILLEGAL_OPERATION)
        cancelled = api.data("POST", f"/api/v1/tasks/{task_id}/cancel")
        check("排队任务可取消", cancelled["status"] == "cancelled", str(cancelled)[:160])
        api.expect("重复取消被拒绝", "POST", f"/api/v1/tasks/{task_id}/cancel",
                   ErrorCode.ILLEGAL_OPERATION)
        removed = api.data("DELETE", f"/api/v1/tasks/{task_id}")
        check("已结束任务可删除", removed["deleted"] is True, str(removed))
        api.expect("非法任务状态筛选被拒绝", "GET", "/api/v1/tasks?status=whatever",
                   ErrorCode.PARAM_ERROR)

        conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
