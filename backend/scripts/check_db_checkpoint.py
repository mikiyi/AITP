"""数据库查询检查点（方案 A）验收门禁。

覆盖：
- 只读闸门：SELECT/WITH/SHOW/DESCRIBE/EXPLAIN 放行；写操作/多语句/INTO 拒绝
- Redis 只读命令白名单、MongoDB 查询 JSON 解析
- SQLite 真执行：接口 db:/db:rows:/db:json: 断言、{{var}} 参数化、
  行数上限、失败断言、写语句拒绝且数据无损
- 场景 database 步骤：结果 JSON 断言、变量提取传递、无环境/写语句失败
- 证据 log_summary 留查询语句与结果；不支持类型/缺驱动给明确提示

    python backend/scripts/check_db_checkpoint.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。全程隔离临时库，不碰正式服务。
"""

from __future__ import annotations

import importlib.util
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
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.services import db_checkpoint_service as dbs  # noqa: E402
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


def expect_reject(name: str, statement: str) -> None:
    try:
        dbs.ensure_readonly_sql(statement)
    except AppError:
        check(name, True)
        return
    check(name, False, f"未拒绝：{statement}")


class Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802
        raw = json.dumps({"code": 0, "data": {"id": 1}}).encode("utf-8")
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
    db_path = workdir / "check_db_checkpoint.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "check.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def make_fixture(workdir: pathlib.Path) -> pathlib.Path:
    path = workdir / "fixture.db"
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE orders (id INTEGER PRIMARY KEY, status TEXT, amount REAL)")
    conn.executemany("INSERT INTO orders VALUES (?, ?, ?)",
                     [(1, "paid", 10.0), (2, "pending", 20.0), (3, "paid", 30.0)])
    conn.execute("CREATE TABLE nums (n INTEGER)")
    conn.executemany("INSERT INTO nums VALUES (?)", [(i,) for i in range(250)])
    conn.commit()
    conn.close()
    return path


def unit_checks() -> None:
    for stmt in ("SELECT 1", "WITH x AS (SELECT 1) SELECT * FROM x",
                 "SHOW TABLES", "DESCRIBE orders", "EXPLAIN SELECT * FROM orders",
                 "SELECT 'delete' FROM orders", "SELECT 1;"):
        try:
            dbs.ensure_readonly_sql(stmt)
            check(f"只读放行：{stmt[:40]}", True)
        except AppError as exc:
            check(f"只读放行：{stmt[:40]}", False, str(exc))
    expect_reject("拒绝 UPDATE", "UPDATE orders SET status='x'")
    expect_reject("拒绝 DELETE", "DELETE FROM orders")
    expect_reject("拒绝 INSERT", "INSERT INTO orders VALUES (9,'x',1)")
    expect_reject("拒绝 DROP", "DROP TABLE orders")
    expect_reject("拒绝多语句", "SELECT 1; SELECT 2")
    expect_reject("拒绝 SELECT INTO", "SELECT * INTO backup FROM orders")
    expect_reject("拒绝 WITH 后接 INSERT", "WITH x AS (SELECT 1) INSERT INTO orders SELECT * FROM x")
    expect_reject("拒绝 EXPLAIN 包写语句", "EXPLAIN ANALYZE INSERT INTO orders VALUES (9,'x',1)")
    expect_reject("拒绝 PRAGMA", "PRAGMA writable_schema=ON")
    expect_reject("拒绝注释夹带写语句", "SELECT 1; /* c */ DELETE FROM orders")

    check("Redis 只读命令放行", dbs.parse_redis_command("HGETALL order:1001")[0] == "HGETALL")
    for cmd in ("SET k v", "DEL k", "FLUSHALL", "CONFIG GET maxmemory"):
        try:
            dbs.parse_redis_command(cmd)
            check(f"Redis 拒绝写/管理命令：{cmd}", False)
        except AppError:
            check(f"Redis 拒绝写/管理命令：{cmd}", True)

    spec = dbs.parse_mongo_statement('{"collection":"orders","filter":{"status":"paid"}}')
    check("Mongo 查询解析", spec["collection"] == "orders")
    for bad in ("不是JSON", '{"filter":{}}', '{"collection":"c","aggregate":"x"}'):
        try:
            dbs.parse_mongo_statement(bad)
            check(f"Mongo 非法语句拒绝：{bad[:30]}", False)
        except AppError:
            check(f"Mongo 非法语句拒绝：{bad[:30]}", True)


