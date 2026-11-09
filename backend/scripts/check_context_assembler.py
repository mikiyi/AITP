"""GAP-27 上下文装配门禁：三档口径、硬上限、召回真进 prompt、预览与实装一致、装配失败不阻断、结论落库。

守住的口径（清单 §5.3 GAP-27 功能点拆解 + 决策 3）：

- 三档 `lite / standard / full`，**默认 standard**；条目与字数都是硬上限，超限丢低分块并留痕；
- 召回起点复用既有字面检索（`instr` + `fts.hybrid_search`），不新建检索体系、不做向量检索；
- `preview()` 与 `assemble()` 同一实现，「调用前看到的」必须等于「实际带上的」；
- 装配是增强项：任一路召回失败只留痕，绝不挡住能力主链路；
- 结论落库：`biz_ai_record.context_json` 里能查回档位、条目数与 token 估计。

    python backend/scripts/check_context_assembler.py

退出码 0 = 全部通过。全程只用本地 mock 模型与临时库，绝不碰正式库与 8760 服务。
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

from aitp.ai import capability_codes as caps  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.prompts.registry import PromptRegistry  # noqa: E402
from aitp.services import context_assembler, lab_service  # noqa: E402

V1 = "/api/v1"
#: 统一包封的成功码（见 aitp.envelope.ok）
SUCCESS = 200000

#: 装配验收用的需求原文：词面同时命中功能点、已有用例、历史缺陷与知识条目
REQUIREMENT = "登录模块 手机号 验证码 超时 重发 登录"
#: 整句式需求：字面 instr 召回对长句天然弱，这条路只有 fts 那一路该开火（口径边界）
PROSE_REQUIREMENT = "登录模块的手机号验证码登录，验证码超时后重发要能成功下发新验证码"

#: mock 固定产出：两条合法用例（结构与 GAP-28 口径一致，避免装配门禁被校验层带红）
CASE_REPLY = [
    {
        "title": "验证码超时后重发成功",
        "caseType": "functional",
        "priority": "P1",
        "preconditions": "已打开登录页",
        "steps": [{"action": "等待 61 秒后点击重新发送", "expected": "下发新验证码"}],
        "expectedResult": "登录成功",
        "tags": "登录/验证码",
    },
    {
        "title": "验证码重发次数上限校验",
        "caseType": "functional",
        "priority": "P2",
        "preconditions": "已打开登录页",
        "steps": [{"action": "连续重发 6 次", "expected": "提示稍后再试"}],
        "expectedResult": "超出上限被拦截",
        "tags": "登录/验证码",
    },
]

_passed = 0
_failed = 0
#: mock 收到的每次请求体：断言「上下文到底有没有进提示词」只认这份实证
_CALLS: list[dict[str, Any]] = []
_CALLS_LOCK = threading.Lock()


def check(name: str, condition: bool, detail: str = "") -> None:
    """标准断言记录器：红绿都计数，detail 只在看失败时需要读。"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


# ---------------- 本地 mock 模型（绝不连真实服务） ----------------

class _MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 屏蔽 stderr 噪声
        pass

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return cast("dict[str, Any]", json.loads(self.rfile.read(length).decode("utf-8")))
        except Exception:
            return {}

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = self._read_json()
        with _CALLS_LOCK:
            _CALLS.append(body)
        payload = json.dumps({
            "id": "chatcmpl-ctx",
            "object": "chat.completion",
            "model": body.get("model") or "mock-ctx",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant",
                            "content": json.dumps(CASE_REPLY, ensure_ascii=False)},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 800, "completion_tokens": 200, "total_tokens": 1000},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_mock() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


def prompt_text_of_call(index: int = -1) -> str:
    """取某次 mock 请求里全部消息拼成的文本（用于证明上下文真的发给了模型）。"""
    with _CALLS_LOCK:
        if not _CALLS:
            return ""
        body = _CALLS[index]
    parts: list[str] = []
    for message in cast("list[dict[str, Any]]", body.get("messages") or []):
        content = message.get("content")
        parts.append(str(content if isinstance(content, str) else json.dumps(content,
                                                                             ensure_ascii=False)))
    return "\n".join(parts)


# ---------------- 请求脚手架 ----------------

