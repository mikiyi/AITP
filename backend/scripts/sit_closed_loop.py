"""SIT 闭环演示数据生成：同一套用例走完 接口/Web/性能/MCP探索 四执行方式闭环。

运行：python backend/scripts/sit_closed_loop.py
数据写入独立 demo 项目「SIT-DEMO」，保留作演示数据，不触碰其他项目。
闭环：需求→用例→计划→4轮模板→逐轮执行(接口/Web/性能/探索)→失败转缺陷→报告→知识沉淀。
"""

from __future__ import annotations

import json
import pathlib
import sys
import time
import urllib.request
from typing import Any, Callable

BASE = "http://127.0.0.1:8760/api/v1"
ROOT = pathlib.Path(__file__).resolve().parents[2]


def req(method: str, path: str, body: dict[str, Any] | None = None) -> Any:
    r = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode() if body is not None else None,
        headers={"Content-Type": "application/json"},
        method=method,
    )
    with urllib.request.urlopen(r, timeout=60) as resp:
        out = json.load(resp)
    if out.get("code") != 200000:
        raise AssertionError(f"{method} {path} -> {out.get('code')} {out.get('message')}")
    return out.get("data")


def main() -> int:
    log: list[str] = []

    def step(name: str, fn: Callable[[], Any]) -> Any:
        try:
            result = fn()
            log.append(f"[OK] {name}")
            return result
        except Exception as exc:  # noqa: BLE001
            log.append(f"[FAIL] {name}: {exc}")
            return None

    project = step("建 demo 项目", lambda: req("POST", "/projects", {"code": "SIT-DEMO", "name": "SIT 闭环演示", "description": "四执行方式闭环演示数据"}))
    if not project:
        print("\n".join(log)); return 1
    pid = int(project["id"])
    version = step("建版本", lambda: req("POST", "/project-versions", {"projectId": pid, "versionCode": "V-SIT", "name": "SIT 迭代"}))
    vid = int(version["id"]) if version else None
    env = step("建环境(指向本应用)", lambda: req("POST", "/test-envs", {"name": "SIT 本机", "kind": "test", "app": {"baseUrl": "http://127.0.0.1:8760", "servers": [{"baseUrl": "http://127.0.0.1:8760"}]}, "db": {"type": "sqlite", "host": ""}}))
    eid = int(env["id"]) if env else None
    step("建需求", lambda: req("POST", "/requirements", {"projectId": pid, "versionId": vid, "title": "登录与下单主流程", "content": "SIT 闭环需求"}))

    cases = []
    specs = [
        ("健康检查接口", "interface", "P1", {"接口URL": "/api/v1/health", "请求方式": "GET", "断言规则": "json:$.code = 200000"}),
        ("登录主流程", "functional", "P1", {}),
        ("下单校验", "functional", "P2", {}),
        ("金额边界", "boundary", "P2", {}),
        ("支付超时异常", "exception", "P2", {}),
        ("浏览器兼容", "ui", "P2", {"tags": "兼容"}),
        ("必失败接口(转缺陷用)", "interface", "P1", {"接口URL": "/api/v1/health", "请求方式": "GET", "断言规则": "json:$.code = 0"}),
    ]
    def make_case(t: str, ct: str, p: str, e: dict[str, Any]) -> Any:
        return req("POST", "/test-cases", {"projectId": pid, "versionId": vid, "title": t, "caseType": ct, "priority": p, "extra": e})
    for title, ctype, pri, extra in specs:
        def _mk(t: str = title, ct: str = ctype, p: str = pri, e: dict[str, Any] = extra) -> Any:
            return make_case(t, ct, p, e)
        c = step(f"用例 {title}", _mk)
        if c:
            cases.append(c)

    plan = step("建计划", lambda: req("POST", "/test-plans", {"projectId": pid, "versionId": vid, "name": "SIT 闭环计划", "defaultEnvId": eid}))
    plan_id = int(plan["id"]) if plan else None
    rounds = step("4轮模板生成", lambda: req("POST", f"/test-plans/{plan_id}/rounds/from-template", {"template": "four", "startDate": "2026-11-02", "envId": eid})) if plan_id else None
    if rounds:
        log.append(f"    轮次: {[(r['roundType'], r['status'], r['total']) for r in rounds['runs']]}")

    # 接口自动化：对两个接口用例真跑（独立 interface-run，留证据）
    if eid:
        for c in cases:
            if c["caseType"] == "interface":
                def _run(cid: int = int(c["id"])) -> Any:
                    return req("POST", f"/test-cases/{cid}/interface-run", {"envId": eid})
                step(f"接口真跑 {c['title']}", _run)
    # 失败转缺陷：必失败接口断言不通过 → 建缺陷
    fail_case = next((c for c in cases if "必失败" in c["title"]), None)
    if fail_case:
        bug = step("失败转缺陷", lambda: req("POST", "/bugs", {"projectId": pid, "versionId": vid, "title": "[SIT] 健康检查业务码断言失败", "severity": "P2", "bugType": "interface", "description": "断言 json:$.code = 0 不通过（实际 200000），SIT 闭环演示缺陷。"}))
        if bug:
            log.append(f"    缺陷: {bug.get('bugNo')}")

    # Web 自动化：真跑一个 python 脚本并回写轮2
    web_script = step("建Web自动化脚本", lambda: req("POST", "/automation/scripts", {
        "projectId": pid, "name": "SIT Web 冒烟", "category": "web", "targetType": "web",
        "framework": "selenium", "language": "python", "runMode": "script",
        "content": "import sys\nprint('[PASS] SIT web 冒烟：登录页可达')\nsys.exit(0)\n",
    }))
    if web_script and rounds:
        r2 = rounds["runs"][1]
        step("Web自动化真跑+回写", lambda: req("POST", f"/automation/scripts/{web_script['id']}/run", {"testRunId": r2["id"]}))

    # 性能：JMeter .jmx 真跑轮3
    jmx = ROOT / "backend" / "scripts" / "sit_perf.jmx"
    jmx.write_text(_jmx(), encoding="utf-8")
    perf_script = step("建性能脚本", lambda: req("POST", "/automation/scripts", {
        "projectId": pid, "name": "SIT 性能压测", "category": "performance", "targetType": "interface",
        "framework": "jmeter", "language": "jmeter", "runMode": "jmeter",
        "fileName": "sit_perf.jmx", "content": _jmx(),
    }))
    if perf_script and rounds:
        r3 = rounds["runs"][2]
        step("JMeter真跑", lambda: req("POST", f"/automation/scripts/{perf_script['id']}/run", {"testRunId": r3["id"], "timeoutSeconds": 120}))

    # MCP 探索：stub 驱动 → 异常证据 → 待审 → 审核转缺陷
    import os
    os.environ["AITP_MCP_ANOMALY_STEPS"] = "2"
    os.environ["AITP_MCP_ANOMALY_KIND"] = "http"
    stub = ROOT / "backend" / "scripts" / "mock_mcp_server.py"
    step("配置探索MCP", lambda: req("PUT", "/exploratory/settings", {"mcpCommand": f'"{sys.executable}" "{stub}"', "logSources": []}))
    sess = step("建探索会话", lambda: req("POST", "/exploratory/sessions", {"projectId": pid, "name": "SIT 探索", "targetUrl": "http://127.0.0.1:8760/", "driver": "scripted", "maxSteps": 3, "plan": [{"action": "navigate"}, {"action": "click", "selector": "#pay"}, {"action": "snapshot"}]}))
    if sess:
        step("运行探索", lambda: req("POST", f"/exploratory/sessions/{sess['id']}/run"))
        time.sleep(2)
        drafts = step("查待审产物", lambda: req("GET", "/knowledge/drafts?reviewStatus=pending"))
        if drafts:
            exp = [d for d in drafts["list"] if d.get("sourceType") == "exploratory"]
            if exp:
                step("探索产物审核转缺陷", lambda: req("POST", f"/knowledge/drafts/{exp[0]['id']}/review", {"action": "approve", "reviewer": "SIT"}))
    os.environ.pop("AITP_MCP_ANOMALY_STEPS", None)
    os.environ.pop("AITP_MCP_ANOMALY_KIND", None)

    # 报告 + 知识沉淀（走待审→审核转正的真实流程）
    step("生成报告", lambda: req("POST", "/reports", {"projectId": pid, "versionId": vid, "title": "SIT 闭环测试报告", "reportType": "summary"}))

    def knowledge() -> Any:
        d = req("POST", "/knowledge/drafts", {"projectId": pid, "title": "SIT 闭环结论：支付需幂等", "content": "支付回调重复推送需幂等校验。", "category": "踩坑记录", "sourceType": "manual"})
        return req("POST", f"/knowledge/drafts/{d['id']}/review", {"action": "approve", "reviewer": "SIT"})
    step("知识沉淀(待审→转正)", knowledge)

    print("\n".join(log))
    print(f"\nSIT-DEMO project id={pid} 保留作演示数据")
    return 0


