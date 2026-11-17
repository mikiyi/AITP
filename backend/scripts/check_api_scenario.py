"""多接口串联场景自动化验收门禁。

本地起一个桩 HTTP 服务（/login 发 token、/order 校验 token），验证：
- 场景顺序执行 + 变量提取（JSONPath）+ {{var}} 传递到下一步
- 每步断言复用接口断言口径（json:$.code = 0）
- 失败策略：某步失败即停（continueOnError=false）
- 数据驱动：记录集逐条跑整个场景
- 执行历史落库

    python backend/scripts/check_api_scenario.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
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


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/login"):
            body = {"code": 0, "data": {"token": "T1"}}
        elif self.path.startswith("/order"):
            ok = "token=T1" in self.path
            body = {"code": 0 if ok else 1, "data": {"orderId": "O1"}}
        else:
            body = {"code": 1}
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args: Any) -> None:
        pass


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
    db_path = workdir / "check_scenario.db"
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
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_scenario_"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    try:
        app = build_app(workdir)
        api = Api(app)
        project = api.data("POST", "/api/v1/projects", {"name": "场景门禁", "code": "GATE-SCN"})
        pid = project["id"]
        env = api.data("POST", "/api/v1/test-envs", {
            "projectId": pid, "name": "桩环境", "kind": "test",
            "app": {"baseUrl": base, "servers": [{"baseUrl": base}]},
            "db": {"type": "sqlite", "host": ""},
            "variables": {"tenant": "acme"},
        })
        eid = env["id"]

        steps = [
            {"name": "登录", "extra": {"接口URL": "/login", "请求方式": "GET",
                                      "断言规则": "json:$.code = 0\nlen:$.data = 1\ntime < 5000"},
             "extract": [{"var": "token", "path": "$.data.token"}]},
            {"name": "下单", "extra": {"接口URL": "/order?token={{token}}&tenant={{tenant}}",
                                      "请求方式": "GET", "断言规则": "json:$.code = 0"}},
        ]
        scn = api.data("POST", "/api/v1/api-scenarios",
                       {"projectId": pid, "name": "登录下单串联", "envId": eid, "steps": steps})
        check("场景创建带编号", str(scn["scenarioNo"]).startswith("SCN"), str(scn["scenarioNo"]))

        run = api.data("POST", f"/api/v1/api-scenarios/{scn['id']}/run", {})
        check("串联执行通过", run["passed"] is True, str(run["passed"]))
        check("两步都执行", len(run["runs"][0]["steps"]) == 2, str(len(run["runs"][0]["steps"])))
        check("变量提取 token", run["runs"][0]["variables"].get("token") == "T1",
              str(run["runs"][0]["variables"]))
        step2 = run["runs"][0]["steps"][1]
        check("{{token}} 传递到第二步", "token=T1" in str(step2["request"]["url"]),
              str(step2["request"]["url"]))

        # 数据驱动
        api.data("PUT", f"/api/v1/api-scenarios/{scn['id']}",
                 {"dataSource": '[{"u":"a"},{"u":"b"}]'})
        run2 = api.data("POST", f"/api/v1/api-scenarios/{scn['id']}/run", {})
        check("数据驱动逐条跑场景", run2["recordCount"] == 2 and run2["passed"] is True,
              str(run2["recordCount"]))

        # 失败即停
        bad = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "失败即停", "envId": eid,
            "steps": [
                {"name": "必败", "extra": {"接口URL": "/login", "请求方式": "GET",
                                          "断言规则": "json:$.code = 999"}},
                {"name": "不应执行", "extra": {"接口URL": "/order", "请求方式": "GET"}},
            ],
        })
        run3 = api.data("POST", f"/api/v1/api-scenarios/{bad['id']}/run", {})
        check("失败即停只跑一步", run3["passed"] is False and len(run3["runs"][0]["steps"]) == 1,
              str(len(run3["runs"][0]["steps"])))

        runs = api.data("GET", f"/api/v1/api-scenarios/{scn['id']}/runs")
        check("执行历史落库", runs["total"] >= 2, str(runs["total"]))

        check("环境变量注入 {{tenant}}", "tenant=acme" in str(step2["request"]["url"]),
              str(step2["request"]["url"]))
        check("len/time 断言通过",
              all(a["passed"] for a in run["runs"][0]["steps"][0]["assertions"]),
              str(run["runs"][0]["steps"][0]["assertions"]))
        tr = api.data("GET", f"/api/v1/api-scenarios/{scn['id']}/trend")
        check("趋势聚合", tr["total"] >= 1 and isinstance(tr["points"], list), str(tr)[:120])
        resp = api.client.open(
            f"/api/v1/api-scenarios/{scn['id']}/runs/{run['runId']}/report"
            f"?projectId={scn['projectId']}"
        )
        check("HTML 报告可分享", resp.mimetype == "text/html" and "场景报告" in resp.get_data(as_text=True),
              str(resp.mimetype))
        bug = api.data("POST", f"/api/v1/api-scenarios/{bad['id']}/runs/{run3['runId']}/to-bug", {})
        check("失败转缺陷", str(bug["bugNo"]).startswith("BUG"), str(bug))
    finally:
        server.shutdown()
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())