def request_json(
    client: Any, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    if method == "GET":
        resp = client.get(path)
    elif method == "PUT":
        resp = client.put(path, json=payload or {})
    else:
        resp = client.post(path, json=payload or {})
    return int(resp.status_code), cast("dict[str, Any]", resp.get_json() or {})


def is_failure(status: int, body: dict[str, Any]) -> bool:
    return status != 200 or int(body.get("code") or 0) != SUCCESS


def data_of(
    client: Any, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    status, body = request_json(client, method, path, payload)
    if is_failure(status, body):
        raise AssertionError(f"{method} {path} 失败：status={status} body={str(body)[:300]}")
    got = body.get("data")
    return cast("dict[str, Any]", got) if isinstance(got, dict) else {"value": got}


# ---------------- 造数 ----------------

def publish_knowledge(client: Any, title: str, content: str) -> None:
    """知识条目要先过待审池才算发布，装配只认 published 的条目。"""
    draft = data_of(client, "POST", f"{V1}/knowledge/drafts", {
        "title": title, "content": content, "summary": title,
        "category": "通用", "sourceType": "manual"})
    data_of(client, "POST", f"{V1}/knowledge/drafts/{draft['id']}/review",
            {"action": "approve", "comment": "验收通过"})


def seed_project(client: Any, code: str, name: str) -> dict[str, int]:
    """建一套「需求 + 功能点 + 已有用例 + 历史缺陷 + 知识条目」的最小可召回数据。"""
    project = data_of(client, "POST", f"{V1}/projects", {
        "code": code, "name": name, "owner": "QA"})
    project_id = int(project["id"])
    module = data_of(client, "POST", f"{V1}/modules", {
        "projectId": project_id, "parentId": None, "nodeType": "module", "name": "登录模块"})
    submodule = data_of(client, "POST", f"{V1}/modules", {
        "projectId": project_id, "parentId": module["id"], "nodeType": "submodule",
        "name": "验证码"})
    feature = data_of(client, "POST", f"{V1}/modules", {
        "projectId": project_id, "parentId": submodule["id"], "nodeType": "feature",
        "name": "验证码重发"})
    data_of(client, "POST", f"{V1}/modules", {
        "projectId": project_id, "parentId": module["id"], "nodeType": "submodule",
        "name": "验证码超时"})
    requirement = data_of(client, "POST", f"{V1}/requirements", {
        "projectId": project_id, "title": REQUIREMENT,
        "content": "验证码 60 秒过期，过期后可重发，重发有次数上限。"})
    ids = {"project": project_id, "module": int(module["id"]),
           "submodule": int(submodule["id"]), "feature": int(feature["id"]),
           "requirement": int(requirement["id"])}
    for index in range(10):
        data_of(client, "POST", f"{V1}/test-cases", {
            "projectId": project_id, "moduleId": ids["feature"],
            "requirementId": ids["requirement"],
            "title": f"验证码超时后重发成功-变体{index}",
            "caseType": "functional", "priority": "P2",
            "preconditions": "已打开登录页",
            "steps": [{"action": f"等待 {61 + index} 秒后点击重新发送",
                       "expected": "下发新验证码"}],
            "expectedResult": "验证码重发成功", "tags": "登录/验证码",
        })
    for index in range(6):
        data_of(client, "POST", f"{V1}/bugs", {
            "projectId": project_id, "moduleId": ids["feature"],
            "title": f"验证码重发后旧验证码仍可用-{index}",
            "description": "重发接口没有让旧验证码失效，存在登录风险。", "severity": "P1"})
    for index in range(3):
        data_of(client, "POST", f"{V1}/bugs", {
            "projectId": project_id, "moduleId": ids["feature"],
            "title": f"验证码超时时间显示错误-{index}",
            "description": "倒计时读到的剩余秒数比服务端有效期长。", "severity": "P3"})
    publish_knowledge(client, "验证码时效与重发规范", "验证码有效期 60 秒，重发间隔不小于 10 秒。")
    publish_knowledge(client, "登录模块手机号规则", "手机号登录需先校验验证码，超时后重发。")
    publish_knowledge(client, "验证码倒计时口径", "倒计时以服务端剩余有效期为准，不允许本地估算。")
    publish_knowledge(client, "验证码重发频率限制", "验证码重发每分钟不超过 3 次，超时后重新计时。")
    return ids


def conn_of(db_path: pathlib.Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


# ---------------- A. 档位与变量口径（纯函数） ----------------

def tier_pure_checks() -> None:
    limits = context_assembler.TIER_LIMITS
    check("默认档是「标准」（决策 3）", context_assembler.DEFAULT_TIER == "standard"
          and context_assembler.TIER_STANDARD == "standard")
    check("三档齐备且都有 total / chars 硬上限",
          set(limits) == {"lite", "standard", "full"}
          and all({"total", "chars"} <= set(item) for item in limits.values()))
    monotonic = all(
        limits["lite"][kind] < limits["standard"][kind] < limits["full"][kind]
        for kind in ("module", "case", "bug", "knowledge", "total"))
    check("三档条目上限单调递增（正控：档位不是摆设）", monotonic, str(limits))
    check("三档字数上限单调递增",
          limits["lite"]["chars"] < limits["standard"]["chars"] < limits["full"]["chars"])
    check("档位中文标签齐备",
          context_assembler.TIER_LABELS["lite"] == "精简"
          and context_assembler.TIER_LABELS["standard"] == "标准"
          and context_assembler.TIER_LABELS["full"] == "完整")

    check("normalize_tier 认英文档名并大小写不敏感",
          context_assembler.normalize_tier("FULL") == "full")
    check("normalize_tier 认中文档位", context_assembler.normalize_tier("精简") == "lite")
    check("normalize_tier 拒绝脏值回落空串（红可证：不写死默认）",
          context_assembler.normalize_tier("ultra") == ""
          and context_assembler.normalize_tier(None) == "")

    kinds = context_assembler.CONTEXT_KINDS
    check("四种上下文种类齐备", set(kinds) == {"module", "case", "bug", "knowledge"})
    check("种类到提示词变量的映射覆盖全部种类",
          set(context_assembler.VARIABLE_OF_KIND) == set(kinds)
          and set(context_assembler.VARIABLE_OF_KIND.values()) ==
          {"contextModules", "contextCases", "contextBugs", "contextKnowledge"})
    check("档位码与能力声明同源（capability_codes 的 TIER_*）",
          context_assembler.TIER_LITE == caps.TIER_LITE
          and context_assembler.TIER_FULL == caps.TIER_FULL)

    check("估算 token：中日韩按字计", context_assembler.estimate_tokens("验证码重发") == 5,
          str(context_assembler.estimate_tokens("验证码重发")))
    check("估算 token：拉丁按 4 字符 1 token", context_assembler.estimate_tokens("abcdefgh") == 2)
    check("估算 token：空白不计", context_assembler.estimate_tokens("   ") == 0)
    check("估算 token：空文本为 0", context_assembler.estimate_tokens("") == 0)

    text = context_assembler.prompt_text({"contextCases": "- 已有用例"})
    check("拼接文本带种类小标题（界面/提示词同一份口径）",
          "#已有用例" in text and "- 已有用例" in text, text)

    empty_plan = context_assembler.ContextPlan(
        capability="case_generation", tier="standard", kinds=("case", "bug"))
    variables = empty_plan.variables()
    check("声明过但没召回到的种类写「没有召回到相关…」",
          "本次没有召回到相关已有用例" in variables["contextCases"], variables["contextCases"])
    check("能力没声明的种类一个变量都不注入（不把没查过说成没查到）",
          "contextModules" not in variables and "contextBugs" in variables,
          str(sorted(variables)))

    plan = context_assembler.ContextPlan(
        capability="case_generation", tier="standard", kinds=("case",),
        blocks=[context_assembler.ContextBlock("case", "TC-1", "标题", "正文", 3)])
    payload = plan.as_dict()
    required_keys = {"capability", "tier", "tierLabel", "projectId", "moduleId", "kinds",
                     "keywords", "itemCount", "counts", "limitTotal", "limitChars", "chars",
                     "estimatedTokens", "truncated", "skipped", "notes", "blocks"}
    check("装配结论字段齐备（落库与界面读的就是这份）",
          required_keys <= set(payload), str(sorted(set(payload) ^ required_keys))[:200])
    check("装配结论带条目数与上限对照",
          payload["itemCount"] == 1 and payload["limitTotal"] == 20
          and payload["limitChars"] == 2600, str(payload)[:200])
    check("块体自带中文种类标签",
          payload["blocks"][0]["kindLabel"] == "已有用例", str(payload["blocks"])[:160])
    check("块体提示词行带来源引用", "TC-1｜标题" in plan.blocks[0].as_prompt_line(),
          plan.blocks[0].as_prompt_line())

    check("关键词提取：长词优先且限量",
          len(context_assembler.keywords_of("验证码 验证码超时 重发 登录")) <= 8
          and context_assembler.keywords_of("验证码超时后重发")[0] in
          ("验证码超时后重发", "验证码"), str(context_assembler.keywords_of("验证码超时后重发")))
    check("关键词提取：空文本不硬造关键词", context_assembler.keywords_of("   ") == [])
    check("检索入口按能力取变量（用例取 requirement）",
          context_assembler.query_text_of(caps.spec_of("case_generation"),
                                          {"requirement": "验证码"}) == "验证码")
    check("未登记能力回落到常见需求变量（正控）",
          context_assembler.query_text_of(None, {"requirement": "回落文本"}) == "")

    clause, params = context_assembler._or_match(("title", "content"), ["验证码", "重发"])
    check("多关键词的字面召回占位符与参数同数（钉死 Incorrect bindings 复发）",
          clause.count("?") == len(params) == 4 and clause.count("instr") == 4,
          f"placeholders={clause.count('?')} params={len(params)}")
    one_clause, one_params = context_assembler._or_match(("title",), ["验证码"])
    check("单词单列同样成对（正控）",
          one_clause.count("?") == len(one_params) == 1, str(one_params))
    empty_clause, empty_params = context_assembler._or_match(("title",), [])
    check("无关键词时不给条件而不是空占位（负控）",
          empty_clause == "" and empty_params == [], str(empty_clause))


# ---------------- B. 档位解析优先级 ----------------

def tier_resolution_checks(client: Any, db_path: pathlib.Path) -> None:
    conn = conn_of(db_path)
    try:
        check("无任何覆盖时取默认标准档",
              context_assembler.resolve_tier(conn, "case_generation") == "standard")
        check("无覆盖时读的是系统配置缺失后的默认档（配置未写不报错）",
              context_assembler.resolve_tier(
                  conn, "case_generation", binding_config=None) == "standard")
        check("功能点绑定档位盖过系统配置",
              context_assembler.resolve_tier(
                  conn, "case_generation",
                  binding_config={"contextTier": "lite"}) == "lite")
        check("请求参数盖过功能点绑定（优先级：请求 > 绑定 > 配置 > 默认）",
              context_assembler.resolve_tier(
                  conn, "case_generation", requested="full",
                  binding_config={"contextTier": "lite"}) == "full")
        check("非法档位请求不报错，回落默认档",
              context_assembler.resolve_tier(conn, "case_generation",
                                             requested="ultra") == "standard")

        data_of(client, "PUT", f"{V1}/settings/config",
                {"items": [{"key": "ai.context.tier", "value": "lite", "valueType": "string"}]})
        check("系统配置写成 lite 后无覆盖即取 lite",
              context_assembler.resolve_tier(conn, "case_generation") == "lite")
        check("系统配置存在时请求参数仍然优先",
              context_assembler.resolve_tier(conn, "case_generation",
                                             requested="full") == "full")
        plan = context_assembler.assemble(conn, capability="case_generation",
                                         variables={"requirement": REQUIREMENT})
        check("装配结果跟着系统配置降档", plan.tier == "lite", plan.tier)
        data_of(client, "PUT", f"{V1}/settings/config",
                {"items": [{"key": "ai.context.tier", "value": "", "valueType": "string"}]})
        check("清空配置后回到默认标准档（正控：配置不是恒 lite）",
              context_assembler.resolve_tier(conn, "case_generation") == "standard")
        check("连接不可用时档位解析不崩（无 conn 也能算档）",
              context_assembler.resolve_tier(None, "case_generation",
                                             requested="standard") == "standard")
    finally:
        conn.close()


# ---------------- C. 召回、硬上限与预览 ----------------

def recall_checks(client: Any, app: Any, db_path: pathlib.Path, ids: dict[str, int],
                  mock_port: int) -> None:
    profile = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
        "name": "装配预览 mock 档案", "provider": "custom", "protocol": "openai_compat",
        "baseUrl": f"http://127.0.0.1:{mock_port}/v1", "model": "mock-ctx",
        "isLocal": True, "isActive": True, "priority": 290, "timeout": 10,
    })
    preview_profile_id = int(profile["id"])
    check("装配预览需要一个可用档案（本地 mock，不碰真实额度）", preview_profile_id > 0,
          str(profile)[:120])
    conn = conn_of(db_path)
    try:
        variables = {"requirement": REQUIREMENT}
        plans = {tier: context_assembler.assemble(
            conn, capability="case_generation", variables=variables,
            project_id=ids["project"], tier=tier) for tier in ("lite", "standard", "full")}
        counts = {tier: plan.as_dict()["itemCount"] for tier, plan in plans.items()}
        check("三档装配条目数单调递增（验收：三档可区分）",
              counts["lite"] <= counts["standard"] <= counts["full"]
              and counts["full"] > counts["lite"], str(counts))
        for tier, plan in plans.items():
            payload = plan.as_dict()
            limits = context_assembler.TIER_LIMITS[tier]
            check(f"{tier} 档总条目不超上限", payload["itemCount"] <= limits["total"],
                  str(payload["itemCount"]))
            check(f"{tier} 档总字数不超上限（token 成本硬约束）",
                  payload["chars"] <= limits["chars"], str(payload["chars"]))
            over = [kind for kind, value in payload["counts"].items()
                    if value > limits.get(kind, 999)]
            check(f"{tier} 档单种类条目不超上限", not over, str(payload["counts"]))

        standard = plans["standard"].as_dict()
        lite_counts = plans["lite"].as_dict()["counts"]
        check("三档单种类上限各自开火（造数都多于上限，装配结果正好等于上限）",
              lite_counts == {"module": 2, "case": 3, "bug": 2, "knowledge": 1}
              and standard["counts"]["module"] == 4
              and standard["counts"]["case"] == 8
              and standard["counts"]["bug"] == 5
              and plans["full"].as_dict()["counts"]["case"] == 10,
              f"lite={lite_counts} standard={standard['counts']}"
              f" full={plans['full'].as_dict()['counts']}")
        check("标准档触达总条数上限并留痕（正控：total 上限真的开火）",
              standard["itemCount"] == standard["limitTotal"] == 20
              and standard["truncated"] is True and standard["skippedCount"] >= 1,
              str({k: standard[k] for k in ("itemCount", "truncated", "skippedCount")}))
        check("精简档条目数严格少于标准档（档位差异可见）",
              plans["lite"].as_dict()["itemCount"] < standard["itemCount"],
              str(plans["lite"].as_dict()["itemCount"]))
        check("标准档已有用例被条数上限裁到 8 条并留痕（正控：上限真的开火）",
              standard["counts"]["case"] == context_assembler.TIER_LIMITS["standard"]["case"]
              and standard["truncated"] is True and standard["skipped"],
              str({k: standard[k] for k in ("counts", "truncated", "skippedCount")})[:220])
        lite = plans["lite"].as_dict()
        check("精简档总条数不超过 8 条（硬上限）",
              lite["itemCount"] <= 8 and lite["limitTotal"] == 8, str(lite["itemCount"]))

        assembled = plans["standard"]
        variables_out = assembled.variables()
        check("模块树路径确实进入 contextModules",
              "登录模块" in variables_out.get("contextModules", ""),
              str(variables_out.get("contextModules"))[:200])
        check("已有用例标题确实进入 contextCases",
              "验证码超时后重发成功-变体" in variables_out.get("contextCases", ""),
              str(variables_out.get("contextCases"))[:200])
        check("历史缺陷确实进入 contextBugs",
              "旧验证码仍可用" in variables_out.get("contextBugs", ""),
              str(variables_out.get("contextBugs"))[:200])
        check("知识条目确实进入 contextKnowledge（复用 fts 混合召回）",
              "验证码时效" in variables_out.get("contextKnowledge", ""),
              str(variables_out.get("contextKnowledge"))[:200])

        bound = context_assembler.assemble(
            conn, capability="case_generation", variables=variables,
            project_id=ids["project"], module_id=ids["feature"], tier="standard")
        bound_vars = bound.variables()
        check("绑定功能点时给出完整层级路径",
              "/" in bound_vars.get("contextModules", "")
              and "验证码重发" in bound_vars.get("contextModules", ""),
              str(bound_vars.get("contextModules"))[:220])

        scoped = context_assembler.assemble(
            conn, capability="bug_analysis", variables={"bugTitle": "验证码重发后旧验证码仍可用",
                                                        "bugDescription": "旧验证码未失效"},
            project_id=ids["project"], tier="full")
        check("缺陷分析只装配声明的种类（不越界装配知识以外的口径由声明决定）",
          set(scoped.kinds) == {"module", "bug", "knowledge"}, str(scoped.kinds))
        check("缺陷装配确实拿到历史缺陷", scoped.counts_by_kind()["bug"] >= 1,
              str(scoped.counts_by_kind()))

        no_project = context_assembler.assemble(
            conn, capability="case_generation", variables=variables, project_id=None,
            tier="standard")
        check("未确定项目时不注入任何条目",
              no_project.as_dict()["itemCount"] == 0, str(no_project.notes))
        check("未确定项目的留痕说清原因",
              any("未确定项目" in note for note in no_project.notes), str(no_project.notes))

        bare = context_assembler.assemble(
            conn, capability="log_analysis", variables={"logText": REQUIREMENT},
            project_id=ids["project"])
        check("未声明上下文的能力不装配（负控）",
              bare.as_dict()["itemCount"] == 0 and bare.variables() == {},
              str(bare.as_dict())[:160])
        check("负控对照：同一份数据下用例能力确实装出了条目",
              assembled.as_dict()["itemCount"] > 0, str(assembled.as_dict())[:120])
        check("未声明能力的留痕说明「未声明」而非「查不到」",
              any("未声明" in note for note in bare.notes), str(bare.notes))

        previewed = context_assembler.preview(
            conn, capability="case_generation", variables=variables,
            project_id=ids["project"], tier="standard")
        check("预览与实际装配同一口径（预览即实装）",
              previewed.as_dict() == assembled.as_dict(),
              str(previewed.as_dict())[:160])

        with app.app_context():
            run_plan = lab_service.prepare_run(
                conn, "case_generation",
            {**variables, "projectName": "上下文装配验收", "caseTypes": "functional",
             "count": "2"},
            project_id=ids["project"], context_tier="standard")
            optional_names = list(lab_service.optional_variables_of("case-generation"))
        run_context = run_plan.context.as_dict() if run_plan.context is not None else {}
        check("真实执行准备阶段的装配与预览一致（条数/种类/关键词）",
              run_context.get("itemCount") == previewed.as_dict()["itemCount"]
              and run_context.get("kinds") == list(previewed.kinds)
              and run_context.get("keywords") == list(previewed.keywords),
              str({k: run_context.get(k) for k in ("itemCount", "kinds", "keywords")})[:260])
        check("装配条目真的渲染进提示词（模板占位有值，不是空壳）",
              any(block.title in run_plan.prompt_text for block in run_plan.context.blocks
                  if block.title) if run_plan.context is not None else False,
              str(run_plan.prompt_text[:200]))
        template_optional = list(
            PromptRegistry(ROOT / "backend" / "prompts").get("case-generation").optional)
        check("模板把四个上下文占位都声明为可选（老模板缺变量不留空壳）",
              all(name in template_optional for name in
                  ("contextModules", "contextCases", "contextBugs", "contextKnowledge")),
              str(template_optional))
        check("界面上的静默可选变量仍只暴露 caseTypes/count（口径不被上下文变量撑大）",
              optional_names == ["caseTypes", "count"], str(optional_names))

        prose = context_assembler.assemble(
            conn, capability="case_generation",
            variables={"requirement": PROSE_REQUIREMENT},
            project_id=ids["project"], tier="standard")
        prose_vars = prose.variables()
        check("长句需求下 fts 那一路仍能召回（口径边界如实记录）",
              "验证码时效" in prose_vars.get("contextKnowledge", ""),
              str(prose_vars.get("contextKnowledge"))[:160])
        check("长句需求的字面召回如实留空而不是编造条目（负控）",
              prose.counts_by_kind()["bug"] == 0
              and "没有召回到相关历史缺陷" in prose_vars.get("contextBugs", ""),
              str(prose_vars.get("contextBugs"))[:120])

        keywords_req = context_assembler.related_requirement_id(
            conn, ids["project"], context_assembler.keywords_of(REQUIREMENT))
        check("能按字面命中把需求对上（GAP-29 比较域要用）",
              keywords_req == ids["requirement"], f"{keywords_req} != {ids['requirement']}")
        check("对不上需求时返回 None 而不是乱指（负控）",
              context_assembler.related_requirement_id(
                  conn, ids["project"], ["不存在的关键词组合"]) is None)
    finally:
        conn.close()


# ---------------- D. 装配失败不阻断主链路 ----------------

def _boom(conn: sqlite3.Connection, **kwargs: Any) -> list[context_assembler.ContextBlock]:
    raise sqlite3.OperationalError("门禁注入：模拟已有用例召回失败")


def failure_checks(client: Any, db_path: pathlib.Path, ids: dict[str, int],
                   mock_port: int) -> None:
    original = context_assembler._case_blocks
    context_assembler._case_blocks = _boom
    try:
        conn = conn_of(db_path)
        try:
            plan = context_assembler.assemble(
                conn, capability="case_generation", variables={"requirement": REQUIREMENT},
                project_id=ids["project"], tier="standard")
            payload = plan.as_dict()
            check("单路召回异常只留痕，不抛出（装配是增强项）",
                  any("召回失败" in note for note in payload["notes"]), str(payload["notes"]))
            check("失败的那路为空，其余种类照常装配",
                  payload["counts"]["case"] == 0 and payload["counts"]["module"] >= 1,
                  str(payload["counts"]))
            check("失败留痕写清异常类型", "OperationalError" in " ".join(payload["notes"]),
                  str(payload["notes"])[:200])
        finally:
            conn.close()

        profile = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
            "name": "装配容错 mock 档案", "provider": "custom", "protocol": "openai_compat",
            "baseUrl": f"http://127.0.0.1:{mock_port}/v1", "model": "mock-ctx",
            "isLocal": True, "isActive": True, "priority": 300, "timeout": 10,
        })
        run = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
            "variables": {"requirement": REQUIREMENT, "caseTypes": "functional", "count": "2",
                          "projectName": "上下文门禁"},
            "profileId": int(profile["id"]), "projectId": ids["project"],
            "title": "装配失败容错",
        })
        check("召回失败不影响能力出产出（主链路不被上下文带崩）",
              bool(str(run.get("output") or "").strip()), str(run)[:160])
        broken_context = cast("dict[str, Any]", run.get("context") or {})
        check("响应仍带回上下文结论并写明失败原因",
              any("召回失败" in str(note) for note in broken_context.get("notes") or []),
              str(broken_context)[:240])
    finally:
        context_assembler._case_blocks = original
    conn2 = conn_of(db_path)
    try:
        restored = context_assembler.assemble(
            conn2, capability="case_generation", variables={"requirement": REQUIREMENT},
            project_id=ids["project"], tier="standard")
        check("补丁撤销后召回恢复（正控：红绿都来自真实规则）",
              restored.counts_by_kind()["case"] >= 1, str(restored.counts_by_kind()))
    finally:
        conn2.close()


