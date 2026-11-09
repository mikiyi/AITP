"""多项目 × 多版本数据隔离审计门禁（HTTP 版，可长跑）。

只做审计，不改业务代码。直接打已经在跑的服务（默认 127.0.0.1:8760），
自建 ISOAUDIT-<tag> 两个审计项目 + 每项目两个版本 + 少量资产，按
「端点 × 场景」断言六类口径：

1. 项目维度：任何列表/统计/图表接口都不能把 B 项目的行混进 A 项目的结果；
2. 跨项目引用：把 B 的 requirementId/moduleId/versionId/caseId/runId 挂到 A 的
   资产上必须被明确拒绝（业务码），不能静默接受；
3. 详情接口：GET /<实体>/<B 的 id>（含 ?projectId=A）不能读到跨项目数据；
4. 统计与图表：bugs/stats、workbench/overview、reports/trends、
   version-compare、知识待审角标、导航角标；
5. 版本维度（重点）：versionId 过滤互斥、未挂版本口径、跨项目 versionId、
   采纳落库的版本归属、残留 versionId、趋势/对比只算本版本；
6. 导出：xlsx / csv / json 必须遵守 projectId 与 versionId。

    python backend/scripts/check_isolation_matrix.py
    AITP_BASE_URL=http://127.0.0.1:8760 AITP_ACCESS_TOKEN=xxx python backend/scripts/check_isolation_matrix.py
    AITP_AUDIT_KEEP=1 python backend/scripts/check_isolation_matrix.py   # 保留数据便于复查

退出码：0=断言全通过；1=有失败（即泄漏点，修复后应转 0）；2=环境不可用。
"""

from __future__ import annotations

import json
import os
import pathlib
import sys
import urllib.error
import urllib.parse
import urllib.request
import uuid
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.errors import ErrorCode  # noqa: E402

BASE_URL = os.environ.get("AITP_BASE_URL", "http://127.0.0.1:8760").rstrip("/")
TOKEN = os.environ.get("AITP_ACCESS_TOKEN", "").strip()
TIMEOUT = float(os.environ.get("AITP_AUDIT_TIMEOUT", "30"))
KEEP = os.environ.get("AITP_AUDIT_KEEP", "") in ("1", "true", "yes")
V1 = "/api/v1"

#: 失败严重度：修复优先级从高到低
S1 = "S1-数据串了"      # 真实返回/写入/统计了别的项目的数据
S2 = "S2-越权引用"      # 跨项目引用被静默接受，或统计口径失真
S3 = "S3-口径"          # 参数被忽略、缺省口径不明确、同一概念两种写法
S4 = "S4-护栏"          # 已经正确的行为，防回归

REJECT_CODES = (
    ErrorCode.PARAM_ERROR,
    ErrorCode.RESOURCE_NOT_FOUND,
    ErrorCode.ILLEGAL_OPERATION,
    ErrorCode.UNIQUE_CONFLICT,
)

_passed = 0
_failed = 0
_failures: list[tuple[str, str, str]] = []
_notes: list[str] = []
_created: list[tuple[str, int]] = []

#: 清理顺序（先删下游再删上游，否则 140104/140105 挡路）
KIND_ORDER = (
    "release_gate", "test_run", "test_plan", "automation_script", "sql_analysis",
    "todo", "report", "regression", "bug", "test_case", "requirement", "module",
    "project_version", "project",
)
DELETE_PATH = {
    "release_gate": "/api/v1/release-gates/",
    "test_run": "/api/v1/test-runs/",
    "test_plan": "/api/v1/test-plans/",
    "automation_script": "/api/v1/automation/scripts/",
    "sql_analysis": "/api/v1/sql-analyses/",
    "todo": "/api/v1/todos/",
    "report": "/api/v1/reports/",
    "regression": "/api/v1/regressions/",
    "bug": "/api/v1/bugs/",
    "test_case": "/api/v1/test-cases/",
    "requirement": "/api/v1/requirements/",
    "module": "/api/v1/modules/",
    "project_version": "/api/v1/project-versions/",
    "project": "/api/v1/projects/",
}


def check(name: str, cond: bool, detail: str = "", severity: str = S4) -> bool:
    global _passed, _failed
    if cond:
        _passed += 1
        print(f"  [PASS] {name}")
        return True
    _failed += 1
    _failures.append((severity, name, detail))
    print(f"  [FAIL] ({severity}) {name}")
    if detail:
        print(f"         {detail}")
    return False


def note(text: str) -> None:
    _notes.append(text)
    print(f"  [NOTE] {text}")


def brief(payload: Any, limit: int = 240) -> str:
    text = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return text if len(text) <= limit else text[:limit] + "…"


def req_desc(method: str, path: str, payload: Any = None) -> str:
    return f"{method} {path}" + (f" body={brief(payload)}" if payload is not None else "")


def _flat_headers(headers: Any) -> dict[str, str]:
    """waitress 会把 X-AITP-… 规范化成 X-Aitp-…，这里统一小写，避免按大小写取头取空。"""
    return {str(key).lower(): str(value) for key, value in (headers.items() if headers else ())}


class Api:
    """纯标准库 HTTP 客户端：统一拆包封，保留响应头（导出条数要靠头）。"""

    def __init__(self, base: str, token: str) -> None:
        self.base = base
        self.token = token

    def raw(self, method: str, path: str, payload: Any = None) -> tuple[int, dict[str, str], bytes]:
        data = None
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(self.base + path, data=data, method=method)
        if data is not None:
            request.add_header("Content-Type", "application/json; charset=utf-8")
        if self.token:
            request.add_header("Authorization", f"Bearer {self.token}")
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as resp:
                return resp.status, _flat_headers(resp.headers), resp.read()
        except urllib.error.HTTPError as exc:
            return exc.code, _flat_headers(exc.headers), exc.read()

    def call(self, method: str, path: str, payload: Any = None) -> tuple[int, dict[str, str], dict[str, Any]]:
        status, headers, content = self.raw(method, path, payload)
        try:
            body = json.loads(content.decode("utf-8", errors="replace"))
        except ValueError:
            body = {"code": -1, "message": f"非 JSON 响应（{len(content)} 字节）", "data": None}
        if not isinstance(body, dict):
            body = {"code": -1, "message": f"响应不是对象：{brief(body)}", "data": None}
        return status, headers, body

    def data(self, method: str, path: str, payload: Any = None) -> Any:
        status, _headers, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{req_desc(method, path, payload)} 期望成功码 {ErrorCode.SUCCESS}，"
                f"实际 http={status} code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def code(self, method: str, path: str, payload: Any = None) -> tuple[int, dict[str, Any]]:
        _status, _headers, body = self.call(method, path, payload)
        return int(body.get("code") or -1), body

    def create(self, path: str, payload: dict[str, Any], kind: str | None = None) -> dict[str, Any]:
        data = self.data("POST", path, payload) or {}
        if kind and isinstance(data, dict) and data.get("id") is not None:
            _created.append((kind, int(data["id"])))
        return data

    def remove(self, kind: str, pk: int) -> tuple[int, dict[str, str], dict[str, Any]]:
        return self.call("DELETE", DELETE_PATH[kind] + str(pk))

    # ---- 查询便捷层 ----
    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return self.data("GET", f"{path}?{params_of(params)}")

    def rows(self, path: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        return rows_of(self.get(path, params))

    def total(self, path: str, params: dict[str, Any] | None = None) -> int:
        data = self.get(path, params)
        if isinstance(data, dict) and isinstance(data.get("total"), int):
            return int(data["total"])
        return len(rows_of(data))

    def export_count(self, path: str, params: dict[str, Any]) -> tuple[int | None, str]:
        merged = {"format": "csv", **params}
        status, headers, content = self.raw("GET", f"{path}?{params_of(merged)}")
        if status != 200:
            return None, f"http={status} 响应={content[:120]!r}"
        raw = headers.get("x-aitp-export-count")
        if raw is None:
            return None, "响应没有 X-AITP-Export-Count 头"
        try:
            return int(raw), ""
        except ValueError:
            return None, f"头内容异常：{raw!r}"


def params_of(params: dict[str, Any] | None) -> str:
    merged: dict[str, Any] = {"pageNum": 1, "pageSize": 100}
    merged.update(params or {})
    return urllib.parse.urlencode({k: v for k, v in merged.items() if v is not None})


def rows_of(data: Any) -> list[dict[str, Any]]:
    if isinstance(data, list):
        return [row for row in data if isinstance(row, dict)]
    if isinstance(data, dict):
        listing = data.get("list")
        if isinstance(listing, list):
            return [row for row in listing if isinstance(row, dict)]
    return []


def id_set(rows: list[dict[str, Any]]) -> set[int]:
    return {int(row["id"]) for row in rows if row.get("id") is not None}


# ---------------- 断言原语 ----------------

def expect_ids(severity: str, label: str, path: str, params: dict[str, Any],
               expected: set[int], extra: str = "") -> list[dict[str, Any]]:
    """列表端点必须精确返回 expected 这批 id（多一行 = 串项目，少一行 = 漏）。"""
    try:
        rows = API.rows(path, params)
    except AssertionError as exc:
        check(label, False, f"请求=GET {path}?{params_of(params)} 失败：{exc}", severity)
        return []
    got = id_set(rows)
    detail = (
        f"请求=GET {path}?{params_of(params)}｜期望 id={sorted(expected)}｜实际 id={sorted(got)}"
        + (f"｜{extra}" if extra else "")
    )
    check(label, got == expected, detail, severity)
    return rows


def expect_code(severity: str, label: str, method: str, path: str, expected: int,
                payload: dict[str, Any] | None = None) -> dict[str, Any]:
    status, _headers, body = API.call(method, path, payload)
    got = int(body.get("code") or -1)
    check(label, got == expected,
          f"请求={req_desc(method, path, payload)}｜期望 code={expected}｜实际 http={status} "
          f"code={got} msg={body.get('message')}", severity)
    return body


def expect_rejected(severity: str, label: str, method: str, path: str,
                    payload: dict[str, Any], *, kind: str | None = None) -> dict[str, Any] | None:
    """跨项目引用必须被拒绝；若被静默接受则登记脏数据，供后续组合取证 + 收尾删除。"""
    status, _headers, body = API.call(method, path, payload)
    got = int(body.get("code") or -1)
    accepted = got == ErrorCode.SUCCESS
    if accepted:
        check(label, False,
              f"请求={req_desc(method, path, payload)}｜期望业务拒绝"
              f"（{list(REJECT_CODES)} 之一）｜实际 http={status} code={got}"
              f"｜结果=被静默接受，脏数据已落库", severity)
    elif got == ErrorCode.INTERNAL_ERROR:
        check(label, False,
              f"请求={req_desc(method, path, payload)}｜期望业务拒绝码（140100/100100）"
              f"｜实际 code=199000 未分类内部错误：{body.get('message')}"
              f"｜结果=拦住了但异常没归类（写入前缺存在性校验）", severity)
    else:
        check(label, got in REJECT_CODES,
              f"请求={req_desc(method, path, payload)}｜期望业务拒绝"
              f"｜实际 http={status} code={got} msg={body.get('message')}", severity)
    if accepted:
        data = body.get("data") or {}
        if kind and isinstance(data, dict) and data.get("id") is not None:
            _created.append((kind, int(data["id"])))
        return data if isinstance(data, dict) else {}
    return None


def sweep(kind: str, payload: dict[str, Any] | None) -> None:
    """把「本不该被接受却被静默接受」的脏数据立刻删掉，避免污染后续场景的期望值。"""
    if not payload or payload.get("id") is None:
        return
    pk = int(payload["id"])
    API.remove(kind, pk)
    _created[:] = [(k, v) for k, v in _created if not (k == kind and v == pk)]


def expect_value(severity: str, label: str, path: str, params: dict[str, Any],
                 key: str, expected: Any) -> Any:
    try:
        data = API.get(path, params)
    except AssertionError as exc:
        check(label, False, f"请求=GET {path}?{params_of(params)} 失败：{exc}", severity)
        return None
    actual = dig(data, key)
    check(label, actual == expected,
          f"请求=GET {path}?{params_of(params)}｜期望 {key}={expected}｜实际 {key}={actual}", severity)
    return actual


def dig(data: Any, dotted: str) -> Any:
    cursor: Any = data
    for part in dotted.split("."):
        if not isinstance(cursor, dict) or part not in cursor:
            return None
        cursor = cursor[part]
    return cursor


# ---------------- 夹具 ----------------

def make_project(ctx: dict[str, Any], code_suffix: str, name: str) -> int:
    data = API.create(f"{V1}/projects", {
        "code": f"ISOAUDIT-{ctx['tag']}-{code_suffix}", "name": name, "owner": "iso-audit",
    }, "project")
    return int(data["id"])


def make_version(ctx: dict[str, Any], project_key: str, code: str, sort: int) -> int:
    data = API.create(f"{V1}/project-versions", {
        "projectId": ctx[project_key], "versionCode": code, "name": f"审计版本 {code}",
        "status": "in_progress", "sortOrder": sort,
    }, "project_version")
    return int(data["id"])


def make_requirement(ctx: dict[str, Any], project_key: str, title: str,
                     version_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "title": title,
                              "content": f"{title} 的审计需求正文"}
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/requirements", payload, "requirement")["id"])


def make_case(ctx: dict[str, Any], project_key: str, title: str, version_key: str | None = None,
              requirement_key: str | None = None, module_key: str | None = None,
              priority: str = "P1") -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "title": title,
                              "caseType": "functional", "priority": priority,
                              "steps": [{"no": 1, "action": "执行审计步骤", "expected": "预期通过"}]}
    if version_key:
        payload["versionId"] = ctx[version_key]
    if requirement_key:
        payload["requirementId"] = ctx[requirement_key]
    if module_key:
        payload["moduleId"] = ctx[module_key]
    return int(API.create(f"{V1}/test-cases", payload, "test_case")["id"])


