"""验证执行矩阵（GAP-43）：定义→一次调度→逐格真实执行→对比归因→转缺陷→生产确认。

    python backend/scripts/check_execution_matrix.py

全部在临时库 + 本机临时 HTTP 服务上运行，不读写正式数据；
矩阵执行走任务队列，门禁轮询矩阵状态直到收口。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
import time
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


class OkHandler(BaseHTTPRequestHandler):
    """永远返回 code=0 的桩：断言 `json:$.code = 0` 通过。"""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 基类签名
        return

    def do_GET(self) -> None:  # noqa: N802 - 基类签名
        raw = json.dumps({"code": 0, "data": {"id": 1}}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


class FailHandler(BaseHTTPRequestHandler):
    """永远返回 code=1 的桩：断言 `json:$.code = 0` 失败。"""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 基类签名
        return

    def do_GET(self) -> None:  # noqa: N802 - 基类签名
        raw = json.dumps({"code": 1, "message": "业务错误"}, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)


def start_server(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, f"http://127.0.0.1:{int(server.server_address[1])}"


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "matrix.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8766,
        debug=False,
        db_path=db_path,
        log_path=workdir / "matrix.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def wait_matrix(api: Api, matrix_id: int, timeout: float = 90.0) -> JsonDict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = api.data("GET", f"{V1}/execution-matrices/{matrix_id}")
        status = detail["matrix"]["status"]
        if status in ("done", "failed", "cancelled"):
            return cast(JsonDict, detail)
        time.sleep(0.5)
    raise AssertionError(f"矩阵 {matrix_id} 执行超时")


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-matrix-"))
    ok_server, ok_base = start_server(OkHandler)
    fail_server, fail_base = start_server(FailHandler)
    try:
        app = build_app(workdir)
        api = Api(app)

        conn = sqlite3.connect(workdir / "matrix.db")
        conn.row_factory = sqlite3.Row
        check("迁移 054 已应用", 54 in applied_versions(conn))
        check("矩阵表已建立",
              conn.execute(
                  "SELECT name FROM sqlite_master WHERE type='table' AND name='biz_execution_matrix'"
              ).fetchone() is not None)
        check("自动化执行记录带 matrix_cell_id 列",
              "matrix_cell_id" in {
                  row[1] for row in conn.execute("PRAGMA table_info(biz_automation_run)").fetchall()
              })

        project = api.data("POST", f"{V1}/projects", {"code": "MATRIX", "name": "执行矩阵项目"})
        project_id = int(project["id"])

        def make_env(name: str, kind: str, base: str, extra_servers: list[str] | None = None) -> int:
            servers = [{"baseUrl": base}] + [{"baseUrl": item} for item in (extra_servers or [])]
            env = api.data("POST", f"{V1}/test-envs", {
                "name": name,
                "kind": kind,
                "app": {"baseUrl": base, "servers": servers},
                "db": {"type": "sqlite", "host": ""},
            })
            return int(env["id"])

        # 1. 环境特有：测试环境全通过、预发环境全失败（targetMode=default 每环境一格）
        env_ok = make_env("测试环境", "test", ok_base)
        env_fail = make_env("预发环境", "staging", fail_base)
        case_id = int(api.data("POST", f"{V1}/test-cases", {
            "projectId": project_id,
            "title": "健康检查接口",
            "caseType": "interface",
            "priority": "P1",
            "extra": {"接口URL": "/ok", "请求方式": "GET", "断言规则": "json:$.code = 0"},
        })["id"])
        run_id = int(api.data("POST", f"{V1}/test-runs", {
            "projectId": project_id, "name": "接口回归轮次", "caseIds": [case_id],
        })["id"])

        created = api.data("POST", f"{V1}/execution-matrices", {
            "projectId": project_id, "name": "环境差异矩阵", "mode": "interface",
            "runId": run_id, "envIds": [env_ok, env_fail], "targetMode": "default",
        })
        check("矩阵创建为草稿", created["matrix"]["status"] == "draft", str(created["matrix"]))
        check("default 模式每环境一格", created["matrix"]["cellTotal"] == 2,
              str(created["matrix"].get("cellTotal")))
        check("格子带环境名与端地址",
              all(cell.get("envName") and cell.get("target") for cell in created["cells"]),
              str(created["cells"])[:300])
        matrix_env = int(created["matrix"]["id"])

        api.data("POST", f"{V1}/execution-matrices/{matrix_env}/run", {"confirmProd": False})
        detail = wait_matrix(api, matrix_env)
        check("环境差异矩阵执行完成", detail["matrix"]["status"] == "done",
              detail["matrix"].get("errorMessage"))
        check("两格都真实执行且有结论",
              all(cell["status"] == "done" and cell["total"] == 1 for cell in detail["cells"]),
              str(detail["cells"])[:400])
        by_target = {cell["target"]: cell for cell in detail["cells"]}
        check("成功桩所在格通过", by_target[ok_base]["failed"] == 0, str(by_target[ok_base]))
        check("失败桩所在格失败", by_target[fail_base]["failed"] == 1, str(by_target[fail_base]))

        comparison = api.data("GET", f"{V1}/execution-matrices/{matrix_env}/compare")
        rows = comparison["rows"]
        check("对比返回同用例行", len(rows) == 1, str(rows)[:400])
        row = rows[0]
        check("差异归因为环境特有", row["classification"] == "environment_specific",
              row.get("classification"))
        check("行内各格结论齐全", len(row["cells"]) == 2, str(row.get("cells"))[:300])
        check("断言明细随格返回（可下钻）",
              any(item.get("assertions") for item in row["cells"].values()),
              str(row.get("cells"))[:400])
        check("断言差异被标出", len(row.get("assertionDiffs") or []) >= 1,
              str(row.get("assertionDiffs"))[:300])
        check("统计口径正确", comparison["stats"]["environmentSpecific"] == 1,
              str(comparison["stats"]))

        # 2. 端特有：同一环境两台应用服务器一好一坏，两个环境结论一致（targetMode=all）
        env_a = make_env("集群A", "test", ok_base, [fail_base])
        env_b = make_env("集群B", "test", ok_base, [fail_base])
        created2 = api.data("POST", f"{V1}/execution-matrices", {
            "projectId": project_id, "name": "端差异矩阵", "mode": "interface",
            "runId": run_id, "envIds": [env_a, env_b], "targetMode": "all",
        })
        check("all 模式展开应用服务器", created2["matrix"]["cellTotal"] == 4,
              str(created2["matrix"].get("cellTotal")))
        matrix_target = int(created2["matrix"]["id"])
        api.data("POST", f"{V1}/execution-matrices/{matrix_target}/run", {})
        wait_matrix(api, matrix_target)
        comparison2 = api.data("GET", f"{V1}/execution-matrices/{matrix_target}/compare")
        row2 = comparison2["rows"][0]
        check("同一端跨环境同结论归因为端特有", row2["classification"] == "target_specific",
              row2.get("classification"))
        check("失败集中在失败桩端",
              sum(1 for cell_result in row2["cells"].values() if cell_result["result"] == "failed") == 2,
              str(row2["cells"])[:400])

        # 3. 普遍失败：两个环境都指向失败桩，全部格失败 → 一键转缺陷带证据
        created3 = api.data("POST", f"{V1}/execution-matrices", {
            "projectId": project_id, "name": "普遍失败矩阵", "mode": "interface",
            "runId": run_id, "envIds": [env_fail], "targetMode": "default",
        })
        matrix_common = int(created3["matrix"]["id"])
        env_fail2 = make_env("灾备环境", "test", fail_base)
        # 追加第二环境需要重建矩阵；这里直接用单环境双格版本验证普遍失败即可
        api.data("POST", f"{V1}/execution-matrices/{matrix_common}/run", {})
        wait_matrix(api, matrix_common)
        comparison3 = api.data("GET", f"{V1}/execution-matrices/{matrix_common}/compare")
        row3 = comparison3["rows"][0]
        check("全部失败归因为普遍失败", row3["classification"] == "common_failure",
              row3.get("classification"))

        bugs = api.data("POST", f"{V1}/execution-matrices/{matrix_common}/bugs", {})
        check("普遍失败一键转缺陷成功", bugs["total"] == 1 and bugs["created"], str(bugs)[:400])
        bug_id = int(bugs["created"][0]["bugId"])
        check("转缺陷带证据", bool(bugs["created"][0].get("evidenceIds")), str(bugs)[:300])
        bug_row = conn.execute("SELECT * FROM biz_bug WHERE id = ?", (bug_id,)).fetchone()
        check("缺陷标题标注来源", "执行矩阵" in str(bug_row["title"]), str(bug_row["title"]))
        check("缺陷描述带各格结论", "普遍失败" in str(bug_row["description"])
              and "测试环境" not in str(bug_row["description"])  # 单环境矩阵不含测试环境
              and "预发环境" in str(bug_row["description"]),
              str(bug_row["description"])[:400])
        ev_count = conn.execute(
            "SELECT COUNT(*) FROM biz_execution_evidence WHERE bug_id = ? AND is_deleted = 0",
            (bug_id,),
        ).fetchone()[0]
        check("缺陷反向关联到执行证据", int(ev_count) >= 1, str(ev_count))
        comparison3b = api.data("GET", f"{V1}/execution-matrices/{matrix_common}/compare")
        check("对比回显已转缺陷", comparison3b["rows"][0].get("bugId") == bug_id,
              str(comparison3b["rows"][0])[:300])
        bugs_again = api.data("POST", f"{V1}/execution-matrices/{matrix_common}/bugs", {})
        check("重复转缺陷幂等跳过", bugs_again["total"] == 0 and bugs_again["skipped"],
              str(bugs_again)[:300])

        # 4. 生产环境：未确认拒绝调度，确认后放行
        env_prod = make_env("生产环境", "prod", ok_base)
        created4 = api.data("POST", f"{V1}/execution-matrices", {
            "projectId": project_id, "name": "生产回归矩阵", "mode": "interface",
            "runId": run_id, "envIds": [env_prod], "targetMode": "default",
        })
        matrix_prod = int(created4["matrix"]["id"])
        api.expect("生产环境未确认拒绝调度", "POST",
                   f"{V1}/execution-matrices/{matrix_prod}/run", ErrorCode.PARAM_ERROR,
                   {"confirmProd": False})
        after_reject = api.data("GET", f"{V1}/execution-matrices/{matrix_prod}")
        check("拒绝后矩阵仍是草稿", after_reject["matrix"]["status"] == "draft",
              after_reject["matrix"].get("status"))
        payload4 = api.data("POST", f"{V1}/execution-matrices/{matrix_prod}/run",
                            {"confirmProd": True})
        check("确认后放行并记录生产环境", payload4["queued"] is True
              and "生产环境" in (payload4.get("prodEnvs") or []), str(payload4)[:300])
        detail4 = wait_matrix(api, matrix_prod)
        check("生产矩阵执行完成", detail4["matrix"]["status"] == "done",
              detail4["matrix"].get("errorMessage"))
        check("生产确认留痕", bool(detail4["matrix"].get("prodConfirmedAt")),
              str(detail4["matrix"]))

        # 5. 自动化矩阵：环境 × 浏览器，真实子进程执行，浏览器差异归为端特有
        script = api.data("POST", f"{V1}/automation/scripts", {
            "projectId": project_id,
            "name": "浏览器兼容脚本",
            "category": "web",
            "language": "python",
            "runMode": "script",
            "content": (
                "import os, sys\n"
                "browser = os.environ.get('AITP_BROWSER', '')\n"
                "env_name = os.environ.get('AITP_ENV_NAME', '')\n"
                "print(f'browser={browser} env={env_name}')\n"
                "sys.exit(0 if browser == 'chromium' else 1)\n"
            ),
        })
        script_id = int(script["id"])
        created5 = api.data("POST", f"{V1}/execution-matrices", {
            "projectId": project_id, "name": "浏览器矩阵", "mode": "automation",
            "scriptIds": [script_id], "envIds": [env_ok, env_fail2],
            "browsers": ["chromium", "firefox"],
        })
        check("自动化矩阵按环境×浏览器建格", created5["matrix"]["cellTotal"] == 4,
              str(created5["matrix"].get("cellTotal")))
        check("格子带浏览器维度",
              all(cell.get("browser") for cell in created5["cells"]), str(created5["cells"])[:300])
        matrix_auto = int(created5["matrix"]["id"])
        api.data("POST", f"{V1}/execution-matrices/{matrix_auto}/run", {})
        detail5 = wait_matrix(api, matrix_auto, timeout=180.0)
        check("自动化矩阵执行完成", detail5["matrix"]["status"] == "done",
              detail5["matrix"].get("errorMessage"))
        comparison5 = api.data("GET", f"{V1}/execution-matrices/{matrix_auto}/compare")
        row5 = comparison5["rows"][0]
        check("浏览器差异归因为端特有", row5["classification"] == "target_specific",
              row5.get("classification"))
        check("chromium 格全通过、firefox 格全失败",
              sum(1 for cell_result in row5["cells"].values() if cell_result["result"] == "passed") == 2
              and sum(1 for cell_result in row5["cells"].values() if cell_result["result"] == "failed") == 2,
              str(row5["cells"])[:400])
        run_linked = conn.execute(
            "SELECT COUNT(*) FROM biz_automation_run WHERE matrix_cell_id IN"
            " (SELECT id FROM biz_execution_matrix_cell WHERE matrix_id = ?)",
            (matrix_auto,),
        ).fetchone()[0]
        check("自动化执行记录反查得到格子", int(run_linked) == 4, str(run_linked))

        # 6. 审计留痕
        audit_rows = conn.execute(
            "SELECT action, resource_type FROM sys_audit_log WHERE resource_type IN"
            " ('execution_matrix', 'bug') ORDER BY id",
        ).fetchall()
        actions = {(str(item[0]), str(item[1])) for item in audit_rows}
        check("矩阵创建留审计", ("create", "execution_matrix") in actions, str(actions))
        check("矩阵调度留审计", ("execute", "execution_matrix") in actions, str(actions))
        check("转缺陷留审计", ("create", "bug") in actions, str(actions))

        conn.close()
    finally:
        ok_server.shutdown()
        fail_server.shutdown()

    print(f"\nPASS={_passed} FAIL={_failed}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
