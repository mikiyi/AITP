"""P3 API 冒烟测试。

在临时数据库上跑通全部蓝图的关键路径，校验包封、分页数学、过滤组合、软删、
四级模块树、知识治理闭环、工作台指标与审计留痕。

    python backend/scripts/smoke_api.py

输出逐条 [PASS]/[FAIL]，结尾给出 PASS/FAIL 计数；FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.client import RETRY_LIMIT  # noqa: E402
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
            raise AssertionError(f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None, **kwargs: Any) -> JsonDict:
        resp, body = self.call(method, path, payload, **kwargs)
        ok = resp.status_code == 200 and body.get("code") == code
        check(
            name,
            ok,
            f"http={resp.status_code} code={body.get('code')} msg={body.get('message')}",
        )
        return body


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "smoke.db"
    init_db(db_path)
    # 知识库落盘目录指向临时目录，避免污染项目默认 knowledge/
    conn = sqlite3.connect(db_path)
    SysConfigRepository(conn).set_value(
        CONFIG_PATH, str(workdir / "knowledge"), "string", "冒烟脚本临时目录")
    conn.commit()
    conn.close()
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "smoke.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


# ---------------- 分组检查 ----------------

def check_envelope(api: Api) -> None:
    resp, body = api.call("GET", "/api/v1/health")
    check("健康检查返回成功码", body.get("code") == ErrorCode.SUCCESS, str(body))
    check("包封字段齐全",
          all(k in body for k in ("code", "message", "data", "traceId", "timestamp")),
          str(sorted(body.keys())))
    check("HTTP 恒为 200", resp.status_code == 200, f"status={resp.status_code}")
    check("traceId 头与包封一致",
          resp.headers.get("X-Trace-Id") == body.get("traceId"),
          f"header={resp.headers.get('X-Trace-Id')} body={body.get('traceId')}")
    cors = [h for h in resp.headers.keys() if h.lower().startswith("access-control-")]
    check("服务端不下发 CORS 头", not cors, str(cors))

    api.expect("未知接口返回 100103", "GET", "/api/v1/definitely-not-exist", ErrorCode.NOT_FOUND_ENDPOINT)
    api.expect("方法不支持返回 100104", "DELETE", "/api/v1/health", ErrorCode.METHOD_NOT_ALLOWED)


def check_routes(api: Api) -> None:
    rules = list(api.app.url_map.iter_rules())
    pairs: dict[tuple[str, str], str] = {}
    duplicates: list[str] = []
    for rule in rules:
        for method in sorted(cast("set[str]", rule.methods) - {"HEAD", "OPTIONS"}):
            key = (rule.rule, method)
            if key in pairs and pairs[key] != rule.endpoint:
                duplicates.append(f"{method} {rule.rule}")
            pairs[key] = rule.endpoint

    check("路由表无重复规则（不存在蓝图互相遮蔽）", not duplicates, str(duplicates))
    static_rules = {"/", "/<path:path>"}
    outside = [
        f"{sorted(cast('set[str]', r.methods) - {'HEAD', 'OPTIONS'})} {r.rule}"
        for r in rules
        if not r.rule.startswith("/api/v1")
        and r.rule not in static_rules
    ]
    check("除静态资源外所有路由都在 /api/v1 下", not outside, str(outside))
    print(f"       路由总数: {len(pairs)}")


def check_projects(api: Api) -> int:
    data = api.data("POST", "/api/v1/projects", {
        "code": "SMOKE-EC", "name": "冒烟电商项目", "owner": "yichengren",
    })
    project_id = int(data["id"])
    check("创建项目返回自增主键", project_id > 0, str(data))

    api.expect("项目编号重复返回 140103", "POST", "/api/v1/projects",
               ErrorCode.UNIQUE_CONFLICT, {"code": "SMOKE-EC", "name": "重复项目"})
    api.expect("缺少必填参数返回 100100", "POST", "/api/v1/projects",
               ErrorCode.PARAM_ERROR, {"name": "缺编号的项目"})

    updated = api.data("PUT", f"/api/v1/projects/{project_id}", {"description": "冒烟用项目"})
    check("更新项目返回最新内容", updated["description"] == "冒烟用项目", str(updated))

    page = api.data("GET", "/api/v1/projects?pageNum=1&pageSize=10")
    check("项目列表分页字段完整",
          all(k in page for k in ("list", "pageNum", "pageSize", "total", "pages")),
          str(sorted(page.keys())))

    hit = api.data("GET", "/api/v1/projects?keyword=冒烟电商")
    check("项目关键词检索命中", hit["total"] >= 1, str(hit["total"]))

    api.data("DELETE", f"/api/v1/projects/{project_id}")
    api.expect("软删后查项目返回 140100", "GET", f"/api/v1/projects/{project_id}",
               ErrorCode.RESOURCE_NOT_FOUND)
    after = api.data("GET", "/api/v1/projects?pageSize=100")
    check("软删后列表不再包含该项目",
          all(item["id"] != project_id for item in after["list"]),
          str([item["id"] for item in after["list"]]))

    # 重建一个供后续用例使用
    data = api.data("POST", "/api/v1/projects", {"code": "SMOKE-MAIN", "name": "冒烟主项目"})
    return int(data["id"])


def check_requirements(api: Api, project_id: int) -> int:
    data = api.data("POST", "/api/v1/requirements", {
        "projectId": project_id, "title": "手机号验证码登录", "content": "支持手机号+验证码登录",
    })
    req_id = int(data["id"])
    check("需求编号自动生成", str(data.get("reqNo", "")).startswith("REQ-"), str(data.get("reqNo")))

    page = api.data("GET", f"/api/v1/requirements?projectId={project_id}&pageSize=5")
    check("需求按项目过滤", page["total"] == 1, str(page["total"]))

    api.expect("需求不存在返回 140100", "GET", "/api/v1/requirements/999999",
               ErrorCode.RESOURCE_NOT_FOUND)
    return req_id


def check_module_tree(api: Api, project_id: int) -> tuple[int, int, int]:
    project = api.data("GET", f"/api/v1/projects/{project_id}")
    project_name = project["name"]

    module = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "登录模块", "nodeType": "module",
    })
    sub = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "手机号登录", "nodeType": "submodule",
        "parentId": module["id"],
    })
    feature = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "验证码时效", "nodeType": "feature",
        "parentId": sub["id"],
    })
    module_id, sub_id, feature_id = int(module["id"]), int(sub["id"]), int(feature["id"])

    tree = api.data("GET", f"/api/v1/modules/tree?projectId={project_id}")
    roots = tree["tree"]
    check("模块树返回根节点", len(roots) == 1 and roots[0]["name"] == "登录模块", str(roots))
    check("模块树返回 4 级层级（模块/子模块/功能）",
          len(roots[0]["children"]) == 1
          and len(roots[0]["children"][0]["children"]) == 1
          and len(roots[0]["children"][0]["children"][0]["children"]) == 0,
          json_paths(roots))

    feature_node = roots[0]["children"][0]["children"][0]
    check("模块树深度：模块=1 功能=3",
          roots[0]["depth"] == 1 and feature_node["depth"] == 3,
          f"root={roots[0]['depth']} feature={feature_node['depth']}")
    expected_path = f"{project_name}/登录模块/手机号登录/验证码时效"
    check("模块树返回完整路径", feature_node["path"] == expected_path, feature_node["path"])

    detail = api.data("GET", f"/api/v1/modules/{feature_id}")
    check("模块详情深度与树一致", detail["depth"] == 3, str(detail["depth"]))
    check("模块详情路径与树一致", detail["path"] == expected_path, detail["path"])

    options = api.data("GET", f"/api/v1/modules/options?projectId={project_id}")
    check("功能点下拉只列叶子节点",
          len(options) == 1 and options[0]["id"] == feature_id, str(options))

    api.expect("改父成环（挂到自己子孙下）返回 140105",
               "POST", f"/api/v1/modules/{module_id}/move",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": feature_id})
    api.expect("改父成环（挂到自己下面）返回 140105",
               "POST", f"/api/v1/modules/{module_id}/move",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": module_id})
    api.expect("删除有子节点的模块返回 140104",
               "DELETE", f"/api/v1/modules/{module_id}",
               ErrorCode.RESOURCE_REFERENCED)

    caps = api.data("PUT", f"/api/v1/modules/{feature_id}/capabilities", {
        "capabilities": [
            {"capability": "bug_analysis", "enabled": True, "promptCode": "bug-analysis"},
            {"capability": "log_analysis", "enabled": False, "config": {"errorType": "timeout"}},
        ],
    })
    check("挂载 AI 能力返回更新数量", caps["updated"] == 2, str(caps))
    bound = api.data("GET", f"/api/v1/modules/{feature_id}/capabilities")
    names = {item["capability"] for item in bound["capabilities"]}
    check("读取能力绑定返回两条", len(bound["capabilities"]) == 2, str(bound))
    check("能力绑定包含 bug_analysis", "bug_analysis" in names, str(names))
    api.expect("未知能力返回 100100", "PUT", f"/api/v1/modules/{feature_id}/capabilities",
               ErrorCode.PARAM_ERROR, {"capabilities": [{"capability": "not_a_capability"}]})
    return module_id, sub_id, feature_id


def json_paths(nodes: list[JsonDict]) -> str:
    out: list[str] = []

    def walk(items: list[JsonDict]) -> None:
        for item in items:
            out.append(f"{item.get('path')}(d={item.get('depth')})")
            walk(item.get("children") or [])

    walk(nodes)
    return ", ".join(out)


def check_cases(api: Api, project_id: int, requirement_id: int, feature_id: int) -> list[int]:
    priorities = ["P0", "P1", "P2", "P2", "P3"]
    case_ids: list[int] = []
    for index, priority in enumerate(priorities, start=1):
        data = api.data("POST", "/api/v1/test-cases", {
            "projectId": project_id,
            "requirementId": requirement_id,
            "moduleId": feature_id,
            "title": f"冒烟用例{index}",
            "caseType": "functional",
            "priority": priority,
            "steps": [{"action": "输入验证码", "expect": "登录成功"}],
        })
        case_ids.append(int(data["id"]))
    check("用例编号自动生成且按类型带前缀",
          str(data["caseNo"]).startswith("FT-"), str(data.get("caseNo")))

    page = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&pageNum=1&pageSize=2")
    check("分页 total 正确", page["total"] == 5, str(page["total"]))
    check("分页 pages 向上取整正确", page["pages"] == 3, str(page["pages"]))
    check("分页当前页返回条数正确", len(page["list"]) == 2, str(len(page["list"])))

    page2 = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&pageNum=3&pageSize=2")
    check("翻到末页返回剩余 1 条", len(page2["list"]) == 1, str(len(page2["list"])))
    check("末页页码正确", page2["pageNum"] == 3, str(page2["pageNum"]))

    capped = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&pageSize=1000")
    check("pageSize 被夹到上限 100", capped["pageSize"] == 100, str(capped["pageSize"]))

    default_size = api.data("GET", f"/api/v1/test-cases?projectId={project_id}")
    check("pageSize 缺省为 20", default_size["pageSize"] == 20, str(default_size["pageSize"]))

    api.expect("pageSize 非法返回 100108", "GET", "/api/v1/test-cases?pageSize=0",
               ErrorCode.PAGE_PARAM_INVALID)

    p0 = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&priority=P0")
    check("按优先级过滤生效", p0["total"] == 1, str(p0["total"]))
    p2 = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&priority=P2")
    check("按优先级过滤命中多条", p2["total"] == 2, str(p2["total"]))
    combined = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&priority=P2&moduleId={feature_id}")
    check("多条件组合过滤生效", combined["total"] == 2, str(combined["total"]))
    empty = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&priority=P0&moduleId=999999")
    check("组合过滤无命中返回空列表", empty["total"] == 0 and empty["list"] == [], str(empty))

    other_project = api.data("POST", "/api/v1/projects", {"code": "SMOKE-OTHER", "name": "另一个项目"})
    api.data("POST", "/api/v1/test-cases", {
        "projectId": other_project["id"], "title": "别的项目用例", "priority": "P0",
    })
    scoped = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&priority=P0")
    check("过滤不串项目", scoped["total"] == 1, str(scoped["total"]))

    # 用例列表左侧功能点面板的两种扩展写法：未挂功能点、多个功能点（含下级由前端展开成 id 列表）
    no_module = api.data("GET", f"/api/v1/test-cases?projectId={other_project['id']}&moduleId=none")
    check("按未挂功能点筛选生效", no_module["total"] == 1, str(no_module))
    multi_module = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&moduleId={feature_id},999999")
    check("按多个功能点筛选生效", multi_module["total"] == 5, str(multi_module["total"]))

    keyword = api.data("GET", "/api/v1/test-cases?keyword=冒烟用例")
    check("关键词检索命中", keyword["total"] == 5, str(keyword["total"]))

    detail = api.data("GET", f"/api/v1/test-cases/{case_ids[0]}")
    check("用例详情还原 steps 数组",
          isinstance(detail["steps"], list) and len(detail["steps"]) == 1, str(detail["steps"]))

    executed = api.data("POST", f"/api/v1/test-cases/{case_ids[0]}/execute", {"result": "pass"})
    check("执行用例回写状态", executed["status"] == "pass", str(executed))
    after = api.data("GET", f"/api/v1/test-cases/{case_ids[0]}")
    check("执行结果同步到用例状态", after["status"] == "pass", str(after["status"]))

    api.data("DELETE", f"/api/v1/test-cases/{case_ids[4]}")
    api.expect("软删后查用例返回 140100", "GET", f"/api/v1/test-cases/{case_ids[4]}",
               ErrorCode.RESOURCE_NOT_FOUND)
    listed = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&pageSize=100")
    check("软删后列表排除该用例",
          all(item["id"] != case_ids[4] for item in listed["list"]) and listed["total"] == 4,
          str(listed["total"]))
    return case_ids


def check_bugs(api: Api, project_id: int, feature_id: int, case_ids: list[int]) -> int:
    data = api.data("POST", "/api/v1/bugs", {
        "projectId": project_id, "moduleId": feature_id,
        "title": "支付回调重复推送导致金额翻倍",
        "severity": "P0", "bugType": "functional",
        "description": "回调未做幂等", "steps": [{"action": "推送两次回调"}],
    })
    bug_id = int(data["id"])
    check("缺陷编号自动生成", str(data.get("bugNo", "")).startswith("BUG-"), str(data.get("bugNo")))

    api.expect("非法缺陷状态流转返回 140102", "POST", f"/api/v1/bugs/{bug_id}/transition",
               ErrorCode.ILLEGAL_TRANSITION, {"toStatus": "retest"})
    logs = api.data("GET", f"/api/v1/bugs/{bug_id}/logs")
    check("非法流转不写流转日志", len(logs) == 0, str(logs))

    moved = api.data("POST", f"/api/v1/bugs/{bug_id}/transition",
                     {"toStatus": "assigned", "operator": "yichengren", "comment": "分派处理"})
    check("合法流转返回前后状态", moved["fromStatus"] == "open" and moved["toStatus"] == "assigned",
          str(moved))
    logs = api.data("GET", f"/api/v1/bugs/{bug_id}/logs")
    check("合法流转写入流转日志", len(logs) == 1, str(logs))

    linked = api.data("POST", f"/api/v1/bugs/{bug_id}/cases", {"caseIds": case_ids[:2]})
    check("缺陷关联用例成功", linked["linked"] == 2, str(linked))
    detail = api.data("GET", f"/api/v1/bugs/{bug_id}")
    check("缺陷详情回传关联用例", len(detail["linkedCaseIds"]) == 2, str(detail["linkedCaseIds"]))

    stats = api.data("GET", f"/api/v1/bugs/stats?projectId={project_id}")
    check("缺陷统计与项目一致", stats["total"] == 1 and stats["statusCounts"]["assigned"] == 1,
          str(stats))

    api.data("DELETE", f"/api/v1/bugs/{bug_id}")
    api.expect("软删后查缺陷返回 140100", "GET", f"/api/v1/bugs/{bug_id}", ErrorCode.RESOURCE_NOT_FOUND)
    return bug_id


def check_knowledge(api: Api, project_id: int, feature_id: int) -> None:
    before = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")
    drafts_before = api.data("GET", "/api/v1/knowledge/drafts?pageSize=100")

    draft = api.data("POST", "/api/v1/knowledge/drafts", {
        "title": "支付回调幂等方案",
        "content": "回调按交易流水号去重，落库前先查幂等表。",
        "summary": "幂等处理要点",
        "category": "测试方法",
        "tags": "支付,幂等",
        "moduleId": feature_id,
        "sourceType": "ai_output",
        "producer": "bug_analysis",
    })
    draft_id = int(draft["id"])
    check("待审条目默认 pending", draft["reviewStatus"] == "pending", str(draft["reviewStatus"]))

    drafts_after = api.data("GET", "/api/v1/knowledge/drafts?pageSize=100")
    check("待审池新增 1 条",
          drafts_after["total"] == drafts_before["total"] + 1,
          f"{drafts_before['total']} -> {drafts_after['total']}")
    after = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")
    check("待审条目不进入正式库",
          after["total"] == before["total"],
          f"{before['total']} -> {after['total']}")

    reviewed = api.data("POST", f"/api/v1/knowledge/drafts/{draft_id}/review",
                        {"action": "approve", "reviewer": "yichengren", "comment": "内容可信"})
    article_id = int(reviewed["articleId"])
    check("审核通过生成正式条目", article_id > 0, str(reviewed))

    published = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")
    check("正式库 +1", published["total"] == before["total"] + 1,
          f"{before['total']} -> {published['total']}")

    hit = api.data("GET", "/api/v1/knowledge/search?keyword=幂等")
    check("审核通过后可被全文检索命中",
          any(item["id"] == article_id for item in hit["list"]),
          str(hit.get("matchMode")))

    versioned = api.data("PUT", f"/api/v1/knowledge/articles/{article_id}", {
        "title": "支付回调幂等方案（修订）",
        "content": "回调按交易流水号去重，落库前先查幂等表，并补充重试退避策略。",
        "changeLog": "补充重试退避",
    })
    check("编辑已发布条目产生待审版本", versioned["status"] == "pending", str(versioned))

    current = api.data("GET", f"/api/v1/knowledge/articles/{article_id}")
    check("待审版本不影响当前正文", "幂等表，并补充" not in (current["content"] or ""),
          current["content"])

    versions = api.data("GET", f"/api/v1/knowledge/articles/{article_id}/versions")
    check("版本列表含 v1 与待审 v2",
          {v["version"] for v in versions} == {1, 2},
          str([v["version"] for v in versions]))
    check("待审版本状态为 pending",
          any(v["version"] == 2 and v["reviewStatus"] == "pending" for v in versions),
          str(versions))

    diff = api.data("GET", f"/api/v1/knowledge/articles/{article_id}/diff?from=1&to=2")
    check("版本 diff 返回差异行", diff["changed"] and diff["added"] >= 1, str(diff))

    applied = api.data("POST", f"/api/v1/knowledge/articles/{article_id}/versions/2/review",
                       {"action": "approve", "reviewer": "yichengren"})
    check("审核通过版本后 current_version 前移", applied["version"] == 2, str(applied["version"]))
    updated = api.data("GET", f"/api/v1/knowledge/articles/{article_id}")
    check("审核通过后正文已更新", "重试退避" in (updated["content"] or ""), updated["content"])

    searchable = api.data("GET", "/api/v1/knowledge/search?keyword=退避策略")
    check("新版本正文可被检索", searchable["total"] >= 1, str(searchable["total"]))

    # 引用保护：登记一条引用后禁止撤销
    from aitp.db.connection import get_standalone_conn
    from aitp.repositories.knowledge_ref_repo import KnowledgeRefRepository

    conn = get_standalone_conn(pathlib.Path(api.app.config["AITP_DB_PATH"]))
    try:
        KnowledgeRefRepository(conn).add_ref(article_id, "module", feature_id, "验证码时效")
        conn.commit()
    finally:
        conn.close()

    blocked = api.expect("被引用的条目撤销返回 140104", "POST",
                         f"/api/v1/knowledge/articles/{article_id}/revoke",
                         ErrorCode.RESOURCE_REFERENCED)
    refs = blocked.get("data", {}) or {}
    check("140104 回传引用清单", len(refs.get("refs") or []) == 1, str(refs))

    conn = get_standalone_conn(pathlib.Path(api.app.config["AITP_DB_PATH"]))
    try:
        KnowledgeRefRepository(conn).remove_ref(article_id, "module", feature_id)
        conn.commit()
    finally:
        conn.close()

    revoked = api.data("POST", f"/api/v1/knowledge/articles/{article_id}/revoke",
                       {"operator": "yichengren"})
    check("解除引用后可撤销", revoked["reviewStatus"] == "revoked", str(revoked))

    published_now = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")
    check("撤销后退出正式库",
          all(item["id"] != article_id for item in published_now["list"]),
          str([item["id"] for item in published_now["list"]]))

    restored = api.data("POST", f"/api/v1/knowledge/articles/{article_id}/restore")
    check("恢复后回到已发布", restored["reviewStatus"] == "published", str(restored))

    rejected = api.data("POST", "/api/v1/knowledge/drafts", {
        "title": "待驳回的条目", "content": "内容待评审", "sourceType": "manual",
    })
    result = api.data("POST", f"/api/v1/knowledge/drafts/{rejected['id']}/review",
                      {"action": "reject", "reviewer": "yichengren", "comment": "证据不足"})
    check("驳回不产生正式条目", result["articleId"] is None, str(result))
    api.expect("已审核条目不可重复审核", "POST", f"/api/v1/knowledge/drafts/{rejected['id']}/review",
               ErrorCode.ILLEGAL_OPERATION, {"action": "approve"})


def check_reports(api: Api, project_id: int) -> None:
    report = api.data("POST", "/api/v1/reports", {
        "projectId": project_id, "title": "冒烟测试报告", "reportType": "summary",
    })
    report_id = int(report["id"])
    check("报告编号自动生成", str(report.get("reportNo", "")).startswith("RPT-"),
          str(report.get("reportNo")))

    items = api.data("PUT", f"/api/v1/reports/{report_id}/items", {
        "items": [
            {"section": "overview", "title": "概述", "content": "本轮共 5 条用例", "sortOrder": 1},
            {"section": "conclusion", "title": "结论", "content": "阻断项 0 个", "sortOrder": 2},
        ],
    })
    check("报告条目写入成功", len(items["items"]) == 2, str(items))

    detail = api.data("GET", f"/api/v1/reports/{report_id}")
    check("报告详情回传条目", len(detail["items"]) == 2, str(detail["items"]))

    api.expect("报告不存在返回 140100", "GET", "/api/v1/reports/999999", ErrorCode.RESOURCE_NOT_FOUND)


def check_workbench(api: Api, project_id: int) -> None:
    overview = api.data("GET", f"/api/v1/workbench/overview?projectId={project_id}")
    metrics = overview["metrics"]

    cases = api.data("GET", f"/api/v1/test-cases?projectId={project_id}&pageSize=100")
    bugs = api.data("GET", f"/api/v1/bugs?projectId={project_id}&pageSize=100")
    reqs = api.data("GET", f"/api/v1/requirements?projectId={project_id}&pageSize=100")
    reports = api.data("GET", f"/api/v1/reports?projectId={project_id}&pageSize=100")
    articles = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")

    check("工作台用例总数与实时表一致", metrics["caseTotal"] == cases["total"],
          f"{metrics['caseTotal']} vs {cases['total']}")
    check("工作台缺陷总数与实时表一致", metrics["bugTotal"] == bugs["total"],
          f"{metrics['bugTotal']} vs {bugs['total']}")
    check("工作台需求总数与实时表一致", metrics["requirementTotal"] == reqs["total"],
          f"{metrics['requirementTotal']} vs {reqs['total']}")
    check("工作台报告总数与实时表一致", metrics["reportTotal"] == reports["total"],
          f"{metrics['reportTotal']} vs {reports['total']}")
    check("工作台知识数只算已发布", metrics["knowledgeTotal"] == articles["total"],
          f"{metrics['knowledgeTotal']} vs {articles['total']}")

    passed = sum(1 for item in cases["list"] if item["status"] == "pass")
    expected_rate = round(passed / cases["total"], 4) if cases["total"] else 0.0
    check("工作台用例通过率计算正确", metrics["casePassRate"] == expected_rate,
          f"{metrics['casePassRate']} vs {expected_rate}")

    check("用例状态分桶总和等于用例总数",
          sum(overview["caseStatus"].values()) == cases["total"],
          str(overview["caseStatus"]))
    check("缺陷状态分桶总和等于缺陷总数",
          sum(overview["bugStatus"].values()) == bugs["total"],
          str(overview["bugStatus"]))
    shortcuts = overview["shortcuts"]
    check("快捷入口最多 10 个", len(shortcuts) == 10, str(len(shortcuts)))
    check("快捷入口字段完整",
          all(s.get("key") and s.get("label") and s.get("path") for s in shortcuts),
          str(shortcuts[:1]))
    check("快捷入口不重复",
          len({s["key"] for s in shortcuts}) == len(shortcuts),
          str([s["key"] for s in shortcuts]))
    check("待审池数量可从概览读到", isinstance(overview["pendingKnowledge"], int),
          str(overview["pendingKnowledge"]))


def check_settings(api: Api, workdir: pathlib.Path) -> None:
    secret = "sk-smoke-test-1234567890abcdef"
    profile = api.data("POST", "/api/v1/settings/ai/profiles", {
        "name": "冒烟云端模型", "provider": "openai", "model": "gpt-4o-mini",
        "apiKey": secret, "priority": 100,
    })
    profile_id = int(profile["id"])
    check("密钥只回传掩码",
          profile.get("apiKeyMasked") and secret not in json_dump(profile),
          profile.get("apiKeyMasked"))
    check("档案标记已配置密钥", profile.get("hasApiKey") is True, str(profile))

    listed = api.data("GET", "/api/v1/settings/ai/profiles")
    check("档案列表不含明文密钥", secret not in json_dump(listed), "明文泄露")
    single = api.data("GET", f"/api/v1/settings/ai/profiles/{profile_id}")
    check("档案详情不含明文密钥", secret not in json_dump(single), "明文泄露")

    masked = api.data("POST", f"/api/v1/settings/ai/profiles/{profile_id}/api-key",
                      {"apiKey": "sk-rotated-9876543210abcdef"})
    check("轮换密钥后只返回新掩码",
          secret not in json_dump(masked) and "sk-rotated" not in json_dump(masked),
          str(masked))

    providers = api.data("GET", "/api/v1/settings/ai/providers")
    check("供应商清单可读", len(providers) >= 4, str(len(providers)))

    status = api.data("GET", "/api/v1/settings/ai/status")
    check("AI 生效状态含来源层与候选",
          isinstance(status.get("sources"), dict) and isinstance(status.get("candidates"), list),
          json_dump(status)[:200])
    check("AI 生效状态不含明文密钥", secret not in json_dump(status), "明文泄露")

    draft_key = "sk-draft-only-1234567890"
    probe = api.data("POST", "/api/v1/settings/ai/selfcheck", {
        "provider": "custom", "protocol": "openai_compat",
        "baseUrl": "http://127.0.0.1:59998", "model": "probe-model",
        "apiKey": draft_key, "timeout": 3,
    })
    check("自检失败时给出可操作提示",
          probe.get("ok") is False and bool(probe.get("hint")), json_dump(probe)[:200])
    check("自检回传不含临时密钥明文", draft_key not in json_dump(probe), "明文泄露")
    check("自检标注用了未保存配置", probe.get("draft") is True, json_dump(probe)[:120])

    api.expect("模型列表在服务不可达时返回错误码",
               "POST", "/api/v1/settings/ai/models", ErrorCode.AI_UNREACHABLE,
               {"provider": "custom", "protocol": "openai_compat",
                "baseUrl": "http://127.0.0.1:59998", "model": "probe-model", "timeout": 3})

    storage = api.data("GET", "/api/v1/knowledge/storage")
    check("知识库存放状态可读",
          isinstance(storage.get("storagePath"), str) and "fileMode" in storage,
          json_dump(storage)[:200])

    config = api.data("GET", "/api/v1/settings/config")
    check("系统配置可读", isinstance(config, list), str(type(config)))
    api.data("PUT", "/api/v1/settings/config", {
        "items": [{"key": "knowledge.storage_path", "value": str(workdir / "knowledge"), "valueType": "string"}],
    })
    updated = api.data("GET", "/api/v1/settings/config")
    hit = [item for item in updated if item["key"] == "knowledge.storage_path"]
    check("系统配置写入生效",
          bool(hit) and bool(hit[0]["value"] == str(workdir / "knowledge")), str(hit))

    api.data("DELETE", f"/api/v1/settings/ai/profiles/{profile_id}")
    api.expect("软删后查档案返回 140100", "GET", f"/api/v1/settings/ai/profiles/{profile_id}",
               ErrorCode.RESOURCE_NOT_FOUND)


def check_audit(api: Api, project_id: int) -> None:
    logs = api.data("GET", "/api/v1/settings/audit-logs?pageSize=100")
    check("写操作产生审计日志", logs["total"] > 0, str(logs["total"]))
    actions = {item["action"] for item in logs["list"]}
    check("审计日志覆盖创建动作", "create" in actions, str(actions))

    scoped = api.data("GET",
                      f"/api/v1/settings/audit-logs?resourceType=project&resourceId={project_id}")
    check("按资源过滤审计日志", scoped["total"] >= 1, str(scoped["total"]))

    resources = api.data("GET",
                         f"/api/v1/settings/audit-logs/resources?resourceType=project&resourceId={project_id}")
    check("按资源查审计清单", len(resources) >= 1, str(resources))

    detail = api.data("GET", "/api/v1/settings/audit-logs?pageSize=5")
    check("审计条目含 traceId 与操作人",
          all("traceId" in item and "actor" in item for item in detail["list"]),
          str(detail["list"][:1]))


def check_meta(api: Api) -> None:
    enums = api.data("GET", "/api/v1/meta/enums")
    check("枚举字典含缺陷状态机", "bugTransitions" in enums, str(sorted(enums.keys())))
    caps = api.data("GET", "/api/v1/meta/capabilities")
    check("能力目录含 19 项", len(caps) == 19, str(len(caps)))
    codes = {item["code"] for item in caps}
    check("能力目录覆盖需求确认与新增脚本能力",
          "requirement_confirm" in codes and "knowledge_assistant" not in codes
          and "prompt_optimize" in codes
          and "api_test_design" in codes and "risk_assessment" in codes
          and "automation_script" in codes and "performance_script" in codes,
          str(sorted(codes)))
    check("重复能力与旧需求评审编号已下线",
          "requirement_review" not in codes and "requirement_clarify" not in codes
          and "knowledge_qa" not in codes,
          str(sorted(codes)))


def json_dump(value: object) -> str:
    import json
    return json.dumps(value, ensure_ascii=False)


# ---------------- AI 实验室 ----------------

#: 实验室能力的样例变量（10 个主能力 + 6 个提示词库能力 + 2 个脚本能力 + 1 个造数能力）
LAB_SAMPLES: dict[str, JsonDict] = {
    "requirement_confirm": {"requirement": "手机号+验证码登录，验证码 5 分钟有效", "projectName": "示例电商"},
    "testpoint_generation": {"requirement": "手机号+验证码登录", "projectName": "示例电商", "moduleCount": "3"},
    "case_generation": {"requirement": "手机号+验证码登录", "projectName": "示例电商", "caseTypes": "functional", "count": "5"},
    "xmind_to_case": {"xmindOutline": "登录\n  验证码\n    超时", "projectName": "示例电商", "caseTypes": "functional"},
    "bug_analysis": {
        "bugTitle": "支付回调金额翻倍", "bugDescription": "重复推送导致金额翻倍",
        "reproSteps": "1. 下单 2. 重复回调", "expectedResult": "金额只扣一次",
        "actualResult": "金额扣两次", "env": "测试环境", "version": "v1.2.0",
    },
    "log_analysis": {"logContent": "NullPointerException at OrderService:88", "errorType": "NPE", "context": "支付"},
    "sql_analysis": {"sqlContent": "SELECT * FROM orders", "schemaDesc": "orders(id, amount)", "intent": "查订单"},
    "regression_generation": {"changeScope": "支付回调改造", "affectedModules": "交易/支付", "projectName": "示例电商"},
    "report_generation": {
        "projectName": "示例电商", "reportType": "迭代", "periodStart": "2026-10-01",
        "periodEnd": "2026-10-31", "metricsJson": "{}", "caseSummary": "10 条", "bugSummary": "2 个",
    },
    "project_progress_board": {
        "projectName": "示例电商", "progressData": "完成 60%", "period": "第 3 周", "milestones": "登录上线",
    },
    "api_test_design": {
        "projectName": "示例电商",
        "apiDescription": "POST /api/v1/orders 提交订单，参数 productId、quantity",
        "baseUrl": "https://api.example.com",
    },
    "test_plan": {
        "projectName": "示例电商", "period": "第 3 周",
        "requirementSummary": "登录与下单", "modules": "登录、交易",
    },
    "case_review": {"cases": "用例1：正常登录", "requirement": "手机号+验证码登录", "criteria": "覆盖度"},
    "bug_classification": {
        "bugTitle": "支付回调金额翻倍", "bugDescription": "重复推送导致金额翻倍",
        "modulePath": "示例电商/交易模块",
    },
    "risk_assessment": {
        "projectName": "示例电商", "changeScope": "支付回调改造",
        "metricsJson": "{\"passRate\": 0.92}",
    },
    "automation_script": {
        "projectName": "示例电商", "targetType": "web（网页 / Web 端）", "framework": "selenium",
        "language": "Python", "appPlatforms": "（未提供）",
        "scenario": "登录后查询订单列表", "baseUrl": "https://www.example.com",
        "extraRequirements": "失败时截图",
    },
    "performance_script": {
        "projectName": "示例电商", "scenario": "下单接口压测", "baseUrl": "https://api.example.com",
        "threads": "20", "duration": "60", "rampUp": "10", "dataFile": "（未提供）",
        "extraRequirements": "（未提供）",
    },
    "test_data_generation": {
        "projectName": "示例电商", "dialect": "MySQL 8.0",
        "intent": "给订单表造 20 条数据，覆盖三种订单状态与金额为 0 的边界值",
        "schemaDesc": "orders(id, user_id, amount, status, created_at)", "rowCount": "20",
    },
}


class MockAI:
    """进程内 mock OpenAI 服务，供 AI 相关接口离线验证。"""

    def __init__(self) -> None:
        import socket
        import threading

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = int(probe.getsockname()[1])

        sys.path.insert(0, str(ROOT / "backend" / "scripts"))
        import mock_openai_server

        self._thread = threading.Thread(
            target=mock_openai_server.serve,
            args=(self.port, mock_openai_server.OpenAIHandler),
            daemon=True,
        )
        self._thread.start()
        self._wait_ready()

    def _wait_ready(self) -> None:
        import socket
        import time

        for _ in range(100):
            try:
                with socket.create_connection(("127.0.0.1", self.port), timeout=0.2):
                    return
            except OSError:
                time.sleep(0.05)
        raise RuntimeError("mock AI 服务启动失败")

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"


def _sse_frames(iterator: Any) -> list[JsonDict]:
    """把 SSE 字节流解析成帧列表。"""
    import json

    frames: list[JsonDict] = []
    buffer = ""
    for chunk in iterator:
        text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
        buffer += text
        while "\n\n" in buffer:
            raw, buffer = buffer.split("\n\n", 1)
            for line in raw.splitlines():
                if line.startswith("data: "):
                    frames.append(cast(JsonDict, json.loads(line[6:])))
    return frames


def _chained_stream(head: Any, rest: Any) -> Any:
    """把已经取出的第一帧放回流头部，供 _sse_frames 完整解析。"""
    yield head
    yield from rest


def check_lab(api: Api, project_id: int, feature_id: int) -> None:
    mock = MockAI()
    saved_env = {k: os.environ.get(k) for k in ("AITP_AI_PROVIDER", "AITP_AI_BASE_URL",
                                               "AITP_AI_API_KEY", "AITP_AI_MODEL")}
    os.environ["AITP_AI_PROVIDER"] = "custom"
    os.environ["AITP_AI_BASE_URL"] = mock.base_url
    os.environ["AITP_AI_API_KEY"] = "sk-test"
    os.environ["AITP_AI_MODEL"] = "mock-gpt-4o-mini"
    try:
        _check_lab_inner(api, project_id, feature_id)
    finally:
        for key, value in saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def _check_lab_inner(api: Api, project_id: int, feature_id: int) -> None:
    caps = api.data("GET", "/api/v1/lab/capabilities")
    check("能力目录含 19 项", len(caps) == 19, str(len(caps)))
    main_caps = api.data("GET", "/api/v1/lab/main-capabilities")
    check("实验室主能力 19 个", len(main_caps) == 19, str(len(main_caps)))
    check("每个能力都有卡片入口",
          {item["code"] for item in caps} == {item["code"] for item in main_caps},
          str(sorted({item["code"] for item in caps} ^ {item["code"] for item in main_caps})))
    check("扩展能力已并入核心能力",
          sum(1 for item in main_caps if item.get("category") == "core") == 19
          and sum(1 for item in main_caps if item.get("category") == "extended") == 0,
          str([item.get("category") for item in main_caps]))
    check("能力目录含提示词编号",
          all(item.get("promptCode") for item in main_caps), str(main_caps[:1]))
    check("能力卡片带提示词库元数据",
          all(item.get("promptTitle") and item.get("promptDescription") is not None
              for item in main_caps), str(main_caps[:1]))
    check("能力卡片区分必填与可选变量",
          all("requiredVariables" in item and "optionalVariables" in item
              for item in main_caps), str(main_caps[:1]))
    prompts = api.data("GET", "/api/v1/lab/prompts")
    check("提示词库列出全部 23 个模板", len(prompts) == 23, str(len(prompts)))

    # 缺变量 → 120102 并列出缺失名
    body = api.expect("缺必填变量返回 120102", "POST", "/api/v1/lab/requirement_confirm/run",
                      ErrorCode.PROMPT_VAR_MISSING, {"variables": {"projectName": "示例电商"}})
    missing = (body.get("data") or {}).get("missing")
    check("120102 列出缺失变量名", missing == ["requirement"], str(missing))

    api.expect("未知能力返回 100100", "POST", "/api/v1/lab/not_a_capability/run",
               ErrorCode.PARAM_ERROR, {"variables": {}})

    # 功能点未启用该能力 → 140105
    api.expect("功能点未绑定该能力返回 140105", "POST", "/api/v1/lab/requirement_confirm/run",
               ErrorCode.ILLEGAL_OPERATION,
               {"moduleId": feature_id, "variables": {"requirement": "登录", "projectName": "示例电商"}})
    api.expect("功能点停用该能力返回 140105", "POST", "/api/v1/lab/log_analysis/run",
               ErrorCode.ILLEGAL_OPERATION,
               {"moduleId": feature_id, "variables": {"logContent": "NPE"}})

    # 10 个主能力 + 6 个扩展能力逐个跑通
    task_ids: list[int] = []
    for code, variables in LAB_SAMPLES.items():
        result = api.data("POST", f"/api/v1/lab/{code}/run", {"variables": variables})
        task_ids.append(int(result["taskId"]))
        check(f"能力 {code} 执行成功且有产出",
              bool(result.get("output")) and bool(result.get("model")),
              str(result)[:200])
        check(f"能力 {code} 返回降级信息",
              isinstance(result.get("degrade"), dict), str(result.get("degrade")))
        check(f"能力 {code} 给出数据外发边界提示",
              bool(result.get("egressWarning")) or result.get("egress") == "none",
              f"egress={result.get('egress')}")

    # JSON 能力的产出可解析
    case_result = api.data("POST", "/api/v1/lab/case_generation/run",
                           {"variables": LAB_SAMPLES["case_generation"]})
    check("JSON 能力产出已解析为结构化数据", case_result.get("parsed") is not None,
          str(case_result.get("parsed"))[:120])

    # 功能点已绑定的能力带 moduleId 也能跑
    bound = api.data("POST", "/api/v1/lab/bug_analysis/run", {
        "moduleId": feature_id,
        "variables": LAB_SAMPLES["bug_analysis"],
    })
    check("功能点已启用能力可带 moduleId 执行", bool(bound.get("output")), str(bound)[:200])
    check("功能点执行返回归属路径", "验证码时效" in (bound.get("modulePath") or ""),
          str(bound.get("modulePath")))

    # 任务与会话落库
    task = api.data("GET", f"/api/v1/lab/tasks/{task_ids[0]}")
    check("任务状态为 success", task.get("status") == "success", str(task.get("status")))
    check("任务记录能力编号", task.get("capability") == "requirement_confirm", str(task.get("capability")))
    tasks = api.data("GET", "/api/v1/lab/tasks?pageSize=100")
    check("任务列表可查", tasks["total"] >= len(task_ids), str(tasks["total"]))

    sessions = api.data("GET", "/api/v1/lab/sessions?pageSize=100")
    check("会话已落库", sessions["total"] >= 1, str(sessions["total"]))
    session_id = int(sessions["list"][0]["id"])
    detail = api.data("GET", f"/api/v1/lab/sessions/{session_id}")
    check("会话详情含助手消息",
          any(item["role"] == "assistant" for item in detail.get("messages", [])),
          str(detail.get("messages"))[:200])

    # 流式：start → delta → usage → done，seq 单调
    resp = api.client.post("/api/v1/lab/requirement_confirm/stream", json={
        "variables": LAB_SAMPLES["requirement_confirm"],
    })
    check("流式响应类型为 text/event-stream",
          "text/event-stream" in (resp.headers.get("Content-Type") or ""),
          str(resp.headers.get("Content-Type")))
    frames = _sse_frames(resp.response)
    types = [str(frame.get("type")) for frame in frames]
    check("流式帧顺序为 start→delta→done",
          bool(types) and types[0] == "start" and "delta" in types and types[-1] == "done",
          str(types))
    seqs = [int(frame.get("seq", 0)) for frame in frames]
    check("流式帧 seq 单调递增", seqs == sorted(seqs) and len(set(seqs)) == len(seqs), str(seqs))
    check("流式 done 帧带产出", bool(frames[-1].get("output")), str(frames[-1])[:200])

    # 客户端中断 → 任务落库为 cancelled
    resp2 = api.client.post("/api/v1/lab/requirement_confirm/stream", json={
        "variables": LAB_SAMPLES["requirement_confirm"],
    })
    stream = cast(Any, resp2.response)
    next(stream)
    stream.close()
    cancelled = api.data("GET", "/api/v1/lab/tasks?status=cancelled&pageSize=100")
    check("客户端中断后任务落库 cancelled", cancelled["total"] >= 1, str(cancelled["total"]))

    # 显式取消：先取出 start 帧让生成器进入事件循环，再打取消接口，
    # 验证进程内注册表真的叫停了模型输出（而不是只改库、模型继续计费）。
    resp_cancel = api.client.post("/api/v1/lab/requirement_confirm/stream", json={
        "variables": LAB_SAMPLES["requirement_confirm"],
    })
    cancel_task_id = int(resp_cancel.headers.get("X-AITP-Task-Id") or 0)
    cancel_stream = cast(Any, resp_cancel.response)
    cancel_head = next(cancel_stream)
    cancel_result = api.data("POST", f"/api/v1/lab/tasks/{cancel_task_id}/cancel", {})
    cancel_frames = _sse_frames(_chained_stream(cancel_head, cancel_stream))
    cancel_detail = api.data("GET", f"/api/v1/lab/tasks/{cancel_task_id}")
    check("取消接口命中正在跑的流", bool(cancel_result.get("interrupted")), str(cancel_result)[:200])
    check("取消后流以用户取消的错误帧收尾",
          bool(cancel_frames) and str(cancel_frames[-1].get("type")) == "error"
          and "取消" in str(cancel_frames[-1].get("errorMessage")),
          str(cancel_frames[-1])[:200])
    check("取消后任务落库为 cancelled", str(cancel_detail.get("status")) == "cancelled",
          str(cancel_detail.get("status")))

    # 错密钥 → 只有一个 error 帧
    os.environ["AITP_AI_API_KEY"] = "sk-wrong-key"
    resp3 = api.client.post("/api/v1/lab/requirement_confirm/stream", json={
        "variables": LAB_SAMPLES["requirement_confirm"],
    })
    error_frames = [f for f in _sse_frames(resp3.response) if f.get("type") == "error"]
    check("错密钥只产出 1 个 error 帧", len(error_frames) == 1, str(len(error_frames)))
    check("error 帧带错误码", bool(error_frames and error_frames[0].get("errorCode")), str(error_frames[:1]))
    os.environ["AITP_AI_API_KEY"] = "sk-test"

    # 智能路由
    route = api.data("POST", "/api/v1/lab/route", {
        "text": "支付回调重复推送导致金额翻倍，这是个缺陷", "projectId": project_id, "topK": 3,
    })
    check("路由返回能力候选", len(route.get("capabilityRoutes") or []) >= 1, str(route))
    check("路由返回功能点候选", len(route.get("moduleRoutes") or []) >= 1, str(route))
    check("路由结果标记来源",
          route.get("source") in ("ai", "fallback"), str(route.get("source")))
    check("能力候选带功能点归属",
          "targetModuleId" in (route.get("capabilityRoutes") or [{}])[0], str(route.get("capabilityRoutes"))[:200])

    # 离线兜底：无模型时仍给出候选
    saved_key = os.environ.pop("AITP_AI_API_KEY")
    fallback_route = api.data("POST", "/api/v1/lab/route", {
        "text": "生成测试用例", "projectId": project_id, "topK": 3,
    })
    check("无模型时路由走 fallback 仍给候选",
          len(fallback_route.get("capabilityRoutes") or []) >= 1, str(fallback_route))
    if saved_key is not None:
        os.environ["AITP_AI_API_KEY"] = saved_key

    # 提示词优化
    optimized = api.data("POST", "/api/v1/ai/prompts/optimize", {
        "capability": "case_generation", "rawText": "写点用例",
    })
    check("优化返回改写文本", bool(optimized.get("optimized")), str(optimized)[:200])
    check("优化保留原文", optimized.get("original") == "写点用例", str(optimized.get("original")))
    check("优化返回差异", isinstance(optimized.get("diff"), list), str(type(optimized.get("diff"))))

    # 优化失败保留原文
    os.environ["AITP_AI_API_KEY"] = "sk-wrong-key"
    failed = api.data("POST", "/api/v1/ai/prompts/optimize", {
        "capability": "case_generation", "rawText": "写点用例",
    })
    check("优化失败保留原文不阻断", failed.get("optimized") == "写点用例", str(failed)[:200])
    check("优化失败给出提示", bool(failed.get("message")), str(failed.get("message")))
    os.environ["AITP_AI_API_KEY"] = "sk-test"

    # 不可达模型排第一：应先重试若干次，再按优先级降级到可用模型，而不是失败一次就放弃。
    # 这里同时建一个可用的库内档案——库里一旦有档案，环境变量那份就不再进候选链了。
    bad_profile = api.data("POST", "/api/v1/settings/ai/profiles", {
        "name": "不可达模型",
        "provider": "custom",
        "protocol": "openai_compat",
        "baseUrl": "http://127.0.0.1:1/v1",
        "model": "mock-model",
        "apiKey": "sk-test",
        "isActive": True,
        "priority": 900,
    })
    fallback_profile = api.data("POST", "/api/v1/settings/ai/profiles", {
        "name": "降级目标模型",
        "provider": "custom",
        "protocol": "openai_compat",
        "baseUrl": os.environ.get("AITP_AI_BASE_URL") or "",
        "model": os.environ.get("AITP_AI_MODEL") or "mock-model",
        "apiKey": "sk-test",
        "isActive": True,
        "priority": 100,
    })
    retried = api.data("POST", "/api/v1/lab/requirement_confirm/run", {
        "variables": LAB_SAMPLES["requirement_confirm"],
    })
    bad_attempts = [item for item in (retried.get("degrade") or {}).get("attempts") or []
                    if "不可达模型" in str(item.get("config"))]
    check("不可达模型记录重试次数",
          bool(bad_attempts) and int(bad_attempts[0].get("retries") or 0) == RETRY_LIMIT,
          str(bad_attempts)[:200])
    check("不可达模型重试后降级到下一候选出结果",
          bool(retried.get("output")) and bool((retried.get("degrade") or {}).get("degraded")),
          str(retried)[:200])
    for cleanup in (bad_profile, fallback_profile):
        api.data("DELETE", f"/api/v1/settings/ai/profiles/{cleanup['id']}")


def check_backups(api: Api, project_id: int, workdir: pathlib.Path) -> None:
    """备份 / 下载 / 恢复 / 删除，以及恢复前的强制文件名确认。"""
    created = api.data("POST", "/api/v1/settings/backups")
    name = str(created.get("name") or "")
    check("备份创建返回文件名", bool(name), str(created)[:200])
    check("备份落盘有大小", int(created.get("sizeBytes") or 0) > 0, str(created)[:200])

    listed = api.data("GET", "/api/v1/settings/backups")
    check("备份列表包含新建备份", any(item.get("name") == name for item in listed.get("list") or []),
          str(listed)[:200])
    check("备份返回库路径", bool(listed.get("dbPath")), str(listed)[:120])

    resp, _ = api.call("GET", f"/api/v1/settings/backups/{name}/download")
    check("备份可下载", resp.status_code == 200 and len(resp.data) > 0, f"status={resp.status_code}")
    check("下载走附件Disposition", "attachment" in resp.headers.get("Content-Disposition", ""),
          resp.headers.get("Content-Disposition", ""))
    # send_file 的句柄要显式释放，否则 Windows 上后续删除会被占用
    resp.close()

    api.expect("非法备份文件名被拒", "DELETE", "/api/v1/settings/backups/evil.txt",
               ErrorCode.PARAM_ERROR)
    resp, traversal = api.call("DELETE", "/api/v1/settings/backups/..%2F..%2Ft.db")
    check("路径穿越文件名被拒",
          traversal.get("code") in (ErrorCode.PARAM_ERROR, ErrorCode.NOT_FOUND_ENDPOINT,
                                    ErrorCode.METHOD_NOT_ALLOWED),
          f"code={traversal.get('code')} status={resp.status_code}")
    api.expect("下载接口拒绝非法文件名", "GET", "/api/v1/settings/backups/evil.txt/download",
               ErrorCode.PARAM_ERROR)
    api.expect("恢复缺少确认返回 140105", "POST", f"/api/v1/settings/backups/{name}/restore",
               ErrorCode.ILLEGAL_OPERATION, {"confirm": "wrong-name"})

    sentinel = int(api.data("POST", "/api/v1/requirements",
                            {"projectId": project_id, "title": "备份之后新增的需求"})["id"])
    restored = api.data("POST", f"/api/v1/settings/backups/{name}/restore", {"confirm": name})
    check("恢复返回备份名", restored.get("restored") == name, str(restored)[:200])
    check("恢复前自动生成安全备份", bool(restored.get("safetyBackup")), str(restored)[:200])
    api.expect("恢复回滚了备份之后的写入", "GET", f"/api/v1/requirements/{sentinel}",
               ErrorCode.RESOURCE_NOT_FOUND)

    after_restore = api.data("GET", "/api/v1/settings/backups")
    names = [str(item.get("name")) for item in after_restore.get("list") or []]
    check("安全备份进入备份列表", any(item.startswith("pre-restore") for item in names), str(names)[:200])

    api.data("DELETE", f"/api/v1/settings/backups/{name}")
    after_delete = api.data("GET", "/api/v1/settings/backups")
    check("删除后备份不再列出",
          all(item.get("name") != name for item in after_delete.get("list") or []), str(names)[:200])
    api.expect("删除不存在的备份报错", "DELETE", f"/api/v1/settings/backups/{name}",
               ErrorCode.RESOURCE_NOT_FOUND)

    # ---- 整包备份：库 + 附件 / 脚本 / 生成物 / 密钥 / 知识库正文 ----
    knowledge_root = workdir / "knowledge"
    attachments_root = workdir / "attachments"
    knowledge_root.mkdir(parents=True, exist_ok=True)
    attachments_root.mkdir(parents=True, exist_ok=True)
    knowledge_file = knowledge_root / "整包正文.md"
    attachment_file = attachments_root / "整包附件.txt"
    knowledge_file.write_text("# 整包正文\n", encoding="utf-8")
    attachment_file.write_text("attachment", encoding="utf-8")
    api.data("PUT", "/api/v1/settings/config", {"items": [
        {"key": "knowledge.storage_path", "value": str(knowledge_root), "valueType": "string"}]})

    bundle = api.data("POST", "/api/v1/settings/backups/bundle")
    bundle_name = str(bundle.get("name") or "")
    check("整包备份返回 zip 名", bundle_name.endswith(".zip"), str(bundle)[:200])
    check("整包统计带文件数", int(bundle.get("files") or 0) >= 3, str(bundle)[:200])
    listed_bundles = api.data("GET", "/api/v1/settings/backups").get("bundles") or []
    check("整包出现在备份列表",
          any(item.get("name") == bundle_name and item.get("kind") == "bundle"
              for item in listed_bundles),
          str(listed_bundles)[:200])
    bundle_resp, _ = api.call("GET", f"/api/v1/settings/backups/{bundle_name}/download")
    check("整包可下载", bundle_resp.status_code == 200 and len(bundle_resp.data) > 0,
          f"status={bundle_resp.status_code}")
    bundle_resp.close()

    knowledge_file.unlink()
    attachment_file.unlink()
    api.expect("整包恢复缺少文件覆盖确认被拒", "POST",
               f"/api/v1/settings/backups/{bundle_name}/restore", ErrorCode.ILLEGAL_OPERATION,
               {"confirm": bundle_name})
    bundle_restored = api.data("POST", f"/api/v1/settings/backups/{bundle_name}/restore",
                               {"confirm": bundle_name, "confirmFiles": "覆盖本机文件"})
    check("整包恢复回补知识库正文", knowledge_file.exists(), str(bundle_restored)[:200])
    check("整包恢复回补附件目录", attachment_file.exists(), str(bundle_restored)[:200])
    check("整包恢复自动留一份安全整包",
          str(bundle_restored.get("safetyBundle") or "").startswith("pre-bundle"),
          str(bundle_restored)[:200])
    api.data("DELETE", f"/api/v1/settings/backups/{bundle_name}")
    after_bundle_delete = api.data("GET", "/api/v1/settings/backups").get("bundles") or []
    check("删除整包后不再列出",
          all(item.get("name") != bundle_name for item in after_bundle_delete),
          str(after_bundle_delete)[:200])


def check_bulk_and_export(api: Api, project_id: int) -> None:
    """批量删除 + CSV/JSON 导出；放在最后跑，避免影响前面的计数断言。"""
    ids = [
        int(api.data("POST", "/api/v1/requirements",
                     {"projectId": project_id, "title": f"批量删除用需求 {i}"})["id"])
        for i in range(3)
    ]
    result = api.data("POST", "/api/v1/requirements/batch-delete",
                      {"ids": [*ids, 999_999]})
    check("批量删除返回成功数量", result.get("count") == 3, str(result)[:200])
    check("批量删除跳过不存在项", result.get("skipped") == [999_999], str(result)[:200])
    for item in ids:
        api.expect(f"批量删除后 {item} 不可查", "GET", f"/api/v1/requirements/{item}",
                   ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("空批量删除被拒", "POST", "/api/v1/requirements/batch-delete",
               ErrorCode.PARAM_ERROR, {"ids": []})

    for label, path in (
        ("需求", f"/api/v1/requirements/export?format=csv&projectId={project_id}"),
        ("缺陷", f"/api/v1/bugs/export?format=csv&projectId={project_id}"),
        ("用例", f"/api/v1/test-cases/export?format=csv&projectId={project_id}"),
        ("知识", "/api/v1/knowledge/articles/export?format=csv"),
    ):
        resp, _ = api.call("GET", path)
        text = resp.data.decode("utf-8", errors="replace")
        check(f"{label} CSV 导出成功", resp.status_code == 200, f"status={resp.status_code}")
        check(f"{label} CSV 带 UTF-8 BOM", resp.data.startswith(b"\xef\xbb\xbf"),
              repr(resp.data[:6]))
        check(f"{label} CSV 含表头", "编号" in text or "标题" in text or "ID" in text, text[:60])
        check(f"{label} 导出计数头", bool(resp.headers.get("X-AITP-Export-Count")),
              str(resp.headers.get("X-AITP-Export-Count")))

    resp, _ = api.call("GET", f"/api/v1/requirements/export?format=json&projectId={project_id}")
    payload = resp.get_json(silent=True)
    check("需求 JSON 导出带清单", isinstance(payload, dict) and "list" in payload, str(payload)[:120])

    api.expect("用例导出格式白名单", "GET", "/api/v1/test-cases/export?projectId=1&format=pdf",
               ErrorCode.PARAM_ERROR)
    api.expect("需求导出格式白名单", "GET", "/api/v1/requirements/export?format=pdf",
               ErrorCode.PARAM_ERROR)


def check_ai_records_and_search(api: Api, project_id: int) -> None:
    """AI 执行记录（证据/缺项/复核）与全局搜索。"""
    records = api.data("GET", "/api/v1/ai-records?pageSize=100")
    items = records.get("list") or []
    check("执行记录列表有数据", len(items) > 0, str(len(items)))
    check("执行记录带复核统计",
          all(k in records.get("stats") or {} for k in ("pending", "approved", "rejected")),
          str(records.get("stats")))
    if items:
        item = items[0]
        check("执行记录含风险等级与复核标记",
              "riskLevel" in item and "reviewRequired" in item, str(sorted(item.keys())))
        detail = api.data("GET", f"/api/v1/ai-records/{item['id']}")
        check("执行记录详情含证据/缺项/风险三段",
              all(k in detail for k in ("evidence", "missing", "risks")), str(sorted(detail.keys())))

    api.expect("执行记录不存在返回 140100", "GET", "/api/v1/ai-records/999999",
               ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("复核不存在的记录返回 140100", "POST", "/api/v1/ai-records/999999/review",
               ErrorCode.RESOURCE_NOT_FOUND, {"status": "approved"})

    if items:
        record_id = int(items[0]["id"])
        api.expect("复核状态非法返回 100100", "POST",
                   f"/api/v1/ai-records/{record_id}/review", ErrorCode.PARAM_ERROR,
                   {"status": "not-a-status"})
        reviewed = api.data("POST", f"/api/v1/ai-records/{record_id}/review",
                            {"status": "approved", "note": "冒烟复核通过"})
        check("复核写回成功", bool(reviewed.get("reviewedAt")) or
              reviewed.get("reviewStatus") == "approved", str(reviewed)[:200])
        after = api.data("GET", f"/api/v1/ai-records/{record_id}")
        check("复核后状态为已采纳", after.get("reviewStatus") == "approved", str(after)[:200])
        check("复核备注已落库", after.get("reviewNote") == "冒烟复核通过", str(after)[:200])

    # 复核是写操作，应留下审计
    audits = api.data("GET", "/api/v1/settings/audit-logs?pageSize=100")
    actions = [str(item.get("action")) for item in audits.get("list") or []]
    check("复核写入审计", "review" in actions, str(actions[:10]))

    # 全局搜索
    keyword = "冒烟检索关键词"
    api.data("POST", "/api/v1/requirements",
             {"projectId": project_id, "title": f"{keyword}-登录改造"})
    found = api.data("GET", f"/api/v1/search?q={keyword}")
    groups = found.get("groups") or []
    check("搜索命中需求分组", any(g.get("type") == "requirement" for g in groups), str(groups)[:200])
    hit = next((g for g in groups if g.get("type") == "requirement"), None)
    check("搜索结果含标题片段", bool(hit and hit.get("list")), str(hit)[:200])
    check("搜索返回总命中数", isinstance(found.get("total"), int) and found["total"] >= 1,
          str(found.get("total")))

    typed = api.data("GET", f"/api/v1/search?q={keyword}&types=requirement")
    check("搜索类型过滤生效",
          all(g.get("type") == "requirement" for g in typed.get("groups") or []),
          str(typed.get("groups"))[:200])

    api.expect("搜索未知类型被拒", "GET", "/api/v1/search?q=x&types=unknown", ErrorCode.PARAM_ERROR)
    api.expect("搜索空关键词被拒", "GET", "/api/v1/search?q=", ErrorCode.PARAM_ERROR)
    api.expect("搜索缺少关键词被拒", "GET", "/api/v1/search", ErrorCode.PARAM_ERROR)


def check_preflight(api: Api, project_id: int) -> None:
    """P0-1 执行前预检：缺项、完整度、主/辅助任务与执行顺序。"""
    result = api.data("POST", "/api/v1/lab/preflight", {
        "text": "支付回调重复推送导致金额翻倍",
        "projectId": project_id,
        "topK": 3,
    })
    tasks = result.get("tasks") or []
    check("预检给出候选任务", bool(tasks), str(result)[:200])
    check("预检区分主任务与辅助任务",
          any(item.get("role") == "primary" for item in tasks)
          and all(item.get("role") in ("primary", "auxiliary") for item in tasks),
          str([item.get("role") for item in tasks]))
    check("预检给出执行顺序", bool(result.get("order")), str(result.get("order")))
    check("预检给出整体完整度",
          isinstance(result.get("overallCompleteness"), (int, float)), str(result)[:200])
    check("预检给出模型可用标记", isinstance(result.get("modelReady"), bool), str(result)[:200])
    for task in tasks:
        check(f"预检任务 {task.get('capability')} 带必填与缺项清单",
              "required" in task and "missing" in task, str(task)[:200])
        break

    empty = api.data("POST", "/api/v1/lab/preflight", {"text": "支付回调重复推送导致金额翻倍"})
    check("不带项目的预检也能出结论", bool(empty.get("tasks") or empty.get("blockers")),
          str(empty)[:200])

    api.expect("预检 text 与 capabilities 同时为空返回 100100", "POST",
               "/api/v1/lab/preflight", ErrorCode.PARAM_ERROR, {"variables": {}})
    api.expect("预检 variables 非对象返回 100100", "POST",
               "/api/v1/lab/preflight", ErrorCode.PARAM_ERROR, {"text": "x", "variables": []})


def check_prompt_library(api: Api) -> None:
    """P1-5 提示词库：目录可见、单模板正文可取。"""
    listing = api.data("GET", "/api/v1/lab/prompts")
    check("提示词库有模板", bool(listing), str(len(listing) if listing else 0))
    if listing:
        first = listing[0]
        check("提示词目录含变量清单", "variables" in first, str(sorted(first.keys())))
        detail = api.data("GET", f"/api/v1/lab/prompts/{first['code']}")
        check("提示词详情含正文", bool(str(detail.get("body") or "").strip()), str(detail)[:160])
    api.expect("提示词不存在返回 120101", "GET",
               "/api/v1/lab/prompts/definitely-not-a-prompt", ErrorCode.PROMPT_NOT_FOUND)


def check_case_tree(api: Api, project_id: int, feature_id: int) -> None:
    """P1-3 用例树：功能点树上挂各自的用例。"""
    tree = api.data("GET", f"/api/v1/test-cases/tree?projectId={project_id}")
    nodes = tree.get("list") or []
    check("用例树返回功能点树", bool(nodes), str(len(nodes)))
    check("用例树带总用例数", isinstance(tree.get("total"), int), str(tree.get("total")))

    def find(items: list[JsonDict]) -> JsonDict | None:
        for item in items:
            if int(item.get("id") or 0) == feature_id:
                return item
            found = find(item.get("children") or [])
            if found:
                return found
        return None

    node = find(nodes)
    check("用例树能定位到功能点节点", node is not None, str(feature_id))
    if node:
        check("功能点节点挂着用例清单", isinstance(node.get("cases"), list), str(node.keys()))
        check("功能点节点带用例计数", isinstance(node.get("caseCount"), int), str(node.get("caseCount")))

    api.expect("用例树缺项目参数返回 100100", "GET",
               "/api/v1/test-cases/tree", ErrorCode.PARAM_ERROR)


def check_workbench_entries(api: Api) -> None:
    """P0-3 编排入口：草稿 → 发布 → 版本号递增。"""
    created = api.data("POST", "/api/v1/workbench/entries", {
        "code": "smoke-req-to-case",
        "name": "冒烟-需求转用例",
        "capability": "case_generation",
        "description": "冒烟用编排入口",
        "triggers": ["需求", "用例"],
        "required": ["requirement"],
        "workflow": [
            {"capability": "requirement_confirm", "name": "需求确认", "variables": {}},
            {"capability": "case_generation", "name": "生成用例", "variables": {}},
        ],
    })
    entry_id = int(created.get("id") or 0)
    check("入口创建成功", entry_id > 0, str(created)[:200])
    check("入口默认是草稿", created.get("status") == "draft", str(created.get("status")))
    check("入口带工作流步骤", len(created.get("workflow") or []) == 2, str(created.get("workflow")))

    listing = api.data("GET", "/api/v1/workbench/entries")
    check("入口列表可查", any(int(item.get("id") or 0) == entry_id
                              for item in listing.get("list") or []), str(listing)[:200])

    published = api.data("POST", f"/api/v1/workbench/entries/{entry_id}/publish")
    check("入口发布后状态为已发布", published.get("status") == "published", str(published)[:200])
    check("入口发布后版本号递增", int(published.get("version") or 0) >= 2,
          str(published.get("version")))

    detail = api.data("GET", f"/api/v1/workbench/entries/{entry_id}")
    check("入口详情带版本历史", bool(detail.get("versions")), str(detail)[:200])

    updated = api.data("PUT", f"/api/v1/workbench/entries/{entry_id}",
                       {"description": "冒烟改过的说明"})
    check("入口可编辑", updated.get("description") == "冒烟改过的说明", str(updated)[:200])

    api.expect("入口编码重复返回 140103", "POST", "/api/v1/workbench/entries",
               ErrorCode.UNIQUE_CONFLICT, {
                   "code": "smoke-req-to-case", "name": "重复编码",
                   "capability": "case_generation",
               })
    api.expect("入口不存在返回 140100", "GET",
               "/api/v1/workbench/entries/999999", ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("入口缺主能力返回 100100", "POST", "/api/v1/workbench/entries",
               ErrorCode.PARAM_ERROR, {"code": "smoke-no-cap", "name": "缺能力"})

    api.data("DELETE", f"/api/v1/workbench/entries/{entry_id}")


def check_validation_sandbox(api: Api, project_id: int) -> None:
    """P0-4 案例验证沙箱：期望 vs 实际。"""
    created = api.data("POST", "/api/v1/validation/cases", {
        "name": "冒烟-支付回调路由",
        "inputText": "支付回调重复推送导致金额翻倍",
        "capability": "bug_analysis",
        "expectedRoutes": ["bug_analysis"],
        "expectedMissing": [],
        "expectedRules": [],
        "status": "published",
    })
    case_id = int(created.get("id") or 0)
    check("验证案例创建成功", case_id > 0, str(created)[:200])
    check("验证案例保存期望路由", created.get("expectedRoutes") == ["bug_analysis"],
          str(created.get("expectedRoutes")))

    result = api.data("POST", f"/api/v1/validation/cases/{case_id}/run",
                      {"projectId": project_id})
    check("验证案例跑出比对结论", "passed" in result, str(sorted(result.keys())))
    check("比对结果含路由段", "route" in result, str(result)[:200])
    check("比对结果含缺项段", "missing" in result, str(result)[:200])
    check("比对结果含规则段", "rules" in result, str(result)[:200])
    check("期望命中的路由在实测里也命中",
          "bug_analysis" in [item.get("capability") for item in result.get("route", {}).get("actual", [])],
          str(result.get("route"))[:200])

    listing = api.data("GET", "/api/v1/validation/cases")
    check("验证案例列表可查", any(int(item.get("id") or 0) == case_id
                                  for item in listing.get("list") or []), str(listing)[:200])
    after = api.data("GET", f"/api/v1/validation/cases/{case_id}")
    check("验证案例回写上次结果", bool(after.get("lastResult")), str(after)[:200])

    api.expect("验证案例缺输入返回 100100", "POST", "/api/v1/validation/cases",
               ErrorCode.PARAM_ERROR, {"name": "缺输入"})
    api.expect("验证案例不存在返回 140100", "POST",
               "/api/v1/validation/cases/999999/run", ErrorCode.RESOURCE_NOT_FOUND)

    api.data("DELETE", f"/api/v1/validation/cases/{case_id}")


def check_regression(api: Api, project_id: int) -> None:
    """P1-2 回归清单：清单 → 明细 → 在线执行回写。"""
    created = api.data("POST", "/api/v1/regressions", {
        "projectId": project_id,
        "title": "冒烟-支付回调回归",
        "changeScope": "回调幂等改造",
        "affectedModules": "支付回调",
        "version": "v0.0.1",
        "riskLevel": "high",
    })
    reg_id = int(created.get("id") or 0)
    check("回归清单创建成功", reg_id > 0, str(created)[:200])
    check("回归清单默认草稿", created.get("status") == "draft", str(created.get("status")))

    item = api.data("POST", f"/api/v1/regressions/{reg_id}/items", {
        "title": "重复推送只入账一次",
        "module": "支付回调",
        "priority": "P0",
        "reason": "幂等改造后需要回归",
        "verifyTarget": "并发回放同一报文",
    })
    item_id = int(item.get("id") or 0)
    check("回归明细添加成功", item_id > 0, str(item)[:200])
    check("回归明细默认未执行", item.get("result") == "not_executed", str(item.get("result")))

    items = api.data("GET", f"/api/v1/regressions/{reg_id}/items")
    check("回归明细可查", len(items.get("list") or []) == 1, str(items)[:200])

    executed = api.data("POST", f"/api/v1/regressions/{reg_id}/execute", {
        "results": [{"itemId": item_id, "result": "passed", "remark": "手工验证通过"}],
    })
    check("回归执行回写成功", item_id in (executed.get("updated") or []), str(executed)[:200])
    detail = api.data("GET", f"/api/v1/regressions/{reg_id}")
    check("回归明细结果已落库",
          (detail.get("items") or [{}])[0].get("result") == "passed", str(detail)[:240])
    check("回归清单状态按明细回推", detail.get("status") != "draft", str(detail.get("status")))

    api.expect("回归执行结果非法被跳过", "POST", f"/api/v1/regressions/{reg_id}/execute",
               ErrorCode.PARAM_ERROR, {"results": []})
    api.expect("回归清单不存在返回 140100", "GET",
               "/api/v1/regressions/999999", ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("回归明细不存在返回 140100", "PUT",
               f"/api/v1/regressions/{reg_id}/items/999999", ErrorCode.RESOURCE_NOT_FOUND,
               {"title": "x"})

    api.data("DELETE", f"/api/v1/regressions/{reg_id}")


def check_relations(api: Api) -> None:
    """P1-4 通用实体关联：建关系、双向可查。"""
    created = api.data("POST", "/api/v1/relations", {
        "sourceType": "case",
        "sourceId": 1,
        "relationType": "verifies",
        "targetType": "requirement",
        "targetId": 1,
        "remark": "冒烟关联",
    })
    rel_id = int(created.get("id") or 0)
    check("关联创建成功", rel_id > 0, str(created)[:200])

    types = api.data("GET", "/api/v1/relations/types")
    check("关联类型目录可用",
          "case" in (types.get("entityTypes") or [])
          and "verifies" in (types.get("relationTypes") or []), str(types))

    of_source = api.data("GET", "/api/v1/relations/of/case/1")
    check("来源侧能查到关联", bool(of_source.get("list")), str(of_source)[:200])
    of_target = api.data("GET", "/api/v1/relations/of/requirement/1")
    check("目标侧能查到关联（双向）", bool(of_target.get("list")), str(of_target)[:200])
    if of_source.get("list"):
        row = of_source["list"][0]
        check("关联带方向标记", row.get("direction") in ("in", "out"), str(row)[:200])
        check("关联带对端实体", row.get("peerType") and row.get("peerId"), str(row)[:200])

    listing = api.data("GET", "/api/v1/relations?relationType=verifies")
    check("关联可按类型过滤",
          all(str(item.get("relationType")) == "verifies" for item in listing.get("list") or []),
          str(listing)[:200])

    api.expect("关联类型非法返回 100100", "POST", "/api/v1/relations",
               ErrorCode.PARAM_ERROR, {
                   "sourceType": "case", "sourceId": 1,
                   "relationType": "not-a-type", "targetType": "bug", "targetId": 1,
               })
    api.expect("关联实体类型非法返回 100100", "POST", "/api/v1/relations",
               ErrorCode.PARAM_ERROR, {
                   "sourceType": "not-a-type", "sourceId": 1,
                   "relationType": "relates", "targetType": "bug", "targetId": 1,
               })

    api.data("DELETE", f"/api/v1/relations/{rel_id}")


def check_composite_guard(api: Api, project_id: int) -> None:
    """P0-2 顺序执行：参数校验与缺变量拦截。"""
    api.expect("顺序执行缺步骤返回 100100", "POST", "/api/v1/lab/composite/run",
               ErrorCode.PARAM_ERROR, {"steps": []})
    api.expect("顺序执行步骤非法返回 100100", "POST", "/api/v1/lab/composite/run",
               ErrorCode.PARAM_ERROR, {"steps": [{"capability": "not-a-capability"}]})

    missing = api.call("POST", "/api/v1/lab/composite/run", {
        "steps": [{"capability": "case_generation", "variables": {}}],
        "projectId": project_id,
    })
    check("步骤缺必填变量被拦下", missing[1].get("code") != ErrorCode.SUCCESS,
          str(missing[1])[:200])


def check_todos(api: Api, project_id: int) -> None:
    """任务清单：勾选、父子进度回推、视图筛选、批量与导出。"""
    parent = api.data("POST", "/api/v1/todos", {
        "title": "核对支付回调重复推送",
        "priority": "urgent",
        "category": "缺陷跟进",
        "dueDate": "2020-01-01",
        "projectId": project_id,
    })
    parent_id = int(parent.get("id") or 0)
    check("任务创建成功", parent_id > 0, str(parent)[:200])
    check("任务编号形如 TD-001", str(parent.get("todoNo") or "").startswith("TD-"), str(parent.get("todoNo")))
    check("过期任务标为逾期", parent.get("isOverdue") is True, str(parent)[:200])
    check("新任务默认待办", parent.get("status") == "open", str(parent.get("status")))

    child_a = api.data("POST", "/api/v1/todos", {"title": "拉取网关日志", "parentId": parent_id})
    child_b = api.data("POST", "/api/v1/todos", {"title": "补一条回归用例", "parentId": parent_id})
    child_a_id = int(child_a.get("id") or 0)
    child_b_id = int(child_b.get("id") or 0)
    check("子任务创建成功", child_a_id > 0 and child_b_id > 0, str(child_a)[:160])

    api.data("POST", f"/api/v1/todos/{child_a_id}/toggle")
    half = api.data("GET", f"/api/v1/todos/{parent_id}")
    check("子任务完成一半父任务进度 50", half.get("progress") == 50, str(half.get("progress")))
    check("父任务详情带子任务", len(half.get("children") or []) == 2, str(half.get("children"))[:200])

    api.data("POST", f"/api/v1/todos/{child_b_id}/toggle")
    full = api.data("GET", f"/api/v1/todos/{parent_id}")
    check("子任务全完成父任务自动完成", full.get("status") == "done", str(full.get("status")))
    check("子任务全完成父任务进度 100", full.get("progress") == 100, str(full.get("progress")))

    api.data("POST", f"/api/v1/todos/{child_a_id}/toggle")
    reopened = api.data("GET", f"/api/v1/todos/{parent_id}")
    check("子任务退回后父任务回到进行中", reopened.get("status") == "doing", str(reopened.get("status")))

    api.expect("任务非法优先级返回 100100", "POST", "/api/v1/todos",
               ErrorCode.PARAM_ERROR, {"title": "x", "priority": "asap"})
    api.expect("任务非法到期日返回 100100", "POST", "/api/v1/todos",
               ErrorCode.PARAM_ERROR, {"title": "x", "dueDate": "not-a-date"})
    api.expect("任务空标题返回 100100", "POST", "/api/v1/todos",
               ErrorCode.PARAM_ERROR, {"title": "   "})
    api.expect("任务进度越界返回 100100", "POST", "/api/v1/todos",
               ErrorCode.PARAM_ERROR, {"title": "x", "progress": 120})
    api.expect("任务父级不存在返回 140100", "POST", "/api/v1/todos",
               ErrorCode.RESOURCE_NOT_FOUND, {"title": "x", "parentId": 999999})
    api.expect("任务挂到自己下面被拦", "PUT", f"/api/v1/todos/{parent_id}",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": parent_id})
    api.expect("任务挂到自己子树下被拦", "PUT", f"/api/v1/todos/{parent_id}",
               ErrorCode.ILLEGAL_OPERATION, {"parentId": child_a_id})
    api.expect("任务列表非法视图返回 100100", "GET", "/api/v1/todos?view=bogus",
               ErrorCode.PARAM_ERROR)
    api.expect("任务批量空 ids 返回 100100", "POST", "/api/v1/todos/batch-update",
               ErrorCode.PARAM_ERROR, {"ids": []})
    api.expect("任务非数字 id 返回 100100", "GET", "/api/v1/todos/not-a-number",
               ErrorCode.PARAM_ERROR)

    overdue = api.data("GET", "/api/v1/todos?view=overdue")
    check("逾期视图取到逾期任务",
          any(int(item.get("id") or 0) == parent_id for item in overdue.get("list") or []),
          str(overdue)[:200])
    check("任务列表一次带回统计", isinstance(overdue.get("stats"), dict), str(overdue)[:160])

    done_view = api.data("GET", "/api/v1/todos?view=done")
    check("已完成视图不含未收口任务",
          all(item.get("status") in ("done", "cancelled") for item in done_view.get("list") or []),
          str(done_view)[:200])

    keyword = api.data("GET", "/api/v1/todos?keyword=支付回调")
    check("任务关键词检索命中",
          any(int(item.get("id") or 0) == parent_id for item in keyword.get("list") or []),
          str(keyword)[:200])

    by_category = api.data("GET", "/api/v1/todos?category=缺陷跟进")
    check("任务按分类筛选命中",
          any(int(item.get("id") or 0) == parent_id for item in by_category.get("list") or []),
          str(by_category)[:200])

    by_project = api.data("GET", f"/api/v1/todos?projectId={project_id}")
    check("任务按项目筛选命中",
          any(int(item.get("id") or 0) == parent_id for item in by_project.get("list") or []),
          str(by_project)[:200])

    top_only = api.data("GET", "/api/v1/todos")
    check("列表默认只返回顶层任务",
          all(int(item.get("parentId") or 0) == 0 for item in top_only.get("list") or []),
          str(top_only)[:200])

    stats = api.data("GET", "/api/v1/todos/stats")
    summary = stats.get("summary") or {}
    check("任务统计带总数与逾期", int(summary.get("total") or 0) >= 1 and int(summary.get("overdue") or 0) >= 1,
          str(summary)[:200])
    # 计数口径：stats 与列表都是顶层任务优先，子任务不占左栏计数
    top_level_rows = api.data("GET", "/api/v1/todos?pageSize=100").get("list") or []
    check("任务统计总数与顶层列表同口径",
          int(summary.get("total") or 0) == len(top_level_rows),
          f"summary={summary.get('total')} rows={len(top_level_rows)}")
    check("任务统计带完成率", isinstance(summary.get("completionRate"), float), str(summary)[:200])
    check("任务统计带状态分布", set((stats.get("byStatus") or {})) == {"open", "doing", "done", "cancelled"},
          str(stats.get("byStatus")))
    check("任务分类清单可用",
          any(item.get("category") == "缺陷跟进" for item in stats.get("categories") or []),
          str(stats.get("categories"))[:200])

    pinned = api.data("PUT", f"/api/v1/todos/{parent_id}", {"isPinned": True})
    check("任务置顶生效", pinned.get("isPinned") is True, str(pinned)[:200])
    starred = api.data("GET", "/api/v1/todos?view=starred")
    check("星标视图取到置顶任务",
          any(int(item.get("id") or 0) == parent_id for item in starred.get("list") or []),
          str(starred)[:200])

    batched = api.data("POST", "/api/v1/todos/batch-update",
                       {"ids": [child_a_id, child_b_id], "priority": "high"})
    check("任务批量更新生效", int(batched.get("count") or 0) == 2, str(batched)[:200])
    check("任务批量移动可用",
          api.data("POST", f"/api/v1/todos/{child_a_id}/move", {"direction": "down"}) is not None)
    api.expect("任务非法移动方向返回 100100", "POST", f"/api/v1/todos/{child_a_id}/move",
               ErrorCode.PARAM_ERROR, {"direction": "sideways"})
    check("任务重排序可用",
          int((api.data("POST", "/api/v1/todos/reorder", {"ids": [parent_id]}) or {}).get("count") or 0) == 1)

    for fmt in ("csv", "json"):
        resp, _ = api.call("GET", f"/api/v1/todos/export?format={fmt}")
        check(f"任务导出 {fmt}", resp.status_code == 200 and len(resp.data) > 0, str(resp.status_code))
    api.expect("任务导出非法格式返回 100100", "GET", "/api/v1/todos/export?format=xlsx",
               ErrorCode.PARAM_ERROR)

    removed = api.data("DELETE", f"/api/v1/todos/{parent_id}")
    check("删除父任务连带子任务", len(removed.get("removed") or []) == 3, str(removed)[:200])
    api.expect("子任务随父删除后不可取", "GET", f"/api/v1/todos/{child_a_id}",
               ErrorCode.RESOURCE_NOT_FOUND)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-smoke-"))
    try:
        app = build_app(workdir)
        api = Api(app)

        check_envelope(api)
        check_routes(api)
        project_id = check_projects(api)
        requirement_id = check_requirements(api, project_id)
        _, _, feature_id = check_module_tree(api, project_id)
        case_ids = check_cases(api, project_id, requirement_id, feature_id)
        check_bugs(api, project_id, feature_id, case_ids)
        check_knowledge(api, project_id, feature_id)
        check_reports(api, project_id)
        check_workbench(api, project_id)
        check_lab(api, project_id, feature_id)
        check_settings(api, workdir)
        check_audit(api, project_id)
        check_meta(api)
        check_bulk_and_export(api, project_id)
        check_backups(api, project_id, workdir)
        check_ai_records_and_search(api, project_id)
        check_preflight(api, project_id)
        check_prompt_library(api)
        check_case_tree(api, project_id, feature_id)
        check_workbench_entries(api)
        check_validation_sandbox(api, project_id)
        check_regression(api, project_id)
        check_relations(api)
        check_composite_guard(api, project_id)
        check_todos(api, project_id)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print()
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for name in _failures:
            print(f"  - {name}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
