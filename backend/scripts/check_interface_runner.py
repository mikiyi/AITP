"""验证接口用例执行引擎：请求组装、断言、超时、同源限制、轮次回写、批量队列。

    python backend/scripts/check_interface_runner.py

全部在临时库 + 本机临时 HTTP 服务上运行，不读写正式数据。
"""

from __future__ import annotations

import json
import pathlib
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
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
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


class Handler(BaseHTTPRequestHandler):
    """本机测试服务：成功、回显、失败、超时、跨源重定向。"""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 基类签名
        return

    def _send(self, status: int, payload: JsonDict, headers: dict[str, str] | None = None) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        for key, value in (headers or {}).items():
            self.send_header(key, value)
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - 基类签名
        if self.path.startswith("/ok"):
            self._send(200, {"code": 0, "data": {"id": 1, "name": "alpha"}}, {"X-Trace": "ok"})
            return
        if self.path.startswith("/fail"):
            self._send(500, {"code": 500, "message": "boom"})
            return
        if self.path.startswith("/slow"):
            import time

            time.sleep(3)
            self._send(200, {"code": 0, "message": "late"})
            return
        if self.path.startswith("/redirect"):
            address = cast(tuple[Any, ...], self.server.server_address)
            port = int(address[1])
            target = f"http://127.0.0.1:{port + 1}/ok"
            self.send_response(302)
            self.send_header("Location", target)
            self.end_headers()
            return
        self._send(404, {"code": 404})

    def do_POST(self) -> None:  # noqa: N802 - 基类签名
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            parsed = {}
        self._send(200, {
            "code": 0,
            "method": "POST",
            "received": parsed,
            "auth": self.headers.get("Authorization", ""),
            "xTest": self.headers.get("X-Test", ""),
        })