def make_bug(ctx: dict[str, Any], project_key: str, title: str, version_key: str | None = None,
             severity: str = "P2") -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "title": title,
                              "severity": severity, "description": f"{title} 正文"}
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/bugs", payload, "bug")["id"])


def make_report(ctx: dict[str, Any], project_key: str, title: str, version_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "title": title,
                              "reportType": "summary", "summary": "审计用报告摘要"}
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/reports", payload, "report")["id"])


def make_sql(ctx: dict[str, Any], project_key: str, title: str, version_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "title": title,
                              "sqlText": "SELECT 1", "riskLevel": "none"}
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/sql-analyses", payload, "sql_analysis")["id"])


def make_script(ctx: dict[str, Any], project_key: str, name: str, version_key: str | None = None,
                module_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "name": name,
                             "category": "api", "content": "print(1)"}
    if version_key:
        payload["versionId"] = ctx[version_key]
    if module_key:
        payload["moduleId"] = ctx[module_key]
    return int(API.create(f"{V1}/automation/scripts", payload, "automation_script")["id"])


def make_todo(ctx: dict[str, Any], project_key: str, title: str,
              module_key: str | None = None, version_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "title": title}
    if module_key:
        payload["moduleId"] = ctx[module_key]
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/todos", payload, "todo")["id"])


def make_plan(ctx: dict[str, Any], project_key: str, name: str, version_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "name": name,
                              "scope": {"caseType": "functional"}}
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/test-plans", payload, "test_plan")["id"])


def make_run(ctx: dict[str, Any], project_key: str, name: str, case_keys: list[str],
             version_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "name": name,
                              "caseIds": [ctx[key] for key in case_keys]}
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/test-runs", payload, "test_run")["id"])


def make_gate(ctx: dict[str, Any], project_key: str, name: str, run_key: str,
              version_key: str | None = None) -> int:
    # 阈值放到最松，保证结论是 passed，不触发通知外发（不产生副作用）
    payload: dict[str, Any] = {
        "projectId": ctx[project_key], "runId": ctx[run_key], "name": name,
        "thresholds": {"overallPassRate": 0, "p0PassRate": 0, "p1PassRate": 0,
                       "maxOpenP0": 9999, "maxOpenP1": 9999, "minCoverageRate": 0},
    }
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/release-gates", payload, "release_gate")["id"])


def make_module(ctx: dict[str, Any], project_key: str, name: str, node_type: str,
                parent_key: str | None = None) -> int:
    payload: dict[str, Any] = {"projectId": ctx[project_key], "name": name, "nodeType": node_type}
    if parent_key:
        payload["parentId"] = ctx[parent_key]
    return int(API.create(f"{V1}/modules", payload, "module")["id"])


def make_regression(ctx: dict[str, Any], project_key: str, title: str, version_code: str,
                    version_key: str | None = None) -> int:
    payload: dict[str, Any] = {
        "projectId": ctx[project_key], "title": title, "version": version_code,
        "riskLevel": "low", "source": "manual",
    }
    if version_key:
        payload["versionId"] = ctx[version_key]
    return int(API.create(f"{V1}/regressions", payload, "regression")["id"])