# ---------------- E. 活链路落库 ----------------

def live_checks(client: Any, db_path: pathlib.Path, ids: dict[str, int],
                mock_port: int) -> None:
    profile = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
        "name": "上下文活链路 mock 档案", "provider": "custom", "protocol": "openai_compat",
        "baseUrl": f"http://127.0.0.1:{mock_port}/v1", "model": "mock-ctx",
        "isLocal": True, "isActive": True, "priority": 310, "timeout": 10,
    })
    profile_id = int(profile["id"])
    variables = {"requirement": REQUIREMENT, "caseTypes": "functional", "count": "2",
                 "projectName": "上下文门禁"}

    standard = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": profile_id, "projectId": ids["project"],
        "title": "上下文标准档",
    })
    block = cast("dict[str, Any]", standard.get("context") or {})
    check("响应带上下文结论（验收 2：条目数与 token 增量可见）",
          int(block.get("itemCount") or 0) > 0 and block.get("tier") == "standard"
          and int(block.get("estimatedTokens") or 0) > 0, str(block)[:260])
    check("上下文结论带中文字位标签与上限",
          block.get("tierLabel") == "标准" and int(block.get("limitTotal") or 0) == 20,
          str({k: block.get(k) for k in ("tierLabel", "limitTotal")}))
    check("响应里的上下文与能力声明一致",
          set(block.get("kinds") or []) == {"module", "case", "bug", "knowledge"},
          str(block.get("kinds")))

    lite = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": profile_id, "projectId": ids["project"],
        "contextTier": "lite", "title": "上下文精简档",
    })
    lite_block = cast("dict[str, Any]", lite.get("context") or {})
    check("请求可覆盖档位（contextTier=lite 生效）",
          lite_block.get("tier") == "lite"
          and int(lite_block.get("itemCount") or 0) <= int(block.get("itemCount") or 0),
          str({k: lite_block.get(k) for k in ("tier", "itemCount")}))

    full = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": profile_id, "projectId": ids["project"],
        "contextTier": "full", "title": "上下文完整档",
    })
    full_block = cast("dict[str, Any]", full.get("context") or {})
    check("完整档条目不少于标准档（三档在活链路上同样可区分）",
          int(full_block.get("itemCount") or 0) >= int(block.get("itemCount") or 0)
          and int(full_block.get("chars") or 0) <= context_assembler.TIER_LIMITS["full"]["chars"],
          str({k: full_block.get(k) for k in ("itemCount", "chars")}))

    check("上下文真的发给了模型：提示词里能看到已有用例标题",
          "验证码超时后重发成功-变体" in prompt_text_of_call(-1), prompt_text_of_call(-1)[:300])
    check("上下文真的发给了模型：能看到知识条目小标题",
          "#知识条目" in prompt_text_of_call(-1) or "验证码时效" in prompt_text_of_call(-1),
          prompt_text_of_call(-1)[:200])

    record_id = int(standard.get("recordId") or 0)
    check("活链路记录号可用", record_id > 0, str(standard)[:120])
    detail = data_of(client, "GET", f"{V1}/ai-records/{record_id}")
    stored = cast("dict[str, Any]", detail.get("context") or {})
    check("上下文结论已落库（biz_ai_record.context_json）",
          int(stored.get("itemCount") or 0) == int(block.get("itemCount") or 0)
          and stored.get("tier") == "standard", str(stored)[:260])
    check("落库的上下文带裁剪留痕与上限（可复盘为什么少）",
          "limitChars" in stored and "skipped" in stored and "truncated" in stored,
          str(sorted(stored))[:220])
    lite_detail = data_of(client, "GET", f"{V1}/ai-records/{int(lite.get('recordId') or 0)}")
    check("精简档的落库档位同为 lite",
          (lite_detail.get("context") or {}).get("tier") == "lite",
          str(lite_detail.get("context"))[:160])

    no_ctx = data_of(client, "POST", f"{V1}/lab/log_analysis/run", {
        "variables": {"logContent": "java.lang.NullPointerException at LoginService.send",
                      "errorType": "NPE"},
        "profileId": profile_id, "title": "无上下文能力",
    })
    check("未声明上下文的能力不带条目（负控，活链路同口径）",
          int((no_ctx.get("context") or {}).get("itemCount") or 0) == 0
          or not no_ctx.get("context"), str(no_ctx.get("context"))[:160])
    check("负控对照：同一次请求序列里用例能力确实带了条目",
          int(block.get("itemCount") or 0) > 0, str(block)[:120])

    status, cards_body = request_json(client, "GET", f"{V1}/lab/capabilities")
    check("能力目录接口成功", not is_failure(status, cards_body), str(cards_body)[:120])
    items = cast("list[dict[str, Any]]", cards_body.get("data") or [])
    target = next((item for item in items if item.get("code") == "case_generation"), {})
    card_context = cast("dict[str, Any]", target.get("context") or {})
    check("能力卡片暴露上下文口径（档位、种类、硬上限都能给界面呈现）",
          card_context.get("defaultTier") == "standard"
          and {kind_item["kind"] for kind_item in card_context.get("kinds") or []} ==
          {"module", "case", "bug", "knowledge"}
          and card_context.get("limits", {}).get("total") == 20, str(card_context)[:260])
    check("卡片带三档中文标签",
          [item["label"] for item in card_context.get("tiers") or []] ==
          ["精简", "标准", "完整"], str(card_context.get("tiers")))
    log_card = next((item for item in items if item.get("code") == "log_analysis"), {})
    check("卡片对未声明上下文的能力如实给空种类（负控）",
          (log_card.get("context") or {}).get("kinds") == [], str(log_card)[:160])

    conn = conn_of(db_path)
    try:
        row = conn.execute(
            "SELECT context_json FROM biz_ai_record WHERE id = ?", (record_id,)).fetchone()
        check("落库列非空且 JSON 可解析（不是响应内存回显）",
              bool(row["context_json"]) and isinstance(
                  json.loads(str(row["context_json"])), dict), str(row["context_json"])[:120])
    finally:
        conn.close()


