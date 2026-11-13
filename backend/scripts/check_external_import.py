"""外部平台只读导入门禁（三期 GAP-40，桩禅道服务器）。

覆盖：
  - 平台/内容目录与默认映射；
  - 连接配置：口令只进系统密钥库，库内仅存引用与掩码，接口不回显明文；
  - 预览/导入：登录换 token 后只发 GET；需求/用例/缺陷按映射落库，
    external_platform/external_id/external_url 齐全，priority/severity 由 pri 映射；
  - 去重：同 项目+平台+外部 id 二次导入全部 skipped，不新增行；
  - 只读红线：桩服务器收到的业务请求一律 GET，绝无 PUT/POST/DELETE 回写；
  - 服务器级 HTTP Basic 与应用账号两层认证分开配置；
  - 映射模板 CRUD / 默认唯一 / 重名拒绝 / 自定义映射生效；
  - 批次留痕与项目隔离；审计落 sys_audit_log；
  - 参数校验与删除配置清密钥。

    python backend/scripts/check_external_import.py
"""

from __future__ import annotations

import base64
import json
import pathlib
import re
import sqlite3
import sys
import tempfile
import threading
import xmlrpc.client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.common.secret_store import SecretStore, set_store  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services.external_import.mapping import apply_mapping, get_path  # noqa: E402
from aitp.services.external_import.zentao import default_mapping  # noqa: E402

V1 = "/api/v1"
_passed = 0
_failed = 0
_failures: list[str] = []

ACCOUNT = "admin"
PASSWORD = "Ycr@123456"
SERVER_USER = "runner"
SERVER_PASS = "runner-pass"
TOKEN = "tok-123"

MOCK_DATA: dict[str, list[dict[str, Any]]] = {
    "stories": [
        {"id": 101, "title": "登录需求", "pri": 1, "spec": "支持账号密码登录", "status": "active"},
        {"id": 102, "title": "导出需求", "pri": 3, "story": "导出 CSV", "status": "draft"},
    ],
    "testcases": [
        {
            "id": 201, "title": "登录用例", "pri": 2, "precondition": "已注册",
            "steps": [
                {"content": "输入账号", "expect": "显示账号"},
                {"content": "点击登录", "expect": "进入首页"},
            ],
            "status": "normal",
        },
    ],
    "bugs": [
        {"id": 301, "title": "登录崩溃", "severity": 1, "pri": 1,
         "steps": "重现步骤描述", "status": "active"},
    ],
}


def _adf(*paragraphs: str) -> dict[str, Any]:
    return {
        "type": "doc", "version": 1,
        "content": [
            {"type": "paragraph", "content": [{"type": "text", "text": text}]}
            for text in paragraphs
        ],
    }


MOCK_JIRA: list[dict[str, Any]] = [
    {
        "id": "10001", "key": "DEMO-1",
        "fields": {
            "summary": "Jira 登录需求", "issuetype": {"name": "Story"},
            "priority": {"name": "High"}, "status": {"name": "In Progress"},
            "description": _adf("支持单点登录", "兼容旧账号体系"),
        },
    },
    {
        "id": "10002", "key": "DEMO-2",
        "fields": {
            "summary": "Jira 支付崩溃", "issuetype": {"name": "Bug"},
            "priority": {"name": "Highest"}, "status": {"name": "Open"},
            "description": _adf("支付回调偶发 500"),
        },
    },
    {
        "id": "10003", "key": "DEMO-3",
        "fields": {
            "summary": "Jira 登录测试用例", "issuetype": {"name": "Test"},
            "priority": {"name": "Medium"}, "status": {"name": "Open"},
            "description": _adf("输入账号", "点击登录"),
        },
    },
    {
        "id": "10004", "key": "DEMO-4",
        "fields": {
            "summary": "Jira 普通任务", "issuetype": {"name": "Task"},
            "priority": {"name": "Low"}, "status": {"name": "Open"},
            "description": _adf("不应被导入"),
        },
    },
]