def build_fixtures(ctx: dict[str, Any]) -> None:
    tag = ctx["tag"]
    print(f"  [SETUP] 审计项目前缀 ISOAUDIT-{tag}（服务：{BASE_URL}）")
    ctx["pa"] = make_project(ctx, "A", f"隔离审计项目A-{tag}")
    ctx["pb"] = make_project(ctx, "B", f"隔离审计项目B-{tag}")

    ctx["va1"] = make_version(ctx, "pa", f"ISOV-{tag}-A1", 1)
    ctx["va2"] = make_version(ctx, "pa", f"ISOV-{tag}-A2", 2)
    ctx["vb1"] = make_version(ctx, "pb", f"ISOV-{tag}-B1", 1)
    ctx["vb2"] = make_version(ctx, "pb", f"ISOV-{tag}-B2", 2)

    # A：模块 3 级（模块 → 子模块 → 功能点），B：1 个模块
    ctx["mA"] = make_module(ctx, "pa", "ISOA-登录模块", "module")
    ctx["mAc"] = make_module(ctx, "pa", "ISOA-手机号登录", "submodule", "mA")
    ctx["mAf"] = make_module(ctx, "pa", "ISOA-验证码时效", "feature", "mAc")
    ctx["mB"] = make_module(ctx, "pb", "ISOB-订单模块", "module")

    # 需求：A=4（v1×2、v2×1、未挂×1），B=2（v1×1、未挂×1）
    ctx["rA1"] = make_requirement(ctx, "pa", f"ISOA-需求1-{tag}", "va1")
    ctx["rA2"] = make_requirement(ctx, "pa", f"ISOA-需求2-{tag}", "va1")
    ctx["rA3"] = make_requirement(ctx, "pa", f"ISOA-需求3-{tag}", "va2")
    ctx["rA0"] = make_requirement(ctx, "pa", f"ISOA-需求4-未挂版本-{tag}")
    ctx["rB1"] = make_requirement(ctx, "pb", f"ISOB-需求1-{tag}", "vb1")
    ctx["rB0"] = make_requirement(ctx, "pb", f"ISOB-需求2-未挂版本-{tag}")

    # 用例：A=4，B=2；cA1/cA2 挂 rA1，cA3 挂 rA3，cA0 挂 rA0
    ctx["cA1"] = make_case(ctx, "pa", f"ISOA-用例1-{tag}", "va1", "rA1", "mAc", "P0")
    ctx["cA2"] = make_case(ctx, "pa", f"ISOA-用例2-{tag}", "va1", "rA1", None, "P1")
    ctx["cA3"] = make_case(ctx, "pa", f"ISOA-用例3-{tag}", "va2", "rA3")
    ctx["cA0"] = make_case(ctx, "pa", f"ISOA-用例4-未挂版本-{tag}", None, "rA0")
    ctx["cB1"] = make_case(ctx, "pb", f"ISOB-用例1-{tag}", "vb1", "rB1")
    ctx["cB0"] = make_case(ctx, "pb", f"ISOB-用例2-未挂版本-{tag}", None, "rB0")

    # 缺陷：A=3，B=1
    ctx["bA1"] = make_bug(ctx, "pa", f"ISOA-缺陷1-{tag}", "va1", "P1")
    ctx["bA2"] = make_bug(ctx, "pa", f"ISOA-缺陷2-{tag}", "va2")
    ctx["bA0"] = make_bug(ctx, "pa", f"ISOA-缺陷3-未挂版本-{tag}")
    ctx["bB1"] = make_bug(ctx, "pb", f"ISOB-缺陷1-{tag}", "vb1")

    # 报告 2/1、SQL 2/1、脚本 2/1、待办 2/1、计划 1/1、轮次 1/1、门禁 1/0、回归 1/0
    ctx["rpA1"] = make_report(ctx, "pa", f"ISOA-报告1-{tag}", "va1")
    ctx["rpA0"] = make_report(ctx, "pa", f"ISOA-报告2-未挂版本-{tag}")
    ctx["rpB1"] = make_report(ctx, "pb", f"ISOB-报告1-{tag}", "vb1")
    ctx["sA1"] = make_sql(ctx, "pa", f"ISOA-SQL1-{tag}", "va1")
    ctx["sA0"] = make_sql(ctx, "pa", f"ISOA-SQL2-未挂版本-{tag}")
    ctx["sB1"] = make_sql(ctx, "pb", f"ISOB-SQL1-{tag}")
    ctx["scA1"] = make_script(ctx, "pa", f"ISOA脚本1{tag}", "va1")
    ctx["scA0"] = make_script(ctx, "pa", f"ISOA脚本2未挂版本{tag}")
    ctx["scB1"] = make_script(ctx, "pb", f"ISOB脚本1{tag}")
    ctx["tA1"] = make_todo(ctx, "pa", f"ISOA-待办1-{tag}", version_key="va1")
    ctx["tA2"] = make_todo(ctx, "pa", f"ISOA-待办2-{tag}", "mAf")
    ctx["tB1"] = make_todo(ctx, "pb", f"ISOB-待办1-{tag}", version_key="vb1")
    ctx["pA1"] = make_plan(ctx, "pa", f"ISOA-计划1-{tag}", "va1")
    ctx["pB0"] = make_plan(ctx, "pb", f"ISOB-计划1-{tag}")
    ctx["runA"] = make_run(ctx, "pa", f"ISOA-轮次1-{tag}", ["cA1", "cA2"], "va1")
    ctx["runB"] = make_run(ctx, "pb", f"ISOB-轮次1-{tag}", ["cB1"], "vb1")
    ctx["gateA"] = make_gate(ctx, "pa", f"ISOA-门禁1-{tag}", "runA", "va1")
    ctx["regA"] = make_regression(ctx, "pa", f"ISOA-回归清单-{tag}", f"ISOV-{ctx['tag']}-A1", "va1")
    ctx["regB"] = make_regression(ctx, "pb", f"ISOB-回归清单-{tag}", f"ISOV-{ctx['tag']}-B1", "vb1")

    # 期望集合（断言的唯一真相来源）
    ctx["exp"] = {
        "requirements": {"A": {ctx["rA1"], ctx["rA2"], ctx["rA3"], ctx["rA0"]},
                         "B": {ctx["rB1"], ctx["rB0"]},
                         "A@v1": {ctx["rA1"], ctx["rA2"]}, "A@v2": {ctx["rA3"]},
                         "A@none": {ctx["rA0"]}, "B@v1": {ctx["rB1"]}, "B@none": {ctx["rB0"]}},
        "test-cases": {"A": {ctx["cA1"], ctx["cA2"], ctx["cA3"], ctx["cA0"]},
                       "B": {ctx["cB1"], ctx["cB0"]},
                       "A@v1": {ctx["cA1"], ctx["cA2"]}, "A@v2": {ctx["cA3"]},
                       "A@none": {ctx["cA0"]}, "B@v1": {ctx["cB1"]}, "B@none": {ctx["cB0"]}},
        "bugs": {"A": {ctx["bA1"], ctx["bA2"], ctx["bA0"]}, "B": {ctx["bB1"]},
                 "A@v1": {ctx["bA1"]}, "A@v2": {ctx["bA2"]}, "A@none": {ctx["bA0"]},
                 "B@v1": {ctx["bB1"]}, "B@none": set()},
        "reports": {"A": {ctx["rpA1"], ctx["rpA0"]}, "B": {ctx["rpB1"]},
                    "A@v1": {ctx["rpA1"]}, "A@v2": set(), "A@none": {ctx["rpA0"]},
                    "B@v1": {ctx["rpB1"]}, "B@none": set()},
        "sql-analyses": {"A": {ctx["sA1"], ctx["sA0"]}, "B": {ctx["sB1"]},
                         "A@v1": {ctx["sA1"]}, "A@v2": set(), "A@none": {ctx["sA0"]},
                         "B@v1": set(), "B@none": {ctx["sB1"]}},
        "automation/scripts": {"A": {ctx["scA1"], ctx["scA0"]}, "B": {ctx["scB1"]},
                               "A@v1": {ctx["scA1"]}, "A@v2": set(), "A@none": {ctx["scA0"]},
                               "B@v1": set(), "B@none": {ctx["scB1"]}},
        "test-plans": {"A": {ctx["pA1"]}, "B": {ctx["pB0"]}, "A@v1": {ctx["pA1"]},
                       "A@v2": set(), "B@v1": set(), "A@none": set(), "B@none": {ctx["pB0"]}},
        "test-runs": {"A": {ctx["runA"]}, "B": {ctx["runB"]}, "A@v1": {ctx["runA"]},
                      "A@v2": set(), "B@v1": {ctx["runB"]}, "A@none": set(),
                      "B@none": set()},
        "release-gates": {"A": {ctx["gateA"]}, "B": set(), "A@v1": {ctx["gateA"]},
                          "A@v2": set(), "A@none": set(), "B@v1": set(), "B@none": set()},
        "todos": {"A": {ctx["tA1"], ctx["tA2"]}, "B": {ctx["tB1"]},
                  "A@v1": {ctx["tA1"]}, "A@v2": set(), "A@none": {ctx["tA2"]},
                  "B@v1": {ctx["tB1"]}, "B@none": set()},
        "regressions": {"A": {ctx["regA"]}, "B": {ctx["regB"]},
                        "A@v1": {ctx["regA"]}, "A@v2": set(), "A@none": set(),
                        "B@v1": {ctx["regB"]}, "B@none": set()},
    }

    check("夹具：A 项目 4 需求 / B 项目 2 需求",
          API.total(f"{V1}/requirements", {"projectId": ctx["pa"]}) == 4
          and API.total(f"{V1}/requirements", {"projectId": ctx["pb"]}) == 2,
          f"A total={API.total(f'{V1}/requirements', {'projectId': ctx['pa']})}")
    note("夹具数量刻意不对称（A>B、v1>v2>未挂），这样「全局统计」一定会算错，便于取证。")


# ---------------- 场景 1：项目维度列表 ----------------

def s_project_lists(ctx: dict[str, Any]) -> None:
    exp = ctx["exp"]
    for path in ("requirements", "test-cases", "bugs", "reports", "sql-analyses",
                 "automation/scripts", "test-plans", "test-runs", "todos", "regressions",
                 "release-gates"):
        expect_ids(S1, f"{path} 列表：projectId=A 只能是 A 的行", f"{V1}/{path}",
                   {"projectId": ctx["pa"]}, exp[path]["A"])
        expect_ids(S1, f"{path} 列表：projectId=B 只能是 B 的行", f"{V1}/{path}",
                   {"projectId": ctx["pb"]}, exp[path]["B"])

    # 版本维度接口
    expect_ids(S1, "project-versions 列表：projectId=A 只返回 A 的两个版本", f"{V1}/project-versions",
               {"projectId": ctx["pa"]}, {ctx["va1"], ctx["va2"]})

    # 缺省 projectId 的默认口径（记录下来，不判失败）
    all_total = API.total(f"{V1}/test-cases", {})
    a_total = API.total(f"{V1}/test-cases", {"projectId": ctx["pa"]})
    check("test-cases 缺省 projectId 时不得比 A 项目还少（口径基线）", all_total >= a_total,
          f"缺省 total={all_total}，projectId=A total={a_total}", S3)
    note(f"缺省 projectId 口径：test-cases 无项目参数时返回全库 {all_total} 条"
         f"（A 项目 {a_total} 条），即「不传 = 看全部项目」，非本项目默认视图。")
    code, _body = API.code("GET", f"{V1}/project-versions?pageSize=100")
    check("project-versions 缺省 projectId 必须报参数错误（100100）", code == ErrorCode.PARAM_ERROR,
          f"请求=GET {V1}/project-versions（无 projectId）｜期望 code=100100｜实际 code={code}", S3)


# ---------------- 场景 2：项目维度树 / 下拉 / 关联视图 ----------------

