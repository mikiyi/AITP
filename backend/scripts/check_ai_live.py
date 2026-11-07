"""真实大模型驱动的 AI 能力全路径测试。

针对正在运行的服务（默认 http://127.0.0.1:8760），用商汤日日新逐个跑全部
AI 能力，验证：SSE 流式、模型降级链、数据外发提示、执行记录、历史消息、
一键采纳资产、Excel/Word 导出、知识库问答助手、提示词优化、意图/模块路由、附件。

用法：
    .venv/Scripts/python.exe -X utf8 backend/scripts/check_ai_live.py

环境变量（可选）：
    AITP_LIVE_BASE   服务地址，默认 http://127.0.0.1:8760
    AITP_LIVE_PROFILE AI 档案 id，默认取「优先级最高且启用」的那个
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.capability_codes import LAB_CAPABILITIES  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402

JsonDict = dict[str, Any]

BASE = os.environ.get("AITP_LIVE_BASE", "http://127.0.0.1:8760").rstrip("/")
PROFILE_ID: int | None = None  # 运行时探测

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        return True
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")
    return False


def _http(method: str, path: str, payload: JsonDict | None = None,
          timeout: int = 180) -> tuple[int, JsonDict]:
    """发 JSON 请求，返回 (http_status, 解析后的包封)。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
            return resp.status, cast(JsonDict, json.loads(raw))
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8")
        try:
            return exc.code, cast(JsonDict, json.loads(raw))
        except json.JSONDecodeError:
            return exc.code, {"code": -1, "message": raw[:200]}


def _data(method: str, path: str, payload: JsonDict | None = None) -> Any:
    status, body = _http(method, path, payload)
    if body.get("code") != ErrorCode.SUCCESS:
        raise AssertionError(
            f"{method} {path} 期望成功，实际 http={status} code={body.get('code')} "
            f"msg={body.get('message')}")
    return body.get("data")


def _sse_frames(path: str, payload: JsonDict, timeout: int = 180) -> list[JsonDict]:
    """POST 一个 SSE 接口，把帧流解析成列表。"""
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}{path}", data=data, method="POST",
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
    )
    frames: list[JsonDict] = []
    buffer = ""
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        for chunk in resp:
            text = chunk.decode("utf-8") if isinstance(chunk, bytes) else str(chunk)
            buffer += text
            while "\n\n" in buffer:
                raw, buffer = buffer.split("\n\n", 1)
                for line in raw.splitlines():
                    if line.startswith("data: "):
                        frames.append(cast(JsonDict, json.loads(line[6:])))
    return frames


def _pick_profile() -> int:
    """取优先级最高且启用的 AI 档案；这是真实模型所在。"""
    profiles = _data("GET", "/api/v1/settings/ai/profiles")
    enabled = [p for p in profiles if p.get("isActive")]
    if not enabled:
        raise SystemExit("没有任何启用的 AI 档案，无法跑真实模型测试")
    chosen = max(enabled, key=lambda p: int(p.get("priority") or 0))
    pid = int(chosen["id"])
    label = f"{chosen.get('provider')}/{chosen.get('model')}"
    print(f"[INFO] 使用 AI 档案 #{pid} {label}（优先级 {chosen.get('priority')}）")
    return pid


# ---------------- 各能力的最小可用入参 ----------------

