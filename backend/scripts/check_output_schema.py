"""GAP-28 输出结构约束门禁：解析 → 校验 → 规范化 → 一次修正重试 → 统计与原文，全链路可复盘。

守住的口径（清单 §5.3 GAP-28 功能点拆解 + 验收 1~4）：

- 只用标准库实现 `required / type / enum / items / 白名单` 这一子集，不引第三方校验框架；
- 缺字段 / 类型错 → 拦条目并给原因；多余字段 → 裁剪；可安全推断的缺省 → 补默认值；
- 修正重试**恰好一次**（`MAX_SCHEMA_RETRIES = 1`，红线：不无限烧 token），重试请求把校验错误回传模型；
- 最终仍不合格 → 保留原文 + 可读原因，**不静默回退**；
- 「N 条通过 / M 条被拦下」必须与实际可采纳条数一致；结论落 `biz_ai_record.schema_json`。

    python backend/scripts/check_output_schema.py

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
from aitp.ai import output_schema as schema  # noqa: E402
from aitp.ai.errors import AIError  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import lab_service  # noqa: E402

V1 = "/api/v1"
#: 统一包封的成功码（见 aitp.envelope.ok）
SUCCESS = 200000

#: 修正重试那一次请求的用户消息特征（lab_service 用它把校验错误回传模型）
REPAIR_MARK = "上一次输出未通过本机字段校验"

GOOD_CASES = [
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
        "caseType": "boundary",
        "priority": "P2",
        "steps": [{"action": "连续重发 5 次", "expected": "第 6 次提示稍后再试"}],
    },
]

#: 关键字段缺失 / 空数组：`steps` 是用例的必填数组，两种都必须整条拦下
BAD_CASES = [
    {"title": "缺少步骤的用例", "caseType": "functional", "priority": "P1"},
    {"title": "步骤为空数组的用例", "caseType": "functional", "steps": []},
]

#: 三条里第二条缺 title：应当「2 条通过 / 1 条被拦下」
MIXED_CASES = [
    GOOD_CASES[0],
    {"caseType": "functional", "steps": [{"action": "没有标题的步骤"}]},
    GOOD_CASES[1],
]

#: 多余字段 + 枚举越界 + 别名 + 超长，一条里全占上，规范化后必须逐样留痕
MESSY_CASES = [{
    "title": "超长标题" + "验" * 260,
    "type": "functional",
    "caseType": "性能",
    "priority": "P9",
    "steps": [{"step": "输入手机号", "expect": "收到验证码"}],
    "expected": "登录成功",
    "module": "登录/验证码",
    "author": "门禁写入的多余字段",
}]

#: 只给别名字段、不给规范名。别名归一的留痕只能这样测：MESSY_CASES 里
#: `type` 与 `caseType` 同时存在，规范名优先是对的（output_schema 取值顺序），
#: 拿它断言「别名留痕」永远不成立。
ALIAS_CASES = [{
    "title": "验证码倒计时结束后按钮恢复可点",
    "type": "boundary",
    "steps": [{"step": "等待倒计时结束", "expect": "按钮恢复可点"}],
    "expected": "可以重新发送验证码",
    "module": "登录/验证码",
}]

_passed = 0
_failed = 0
#: 每个模型名被调用了几次：修正重试「恰好一次」只认这份计数
_CALL_COUNTS: dict[str, int] = {}
#: 每次请求的原样报文：断言「修正请求把校验错误回传了模型」只认这份实证
_REQUESTS: list[dict[str, Any]] = []
_CALLS_LOCK = threading.Lock()


def messages_of(model: str, occurrence: int) -> str:
    """取某模型第 occurrence 次请求（1 起）的全部消息文本。"""
    with _CALLS_LOCK:
        hits = [item for item in _REQUESTS if str(item.get("model") or "") == model]
    if occurrence > len(hits):
        return ""
    return chr(10).join(
        str(message.get("content") or "") for message in
        cast("list[dict[str, Any]]", hits[occurrence - 1].get("messages") or []))


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

def _bump(model: str) -> None:
    with _CALLS_LOCK:
        _CALL_COUNTS[model] = _CALL_COUNTS.get(model, 0) + 1


def calls_of(model: str) -> int:
    with _CALLS_LOCK:
        return _CALL_COUNTS.get(model, 0)


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

    def _content_for(self, body: dict[str, Any]) -> str:
        """按模型名给确定性产出；带修正标记的请求一律回合规 JSON。"""
        model = str(body.get("model") or "")
        messages = cast("list[dict[str, Any]]", body.get("messages") or [])
        asked_to_repair = any(REPAIR_MARK in str(message.get("content") or "")
                              for message in messages)
        if model == "mock-markdown":
            return "## 需求确认\n- 已确认：验证码 60 秒有效\n- 待澄清：是否限制重发次数"
        if model == "mock-always-bad":
            # 连修正请求都回同样的坏产出：用来验「仍失败就不静默回退」
            return json.dumps(BAD_CASES, ensure_ascii=False)
        if model == "mock-worse":
            # 修正请求回更差的结果（连 JSON 都不是）：必须不替换上一次结论
            return "抱歉，我重新想想。" if asked_to_repair else json.dumps(BAD_CASES,
                                                                           ensure_ascii=False)
        if model == "mock-mixed":
            return "```json\n" + json.dumps(MIXED_CASES, ensure_ascii=False) + "\n```"
        if model == "mock-messy":
            return json.dumps(MESSY_CASES, ensure_ascii=False)
        if model == "mock-not-json":
            return "抱歉，我无法生成用例。"
        if asked_to_repair:
            return json.dumps(GOOD_CASES, ensure_ascii=False)
        # mock-bad：第一次给缺字段产出，被要求修正后给合规产出
        return json.dumps(BAD_CASES, ensure_ascii=False)

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = self._read_json()
        model = str(body.get("model") or "mock-good")
        _bump(model)
        with _CALLS_LOCK:
            _REQUESTS.append(body)
        payload = json.dumps({
            "id": f"chatcmpl-{model}",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": self._content_for(body)},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 600, "completion_tokens": 180, "totalTokens": 0,
                      "total_tokens": 780},
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


def dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


# ---------------- A. 校验层口径（纯函数） ----------------

def pure_checks() -> None:
    check("修正重试上限写死一次（红线：不无限烧 token）", schema.MAX_SCHEMA_RETRIES == 1)
    check("schema 只覆盖四类结构化产出",
          set(schema.SCHEMAS) == {"case_generation", "xmind_to_case",
                                  "regression_generation", "testpoint_generation"},
          str(sorted(schema.SCHEMAS)))
    json_caps = {code for code, spec in caps.CAPABILITIES.items()
                 if spec.output_format == caps.OUTPUT_JSON}
    check("每个 JSON 产出能力都有 schema（验收 4：路径全覆盖）",
          json_caps <= set(schema.SCHEMAS), str(sorted(json_caps - set(schema.SCHEMAS))))
    adoptable_json = {code for code, spec in caps.CAPABILITIES.items()
                      if spec.adoptable and spec.output_format == caps.OUTPUT_JSON}
    check("可采纳能力全部在校验覆盖内（正控：覆盖名单不是随手列的）",
          adoptable_json <= set(schema.SCHEMAS), str(sorted(adoptable_json)))
    markdown_caps = {code for code, spec in caps.CAPABILITIES.items()
                     if spec.output_format != caps.OUTPUT_JSON}
    check("markdown 能力一个都不给编 schema（负控）",
          not (markdown_caps & set(schema.SCHEMAS)) and len(markdown_caps) > 5,
          str(sorted(markdown_caps & set(schema.SCHEMAS))))
    check("未定义 schema 的能力走「只解析不校验」分支",
          schema.has_schema("log_analysis") is False
          and schema.schema_of("log_analysis") is None
          and schema.validate_output("log_analysis", {"a": 1}).ok is True)

    report = schema.validate_output("case_generation", GOOD_CASES)
    check("合规产出一并放行", report.ok and report.passed == 2 and report.rejected == 0,
          report.message)
    check("校验结论自带重试上限（界面不用猜）", report.as_dict()["retryLimit"] == 1)
    check("通过条数与规范化条数一致（验收 3：统计即可采纳数）",
          isinstance(report.value, list) and len(report.value) == report.passed,
          str(report.value)[:120])
    check("合规产出不做默认值补齐之外的改动",
          report.value[1]["preconditions"] == "无" and report.value[1]["tags"] == "",
          str(report.value[1])[:200])

    missing = schema.validate_output("case_generation", BAD_CASES)
    check("缺关键字段整条拦下（验收 1）",
          missing.ok is False and missing.rejected == 2 and missing.passed == 0,
          missing.message)
    codes = {issue.code for issue in missing.issues}
    check("拦下原因分成缺失与类型两类可解释",
          "missing" in codes and "type" in codes, str(sorted(codes)))
    check("被拦条目留下标题与原文（界面能查看原文）",
          bool(missing.dropped) and missing.dropped[0].title == "缺少步骤的用例"
          and "缺少步骤" in missing.dropped[0].raw, str(missing.dropped)[:220])
    check("汇总文案说清全部被拦", "都不满足关键字段要求" in missing.message, missing.message)
    check("空数组步骤与缺失步骤同等拦下（红线：空壳不算用例）",
          missing.dropped[1].title == "步骤为空数组的用例", str(missing.dropped[1])[:160])

    mixed = schema.validate_output("case_generation", MIXED_CASES)
    check("好坏混排按条计数：2 通过 / 1 拦下",
          mixed.ok and mixed.total == 3 and mixed.passed == 2 and mixed.rejected == 1,
          str([mixed.total, mixed.passed, mixed.rejected]))
    check("拦下的那条是缺标题的（下标对得上产出）",
          mixed.dropped[0].path == "[1]", str(mixed.dropped)[:200])
    check("放行条目的下标与产出对齐（前端按下标勾选）",
          isinstance(mixed.value, list) and len(mixed.value) == 2
          and mixed.value[0]["title"] == "验证码超时后重发成功", str(mixed.value)[:160])
    check("通过条数 + 拦下条数 = 总数（不留糊涂账）",
          mixed.passed + mixed.rejected == mixed.total, str(mixed.as_dict())[:200])

    messy = schema.validate_output("case_generation", MESSY_CASES)
    payload = messy.as_dict()
    check("多余字段被裁剪并逐个留痕（白名单口径）",
          "[0].author" in messy.trimmed, str(messy.trimmed))
    alias = schema.validate_output("case_generation", ALIAS_CASES)
    check("别名归一留痕：type -> caseType",
          any(item.startswith("[0].caseType←type") for item in alias.filled)
          and alias.value[0]["caseType"] == "boundary", str(alias.filled))
    check("规范名已在时别名不覆盖它，且不算「补齐」（负控：优先级 caseType > type）",
          not any(item.startswith("[0].caseType") for item in messy.filled)
          and messy.value[0]["caseType"] == "functional", str(messy.filled))
    check("枚举越界不拦条目，回落默认并记 warn（宽松字段不放大招）",
          messy.ok and messy.passed == 1 and messy.value[0]["caseType"] == "functional",
          str(messy.value)[:200])
    check("优先级越界同样回落默认 P2",
          messy.value[0]["priority"] == "P2", str(messy.value[0])[:200])
    check("超长文本被裁到上限并留痕",
          "[0].title" in messy.clipped and len(str(messy.value[0]["title"])) <= 201,
          str(messy.clipped))
    check("步骤内字段同样按别名归一",
          messy.value[0]["steps"][0]["action"] == "输入手机号", str(messy.value[0])[:220])
    check("规范化后的条目里不再有白名单外字段",
          "author" not in messy.value[0], str(sorted(messy.value[0])))
    check("缺失的可推断字段补默认值并留痕",
          "preconditions" in str(payload["filledDefaults"]), str(payload["filledDefaults"]))

    empty = schema.validate_output("case_generation", [])
    check("模型没给任何条目时如实报 0 条且判不通过",
          empty.ok is False and empty.total == 0 and "没有给出任何" in empty.message,
          empty.message)
    non_json = schema.validate_output("case_generation", None)
    check("连 JSON 都没解析出来：parsed=False 且不谎称通过",
          non_json.parsed is False and non_json.ok is False
          and non_json.issues[0].code == "parse", str(non_json.as_dict())[:200])
    scalar = schema.validate_output("case_generation", "一串文本")
    check("根结构不是数组时明确拦下（负控：不静默当成一条）",
          scalar.ok is False and scalar.total == 0
          and "JSON 数组" in scalar.issues[0].expected, str(scalar.as_dict())[:220])
    single = schema.validate_output("case_generation", GOOD_CASES[0])
    check("根结构给单个对象时按一条处理并留痕",
          single.passed == 1 and single.total == 1 and single.as_dict()["issueCount"] >= 1,
          single.message)
    not_object = schema.validate_output("case_generation", ["这条不是对象"])
    check("条目不是对象时逐条拦下而不是整体失败",
          not_object.ok is False and not_object.dropped[0].path == "[0]",
          str(not_object.as_dict())[:200])

    regression = schema.validate_output("regression_generation", [
        {"title": "支付回调重复通知", "module": "支付/回调", "priority": "P1",
         "reason": "幂等键变更", "verifyTarget": "同一通知两次只入账一次"},
    ])
    check("回归清单走自己的 schema",
          regression.ok and regression.passed == 1 and regression.as_dict()["capability"]
          == "regression_generation", regression.message)
    testpoint = schema.validate_output("testpoint_generation", {
        "name": "登录",
        "children": [
            {"name": "手机号登录", "children": [{"name": "验证码时效"}]},
            {"name": "扫码登录"},
        ],
    })
    check("测试点树按节点计数（对象根不套数组口径）",
          testpoint.ok and testpoint.root == "object" and testpoint.passed == 4,
          str([testpoint.passed, testpoint.root]))
    bad_child = schema.validate_output("testpoint_generation", {
        "name": "登录",
        "children": [{"name": "手机号登录"}, {"priority": "P1"}],
    })
    check("子节点缺名字只摘子节点，不判整棵树无效",
          bad_child.ok and bad_child.passed == 2 and bad_child.rejected == 1,
          str([bad_child.passed, bad_child.rejected]))
    bad_root = schema.validate_output("testpoint_generation", {"children": [{"name": "孤儿"}]})
    check("根节点缺名字判整棵无效并写明根因",
          bad_root.ok is False and "根节点关键字段不合规" in bad_root.message,
          bad_root.message)
    check("测试点只支持有限层数（红线：不做无界递归）",
          "depth" in pathlib.Path(str(schema.__file__)).read_text(encoding="utf-8"))
    xmind = schema.validate_output("xmind_to_case", GOOD_CASES)
    check("脑图转用例与用例生成同一套条目规则",
          xmind.ok and xmind.passed == 2 and xmind.label == "测试用例（脑图转换）",
          xmind.label)

    feedback = schema.retry_feedback(missing)
    check("修正指令把错误原样回传模型（不是空话）",
          REPAIR_MARK in feedback and "steps" in feedback and "期望" in feedback,
          feedback[:200])
    check("修正指令列出被丢弃条目", "已丢弃" in feedback, feedback[-260:])
    check("修正指令要求只输出 JSON 且不要围栏",
          "不要输出解释文字或代码块围栏" in feedback, feedback[-160:])
    clean_feedback = schema.retry_feedback(report)
    check("无 error 明细时修正指令仍有兜底文案（不留空消息）",
          len(clean_feedback.splitlines()) >= 2 and "关键字段不得省略" in clean_feedback,
          clean_feedback[:160])
    check("校验结论字段齐备（落库/界面/门禁同一份）",
          {"parsed", "ok", "total", "passed", "rejected", "retried", "retryLimit",
           "issues", "issueCount", "dropped", "droppedCount", "trimmedFields",
           "filledDefaults", "clippedFields", "message"} <= set(report.as_dict()),
          str(sorted(report.as_dict()))[:260])


# ---------------- B. 放宽规则必须让断言变红（规则真在开火） ----------------

def rule_causality_checks() -> None:
    """故意放宽一条规则：同一份产出应当多放行条目，证明计数来自规则而不是写死的常量。"""
    strict = schema.validate_output("case_generation", BAD_CASES)
    relaxed = schema.FieldRule(
        name="case", type=schema.TYPE_OBJECT,
        fields=(schema.FieldRule("title", schema.TYPE_STRING, required=True, strict=True),))
    relaxed_report = schema._validate_array(
        schema.SchemaSpec("case_generation", "测试用例", schema.ROOT_ARRAY, "用例",
                          item=relaxed),
        BAD_CASES)
    check("放宽必填规则后同一份产出全部放行（正控：规则确实在决定结果）",
          relaxed_report.ok and relaxed_report.passed == 2 and not strict.ok,
          f"strict={strict.passed} relaxed={relaxed_report.passed}")
    check("模块级规则没有被门禁改写（用完即撤，不污染生产口径）",
          schema.validate_output("case_generation", BAD_CASES).passed == 0
          and schema.CASE_ITEM is not relaxed)
    check("步骤规则仍要求 action / expected 至少一个非空",
          schema.validate_output("case_generation", [
              {"title": "空步骤", "steps": [{"action": "", "expected": ""}]}]).passed == 0
          and schema._STEP_RULE.require_any_of == ("action", "expected"))


# ---------------- C. 收尾管线（服务层，真实函数） ----------------

def _sample_for(code: str) -> Any:
    if code == "regression_generation":
        return [{"title": "支付回调重复通知", "module": "支付", "reason": "幂等键变更",
                 "verifyTarget": "同一通知两次只入账一次"}]
    if code == "testpoint_generation":
        return {"name": "登录", "children": [{"name": "手机号登录"}]}
    return GOOD_CASES


def pipeline_checks() -> None:
    spec = caps.spec_of("case_generation")
    if spec is None:  # pragma: no cover - 能力表缺条目属于环境异常，直接判红
        check("用例生成能力声明存在", False, "spec_of 返回 None")
        return
    check("用例生成能力声明存在", True)
    good = lab_service.finalize_output(spec, dump(GOOD_CASES))
    check("合规产出直接规范化输出（不触发修正）",
          good.report.ok and good.retries == 0 and good.report.retried == 0
          and json.loads(good.output) == good.parsed, str(good.report.message))
    markdown_spec = caps.spec_of("requirement_confirm")
    if markdown_spec is not None:
        markdown_report = lab_service.schema_payload_of(
            lab_service.finalize_output(markdown_spec, "## 结论"))
        check("markdown 能力给的是「无字段校验」结论而不是空结论",
              markdown_report.get("root") == "markdown"
              and markdown_report.get("ok") is True, str(markdown_report)[:200])

    calls = {"n": 0}
    seen = {"issues": False, "codes": ""}

    def repair_ok(text: str, report: schema.SchemaReport) -> lab_service.RepairResult:
        calls["n"] += 1
        seen["issues"] = bool(report.issues)
        seen["codes"] = ",".join(sorted({issue.code for issue in report.issues}))
        return lab_service.RepairResult(
            text=dump(GOOD_CASES),
            usage={"promptTokens": 700, "completionTokens": 200, "totalTokens": 900},
            model="mock-repair")

    fixed = lab_service.finalize_output(spec, dump(BAD_CASES), repair=repair_ok)
    check("第一次不合格时正好修正一次",
          calls["n"] == 1 and fixed.retries == 1 and fixed.report.retried == 1,
          f"calls={calls['n']} retries={fixed.retries}")
    check("修正回调拿到的报告里有可回传的错误明细",
          bool(seen["issues"]) and bool(seen["codes"]), str(seen))
    check("修正后结论换成新的校验报告",
          fixed.report.ok and fixed.report.passed == 2, fixed.report.message)
    check("修正调用的用量与模型随结论返回（成本口径不丢）",
          fixed.retry_model == "mock-repair"
          and int(fixed.retry_usage.get("totalTokens") or 0) == 900,
          str([fixed.retry_model, fixed.retry_usage]))
    check("修正成功不再挂失败原因", fixed.retry_failed == "", fixed.retry_failed)

    def repair_same(text: str, report: schema.SchemaReport) -> lab_service.RepairResult:
        return lab_service.RepairResult(text=text, model="mock-repair")

    stubborn = lab_service.finalize_output(spec, dump(BAD_CASES), repair=repair_same)
    check("修正后仍不合格：只重试一次，不继续烧 token",
          stubborn.retries == 1 and stubborn.report.retried == 1 and not stubborn.report.ok,
          str(stubborn.report.as_dict())[:200])
    check("仍不合格时保留原文并写明重试过（不静默回退）",
          "缺少步骤的用例" in stubborn.output
          and "已自动修正重试 1 次" in stubborn.report.message, stubborn.report.message)

    def repair_worse(text: str, report: schema.SchemaReport) -> lab_service.RepairResult:
        return lab_service.RepairResult(text="抱歉，我重新想想。", model="mock-worse")

    worse = lab_service.finalize_output(spec, dump(BAD_CASES), repair=repair_worse)
    check("修正结果更差不替换上一次结论（评分口径：先看通过条数再看可否解析）",
          worse.output == json.dumps(
              schema.validate_output("case_generation", BAD_CASES).value, ensure_ascii=False
          ) or "缺少步骤的用例" in worse.output,
          str(worse.output)[:200])
    check("更差的修正仍只重试一次并把原因写进结论",
          worse.retries == 1 and "仍不合关键字段要求" in worse.report.message,
          worse.report.message)

    def repair_boom(text: str, report: schema.SchemaReport) -> lab_service.RepairResult:
        raise AIError("模拟修正调用失败")

    broken = lab_service.finalize_output(spec, dump(BAD_CASES), repair=repair_boom)
    check("修正调用抛异常不带崩主链路，原因进结论",
          broken.retries == 1 and "模拟修正调用失败" in broken.retry_failed
          and "自动修正调用失败" in broken.report.message,
          str([broken.retry_failed, broken.report.message])[:240])
    check("修正通道坏了也保留原产出", "缺少步骤的用例" in broken.output, broken.output[:160])

    silent = lab_service.finalize_output(spec, dump(GOOD_CASES), repair=repair_boom)
    check("合规产出不会因为修正通道坏了而受影响",
          silent.report.ok and silent.retry_failed == "", silent.retry_failed)

    no_repair = lab_service.finalize_output(spec, dump(BAD_CASES))
    check("没有修正通道时不假装重试（retried 为 0）",
          no_repair.retries == 0 and no_repair.report.retried == 0
          and not no_repair.report.ok, str(no_repair.report.as_dict())[:160])

    fenced = lab_service.finalize_output(spec, "```json\n" + dump(GOOD_CASES) + "\n```")
    check("代码块围栏内的 JSON 照样解析（模型常见写法）",
          fenced.report.ok and fenced.report.parsed, fenced.report.message)
    noisy = lab_service.finalize_output(spec, "好的，这是用例：\n" + dump(GOOD_CASES))
    check("正文夹带说明文字时仍能抽出 JSON", noisy.report.ok, noisy.report.message)
    check("抽不出 JSON 时返回 None（负控）",
          lab_service.extract_json("完全不是 JSON") is None)

    payload = lab_service.schema_payload_of(fixed)
    check("统一口径的 schema 结论含重试次数与重试用量",
          payload["retried"] == 1 and payload["retryModel"] == "mock-repair"
          and bool(payload["retryUsage"]), str(payload)[:200])
    for code in sorted(schema.SCHEMAS):
        item_spec = caps.spec_of(code)
        if item_spec is None:  # pragma: no cover
            check(f"{code} 能力声明存在", False)
            continue
        report = lab_service.finalize_output(item_spec, dump(_sample_for(code))).report
        check(f"{code} 的 schema 收尾管线可跑通（验收 4）",
              report.ok and report.parsed and report.passed >= 1, str(report.as_dict())[:200])


# ---------------- D. 活链路：接口、重试计数与落库 ----------------

def make_profile(client: Any, mock_port: int, name: str, model: str, priority: int) -> int:
    created = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
        "name": name, "provider": "custom", "protocol": "openai_compat",
        "baseUrl": f"http://127.0.0.1:{mock_port}/v1", "model": model,
        "isLocal": True, "isActive": True, "priority": priority, "timeout": 10,
    })
    return int(created["id"])


def live_checks(client: Any, db_path: pathlib.Path, mock_port: int) -> None:
    project = data_of(client, "POST", f"{V1}/projects", {
        "code": "SCHEMA-A", "name": "输出结构约束验收", "owner": "QA"})
    project_id = int(project["id"])
    variables = {"requirement": "验证码超时后重发", "caseTypes": "functional", "count": "2",
                 "projectName": "输出结构约束验收"}

    bad_id = make_profile(client, mock_port, "校验-可修正档案", "mock-bad", 400)
    before = calls_of("mock-bad")
    fixed_run = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": bad_id, "projectId": project_id,
        "title": "修正重试活链路",
    })
    fixed_schema = cast("dict[str, Any]", fixed_run.get("schema") or {})
    check("活链路第一次不合格后修正一次即通过",
          fixed_schema.get("ok") is True and fixed_schema.get("retried") == 1
          and fixed_schema.get("retryLimit") == 1, str(fixed_schema)[:260])
    check("修正恰好一次：模型只被多调了一次（红线：不无限重试）",
          calls_of("mock-bad") - before == 2, str(calls_of("mock-bad") - before))
    check("修正后的产出就是规范化 JSON，条数与通过数一致",
          len(json.loads(str(fixed_run.get("output") or "[]"))) ==
          int(fixed_schema.get("passed") or 0) == 2, str(fixed_run)[:180])
    check("修正那次调用的用量随结论返回", bool(fixed_schema.get("retryUsage")),
          str(fixed_schema)[:160])
    check("第二次请求确实把校验错误原样回传给了模型（不是闷头重问）",
          REPAIR_MARK in messages_of("mock-bad", 2)
          and "steps" in messages_of("mock-bad", 2), messages_of("mock-bad", 2)[:240])
    check("第一次请求里没有修正指令（正控：修正只在第二次）",
          REPAIR_MARK not in messages_of("mock-bad", 1), messages_of("mock-bad", 1)[:160])

    always_bad_id = make_profile(client, mock_port, "校验-恒不合格档案", "mock-always-bad", 410)
    bad_run = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": always_bad_id, "projectId": project_id,
        "title": "仍不合格活链路",
    })
    bad_schema = cast("dict[str, Any]", bad_run.get("schema") or {})
    check("仍不合格时不静默回退：原文保留在产出里",
          "缺少步骤的用例" in str(bad_run.get("output") or ""), str(bad_run)[:180])
    check("仍不合格时 ok=False 且写明拦下原因",
          bad_schema.get("ok") is False and int(bad_schema.get("passed") or 0) == 0
          and int(bad_schema.get("issueCount") or 0) >= 1, str(bad_schema)[:260])
    check("仍不合格时提示已重试一次且仍不合关键字段要求",
          "已自动修正重试 1 次" in str(bad_schema.get("message") or ""),
          str(bad_schema.get("message"))[:200])
    check("拦下条目带原文与原因供界面查看",
          bool(bad_schema.get("dropped")) and "raw" in str(bad_schema.get("dropped")),
          str(bad_schema.get("dropped"))[:220])

    record_id = int(bad_run.get("recordId") or 0)
    detail = data_of(client, "GET", f"{V1}/ai-records/{record_id}")
    stored = cast("dict[str, Any]", detail.get("schema") or {})
    check("校验结论已落库（biz_ai_record.schema_json，不是只在响应里）",
          stored.get("ok") is False and stored.get("retried") == 1
          and stored.get("retryLimit") == 1, str(stored)[:260])
    check("落库结论与响应同口径（同一份报告，不两处维护）",
          {stored.get(k) for k in ("total", "passed", "rejected", "retried")} ==
          {bad_schema.get(k) for k in ("total", "passed", "rejected", "retried")},
          str([stored, bad_schema])[:240])

    mixed_id = make_profile(client, mock_port, "校验-好坏混排档案", "mock-mixed", 420)
    mixed_run = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": mixed_id, "projectId": project_id,
        "title": "混排统计活链路",
    })
    mixed_schema = cast("dict[str, Any]", mixed_run.get("schema") or {})
    check("混排产出给出「2 条通过 / 1 条被拦下」的可见统计（验收 3）",
          mixed_schema.get("passed") == 2 and mixed_schema.get("rejected") == 1
          and mixed_schema.get("total") == 3, str(mixed_schema)[:240])
    adopted = data_of(client, "POST", f"{V1}/test-cases/adopt", {
        "projectId": project_id, "cases": json.loads(str(mixed_run.get("output") or "[]"))})
    check("采纳条数与通过条数一致（界面统计不是空头数字，验收 3）",
          int(adopted.get("count") or 0) == int(mixed_schema.get("passed") or 0) == 2,
          str(adopted)[:220])

    messy_id = make_profile(client, mock_port, "校验-脏字段档案", "mock-messy", 430)
    messy_run = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": messy_id, "projectId": project_id,
        "title": "脏字段活链路",
    })
    messy_schema = cast("dict[str, Any]", messy_run.get("schema") or {})
    messy_output = json.loads(str(messy_run.get("output") or "[]"))
    check("接口层同样裁掉多余字段（前端拿到的就是规范化产出）",
          bool(messy_schema.get("trimmedFields")) and "author" not in messy_output[0],
          str([messy_schema.get("trimmedFields"), sorted(messy_output[0])])[:240])
    check("枚举越界在接口层回落默认而不是拦条目",
          messy_schema.get("passed") == 1 and messy_output[0]["caseType"] == "functional",
          str(messy_output[0])[:200])

    markdown_id = make_profile(client, mock_port, "校验-markdown 档案", "mock-markdown", 440)
    md_before = calls_of("mock-markdown")
    md_run = data_of(client, "POST", f"{V1}/lab/requirement_confirm/run", {
        # requirement-confirm 提示词里 projectName 是必填变量，缺了会在渲染阶段就 120102
        "variables": {"requirement": "验证码 60 秒有效", "projectName": "校验层验收项目"},
        "profileId": markdown_id,
        "projectId": project_id, "title": "markdown 能力口径",
    })
    md_schema = cast("dict[str, Any]", md_run.get("schema") or {})
    check("markdown 能力在接口层也如实回报「无字段校验」",
          md_schema.get("root") == "markdown" and md_schema.get("ok") is True
          and md_schema.get("parsed") is True, str(md_schema)[:220])
    check("markdown 能力绝不触发修正重试（负控）",
          md_schema.get("retried") == 0 and calls_of("mock-markdown") - md_before == 1,
          str(calls_of("mock-markdown") - md_before))

    worse_id = make_profile(client, mock_port, "校验-越修越差档案", "mock-worse", 450)
    worse_run = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": worse_id, "projectId": project_id,
        "title": "越修越差活链路",
    })
    worse_schema = cast("dict[str, Any]", worse_run.get("schema") or {})
    check("越修越差时保留第一次的结论与原文",
          worse_schema.get("retried") == 1 and "缺少步骤的用例" in
          str(worse_run.get("output") or ""), str(worse_run)[:180])

    notjson_id = make_profile(client, mock_port, "校验-非 JSON 档案", "mock-not-json", 460)
    nj_before = calls_of("mock-not-json")
    notjson_run = data_of(client, "POST", f"{V1}/lab/case_generation/run", {
        "variables": variables, "profileId": notjson_id, "projectId": project_id,
        "title": "解析不出 JSON",
    })
    nj_schema = cast("dict[str, Any]", notjson_run.get("schema") or {})
    check("完全不是 JSON 的产出：parsed=False，原文照留",
          nj_schema.get("parsed") is False and nj_schema.get("ok") is False
          and "抱歉" in str(notjson_run.get("output") or ""), str(nj_schema)[:220])
    check("解析失败也只做一次修正", calls_of("mock-not-json") - nj_before == 2,
          str(calls_of("mock-not-json") - nj_before))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute("SELECT schema_json FROM biz_ai_record WHERE id = ?",
                           (record_id,)).fetchone()
        stored_json = json.loads(str(row["schema_json"] or "{}"))
        check("落库的是结构化结论而不是字符串回显",
              isinstance(stored_json, dict) and "passed" in stored_json,
              str(row["schema_json"])[:160])
        total_records = int(conn.execute(
            "SELECT COUNT(*) FROM biz_ai_record WHERE schema_json IS NOT NULL"
        ).fetchone()[0])
        check("每条走校验的活链路记录都留了结论", total_records >= 6, str(total_records))
    finally:
        conn.close()


# ---------------- E. 红线静态扫描 ----------------

def redline_checks() -> None:
    text = (ROOT / "backend" / "aitp" / "ai" / "output_schema.py").read_text(encoding="utf-8")
    check("不引第三方校验框架（边界：标准库子集）",
          "import jsonschema" not in text and "pydantic" not in text)
    check("重试上限常量写死 1", "MAX_SCHEMA_RETRIES = 1" in text)
    check("白名单裁剪有实现而不是注释", "sink.trimmed.append" in text)
    check("默认值补齐有实现", "sink.filled.append" in text)
    check("超长文本裁剪有实现", "sink.clipped.append" in text)
    check("宽松字段与关键字段分级（strict 与非 strict 两条路）",
          "rule.strict" in text and "sink.warn" in text and "sink.error" in text)
    service = (ROOT / "backend" / "aitp" / "services" / "lab_service.py").read_text(
        encoding="utf-8")
    check("校验不通过时保留原文（不静默回退）",
          "if not report.ok:\n        return text, parsed, report" in service)
    check("修正请求把校验错误回传模型", "retry_feedback(report)" in service)
    check("修正次数与用量并入统一结论", 'payload["retryUsage"]' in service)


def main() -> int:
    pure_checks()
    rule_causality_checks()
    pipeline_checks()
    redline_checks()
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-schema-"))
    db_path = workdir / "schema.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "schema.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    client = create_app(cfg).test_client()
    server, mock_port = start_mock()
    try:
        live_checks(client, db_path, mock_port)
    finally:
        server.shutdown()
    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
