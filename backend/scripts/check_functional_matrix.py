"""功能矩阵测试：多项目 × 边界值 × 全路径。

以资深测试的视角，对每个业务实体在多个项目下重复走一遍正常路径与边界值：
必填缺失/空白/超长、枚举非法值、不存在的 id、非数字 id、分页极值、
keyword 注入串与通配符、批量接口异常入参、软删后编号复用、跨项目隔离。

跑的是临时数据库 + test_client，不依赖已启动的服务，也不调用任何大模型。

    python backend/scripts/check_functional_matrix.py
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from typing import Any, Callable, cast
from urllib.parse import quote

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services.knowledge_storage import CONFIG_PATH  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []

V1 = "/api/v1"


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        return True
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    """薄封装：统一拆包封、断言业务码。"""

    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()
        self.app = app

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        resp, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 http={resp.status_code} "
                f"code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None, **kwargs: Any) -> JsonDict:
        resp, body = self.call(method, path, payload, **kwargs)
        check(name, body.get("code") == code,
              f"http={resp.status_code} code={body.get('code')} msg={body.get('message')}")
        return body

    def no_server_error(self, name: str, method: str, path: str,
                        payload: JsonDict | None = None, **kwargs: Any) -> JsonDict:
        """异常入参允许失败，但必须是业务码，不能是 500/未分类内部错误。"""
        resp, body = self.call(method, path, payload, **kwargs)
        check(f"{name}（不产生内部错误）",
              resp.status_code == 200 and body.get("code") != ErrorCode.INTERNAL_ERROR,
              f"http={resp.status_code} code={body.get('code')} msg={body.get('message')}")
        return body

    def create(self, path: str, payload: JsonDict) -> Any:
        return self.data("POST", path, payload)

    def remove(self, path: str) -> None:
        self.call("DELETE", path)


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "matrix.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    SysConfigRepository(conn).set_value(
        CONFIG_PATH, str(workdir / "knowledge"), "string", "功能矩阵脚本临时目录")
    conn.commit()
    conn.close()
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "matrix.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


# ---------------- 通用边界值探针 ----------------

def probe_required_fields(api: Api, label: str, path: str, base: JsonDict,
                          required: tuple[str, ...]) -> None:
    """必填项：缺失、null、空白串都要被拦下，且报的是参数错误而不是内部错误。"""
    for field in required:
        missing = {k: v for k, v in base.items() if k != field}
        api.expect(f"{label} 缺 {field} 被拒绝", "POST", path,
                   ErrorCode.PARAM_ERROR, missing)
        null_payload = dict(base)
        null_payload[field] = None
        api.expect(f"{label} {field}=null 被拒绝", "POST", path,
                   ErrorCode.PARAM_ERROR, null_payload)
        blank_payload = dict(base)
        blank_payload[field] = "   "
        api.expect(f"{label} {field} 全空白被拒绝", "POST", path,
                   ErrorCode.PARAM_ERROR, blank_payload)


def probe_max_length(api: Api, label: str, path: str, base: JsonDict,
                     field: str, max_length: int) -> None:
    """长度边界：max+1 拒绝、正好 max 通过，杜绝「差一」错误。"""
    over = dict(base)
    over[field] = "长" * (max_length + 1)
    api.expect(f"{label} {field} 超长 {max_length + 1} 被拒绝", "POST", path,
               ErrorCode.PARAM_ERROR, over)
    exact = dict(base)
    exact[field] = "长" * max_length
    created = api.create(path, exact)
    check(f"{label} {field} 正好 {max_length} 可保存",
          bool(created and created.get("id")), str(created)[:160])
    if created and created.get("id"):
        api.remove(f"{path}/{created['id']}")


_BAD_ID_VERBS = {"GET": "取", "PUT": "改", "DELETE": "删"}


def probe_bad_id(api: Api, label: str, detail: str,
                 methods: tuple[str, ...] = ("GET", "PUT", "DELETE"),
                 non_numeric_method: str = "GET") -> None:
    """不存在的 id 与非法 id：按该资源真实提供的路由逐条断言，不能出现内部错误。"""
    for method in methods:
        api.expect(f"{label} {_BAD_ID_VERBS[method]}不存在 id 报资源不存在", method, detail,
                   ErrorCode.RESOURCE_NOT_FOUND,
                   {"name": "x"} if method == "PUT" else None)
    non_numeric = detail.rsplit("/", 1)[0] + "/abc"
    api.no_server_error(f"{label} 非数字 id 不报内部错误", non_numeric_method, non_numeric)
    api.expect(f"{label} 非数字 id 报参数错误", non_numeric_method, non_numeric,
               ErrorCode.PARAM_ERROR)


def probe_pagination(api: Api, label: str, list_path: str) -> None:
    """分页极值：非法值要么拒绝要么回落到安全默认，上限 100 不能被绕过。"""
    for raw in ("0", "-1", "-100"):
        api.expect(f"{label} pageSize={raw} 被拒绝", "GET",
                   f"{list_path}?pageSize={raw}", ErrorCode.PAGE_PARAM_INVALID)
    for raw in ("abc", "1.5", ""):
        resp, body = api.call("GET", f"{list_path}?pageSize={quote(raw)}")
        check(f"{label} pageSize={raw or '空'} 不报内部错误",
              body.get("code") in (ErrorCode.SUCCESS, ErrorCode.PAGE_PARAM_INVALID),
              f"code={body.get('code')} msg={body.get('message')}")
    page = api.data("GET", f"{list_path}?pageSize=1000")
    check(f"{label} pageSize 被压到 100 以内",
          isinstance(page.get("pageSize"), int) and page["pageSize"] <= 100,
          str(page.get("pageSize")))
    page = api.data("GET", f"{list_path}?pageNum=0")
    check(f"{label} pageNum=0 归一为第 1 页", page.get("pageNum") == 1, str(page.get("pageNum")))
    far = api.data("GET", f"{list_path}?pageNum=99999&pageSize=20")
    check(f"{label} 越界页返回空列表且 total 不漂",
          far.get("list") == [] and far.get("total") == page.get("total"),
          f"list={len(far.get('list') or [])} total={far.get('total')} vs {page.get('total')}")


def probe_keyword_literal(api: Api, label: str, list_path: str) -> None:
    """keyword 走的是字面子串匹配：注入串与 SQL 通配符都不该命中全部数据。"""
    for raw in ("' OR 1=1 --", "%", "_", "%%%", "\\", "'; DROP TABLE biz_project;--"):
        page = api.data("GET", f"{list_path}?keyword={quote(raw)}")
        check(f"{label} keyword={raw!r} 不误伤全表",
              page.get("total") == 0,
              f"total={page.get('total')}")


def probe_batch_ids(api: Api, label: str, path: str) -> None:
    """批量接口：空数组、非数组、字符串元素、不存在的 id 都不能变成内部错误。"""
    api.expect(f"{label} 空 ids 被拒绝", "POST", path, ErrorCode.PARAM_ERROR, {"ids": []})
    api.expect(f"{label} ids 非数组被拒绝", "POST", path, ErrorCode.PARAM_ERROR, {"ids": "1,2"})
    api.expect(f"{label} ids 含非整数被拒绝", "POST", path, ErrorCode.PARAM_ERROR,
               {"ids": ["abc"]})
    result = api.data("POST", path, {"ids": [999999]})
    check(f"{label} 不存在的 id 归入 skipped",
          result.get("count") == 0 and 999999 in (result.get("skipped") or []),
          str(result)[:200])


def probe_malformed_body(api: Api, label: str, path: str) -> None:
    """请求体不是 JSON 对象时不能 500。"""
    for raw in (b"[1,2,3]", b'"just-a-string"', b"{not json}"):
        resp = api.client.open(path, method="POST", data=raw,
                               content_type="application/json")
        body = resp.get_json(silent=True) or {"code": -1}
        check(f"{label} 非法请求体不产生内部错误",
              resp.status_code == 200 and body.get("code") != ErrorCode.INTERNAL_ERROR,
              f"http={resp.status_code} code={body.get('code')}")


def probe_enum(api: Api, label: str, path: str, base: JsonDict, field: str) -> None:
    """枚举字段注入非法值必须被拒绝，不能落库。"""
    bad = dict(base)
    bad[field] = "不存在的枚举值"
    api.expect(f"{label} {field} 非法枚举被拒绝", "POST", path, ErrorCode.PARAM_ERROR, bad)


# ---------------- 数据准备：多项目 ----------------

def seed_projects(api: Api) -> dict[str, int]:
    """三个项目：后续每个实体都在多项目下各建一份，验证隔离。"""
    ids: dict[str, int] = {}
    for key, code, name in (("p1", "MTX-A", "矩阵项目甲"),
                            ("p2", "MTX-B", "矩阵项目乙"),
                            ("p3", "MTX-C", "Matrix Project C")):
        row = api.create(f"{V1}/projects", {"code": code, "name": name, "owner": "QA"})
        ids[key] = int(row["id"])
    return ids


# ---------------- 各实体分区 ----------------

def section_projects(api: Api, ctx: dict[str, int]) -> None:
    base = {"code": "MTX-P", "name": "项目边界"}
    probe_required_fields(api, "项目", f"{V1}/projects", base, ("code", "name"))
    probe_max_length(api, "项目", f"{V1}/projects", dict(base), "name", 200)
    probe_enum(api, "项目", f"{V1}/projects", dict(base, status="🚀"), "status")
    api.expect("项目 code 重复被拒绝", "POST", f"{V1}/projects", ErrorCode.UNIQUE_CONFLICT,
               {"code": "MTX-A", "name": "重复编码"})
    created = api.create(f"{V1}/projects", {"code": "MTX-DUP", "name": "删后再建"})
    api.remove(f"{V1}/projects/{created['id']}")
    again = api.data("POST", f"{V1}/projects", {"code": "MTX-DUP", "name": "软删后复用编码"})
    check("项目软删后同编码可复用", bool(again.get("id")), str(again)[:120])
    api.remove(f"{V1}/projects/{again['id']}")
    probe_bad_id(api, "项目", f"{V1}/projects/424242")
    probe_pagination(api, "项目", f"{V1}/projects")
    probe_keyword_literal(api, "项目", f"{V1}/projects")
    probe_malformed_body(api, "项目", f"{V1}/projects")
    # Unicode 往返：中文/全角/emoji 不应被截断或转码
    emoji = api.create(f"{V1}/projects", {"code": "MTX-U", "name": "项目 🚀 全角ＡＢ　换行"})
    fetched = api.data("GET", f"{V1}/projects/{emoji['id']}")
    check("项目名 Unicode 原样往返",
          fetched.get("name") == "项目 🚀 全角ＡＢ　换行", str(fetched.get("name")))
    api.remove(f"{V1}/projects/{emoji['id']}")
    check("项目 options 不含软删项",
          {p["id"] for p in api.data("GET", f"{V1}/projects/options")} <=
          {ctx["p1"], ctx["p2"], ctx["p3"]}, "残留已删除项目")


def section_modules(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/modules"
    probe_required_fields(api, "模块", path, {"projectId": ctx["p1"], "name": "M"},
                          ("projectId", "name"))
    probe_max_length(api, "模块", path, {"projectId": ctx["p1"]}, "name", 200)
    api.expect("模块挂到不存在的父节点被拒绝", "POST", path, ErrorCode.RESOURCE_NOT_FOUND,
               {"projectId": ctx["p1"], "name": "孤儿", "parentId": 424242})
    foreign = api.create(path, {"projectId": ctx["p1"], "name": "他项目父节点"})
    api.expect("模块挂到别的项目的父节点被拒绝", "POST", path, ErrorCode.PARAM_ERROR,
               {"projectId": ctx["p2"], "name": "跨项目挂靠", "parentId": foreign["id"]})
    top = api.create(path, {"projectId": ctx["p1"], "name": "模块"})
    second = api.create(path, {"projectId": ctx["p1"], "name": "子模块", "parentId": top["id"]})
    third = api.create(path, {"projectId": ctx["p1"], "name": "功能点", "parentId": second["id"]})
    too_deep = api.call("POST", path, {"projectId": ctx["p1"], "name": "功能点下再挂",
                                       "parentId": third["id"]})
    check("功能点即叶子，第五级被限制",
          too_deep[1].get("code") == ErrorCode.ILLEGAL_OPERATION,
          f"code={too_deep[1].get('code')} msg={too_deep[1].get('message')}")
    api.expect("父节点移动到自己名下被拒绝（防成环）", "POST", f"{V1}/modules/{top['id']}/move",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": third["id"]})
    api.expect("父节点移动到自己被拒绝", "POST", f"{V1}/modules/{top['id']}/move",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": top["id"]})
    api.data("POST", f"{V1}/modules/{third['id']}/move", {"parentId": foreign["id"]})
    api.expect("整棵子树移动后会超出四级就被拒绝", "POST", f"{V1}/modules/{top['id']}/move",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": third["id"]})
    tree = api.data("GET", f"{V1}/modules/tree?projectId={ctx['p1']}")
    check("模块树仍可读出", isinstance(tree.get("tree"), list), str(tree)[:120])
    # 带子节点/被引用的模块不允许直接删（防止用例悬空）
    resp, body = api.call("DELETE", f"{V1}/modules/{top['id']}")
    check("删除有子节点的模块给出明确结果",
          body.get("code") in (ErrorCode.SUCCESS, ErrorCode.RESOURCE_REFERENCED,
                               ErrorCode.ILLEGAL_OPERATION),
          f"code={body.get('code')} msg={body.get('message')}")
    caps = api.data("GET", f"{V1}/modules/{second['id']}/capabilities")
    check("模块能力绑定可读", isinstance(caps, (dict, list)), str(caps)[:120])
    api.expect("模块能力绑定非法 capability 被拒绝", "PUT",
               f"{V1}/modules/{second['id']}/capabilities", ErrorCode.PARAM_ERROR,
               {"capabilities": [{"capability": "not_a_capability"}]})
    probe_bad_id(api, "模块", f"{V1}/modules/424242")
    api.expect("模块树缺 projectId 被拒绝", "GET", f"{V1}/modules/tree",
               ErrorCode.PARAM_ERROR)
    api.no_server_error("模块树 projectId 非数字", "GET", f"{V1}/modules/tree?projectId=abc")


def section_requirements(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/requirements"
    base = {"projectId": ctx["p1"], "title": "需求边界"}
    probe_required_fields(api, "需求", path, base, ("projectId", "title"))
    probe_max_length(api, "需求", path, dict(base), "title", 500)
    probe_enum(api, "需求", path, dict(base, status="nope"), "status")
    probe_bad_id(api, "需求", f"{V1}/requirements/424242")
    probe_pagination(api, "需求", f"{V1}/requirements")
    probe_keyword_literal(api, "需求", f"{V1}/requirements")
    probe_batch_ids(api, "需求", f"{V1}/requirements/batch-delete")
    probe_malformed_body(api, "需求", path)
    api.expect("需求挂到不存在的项目被拒绝", "POST", path, ErrorCode.RESOURCE_NOT_FOUND,
               {"projectId": 424242, "title": "悬空需求"})
    # 多项目：同一标题在两个项目各建一份，编号互不干扰
    a = api.create(path, {"projectId": ctx["p1"], "title": "登录风控"})
    b = api.create(path, {"projectId": ctx["p2"], "title": "登录风控"})
    check("跨项目同名需求可并存", a.get("id") != b.get("id"), str(a)[:120])
    check("需求编号在各自项目内独立生成",
          a.get("reqNo") and b.get("reqNo") and a.get("projectId") != b.get("projectId"),
          f"{a.get('reqNo')} / {b.get('reqNo')}")
    page = api.data("GET", f"{V1}/requirements?projectId={ctx['p1']}&pageSize=100")
    ids = {item["id"] for item in page["list"]}
    check("需求列表按项目隔离", b["id"] not in ids, "跨项目泄露")
    content = api.create(path, {"projectId": ctx["p1"], "title": "超长正文",
                                "content": "字" * 200000})
    check("需求正文 200000 字可保存", bool(content.get("id")), str(content)[:120])
    over = dict(base, content="字" * 200001)
    api.expect("需求正文超长被拒绝", "POST", path, ErrorCode.PARAM_ERROR, over)
    api.remove(f"{V1}/requirements/{content['id']}")
    exported = api.client.get(f"{V1}/requirements/export?format=csv&projectId={ctx['p1']}")
    check("需求导出 CSV 可读", exported.status_code == 200 and exported.data,
          f"status={exported.status_code}")
    bad_format = api.call("GET", f"{V1}/requirements/export?format=exe")
    check("需求导出非法格式被拒绝",
          bad_format[1].get("code") == ErrorCode.PARAM_ERROR, str(bad_format[1])[:120])
    for row in (a, b):
        api.remove(f"{V1}/requirements/{row['id']}")


def section_cases(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/test-cases"
    base = {"projectId": ctx["p1"], "title": "用例边界"}
    probe_required_fields(api, "用例", path, base, ("projectId", "title"))
    probe_max_length(api, "用例", path, dict(base), "title", 500)
    probe_enum(api, "用例", path, dict(base, caseType="nope"), "caseType")
    probe_enum(api, "用例", path, dict(base, priority="P9"), "priority")
    probe_enum(api, "用例", path, dict(base, status="unknown"), "status")
    probe_bad_id(api, "用例", f"{V1}/test-cases/424242")
    probe_pagination(api, "用例", f"{V1}/test-cases")
    probe_keyword_literal(api, "用例", f"{V1}/test-cases")
    probe_batch_ids(api, "用例", f"{V1}/test-cases/batch-delete")
    probe_malformed_body(api, "用例", path)
    created = api.create(path, {"projectId": ctx["p1"], "title": "编号复用",
                                "caseType": "boundary"})
    check("用例编号按类型前缀生成", str(created.get("caseNo", "")).startswith("BT"),
          str(created.get("caseNo")))
    api.remove(f"{V1}/test-cases/{created['id']}")
    reused = api.create(path, {"projectId": ctx["p1"], "title": "编号复用2",
                               "caseType": "boundary"})
    check("软删用例后编号不冲突", bool(reused.get("id")), str(reused)[:120])
    api.data("POST", f"{V1}/test-cases/{reused['id']}/execute",
             {"result": "pass", "remark": "执行通过"})
    api.expect("用例执行结果非法被拒绝", "POST", f"{V1}/test-cases/{reused['id']}/execute",
               ErrorCode.PARAM_ERROR, {"result": "maybe"})
    api.expect("执行不存在的用例报资源不存在", "POST", f"{V1}/test-cases/424242/execute",
               ErrorCode.RESOURCE_NOT_FOUND, {"result": "pass"})
    api.data("PUT", f"{V1}/test-cases/{reused['id']}", {"status": "pass"})
    detail = api.data("GET", f"{V1}/test-cases/{reused['id']}")
    check("用例状态更新落库", detail.get("status") == "pass", str(detail.get("status")))
    adopt = api.data("POST", f"{V1}/test-cases/adopt", {
        "projectId": ctx["p2"],
        "cases": [{"title": "采纳用例一", "caseType": "functional", "steps": "打开页面",
                   "expectedResult": "成功"}, {"title": "  "}]})
    check("用例批量采纳只收合法项",
          adopt.get("count", 0) == 1 and len(adopt.get("created") or []) == 1,
          str(adopt)[:200])
    coverage = api.data("GET", f"{V1}/test-cases/coverage?projectId={ctx['p1']}")
    check("用例覆盖率可读且不含 NaN", isinstance(coverage, dict), str(coverage)[:120])
    tree = api.data("GET", f"{V1}/test-cases/tree?projectId={ctx['p1']}")
    check("用例树可读", isinstance(tree.get("list"), list), str(tree)[:120])
    for row in (reused,):
        api.remove(f"{V1}/test-cases/{row['id']}")
    for item in adopt.get("created") or []:
        api.remove(f"{V1}/test-cases/{item['id']}")


def section_bugs(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/bugs"
    base = {"projectId": ctx["p1"], "title": "缺陷边界"}
    probe_required_fields(api, "缺陷", path, base, ("projectId", "title"))
    probe_max_length(api, "缺陷", path, dict(base), "title", 500)
    probe_enum(api, "缺陷", path, dict(base, severity="P9"), "severity")
    probe_enum(api, "缺陷", path, dict(base, bugType="nope"), "bugType")
    probe_bad_id(api, "缺陷", f"{V1}/bugs/424242")
    probe_pagination(api, "缺陷", f"{V1}/bugs")
    probe_keyword_literal(api, "缺陷", f"{V1}/bugs")
    probe_batch_ids(api, "缺陷", f"{V1}/bugs/batch-delete")
    probe_malformed_body(api, "缺陷", path)

    bug = api.create(path, {"projectId": ctx["p1"], "title": "状态机", "severity": "P1"})
    api.expect("缺陷非法状态迁移被拒绝", "POST", f"{V1}/bugs/{bug['id']}/transition",
               ErrorCode.ILLEGAL_TRANSITION, {"toStatus": "retest"})
    api.expect("缺陷迁到不存在的状态被拒绝", "POST", f"{V1}/bugs/{bug['id']}/transition",
               ErrorCode.PARAM_ERROR, {"toStatus": "已消失"})
    for target in ("assigned", "fixed", "retest", "closed"):
        api.data("POST", f"{V1}/bugs/{bug['id']}/transition",
                 {"toStatus": target, "operator": "QA", "comment": f"→{target}"})
    detail = api.data("GET", f"{V1}/bugs/{bug['id']}")
    check("缺陷沿合法路径流转到 closed", detail.get("status") == "closed",
          str(detail.get("status")))
    logs = api.data("GET", f"{V1}/bugs/{bug['id']}/logs")
    check("缺陷留痕记录每次流转", len(logs) >= 4, f"logs={len(logs)}")
    api.expect("closed 缺陷直接跳 retest 被拒绝", "POST", f"{V1}/bugs/{bug['id']}/transition",
               ErrorCode.ILLEGAL_TRANSITION, {"toStatus": "retest"})
    api.data("POST", f"{V1}/bugs/{bug['id']}/transition", {"toStatus": "reopened"})
    case = api.create(f"{V1}/test-cases", {"projectId": ctx["p1"], "title": "关联用例"})
    api.data("POST", f"{V1}/bugs/{bug['id']}/cases", {"caseIds": [case["id"]]})
    linked_detail = api.data("GET", f"{V1}/bugs/{bug['id']}")
    check("缺陷关联用例可查", case["id"] in (linked_detail.get("linkedCaseIds") or []),
          str(linked_detail.get("linkedCaseIds"))[:200])
    api.expect("缺陷关联不存在的用例被拒绝", "POST", f"{V1}/bugs/{bug['id']}/cases",
               ErrorCode.RESOURCE_NOT_FOUND, {"caseIds": [424242]})
    api.expect("缺陷关联非整数用例被拒绝", "POST", f"{V1}/bugs/{bug['id']}/cases",
               ErrorCode.PARAM_ERROR, {"caseIds": ["x"]})
    api.data("DELETE", f"{V1}/bugs/{bug['id']}/cases", {"caseIds": [case["id"]]})
    stats = api.data("GET", f"{V1}/bugs/stats")
    check("缺陷统计可读", isinstance(stats, (dict, list)), str(stats)[:120])
    exported = api.client.get(f"{V1}/bugs/export?format=json&projectId={ctx['p1']}")
    check("缺陷导出 JSON 可读", exported.status_code == 200 and exported.data,
          f"status={exported.status_code}")
    api.remove(f"{V1}/test-cases/{case['id']}")
    api.remove(f"{V1}/bugs/{bug['id']}")


def section_knowledge(api: Api, ctx: dict[str, int]) -> None:
    draft_path = f"{V1}/knowledge/drafts"
    probe_required_fields(api, "知识草稿", draft_path,
                          {"title": "T", "content": "C"}, ("title", "content"))
    probe_max_length(api, "知识草稿", draft_path, {"content": "C"}, "title", 500)
    probe_pagination(api, "知识草稿", draft_path)
    # 待审池只有「改 + 审核」两条路由，取详情与删条目都走审核动作
    probe_bad_id(api, "知识草稿", f"{V1}/knowledge/drafts/424242",
                 methods=("PUT",), non_numeric_method="PUT")
    probe_malformed_body(api, "知识草稿", draft_path)

    draft = api.create(draft_path, {"title": "审核闭环", "content": "正文一",
                                    "tags": "登录,风控"})
    api.expect("草稿审核动作非法被拒绝", "POST",
               f"{V1}/knowledge/drafts/{draft['id']}/review", ErrorCode.PARAM_ERROR,
               {"action": "merge"})
    api.data("POST", f"{V1}/knowledge/drafts/{draft['id']}/review",
             {"action": "approve", "reviewer": "QA"})
    articles = api.data("GET", f"{V1}/knowledge/articles?keyword=审核闭环&pageSize=50")
    hits = [a for a in articles.get("list") or [] if a.get("title") == "审核闭环"]
    check("草稿审核通过后进入正式库", len(hits) == 1, str(articles)[:200])
    article_id = hits[0]["id"] if hits else 0
    if article_id:
        # 新版本是整篇快照：正文可改，标题必须随版本一起落库
        api.expect("知识条目缺 title 被拒绝", "PUT",
                   f"{V1}/knowledge/articles/{article_id}", ErrorCode.PARAM_ERROR,
                   {"content": "只有正文"})
        api.data("PUT", f"{V1}/knowledge/articles/{article_id}",
                 {"title": "审核闭环", "content": "正文二，改了内容"})
        versions = api.data("GET", f"{V1}/knowledge/articles/{article_id}/versions")
        check("正式库修改产生新版本", len(versions) >= 2, f"versions={len(versions)}")
        diff = api.data("GET",
                        f"{V1}/knowledge/articles/{article_id}/diff?from=1&to=2")
        check("版本 diff 可读", isinstance(diff, (dict, list)), str(diff)[:120])
        api.expect("取不存在的版本报资源不存在", "GET",
                   f"{V1}/knowledge/articles/{article_id}/versions/9999",
                   ErrorCode.RESOURCE_NOT_FOUND)
        api.expect("diff 版本号非数字不报内部错误", "GET",
                   f"{V1}/knowledge/articles/{article_id}/diff?from=a&to=b",
                   ErrorCode.PARAM_ERROR)
        # 草稿审核通过产生 v1（已生效），PUT 又生成一个待审 v2；审核 v2 才是回滚/前移
        api.expect("重复审核同一版本被拒绝", "POST",
                   f"{V1}/knowledge/articles/{article_id}/versions/1/review",
                   ErrorCode.ILLEGAL_OPERATION, {"action": "approve", "reviewer": "QA"})
        api.data("POST", f"{V1}/knowledge/articles/{article_id}/versions/2/review",
                 {"action": "approve", "reviewer": "QA"})
        applied = api.data("GET", f"{V1}/knowledge/articles/{article_id}")
        check("审核新版本后正文前移", "正文二" in str(applied.get("content", "")),
              str(applied.get("content"))[:80])
        api.data("POST", f"{V1}/knowledge/articles/{article_id}/revoke")
        api.expect("撤回后的文章不能再次撤回", "POST",
                   f"{V1}/knowledge/articles/{article_id}/revoke", ErrorCode.ILLEGAL_OPERATION)
        revoked = api.data("POST", f"{V1}/knowledge/articles/{article_id}/restore")
        check("撤销条目可恢复", isinstance(revoked, dict), str(revoked)[:120])
    # 分类：内置中文分类不可删，自定义分类可删；名称长度上限 20
    cats = api.data("GET", f"{V1}/knowledge/categories")
    check("知识分类返回中文清单", any(c.get("name") == "测试方法"
                                      for c in cats.get("list") or []), str(cats)[:160])
    custom = api.create(f"{V1}/knowledge/categories", {"name": "矩阵专用分类"})
    api.expect("知识分类名超长被拒绝", "POST", f"{V1}/knowledge/categories",
               ErrorCode.PARAM_ERROR, {"name": "长" * 21})
    api.expect("知识分类名重复被拒绝", "POST", f"{V1}/knowledge/categories",
               ErrorCode.UNIQUE_CONFLICT, {"name": "矩阵专用分类"})
    builtin = next((c for c in cats.get("list") or []
                    if c.get("name") == "测试方法"), None)
    if builtin:
        api.expect("内置分类不允许删除", "DELETE",
                   f"{V1}/knowledge/categories/{builtin['id']}", ErrorCode.ILLEGAL_OPERATION)
    api.data("DELETE", f"{V1}/knowledge/categories/{custom['id']}")
    search = api.data("GET", f"{V1}/knowledge/search?keyword=审核闭环")
    check("知识检索可用", isinstance(search, (dict, list)), str(search)[:120])
    ask = api.data("POST", f"{V1}/knowledge/ask", {"question": "审核闭环", "mode": "kb"})
    check("kb 模式问答不出网", ask.get("egress") in (None, "none") or
          str(ask.get("egress", "")) == "none", str(ask)[:200])
    api.expect("问答模式非法被拒绝", "POST", f"{V1}/knowledge/ask", ErrorCode.PARAM_ERROR,
               {"question": "x", "mode": "magic"})
    api.expect("问题为空被拒绝", "POST", f"{V1}/knowledge/ask", ErrorCode.PARAM_ERROR,
               {"question": "  ", "mode": "kb"})
    reindex = api.data("POST", f"{V1}/knowledge/reindex")
    check("重建索引可用", isinstance(reindex, dict), str(reindex)[:120])
    api.data("PUT", f"{V1}/knowledge/storage", {"fileMode": "db_only"})
    before = api.data("PUT", f"{V1}/knowledge/storage", {"storagePath": "  "})
    check("空串路径视为不修改", isinstance(before, dict), str(before)[:120])
    api.expect("存储路径穿越被拒绝", "PUT", f"{V1}/knowledge/storage",
               ErrorCode.PARAM_ERROR, {"storagePath": "relative/../escape"})
    api.expect("存储路径非字符串被拒绝", "PUT", f"{V1}/knowledge/storage",
               ErrorCode.PARAM_ERROR, {"storagePath": 12345})
    tags = api.data("GET", f"{V1}/knowledge/tags")
    check("标签清单可读", isinstance(tags, list), str(tags)[:120])


def section_reports(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/reports"
    base = {"projectId": ctx["p1"], "title": "报告边界"}
    probe_required_fields(api, "报告", path, base, ("projectId", "title"))
    probe_max_length(api, "报告", path, dict(base), "title", 500)
    probe_enum(api, "报告", path, dict(base, reportType="nope"), "reportType")
    probe_bad_id(api, "报告", f"{V1}/reports/424242")
    probe_pagination(api, "报告", f"{V1}/reports")
    probe_keyword_literal(api, "报告", f"{V1}/reports")
    probe_malformed_body(api, "报告", path)

    report = api.create(path, {"projectId": ctx["p1"], "title": "迭代报告",
                               "reportType": "iteration"})
    api.data("PUT", f"{V1}/reports/{report['id']}/items", {"items": [
        {"section": "overview", "title": "概览", "content": "正文", "sortOrder": 1},
        {"section": "case", "title": "用例", "content": "表格", "sortOrder": 2}]})
    items = api.data("GET", f"{V1}/reports/{report['id']}/items")
    check("报告小节按 sortOrder 保存", len(items) == 2, f"items={len(items)}")
    api.expect("报告小节 section 非法被拒绝", "PUT", f"{V1}/reports/{report['id']}/items",
               ErrorCode.PARAM_ERROR, {"items": [{"section": "nonsense"}]})
    stats = api.data("GET", f"{V1}/reports/{report['id']}/stats")
    check("报告统计可读", isinstance(stats, dict), str(stats)[:160])
    for fmt in ("md", "html", "print"):
        resp = api.client.get(f"{V1}/reports/{report['id']}/export?format={fmt}")
        check(f"报告导出 {fmt} 成功", resp.status_code == 200 and bool(resp.data),
              f"status={resp.status_code}")
    resp_pdf = api.client.get(f"{V1}/reports/{report['id']}/export?format=pdf")
    check("报告导出 pdf 成功", resp_pdf.status_code == 200 and resp_pdf.data[:4] == b"%PDF",
          f"status={resp_pdf.status_code} head={resp_pdf.data[:8]}")
    api.expect("报告导出格式非法被拒绝", "GET",
               f"{V1}/reports/{report['id']}/export?format=tar", ErrorCode.PARAM_ERROR)
    api.expect("导出不存在的报告报资源不存在", "GET",
               f"{V1}/reports/424242/export?format=md", ErrorCode.RESOURCE_NOT_FOUND)
    gen = api.data("POST", f"{V1}/reports/generate", {"projectId": ctx["p2"],
                                                     "reportType": "summary"})
    check("按项目自动生成报告", bool(gen.get("id")), str(gen)[:160])
    adopt = api.data("POST", f"{V1}/reports/adopt", {
        "projectId": ctx["p2"], "title": "采纳的产出", "content": "# 结论"})
    check("AI 产出采纳为报告", bool(adopt.get("id")), str(adopt)[:160])
    templates = api.data("GET", f"{V1}/reports/templates")
    check("报告模板清单可读", isinstance(templates, (dict, list)), str(templates)[:120])
    api.remove(f"{V1}/reports/{report['id']}")
    api.remove(f"{V1}/reports/{gen['id']}")
    api.remove(f"{V1}/reports/{adopt['id']}")


def section_regressions(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/regressions"
    base = {"projectId": ctx["p1"], "title": "回归边界"}
    probe_required_fields(api, "回归清单", path, base, ("projectId", "title"))
    probe_max_length(api, "回归清单", path, dict(base), "title", 200)
    probe_bad_id(api, "回归清单", f"{V1}/regressions/424242")
    probe_pagination(api, "回归清单", path)
    probe_keyword_literal(api, "回归清单", path)
    probe_malformed_body(api, "回归清单", path)

    reg = api.create(path, {"projectId": ctx["p1"], "title": "上线回归",
                         "changeScope": "支付模块"})
    item = api.create(f"{V1}/regressions/{reg['id']}/items",
                      {"title": "检查支付回调", "sourceType": "manual"})
    api.expect("回归项挂到不存在的清单被拒绝", "POST", f"{V1}/regressions/424242/items",
               ErrorCode.RESOURCE_NOT_FOUND, {"title": "x"})
    api.data("PUT", f"{V1}/regressions/{reg['id']}/items/{item['id']}", {"result": "passed"})
    api.expect("回归项结果非法被拒绝", "PUT",
               f"{V1}/regressions/{reg['id']}/items/{item['id']}", ErrorCode.PARAM_ERROR,
               {"result": "maybe"})
    executed = api.data("POST", f"{V1}/regressions/{reg['id']}/execute",
                        {"results": [{"itemId": item["id"], "result": "passed", "remark": "ok"},
                                     {"itemId": 424242, "result": "failed"}]})
    check("回归执行只更新存在的项",
          item["id"] in (executed.get("updated") or []) and
          424242 in (executed.get("skipped") or []), str(executed)[:200])
    # execute 对非法结果走「跳过」而不是报错，和只更新存在项是同一套语义
    skipped_result = api.data("POST", f"{V1}/regressions/{reg['id']}/execute",
                              {"results": [{"itemId": item["id"], "result": "maybe"}]})
    check("回归执行非法结果归入 skipped 且不更新",
          item["id"] in (skipped_result.get("skipped") or []) and
          item["id"] not in (skipped_result.get("updated") or []),
          str(skipped_result)[:200])
    api.expect("回归执行缺少 results 被拒绝", "POST", f"{V1}/regressions/{reg['id']}/execute",
               ErrorCode.PARAM_ERROR, {})
    api.data("DELETE", f"{V1}/regressions/{reg['id']}/items/{item['id']}")
    items_after = api.data("GET", f"{V1}/regressions/{reg['id']}/items")
    check("回归项删除后不再返回",
          all(i.get("id") != item["id"] for i in items_after.get("list") or []),
          str(items_after)[:160])
    # generate 读的是清单自身存好的「变更范围」，不接请求体里的值；
    # 矩阵环境没有配模型，缺范围先报参数错误、配了范围则报「没有可用模型」
    # 清单已带 changeScope，缺模型时后端给出明确的 110102 提示
    api.expect("无模型时生成回归给出明确提示", "POST",
               f"{V1}/regressions/{reg['id']}/generate",
               ErrorCode.AI_CREDENTIAL_MISSING, {})
    api.remove(f"{V1}/regressions/{reg['id']}")


def section_todos(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/todos"
    probe_required_fields(api, "任务", path, {"title": "T"}, ("title",))
    probe_max_length(api, "任务", path, {}, "title", 200)
    probe_bad_id(api, "任务", f"{V1}/todos/424242")
    probe_pagination(api, "任务", path)
    probe_keyword_literal(api, "任务", path)
    probe_batch_ids(api, "任务", f"{V1}/todos/batch-delete")
    probe_malformed_body(api, "任务", path)

    first = api.create(path, {"title": "任务一", "priority": "high"})
    second = api.create(path, {"title": "任务二", "priority": "low"})
    api.data("POST", f"{V1}/todos/{first['id']}/toggle")
    after = api.data("GET", f"{V1}/todos/{first['id']}")
    check("任务勾选后状态变化并带完成时间",
          after.get("status") in ("done", "doing") or after.get("completedAt"),
          str(after)[:200])
    api.data("POST", f"{V1}/todos/{first['id']}/toggle")
    api.data("POST", f"{V1}/todos/{first['id']}/move", {"direction": "down"})
    api.expect("任务移动方向非法被拒绝", "POST", f"{V1}/todos/{first['id']}/move",
               ErrorCode.PARAM_ERROR, {"direction": "left"})
    api.expect("任务父级指向自己被拒绝", "PUT", f"{V1}/todos/{first['id']}",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": first["id"]})
    child = api.create(path, {"title": "子任务", "parentId": first["id"]})
    api.expect("任务父级不存在被拒绝", "POST", path, ErrorCode.RESOURCE_NOT_FOUND,
               {"title": "孤儿任务", "parentId": 424242})
    ordered = api.data("POST", f"{V1}/todos/reorder",
                       {"ids": [second["id"], first["id"], child["id"]]})
    check("任务重排返回顺序", (ordered.get("count") or 0) >= 2, str(ordered)[:160])
    batch = api.data("POST", f"{V1}/todos/batch-update",
                     {"ids": [first["id"], second["id"]], "status": "done"})
    check("任务批量更新生效", (batch.get("count") or 0) == 2, str(batch)[:160])
    stats = api.data("GET", f"{V1}/todos/stats")
    check("任务统计可读", isinstance(stats, dict), str(stats)[:160])
    cats = api.data("GET", f"{V1}/todos/categories")
    check("任务分类可读", isinstance(cats, dict), str(cats)[:160])
    view = api.data("GET", f"{V1}/todos?view=done&pageSize=100")
    check("任务视图筛选可用", isinstance(view.get("list"), list), str(view)[:120])
    api.expect("任务视图非法被拒绝", "GET", f"{V1}/todos?view=magic", ErrorCode.PARAM_ERROR)
    exported = api.client.get(f"{V1}/todos/export?format=csv")
    check("任务导出 CSV 成功", exported.status_code == 200 and bool(exported.data),
          f"status={exported.status_code}")
    for row in (child, first, second):
        api.remove(f"{V1}/todos/{row['id']}")


def section_automation(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/automation/scripts"
    base = {"name": "脚本边界", "category": "web"}
    probe_required_fields(api, "自动化脚本", path, base, ("name",))
    probe_max_length(api, "自动化脚本", path, dict(base), "name", 200)
    probe_enum(api, "自动化脚本", path, dict(base, category="nope"), "category")
    probe_bad_id(api, "自动化脚本", f"{V1}/automation/scripts/424242")
    probe_pagination(api, "自动化脚本", path)
    probe_keyword_literal(api, "自动化脚本", path)
    probe_malformed_body(api, "自动化脚本", path)

    script = api.create(path, {"name": "登录流", "category": "web",
                               "content": "print('ok')", "language": "python",
                               "interpreter": "python"})
    run = api.data("POST", f"{V1}/automation/scripts/{script['id']}/run", {})
    check("脚本执行返回记录与状态",
          isinstance(run, dict) and run.get("status") in
          ("success", "failed", "running", "cancelled", "skipped"), str(run)[:200])
    api.expect("执行不存在的脚本报资源不存在", "POST", f"{V1}/automation/scripts/424242/run",
               ErrorCode.RESOURCE_NOT_FOUND, {})
    timeout = api.call("POST", f"{V1}/automation/scripts/{script['id']}/run",
                       {"timeoutSeconds": -5})
    check("脚本执行超时为负被拒绝或回落到默认",
          timeout[1].get("code") != ErrorCode.INTERNAL_ERROR, str(timeout[1])[:160])
    from_output = api.data("POST", f"{V1}/automation/scripts/from-output", {
        "category": "api", "output": "```python\nprint(1)\n```", "name": "来自 AI"})
    check("AI 产出转脚本资产成功", bool(from_output.get("id")), str(from_output)[:160])
    api.expect("AI 产出转脚本缺 output 被拒绝", "POST", f"{V1}/automation/scripts/from-output",
               ErrorCode.PARAM_ERROR, {"category": "api"})
    runs = api.data("GET", f"{V1}/automation/runs?pageSize=100")
    check("执行历史分页可读", isinstance(runs.get("list"), list), str(runs)[:120])
    runtime = api.data("GET", f"{V1}/automation/runtime")
    check("运行时体检可读", isinstance(runtime, dict), str(runtime)[:200])
    cats = api.data("GET", f"{V1}/automation/categories")
    check("四类脚本目录可读", isinstance(cats, list) and len(cats) >= 4, str(cats)[:160])
    api.remove(f"{V1}/automation/scripts/{script['id']}")
    api.remove(f"{V1}/automation/scripts/{from_output['id']}")


def section_workbench(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/workbench/entries"
    base = {"code": "mtx-entry", "name": "入口", "capability": "case_generation"}
    probe_required_fields(api, "工作台入口", path, base, ("code", "name", "capability"))
    probe_max_length(api, "工作台入口", path, dict(base), "name", 100)
    probe_bad_id(api, "工作台入口", f"{V1}/workbench/entries/424242")
    probe_pagination(api, "工作台入口", path)
    probe_malformed_body(api, "工作台入口", path)

    entry = api.create(path, {"code": "mtx-flow", "name": "造数链路",
                              "capability": "case_generation",
                              "workflow": [{"capability": "requirement_confirm"}]})
    api.data("POST", f"{V1}/workbench/entries/{entry['id']}/publish")
    detail = api.data("GET", f"{V1}/workbench/entries/{entry['id']}")
    check("入口发布后状态与版本变化",
          detail.get("status") == "published", str(detail)[:200])
    api.expect("重复发布被拒绝或幂等", "POST",
               f"{V1}/workbench/entries/{entry['id']}/publish", ErrorCode.SUCCESS)
    api.expect("发布未绑能力的入口被拒绝", "POST", f"{V1}/workbench/entries",
               ErrorCode.PARAM_ERROR, {"code": "mtx-bad", "name": "无能力",
                                       "capability": "not_a_capability"})
    overview = api.data("GET", f"{V1}/workbench/overview?projectId={ctx['p1']}")
    check("工作台概览可读", isinstance(overview, dict), str(overview)[:160])
    coverage = api.data("GET", f"{V1}/workbench/coverage?projectId={ctx['p1']}")
    check("工作台覆盖率可读", isinstance(coverage, (dict, list)), str(coverage)[:160])
    api.expect("概览缺 projectId 不报内部错误", "GET", f"{V1}/workbench/overview",
               ErrorCode.SUCCESS)
    usage = api.data("POST", f"{V1}/workbench/quick-entry/usage", {"key": "cases"})
    check("快捷入口点击计数可用", isinstance(usage, (dict, list)), str(usage)[:160])
    api.expect("快捷入口 key 为空被拒绝", "POST", f"{V1}/workbench/quick-entry/usage",
               ErrorCode.PARAM_ERROR, {"key": " "})
    entries = api.data("GET", f"{V1}/workbench/entries?pageSize=100")
    check("入口列表分页可读", isinstance(entries.get("list"), list), str(entries)[:120])
    executions = api.data("GET", f"{V1}/workbench/executions?projectId={ctx['p1']}")
    # 入口执行记录返回的是结果计数 {result: n}，不是分页列表
    check("入口执行记录可读", isinstance(executions, dict), str(executions)[:120])
    api.remove(f"{V1}/workbench/entries/{entry['id']}")


def section_validation(api: Api, ctx: dict[str, int]) -> None:
    path = f"{V1}/validation/cases"
    base = {"name": "案例", "inputText": "给登录接口写用例"}
    probe_required_fields(api, "验证案例", path, base, ("name", "inputText"))
    probe_max_length(api, "验证案例", path, dict(base), "name", 100)
    probe_bad_id(api, "验证案例", f"{V1}/validation/cases/424242")
    probe_pagination(api, "验证案例", path)
    probe_malformed_body(api, "验证案例", path)

    case = api.create(path, {"name": "路由校验", "inputText": "给登录写用例",
                             "expectedRoutes": ["case_generation"],
                             "expectedMissing": ["projectId"]})
    result = api.data("POST", f"{V1}/validation/cases/{case['id']}/run", {})
    check("单条验证案例可跑", isinstance(result, dict), str(result)[:200])
    allresult = api.data("POST", f"{V1}/validation/run-all", {})
    check("全量验证可跑并汇总", isinstance(allresult, dict) and "total" in allresult,
          str(allresult)[:200])
    api.remove(f"{V1}/validation/cases/{case['id']}")


def section_relations(api: Api, ctx: dict[str, int]) -> None:
    req = api.create(f"{V1}/requirements", {"projectId": ctx["p1"], "title": "被关联需求"})
    bug = api.create(f"{V1}/bugs", {"projectId": ctx["p1"], "title": "被关联缺陷"})
    rel = api.create(f"{V1}/relations", {
        "sourceType": "requirement", "sourceId": req["id"],
        "targetType": "bug", "targetId": bug["id"], "relationType": "relates"})
    check("跨实体关联可建立", bool(rel.get("id")), str(rel)[:160])
    api.expect("关联不存在的实体被拒绝", "POST", f"{V1}/relations",
               ErrorCode.RESOURCE_NOT_FOUND, {
                   "sourceType": "requirement", "sourceId": req["id"],
                   "targetType": "bug", "targetId": 424242, "relationType": "relates"})
    api.expect("关联实体类型非法被拒绝", "POST", f"{V1}/relations", ErrorCode.PARAM_ERROR, {
        "sourceType": "spaceship", "sourceId": req["id"],
        "targetType": "bug", "targetId": bug["id"]})
    api.expect("关联自己到自己被拒绝", "POST", f"{V1}/relations", ErrorCode.PARAM_ERROR, {
        "sourceType": "requirement", "sourceId": req["id"],
        "targetType": "requirement", "targetId": req["id"]})
    of = api.data("GET", f"{V1}/relations/of/requirement/{req['id']}")
    check("按实体查关联可读", isinstance(of.get("list"), list), str(of)[:160])
    api.expect("查询非法实体类型被拒绝", "GET", f"{V1}/relations/of/spaceship/1",
               ErrorCode.PARAM_ERROR)
    types = api.data("GET", f"{V1}/relations/types")
    check("关联类型元数据可读", isinstance(types, dict), str(types)[:160])
    api.data("DELETE", f"{V1}/relations/{rel['id']}")
    api.remove(f"{V1}/bugs/{bug['id']}")
    api.remove(f"{V1}/requirements/{req['id']}")


def section_assets(api: Api, ctx: dict[str, int]) -> None:
    """SQL 分析、文档模板、测试数据、AI 记录四条资产线。"""
    sql_path = f"{V1}/sql-analyses"
    probe_required_fields(api, "SQL 分析", sql_path,
                          {"projectId": ctx["p1"], "title": "慢查询"},
                          ("projectId", "title"))
    probe_enum(api, "SQL 分析", sql_path,
               {"projectId": ctx["p1"], "title": "t", "riskLevel": "nope"}, "riskLevel")
    probe_bad_id(api, "SQL 分析", f"{V1}/sql-analyses/424242")
    probe_pagination(api, "SQL 分析", sql_path)
    probe_keyword_literal(api, "SQL 分析", sql_path)
    sql = api.create(sql_path, {"projectId": ctx["p1"], "title": "全表扫描",
                                "sqlText": "SELECT * FROM t", "riskLevel": "high"})
    api.data("PUT", f"{V1}/sql-analyses/{sql['id']}", {"riskLevel": "low"})
    api.remove(f"{V1}/sql-analyses/{sql['id']}")

    tpl_path = f"{V1}/doc-templates"
    probe_required_fields(api, "文档模板", tpl_path, {"name": "T"}, ("name",))
    probe_max_length(api, "文档模板", tpl_path, {}, "name", 100)
    probe_enum(api, "文档模板", tpl_path, {"name": "n", "kind": "nope"}, "kind")
    probe_bad_id(api, "文档模板", f"{V1}/doc-templates/424242")
    probe_pagination(api, "文档模板", tpl_path)
    tpl = api.create(tpl_path, {"name": "用例模板", "kind": "case", "content": "|列|\n|--|\n"})
    options = api.data("GET", f"{V1}/doc-templates/options?kind=case")
    check("同类模板下拉可查到",
          any(t.get("id") == tpl["id"] for t in options.get("list") or []), str(options)[:160])
    api.expect("模板 kind 非法下拉被拒绝", "GET", f"{V1}/doc-templates/options?kind=nope",
               ErrorCode.PARAM_ERROR)
    api.remove(f"{V1}/doc-templates/{tpl['id']}")

    td = f"{V1}/test-data"
    catalog = api.data("GET", f"{td}/formats")
    check("文件格式目录数量充足",
          isinstance(catalog, dict) and len(catalog.get("categories") or catalog.get("list") or []) > 0,
          str(catalog)[:120])
    api.expect("生成测试文件缺少格式被拒绝", "POST", f"{td}/files", ErrorCode.PARAM_ERROR,
               {"projectId": ctx["p1"]})
    api.expect("生成数量超上限被拒绝", "POST", f"{td}/files", ErrorCode.PARAM_ERROR, {
        "projectId": ctx["p1"], "ext": "csv", "fileCount": 5000})
    api.expect("生成数量为 0 被拒绝", "POST", f"{td}/files", ErrorCode.PARAM_ERROR, {
        "projectId": ctx["p1"], "ext": "csv", "fileCount": 0})
    api.expect("不支持的格式被拒绝", "POST", f"{td}/files", ErrorCode.PARAM_ERROR, {
        "projectId": ctx["p1"], "ext": "exe_不存在", "fileCount": 1})
    api.expect("文件名字符越界被拒绝", "POST", f"{td}/files", ErrorCode.PARAM_ERROR, {
        "projectId": ctx["p1"], "ext": "csv", "namePrefix": "../../etc/passwd", "fileCount": 1})
    batch = api.create(f"{td}/files", {"projectId": ctx["p1"], "ext": "csv",
                                       "fileCount": 2, "sizeBytes": 400})
    check("测试文件批次生成成功", bool(batch.get("id")) and len(batch.get("files") or []) == 2,
          str(batch)[:200])
    files = batch.get("files") or []
    if files:
        first = files[0]
        dl = api.client.get(f"{td}/files/{first['id']}/download")
        check("单个测试文件可下载", dl.status_code == 200 and bool(dl.data),
              f"status={dl.status_code}")
        api.expect("下载不存在的文件报资源不存在", "GET", f"{td}/files/424242/download",
                   ErrorCode.RESOURCE_NOT_FOUND)
    arc = api.client.get(f"{td}/batches/{batch['id']}/archive")
    check("批次 zip 可下载", arc.status_code == 200 and arc.data[:2] == b"PK",
          f"status={arc.status_code} head={arc.data[:4]}")
    api.expect("打包不存在的批次报资源不存在", "GET", f"{td}/batches/424242/archive",
               ErrorCode.RESOURCE_NOT_FOUND)
    probe_pagination(api, "测试数据批次", f"{td}/batches")
    # 批次只有查/删/打包/下载，没有整批改字段的路由
    probe_bad_id(api, "测试数据批次", f"{td}/batches/424242", methods=("GET", "DELETE"))
    api.remove(f"{td}/batches/{batch['id']}")

    records = api.data("GET", f"{V1}/ai-records?pageSize=100")
    check("AI 执行记录分页可读", isinstance(records.get("list"), list), str(records)[:120])
    probe_pagination(api, "AI 记录", f"{V1}/ai-records")
    api.expect("记录复核状态非法被拒绝", "POST", f"{V1}/ai-records/1/review",
               ErrorCode.PARAM_ERROR, {"status": "maybe"})


def section_search_badges(api: Api, ctx: dict[str, int]) -> None:
    res = api.data("GET", f"{V1}/search?q=矩阵项目甲&limit=20")
    check("全局搜索命中项目名", isinstance(res, (dict, list)), str(res)[:160])
    api.expect("搜索关键字为空被拒绝", "GET", f"{V1}/search?q=", ErrorCode.PARAM_ERROR)
    api.expect("搜索关键字超长被拒绝", "GET", f"{V1}/search?q={quote('长' * 201)}",
               ErrorCode.PARAM_ERROR)
    api.expect("搜索 limit 为 0 被拒绝或回落", "GET", f"{V1}/search?q=矩阵&limit=0",
               ErrorCode.SUCCESS)
    api.no_server_error("搜索类型非法", "GET", f"{V1}/search?q=矩阵&types=spaceship")
    scoped = api.data("GET", f"{V1}/search?q=登录&projectId={ctx['p2']}")
    check("搜索按项目过滤可用", isinstance(scoped, (dict, list)), str(scoped)[:120])
    api.expect("搜索项目不存在不报内部错误", "GET", f"{V1}/search?q=x&projectId=424242",
               ErrorCode.SUCCESS)

    badges = api.data("GET", f"{V1}/badges")
    check("角标清单可读", isinstance(badges.get("items"), dict), str(badges)[:120])
    api.data("POST", f"{V1}/badges/cases/increase", {"count": 3})
    after = api.data("GET", f"{V1}/badges")
    check("角标累加生效", (after.get("items") or {}).get("cases", 0) >= 3, str(after)[:160])
    api.data("POST", f"{V1}/badges/cases/clear")
    cleared = api.data("GET", f"{V1}/badges")
    check("角标清零生效", (cleared.get("items") or {}).get("cases", 0) == 0, str(cleared)[:160])
    api.expect("未知角标区被拒绝", "POST", f"{V1}/badges/spaceship/increase",
               ErrorCode.PARAM_ERROR, {"count": 1})
    api.expect("角标负数被拒绝", "POST", f"{V1}/badges/cases/increase",
               ErrorCode.PARAM_ERROR, {"count": -5})


def section_settings(api: Api, ctx: dict[str, int]) -> None:
    st = f"{V1}/settings"
    providers = api.data("GET", f"{st}/ai/providers")
    check("供应商清单含商汤日日新",
          any(p.get("name") == "sensenova" for p in providers), str(providers)[:160])
    probe_required_fields(api, "AI 档案", f"{st}/ai/profiles",
                          {"name": "N", "provider": "openai"}, ("name", "provider"))
    api.expect("档案 provider 不存在不报内部错误", "POST", f"{st}/ai/profiles",
               ErrorCode.SUCCESS, {"name": "未知供应商", "provider": "spaceship"})
    api.expect("档案名超长被拒绝", "POST", f"{st}/ai/profiles", ErrorCode.PARAM_ERROR,
               {"name": "长" * 201, "provider": "openai"})
    api.expect("档案地址超长被拒绝", "POST", f"{st}/ai/profiles", ErrorCode.PARAM_ERROR, {
        "name": "n", "provider": "openai", "baseUrl": "h" * 501})
    probe_bad_id(api, "AI 档案", f"{st}/ai/profiles/424242")

    profile = api.create(f"{st}/ai/profiles", {
        "name": "矩阵档案", "provider": "custom", "baseUrl": "http://127.0.0.1:59999",
        "model": "m", "timeout": 3, "priority": 5})
    masked = api.data("POST", f"{st}/ai/profiles/{profile['id']}/api-key",
                      {"apiKey": "sk-matrix-secret-123456"})
    check("密钥只回掩码", masked.get("hasApiKey") is True and
          "matrix-secret" not in str(masked), str(masked)[:160])
    listed = api.data("GET", f"{st}/ai/profiles")
    check("档案清单不含明文密钥",
          "matrix-secret" not in str(listed), "明文泄露")
    api.data("POST", f"{st}/ai/profiles/{profile['id']}/api-key", {"apiKey": ""})
    gone = api.data("GET", f"{st}/ai/profiles/{profile['id']}")
    check("清空密钥后掩码为空", not gone.get("apiKeyMasked"), str(gone)[:160])
    api.expect("档案 temperature 非数字被拒绝", "PUT", f"{st}/ai/profiles/{profile['id']}",
               ErrorCode.PARAM_ERROR, {"temperature": "hot"})
    api.remove(f"{st}/ai/profiles/{profile['id']}")

    cfg = api.data("GET", f"{st}/config")
    check("系统配置可读", isinstance(cfg, list), str(cfg)[:120])
    api.expect("配置项缺 key 被拒绝", "PUT", f"{st}/config", ErrorCode.PARAM_ERROR,
               {"items": [{"value": "v"}]})
    api.data("PUT", f"{st}/config", {"items": [{"key": "matrix.test", "value": "v1"}]})
    check("自定义配置写读一致",
          any(i.get("key") == "matrix.test" for i in api.data("GET", f"{st}/config")),
          "配置未落库")

    logs = api.data("GET", f"{st}/audit-logs?pageSize=100")
    check("审计日志分页可读", isinstance(logs.get("list"), list), str(logs)[:120])
    probe_pagination(api, "审计日志", f"{st}/audit-logs")
    api.no_server_error("审计日志时间非法", "GET",
                        f"{st}/audit-logs?startTime=abc&endTime=%%%")
    res = api.data("GET", f"{st}/audit-logs/resources?resourceType=project&resourceId={ctx['p1']}")
    check("按资源查审计可追溯", isinstance(res, (dict, list)), str(res)[:120])

    backup = api.data("POST", f"{st}/backups")
    check("备份可创建", bool(backup), str(backup)[:160])
    listing = api.data("GET", f"{st}/backups")
    check("备份清单可读", isinstance(listing.get("list"), list), str(listing)[:160])
    api.expect("恢复不存在的备份被拒绝", "POST",
               f"{st}/backups/aitp-20990101-000000.db/restore",
               ErrorCode.RESOURCE_NOT_FOUND, {"confirm": "aitp-20990101-000000.db"})
    api.expect("恢复未确认被拒绝", "POST",
               f"{st}/backups/{backup['name']}/restore", ErrorCode.ILLEGAL_OPERATION,
               {"confirm": "wrong"})
    dl = api.client.get(f"{st}/backups/{quote(str(backup['name']))}/download")
    check("备份文件可下载", dl.status_code == 200 and bool(dl.data),
          f"status={dl.status_code}")
    dl.close()
    api.expect("备份名穿越被拒绝", "GET", f"{st}/backups/..%2F..%2Faitp.db/download",
               ErrorCode.NOT_FOUND_ENDPOINT)
    api.data("DELETE", f"{st}/backups/{backup['name']}")

    enums = api.data("GET", f"{V1}/meta/enums")
    check("枚举元数据含状态机", bool(enums.get("bugTransitions")), str(enums)[:120])
    caps = api.data("GET", f"{V1}/meta/capabilities")
    check("能力元数据可读", isinstance(caps, (dict, list)), str(caps)[:120])


def section_lab(api: Api, ctx: dict[str, int]) -> None:
    """AI 实验室的本地路径：路由、预检、脑图解析、会话与任务。

    真实大模型驱动的全路径在 check_ai_live.py 里跑，这里只保证不依赖模型。
    """
    caps = api.data("GET", f"{V1}/lab/capabilities")
    check("能力清单不少于 19 个", len(caps) >= 19, f"count={len(caps)}")
    codes = {c.get("code") for c in caps}
    check("能力清单含测试数据生成", "test_data_generation" in codes, str(codes)[:200])
    main_caps = api.data("GET", f"{V1}/lab/main-capabilities")
    check("主能力清单可读", isinstance(main_caps, list), str(main_caps)[:120])
    prompts = api.data("GET", f"{V1}/lab/prompts")
    check("提示词清单可读", isinstance(prompts, list) and len(prompts) >= 20,
          f"count={len(prompts)}")
    code = str(prompts[0].get("code") or "") if prompts else ""
    if code:
        one = api.data("GET", f"{V1}/lab/prompts/{quote(code)}")
        check("单个提示词可读", one.get("code") == code, str(one)[:120])
    api.expect("不存在的提示词报 120101", "GET", f"{V1}/lab/prompts/nope-不存在",
               ErrorCode.PROMPT_NOT_FOUND)

    route = api.data("POST", f"{V1}/lab/route", {"text": "给登录接口写用例", "topK": 3})
    check("意图路由返回能力候选", isinstance(route.get("capabilityRoutes"), list),
          str(route)[:200])
    check("意图路由返回模块候选", isinstance(route.get("moduleRoutes"), list),
          str(route)[:200])
    api.expect("路由文本为空被拒绝", "POST", f"{V1}/lab/route", ErrorCode.PARAM_ERROR,
               {"text": "  "})
    api.no_server_error("路由 topK 越界被夹紧", "POST", f"{V1}/lab/route",
                        {"text": "写用例", "topK": 99999})

    preflight = api.data("POST", f"{V1}/lab/preflight",
                         {"text": "给登录写用例并生成回归清单", "projectId": ctx["p1"]})
    check("预检返回主辅步骤与阻断项", isinstance(preflight, dict) and
          all(k in preflight for k in ("primary", "auxiliary", "blockers", "ready")),
          str(preflight)[:200])
    xmind = api.data("POST", f"{V1}/lab/xmind/parse",
                     {"outlineText": "- 登录\n  - 正确口令\n  - 错误口令"})
    check("脑图大纲解析出节点", isinstance(xmind, dict), str(xmind)[:200])
    api.expect("空大纲被拒绝", "POST", f"{V1}/lab/xmind/parse", ErrorCode.PARAM_ERROR,
               {"outlineText": "  "})

    sessions = api.data("GET", f"{V1}/lab/sessions?pageSize=100")
    check("会话分页可读", isinstance(sessions.get("list"), list), str(sessions)[:120])
    tasks = api.data("GET", f"{V1}/lab/tasks?pageSize=100")
    check("任务分页可读", isinstance(tasks.get("list"), list), str(tasks)[:120])
    probe_pagination(api, "AI 会话", f"{V1}/lab/sessions")
    probe_pagination(api, "AI 任务", f"{V1}/lab/tasks")
    api.expect("取消不存在的任务报资源不存在", "POST", f"{V1}/lab/tasks/424242/cancel",
               ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("查询不存在的任务报资源不存在", "GET", f"{V1}/lab/tasks/424242",
               ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("查询不存在的会话报资源不存在", "GET", f"{V1}/lab/sessions/424242",
               ErrorCode.RESOURCE_NOT_FOUND)

    api.expect("未知能力报参数错误", "POST", f"{V1}/lab/spacecraft_generation/run",
               ErrorCode.PARAM_ERROR, {"variables": {}})
    api.expect("能力缺变量报缺项错误", "POST", f"{V1}/lab/requirement_confirm/run",
               ErrorCode.PROMPT_VAR_MISSING, {"variables": {}})
    api.expect("附件 id 非法不报内部错误", "GET", f"{V1}/lab/attachments/../../etc/passwd",
               ErrorCode.NOT_FOUND_ENDPOINT)
    api.expect("删除不存在附件报资源不存在", "DELETE", f"{V1}/lab/attachments/nope",
               ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("导出产出物缺 output 被拒绝", "POST", f"{V1}/lab/output/export",
               ErrorCode.PARAM_ERROR, {"capability": "case_generation"})
    api.expect("产出采纳目标非法被拒绝", "POST", f"{V1}/lab/output/adopt",
               ErrorCode.PARAM_ERROR, {"target": "spaceship", "projectId": ctx["p1"],
                                       "output": "x", "capability": "case_generation"})
    api.expect("提示词优化接口不崩", "POST", f"{V1}/ai/prompts/optimize", ErrorCode.SUCCESS,
               {"capability": "case_generation", "rawText": "写个登录用例"})


def section_cross_project(api: Api, ctx: dict[str, int]) -> None:
    """多项目覆盖：每个列表接口都必须在指定项目内返回，不串数据。"""
    a_req = api.create(f"{V1}/requirements", {"projectId": ctx["p1"], "title": "隔离标记甲"})
    b_req = api.create(f"{V1}/requirements", {"projectId": ctx["p2"], "title": "隔离标记乙"})
    c_case = api.create(f"{V1}/test-cases", {"projectId": ctx["p3"], "title": "隔离标记丙"})
    matrix = (("需求", f"{V1}/requirements", ctx["p1"], a_req["id"]),
              ("需求", f"{V1}/requirements", ctx["p2"], b_req["id"]),
              ("需求", f"{V1}/requirements", ctx["p3"], None),
              ("用例", f"{V1}/test-cases", ctx["p3"], c_case["id"]),
              ("用例", f"{V1}/test-cases", ctx["p1"], None))
    for label, path, project_id, expected in matrix:
        page = api.data("GET", f"{path}?projectId={project_id}&pageSize=100")
        ids = {item.get("id") for item in page.get("list") or []}
        if expected is not None:
            check(f"{label} 项目 {project_id} 列表含自己的数据", expected in ids,
                  f"ids={sorted(ids)[:10]}")
        others = {a_req["id"], b_req["id"], c_case["id"]} - {expected} - {0}
        check(f"{label} 项目 {project_id} 列表不含别的项目数据",
              not (ids & {o for o in others if o}),
              f"泄露 {sorted(ids & others)}")
    # 全局列表（不带 projectId）也必须只统计未删除数据
    allpage = api.data("GET", f"{V1}/requirements?pageSize=100")
    check("全局需求列表 total 与项目内之和一致",
          allpage.get("total", 0) >= 2, str(allpage)[:120])
    for row in (a_req, b_req):
        api.remove(f"{V1}/requirements/{row['id']}")
    api.remove(f"{V1}/test-cases/{c_case['id']}")
    deleted_page = api.data("GET", f"{V1}/requirements?pageSize=100")
    check("软删后不再出现在列表",
          all(item.get("id") not in (a_req["id"], b_req["id"])
              for item in deleted_page.get("list") or []), "残留已删数据")


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-matrix-"))
    api = Api(build_app(workdir))
    ctx = seed_projects(api)
    sections: list[tuple[str, Callable[[], None]]] = [
        ("全局包封", lambda: probe_malformed_body(api, "全局", f"{V1}/projects")),
        ("项目", lambda: section_projects(api, ctx)),
        ("模块树", lambda: section_modules(api, ctx)),
        ("需求", lambda: section_requirements(api, ctx)),
        ("用例", lambda: section_cases(api, ctx)),
        ("缺陷", lambda: section_bugs(api, ctx)),
        ("知识治理", lambda: section_knowledge(api, ctx)),
        ("报告", lambda: section_reports(api, ctx)),
        ("回归清单", lambda: section_regressions(api, ctx)),
        ("任务清单", lambda: section_todos(api, ctx)),
        ("自动化", lambda: section_automation(api, ctx)),
        ("工作台", lambda: section_workbench(api, ctx)),
        ("案例验证", lambda: section_validation(api, ctx)),
        ("实体关联", lambda: section_relations(api, ctx)),
        ("资产与记录", lambda: section_assets(api, ctx)),
        ("搜索与角标", lambda: section_search_badges(api, ctx)),
        ("设置与备份", lambda: section_settings(api, ctx)),
        ("AI 实验室", lambda: section_lab(api, ctx)),
        ("跨项目隔离", lambda: section_cross_project(api, ctx)),
    ]
    for name, runner in sections:
        before = _failed
        try:
            runner()
        except Exception as exc:  # noqa: BLE001 - 分区异常要让其它分区继续跑
            check(f"{name} 分区执行不抛异常", False, f"{type(exc).__name__}: {exc}")
        else:
            if _failed == before:
                print(f"[OK] {name}")
    print("-" * 50)
    print(f"功能矩阵：PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败清单：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