MOCK_TESTLINK_REQS: list[dict[str, Any]] = [
    {"id": "11", "title": "TL 支付需求", "scope": "支持退款", "status": "active", "children": []},
]
MOCK_TESTLINK_CASES: dict[str, Any] = {
    "201": {"testcase_id": "201", "name": "TL 退款用例", "summary": "已支付订单",
            "importance": 2},
}
MOCK_TESTLINK_CASE_DETAIL: dict[str, Any] = {
    "name": "TL 退款用例", "summary": "已支付订单", "importance": 2,
    "steps": [
        {"actions": "进入订单", "expected_results": "显示订单"},
        {"actions": "点击退款", "expected_results": "退款成功"},
    ],
}


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        _failures.append(name)
        print(f"[FAIL] {name}  {detail}")


def note(text: str) -> None:
    print(f"  · {text}")


class _MockHandler(BaseHTTPRequestHandler):
    def log_message(self, *_args: Any) -> None:
        pass

    def _send(self, code: int, obj: Any) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _basic_ok(self) -> bool:
        cfg = self.server.mock_cfg  # type: ignore[attr-defined]
        if not cfg["server_auth"]:
            return True
        expect = "Basic " + base64.b64encode(
            f"{SERVER_USER}:{SERVER_PASS}".encode()).decode()
        return self.headers.get("Authorization", "") == expect

    def _basic_reject(self) -> None:
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="zentao runner authentication required"')
        self.end_headers()

    def _drain(self) -> None:
        # 先读空请求体再回错，否则客户端还在写时关连接会触发 RST
        length = int(self.headers.get("Content-Length", 0) or 0)
        if length:
            self.rfile.read(length)

    def _route(self) -> str:
        # 禅道常挂在子路径（如 /zentao），桩服务器也容忍该前缀
        if self.path.startswith("/zentao/"):
            return self.path[len("/zentao"):]
        return self.path

    def _send_xml(self, payload: str) -> None:
        body = payload.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/xml; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _jira_search(self) -> None:
        expect = "Basic " + base64.b64encode(f"{ACCOUNT}:{PASSWORD}".encode()).decode()
        if self.headers.get("Authorization", "") != expect:
            self._send(401, {"error": "jira auth required"})
            return
        query = parse_qs(urlsplit(self.path).query)
        jql = (query.get("jql") or [""])[0]
        issues = [
            issue for issue in MOCK_JIRA
            if f'"{issue["fields"]["issuetype"]["name"]}"' in jql
        ]
        self._send(200, {"issues": issues, "total": len(issues)})

    def _xmlrpc(self, body: bytes) -> None:
        try:
            values, method = xmlrpc.client.loads(body)
        except (ValueError, xmlrpc.client.ResponseError):
            self._send(400, {"error": "bad xmlrpc"})
            return
        self.server.rpc_methods.append(method)  # type: ignore[attr-defined]
        if method == "tl.getRequirements":
            result: Any = MOCK_TESTLINK_REQS
        elif method == "tl.getTestCasesForTestPlan":
            result = MOCK_TESTLINK_CASES
        elif method == "tl.getTestCase":
            result = [MOCK_TESTLINK_CASE_DETAIL]
        else:
            result = []
        self._send_xml(xmlrpc.client.dumps((result,), methodresponse=True, encoding="utf-8"))

    def do_POST(self) -> None:
        self.server.requests.append(("POST", self.path))  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        if not self._basic_ok():
            self._basic_reject()
            return
        route = self._route()
        if route == "/lib/api/xmlrpc.php":
            self._xmlrpc(body)
            return
        data = json.loads(body or b"{}")
        if route == "/api.php/v1/tokens":
            if data.get("account") == ACCOUNT and data.get("password") == PASSWORD:
                self._send(200, {"token": TOKEN})
            else:
                self._send(401, {"error": "bad credentials"})
        else:
            self._send(404, {"error": "not found"})

    def do_GET(self) -> None:
        self.server.requests.append(("GET", self.path))  # type: ignore[attr-defined]
        if not self._basic_ok():
            self._basic_reject()
            return
        route = self._route()
        if route.startswith("/rest/api/3/search"):
            self._jira_search()
            return
        if self.headers.get("Token", "") != TOKEN:
            self._send(401, {"error": "no token"})
            return
        match = re.match(r"/api.php/v1/products/([^/]+)/(stories|testcases|bugs)", route)
        if not match:
            self._send(404, {"error": "not found"})
            return
        _pid, kind = match.groups()
        self._send(200, {"items": MOCK_DATA[kind]})

    def do_PUT(self) -> None:
        self.server.requests.append(("PUT", self.path))  # type: ignore[attr-defined]
        self._drain()
        self._send(405, {"error": "read only"})

    def do_DELETE(self) -> None:
        self.server.requests.append(("DELETE", self.path))  # type: ignore[attr-defined]
        self._drain()
        self._send(405, {"error": "read only"})


