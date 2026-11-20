"""P2 验收门禁：定时调度/触发/调度tick + 指标 + JMeter负载模型 + 浏览器场景步骤。

    python backend/scripts/check_p2.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
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
        self.scope_project_id: int | None = None

    def _scoped(self, path: str) -> str:
        if not self.scope_project_id or "projectId=" in path:
            return path
        bare = path.split("?")[0]
        if not any(part.isdigit() for part in bare.strip("/").split("/")):
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}projectId={self.scope_project_id}"

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(self._scoped(path), method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} code={body.get('code')} msg={body.get('message')}")
        result = body.get("data")
        if (method == "POST" and not self.scope_project_id
                and path.split("?")[0].rstrip("/").endswith("/projects")):
            if isinstance(result, dict) and result.get("id"):
                self.scope_project_id = int(result["id"])
        return result


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_p2.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "check.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_p2_"))
    try:
        app = build_app(workdir)
        api = Api(app)
        pid = api.data("POST", "/api/v1/projects", {"name": "P2门禁", "code": "GATE-P2"})["id"]

        # 场景（接口步骤）供调度目标
        scn = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "调度场景",
            "steps": [{"name": "s", "extra": {"接口URL": "http://127.0.0.1:1/x", "请求方式": "GET"}}],
        })
        sch = api.data("POST", "/api/v1/schedules", {
            "projectId": pid, "name": "每分钟", "targetType": "scenario",
            "targetId": scn["id"], "intervalMinutes": 1,
        })
        check("调度创建", sch["intervalMinutes"] == 1, str(sch))
        # A3 边界：间隔钳到 [1, 1440]，enabled 严格布尔解析（字符串 "false" 不再误判为真）
        clamped = api.data("POST", "/api/v1/schedules", {
            "projectId": pid, "name": "超上限", "targetType": "scenario",
            "targetId": scn["id"], "intervalMinutes": 99999, "enabled": "false",
        })
        check("间隔超上限钳到 1440 且 enabled 字符串 false 判为关",
              clamped["intervalMinutes"] == 1440 and clamped["enabled"] is False, str(clamped)[:200])
        neg = api.data("POST", "/api/v1/schedules", {
            "projectId": pid, "name": "负间隔", "targetType": "scenario",
            "targetId": scn["id"], "intervalMinutes": -5,
        })
        check("间隔负数钳到 1", neg["intervalMinutes"] == 1, str(neg)[:200])
        lst = api.data("GET", f"/api/v1/schedules?projectId={pid}")["list"]
        check("调度列表", len(lst) >= 1, str(len(lst)))
        check("调度带 SCH 编号",
              all(str(s.get("scheduleNo") or "").startswith("SCH-") for s in lst),
              str(lst)[:200])

        # 自动化脚本「已被引用」口径：只认场景自动化 steps_json 里的 automation 步骤，
        # 定时调度与执行记录都不算引用；浮显数据 refs 带 SCN 编号
        used_script = api.data("POST", "/api/v1/automation/scripts", {
            "projectId": pid, "name": "被场景引用脚本", "category": "web", "content": "print(1)",
        })
        idle_script = api.data("POST", "/api/v1/automation/scripts", {
            "projectId": pid, "name": "无引用脚本", "category": "web", "content": "print(1)",
        })
        api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "引用自动化脚本的场景", "scenarioType": "web",
            "steps": [
                {"name": "接口步", "type": "interface",
                 "extra": {"接口URL": "http://127.0.0.1:1/x", "请求方式": "GET"}},
                {"name": "脚本步", "type": "automation", "scriptNo": used_script["scriptNo"]},
            ],
        })
        # 反向对照：只建脚本调度不应再点亮「已被引用」
        api.data("POST", "/api/v1/schedules", {
            "projectId": pid, "name": "脚本调度", "targetType": "script",
            "targetId": idle_script["id"], "intervalMinutes": 60,
        })
        scripts = api.data("GET", f"/api/v1/automation/scripts?projectId={pid}&category=web")["list"]
        used_item = next(s for s in scripts if s["id"] == used_script["id"])
        idle_item = next(s for s in scripts if s["id"] == idle_script["id"])
        check("被场景 automation 步骤引用的脚本判为已被引用且 refs 带 SCN 编号",
              used_item["referenced"] is True
              and any(str(r.get("refNo") or "").startswith("SCN-") for r in used_item.get("refs") or []),
              str(used_item.get("refs"))[:200])
        check("只有调度引用的脚本未被引用（调度与执行记录都不算）",
              idle_item["referenced"] is False and not idle_item.get("refs"),
              str(idle_item.get("refs"))[:200])

        # 场景状态字段：新建可指定停用，非法值拒绝
        scn_off = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "停用场景", "status": "disabled",
            "steps": [{"name": "s", "extra": {"接口URL": "http://127.0.0.1:1/x", "请求方式": "GET"}}],
        })
        check("场景新建可指定停用状态", scn_off["status"] == "disabled", str(scn_off)[:200])
        _, bad_body = api.call("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "非法状态", "status": "bogus",
            "steps": [{"name": "s", "extra": {"接口URL": "http://127.0.0.1:1/x", "请求方式": "GET"}}],
        })
        check("场景非法状态被拒绝", bad_body.get("code") == ErrorCode.PARAM_ERROR, str(bad_body)[:200])
        trig = api.data("POST", f"/api/v1/schedules/{sch['id']}/trigger", {})
        check("手动触发投递任务", trig.get("taskId") is not None, str(trig))

        # 调度 tick：把 next_run_at 拨到过去，_tick 应投递
        import sqlite3
        from aitp.services import test_schedule_service
        conn = sqlite3.connect(workdir / "check_p2.db")
        conn.row_factory = sqlite3.Row
        conn.execute("UPDATE biz_test_schedule SET next_run_at='2000-01-01T00:00:00+00:00'")
        conn.commit()
        fired = test_schedule_service._tick(workdir / "check_p2.db")
        check("调度tick投递到期任务", fired >= 1, str(fired))
        conn.close()

        # 指标
        m = api.data("GET", "/api/v1/automation/metrics")
        check("指标聚合返回", isinstance(m["list"], list), str(m)[:120])

        # JMeter 负载模型
        from aitp.services import automation_service
        home = workdir / "jmeter"
        (home / "bin").mkdir(parents=True, exist_ok=True)
        (home / "bin" / "jmeter.bat").write_text("@echo off\n")
        conn = sqlite3.connect(workdir / "check_p2.db")
        conn.row_factory = sqlite3.Row
        from aitp.repositories.sys_config_repo import SysConfigRepository
        repo = SysConfigRepository(conn)
        repo.set_value("performance.jmeter.home", str(home), "string", "", "global", 1)
        repo.set_value("performance.load_model", json.dumps({"threads": 10, "rampUp": 5}), "json", "", "global", 1)
        conn.commit()
        jmx = workdir / "p.jmx"
        jmx.write_text("<jmeterTestPlan/>")
        run_dir = workdir / "run"
        run_dir.mkdir(exist_ok=True)
        cmd = automation_service._jmeter_command(conn, {"file_name": "p.jmx"}, jmx, run_dir)
        check("负载模型 -Jthreads", any("-Jthreads=10" in c for c in cmd), str(cmd))
        conn.close()

        # 浏览器场景步骤（mock MCP）
        stub = ROOT / "backend" / "scripts" / "mock_mcp_server.py"
        api.data("PUT", "/api/v1/exploratory/settings",
                 {"mcpCommand": f'"{sys.executable}" "{stub}"'})
        bscn = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "浏览器场景",
            "steps": [{"name": "打开", "type": "browser", "action": "navigate", "url": "http://x.local/"},
                      {"name": "快照", "type": "browser", "action": "snapshot"}],
        })
        brun = api.data("POST", f"/api/v1/api-scenarios/{bscn['id']}/run", {})
        check("浏览器场景步骤通过", brun["passed"] is True and len(brun["runs"][0]["steps"]) == 2,
              str(brun["passed"]))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())