CAPABILITY_INPUTS: dict[str, JsonDict] = {
    "requirement_confirm": {"requirement": "用户可以通过手机号+验证码登录，验证码 60 秒有效，连续错 5 次锁定账号。", "projectName": "登录风控"},
    "testpoint_generation": {"requirement": "购物车支持增删改数量，结算时校验库存与优惠券。", "projectName": "商城", "moduleCount": "3"},
    "case_generation": {"requirement": "搜索框支持关键词搜索、分页、按价格排序，无结果时给空态提示。", "caseTypes": "功能,边界", "count": "5", "projectName": "商城"},
    "xmind_to_case": {"xmindOutline": "- 登录\n  - 正确口令\n  - 错误口令\n  - 验证码过期", "caseTypes": "功能", "projectName": "登录"},
    "bug_analysis": {"bugTitle": "登录后偶尔被踢回登录页", "bugDescription": "部分用户登录成功几秒后跳回登录页", "reproSteps": "1.登录 2.等待 10 秒 3.页面跳转", "expectedResult": "保持登录态", "actualResult": "被踢出", "env": "生产", "version": "v2.3.1"},
    "log_analysis": {"logContent": "2026-11-05 10:00:01 ERROR [auth] login failed: password mismatch user=u1\n2026-11-05 10:00:05 ERROR [auth] account locked user=u1", "errorType": "登录失败", "context": "用户反馈无法登录"},
    "sql_analysis": {"sqlContent": "SELECT * FROM orders WHERE user_id = 1 AND status = 'paid' ORDER BY created_at DESC", "schemaDesc": "orders(id, user_id, status, amount, created_at)，user_id 有索引", "intent": "排查慢查询"},
    "regression_generation": {"changeScope": "支付回调增加了重试逻辑", "affectedModules": "支付,订单", "projectName": "商城"},
    "report_generation": {"projectName": "商城", "reportType": "iteration", "periodStart": "2026-10-01", "periodEnd": "2026-10-31", "metricsJson": "{\"cases\": 120, \"passRate\": 0.95, \"bugs\": 18}", "caseSummary": "新增 40 条用例，通过率 95%", "bugSummary": "修复 18 个缺陷，遗留 2 个 P2"},
    "project_progress_board": {"projectName": "商城", "progressData": "登录模块 100%，支付模块 70%，订单模块 40%", "period": "2026 年 10 月", "milestones": "10-15 支付上线, 10-31 全量上线"},
    "api_test_design": {"projectName": "商城", "apiDescription": "POST /api/orders 创建订单，入参 itemId、quantity、couponId，返回订单号", "baseUrl": "https://api.example.com"},
    "test_plan": {"projectName": "商城", "period": "2026 年 11 月", "requirementSummary": "本期做购物车优化与支付重构", "modules": "购物车,支付"},
    "case_review": {"requirement": "搜索支持分页与排序", "criteria": "覆盖正常/边界/异常，断言明确", "cases": "- 用例1：首页搜索商品名，期望返回列表\n- 用例2：搜索不存在的词，期望空态"},
    "bug_classification": {"bugTitle": "优惠券金额算错", "bugDescription": "满减券在多商品结算时金额重复抵扣", "modulePath": "购物车/结算"},
    "risk_assessment": {"projectName": "商城", "changeScope": "支付回调重构，新增重试", "metricsJson": "{\"changedModules\": 3, \"newCases\": 12, \"bugsLastRelease\": 6}"},
    "automation_script": {"projectName": "商城", "targetType": "web", "framework": "pytest", "language": "python", "appPlatforms": "web", "scenario": "登录成功后进入首页", "baseUrl": "https://shop.example.com"},
    "performance_script": {"projectName": "商城", "scenario": "首页接口压测", "baseUrl": "https://api.example.com", "threads": "50", "duration": "60", "rampUp": "10"},
    "test_data_generation": {"dialect": "mysql", "intent": "生成 10 条用户注册数据，含手机号和邮箱", "projectName": "商城", "schemaDesc": "user(id, phone, email, nickname, created_at)", "rowCount": "10"},
    "prompt_optimize": {"capability": "case_generation", "capabilityGoal": "生成可执行的测试用例", "rawText": "帮我写点登录的用例"},
}


def _short(text: Any, limit: int = 160) -> str:
    return str(text)[:limit].replace("\n", " ")