def start_mock() -> tuple[ThreadingHTTPServer, str]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    server.mock_cfg = {"server_auth": False}  # type: ignore[attr-defined]
    server.requests = []  # type: ignore[attr-defined]
    server.rpc_methods = []  # type: ignore[attr-defined]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    address = server.server_address
    host = str(address[0])
    port = int(address[1])
    return server, f"http://{host}:{port}"


def build_app(workdir: pathlib.Path) -> Any:
    db_path = workdir / "external.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "external.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def rows(db_path: pathlib.Path, sql: str, params: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return conn.execute(sql, params).fetchall()
    finally:
        conn.close()


def section_mapping_unit() -> None:
    note("映射语义（get_path / apply_mapping）单元校验")
    item = {"a": {"b": "x"}, "steps": [{"content": "s1"}, {"content": "s2"}], "pri": 1}
    check("get_path 点号路径", get_path(item, "a.b") == "x")
    check("get_path 数组拼接", get_path(item, "steps[].content") == "s1\ns2")
    check("get_path 兜底分支", get_path(item, "missing|a.b") == "x")
    check("get_path 缺失返回 None", get_path(item, "nope") is None)
    mapped = apply_mapping(
        {"title": "T", "pri": 1, "spec": "正文"}, "requirement", default_mapping())
    check("默认映射需求 priority 由 pri→P0", mapped.get("priority") == "P0", str(mapped))
    check("默认映射需求 content 取 spec", mapped.get("content") == "正文", str(mapped))
    case = apply_mapping(
        {"title": "C", "steps": [{"content": "s1", "expect": "e1"}]}, "case", default_mapping())
    check("默认映射用例 steps 拆行", case.get("steps") == [{"step": "s1"}], str(case))
    check("默认映射用例 expectedResult", case.get("expectedResult") == "e1", str(case))


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-extimport-"))
    set_store(SecretStore(workdir))
    server, base_url = start_mock()
    app = build_app(workdir)
    db_path = workdir / "external.db"
    client = app.test_client()

    def call(method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        resp = client.open(path, method=method, **kwargs)
        return resp.status_code, resp.get_json()

    project = client.post(f"{V1}/projects", json={"code": "EXT", "name": "外部导入项目"}).get_json()
    project_id = int(project["data"]["id"])
    other = client.post(f"{V1}/projects", json={"code": "EXT2", "name": "隔离项目"}).get_json()
    other_id = int(other["data"]["id"])

    section_mapping_unit()

    # ---------- 1. 平台目录 ----------
    _, body = call("GET", f"{V1}/external-imports/platforms")
    data = body["data"]
    platforms = {p["key"]: p for p in data["platforms"]}
    check("平台目录含禅道/Jira/TestLink", set(platforms) == {"zentao", "jira", "testlink"})
    check("内容目录含需求/用例/缺陷", {k["key"] for k in data["kinds"]} == {"requirements", "cases", "bugs"})
    check("平台目录带默认映射", "requirement" in data["defaultMapping"])
    check("Jira 支持三类内容", platforms["jira"]["kinds"] == ["requirements", "cases", "bugs"])
    check("TestLink 不支持缺陷", platforms["testlink"]["kinds"] == ["requirements", "cases"])
    check("Jira 默认映射指向 summary",
          platforms["jira"]["defaultMapping"]["requirement"]["title"] == "summary")
    check("TestLink 默认映射指向 name",
          platforms["testlink"]["defaultMapping"]["case"]["title"] == "name")

    # ---------- 2. 连接配置与密钥 ----------
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "platform": "zentao", "baseUrl": base_url,
        "username": ACCOUNT, "password": PASSWORD, "productId": "1",
    })
    check("保存配置成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    cfg = body["data"]
    check("配置回显 hasPassword", cfg.get("hasPassword") is True)
    check("配置不回显明文口令", PASSWORD not in json.dumps(cfg, ensure_ascii=False))
    check("配置带掩码", bool(cfg.get("passwordMasked")))
    cfg_rows = rows(db_path, "SELECT * FROM biz_external_import_config WHERE project_id=?", (project_id,))
    check("配置表只存引用不存明文", len(cfg_rows) == 1 and cfg_rows[0]["auth_ref"]
          and PASSWORD not in json.dumps(dict(cfg_rows[0]), ensure_ascii=False))
    secret_raw = SecretStore(workdir).get(str(cfg_rows[0]["auth_ref"]))
    check("密钥库可读出明文口令", json.loads(secret_raw).get("password") == PASSWORD)

    _, body = call("GET", f"{V1}/external-imports/config", query_string={"projectId": project_id})
    check("读取配置成功", body.get("code") == ErrorCode.SUCCESS and body["data"]["baseUrl"] == base_url)

    # ---------- 3. 预览 ----------
    server.requests.clear()  # type: ignore[attr-defined]
    _, body = call("POST", f"{V1}/external-imports/preview", json={"projectId": project_id})
    check("预览成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    pdata = body["data"]
    check("预览计数正确", pdata["counts"] == {"requirements": 2, "cases": 1, "bugs": 1}, str(pdata["counts"]))
    check("预览样本带标题", pdata["samples"]["requirements"][0] == "登录需求", str(pdata["samples"]))
    methods = {m for m, _ in server.requests}  # type: ignore[attr-defined]
    check("预览只发 GET 与登录 POST", methods <= {"GET", "POST"}, str(methods))
    check("登录走 tokens 端点", any(m == "POST" and p == "/api.php/v1/tokens"
          for m, p in server.requests))  # type: ignore[attr-defined]

    # ---------- 3b. 子路径部署（如 /zentao） ----------
    prefix_url = f"{base_url}/zentao"
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "platform": "zentao", "baseUrl": prefix_url,
        "username": ACCOUNT, "password": PASSWORD, "productId": "1",
    })
    check("子路径 baseUrl 保存成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    server.requests.clear()  # type: ignore[attr-defined]
    _, body = call("POST", f"{V1}/external-imports/preview", json={"projectId": project_id})
    check("子路径部署可预览", body.get("code") == ErrorCode.SUCCESS and
          body["data"]["counts"] == {"requirements": 2, "cases": 1, "bugs": 1}, str(body.get("message")))
    check("子路径请求实际带前缀", any(p.startswith("/zentao/api.php/v1/")
          for _, p in server.requests))  # type: ignore[attr-defined]
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "platform": "zentao", "baseUrl": base_url,
        "username": ACCOUNT, "password": PASSWORD, "productId": "1",
    })
    check("恢复根路径配置成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))

    # ---------- 4. 导入与落库 ----------
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "kinds": ["requirements", "cases", "bugs"],
    })
    check("导入成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    result = body["data"]
    check("导入计数正确", result["imported"] == {"requirements": 2, "cases": 1, "bugs": 1}, str(result))
    check("导入状态 success", result["status"] == "success", str(result))
    reqs = rows(db_path, "SELECT * FROM biz_requirement WHERE project_id=? AND external_platform='zentao'", (project_id,))
    check("需求落库 2 条", len(reqs) == 2, str(len(reqs)))
    req101 = next((r for r in reqs if r["external_id"] == "101"), None)
    check("需求 external_id/url 齐全", req101 is not None and req101["external_url"].endswith("/story/view/101.html"))
    check("需求 content 按映射落库", req101 is not None and req101["content"] == "支持账号密码登录",
          str(dict(req101) if req101 else None))
    check("需求 priority 由 pri 映射 P0", req101 is not None and req101["priority"] == "P0")
    cases = rows(db_path, "SELECT * FROM biz_test_case WHERE project_id=? AND external_platform='zentao'", (project_id,))
    check("用例落库 1 条", len(cases) == 1)
    check("用例步骤按映射拆分", len(json.loads(cases[0]["steps_json"])) == 2, cases[0]["steps_json"])
    bugs = rows(db_path, "SELECT * FROM biz_bug WHERE project_id=? AND external_platform='zentao'", (project_id,))
    check("缺陷落库 1 条", len(bugs) == 1)
    check("缺陷 severity 由 severity 映射 P0", bugs[0]["severity"] == "P0", bugs[0]["severity"])

    # ---------- 5. 去重 ----------
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "kinds": ["requirements", "cases", "bugs"],
    })
    dup = body["data"]
    check("二次导入全部 skipped", dup["skipped"] == {"requirements": 2, "cases": 1, "bugs": 1}, str(dup))
    check("二次导入不新增", dup["imported"] == {"requirements": 0, "cases": 0, "bugs": 0}, str(dup))
    check("需求表仍 2 条", len(rows(db_path, "SELECT * FROM biz_requirement WHERE project_id=? AND external_platform='zentao'", (project_id,))) == 2)

    # ---------- 6. 只读红线 ----------
    all_methods = {m for m, _ in server.requests}  # type: ignore[attr-defined]
    check("全程无 PUT/DELETE 回写", "PUT" not in all_methods and "DELETE" not in all_methods, str(all_methods))
    check("POST 仅限登录端点", all(p.endswith("/api.php/v1/tokens")
          for m, p in server.requests if m == "POST"))  # type: ignore[attr-defined]

    # ---------- 7. 映射模板 ----------
    _, body = call("POST", f"{V1}/external-imports/mappings", json={
        "projectId": project_id, "name": "标题取spec", "isDefault": True,
        "mapping": {"requirement": {"title": "spec"}},
    })
    check("新建映射模板成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    mapping_id = int(body["data"]["id"])
    check("映射模板标记默认", body["data"]["isDefault"] is True)
    _, body = call("POST", f"{V1}/external-imports/mappings", json={
        "projectId": project_id, "name": "标题取spec", "mapping": {},
    })
    check("重名映射被拒绝", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))
    _, body = call("POST", f"{V1}/external-imports/preview", json={
        "projectId": project_id, "mappingId": mapping_id,
    })
    check("自定义映射生效（标题取 spec）",
          body["data"]["samples"]["requirements"][0] == "支持账号密码登录", str(body["data"]["samples"]))
    _, body = call("PUT", f"{V1}/external-imports/mappings/{mapping_id}", json={
        "projectId": project_id, "name": "标题取spec改", "mapping": {"requirement": {"title": "title"}},
    })
    check("更新映射模板成功", body.get("code") == ErrorCode.SUCCESS and body["data"]["name"] == "标题取spec改")
    _, body = call("GET", f"{V1}/external-imports/mappings", query_string={"projectId": project_id})
    check("映射列表含新建项", any(m["id"] == mapping_id for m in body["data"]))
    _, body = call("DELETE", f"{V1}/external-imports/mappings/{mapping_id}", query_string={"projectId": project_id})
    check("删除映射模板成功", body.get("code") == ErrorCode.SUCCESS)

    # ---------- 8. 批次与隔离 ----------
    _, body = call("GET", f"{V1}/external-imports/batches", query_string={"projectId": project_id})
    batches = body["data"]
    check("批次列表留痕", len(batches) >= 2, str(len(batches)))
    batch_id = int(batches[0]["id"])
    _, body = call("GET", f"{V1}/external-imports/batches/{batch_id}", query_string={"projectId": project_id})
    check("批次详情可读", body.get("code") == ErrorCode.SUCCESS and body["data"]["id"] == batch_id)
    _, body = call("GET", f"{V1}/external-imports/batches/{batch_id}", query_string={"projectId": other_id})
    check("批次跨项目按不存在处理", body.get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(body.get("message")))

    # ---------- 9. 审计 ----------
    audit_rows = rows(db_path, "SELECT * FROM sys_audit_log WHERE resource_type='external_import'")
    actions = {r["action"] for r in audit_rows}
    check("审计含导入动作", "import" in actions, str(actions))
    check("审计含配置动作", "config" in actions, str(actions))

    # ---------- 10. 服务器级 Basic 两层认证 ----------
    server.mock_cfg["server_auth"] = True  # type: ignore[attr-defined]
    _, body = call("POST", f"{V1}/external-imports/preview", json={"projectId": project_id})
    check("缺服务器认证时 401 明确报错", body.get("code") == ErrorCode.PARAM_ERROR
          and "401" in str(body.get("message")), str(body.get("message")))
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "baseUrl": base_url, "username": ACCOUNT,
        "productId": "1", "serverUsername": SERVER_USER, "serverPassword": SERVER_PASS,
        "extra": {"insecureTls": True},
    })
    check("补服务器认证保存成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    check("编辑未提交口令不清空已存凭据", body["data"]["hasPassword"] is True)
    check("TLS 跳过校验开关随 extra 往返", body["data"]["extra"].get("insecureTls") is True)
    _, body = call("POST", f"{V1}/external-imports/preview", json={"projectId": project_id})
    check("两层认证齐全后预览成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    server.mock_cfg["server_auth"] = False  # type: ignore[attr-defined]

    # ---------- 10b. Jira 适配 ----------
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "platform": "jira", "baseUrl": base_url,
        "username": ACCOUNT, "password": PASSWORD, "productId": "DEMO",
        "extra": {"requirementTypes": ["Story"], "caseTypes": ["Test"], "bugTypes": ["Bug"]},
    })
    check("Jira 配置保存成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    _, body = call("POST", f"{V1}/external-imports/preview", json={
        "projectId": project_id, "platform": "jira",
    })
    check("Jira 预览成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    if body.get("code") == ErrorCode.SUCCESS:
        jprev = body["data"]
        check("Jira 预览按 issue 类型计数",
              jprev["counts"] == {"requirements": 1, "cases": 1, "bugs": 1}, str(jprev["counts"]))
        check("Jira 预览标题取 summary", jprev["samples"]["requirements"] == ["Jira 登录需求"],
              str(jprev["samples"]))
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "platform": "jira",
        "kinds": ["requirements", "cases", "bugs"],
    })
    check("Jira 导入成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    if body.get("code") == ErrorCode.SUCCESS:
        check("Jira 导入计数正确",
              body["data"]["imported"] == {"requirements": 1, "cases": 1, "bugs": 1}, str(body["data"]))
    jira_req = rows(db_path, "SELECT * FROM biz_requirement WHERE project_id=? AND external_platform='jira'", (project_id,))
    check("Jira 需求落库且编号前缀", len(jira_req) == 1 and jira_req[0]["req_no"] == "JIRA-DEMO-1",
          str([dict(r) for r in jira_req]))
    check("Jira 描述按 ADF 提取", bool(jira_req) and "支持单点登录" in str(jira_req[0]["content"]))
    check("Jira 优先级 High→P1", bool(jira_req) and jira_req[0]["priority"] == "P1")
    check("Jira 详情链接指向 browse",
          bool(jira_req) and str(jira_req[0]["external_url"]).endswith("/browse/DEMO-1"))
    jira_case = rows(db_path, "SELECT * FROM biz_test_case WHERE project_id=? AND external_platform='jira'", (project_id,))
    check("Jira 用例步骤按描述行拆分",
          len(jira_case) == 1 and len(json.loads(str(jira_case[0]["steps_json"]))) == 2,
          str([dict(r) for r in jira_case]))
    jira_bug = rows(db_path, "SELECT * FROM biz_bug WHERE project_id=? AND external_platform='jira'", (project_id,))
    check("Jira 缺陷严重度 Highest→P0",
          len(jira_bug) == 1 and jira_bug[0]["severity"] == "P0", str([dict(r) for r in jira_bug]))
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "platform": "jira",
        "kinds": ["requirements", "cases", "bugs"],
    })
    check("Jira 二次导入全部跳过",
          body.get("code") == ErrorCode.SUCCESS
          and body["data"]["skipped"] == {"requirements": 1, "cases": 1, "bugs": 1}, str(body.get("data")))
    jira_methods = {m for m, p in server.requests if p.startswith("/rest/api")}  # type: ignore[attr-defined]
    check("Jira 只发 GET", jira_methods == {"GET"}, str(jira_methods))

    # ---------- 10c. TestLink 适配 ----------
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "platform": "testlink", "baseUrl": base_url,
        "password": "devkey-123", "productId": "77",
        "extra": {"testPlanId": "88"},
    })
    check("TestLink 配置保存成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    server.rpc_methods.clear()  # type: ignore[attr-defined]
    _, body = call("POST", f"{V1}/external-imports/preview", json={
        "projectId": project_id, "platform": "testlink",
    })
    check("TestLink 预览成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    if body.get("code") == ErrorCode.SUCCESS:
        tprev = body["data"]
        check("TestLink 预览只含需求与用例",
              set(tprev["counts"]) == {"requirements", "cases"}
              and tprev["counts"] == {"requirements": 1, "cases": 1}, str(tprev["counts"]))
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "platform": "testlink",
        "kinds": ["requirements", "cases"],
    })
    check("TestLink 导入成功", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))
    if body.get("code") == ErrorCode.SUCCESS:
        check("TestLink 导入计数正确",
              body["data"]["imported"] == {"requirements": 1, "cases": 1}, str(body["data"]))
    tl_case = rows(db_path, "SELECT * FROM biz_test_case WHERE project_id=? AND external_platform='testlink'", (project_id,))
    check("TestLink 用例编号前缀", len(tl_case) == 1 and tl_case[0]["case_no"] == "TL-201",
          str([dict(r) for r in tl_case]))
    check("TestLink 步骤与预期落库",
          bool(tl_case) and len(json.loads(str(tl_case[0]["steps_json"]))) == 2
          and "退款成功" in str(tl_case[0]["expected_result"]), str([dict(r) for r in tl_case]))
    check("TestLink importance 2→P1", bool(tl_case) and tl_case[0]["priority"] == "P1")
    check("TestLink 详情链接指向 linkto.php",
          bool(tl_case) and "linkto.php" in str(tl_case[0]["external_url"]))
    tl_req = rows(db_path, "SELECT * FROM biz_requirement WHERE project_id=? AND external_platform='testlink'", (project_id,))
    check("TestLink 需求落库", len(tl_req) == 1 and tl_req[0]["req_no"] == "TL-11",
          str([dict(r) for r in tl_req]))
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "platform": "testlink", "kinds": ["requirements", "cases"],
    })
    check("TestLink 二次导入全部跳过",
          body.get("code") == ErrorCode.SUCCESS
          and body["data"]["skipped"] == {"requirements": 1, "cases": 1}, str(body.get("data")))
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "platform": "testlink", "kinds": ["bugs"],
    })
    check("TestLink 拒绝缺陷导入",
          body.get("code") == ErrorCode.PARAM_ERROR and "缺陷" in str(body.get("message")),
          str(body.get("message")))
    rpc_methods = set(server.rpc_methods)  # type: ignore[attr-defined]
    check("TestLink 只调只读方法",
          rpc_methods <= {"tl.getRequirements", "tl.getTestCasesForTestPlan", "tl.getTestCase"},
          str(rpc_methods))

    # ---------- 11. 参数校验 ----------
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "platform": "bugzilla", "baseUrl": base_url,
    })
    check("未知平台被拒绝", body.get("code") == ErrorCode.PARAM_ERROR)
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": project_id, "baseUrl": "ftp://x",
    })
    check("非法地址协议被拒绝", body.get("code") == ErrorCode.PARAM_ERROR)
    _, body = call("POST", f"{V1}/external-imports/import", json={
        "projectId": project_id, "kinds": ["unknown"],
    })
    check("未知导入内容被拒绝", body.get("code") == ErrorCode.PARAM_ERROR)
    _, body = call("POST", f"{V1}/external-imports/config", json={
        "projectId": other_id, "baseUrl": base_url, "username": ACCOUNT, "password": PASSWORD,
    })
    check("隔离项目配置保存成功", body.get("code") == ErrorCode.SUCCESS)
    _, body = call("POST", f"{V1}/external-imports/preview", json={"projectId": other_id})
    check("缺产品 ID 明确报错", body.get("code") == ErrorCode.PARAM_ERROR
          and "产品" in str(body.get("message")), str(body.get("message")))

    # ---------- 12. 删除配置清密钥 ----------
    _, body = call("DELETE", f"{V1}/external-imports/config", query_string={"projectId": project_id})
    check("删除配置成功", body.get("code") == ErrorCode.SUCCESS)
    check("配置软删", len(rows(db_path, "SELECT * FROM biz_external_import_config WHERE project_id=? AND platform='zentao' AND is_deleted=0", (project_id,))) == 0)
    check("密钥库引用已清空", SecretStore(workdir).get(f"external_import:{project_id}:zentao") == "")
    _, body = call("GET", f"{V1}/external-imports/config", query_string={"projectId": project_id})
    check("删除后读取配置为空", body.get("code") == ErrorCode.SUCCESS and body["data"] is None)

    server.shutdown()
    print("-" * 50)
    print(f"外部平台只读导入：PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败清单：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())