def s_project_views(ctx: dict[str, Any]) -> None:
    tree = API.get(f"{V1}/modules/tree", {"projectId": ctx["pa"]}) or {}
    node_ids = flatten_module_ids(tree.get("tree") or [])
    check("modules/tree：projectId=A 只含 A 的模块节点",
          node_ids == {ctx["mA"], ctx["mAc"], ctx["mAf"]},
          f"请求=GET {V1}/modules/tree?projectId={ctx['pa']}｜期望={sorted({ctx['mA'], ctx['mAc'], ctx['mAf']})}"
          f"｜实际={sorted(node_ids)}", S1)

    opts = API.get(f"{V1}/modules/options", {"projectId": ctx["pa"], "all": "1"}) or []
    check("modules/options：不得出现 B 项目功能点",
          {int(o["id"]) for o in opts if o.get("id") is not None} <= {ctx["mA"], ctx["mAc"], ctx["mAf"]},
          f"实际 id={sorted(int(o['id']) for o in opts if o.get('id'))}", S1)

    req_opts = API.get(f"{V1}/requirements/options", {"projectId": ctx["pa"]}) or []
    check("requirements/options：projectId=A 只列 A 的需求",
          {int(r["id"]) for r in req_opts if r.get("id")} == ctx["exp"]["requirements"]["A"],
          f"实际 id={sorted(int(r['id']) for r in req_opts if r.get('id'))}", S1)
    all_code, all_resp = API.code("GET", f"{V1}/requirements/options")
    all_opts = rows_of(all_resp.get("data")) if all_code == ErrorCode.SUCCESS else []
    foreign_opts = [row for row in all_opts if int(row.get("projectId") or 0) != ctx["pa"]]
    check("requirements/options：缺省 projectId 不得枚举全库需求（应报参数错误或限定范围）",
          all_code != ErrorCode.SUCCESS or len(all_opts) == 0,
          f"请求=GET {V1}/requirements/options（无 projectId）｜期望 0 条或 100100"
          f"｜实际 code={all_code} {len(all_opts)} 条，其中 {len(foreign_opts)} 条属于别的项目", S2)

    cases_tree = API.get(f"{V1}/test-cases/tree", {"projectId": ctx["pa"]}) or {}
    tree_case_ids = collect_case_ids(cases_tree.get("list") or []) | id_set(cases_tree.get("orphans") or [])
    check("test-cases/tree：只含 A 的用例",
          tree_case_ids <= ctx["exp"]["test-cases"]["A"],
          f"期望⊆{sorted(ctx['exp']['test-cases']['A'])}｜实际={sorted(tree_case_ids)}", S1)
    note("test-cases/tree 的 versionId 入参走 scope_arg，与用例列表同一套筛选口径。")
    tree_v2 = API.get(f"{V1}/test-cases/tree", {"projectId": ctx["pa"], "versionId": ctx["va2"]}) or {}
    v2_ids = collect_case_ids(tree_v2.get("list") or []) | id_set(tree_v2.get("orphans") or [])
    check("test-cases/tree 传 versionId 时应只挂该版本用例",
          v2_ids == {ctx["cA3"]},
          f"请求=GET {V1}/test-cases/tree?projectId=A&versionId=vA2｜期望={sorted({ctx['cA3']})}"
          f"｜实际={sorted(v2_ids)}", S2)

    cov = API.get(f"{V1}/test-cases/coverage", {"projectId": ctx["pa"]}) or {}
    cov_rows = rows_of(cov)
    check("test-cases/coverage：矩阵行只允许是 A 的需求",
          {int(r["requirement_id"]) for r in cov_rows if r.get("requirement_id")} <= ctx["exp"]["requirements"]["A"]
          if cov_rows else True,
          f"实际需求 id={sorted(int(r['requirement_id']) for r in cov_rows if r.get('requirement_id'))}", S1)
    cov_v2 = API.get(f"{V1}/test-cases/coverage", {"projectId": ctx["pa"], "versionId": ctx["va2"]}) or {}
    cov_v2_rows = rows_of(cov_v2)
    check("test-cases/coverage：versionId=vA2 只统计 v2 的需求与用例（1 行 1 条）",
          {int(r["requirement_id"]) for r in cov_v2_rows if r.get("requirement_id")} == {ctx["rA3"]}
          and sum_counts(cov_v2_rows) == 1,
          f"请求=GET {V1}/test-cases/coverage?projectId=A&versionId=vA2"
          f"｜需求行={sorted(int(r['requirement_id']) for r in cov_v2_rows if r.get('requirement_id'))}"
          f" 用例数={sum_counts(cov_v2_rows)}", S1)
    cov_b = API.get(f"{V1}/test-cases/coverage", {"projectId": ctx["pb"]}) or {}
    note(f"test-cases/coverage 带 versionId 后按版本收敛：A 全版本 {sum_counts(cov_rows)} 条用例、"
         f"B {sum_counts(rows_of(cov_b))} 条、A@v2 {sum_counts(cov_v2_rows)} 条。")

    module_cov = API.get(f"{V1}/test-cases/coverage/modules", {"projectId": ctx["pa"]}) or {}
    module_cov_v2 = API.get(
        f"{V1}/test-cases/coverage/modules", {"projectId": ctx["pa"], "versionId": ctx["va2"]}
    ) or {}
    check("coverage/modules：versionId=vA2 时功能点用例数为 0（v2 用例未挂功能点）",
          sum(int(r.get("caseCount") or 0) for r in rows_of(module_cov_v2)) == 0,
          f"实际={sum(int(r.get('caseCount') or 0) for r in rows_of(module_cov_v2))}", S2)
    note(f"coverage/modules（功能点矩阵）返回 {len(rows_of(module_cov))} 个节点，"
         f"已支持 versionId（A 全版本 {sum_counts(rows_of(module_cov))} 条）。")

    trace = API.get(f"{V1}/traceability", {"projectId": ctx["pa"], "versionId": ctx["va1"]}) or {}
    check("traceability：projectId=A&versionId=vA1 只统计 A/v1 的 2 条需求",
          int((trace.get("summary") or {}).get("requirementTotal") or -1) == 2,
          f"实际 summary={brief(trace.get('summary'))}", S1)
    trace_cross = API.get(f"{V1}/traceability", {"projectId": ctx["pa"], "versionId": ctx["vb1"]}) or {}
    check("traceability：projectId=A + 跨项目 versionId 必须空集（不得返回 B 的数据）",
          int((trace_cross.get("summary") or {}).get("requirementTotal") or 0) == 0,
          f"实际 summary={brief(trace_cross.get('summary'))}", S1)
    expect_code(S2, "traceability：runId 必须属于 projectId（否则跨项目轮次进矩阵）",
                "GET", f"{V1}/traceability?{params_of({'projectId': ctx['pa'], 'runId': ctx['runB']})}",
                ErrorCode.RESOURCE_NOT_FOUND)


# ---------------- 场景 3：详情接口越权读 ----------------

def s_detail_idor(ctx: dict[str, Any]) -> None:
    pa = ctx["pa"]
    targets = (
        ("requirements", ctx["rB1"]), ("test-cases", ctx["cB1"]), ("bugs", ctx["bB1"]),
        ("reports", ctx["rpB1"]), ("sql-analyses", ctx["sB1"]), ("automation/scripts", ctx["scB1"]),
        ("test-plans", ctx["pB0"]), ("test-runs", ctx["runB"]), ("release-gates", ctx["gateA"]),
        ("todos", ctx["tB1"]), ("regressions", ctx["regB"]), ("modules", ctx["mB"]),
        ("project-versions", ctx["vb1"]),
    )
    for path, pk in targets:
        if path == "release-gates" and pk == ctx["gateA"]:
            continue  # 门禁只造了 A 的，跳过自读
        code, body = API.code("GET", f"{V1}/{path}/{pk}?projectId={pa}")
        check(f"{path}/<B 的 id>?projectId=A 不得返回 200000（跨项目详情读取）",
              code != ErrorCode.SUCCESS,
              f"请求=GET {V1}/{path}/{pk}?projectId={pa}｜期望非成功码（140100/140105）"
              f"｜实际 code={code}，返回体={brief(body.get('data'), 160)}", S2)
    plain_code, plain_body = API.code("GET", f"{V1}/test-cases/{ctx['cB1']}")
    note("test-cases/<B id>（不带 projectId）仍可读到 B 的用例正文：这是刻意保留的行为。"
         f"详情页（CaseDetailView）与全局搜索深链只带 id、没有项目上下文，强制 projectId 会把它们打断；"
         f"本机单人工具按 id 直读不构成跨用户越权。带 projectId 时一律校验归属（见上面的 IDOR 断言）。"
         f"实际 code={plain_code} 标题={brief((plain_body.get('data') or {}).get('title'))}")


# ---------------- 场景 4：跨项目引用写入 ----------------