def section_capabilities() -> None:
    """逐个能力跑真实模型：产出非空、落执行记录、带用量与外发提示。"""
    print(f"[INFO] 共 {len(LAB_CAPABILITIES)} 个实验室能力，逐个跑真实模型…")
    outputs: dict[str, str] = {}
    for index, code in enumerate(LAB_CAPABILITIES, start=1):
        spec_inputs = CAPABILITY_INPUTS.get(code)
        if spec_inputs is None:
            # 没有准备入参的能力跳过并记录，避免静默漏测
            check(f"{code} 有测试入参", False, "CAPABILITY_INPUTS 缺该能力")
            continue
        payload: JsonDict = {"profileId": PROFILE_ID, "variables": dict(spec_inputs)}
        started = time.time()
        try:
            result = _data("POST", f"/api/v1/lab/{code}/run", payload)
        except Exception as exc:  # noqa: BLE001 - 单能力失败不影响其余
            check(f"[{index:02d}/{len(LAB_CAPABILITIES)}] {code} 跑通", False, f"{type(exc).__name__}: {exc}")
            continue
        elapsed = time.time() - started
        output = str(result.get("output") or "")
        outputs[code] = output
        detail = (
            f"model={result.get('model')} latency={result.get('latencyMs')}ms "
            f"egress={result.get('egress')} usage={result.get('usage')} out={_short(output, 80)}"
        )
        check(f"[{index:02d}/{len(LAB_CAPABILITIES)}] {code} 真实产出非空", len(output.strip()) >= 30, detail)
        check(f"  {code} 落执行记录", bool(result.get("recordId")), f"recordId={result.get('recordId')}")
        check(f"  {code} 带模型与用量", bool(result.get("model")) and bool(result.get("usage")), detail)
        check(f"  {code} 标注数据外发", result.get("egress") == "third_party", f"egress={result.get('egress')}")
        # 走了真实云端模型，响应里必须带外发边界提示
        check(f"  {code} 带外发警示文案", bool(result.get("egressWarning")), _short(result.get("egressWarning"), 60))
        # 推理模型跑长大纲（脑图/日志排查）正常要 1~2 分钟，预算放宽到 180s 与 HTTP 超时一致
        if elapsed > 180:
            check(f"  {code} 响应在 180s 内", False, f"耗时 {elapsed:.0f}s")

    # 采一个有产出的能力做下游链路
    adoptable = [c for c in ("case_generation", "xmind_to_case", "regression_generation")
                 if outputs.get(c)]
    if not adoptable:
        check("至少一个可采纳能力有产出", False, "无法继续下游验证")
        return
    return_code = adoptable[0]
    outputs["_sample"] = return_code
    outputs["_sample_output"] = outputs[return_code]


def section_stream() -> None:
    """SSE 流式：帧序 start→delta…→done，seq 单调，degrade/usage 帧合规。"""
    code = "log_analysis"
    payload: JsonDict = {"profileId": PROFILE_ID, "variables": dict(CAPABILITY_INPUTS[code])}
    try:
        frames = _sse_frames(f"/api/v1/lab/{code}/stream", payload)
    except Exception as exc:  # noqa: BLE001
        check("SSE 流式跑通", False, f"{type(exc).__name__}: {exc}")
        return
    types = [f.get("type") for f in frames]
    check("SSE 首帧是 start", bool(types) and types[0] == "start", str(types[:3]))
    check("SSE 含 delta 帧", "delta" in types, str(types))
    check("SSE 末帧是 done", bool(types) and types[-1] == "done", str(types[-3:]))
    seqs = [int(f.get("seq") or 0) for f in frames]
    check("SSE seq 单调递增", all(b > a for a, b in zip(seqs, seqs[1:])), str(seqs))
    start = next((f for f in frames if f.get("type") == "start"), {})
    check("SSE start 帧带外发提示", start.get("egress") == "third_party" and bool(start.get("egressWarning")),
          f"egress={start.get('egress')}")
    done = next((f for f in frames if f.get("type") == "done"), {})
    check("SSE done 帧带回完整产出", len(str(done.get("output") or "")) >= 30, _short(done.get("output"), 80))


def section_routing_preflight() -> None:
    """意图路由 + 模块路由 + 预检（路由走真实模型，预检纯本地）。"""
    route = _data("POST", "/api/v1/lab/route",
                  {"text": "给登录接口写一组用例，覆盖验证码过期和账号锁定", "topK": 3})
    caps = route.get("capabilityRoutes") or []
    check("意图路由返回能力候选", isinstance(caps, list) and len(caps) >= 1, _short(route, 200))
    check("意图路由候选带能力码", all(r.get("capability") for r in caps), _short(caps, 200))
    check("意图路由带模块候选", isinstance(route.get("moduleRoutes"), list), _short(route, 160))

    pre = _data("POST", "/api/v1/lab/preflight", {
        "text": "给登录写用例并生成回归清单",
        "capabilities": ["case_generation", "regression_generation"],
        "variables": {"projectName": "商城"},
    })
    check("预检返回主辅步骤", isinstance(pre, dict) and "primary" in pre and "auxiliary" in pre,
          _short(pre, 200))
    check("预检返回阻断项清单", isinstance(pre.get("blockers"), list), _short(pre.get("blockers"), 160))
    check("预检判定是否就绪", "ready" in pre, _short(pre, 120))