# ---------------- F. 红线静态扫描 ----------------

def redline_checks() -> None:
    text = (ROOT / "backend" / "aitp" / "services" / "context_assembler.py").read_text(
        encoding="utf-8")
    check("召回复用既有字面检索（instr + fts），不新建检索体系",
          "instr(" in text and "hybrid_search" in text)
    check("不引入向量检索/嵌入依赖（GAP-27 边界）",
          "embedding" not in text.lower() and "numpy" not in text
          and "faiss" not in text.lower())
    check("条数与字数上限写死在档位表里（红线：硬上限化）",
          "TIER_LIMITS" in text and '"total"' in text and '"chars"' in text)
    check("超限丢块有留痕", "skipped.append" in text)
    check("装配失败单路降级不抛出", "except sqlite3.Error" in text)
    check("预览与装配同函数（不另写一套口径）",
          "return assemble(" in text)
    service = (ROOT / "backend" / "aitp" / "services" / "lab_service.py").read_text(
        encoding="utf-8")
    check("主链路对装配另有兜底（装配失败不带崩生成）", "上下文装配失败" in service)
    check("增量基线走系统提示而不是模板变量", "基线走系统提示" in service)


def main() -> int:
    tier_pure_checks()
    redline_checks()
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-ctx-"))
    db_path = workdir / "context.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "context.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    app = create_app(cfg)
    client = app.test_client()
    server, mock_port = start_mock()
    try:
        ids = seed_project(client, "CTX-A", "上下文装配验收")
        tier_resolution_checks(client, db_path)
        recall_checks(client, app, db_path, ids, mock_port)
        failure_checks(client, db_path, ids, mock_port)
        live_checks(client, db_path, ids, mock_port)
    finally:
        server.shutdown()
    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