def s_cross_refs(ctx: dict[str, Any]) -> None:
    pa, pb = ctx["pa"], ctx["pb"]
    accepted: list[tuple[str, int]] = []

    def rejected(severity: str, label: str, method: str, path: str, payload: dict[str, Any],
                 kind: str | None = None) -> dict[str, Any] | None:
        data = expect_rejected(severity, label, method, path, payload, kind=kind)
        if kind and data and data.get("id") is not None:
            accepted.append((kind, int(data["id"])))
        return data

    rejected(S2, "requirements：A 项目需求不得挂 B 的 versionId", "POST", f"{V1}/requirements",
             {"projectId": pa, "title": "ISOA-脏-跨项目版本引用", "versionId": ctx["vb1"]},
             "requirement")
    rejected(S2, "test-cases：A 项目用例不得引用 B 的需求", "POST", f"{V1}/test-cases",
             {"projectId": pa, "title": "ISOA-脏-跨项目需求", "requirementId": ctx["rB1"]},
             "test_case")
    rejected(S2, "test-cases：A 项目用例不得挂 B 的模块", "POST", f"{V1}/test-cases",
             {"projectId": pa, "title": "ISOA-脏-跨项目模块", "moduleId": ctx["mB"]},
             "test_case")
    dirty_case = rejected(S2, "test-cases：A 项目用例不得挂 B 的 versionId", "POST", f"{V1}/test-cases",
                          {"projectId": pa, "title": "ISOA-脏-版本越权", "versionId": ctx["vb1"]},
                          "test_case")
    rejected(S2, "bugs：A 项目缺陷不得挂 B 的模块+版本", "POST", f"{V1}/bugs",
             {"projectId": pa, "title": "ISOA-脏-缺陷跨引用", "moduleId": ctx["mB"],
              "versionId": ctx["vb1"]}, "bug")
    rejected(S2, "reports：A 项目报告不得挂 B 的 versionId", "POST", f"{V1}/reports",
             {"projectId": pa, "title": "ISOA-脏-报告跨版本", "versionId": ctx["vb1"]}, "report")
    rejected(S2, "sql-analyses：A 项目 SQL 不得挂 B 的 versionId", "POST", f"{V1}/sql-analyses",
             {"projectId": pa, "title": "ISOA-脏-SQL跨版本", "versionId": ctx["vb1"]}, "sql_analysis")
    rejected(S2, "automation/scripts：A 脚本不得挂 B 的模块+版本", "POST",
             f"{V1}/automation/scripts",
             {"projectId": pa, "name": f"ISOA脏脚本{ctx['tag']}", "category": "api",
              "moduleId": ctx["mB"], "versionId": ctx["vb1"]}, "automation_script")
    rejected(S2, "test-plans：A 计划不得挂 B 的 versionId", "POST", f"{V1}/test-plans",
             {"projectId": pa, "name": "ISOA-脏计划", "versionId": ctx["vb1"]}, "test_plan")
    rejected(S2, "todos：A 待办不得挂 B 的模块", "POST", f"{V1}/todos",
             {"projectId": pa, "title": "ISOA-脏待办", "moduleId": ctx["mB"]}, "todo")
    rejected(S2, "todos：A 待办不得挂 B 的 versionId", "POST", f"{V1}/todos",
             {"projectId": pa, "title": "ISOA-脏待办-跨项目版本", "versionId": ctx["vb1"]}, "todo")
    rejected(S2, "regressions：A 清单不得挂 B 的 versionId", "POST", f"{V1}/regressions",
             {"projectId": pa, "title": "ISOA-脏回归-跨项目版本", "versionId": ctx["vb1"]},
             "regression")
    rejected(S2, "todos：projectId 不存在必须报错（当前不校验项目存在性）", "POST", f"{V1}/todos",
             {"projectId": 99999999, "title": "ISOA-脏待办-幽灵项目"}, "todo")
    rejected(S2, "regressions：projectId 不存在必须报错", "POST", f"{V1}/regressions",
             {"projectId": 99999999, "title": "ISOA-脏回归"}, "regression")

    # 反向引用：B 的用例引用 A 的需求 —— 会直接污染 A 的覆盖矩阵（真实跨项目统计泄漏）
    dirty = rejected(S1, "test-cases：B 项目用例不得引用 A 的需求（污染 A 的覆盖矩阵）",
                     "POST", f"{V1}/test-cases",
                     {"projectId": pb, "title": f"ISOB-脏-反向引用A需求{ctx['tag']}",
                      "requirementId": ctx["rA1"]}, "test_case")
    if dirty and dirty.get("id") is not None:
        cov_rows = rows_of(API.get(f"{V1}/test-cases/coverage", {"projectId": pa}) or {})
        hit = next((row for row in cov_rows if int(row.get("requirement_id") or -1) == ctx["rA1"]), None)
        check("test-cases/coverage：A 需求的用例数不得包含 B 项目的用例（join 缺 c.project_id）",
              hit is not None and int(hit.get("case_count") or 0) == 2,
              f"前置=A 用例 2 条 + 脏 B 用例 1 条｜rA1.case_count 期望 2｜实际={brief(hit)}"
              f"（test_case_repo.py:50-67 LEFT JOIN 只按 requirement_id，未加 c.project_id）", S1)

    # 缺陷 ↔ 用例关联：只校验用例存在，不校验项目
    link_body = rejected(S1, "bugs/<A 缺陷>/cases：不得关联 B 项目的用例", "POST",
                         f"{V1}/bugs/{ctx['bA1']}/cases", {"caseIds": [ctx["cB1"]]})
    if link_body is not None:
        detail = API.get(f"{V1}/bugs/{ctx['bA1']}", {}) or {}
        linked = detail.get("linkedCases") or []
        check("bugs 详情：linkedCases 回显不得出现 B 项目用例（跨项目内容泄漏）",
              all(int(item.get("id") or 0) != ctx["cB1"] for item in linked),
              f"GET {V1}/bugs/{ctx['bA1']} linkedCases={brief(linked)}（bugs.py:322-345 只查用例存在，"
              f"bugs.py:61-80 回显不带项目条件）", S1)
        API.call("DELETE", f"{V1}/bugs/{ctx['bA1']}/cases", {"caseIds": [ctx["cB1"]]})

    # 轮次 / 门禁 / 模块：已有跨项目校验，做回归护栏
    rejected(S4, "test-runs：A 轮次的 caseIds 不得包含 B 的用例（现有校验）", "POST",
             f"{V1}/test-runs", {"projectId": pa, "name": "ISOA-脏轮次",
                                 "caseIds": [ctx["cB1"]]}, "test_run")
    rejected(S2, "test-runs：A 轮次不得挂 B 的 versionId", "POST", f"{V1}/test-runs",
             {"projectId": pa, "name": "ISOA-脏轮次版本", "caseIds": [ctx["cA1"]],
              "versionId": ctx["vb1"]}, "test_run")
    rejected(S4, "release-gates：runId 跨项目必须拒绝（现有校验）", "POST", f"{V1}/release-gates",
             {"projectId": pa, "runId": ctx["runB"], "name": "ISOA-脏门禁"}, "release_gate")
    rejected(S4, "modules：A 的模块不得挂到 B 的父节点（现有校验）", "POST", f"{V1}/modules",
             {"projectId": pa, "name": "ISOA-脏模块", "nodeType": "module",
              "parentId": ctx["mB"]}, "module")

    # PUT 侧：把 A 的资产改挂到 B 的关联 id
    if dirty_case and dirty_case.get("id") is not None:
        dirty_id = int(dirty_case["id"])
    else:
        dirty_id = make_case(ctx, "pa", f"ISOA-脏用例-PUT探针-{ctx['tag']}")
    rejected(S2, "test-cases PUT：A 用例不得改挂 B 的 versionId", "PUT",
             f"{V1}/test-cases/{dirty_id}", {"versionId": ctx["vb2"]})
    rejected(S2, "test-cases PUT：A 用例不得整体搬到 B 项目", "PUT",
             f"{V1}/test-cases/{dirty_id}", {"projectId": pb})
    rejected(S2, "requirements PUT：A 需求不得改挂 B 的 versionId", "PUT",
             f"{V1}/requirements/{ctx['rA1']}", {"versionId": ctx["vb1"]})
    API.data("PUT", f"{V1}/requirements/{ctx['rA1']}", {"versionId": ctx["va1"]})
    vrow = API.get(f"{V1}/project-versions/{ctx['va1']}", {}) or {}
    check("project-versions PUT：不接受 projectId 改挂（版本不能跨项目搬）",
          int(vrow.get("projectId") or -1) == pa,
          f"GET {V1}/project-versions/{ctx['va1']} projectId={vrow.get('projectId')} 期望 {pa}", S4)

    # 采纳（adopt）落库：目标项目对，但版本落点未校验
    vb_before = (API.get(f"{V1}/project-versions/{ctx['vb1']}", {}) or {}).get("assetCount")
    _status, _headers, adopt_body = API.call("POST", f"{V1}/test-cases/adopt", {
        "projectId": pa, "versionId": ctx["vb1"],
        "cases": [{"title": f"ISOA-采纳-版本越权{ctx['tag']}"}],
    })
    adopt_code = int(adopt_body.get("code") or -1)
    check("test-cases/adopt：projectId=A + 跨项目 versionId 必须拒绝",
          adopt_code != ErrorCode.SUCCESS,
          f"请求=POST {V1}/test-cases/adopt body={{projectId: A, versionId: vB1, cases: 1 条}}"
          f"｜期望拒绝｜实际 code={adopt_code} msg={adopt_body.get('message')}", S1)
    adopted_ids: list[int] = []
    if adopt_code == ErrorCode.SUCCESS:
        created_rows = (adopt_body.get("data") or {}).get("created") or []
        adopted_ids = [int(row["id"]) for row in created_rows if row.get("id") is not None]
        expect_ids(S1, "组合取证 A：采纳后 projectId=A&versionId=vB1 不得返回任何行（脏引用已落地）",
                   f"{V1}/test-cases", {"projectId": pa, "versionId": ctx["vb1"]}, set())
        expect_ids(S1, "组合取证 B：versionId=vB1 单独查询不得把 A 项目的用例带出来",
                   f"{V1}/test-cases", {"versionId": ctx["vb1"]}, ctx["exp"]["test-cases"]["B@v1"])
        vb_after = (API.get(f"{V1}/project-versions/{ctx['vb1']}", {}) or {}).get("assetCount")
        check("组合取证 C：B 版本的 assetCount 不得因 A 项目的资产而增加",
              vb_after == vb_before,
              f"GET {V1}/project-versions/{ctx['vb1']} assetCount：采纳前={vb_before} → 采纳后={vb_after}"
              f"（project_version_repo.py:53-76 count_assets 只按 version_id，不分项目）", S1)
        note("adopt 副作用：给导航角标 cases +1（sys_nav_badge 全局表，无项目维度），本次审计无法回滚。")
    for kind, pk in accepted:
        sweep(kind, {"id": pk})
    for pk in adopted_ids:
        sweep("test_case", {"id": pk})
    if not dirty_case or dirty_case.get("id") is None:
        sweep("test_case", {"id": dirty_id})
    if dirty and dirty.get("id") is not None:
        sweep("test_case", {"id": int(dirty["id"])})
    if accepted:
        note(f"本场景被静默接受的跨项目引用共 {len(accepted)} 条，已当场删除，"
             f"避免污染后续版本/统计场景的期望值。")


# ---------------- 场景 5：版本维度 ----------------