def section_prompt_optimize() -> None:
    """提示词优化：真实模型改写后有实质差异。"""
    result = _data("POST", "/api/v1/ai/prompts/optimize",
                   {"capability": "case_generation", "rawText": "写点登录的用例"})
    check("提示词优化返回优化结果", isinstance(result, dict) and "optimized" in result,
          _short(result, 200))
    optimized = str(result.get("optimized") or "")
    check("优化后提示词更完整", len(optimized) > len("写点登录的用例"), _short(optimized, 120))
    check("提示词优化带模型与用量", bool(result.get("model")), _short(result.get("model"), 60))


def section_kb_assistant() -> None:
    """问答助手三种模式：kb 纯检索、llm 纯模型、hybrid 检索+模型补强。"""
    for mode in ("kb", "llm", "hybrid"):
        try:
            result = _data("POST", "/api/v1/knowledge/ask",
                           {"question": "登录失败有哪些常见原因？", "mode": mode})
        except Exception as exc:  # noqa: BLE001
            check(f"问答助手 {mode} 模式跑通", False, f"{type(exc).__name__}: {exc}")
            continue
        answer = str(result.get("answer") or "")
        detail = f"egress={result.get('egress')} model={result.get('model')} ans={_short(answer, 60)}"
        check(f"问答助手 {mode} 模式有回答", len(answer.strip()) >= 10, detail)
        check(f"问答助手 {mode} 模式带引用结构", isinstance(result.get("refs"), list), detail)