def start_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, int(server.server_address[1])


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "interface.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8765,
        debug=False,
        db_path=db_path,
        log_path=workdir / "interface.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def make_case(api: Api, project_id: int, title: str, extra: JsonDict) -> int:
    case = api.data("POST", "/api/v1/test-cases", {
        "projectId": project_id,
        "title": title,
        "caseType": "interface",
        "priority": "P1",
        "extra": extra,
    })
    return int(case["id"])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-iface-"))
    server, port = start_server()
    try:
        app = build_app(workdir)
        api = Api(app)
        base = f"http://127.0.0.1:{port}"

        conn = sqlite3.connect(workdir / "interface.db")
        conn.row_factory = sqlite3.Row
        versions = applied_versions(conn)
        check("迁移 026 已应用", 26 in versions)
        check("接口执行表已建立",
              conn.execute(
                  "SELECT name FROM sqlite_master WHERE type='table' AND name='biz_interface_execution'"
              ).fetchone() is not None)

        env = api.data("POST", "/api/v1/test-envs", {
            "name": "接口执行测试环境",
            "kind": "test",
            "app": {"baseUrl": base},
            "db": {"type": "sqlite", "host": ""},
        })
        env_id = int(env["id"])
        project = api.data("POST", "/api/v1/projects", {"code": "IFACE", "name": "接口执行项目"})
        project_id = int(project["id"])

        # 1. GET + 状态码 + JSONPath + 响应头断言
        get_case = make_case(api, project_id, "查询详情", {
            "接口URL": "/ok",
            "请求方式": "GET",
            "预期状态码": "200",
            "断言规则": "json:$.code = 0\njson:$.data.name = alpha\nheader:X-Trace contains ok",
        })
        detail = api.data("POST", f"/api/v1/test-cases/{get_case}/interface-run", {"envId": env_id})
        check("GET 用例通过", detail["result"] == "passed", str(detail)[:300])
        check("状态码记录正确", detail["statusCode"] == 200)
        check("断言全部通过",
              len(detail["assertions"]) >= 4 and all(item["passed"] for item in detail["assertions"]),
              str(detail["assertions"])[:300])
        check("执行流水已落库", isinstance(detail.get("executionId"), int))
        case_after = api.data("GET", f"/api/v1/test-cases/{get_case}")
        check("用例全局状态回写", case_after["status"] == "pass", str(case_after.get("status")))

        # 2. POST + 请求体 + 鉴权 token
        post_case = make_case(api, project_id, "提交订单", {
            "接口URL": "{{baseUrl}}/echo",
            "请求方式": "POST",
            "token/鉴权方式": "Bearer abc123",
            "请求头": '{"X-Test": "{{token}}"}',
            "请求体": '{"name": "beta", "count": 2}',
            "预期状态码": "200",
            "预期响应体": '{"code": 0, "received": {"name": "beta"}}',
            "断言规则": "json:$.auth = Bearer abc123; json:$.xTest = abc123; "
                        "json:$.received.count = 2; body contains POST",
        })
        post_result = api.data("POST", f"/api/v1/test-cases/{post_case}/interface-run",
                               {"envId": env_id})
        check("POST 用例通过", post_result["result"] == "passed", str(post_result)[:400])
        check("Authorization 不回显明文", "***" in str(post_result["request"]["headers"]),
              str(post_result["request"]["headers"]))
        check("请求头占位符已替换",
              any(item["name"] == "JSONPath $.xTest" and item["passed"]
                  for item in post_result["assertions"]),
              str(post_result["assertions"])[:400])

        # 3. 失败断言与响应片段
        fail_case = make_case(api, project_id, "服务异常", {
            "接口URL": "/fail",
            "请求方式": "GET",
            "预期状态码": "200",
        })
        fail_result = api.data("POST", f"/api/v1/test-cases/{fail_case}/interface-run",
                               {"envId": env_id})
        check("失败用例判定失败", fail_result["result"] == "failed")
        check("失败记录实际状态码", fail_result["statusCode"] == 500)
        check("失败断言给出原因",
              any("期望 200" in item["message"] for item in fail_result["assertions"]),
              str(fail_result["assertions"])[:300])

        # 4. 超时按失败收口
        slow_case = make_case(api, project_id, "慢接口", {
            "接口URL": "/slow",
            "请求方式": "GET",
            "预期状态码": "200",
        })
        slow_result = api.data("POST", f"/api/v1/test-cases/{slow_case}/interface-run",
                               {"envId": env_id, "timeoutSeconds": 1})
        check("超时判定失败", slow_result["result"] == "failed", str(slow_result)[:200])
        check("超时给出可读原因", "超时" in str(slow_result["message"]),
              str(slow_result.get("message")))
        check("超时耗时接近设定值",
              900 <= int(slow_result["durationMs"]) <= 2500,
              str(slow_result.get("durationMs")))

        # 5. 非同源地址被拒绝
        foreign_case = make_case(api, project_id, "跨环境地址", {
            "接口URL": "http://127.0.0.1:1/ok",
            "请求方式": "GET",
        })
        foreign = api.expect("非同源地址被拒绝", "POST",
                             f"/api/v1/test-cases/{foreign_case}/interface-run",
                             ErrorCode.ILLEGAL_OPERATION, {"envId": env_id})
        check("非同源原因可读", "不同源" in str(foreign.get("message")), str(foreign.get("message")))

        # 6. 跨源重定向被拒绝
        redirect_case = make_case(api, project_id, "跳转外站", {
            "接口URL": "/redirect",
            "请求方式": "GET",
            "预期状态码": "200",
        })
        redirect_result = api.data("POST", f"/api/v1/test-cases/{redirect_case}/interface-run",
                                   {"envId": env_id})
        check("跨源重定向被拒绝", redirect_result["result"] == "failed", str(redirect_result)[:300])
        check("重定向原因可读", "不同源" in str(redirect_result["message"]),
              str(redirect_result.get("message")))

        # 7. 轮次快照 + 单条执行回写
        functional = api.data("POST", "/api/v1/test-cases", {
            "projectId": project_id, "title": "功能用例", "priority": "P2",
        })
        run = api.data("POST", "/api/v1/test-runs", {
            "projectId": project_id,
            "name": "接口执行轮次",
            "envId": env_id,
            "caseIds": [get_case, fail_case, int(functional["id"])],
        })
        run_id = int(run["id"])
        run_detail = api.data("GET", f"/api/v1/test-runs/{run_id}")
        run_cases = run_detail["cases"]
        get_row = next(item for item in run_cases if int(item["caseId"]) == get_case)
        check("轮次快照保留接口定义",
              get_row.get("extra", {}).get("接口URL") == "/ok", str(get_row.get("extra"))[:200])
        row_result = api.data(
            "POST",
            f"/api/v1/test-runs/{run_id}/cases/{int(get_row['id'])}/interface-run",
            {},
        )
        check("轮次单条执行通过", row_result["result"] == "passed", str(row_result)[:300])
        check("轮次结果已回写", row_result["runCase"]["result"] == "passed",
              str(row_result["runCase"].get("result")))
        check("实际结果带状态码与耗时",
              "HTTP 200" in str(row_result["runCase"].get("actualResult")),
              str(row_result["runCase"].get("actualResult"))[:200])

        # 8. 轮次批量同步执行：接口用例执行，功能用例保持未执行
        batch = api.data("POST", f"/api/v1/test-runs/{run_id}/interface-run",
                         {"envId": env_id, "sync": True, "timeoutSeconds": 3})
        check("批量只执行接口用例", batch["total"] == 2, str(batch)[:200])
        check("批量统计通过/失败", batch["passed"] + batch["failed"] == 2, str(batch)[:200])
        run_after = api.data("GET", f"/api/v1/test-runs/{run_id}")
        functional_row = next(
            item for item in run_after["cases"] if int(item["caseId"]) == int(functional["id"])
        )
        check("功能用例不被误执行", functional_row["result"] == "not_executed",
              str(functional_row.get("result")))

        # 9. 异步批量走任务队列
        queued = api.data("POST", f"/api/v1/test-runs/{run_id}/interface-run",
                          {"envId": env_id, "timeoutSeconds": 3})
        check("批量默认投递任务队列", queued.get("queued") is True, str(queued)[:200])
        task = queued.get("task") or {}
        check("任务类型为接口批量执行", task.get("kind") == "interface.batch_run",
              str(task.get("kind")))
        check("任务类型有中文名", task.get("kindLabel") == "接口用例批量执行",
              str(task.get("kindLabel")))

        # 10. 非接口用例不能自动执行
        api.expect("功能用例拒绝接口执行", "POST",
                   f"/api/v1/test-cases/{int(functional['id'])}/interface-run",
                   ErrorCode.ILLEGAL_OPERATION, {"envId": env_id})
        api.expect("缺少环境时给出可读提示", "POST",
                   f"/api/v1/test-cases/{get_case}/interface-run",
                   ErrorCode.PARAM_ERROR, {"envId": 999999})

        server.shutdown()
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001 - 清理失败不影响结果
            pass
    print(f"\nPASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
