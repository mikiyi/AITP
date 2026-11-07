"""P6 AI 实验室验收脚本。

在临时库 + 本地 mock 模型上验证：
- 10 个能力逐个跑通，产出非空；JSON 能力能被解析
- 智能路由：文本 → 能力候选 + 功能点候选
- XMind 大纲解析：content.json / content.xml / 粘贴文本三种来源
- 产出物入待审池后，正式库计数不变
- 功能点未启用能力时返回 140105

    python backend/scripts/check_lab.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import zipfile
from io import BytesIO
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
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

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_lab.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_lab.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


# ---------------- 能力输入样例 ----------------

CAPABILITY_SAMPLES: list[tuple[str, JsonDict]] = [
    ("requirement_confirm", {"requirement": "手机号+验证码登录，验证码 60 秒有效", "projectName": "示例电商"}),
    ("testpoint_generation", {"requirement": "手机号+验证码登录", "projectName": "示例电商", "moduleCount": "3"}),
    ("case_generation", {"requirement": "手机号+验证码登录", "caseTypes": "functional", "count": "3", "projectName": "示例电商"}),
    ("xmind_to_case", {"xmindOutline": "登录\n  正常流程\n    登录成功", "caseTypes": "functional", "projectName": "示例电商"}),
    ("bug_analysis", {
        "bugTitle": "支付回调金额翻倍", "bugDescription": "重复回调导致金额翻倍",
        "reproSteps": "1. 发起支付 2. 重放回调", "expectedResult": "只入账一次",
        "actualResult": "入账两次", "env": "测试环境", "version": "v2.1",
    }),
    ("log_analysis", {"logContent": "NullPointerException at PayService.java:88", "errorType": "NullPointerException", "context": "支付回调线程"}),
    ("sql_analysis", {"sqlContent": "select * from orders where user_id = 1", "schemaDesc": "orders(id,user_id,amount)", "intent": "查用户订单"}),
    ("regression_generation", {"changeScope": "支付回调改造", "affectedModules": "交易/支付", "projectName": "示例电商"}),
    ("report_generation", {
        "projectName": "示例电商", "reportType": "summary", "periodStart": "2026-10-01",
        "periodEnd": "2026-10-31", "metricsJson": '{"total": 12}',
        "caseSummary": "通过 6 条", "bugSummary": "遗留 2 个",
    }),
    ("project_progress_board", {
        "projectName": "示例电商", "progressData": "用例完成 60%",
        "period": "2026 第 45 周", "milestones": "11-20 提测",
    }),
    ("test_data_generation", {
        "projectName": "示例电商", "dialect": "MySQL 8.0",
        "intent": "给订单表造 20 条数据，覆盖三种订单状态与金额为 0 的边界值",
        "schemaDesc": "orders(id, user_id, amount, status, created_at)", "rowCount": "20",
    }),
]

JSON_CAPABILITIES = {"testpoint_generation", "case_generation", "xmind_to_case", "regression_generation"}


def check_capabilities(api: Api) -> None:
    for code, variables in CAPABILITY_SAMPLES:
        data = api.data("POST", f"/api/v1/lab/{code}/run", {"variables": variables})
        output = str(data.get("output") or "")
        ok = check(f"能力 {code} 产出非空", bool(output.strip()), str(data)[:200])
        if ok and code in JSON_CAPABILITIES:
            check(f"能力 {code} 输出可解析为 JSON", data.get("parsed") is not None, str(data)[:200])
            try:
                json.loads(output)
                pure = True
            except json.JSONDecodeError:
                pure = False
            check(f"能力 {code} 输出为纯 JSON（复核标记不污染结构）", pure, output[:200])
        check(f"能力 {code} 返回人工复核标志", isinstance(data.get("reviewRequired"), bool),
              str(data.get("reviewRequired")))
        check(f"能力 {code} 记录了模型与耗时",
              bool(data.get("model")) and int(data.get("latencyMs", 0)) >= 0,
              str(data)[:200])


def check_route(api: Api, project_id: int) -> None:
    data = api.data("POST", "/api/v1/lab/route", {
        "text": "支付回调重复推送导致金额翻倍",
        "projectId": project_id,
        "topK": 3,
    })
    caps = data.get("capabilityRoutes") or []
    mods = data.get("moduleRoutes") or []
    check("路由返回能力候选", bool(caps), str(data)[:300])
    check("路由返回功能点候选", bool(mods), str(data)[:300])
    check("路由结果标注来源", data.get("source") in ("ai", "fallback"), str(data.get("source")))
    if caps:
        check("能力候选带归属功能点", "targetModuleId" in caps[0], str(caps[0]))
    if mods:
        check("功能点候选带完整路径", "/" in str(mods[0].get("path")), str(mods[0]))


def build_module_tree(api: Api, project_id: int) -> int:
    """建一棵四级模块树，供功能点路由与能力绑定验证使用。"""
    module = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "parentId": None, "nodeType": "module", "name": "交易模块",
    })
    submodule = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "parentId": module["id"], "nodeType": "submodule", "name": "支付",
    })
    feature = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "parentId": submodule["id"], "nodeType": "feature", "name": "支付回调",
    })
    return int(feature["id"])


def make_xmind_json() -> bytes:
    content = [{
        "id": "sheet-1",
        "title": "登录",
        "rootTopic": {
            "id": "root",
            "title": "登录模块",
            "children": {
                "attached": [
                    {"id": "n1", "title": "手机号登录",
                     "children": {"attached": [{"id": "n2", "title": "验证码时效"}]}},
                ]
            },
        },
    }]
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.json", json.dumps(content, ensure_ascii=False))
    return buffer.getvalue()


def make_xmind_xml() -> bytes:
    xml = """<?xml version="1.0" encoding="UTF-8"?>