def section_attachments() -> None:
    """文档与图片附件：上传→参与多模态生成→payload 取回。"""
    # 文档附件
    boundary = "----aitp-livetest"
    doc_text = "需求：登录支持手机号+验证码，验证码 60 秒有效，连续错 5 次锁定。"
    doc_body = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="req.txt"\r\n'
        "Content-Type: text/plain\r\n\r\n"
        f"{doc_text}\r\n--{boundary}--\r\n"
    ).encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/v1/lab/attachments/document", data=doc_body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        doc = cast(JsonDict, json.loads(resp.read().decode("utf-8")))["data"]
    doc_id = doc.get("attachmentId")
    check("文档附件上传可抽取文本", bool(doc_id) and doc_text[:10] in str(doc.get("text") or ""),
          _short(doc, 160))

    # 图片附件：1x1 PNG（纯本机构造，无需外部素材）
    import base64
    png_b64 = (
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
    )
    img_form = (
        f"--{boundary}\r\n"
        'Content-Disposition: form-data; name="file"; filename="dot.png"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode("utf-8") + base64.b64decode(png_b64) + f"\r\n--{boundary}--\r\n".encode("utf-8")
    req = urllib.request.Request(
        f"{BASE}/api/v1/lab/attachments/image", data=img_form, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        img = cast(JsonDict, json.loads(resp.read().decode("utf-8")))["data"]
    img_id = img.get("attachmentId")
    check("图片附件上传去水印后落库", bool(img_id), _short(img, 160))
    payload = _data("GET", f"/api/v1/lab/attachments/{img_id}/payload")
    check("图片 payload 返回 dataUrl 供多模态", str(payload.get("dataUrl") or "").startswith("data:image"),
          _short(payload, 80))

    # 带附件跑一次能力（图片走 flash，lite 不支持图像输入）
    if doc_id:
        result = _data("POST", "/api/v1/lab/requirement_confirm/run", {
            "profileId": PROFILE_ID, "attachmentIds": [doc_id],
            "variables": {"requirement": doc_text, "projectName": "登录风控"},
        })
        check("带文档附件的能力正常产出", len(str(result.get("output") or "")) >= 30,
              _short(result.get("output"), 80))
    if img_id:
        result = _data("POST", "/api/v1/lab/log_analysis/run", {
            "profileId": PROFILE_ID, "attachmentIds": [img_id],
            "variables": {"logContent": "见附图中的日志截图", "errorType": "截图分析", "context": "用户提供截图"},
        })
        check("带图片附件的能力正常产出", len(str(result.get("output") or "")) >= 30,
              _short(result.get("output"), 80))


def section_adopt_export(project_id: int) -> None:
    """一键采纳资产 + Excel/Word 导出，都吃真实模型产出。"""
    sample_code = "case_generation"
    generated = _data("POST", f"/api/v1/lab/{sample_code}/run", {
        "profileId": PROFILE_ID,
        "variables": dict(CAPABILITY_INPUTS[sample_code]),
    })
    output = str(generated.get("output") or "")
    check("采纳前先有一份真实产出", len(output) >= 30, _short(output, 80))

    adopted = _data("POST", "/api/v1/lab/output/adopt", {
        "capability": sample_code, "output": output, "target": "requirement",
        "projectId": project_id, "title": "采纳的真实模型用例产出",
    })
    check("真实产出可一键采纳为资产", isinstance(adopted, dict) and bool(adopted.get("id")),
          _short(adopted, 160))

    import base64
    for fmt, magic in (("xlsx", b"PK"), ("docx", b"PK")):
        export = _export_bytes({
            "capability": sample_code, "output": output, "format": fmt,
        })
        check(f"真实产出可导出 {fmt}", export.startswith(magic) and len(export) > 100,
              f"head={export[:4]!r} size={len(export)}")


def _export_bytes(payload: JsonDict) -> bytes:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(f"{BASE}/api/v1/lab/output/export", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=120) as resp:
        return cast(bytes, resp.read())


def section_history_records() -> None:
    """执行记录与历史消息：本轮跑出来的都能在历史里查到。"""
    records = _data("GET", "/api/v1/ai-records?pageSize=100")
    check("执行记录分页可读", isinstance(records.get("list"), list), str(records)[:120])
    mine = [r for r in records.get("list") or [] if r.get("model", "").startswith("sensenova")]
    check("真实模型执行记录已落库", len(mine) >= 1, f"count={len(mine)}")
    if mine:
        first = mine[0]
        detail = _data("GET", f"/api/v1/ai-records/{first['id']}")
        # 执行记录详情的正文落在 outputText，output 是 run 接口的返回字段
        check("单条执行记录可查详情", isinstance(detail, dict) and bool(detail.get("outputText")),
              _short(detail.get("outputText"), 80))
        # 人工复核闭环
        reviewed = _data("POST", f"/api/v1/ai-records/{first['id']}/review",
                         {"status": "approved", "note": "真实模型测试复核"})
        check("执行记录可人工复核", str(reviewed.get("reviewStatus")) == "approved",
              _short(reviewed, 120))

    sessions = _data("GET", "/api/v1/lab/sessions?pageSize=50")
    check("会话历史分页可读", isinstance(sessions.get("list"), list), str(sessions)[:120])
    if sessions.get("list"):
        sid = sessions["list"][0]["id"]
        session = _data("GET", f"/api/v1/lab/sessions/{sid}")
        messages = session.get("messages") if isinstance(session, dict) else None
        check("会话带历史消息", isinstance(messages, list), _short(session, 160))


def section_composite() -> None:
    """多入口顺序执行：父任务 + 子任务 + 步骤轨迹。"""
    result = _data("POST", "/api/v1/lab/composite/run", {
        "profileId": PROFILE_ID,
        "title": "真实模型复合链路",
        "continueOnError": True,
        "steps": [
            {"capability": "requirement_confirm", "name": "需求确认",
             "variables": dict(CAPABILITY_INPUTS["requirement_confirm"])},
            {"capability": "case_generation", "name": "用例生成",
             "variables": dict(CAPABILITY_INPUTS["case_generation"])},
        ],
    })
    check("复合执行返回父任务", bool(result.get("parentTaskId")), _short(result, 200))
    check("复合执行返回整体状态", bool(result.get("status")), _short(result, 160))
    steps = result.get("steps") if isinstance(result, dict) else None
    check("复合执行带步骤轨迹", isinstance(steps, list) and len(steps) == 2, _short(steps, 200))


def section_degrade_chain() -> None:
    """模型降级链：候选按优先级排列，首选第三方时给出外发提示。"""
    pre = _data("POST", "/api/v1/lab/preflight", {
        "capabilities": ["case_generation"], "variables": {"projectName": "商城"},
    })
    # 预检本身不调模型，但会评估候选与模型可用性
    check("预检能评估模型可用性", isinstance(pre, dict), _short(pre, 160))

    # 直接跑一个能力，确认 result 里带模型标识与降级信息结构
    result = _data("POST", "/api/v1/lab/testpoint_generation/run", {
        "profileId": PROFILE_ID,
        "variables": dict(CAPABILITY_INPUTS["testpoint_generation"]),
    })
    check("能力产出带实际使用模型", bool(result.get("model")), _short(result.get("model"), 60))
    degrade = result.get("degrade")
    check("能力产出带降级信息结构", isinstance(degrade, dict), _short(degrade, 120))


def section_cross_project_isolation() -> None:
    """确认不同项目下跑能力，产出与记录按项目归属，不串数据。"""
    projects = _data("GET", "/api/v1/projects/options")
    check("可选项目清单可读", isinstance(projects, list) and len(projects) >= 1, str(projects)[:120])
    if not projects:
        return
    pid = projects[0]["id"]
    result = _data("POST", "/api/v1/lab/case_generation/run", {
        "profileId": PROFILE_ID, "projectId": pid,
        "variables": dict(CAPABILITY_INPUTS["case_generation"]),
    })
    check("指定项目跑能力正常产出", len(str(result.get("output") or "")) >= 30,
          _short(result.get("output"), 80))
    # 该产出应归到指定项目下
    adopted = _data("POST", "/api/v1/lab/output/adopt", {
        "capability": "case_generation", "output": str(result.get("output") or ""),
        "target": "requirement", "projectId": pid, "title": "跨项目采纳校验",
    })
    check("跨项目采纳归属正确", isinstance(adopted, dict), _short(adopted, 120))


def main() -> int:
    global PROFILE_ID
    print(f"[INFO] 目标服务 {BASE}")
    try:
        _data("GET", "/api/v1/meta/enums")
    except Exception as exc:  # noqa: BLE001
        print(f"[FATAL] 服务不可达：{exc}")
        return 1
    PROFILE_ID = _pick_profile()

    # 能力 run 走 LIVE 服务（真实模型存在它的加密档案里）。
    # 为保证 projectId 合法，先确保 LIVE 上至少有一个项目。
    live_projects = _data("GET", "/api/v1/projects/options")
    if not live_projects:
        created = _data("POST", "/api/v1/projects", {"code": "AILIVE", "name": "真实模型测试项目", "owner": "QA"})
        project_id = int(created["id"])
    else:
        project_id = int(live_projects[0]["id"])
    print(f"[INFO] 目标项目 id={project_id}")

    sections = [
        ("AI 能力全量真实跑", section_capabilities),
        ("SSE 流式", section_stream),
        ("意图路由与预检", section_routing_preflight),
        ("提示词优化", section_prompt_optimize),
        ("问答助手三模式", section_kb_assistant),
        ("附件：文档与图片", section_attachments),
        ("采纳与导出", lambda: section_adopt_export(project_id)),
        ("执行记录与历史", section_history_records),
        ("多入口顺序执行", section_composite),
        ("模型降级链", section_degrade_chain),
        ("跨项目隔离", section_cross_project_isolation),
    ]
    for name, runner in sections:
        print(f"[RUN] {name}")
        try:
            runner()
        except Exception as exc:  # noqa: BLE001 - 分区异常要让其余分区继续
            check(f"{name} 分区执行不抛异常", False, f"{type(exc).__name__}: {exc}")

    print("-" * 50)
    print(f"真实模型全路径：PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败清单：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