def s_version_dimension(ctx: dict[str, Any]) -> None:
    pa, pb = ctx["pa"], ctx["pb"]
    exp = ctx["exp"]
    endpoints = ("requirements", "test-cases", "bugs", "reports", "sql-analyses",
                 "automation/scripts", "test-plans", "test-runs", "release-gates",
                 "todos", "regressions")
    for path in endpoints:
        table = exp[path]
        expect_ids(S1, f"{path}：projectId=A&versionId=vA1 只返回 v1 资产（排除 v2 与未挂版本）",
                   f"{V1}/{path}", {"projectId": pa, "versionId": ctx["va1"]}, table["A@v1"])
        expect_ids(S1, f"{path}：projectId=A&versionId=vA2 只返回 v2 资产",
                   f"{V1}/{path}", {"projectId": pa, "versionId": ctx["va2"]}, table["A@v2"])
        expect_ids(S1, f"{path}：跨项目 versionId（projectId=A&versionId=vB1）必须空集",
                   f"{V1}/{path}", {"projectId": pa, "versionId": ctx["vb1"]}, set())
        expect_ids(S1, f"{path}：跨项目 versionId（projectId=B&versionId=vA1）必须空集",
                   f"{V1}/{path}", {"projectId": pb, "versionId": ctx["va1"]}, set())

    # v1 / v2 互斥且并集 = 已挂版本资产
    v1 = id_set(API.rows(f"{V1}/test-cases", {"projectId": pa, "versionId": ctx["va1"]}))
    v2 = id_set(API.rows(f"{V1}/test-cases", {"projectId": pa, "versionId": ctx["va2"]}))
    all_rows = id_set(API.rows(f"{V1}/test-cases", {"projectId": pa}))
    check("test-cases：v1 与 v2 结果互斥", not (v1 & v2), f"v1={sorted(v1)} v2={sorted(v2)}", S1)
    check("test-cases：v1 ∪ v2 ⊆ 项目全部，且差额就是未挂版本", (v1 | v2) <= all_rows,
          f"全部={sorted(all_rows)} v1∪v2={sorted(v1 | v2)}", S1)

    # 返回体里的版本字段必须与筛选一致
    rows = API.rows(f"{V1}/test-cases", {"projectId": pa, "versionId": ctx["va1"]})
    codes = {row.get("versionCode") for row in rows}
    check("test-cases：versionId=vA1 的行 versionCode 必须全是 A1（attach_version_codes 一致）",
          codes == {f"ISOV-{ctx['tag']}-A1"},
          f"实际 versionCode={sorted(str(c) for c in codes)}", S2)

    # 「未挂版本」口径
    expect_ids(S3, "test-cases：versionId=__null__ 应能筛出未挂版本的用例（后端 NULL_FILTER 能力）",
               f"{V1}/test-cases", {"projectId": pa, "versionId": "__null__"},
               exp["test-cases"]["A@none"])
    expect_ids(S3, "test-cases：versionId=none（前端常用写法）应等价筛未挂版本",
               f"{V1}/test-cases", {"projectId": pa, "versionId": "none"},
               exp["test-cases"]["A@none"])
    expect_ids(S3, "requirements：versionId=__null__ 未挂版本口径",
               f"{V1}/requirements", {"projectId": pa, "versionId": "__null__"},
               exp["requirements"]["A@none"])
    expect_ids(S3, "bugs：versionId=none 未挂版本口径",
               f"{V1}/bugs", {"projectId": pa, "versionId": "none"}, exp["bugs"]["A@none"])
    none_rows = API.rows(f"{V1}/test-cases", {"projectId": pa, "versionId": "none"})
    null_rows = API.rows(f"{V1}/test-cases", {"projectId": pa, "versionId": "__null__"})
    note(f"未挂版本筛选：versionId=__null__ 与 versionId=none 现在同义（helpers.NULL_ALIAS 统一互译），"
         f"分别返回 {len(null_rows)} / {len(none_rows)} 行。")

    # 残留 versionId：前端切项目后未清 versionId
    expect_ids(S1, "残留 versionId：projectId=B + 上一项目的 versionId=vA1 不得返回任何行",
               f"{V1}/test-cases", {"projectId": pb, "versionId": ctx["va1"]}, set())
    expect_ids(S1, "残留 versionId：projectId=A + 已删除/不存在的 versionId 不得放宽成全量",
               f"{V1}/test-cases", {"projectId": pa, "versionId": 99999999}, set())

    # 版本趋势 / 对比
    compare = API.get(f"{V1}/reports/version-compare",
                      {"projectId": pa, "baseVersionId": ctx["va1"], "targetVersionId": ctx["va2"]}) or {}
    check("reports/version-compare：base 必须是 vA1 且只算 vA1 的 2 条用例",
          int(dig(compare, "base.versionId") or -1) == ctx["va1"]
          and int(dig(compare, "base.caseTotal") or -1) == 2,
          f"base={brief({k: dig(compare, f'base.{k}') for k in ('versionId', 'versionCode', 'caseTotal', 'bugTotal')})}", S1)
    check("reports/version-compare：target 必须是 vA2 且只算 vA2 的 1 条用例（不含未挂版本）",
          int(dig(compare, "target.versionId") or -1) == ctx["va2"]
          and int(dig(compare, "target.caseTotal") or -1) == 1
          and int(dig(compare, "target.bugTotal") or -1) == 1,
          f"target={brief({k: dig(compare, f'target.{k}') for k in ('versionId', 'caseTotal', 'bugTotal')})}", S1)
    expect_code(S1, "reports/version-compare：baseVersionId 属于 B 项目必须 140100", "GET",
                f"{V1}/reports/version-compare?{params_of({'projectId': pa, 'baseVersionId': ctx['vb1'], 'targetVersionId': ctx['va2']})}",
                ErrorCode.RESOURCE_NOT_FOUND)
    trends_a = API.get(f"{V1}/reports/trends", {"projectId": pa}) or {}
    trends_a_v2 = API.get(f"{V1}/reports/trends", {"projectId": pa, "versionId": ctx["va2"]}) or {}
    check("reports/trends：必须支持 versionId（当前静默忽略，趋势永远是全项目口径）",
          dig(trends_a_v2, "totals.newBugs") != dig(trends_a, "totals.newBugs"),
          f"传 versionId=vA2 后 totals={brief(trends_a_v2.get('totals'))} 与不传"
          f"totals={brief(trends_a.get('totals'))} 完全一致（reports.py:418-427 只取 projectId；"
          f"report_trends.py:73-188 无版本条件）", S2)
    trends_b = API.get(f"{V1}/reports/trends", {"projectId": pb}) or {}
    check("reports/trends：projectId 维度必须生效（A 新增缺陷 3、B 新增缺陷 1）",
          dig(trends_a, "totals.newBugs") == 3 and dig(trends_b, "totals.newBugs") == 1,
          f"A totals={brief(trends_a.get('totals'))}｜B totals={brief(trends_b.get('totals'))}", S1)


# ---------------- 场景 6：统计与图表 ----------------

def s_stats_charts(ctx: dict[str, Any]) -> None:
    pa, pb = ctx["pa"], ctx["pb"]

    expect_value(S1, "bugs/stats：projectId=A 只数 A 的 3 条缺陷", f"{V1}/bugs/stats",
                 {"projectId": pa}, "total", 3)
    expect_value(S1, "bugs/stats：projectId=B 只数 B 的 1 条缺陷", f"{V1}/bugs/stats",
                 {"projectId": pb}, "total", 1)
    expect_value(S2, "bugs/stats：必须支持 versionId（当前忽略，A/v2 应为 1）", f"{V1}/bugs/stats",
                 {"projectId": pa, "versionId": ctx["va2"]}, "total", 1)
    global_stats = API.get(f"{V1}/bugs/stats", {}) or {}
    note(f"bugs/stats 缺省 projectId 时返回全库口径 total={global_stats.get('total')}（含他人项目）。")

    expect_value(S4, "todos/stats：projectId=A 的 total=2", f"{V1}/todos/stats",
                 {"projectId": pa}, "summary.total", 2)

    overview = API.get(f"{V1}/workbench/overview", {"projectId": pa}) or {}
    metrics = overview.get("metrics") or {}
    assets = overview.get("assets") or {}
    check("workbench/overview：caseTotal 只算 A（=4）", metrics.get("caseTotal") == 4,
          f"实际 caseTotal={metrics.get('caseTotal')}", S1)
    check("workbench/overview：requirementTotal 只算 A（=4）", metrics.get("requirementTotal") == 4,
          f"实际 requirementTotal={metrics.get('requirementTotal')}", S1)
    check("workbench/overview：reportTotal 只算 A（=2）", metrics.get("reportTotal") == 2,
          f"实际 reportTotal={metrics.get('reportTotal')}", S1)
    check("workbench/overview：moduleTotal 必须按项目过滤（当前全库）",
          metrics.get("moduleTotal") == 3,
          f"期望 A 项目 3 个模块节点｜实际 moduleTotal={metrics.get('moduleTotal')}（全库计数，"
          f"workbench.py:127 无 scope_sql）", S1)
    check("workbench/overview：aiTaskTotal 与项目无关却摆在项目视图里",
          metrics.get("aiTaskTotal") in (None, 0),
          f"实际 aiTaskTotal={metrics.get('aiTaskTotal')}（biz_ai_task 无 project_id 列，"
          f"workbench.py:123）", S2)
    check("workbench/overview：knowledgeTotal 不得是全库已发布数",
          assets.get("knowledge") == 0,
          f"实际 assets.knowledge={assets.get('knowledge')}、knowledgeTotal="
          f"{metrics.get('knowledgeTotal')}（knowledge_repo.published_count() 不分项目，"
          f"workbench.py:176/187）", S1)
    check("workbench/overview：assets.project 是全局项目数（误导为项目内资产）",
          assets.get("project") in (None, 2),
          f"实际 assets.project={assets.get('project')}（ProjectRepository.count()，"
          f"workbench.py:183）", S2)
    check("workbench/overview：pendingKnowledge 必须是本项目待审数",
          overview.get("pendingKnowledge") == 0,
          f"实际 pendingKnowledge={overview.get('pendingKnowledge')}"
          f"（draft_repo.pending_count() 全局，workbench.py:191）", S1)
    recent = overview.get("recentTasks") or []
    check("workbench/overview：recentTasks 不得跨项目列出别人的 AI 任务",
          len(recent) == 0,
          f"实际返回 {len(recent)} 条：{brief([r.get('taskType') for r in recent])}"
          f"（AITaskRepository.list_all(None, None, limit=5)，workbench.py:142）", S1)
    overview_v2 = API.get(f"{V1}/workbench/overview", {"projectId": pa, "versionId": ctx["va2"]}) or {}
    check("workbench/overview：versionId=vA2 时 caseTotal 应为 1",
          (overview_v2.get("metrics") or {}).get("caseTotal") == 1,
          f"实际 caseTotal={(overview_v2.get('metrics') or {}).get('caseTotal')}（overview 只做了"
          f"case/bug/requirement/report 的 scope_sql，moduleTotal 等仍全库）", S2)

    wb_cov = API.get(f"{V1}/workbench/coverage", {"projectId": pa, "versionId": ctx["va2"]}) or {}
    check("workbench/coverage：versionId 必须生效（当前与不传 versionId 相同）",
          rows_of(wb_cov) != rows_of(API.get(f"{V1}/workbench/coverage", {"projectId": pa}) or {}),
          f"传 versionId=vA2 返回行数={len(rows_of(wb_cov))}，与不传一致（workbench.py:199-205 未取 versionId）",
          S2)
    wb_exec = API.get(f"{V1}/workbench/executions", {"projectId": pa, "versionId": ctx["va2"]}) or {}
    wb_exec_all = API.get(f"{V1}/workbench/executions", {"projectId": pa}) or {}
    check("workbench/executions：versionId 必须生效（当前忽略）",
          wb_exec != wb_exec_all or not wb_exec_all,
          f"传/不传 versionId 结果相同：{brief(wb_exec)}", S2)

    scripts_payload = API.get(f"{V1}/automation/scripts", {"projectId": pa}) or {}
    cat_counts = scripts_payload.get("categoryCounts") or {}
    check("automation/scripts：categoryCounts 必须按 projectId 统计",
          sum(int(v or 0) for v in cat_counts.values()) == 2,
          f"实际 categoryCounts={brief(cat_counts)}（AutomationScriptRepository.count_by_category()"
          f" 无项目过滤，automation.py:297）", S1)
    runs_a = API.get(f"{V1}/automation/runs", {"projectId": pa}) or {}
    runs_b = API.get(f"{V1}/automation/runs", {"projectId": pb}) or {}
    check("automation/runs：projectId 必须收敛执行记录（当前两边完全相同 = 全库）",
          (runs_a.get("total") == runs_b.get("total") == 0)
          or id_set(rows_of(runs_a)) != id_set(rows_of(runs_b)),
          f"projectId=A total={runs_a.get('total')}、projectId=B total={runs_b.get('total')}（白名单只有 "
          f"category/scriptId/status，automation.py:496-500；biz_automation_run 也没有 project_id 列，"
          f"需按 script 关联收敛）", S2)
    stats_a = (runs_a.get("categoryStats") or {}) if isinstance(runs_a.get("categoryStats"), dict) else {}
    stats_all = (API.get(f"{V1}/automation/runs", {}) or {}).get("categoryStats") or {}
    check("automation/runs：categoryStats 必须随 projectId 收敛（不得固定全库口径）",
          sum(int((v or {}).get("total") or 0) if isinstance(v, dict) else int(v or 0)
              for v in stats_a.values())
          <= sum(int((v or {}).get("total") or 0) if isinstance(v, dict) else int(v or 0)
                 for v in stats_all.values()),
          f"projectId=A 合计 {stats_a}｜全库 {stats_all}", S2)
    note(f"automation/runs 不带 projectId 时 categoryStats 仍是全局口径（执行留痕表按脚本归属收敛）："
         f"{brief(stats_all)}")

    kb_a = API.get(f"{V1}/knowledge/articles", {"projectId": pa}) or {}
    kb_b = API.get(f"{V1}/knowledge/articles", {"projectId": pb}) or {}
    check("knowledge/articles：projectId 必须在白名单里（A/B 结果应不同）",
          kb_a.get("total") != kb_b.get("total") or kb_a.get("total") == 0,
          f"A total={kb_a.get('total')}｜B total={kb_b.get('total')}｜"
          f"ARTICLE_FILTERS 无 projectId（knowledge.py:43），但 biz_knowledge_article 有 project_id 列", S1)
    kb_foreign = [row for row in rows_of(kb_a) if row.get("projectId") not in (None, pa)]
    check("knowledge/articles：返回体里不得出现别的项目的文章",
          not kb_foreign,
          f"projectId=A 时返回 {len(kb_foreign)} 条 projectId 不属于 A 的文章："
          f"{brief([(r.get('id'), r.get('projectId')) for r in kb_foreign][:8])}", S1)
    drafts_a = API.get(f"{V1}/knowledge/drafts", {"projectId": pa}) or {}
    drafts_b = API.get(f"{V1}/knowledge/drafts", {"projectId": pb}) or {}
    check("knowledge/drafts：待审池按项目区分（当前全库 + 全局 stats）",
          drafts_a.get("total") == drafts_b.get("total") == 0,
          f"A total={drafts_a.get('total')}｜B total={drafts_b.get('total')}｜"
          f"DRAFT_FILTERS 无 projectId（knowledge.py:44），stats 也全局："
          f"{brief(drafts_a.get('stats'))}", S2)
    badges = API.get(f"{V1}/badges", {}) or {}
    note(f"导航角标 /badges 是全局单表（sys_nav_badge 无 project_id），返回 {brief(badges)}；"
         f"项目视图里的角标无法按项目区分，属设计缺陷（需要加 project_id 列）。")

    search_a = API.get(f"{V1}/search", {"q": "ISOA-需求1", "projectId": pa}) or {}
    search_b = API.get(f"{V1}/search", {"q": "ISOA-需求1", "projectId": pb}) or {}
    check("search：projectId=B 时不得命中 A 项目的内容",
          int(search_b.get("total") or 0) == 0,
          f"q=ISOA-需求1&projectId=B 命中 {search_b.get('total')} 条：{brief(search_b.get('groups'))[:200]}", S1)
    check("search：projectId=A 时应能命中 A 的需求", int(search_a.get("total") or 0) >= 1,
          f"实际 total={search_a.get('total')}", S4)
    kb_search_all = API.get(f"{V1}/search", {"q": "测试", "types": "knowledge", "projectId": pa,
                                             "limit": 50}) or {}
    kb_search_b = API.get(f"{V1}/search", {"q": "测试", "types": "knowledge", "projectId": pb,
                                          "limit": 50}) or {}
    if int(kb_search_all.get("total") or 0) > 0:
        check("search：知识分组必须遵守 projectId（search_service 未传项目）",
              json.dumps(kb_search_all, sort_keys=True) != json.dumps(kb_search_b, sort_keys=True),
              f"types=knowledge 时 projectId=A 与 B 命中完全相同（{kb_search_all.get('total')} 条）："
              f"search_service.py:152-174 search_knowledge 无 project 条件", S1)
    else:
        note("search：当前库里没有命中知识分组的条目，未对知识分组做判定。")


