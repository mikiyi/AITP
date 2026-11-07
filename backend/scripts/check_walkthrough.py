"""固定走查案例：一次跑通「路由顺序 + 降级顺序 + 行为安全规则 + 结构化记录 + 全局搜索」。

    python backend/scripts/check_walkthrough.py

脚本自起一个临时的 OpenAI 兼容 mock 服务，按场景返回预设产出。
断言的是产品行为——顺序、降级、标记、落库、检索——不是模型说了什么。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import socket
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services.knowledge_storage import CONFIG_PATH  # noqa: E402
from flask import Flask  # noqa: E402
from seed_demo import seed  # noqa: E402

_passed = 0
_failed = 0
_failures: list[str] = []

#: mock 里回给路由能力的候选
ROUTE_REPLY: dict[str, Any] = {
    "routes": [
        {"capability": "bug_analysis", "score": 0.92, "reason": "文本指向支付回调重复推送"},
        {"capability": "regression_generation", "score": 0.41, "reason": "涉及改动后的回归"},
    ]
}

#: mock 里回给缺陷分析能力的产出物：故意踩三条安全规则
#: 1. 出现批准性结论「可以上线」  2. 引用了库里不存在的 BUG-999  3. 自述信息不足
BUG_REPLY = """# 现象归纳
支付回调重复推送导致金额翻倍，属于资金一致性问题，优先级 P0。

# 复现路径
1. 支付回调收到两次相同推送
2. 金额被累加两次

# 可能原因
- 假设一：回调缺少幂等校验。判断依据：现有信息不足，无法判断，还需要网关原始报文与流水表。

# 验证方法
- 复核 BUG-001 的处理记录
- 复跑 FT-001 确认修复效果
- 顺带确认 BUG-999 是否同一根因

# 修复建议
- 增加幂等键并补齐对账任务

# 结论
该问题风险可控，可以上线，建议发布。
"""

#: 功能点候选里要指到的模块（种子数据里的「支付回调」）
MOCK_MODULE: dict[str, Any] = {"id": 0, "path": ""}

#: mock 里回给回归清单能力的产出物：严格 JSON 数组
REGRESSION_REPLY: list[dict[str, str]] = [
    {
        "module": "支付回调",
        "title": "重复推送只入账一次",
        "priority": "P0",
        "reason": "幂等键改造后需要回归",
        "verifyTarget": "并发回放同一报文",
    },
    {
        "module": "对账任务",
        "title": "重复推送后金额与流水一致",
        "priority": "P1",
        "reason": "累加逻辑改了",
        "verifyTarget": "跑一次日终对账",
    },
]

#: mock 里回给需求评审能力的产出物
REQUIREMENT_REPLY = """# 评审结论
需求描述基本完整，可进入测试设计。