<xmap-content version="2.0">
  <sheet id="s1">
    <topic root="1">
      <title>登录模块</title>
      <children>
        <topics type="attached">
          <topic id="t1"><title>手机号登录</title></topic>
        </topics>
      </children>
    </topic>
  </sheet>
</xmap-content>"""
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.xml", xml)
    return buffer.getvalue()


def check_xmind(api: Api) -> None:
    data = api.data("POST", "/api/v1/lab/xmind/parse", None, data={
        "file": (BytesIO(make_xmind_json()), "demo.xmind"),
    }, content_type="multipart/form-data")
    tree = data.get("tree") or []
    check("xmind content.json 解析出树", bool(tree), str(data)[:200])
    if tree:
        check("xmind content.json 保留子节点",
              bool((tree[0].get("children") or [])), str(tree[0])[:200])
        check("xmind 解析回大纲文本", "登录模块" in str(data.get("outlineText") or ""),
              str(data.get("outlineText"))[:200])

    data = api.data("POST", "/api/v1/lab/xmind/parse", None, data={
        "file": (BytesIO(make_xmind_xml()), "demo8.xmind"),
    }, content_type="multipart/form-data")
    check("xmind content.xml 解析出树", bool(data.get("tree")), str(data)[:200])

    data = api.data("POST", "/api/v1/lab/xmind/parse", {"outlineText": "登录模块\n  手机号登录\n    验证码时效"})
    check("粘贴大纲文本解析出三级结构",
          len(((data.get("tree") or [{}])[0]).get("children") or []) == 1, str(data)[:200])

    resp, body = api.call("POST", "/api/v1/lab/xmind/parse", {})
    check("既无文件也无大纲时返回参数错误",
          body.get("code") == ErrorCode.PARAM_ERROR, str(body))


def check_module_binding(api: Api, module_id: int) -> None:
    resp, body = api.call("POST", "/api/v1/lab/log_analysis/run", {
        "variables": {"logContent": "boom", "errorType": "E", "context": "c"},
        "moduleId": module_id,
    })
    check("功能点未启用能力时返回 140105",
          body.get("code") == ErrorCode.ILLEGAL_OPERATION, str(body))

    api.data("PUT", f"/api/v1/modules/{module_id}/capabilities", {
        "capabilities": [{"capability": "log_analysis", "enabled": True, "promptCode": None, "config": {}}],
    })
    data = api.data("POST", "/api/v1/lab/log_analysis/run", {
        "variables": {"logContent": "boom", "errorType": "E", "context": "c"},
        "moduleId": module_id,
    })
    check("功能点启用能力后可执行", bool(str(data.get("output") or "").strip()), str(data)[:200])


def check_knowledge_flow(api: Api) -> None:
    before = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")
    drafts_before = api.data("GET", "/api/v1/knowledge/drafts?pageSize=100")
    draft = api.data("POST", "/api/v1/knowledge/drafts", {
        "title": "验收用例产出物",
        "content": "这是一条 AI 产出物，用于验证待审池流转。",
        "summary": "验收产出物",
        "category": "通用",
        "sourceType": "ai_output",
        "producer": "requirement_confirm",
    })
    after = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")
    drafts_after = api.data("GET", "/api/v1/knowledge/drafts?pageSize=100")
    check("产出物入库后正式库计数不变",
          int(before.get("total", 0)) == int(after.get("total", 0)),
          f"{before.get('total')} -> {after.get('total')}")
    check("产出物进入待审核池",
          int(drafts_after.get("total", 0)) == int(drafts_before.get("total", 0)) + 1,
          f"{drafts_before.get('total')} -> {drafts_after.get('total')}")
    review = api.data("POST", f"/api/v1/knowledge/drafts/{draft['id']}/review",
                      {"action": "approve", "comment": "验收通过"})
    check("审核通过后转正式库", bool(review.get("articleId")), str(review)[:200])
    published = api.data("GET", "/api/v1/knowledge/articles?pageSize=100")
    check("审核通过后正式库计数 +1",
          int(published.get("total", 0)) == int(after.get("total", 0)) + 1,
          f"{after.get('total')} -> {published.get('total')}")


def check_offline(api: Api) -> None:
    """停用全部模型并清掉环境变量，验证离线降级路径。"""
    env_keys = ("AITP_AI_API_KEY", "OPENAI_API_KEY", "DASHSCOPE_API_KEY", "DEEPSEEK_API_KEY")
    saved = {key: os.environ.pop(key, None) for key in env_keys}
    try:
        profiles = api.data("GET", "/api/v1/settings/ai/profiles")
        for profile in profiles:
            api.data("PUT", f"/api/v1/settings/ai/profiles/{profile['id']}", {"isActive": False})

        data = api.data("POST", "/api/v1/lab/route", {
            "text": "支付回调重复推送导致金额翻倍", "topK": 3,
        })
        check("无模型时路由走离线兜底", data.get("source") == "fallback", str(data)[:200])
        check("无模型时仍能给出能力候选", bool(data.get("capabilityRoutes")), str(data)[:200])
        check("无模型时仍能给出功能点候选", bool(data.get("moduleRoutes")), str(data)[:200])

        _, body = api.call("POST", "/api/v1/lab/requirement_confirm/run", {
            "variables": {"requirement": "手机号登录", "projectName": "示例电商"},
        })
        check("无模型时执行能力给出可读提示",
              body.get("code") == ErrorCode.AI_CREDENTIAL_MISSING
              and "配置" in str(body.get("message")),
              str(body)[:200])
    finally:
        for key, value in saved.items():
            if value is not None:
                os.environ[key] = value


def check_restart_reconcile(workdir: pathlib.Path) -> None:
    """服务重启时还挂在 running 的任务要被判中断，不能永远显示「执行中」。"""
    db_path = workdir / "check_lab.db"
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        task_id = int(conn.execute("SELECT MAX(id) FROM biz_ai_task").fetchone()[0] or 0)
        if not task_id:
            check("存在可用于重启校验的任务", False, "biz_ai_task 为空")
            return
        conn.execute(
            "UPDATE biz_ai_task SET status = 'running', finished_at = NULL, error_message = NULL"
            " WHERE id = ?",
            (task_id,),
        )
        conn.execute(
            "INSERT INTO biz_ai_step (task_id, capability, step_order, step_name, status,"
            " created_at, updated_at) VALUES (?, 'requirement_confirm', 1, '重启校验步骤',"
            " 'running', '2026-11-06T00:00:00Z', '2026-11-06T00:00:00Z')",
            (task_id,),
        )
        conn.commit()
    finally:
        conn.close()

    build_app(workdir)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        task = conn.execute(
            "SELECT status, error_message FROM biz_ai_task WHERE id = ?", (task_id,)
        ).fetchone()
        step = conn.execute(
            "SELECT status, error_message FROM biz_ai_step WHERE task_id = ?", (task_id,)
        ).fetchone()
    finally:
        conn.close()

    check("重启后 running 任务判为失败", str(task["status"]) == "failed", str(dict(task))[:200])
    check("重启后任务写明中断原因", "中断" in str(task["error_message"]), str(task["error_message"]))
    check("重启后步骤一并中断", str(step["status"]) == "failed" and "中断" in str(step["error_message"]),
          str(dict(step))[:200])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_lab_"))
    port = free_port()
    mock = subprocess.Popen(
        [sys.executable, str(ROOT / "backend" / "scripts" / "mock_openai_server.py"),
         "--port", str(port), "--ollama-port", str(free_port())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    try:
        if not wait_port(port):
            print("[FAIL] mock 模型服务启动失败")
            return 1
        app = build_app(workdir)
        api = Api(app)

        api.data("POST", "/api/v1/settings/ai/profiles", {
            "name": "本地 mock",
            "provider": "custom",
            "protocol": "openai_compat",
            "baseUrl": f"http://127.0.0.1:{port}/v1",
            "model": "mock-model",
            "apiKey": "sk-test",
            "isDefault": True,
            "isActive": True,
            "priority": 100,
        })

        project = api.data("POST", "/api/v1/projects", {"code": "LAB-EC", "name": "示例电商项目"})
        project_id = int(project["id"])
        feature_id = build_module_tree(api, project_id)

        check_capabilities(api)
        check_route(api, project_id)
        check_xmind(api)
        check_module_binding(api, feature_id)
        check_knowledge_flow(api)
        check_offline(api)
        check_restart_reconcile(workdir)
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock.kill()
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