def main() -> int:
    unit_checks()
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_dbckpt_"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    fixture = make_fixture(workdir)
    try:
        app = build_app(workdir)
        api = Api(app)
        project = api.data("POST", "/api/v1/projects", {"name": "DB检查点门禁", "code": "GATE-DB"})
        pid = int(project["id"])
        env = api.data("POST", "/api/v1/test-envs", {
            "projectId": pid, "name": "SQLite环境", "kind": "test",
            "app": {"baseUrl": base},
            "db": {"type": "sqlite", "name": str(fixture)},
            "variables": {"oid": "2"},
        })
        eid = int(env["id"])

        def make_case(title: str, rules: str) -> int:
            case = api.data("POST", "/api/v1/test-cases", {
                "projectId": pid, "title": title, "caseType": "interface",
                "priority": "P1",
                "extra": {"接口URL": "/ok", "请求方式": "GET", "断言规则": rules},
            })
            return int(case["id"])

        def run_case(case_id: int) -> JsonDict:
            return cast(JsonDict, api.data(
                "POST", f"/api/v1/test-cases/{case_id}/interface-run", {"envId": eid}))

        # 1. 接口 db: 断言全绿
        good = make_case("DB检查点全绿", "\n".join([
            "db:SELECT count(*) FROM orders = 3",
            "db:SELECT status FROM orders WHERE id = 1 = paid",
            "db:rows:SELECT * FROM orders >= 3",
            "db:json:SELECT status FROM orders WHERE id = 1 contains paid",
            "db:SELECT status FROM orders WHERE id = 999 not_exists",
            "db:SELECT amount FROM orders WHERE id = 2 >= 20",
        ]))
        detail = run_case(good)
        check("接口 db: 断言全部通过", detail["result"] == "passed", str(detail)[:400])
        check("dbQueries 留痕 6 条", len(detail.get("dbQueries") or []) == 6,
              str(len(detail.get("dbQueries") or [])))
        conn = sqlite3.connect(workdir / "check_db_checkpoint.db")
        conn.row_factory = sqlite3.Row
        ev = conn.execute("SELECT log_summary FROM biz_execution_evidence WHERE id = ?",
                          (int(detail["evidenceId"]),)).fetchone()
        check("证据日志含查询语句与结果",
              ev is not None and "SELECT count(*) FROM orders" in str(ev["log_summary"])
              and "3 行" in str(ev["log_summary"]),
              str(dict(ev)) if ev else "无证据行")

        # 2. 失败断言
        bad = make_case("DB检查点必败", "db:SELECT count(*) FROM orders = 99")
        detail_bad = run_case(bad)
        check("db 断言失败判失败", detail_bad["result"] == "failed", str(detail_bad)[:200])
        check("失败原因带期望与实际",
              any("99" in str(a.get("message")) and "3" in str(a.get("actual"))
                  for a in detail_bad["assertions"] if not a["passed"]),
              str(detail_bad["assertions"])[:300])

        # 3. 写语句拒绝且数据无损
        evil = make_case("写语句拒绝", "db:DELETE FROM orders WHERE id = 1 = 1")
        detail_evil = run_case(evil)
        check("写语句断言失败", detail_evil["result"] == "failed", str(detail_evil)[:200])
        check("拒绝原因说明只读",
              any("只读" in str(a.get("message")) for a in detail_evil["assertions"]),
              str(detail_evil["assertions"])[:300])
        fconn = sqlite3.connect(fixture)
        left = fconn.execute("SELECT count(*) FROM orders").fetchone()[0]
        fconn.close()
        check("拒绝后数据无损", left == 3, str(left))

        # 4. {{var}} 参数化
        var_case = make_case("变量参数化", "db:SELECT status FROM orders WHERE id = {{oid}} = pending")
        check("db 查询支持 {{oid}} 变量", run_case(var_case)["result"] == "passed")

        # 5. 行数上限
        cap_case = make_case("行数上限", "db:rows:SELECT * FROM nums = 200")
        detail_cap = run_case(cap_case)
        check("行数封顶 200", detail_cap["result"] == "passed", str(detail_cap)[:200])
        check("超限标记 truncated",
              any(q.get("truncated") for q in detail_cap.get("dbQueries") or []),
              str(detail_cap.get("dbQueries"))[:200])

        # 6. 场景 database 步骤：断言 + 变量提取传递
        scn = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "下单查库核对", "envId": eid,
            "steps": [
                {"name": "下单", "type": "interface",
                 "extra": {"接口URL": "/ok", "请求方式": "GET", "断言规则": "json:$.code = 0"}},
                {"name": "查库核对", "type": "database",
                 "statement": "SELECT status FROM orders WHERE id = 1",
                 "extra": {"断言规则": "json:$.rowCount = 1\njson:$.rows[0].status = paid"},
                 "extract": [{"var": "dbstatus", "path": "$.rows[0].status"}]},
                {"name": "回传变量", "type": "interface",
                 "extra": {"接口URL": "/ok?st={{dbstatus}}", "请求方式": "GET"}},
            ],
        })
        run = api.data("POST", f"/api/v1/api-scenarios/{scn['id']}/run", {})
        steps = run["runs"][0]["steps"]
        check("场景 database 步骤通过", run["passed"] is True, str(run)[:400])
        check("DB 步骤请求标记", steps[1]["request"]["method"] == "DB", str(steps[1]["request"]))
        check("查询结果可提取变量", run["runs"][0]["variables"].get("dbstatus") == "paid",
              str(run["runs"][0]["variables"]))
        check("变量传到后续接口步骤", "st=paid" in str(steps[2]["request"]["url"]),
              str(steps[2]["request"]["url"]))
        check("DB 步骤响应为结果 JSON",
              '"rowCount": 1' in str(steps[1]["response"]["body"]),
              str(steps[1]["response"])[:200])

        # 7. 场景 database 步骤失败口径
        scn_bad = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "查库必败", "envId": eid,
            "steps": [{"name": "核对失败", "type": "database",
                       "statement": "SELECT count(*) FROM orders",
                       "extra": {"断言规则": "json:$.scalar = 99"}}],
        })
        run_bad = api.data("POST", f"/api/v1/api-scenarios/{scn_bad['id']}/run", {})
        check("场景 DB 断言失败判失败", run_bad["passed"] is False, str(run_bad)[:200])

        scn_write = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "查库写拒绝", "envId": eid,
            "steps": [{"name": "写语句", "type": "database",
                       "statement": "UPDATE orders SET status = 'x'"}],
        })
        run_write = api.data("POST", f"/api/v1/api-scenarios/{scn_write['id']}/run", {})
        check("场景写语句被拒", run_write["passed"] is False
              and "只读" in str(run_write["runs"][0]["steps"][0]["message"]),
              str(run_write["runs"][0]["steps"][0])[:200])

        scn_noenv = api.data("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "无环境查库",
            "steps": [{"name": "查库", "type": "database", "statement": "SELECT 1"}],
        })
        run_noenv = api.data("POST", f"/api/v1/api-scenarios/{scn_noenv['id']}/run", {})
        check("无环境 DB 步骤明确报错", run_noenv["passed"] is False
              and "执行环境" in str(run_noenv["runs"][0]["steps"][0]["message"]),
              str(run_noenv["runs"][0]["steps"][0])[:200])

        # 8. 保存校验：数据库步骤必须带查询语句
        resp, body = api.call("POST", "/api/v1/api-scenarios", {
            "projectId": pid, "name": "空查询", "envId": eid,
            "steps": [{"name": "空", "type": "database", "statement": ""}],
        })
        check("空查询语句保存被拒", body.get("code") == ErrorCode.PARAM_ERROR, str(body)[:200])

        # 9. 不支持类型 / 缺驱动提示
        env_es = api.data("POST", "/api/v1/test-envs", {
            "projectId": pid, "name": "ES环境", "kind": "test",
            "app": {"baseUrl": base}, "db": {"type": "elasticsearch", "host": "127.0.0.1"},
        })
        es_case = api.data("POST", "/api/v1/test-cases", {
            "projectId": pid, "title": "不支持类型", "caseType": "interface", "priority": "P1",
            "extra": {"接口URL": "/ok", "请求方式": "GET", "断言规则": "db:SELECT 1 = 1"},
        })
        detail_es = api.data("POST", f"/api/v1/test-cases/{es_case['id']}/interface-run",
                             {"envId": int(env_es["id"])})
        check("不支持类型明确提示", detail_es["result"] == "failed"
              and any("暂不支持" in str(a.get("message")) for a in detail_es["assertions"]),
              str(detail_es["assertions"])[:300])

        env_dm = api.data("POST", "/api/v1/test-envs", {
            "projectId": pid, "name": "达梦环境", "kind": "test",
            "app": {"baseUrl": base}, "db": {"type": "dm", "host": "127.0.0.1", "port": 1},
        })
        dm_case = api.data("POST", "/api/v1/test-cases", {
            "projectId": pid, "title": "达梦适配", "caseType": "interface", "priority": "P1",
            "extra": {"接口URL": "/ok", "请求方式": "GET", "断言规则": "db:SELECT 1 = 1"},
        })
        detail_dm = api.data("POST", f"/api/v1/test-cases/{dm_case['id']}/interface-run",
                             {"envId": int(env_dm["id"])})
        dm_msg = " ".join(str(a.get("message")) for a in detail_dm["assertions"])
        if importlib.util.find_spec("dmPython") is None:
            check("缺达梦驱动给安装提示", "pip install dmPython" in dm_msg, dm_msg[:200])
        else:
            check("达梦驱动在装时走真实连接口径", "达梦" in dm_msg, dm_msg[:200])
        conn.close()
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