# 问题清单
| 序号 | 维度 | 问题描述 | 影响 | 建议 |
| 1 | 完整性 | 缺少异常分支说明 | 用例覆盖不全 | 补充超时与重复推送场景 |
"""


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


# ---------------- mock 模型服务 ----------------

class _MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 屏蔽 stderr 日志
        pass

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return cast("dict[str, Any]", json.loads(raw.decode("utf-8")))
        except Exception:
            return {}

    def _reply_for(self, body: dict[str, Any]) -> str:
        model = str(body.get("model") or "")
        prompt = "\n".join(
            str(message.get("content") or "")
            for message in body.get("messages") or []
            if isinstance(message, dict)
        )
        if "功能点清单" in prompt:
            return json.dumps({
                "routes": [{
                    "moduleId": MOCK_MODULE["id"],
                    "path": MOCK_MODULE["path"],
                    "score": 0.88,
                    "reason": "文本指向支付回调",
                }]
            }, ensure_ascii=False)
        if "能力目录" in prompt:
            return json.dumps(ROUTE_REPLY, ensure_ascii=False)
        # 「影响面判定」只在回归清单提示词里出现，用它做分支不会误伤其它能力
        if "影响面判定" in prompt:
            return json.dumps(REGRESSION_REPLY, ensure_ascii=False)
        # 「待澄清问题」只在需求评审提示词里出现
        if "待澄清问题" in prompt:
            return REQUIREMENT_REPLY
        if "bug" in model:
            return BUG_REPLY
        return "走查 mock 默认回复"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = self._read_json()
        reply = self._reply_for(body)
        payload = json.dumps({
            "id": "chatcmpl-walkthrough",
            "object": "chat.completion",
            "model": body.get("model") or "mock",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": reply},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 20, "completion_tokens": 30, "total_tokens": 50},
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


def dead_port() -> int:
    """取一个已关闭的端口：连上去会被立刻拒绝，用来模拟首选模型不可用。"""
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = int(sock.getsockname()[1])
    sock.close()
    return port


# ---------------- 测试脚手架 ----------------

def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "walkthrough.db"
    init_db(db_path)
    conn = get_standalone_conn(db_path)
    try:
        SysConfigRepository(conn).set_value(
            CONFIG_PATH, str(workdir / "knowledge"), "string", "走查脚本临时目录"
        )
        seed(conn)
        conn.commit()
        row = conn.execute(
            "SELECT id, name FROM biz_module WHERE name = ? AND is_deleted = 0",
            ("支付回调",),
        ).fetchone()
        if row:
            MOCK_MODULE["id"] = int(row["id"])
            MOCK_MODULE["path"] = str(row["name"])
    finally:
        conn.close()

    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "walkthrough.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def call(client: Any, method: str, path: str, payload: Any = None) -> dict[str, Any]:
    resp = client.open(path, method=method, json=payload)
    body = resp.get_json(silent=True)
    if not isinstance(body, dict):
        return {"code": -1, "message": "非 JSON 响应", "data": None}
    return cast("dict[str, Any]", body)


def data_of(client: Any, method: str, path: str, payload: Any = None) -> Any:
    body = call(client, method, path, payload)
    if body.get("code") != ErrorCode.SUCCESS:
        raise AssertionError(f"{method} {path} 期望成功，实际 {body.get('code')} {body.get('message')}")
    return body.get("data")


# ---------------- 走查步骤 ----------------

def check_routing(client: Any) -> None:
    """路由顺序：模型可用时按分数排序，模型不可用时关键词兜底给出同样的首位能力。"""
    result = data_of(client, "POST", "/api/v1/lab/route", {"text": "支付回调重复推送导致金额翻倍"})
    routes = result.get("capabilityRoutes") or []
    check("路由走的是模型结果", result.get("source") == "ai", str(result.get("source")))
    check("路由首位是缺陷分析", bool(routes) and routes[0].get("capability") == "bug_analysis",
          str(routes)[:200])
    scores = [float(item.get("score") or 0) for item in routes]
    check("路由候选按分数降序", scores == sorted(scores, reverse=True), str(scores))
    modules = result.get("moduleRoutes") or []
    check("路由带出功能点候选", bool(modules) and "支付回调" in str(modules[0].get("path")),
          str(modules)[:200])
    check("能力候选带出归属功能点", bool(routes) and routes[0].get("targetModuleId") is not None,
          str(routes)[:200])


def check_degrade(client: Any) -> dict[str, Any]:
    """降级顺序：首选模型不可用时自动切到次选，产出物照常返回。"""
    result = data_of(client, "POST", "/api/v1/lab/bug_analysis/run", {
        "variables": {
            "bugTitle": "支付回调重复推送导致金额翻倍",
            "bugDescription": "支付回调收到两次相同推送，金额被累加两次",
            "reproSteps": "1. 触发支付回调 2. 重复推送同一条报文",
            "expectedResult": "只入账一次",
            "actualResult": "入账两次",
        }
    })
    degrade = result.get("degrade") or {}
    check("首选模型不可用时触发降级", bool(degrade.get("degraded")), str(degrade)[:200])
    check("降级原因标为不可达", degrade.get("reason") == "unreachable", str(degrade)[:200])
    check("降级后仍拿到产出物", bool(str(result.get("output") or "").strip()), str(result)[:120])
    check("降级后用的是次选模型", result.get("model") == "mock-bug", str(result.get("model")))
    check("产出物标记需要人工复核", bool(result.get("reviewRequired")), str(result)[:120])
    return cast(dict[str, Any], result)


def check_record(client: Any, run_result: dict[str, Any]) -> int:
    """结构化记录：证据回库核对、缺项抽取、风险点判定。"""
    record_id = int(run_result.get("recordId") or 0)
    check("执行记录已落库", record_id > 0, str(run_result)[:160])
    record = data_of(client, "GET", f"/api/v1/ai-records/{record_id}")
    risks = record.get("risks") or []
    rules = {str(item.get("rule")) for item in risks}
    evidence = record.get("evidence") or []
    by_ref = {str(item.get("ref")): item for item in evidence}
    missing = record.get("missing") or []

    check("记录抓到批准性结论", "approval" in rules, str(risks)[:200])
    check("记录抓到 P0 高风险", "high_risk" in rules, str(risks)[:200])
    check("记录抓到编造引用", "fabrication" in rules, str(risks)[:200])
    check("真实存在的缺陷编号验证通过",
          bool(by_ref.get("BUG-001")) and bool(by_ref.get("BUG-001", {}).get("verified")),
          str(evidence)[:200])
    check("真实存在的用例编号验证通过",
          bool(by_ref.get("FT-001")) and bool(by_ref.get("FT-001", {}).get("verified")),
          str(evidence)[:200])
    check("编造的缺陷编号标为未验证",
          bool(by_ref.get("BUG-999")) and not bool(by_ref.get("BUG-999", {}).get("verified")),
          str(evidence)[:200])
    check("记录到信息不足缺项", any("信息不足" in str(item.get("detail") or "") for item in missing),
          str(missing)[:200])
    check("记录风险等级为高", record.get("riskLevel") == "high", str(record.get("riskLevel")))
    check("记录需要人工复核", bool(record.get("reviewRequired")), str(record)[:120])

    listing = data_of(client, "GET", "/api/v1/ai-records")
    check("执行记录可分页查询", bool((listing.get("list") or [])), str(listing)[:160])
    check("待复核计数可用", int((listing.get("stats") or {}).get("pending") or 0) >= 1,
          str(listing.get("stats")))
    return record_id


def check_review(client: Any, record_id: int) -> None:
    """人工复核结论落库，并留审计。"""
    result = data_of(client, "POST", f"/api/v1/ai-records/{record_id}/review", {
        "status": "approved",
        "note": "已人工核对幂等键方案，采纳",
    })
    check("可写入人工复核结论", result.get("reviewStatus") == "approved", str(result)[:160])
    detail = data_of(client, "GET", f"/api/v1/ai-records/{record_id}")
    check("复核备注保留", "幂等键" in str(detail.get("reviewNote") or ""), str(detail)[:160])
    check("复核时间落库", bool(detail.get("reviewedAt")), str(detail)[:160])

    bad = call(client, "POST", f"/api/v1/ai-records/{record_id}/review", {"status": "whatever"})
    check("非法复核状态被拒", bad.get("code") == ErrorCode.PARAM_ERROR, str(bad)[:160])

    audits = data_of(client, "GET", "/api/v1/settings/audit-logs?action=review")
    rows = audits.get("list") or []
    check("复核动作写入审计",
          any(str(item.get("resourceType")) == "ai_record" for item in rows), str(rows)[:200])


def check_search(client: Any) -> None:
    """全局搜索：一次检索需求、用例、缺陷、知识四类实体。"""
    result = data_of(client, "GET", "/api/v1/search?q=登录")
    groups = result.get("groups") or []
    kinds = {str(item.get("type")) for item in groups}
    check("全局搜索有命中", int(result.get("total") or 0) > 0, str(result)[:160])
    check("全局搜索覆盖多类实体",
          {"requirement", "case", "bug"} <= kinds, str(kinds))
    items = [row for group in groups for row in (group.get("list") or [])]
    check("搜索结果带跳转路由", all(row.get("route") for row in items), str(items)[:160])
    check("搜索结果带命中片段", any(row.get("snippet") for row in items), str(items)[:160])

    only = data_of(client, "GET", "/api/v1/search?q=登录&types=knowledge")
    check("可限定检索类型",
          all(str(group.get("type")) == "knowledge" for group in (only.get("groups") or [])),
          str(only)[:160])

    bad_type = call(client, "GET", "/api/v1/search?q=登录&types=unknown")
    check("未知检索类型被拒", bad_type.get("code") == ErrorCode.PARAM_ERROR, str(bad_type)[:160])
    empty = call(client, "GET", "/api/v1/search?q=")
    check("空关键词被拒", empty.get("code") == ErrorCode.PARAM_ERROR, str(empty)[:160])


def check_offline_fallback(client: Any) -> None:
    """把档案停用后，路由仍能用关键词兜底给出候选。"""
    profiles = data_of(client, "GET", "/api/v1/settings/ai/profiles")
    for profile in profiles:
        data_of(client, "PUT", f"/api/v1/settings/ai/profiles/{profile['id']}", {"isActive": False})
    result = data_of(client, "POST", "/api/v1/lab/route", {"text": "支付回调重复推送导致金额翻倍"})
    routes = result.get("capabilityRoutes") or []
    check("无模型时路由走关键词兜底", result.get("source") == "fallback", str(result.get("source")))
    check("兜底路由仍给出缺陷分析",
          bool(routes) and routes[0].get("capability") == "bug_analysis", str(routes)[:200])


# ---------------- P0/P1 补强走查 ----------------

def check_preflight(client: Any) -> None:
    """P0-1 执行前预检：缺项、完整度、主/辅助任务、执行顺序。"""
    result = data_of(client, "POST", "/api/v1/lab/preflight", {
        "text": "支付回调重复推送导致金额翻倍",
        "topK": 3,
    })
    tasks = result.get("tasks") or []
    check("预检给出候选任务", bool(tasks), str(result)[:200])
    check("预检标出主任务",
          any(str(item.get("role")) == "primary" for item in tasks),
          str([item.get("role") for item in tasks]))
    check("预检给出执行顺序", bool(result.get("order")), str(result.get("order")))
    check("预检认为模型可用", bool(result.get("modelReady")), str(result)[:200])
    check("预检给出整体完整度",
          isinstance(result.get("overallCompleteness"), (int, float)), str(result)[:200])

    # 只给文本不给变量：必填变量没给，完整度应低于 1 并列出缺项
    partial = data_of(client, "POST", "/api/v1/lab/preflight", {
        "text": "支付回调重复推送导致金额翻倍",
        "variables": {},
    })
    partial_tasks = partial.get("tasks") or []
    check("预检列出缺项",
          any(item.get("missing") for item in partial_tasks),
          str([item.get("missing") for item in partial_tasks]))
    check("预检把缺必填变量标为阻断",
          any(item.get("blockers") for item in partial_tasks)
          or bool(partial.get("blockers")),
          str(partial)[:240])
    check("预检完整度不满分",
          float(partial.get("overallCompleteness") or 0) < 1.0,
          str(partial.get("overallCompleteness")))

    # 显式指定能力 + 给全变量：应该没有阻断项
    full = data_of(client, "POST", "/api/v1/lab/preflight", {
        "capabilities": ["bug_analysis"],
        "variables": {
            "bugTitle": "支付回调重复推送导致金额翻倍",
            "bugDescription": "支付回调收到两次相同推送",
            "reproSteps": "1. 触发支付回调 2. 重复推送同一条报文",
            "expectedResult": "只入账一次",
            "actualResult": "入账两次",
        },
    })
    check("给全变量后预检不再报缺项",
          not any(item.get("missing") for item in full.get("tasks") or []),
          str(full)[:240])

    empty = call(client, "POST", "/api/v1/lab/preflight", {"variables": {}})
    check("预检没有输入被拒", empty.get("code") == ErrorCode.PARAM_ERROR, str(empty)[:160])


def check_composite(client: Any) -> None:
    """P0-2 多入口顺序执行：父任务 + 子任务 + 步骤轨迹。"""
    result = data_of(client, "POST", "/api/v1/lab/composite/run", {
        "title": "走查-需求到缺陷",
        "steps": [
            {
                "capability": "requirement_confirm",
                "name": "需求评审",
                "variables": {
                    "requirement": "支付回调重复推送要保证只入账一次",
                    "projectName": "走查项目",
                },
            },
            {
                "capability": "bug_analysis",
                "name": "缺陷分析",
                "variables": {
                    "bugTitle": "支付回调重复推送导致金额翻倍",
                    "bugDescription": "支付回调收到两次相同推送，金额被累加两次",
                    "reproSteps": "1. 触发支付回调 2. 重复推送同一条报文",
                    "expectedResult": "只入账一次",
                    "actualResult": "入账两次",
                },
            },
        ],
        "continueOnError": True,
    })
    steps = result.get("steps") or []
    check("顺序执行返回父任务", int(result.get("parentTaskId") or 0) > 0, str(result)[:200])
    check("顺序执行产出步骤轨迹", len(steps) == 2, str(steps)[:240])
    check("步骤按顺序编号",
          [item.get("order") for item in steps] == [1, 2], str(steps)[:240])
    check("每步都成功", all(item.get("status") == "success" for item in steps), str(steps)[:240])
    check("每步都带子任务号",
          all(int(item.get("taskId") or 0) > 0 for item in steps), str(steps)[:240])
    check("每步都带执行记录号",
          all(int(item.get("recordId") or 0) > 0 for item in steps), str(steps)[:240])
    check("每步都带模型与耗时",
          all(item.get("model") and item.get("latencyMs") is not None for item in steps),
          str(steps)[:240])
    check("父任务汇总产出非空", bool(str(result.get("output") or "").strip()), str(result)[:200])
    check("父任务落了汇总记录", int(result.get("parentRecordId") or 0) > 0, str(result)[:200])

    trajectory = data_of(client, "GET",
                         f"/api/v1/lab/tasks/{result['parentTaskId']}/trajectory")
    check("任务轨迹能取到子任务", len(trajectory.get("children") or []) == 2,
          str(trajectory)[:240])
    check("任务轨迹能取到步骤", len(trajectory.get("steps") or []) == 2,
          str(trajectory)[:240])

    # 缺必填变量：执行前就拦下，不产生半截流水线
    blocked = call(client, "POST", "/api/v1/lab/composite/run", {
        "steps": [{"capability": "case_generation", "variables": {}}],
    })
    check("步骤缺必填变量被拦下", blocked.get("code") == ErrorCode.PARAM_ERROR, str(blocked)[:200])

    bad = call(client, "POST", "/api/v1/lab/composite/run", {
        "steps": [{"capability": "not-a-capability", "variables": {}}],
    })
    check("未知能力被拦下", bad.get("code") == ErrorCode.PARAM_ERROR, str(bad)[:200])


def check_entry(client: Any) -> None:
    """P0-3 编排入口：草稿 → 发布 → 按工作流运行。"""
    created = data_of(client, "POST", "/api/v1/workbench/entries", {
        "code": "walkthrough-req-to-bug",
        "name": "走查-需求到缺陷",
        "capability": "requirement_confirm",
        "triggers": ["支付", "回调"],
        "required": ["requirement"],
        "workflow": [
            {
                "capability": "requirement_confirm",
                "name": "需求评审",
                "variables": {"projectName": "走查项目"},
            },
            {
                "capability": "bug_analysis",
                "name": "缺陷分析",
                "variables": {
                    "bugTitle": "支付回调重复推送导致金额翻倍",
                    "bugDescription": "支付回调收到两次相同推送",
                    "reproSteps": "1. 触发支付回调 2. 重复推送同一条报文",
                    "expectedResult": "只入账一次",
                    "actualResult": "入账两次",
                },
            },
        ],
    })
    entry_id = int(created.get("id") or 0)
    check("编排入口创建成功", entry_id > 0, str(created)[:200])
    check("编排入口默认草稿", created.get("status") == "draft", str(created.get("status")))

    published = data_of(client, "POST", f"/api/v1/workbench/entries/{entry_id}/publish")
    check("编排入口发布成功", published.get("status") == "published", str(published)[:200])
    check("编排入口版本号递增", int(published.get("version") or 0) >= 2,
          str(published.get("version")))

    ran = data_of(client, "POST", f"/api/v1/workbench/entries/{entry_id}/run", {
        "variables": {"requirement": "支付回调重复推送要保证只入账一次"},
        "continueOnError": True,
    })
    check("编排入口运行带回入口号", int(ran.get("entryId") or 0) == entry_id, str(ran)[:200])
    check("编排入口运行带回版本号", int(ran.get("entryVersion") or 0) >= 2, str(ran)[:200])
    check("编排入口按工作流跑出步骤", len(ran.get("steps") or []) == 2, str(ran)[:240])
    check("编排入口运行产生父任务", int(ran.get("parentTaskId") or 0) > 0, str(ran)[:200])


def check_validation(client: Any) -> None:
    """P0-4 案例验证沙箱：期望 vs 实际。"""
    created = data_of(client, "POST", "/api/v1/validation/cases", {
        "name": "走查-支付回调路由",
        "inputText": "支付回调重复推送导致金额翻倍",
        "capability": "bug_analysis",
        "variables": {
            "bugTitle": "支付回调重复推送导致金额翻倍",
            "bugDescription": "支付回调收到两次相同推送",
            "reproSteps": "1. 触发支付回调 2. 重复推送同一条报文",
            "expectedResult": "只入账一次",
            "actualResult": "入账两次",
        },
        "expectedRoutes": ["bug_analysis"],
        "status": "published",
    })
    case_id = int(created.get("id") or 0)
    check("验证案例创建成功", case_id > 0, str(created)[:200])

    result = data_of(client, "POST", f"/api/v1/validation/cases/{case_id}/run", {})
    check("验证案例跑出比对结论", "passed" in result, str(sorted(result.keys())))
    check("验证案例期望命中即通过", bool(result.get("passed")), str(result)[:240])
    check("比对结果带路由命中",
          bool((result.get("route") or {}).get("actual")), str(result.get("route"))[:240])
    check("比对结果带实际缺项",
          isinstance((result.get("missing") or {}).get("actual"), list),
          str(result.get("missing"))[:240])
    check("比对结果带命中规则",
          isinstance((result.get("rules") or {}).get("actual"), list),
          str(result.get("rules"))[:240])

    # 期望一个不会命中的能力：不通过，且差异清单要指出差在哪
    mismatch = data_of(client, "POST", "/api/v1/validation/cases", {
        "name": "走查-故意不匹配",
        "inputText": "支付回调重复推送导致金额翻倍",
        "expectedRoutes": ["sql_analysis"],
        "status": "published",
    })
    mismatch_result = data_of(
        client, "POST", f"/api/v1/validation/cases/{mismatch['id']}/run", {})
    check("期望不命中时判为不通过", not bool(mismatch_result.get("passed")),
          str(mismatch_result)[:240])
    check("不通过时给出差异清单", bool(mismatch_result.get("errors")),
          str(mismatch_result.get("errors"))[:240])

    summary = data_of(client, "POST", "/api/v1/validation/run-all", {})
    check("全量验证给出总数", int(summary.get("total") or 0) >= 2, str(summary)[:240])
    check("全量验证给出通过率", "passRate" in summary, str(summary)[:240])


def check_regression_ai(client: Any) -> None:
    """P1-2 回归清单：AI 生成条目 + 在线执行回写。"""
    created = data_of(client, "POST", "/api/v1/regressions", {
        "projectId": 1,
        "title": "走查-支付回调回归",
        "changeScope": "回调幂等改造",
        "affectedModules": "支付回调",
        "version": "v1.0.0",
        "riskLevel": "high",
    })
    reg_id = int(created.get("id") or 0)
    check("回归清单创建成功", reg_id > 0, str(created)[:200])

    generated = data_of(client, "POST", f"/api/v1/regressions/{reg_id}/generate", {})
    check("AI 生成回归条目", int(generated.get("created") or 0) == 2, str(generated)[:240])
    items = generated.get("items") or []
    check("回归条目带优先级", all(item.get("priority") in ("P0", "P1", "P2") for item in items),
          str(items)[:240])

    detail = data_of(client, "GET", f"/api/v1/regressions/{reg_id}")
    check("生成后清单来源标为 AI", detail.get("source") == "ai", str(detail)[:240])
    check("生成后清单状态就绪", detail.get("status") == "ready", str(detail.get("status")))

    executed = data_of(client, "POST", f"/api/v1/regressions/{reg_id}/execute", {
        "results": [
            {"itemId": items[0]["id"], "result": "passed", "remark": "手工验证通过"},
            {"itemId": items[1]["id"], "result": "failed", "remark": "金额对不上"},
        ],
    })
    check("回归执行回写两条", len(executed.get("updated") or []) == 2, str(executed)[:240])
    after = data_of(client, "GET", f"/api/v1/regressions/{reg_id}")
    results = {item.get("result") for item in after.get("items") or []}
    check("回归结果分别落库", results == {"passed", "failed"}, str(after.get("items"))[:240])
    check("清单状态按明细回推", after.get("status") == "done", str(after.get("status")))


def check_record_four_sections(client: Any) -> None:
    """P1-1 执行记录补四段：原始输入 / 路由命中 / 步骤轨迹 / 模型用量。"""
    records = data_of(client, "GET", "/api/v1/ai-records?pageSize=50")
    items = records.get("list") or []
    check("执行记录有数据", bool(items), str(len(items)))
    if not items:
        return
    detail = data_of(client, "GET", f"/api/v1/ai-records/{items[0]['id']}")
    for key in ("input", "route", "steps", "usage"):
        check(f"执行记录含 {key} 段", key in detail, str(sorted(detail.keys())))
    check("执行记录带提示词编号", "promptCode" in detail, str(sorted(detail.keys())))
    check("执行记录带耗时", "latencyMs" in detail, str(sorted(detail.keys())))


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-walkthrough-"))
    server, mock_port = start_mock()
    try:
        app = build_app(workdir)
        client = app.test_client()

        # 首选档案指向一个已关闭的端口，次选指向本地 mock
        data_of(client, "POST", "/api/v1/settings/ai/profiles", {
            "name": "首选-离线不可用",
            "provider": "custom",
            "protocol": "openai_compat",
            "baseUrl": f"http://127.0.0.1:{dead_port()}/v1",
            "model": "mock-dead",
            "isLocal": True,
            "isActive": True,
            "priority": 100,
            "timeout": 5,
        })
        data_of(client, "POST", "/api/v1/settings/ai/profiles", {
            "name": "次选-本地 mock",
            "provider": "custom",
            "protocol": "openai_compat",
            "baseUrl": f"http://127.0.0.1:{mock_port}/v1",
            "model": "mock-bug",
            "isLocal": True,
            "isActive": True,
            "priority": 50,
            "timeout": 10,
        })

        check_routing(client)
        run_result = check_degrade(client)
        record_id = check_record(client, run_result)
        check_review(client, record_id)
        check_search(client)
        check_preflight(client)
        check_composite(client)
        check_entry(client)
        check_validation(client)
        check_regression_ai(client)
        check_record_four_sections(client)
        check_offline_fallback(client)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 60)
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：" + "、".join(_failures))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