# ---------------- 场景 7：导出 ----------------

def s_exports(ctx: dict[str, Any]) -> None:
    pa, pb = ctx["pa"], ctx["pb"]

    count, err = API.export_count(f"{V1}/test-cases/export", {"projectId": pa})
    check("test-cases/export：projectId=A 导出 4 条", count == 4,
          f"请求=GET {V1}/test-cases/export?projectId=A&format=csv｜期望 4｜实际 {count} {err}", S4)
    count_v1, err = API.export_count(f"{V1}/test-cases/export", {"projectId": pa, "versionId": ctx["va1"]})
    check("test-cases/export：versionId=vA1 只导出 2 条（已知嫌疑：忽略 versionId）", count_v1 == 2,
          f"请求=GET {V1}/test-cases/export?projectId=A&versionId=vA1&format=csv｜期望 X-AITP-Export-Count=2"
          f"（列表同条件是 2 条）｜实际 ={count_v1} {err} —— 导出 column_map 里没有 versionId"
          f"（cases.py:224-230），三种格式（xlsx/csv/json）共用同一 filters，全部忽略版本筛选", S1)
    count_b, _err = API.export_count(f"{V1}/test-cases/export", {"projectId": pb, "versionId": ctx["va1"]})
    check("test-cases/export：projectId=B + 跨项目 versionId 必须导出 0 条", count_b == 0,
          f"实际导出 {count_b} 条", S1)
    count_nop, _err = API.export_count(f"{V1}/test-cases/export", {})
    note(f"test-cases/export 缺省 projectId 直接被参数校验拒绝（require_int），"
         f"这是唯一一个强制项目维度的导出：实际返回条数={count_nop}")

    count, err = API.export_count(f"{V1}/requirements/export", {"projectId": pa})
    check("requirements/export：projectId=A 导出 4 条", count == 4,
          f"期望 4｜实际 {count} {err}", S1)
    count_v1, err = API.export_count(f"{V1}/requirements/export", {"projectId": pa, "versionId": ctx["va1"]})
    check("requirements/export：versionId 生效（导出 2 条）", count_v1 == 2,
          f"期望 2｜实际 {count_v1} {err} —— 作为用例导出对照：同一家写法（helpers.query_filters）"
          f"里白名单带 versionId 就能生效", S4)
    count_nop, _ = API.export_count(f"{V1}/requirements/export", {})
    check("requirements/export：缺省 projectId 不得导出全库需求（含他人项目）",
          count_nop == 4 or count_nop is None,
          f"缺省 projectId 时导出 {count_nop} 条（全库口径），期望只允许项目维度导出", S2)

    count, err = API.export_count(f"{V1}/bugs/export", {"projectId": pa})
    check("bugs/export：projectId=A 导出 3 条", count == 3, f"期望 3｜实际 {count} {err}", S1)
    count_v1, err = API.export_count(f"{V1}/bugs/export", {"projectId": pa, "versionId": ctx["va1"]})
    check("bugs/export：versionId 生效（1 条）", count_v1 == 1, f"期望 1｜实际 {count_v1} {err}", S4)
    count_nop, _ = API.export_count(f"{V1}/bugs/export", {})
    check("bugs/export：缺省 projectId 不得导出全库缺陷", count_nop == 3 or count_nop is None,
          f"缺省 projectId 导出 {count_nop} 条（全库）", S2)

    count, err = API.export_count(f"{V1}/todos/export", {"projectId": pa})
    check("todos/export：projectId=A 导出 2 条", count == 2, f"期望 2｜实际 {count} {err}", S1)

    count, err = API.export_count(f"{V1}/knowledge/articles/export", {"projectId": pa})
    kb_total = (API.get(f"{V1}/knowledge/articles", {"projectId": pa}) or {}).get("total")
    check("knowledge/articles/export：带 projectId 时导出条数必须与项目内条数一致",
          count == kb_total or count == 0,
          f"导出 {count} 条，但 projectId=A 的文章列表 total={kb_total} {err}", S1)

    count, err = API.export_count(f"{V1}/traceability/export", {"projectId": pa})
    check("traceability/export：A 项目导出 4 行需求矩阵", count == 4,
          f"期望 4｜实际 {count} {err}", S1)
    count_v2, err = API.export_count(f"{V1}/traceability/export", {"projectId": pa, "versionId": ctx["va2"]})
    check("traceability/export：versionId=vA2 只导出 1 行", count_v2 == 1,
          f"期望 1｜实际 {count_v2} {err}", S2)

    count, err = API.export_count(f"{V1}/test-runs/{ctx['runB']}/export", {"projectId": pa})
    check("test-runs/<B 轮次>/export?projectId=A 必须拒绝（越权导出他人结果）", count is None,
          f"实际导出 {count} 条：轮次导出未按 projectId 校验归属 {err}", S2)
    count_own, err = API.export_count(f"{V1}/test-runs/{ctx['runB']}/export", {"projectId": pb})
    check("test-runs/<B 轮次>/export?projectId=B 正常导出 1 条", count_own == 1,
          f"期望 1｜实际 {count_own} {err}", S2)
    count_nop, _err = API.export_count(f"{V1}/test-runs/{ctx['runB']}/export", {})
    note("test-runs/<id>/export 不带 projectId 时仍按主键导出：与详情接口同一口径"
         "（详情页与深链只有 id，强约束会打断功能），带 projectId 才校验归属；"
         f"UI 导出按钮已改为带 projectId。实际返回条数={count_nop}")


# ---------------- 场景 8：白名单缺参探针 ----------------

