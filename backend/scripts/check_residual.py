"""残留子项验收门禁：独立数据池 / 轮次环境差异归因 / 执行失败自动转缺陷。

覆盖：
- 数据池 CRUD、同名拦截、解析预览、pool:<名称> 被场景数据驱动消费
- 轮次环境差异归因：环境敏感 / 一致失败 / 无基线 / 未绑定环境
- automation.auto_bug 开关：默认关闭不建缺陷，开启后场景失败自动建缺陷
- 自动转缺陷同一次执行只建一条（重复调用跳过）

    python backend/scripts/check_residual.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
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
from aitp.services import auto_bug_service  # noqa: E402
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
        body = {"code": 0, "data": {"ok": True}}
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

    def error_code(self, method: str, path: str, payload: JsonDict | None = None) -> int:
        _, body = self.call(method, path, payload)
        return int(body.get("code") or 0)


def build_app(workdir: pathlib.Path) -> tuple[Flask, pathlib.Path]:
    db_path = workdir / "check_residual.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "check.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg), db_path


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_residual_"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    try:
        app, db_path = build_app(workdir)
        api = Api(app)
        project = api.data("POST", "/api/v1/projects", {"name": "残留门禁", "code": "GATE-RSD"})
        pid = project["id"]
        env_a = api.data("POST", "/api/v1/test-envs", {
            "projectId": pid, "name": "环境A", "kind": "test",
            "app": {"baseUrl": base, "servers": [{"baseUrl": base}]},
            "db": {"type": "sqlite", "host": ""},
        })
        env_b = api.data("POST", "/api/v1/test-envs", {
            "projectId": pid, "name": "环境B", "kind": "test",
            "app": {"baseUrl": base, "servers": [{"baseUrl": base}]},
            "db": {"type": "sqlite", "host": ""},
        })

        # ---------------- 数据池 ----------------
        pool = api.data("POST", "/api/v1/data-pools", {
            "projectId": pid, "name": "订单组", "description": "两条下单数据",
            "content": '[{"u":"a"},{"u":"b"}]',
        })
        check("数据池创建", pool["name"] == "订单组", str(pool))
        dup = api.error_code("POST", "/api/v1/data-pools", {
            "projectId": pid, "name": "订单组", "content": "x",
        })
        check("同名数据池被拦截", dup != ErrorCode.SUCCESS, str(dup))
        page = api.data("GET", f"/api/v1/data-pools?projectId={pid}")
        check("数据池列表按项目过滤", page["total"] == 1 and page["list"][0]["name"] == "订单组",
              str(page))
        preview = api.data("POST", f"/api/v1/data-pools/{pool['id']}/preview", {})
        check("数据池预览解析记录集", preview["rowCount"] == 2, str(preview))

        case = api.data("POST", "/api/v1/test-cases", {
            "projectId": pid, "title": "池引用用例", "caseType": "interface",
            "extra": {"测试数据": "pool:订单组"},
        })
        case_preview = api.data("POST", "/api/v1/test-cases/preview-test-data",
                                {"value": "pool:订单组", "projectId": pid})
        check("用例测试数据 pool 引用可预览", case_preview["pool"] == "订单组"
              and case_preview["rowCount"] == 2, str(case_preview))
        missing = api.error_code("POST", "/api/v1/test-cases/preview-test-data",
                                 {"value": "pool:不存在", "projectId": pid})
        check("不存在的池引用给出错误", missing != ErrorCode.SUCCESS, str(missing))

        scenario = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "池驱动场景", "envId": env_a["id"],
            "dataSource": "pool:订单组",
            "steps": [{"name": "健康检查", "extra": {
                "接口URL": "/health", "请求方式": "GET", "断言规则": "json:$.code = 0"}}],
        })
        run = api.data("POST", f"/api/v1/api-scenarios/{scenario['id']}/run", {})
        check("场景按 pool 数据逐条执行", run["passed"] is True and run["recordCount"] == 2,
              str(run["recordCount"]))

        # 改名 / 删除前必须拦住仍在引用 pool:<名称> 的用例与场景
        _, rename_body = api.call(
            "PUT", f"/api/v1/data-pools/{pool['id']}", {"name": "订单组新"})
        refs = (rename_body.get("data") or {}).get("references") or []
        check("被引用数据池改名被拦截",
              rename_body.get("code") == ErrorCode.RESOURCE_REFERENCED,
              str(rename_body)[:200])
        check("拦截回传引用方（用例 + 场景）",
              {item.get("type") for item in refs} == {"case", "scenario"}, str(refs))
        _, delete_body = api.call("DELETE", f"/api/v1/data-pools/{pool['id']}")
        check("被引用数据池删除被拦截",
              delete_body.get("code") == ErrorCode.RESOURCE_REFERENCED,
              str(delete_body)[:200])
        edited = api.data("PUT", f"/api/v1/data-pools/{pool['id']}", {"description": "只改说明"})
        check("只改说明不受引用拦截", edited["description"] == "只改说明", str(edited))
        free_pool = api.data("POST", "/api/v1/data-pools", {
            "projectId": pid, "name": "未引用池", "content": "x",
        })
        renamed = api.data("PUT", f"/api/v1/data-pools/{free_pool['id']}", {"name": "未引用池2"})
        check("未被引用可改名", renamed["name"] == "未引用池2", str(renamed))
        deleted = api.data("DELETE", f"/api/v1/data-pools/{free_pool['id']}")
        check("未被引用可删除", deleted["deleted"] is True, str(deleted))

        # ---------------- 自动转缺陷（默认关闭 → 开启） ----------------
        bad_scn = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "必败场景", "envId": env_a["id"],
            "steps": [{"name": "断言必败", "extra": {
                "接口URL": "/health", "请求方式": "GET", "断言规则": "json:$.code = 999"}}],
        })
        run_off = api.data("POST", f"/api/v1/api-scenarios/{bad_scn['id']}/run", {})
        check("默认关闭时不自动建缺陷", run_off["passed"] is False and not run_off.get("autoBugId"),
              str(run_off.get("autoBugId")))
        api.data("PUT", "/api/v1/settings/config", {"items": [
            {"key": "automation.auto_bug", "value": "true", "valueType": "boolean"},
        ]})
        run_on = api.data("POST", f"/api/v1/api-scenarios/{bad_scn['id']}/run", {})
        check("开启后场景失败自动建缺陷", bool(run_on.get("autoBugId")), str(run_on))
        bugs = api.data("GET", f"/api/v1/bugs?projectId={pid}&pageSize=50")
        auto_bug = [b for b in bugs["list"] if str(b.get("reporter") or "").startswith("自动转缺陷")]
        check("自动缺陷落库且带证据描述", len(auto_bug) == 1
              and "断言必败" in str(auto_bug[0].get("description") or ""), str(auto_bug)[:200])

        with sqlite3.connect(db_path) as conn:
            conn.row_factory = sqlite3.Row
            script = {"id": 1, "project_id": pid, "name": "必败脚本", "script_no": "AUTO-1"}
            run_row = {"run_no": "RUN-AUTO-1", "status": "failed", "exit_code": 1}
            case_results = [{"caseNo": "CASE-1", "result": "failed", "message": "断言未通过",
                             "caseId": case["id"]}]
            first = auto_bug_service.from_automation(conn, script, run_row, case_results, "退出码 1")
            second = auto_bug_service.from_automation(conn, script, run_row, case_results, "退出码 1")
            linked = conn.execute(
                "SELECT COUNT(*) FROM biz_case_bug_rel WHERE bug_id = ?",
                (first,),
            ).fetchone()[0] if first else 0
        check("自动化失败自动建缺陷", bool(first), str(first))
        check("同一次执行只建一条", second is None, str(second))
        check("自动缺陷关联失败用例", linked >= 1, str(linked))

        # ---------------- 环境差异归因 ----------------
        case2 = api.data("POST", "/api/v1/test-cases", {
            "projectId": pid, "title": "无基线用例", "caseType": "functional",
        })
        run_a = api.data("POST", "/api/v1/test-runs", {
            "projectId": pid, "name": "环境A轮次", "envId": env_a["id"],
            "caseIds": [case["id"], case2["id"]],
        })
        run_b = api.data("POST", "/api/v1/test-runs", {
            "projectId": pid, "name": "环境B轮次", "envId": env_b["id"],
            "caseIds": [case["id"]],
        })

        def run_case_id(run_id: int, case_id: int) -> int:
            detail = api.data("GET", f"/api/v1/test-runs/{run_id}")
            for item in detail["cases"]:
                if int(item["caseId"]) == int(case_id):
                    return int(item["id"])
            raise AssertionError(f"轮次 {run_id} 里没有用例 {case_id} 的快照")

        api.data("PUT", f"/api/v1/test-runs/{run_a['id']}/cases/{run_case_id(run_a['id'], case['id'])}",
                 {"result": "failed", "actualResult": "返回码不对"})
        api.data("PUT", f"/api/v1/test-runs/{run_a['id']}/cases/{run_case_id(run_a['id'], case2['id'])}",
                 {"result": "failed"})
        api.data("PUT", f"/api/v1/test-runs/{run_b['id']}/cases/{run_case_id(run_b['id'], case['id'])}",
                 {"result": "passed"})
        api.data("POST", f"/api/v1/test-runs/{run_b['id']}/complete", {})
        diff = api.data("GET", f"/api/v1/test-runs/{run_a['id']}/env-diff")
        check("跨环境同用例标出环境敏感", diff["summary"]["envSensitive"] == 1,
              str(diff["summary"]))
        row = [item for item in diff["rows"] if item["caseNo"] == case["caseNo"]][0]
        check("环境敏感行带其他环境基线", row["status"] == "env_sensitive"
              and any(b["envName"] == "环境B" and b["result"] == "passed" for b in row["baselines"]),
              str(row))
        no_base = [item for item in diff["rows"] if item["caseNo"] == case2["caseNo"]]
        check("其他环境没跑过标无基线", bool(no_base) and no_base[0]["status"] == "no_baseline",
              str(no_base))

        run_no_env = api.data("POST", "/api/v1/test-runs", {
            "projectId": pid, "name": "无环境轮次", "caseIds": [case["id"]],
        })
        diff3 = api.data("GET", f"/api/v1/test-runs/{run_no_env['id']}/env-diff")
        check("未绑定环境给出提示", "未绑定执行环境" in str(diff3["message"]), str(diff3["message"]))
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