def _jmx() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<jmeterTestPlan version="1.2" properties="5.0" jmeter="5.6.3">
  <hashTree>
    <TestPlan guiclass="TestPlanGui" testclass="TestPlan" testname="SIT Perf">
      <elementProp name="TestPlan.user_defined_variables" elementType="Arguments"/>
      <boolProp name="TestPlan.functional_mode">false</boolProp>
      <boolProp name="TestPlan.serialize_threadgroups">false</boolProp>
    </TestPlan>
    <hashTree>
      <ThreadGroup guiclass="ThreadGroupGui" testclass="ThreadGroup" testname="SIT 线程组">
        <intProp name="ThreadGroup.num_threads">5</intProp>
        <intProp name="ThreadGroup.ramp_time">1</intProp>
        <boolProp name="ThreadGroup.scheduler">false</boolProp>
        <stringProp name="ThreadGroup.duration"></stringProp>
        <stringProp name="ThreadGroup.delay"></stringProp>
        <elementProp name="ThreadGroup.main_controller" elementType="LoopController">
          <boolProp name="LoopController.continue_forever">false</boolProp>
          <intProp name="LoopController.loops">2</intProp>
        </elementProp>
      </ThreadGroup>
      <hashTree>
        <HTTPSamplerProxy guiclass="HttpTestSampleGui" testclass="HTTPSamplerProxy" testname="健康检查">
          <stringProp name="HTTPSampler.domain">127.0.0.1</stringProp>
          <intProp name="HTTPSampler.port">8760</intProp>
          <stringProp name="HTTPSampler.path">/api/v1/health</stringProp>
          <stringProp name="HTTPSampler.method">GET</stringProp>
          <boolProp name="HTTPSampler.use_keepalive">true</boolProp>
        </HTTPSamplerProxy>
        <hashTree/>
      </hashTree>
    </hashTree>
  </hashTree>
</jmeterTestPlan>
"""


if __name__ == "__main__":
    sys.exit(main())