def s_missing_whitelist(ctx: dict[str, Any]) -> None:
    pa, pb = ctx["pa"], ctx["pb"]
    probes = (
        ("ai-records", "biz_ai_record 无 project_id，但可顺着 task_id 收敛；列表/统计已按项目过滤",
         False),
        ("lab/sessions", "biz_ai_session 无 project_id，顺着名下任务收敛；已按项目过滤", False),
        ("lab/tasks", "biz_ai_task 有 project_id（迁移 038），任务列表按项目过滤", False),
        ("tasks", "sys_task 无 project_id：作业队列忽略 projectId（tasks.py:16-38）", True),
        ("relations", "biz_entity_relation 无 project_id：关系是「两个实体之间的边」，跨项目边本身合法，"
                      "且前端没有消费该列表的页面（relations.py:33-48）", True),
        ("test-data/batches", "biz_test_data_batch 有 project_id + version_id，"
                              "但 FILTERS 只有 projectId（test_data.py:45）→ 版本维度不可筛", False),
        ("test-envs", "biz_test_env 无 project_id：环境是全局配置资源，列表无项目维度"
                      "（test_envs.py:40-50）", True),
    )
    for path, reason, by_design in probes:
        try:
            total_a = API.total(f"{V1}/{path}", {"projectId": pa})
            total_b = API.total(f"{V1}/{path}", {"projectId": pb})
        except AssertionError as exc:
            note(f"{path} 探针跳过：{exc}")
            continue
        if by_design:
            note(f"{path}：projectId=A 与 B 返回同样 {total_a} 行（{reason}）")
        else:
            check(f"{path}：有项目/版本列就必须按项目过滤",
                  total_a != total_b or total_a == 0,
                  f"projectId=A/B 都是 {total_a} 行 —— {reason}", S2)

    # 版本维度白名单缺失（列存在但接口不认）
    version_probes = (
        ("test-data/batches", "biz_test_data_batch.version_id 存在但 FILTERS 无 versionId"
                              "（test_data.py:45），且生成批次时未写入 version_id"),
    )
    for path, reason in version_probes:
        try:
            base = API.total(f"{V1}/{path}", {"projectId": pa})
            with_v = API.total(f"{V1}/{path}", {"projectId": pa, "versionId": ctx["va1"]})
        except AssertionError as exc:
            note(f"{path} 版本探针跳过：{exc}")
            continue
        check(f"{path}：传 versionId 必须收敛结果集（不能静默忽略）",
              with_v == base and base == 0 or with_v < base,
              f"传 versionId=vA1 前后行数不变（{base} → {with_v}）：{reason}", S3)


# ---------------- 场景 9：清理 ----------------

def s_cleanup(ctx: dict[str, Any]) -> None:
    if KEEP:
        note(f"AITP_AUDIT_KEEP=1：保留本次数据，前缀 ISOAUDIT-{ctx['tag']}，"
             f"项目 id={ctx['pa']}/{ctx['pb']}，可用 UI 手动删除。")
        return
    residue: list[tuple[str, int, str]] = []

    def drop(kind: str, pk: int) -> None:
        status, _headers, body = API.remove(kind, pk)
        code = int(body.get("code") or -1)
        if code not in (ErrorCode.SUCCESS, ErrorCode.RESOURCE_NOT_FOUND):
            residue.append((kind, pk, f"http={status} code={code} msg={body.get('message')}"))

    # 1) 业务资产（不含模块/版本/项目）
    asset_kinds = [k for k in KIND_ORDER if k not in ("module", "project_version", "project")]
    for kind in asset_kinds:
        for item_kind, pk in list(_created):
            if item_kind == kind:
                drop(kind, pk)
    # 2) 模块：必须先叶子后父节点，否则 140104 挡住
    for key in ("mAf", "mAc", "mB", "mA"):
        if ctx.get(key):
            drop("module", ctx[key])
    # 3) 版本：资产清空后才能删
    for key in ("va1", "va2", "vb1", "vb2"):
        if ctx.get(key):
            drop("project_version", ctx[key])
    # 4) 项目
    for key in ("pa", "pb"):
        if ctx.get(key):
            drop("project", ctx[key])

    if residue:
        note(f"未能删除 {len(residue)} 条审计数据，残留前缀 ISOAUDIT-{ctx['tag']}："
             + "; ".join(f"{kind}#{pk}: {why}" for kind, pk, why in residue[:12]))
    else:
        print("  [CLEAN] 本次造的审计数据已全部删除（残留前缀：无）")

    verify_targets = (
        ("requirements", ctx.get("pa")), ("test-cases", ctx.get("pa")), ("bugs", ctx.get("pa")),
        ("reports", ctx.get("pa")), ("sql-analyses", ctx.get("pa")), ("automation/scripts", ctx.get("pa")),
        ("test-plans", ctx.get("pa")), ("test-runs", ctx.get("pa")), ("todos", ctx.get("pa")),
        ("regressions", ctx.get("pa")),
    )
    if not ctx.get("pa"):
        note("夹具未建立，跳过清理核对。")
        return
    for path, project_key in verify_targets:
        left = API.rows(f"{V1}/{path}", {"projectId": project_key})
        check(f"清理后 {path}：A 项目已无审计数据", not left,
              f"残留 id={sorted(id_set(left))}", S4)
    for path in ("requirements", "test-cases", "bugs"):
        left = API.rows(f"{V1}/{path}", {"projectId": ctx["pb"]})
        check(f"清理后 {path}：B 项目已无审计数据", not left, f"残留 id={sorted(id_set(left))}", S4)

    code, _body = API.code("GET", f"{V1}/projects/{ctx['pa']}")
    check("清理后审计项目 A 已软删（详情 140100）", code == ErrorCode.RESOURCE_NOT_FOUND,
          f"实际 code={code}", S4)
    note("残留说明：sys_audit_log 会为每次写操作留痕（无法删除，属正常审计足迹）；"
         "adopt/关联等接口会全局 +N 导航角标（sys_nav_badge 无项目维度），"
         "如需清零可执行 POST /api/v1/badges/<section>/clear。")


# ---------------- 工具 ----------------

def flatten_module_ids(nodes: list[dict[str, Any]]) -> set[int]:
    found: set[int] = set()
    for node in nodes:
        if node.get("id") is not None:
            found.add(int(node["id"]))
        found |= flatten_module_ids(node.get("children") or [])
    return found


def collect_case_ids(nodes: list[dict[str, Any]]) -> set[int]:
    found: set[int] = set()
    for node in nodes:
        for case in node.get("cases") or []:
            if case.get("id") is not None:
                found.add(int(case["id"]))
        found |= collect_case_ids(node.get("children") or [])
    return found


def sum_counts(rows: list[dict[str, Any]]) -> int:
    return sum(int(row.get("case_count") or 0) for row in rows if isinstance(row, dict))


def preflight() -> None:
    status, _headers, body = API.call("GET", f"{V1}/health")
    if body.get("code") != ErrorCode.SUCCESS:
        print(f"[ABORT] 服务不可用：{BASE_URL}{V1}/health http={status} body={brief(body)}")
        sys.exit(2)
    auth = API.get(f"{V1}/auth/status", {}) or {}
    if auth.get("enabled") and not TOKEN:
        print("[ABORT] 服务开启了访问口令，请设置环境变量 AITP_ACCESS_TOKEN 后重跑。")
        sys.exit(2)
    print(f"[ENV] base={BASE_URL} health=ok auth.enabled={auth.get('enabled')} "
          f"keep={KEEP}（脚本不重启服务、不 kill 进程、只读写 ISOAUDIT-* 数据）")


API = Api(BASE_URL, TOKEN)


SECTIONS = (
    ("场景 0：夹具准备（2 项目 × 2 版本 + 资产）", build_fixtures),
    ("场景 1：项目维度 × 列表/分页接口", s_project_lists),
    ("场景 2：项目维度 × 树/下拉/关联视图", s_project_views),
    ("场景 3：详情接口越权读取（IDOR）", s_detail_idor),
    ("场景 4：跨项目引用写入（POST/PUT/关联/采纳）", s_cross_refs),
    ("场景 5：版本维度（筛选/互斥/未挂版本/跨项目/趋势对比）", s_version_dimension),
    ("场景 6：统计与图表（stats / overview / 角标 / 搜索）", s_stats_charts),
    ("场景 7：导出（xlsx / csv / json）", s_exports),
    ("场景 8：白名单缺 projectId/versionId 探针", s_missing_whitelist),
    ("场景 9：数据清理与残留核对", s_cleanup),
)


def main() -> int:
    global _failed
    ctx: dict[str, Any] = {"tag": uuid.uuid4().hex[:5].upper()}
    preflight()
    for title, func in SECTIONS:
        print(f"\n=== {title} ===")
        try:
            func(ctx)
        except AssertionError as exc:  # 夹具/请求异常也要把已造数据删掉
            _failed += 1
            _failures.append((S1, f"{title} 执行中断", str(exc)))
            print(f"  [FAIL] ({S1}) {title} 执行中断\n         {exc}")
            try:
                s_cleanup(ctx)
            except Exception as clean_exc:  # noqa: BLE001
                print(f"  [WARN] 清理失败：{clean_exc}")
            break
        except Exception as exc:  # noqa: BLE001
            _failed += 1
            _failures.append((S1, f"{title} 未预期异常", repr(exc)))
            print(f"  [FAIL] ({S1}) {title} 未预期异常\n         {exc!r}")
            try:
                s_cleanup(ctx)
            except Exception as clean_exc:  # noqa: BLE001
                print(f"  [WARN] 清理失败：{clean_exc}")
            break

    print("\n=== 汇总 ===")
    print(f"断言：{_passed + _failed} 条，通过 {_passed}，失败 {_failed}")
    # run_all_checks.py 靠这一行汇总结果，换成别的格式会让本只门禁在报表里显示成「没跑」
    print(f"PASS={_passed} FAIL={_failed}")
    print(f"数据前缀：ISOAUDIT-{ctx['tag']}（已 best-effort 清理）")
    if _failures:
        print("\n--- 失败清单（按严重度） ---")
        for severity in (S1, S2, S3, S4):
            bucket = [item for item in _failures if item[0] == severity]
            if not bucket:
                continue
            print(f"[{severity}] {len(bucket)} 条")
            for _sev, name, detail in bucket:
                print(f"  - {name}")
                if detail:
                    print(f"      {detail}")
    if _notes:
        print("\n--- 口径观察（非断言，供报告引用） ---")
        for line in _notes:
            print(f"  * {line}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
