"""V2.7.0 平台能力验收（确定性门禁）。

覆盖四块：
  1. App 场景引擎：adb 桩驱动 launch/tap/input/assertText，成功与失败两条路径；
  2. 通知模板：四类渠道渲染 + 邮件渠道配置（密码不落库）；
  3. 缺陷待审：review 模式生成草稿 → 人工确认落正式缺陷；MCP 写入同样走待审；
     失败转缺陷三档（关闭 / 待审 / 自动）、非法档位拦截、旧布尔键兼容与设置页控件；
  4. MCP 服务端：开关/令牌/回环校验、tools/list、只读工具、写工具权限；
  5. 质量洞察：跨轮次失败聚类、用例依赖（禁重复/禁环）、变更影响面闭包。

    python backend/scripts/check_v27.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

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


def api(client: Any, method: str, path: str, payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
    response: TestResponse = getattr(client, method)(path, json=payload, headers=headers)
    data = response.get_json(silent=True) or {}
    return response.status_code, data


def code_of(data: dict[str, Any]) -> int:
    return int(data.get("code") or 0)


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "v27.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8781,
        debug=False,
        db_path=db_path,
        log_path=workdir / "v27.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-v27-"))
    try:
        app = build_app(workdir)
        client = app.test_client()
        db_path = workdir / "v27.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        # ---------------- 迁移 ----------------
        versions = applied_versions(conn)
        check("迁移 087 已应用", 87 in versions)
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )}
        for table in ("biz_bug_draft", "biz_case_dependency", "sys_mcp_call_log"):
            check(f"表 {table} 存在", table in tables)
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(sys_notification_channel)")}
        check("通知渠道模板字段存在", {"channel_type", "template_json", "config_json"} <= columns)

        # ---------------- 基础数据 ----------------
        status, payload = api(client, "post", "/api/v1/projects",
                              {"code": f"V27-{workdir.name[-6:]}", "name": "V2.7 验收项目"})
        project_id = int((payload.get("data") or {}).get("id") or 0)
        check("创建项目", code_of(payload) == 200000 and project_id > 0, str(payload)[:200])

        # ---------------- P0：App 场景 ----------------
        adb_command = f'"{sys.executable}" "{ROOT / "backend" / "scripts" / "mock_adb.py"}"'
        status, payload = api(client, "put", "/api/v1/settings/app-driver", {
            "adbCommand": adb_command, "deviceSerial": "E2E123", "commandTimeout": 20,
        })
        check("保存 App 执行配置", code_of(payload) == 200000, str(payload)[:200])
        status, payload = api(client, "post", "/api/v1/settings/app-driver/probe")
        probe = payload.get("data") or {}
        check("探测到在线设备", bool(probe.get("ok")) and bool(probe.get("devices")),
              json.dumps(probe, ensure_ascii=False)[:300])

        status, payload = api(client, "post", "/api/v1/api-scenarios", {
            "projectId": project_id, "name": "App 登录主流程", "scenarioType": "app",
            "steps": [
                {"name": "启动", "type": "app", "action": "launch",
                 "package": "com.example.app", "activity": ".MainActivity"},
                {"name": "点登录", "type": "app", "action": "tap", "target": "登录"},
                {"name": "输入手机号", "type": "app", "action": "input", "value": "13800000000"},
                {"name": "断言首页", "type": "app", "action": "assertText", "target": "登录成功"},
            ],
        })
        scenario_id = int((payload.get("data") or {}).get("id") or 0)
        check("创建 App 场景", code_of(payload) == 200000 and scenario_id > 0, str(payload)[:300])

        status, payload = api(client, "post",
                              f"/api/v1/api-scenarios/{scenario_id}/run?projectId={project_id}")
        run = payload.get("data") or {}
        steps = (run.get("runs") or [{}])[0].get("steps") or []
        check("App 场景执行通过", code_of(payload) == 200000 and run.get("passed") is True,
              json.dumps(payload, ensure_ascii=False)[:400])
        check("App 步骤轨迹完整（4 步）", len(steps) == 4, f"steps={len(steps)}")
        check("App 文本断言命中", any(
            item.get("name") == "界面文本" and item.get("passed") for item in
            ((steps[-1].get("assertions") if steps else []) or [])
        ))

        status, payload = api(client, "post", "/api/v1/api-scenarios", {
            "projectId": project_id, "name": "App 断言失败路径", "scenarioType": "app",
            "steps": [
                {"name": "启动", "type": "app", "action": "launch", "package": "com.example.app"},
                {"name": "断言不存在", "type": "app", "action": "assertText",
                 "target": "绝对不存在的文案"},
            ],
        })
        fail_scenario_id = int((payload.get("data") or {}).get("id") or 0)
        status, payload = api(client, "post",
                              f"/api/v1/api-scenarios/{fail_scenario_id}/run?projectId={project_id}")
        fail_run = payload.get("data") or {}
        check("App 场景失败被识别", code_of(payload) == 200000 and fail_run.get("passed") is False,
              json.dumps(fail_run, ensure_ascii=False)[:300])
        check("失败生成缺陷待审草稿", int(fail_run.get("autoBugDraftId") or 0) > 0,
              str(fail_run.get("autoBugDraftId")))

        # ---------------- P1：通知模板 ----------------
        status, payload = api(client, "get", "/api/v1/settings/notifications")
        data = payload.get("data") or {}
        check("通知渠道类型齐全", {item["value"] for item in data.get("channelTypes") or []}
              == {"webhook", "dingtalk", "wecom", "feishu", "email"})
        check("模板变量清单可见", "dataSummary" in (data.get("templateVariables") or []))

        rendered: dict[str, dict[str, Any]] = {}
        for channel_type in ("webhook", "dingtalk", "wecom", "feishu", "email"):
            status, payload = api(client, "post", "/api/v1/settings/notifications/preview",
                                  {"channelType": channel_type})
            body = payload.get("data") or {}
            rendered[channel_type] = body
            check(f"{channel_type} 模板可渲染", code_of(payload) == 200000
                  and bool(body.get("subject")) and bool(body.get("text")), str(payload)[:200])
        check("钉钉投递体为 markdown", (rendered["dingtalk"].get("body") or {}).get("msgtype")
              == "markdown")
        check("企微投递体含 content", "content" in ((rendered["wecom"].get("body") or {})
                                                      .get("markdown") or {}))
        check("飞书投递体为 text", (rendered["feishu"].get("body") or {}).get("msg_type") == "text")
        check("邮件渠道单独走 SMTP（无 HTTP 体）", rendered["email"].get("body") is None)

        status, payload = api(client, "post", "/api/v1/settings/notifications/preview", {
            "channelType": "webhook", "template": {"title": "T-{{title}}", "text": "{{projectName}}"},
        })
        custom = payload.get("data") or {}
        check("自定义模板变量生效", custom.get("subject", "").startswith("T-")
              and "示例" in custom.get("subject", ""), str(custom)[:200])

        status, payload = api(client, "post", "/api/v1/settings/notifications", {
            "name": "V2.7 邮件测试", "channelType": "email",
            "events": ["run.completed"], "enabled": False,
            "config": {
                "smtpHost": "smtp.126.com", "smtpPort": 465,
                "smtpUser": "yichengrenhg@126.com", "fromAddr": "yichengrenhg@126.com",
                "toAddrs": "yichengrenhg@126.com", "useSsl": True,
            },
        })
        channel = payload.get("data") or {}
        channel_id = int(channel.get("id") or 0)
        check("创建邮箱渠道", code_of(payload) == 200000 and channel_id > 0, str(payload)[:300])
        check("邮箱配置不回传密码", "password" not in (channel.get("config") or {})
              and channel.get("channelType") == "email")
        status, payload = api(client, "post", "/api/v1/settings/notifications", {
            "name": "缺少 SMTP 的邮箱", "channelType": "email", "events": [],
        })
        check("邮箱渠道必填校验生效", code_of(payload) != 200000, str(payload)[:200])
        status, payload = api(client, "delete", f"/api/v1/settings/notifications/{channel_id}")
        check("删除邮箱渠道", code_of(payload) == 200000)

        # ---------------- P1：缺陷待审 ----------------
        status, payload = api(client, "get", f"/api/v1/bugs/drafts?projectId={project_id}")
        drafts = payload.get("data") or {}
        check("待审列表含场景失败草稿", int(drafts.get("pendingCount") or 0) >= 1,
              str(drafts.get("pendingCount")))
        draft = next((item for item in drafts.get("list") or [] if item.get("source") == "scenario"), None)
        check("草稿来源标记为 scenario", draft is not None)
        confirmed: dict[str, Any] = {}
        if draft:
            status, payload = api(client, "post", f"/api/v1/bugs/drafts/{draft['id']}/confirm", {})
            confirmed = payload.get("data") or {}
            check("确认待审草稿生成缺陷", code_of(payload) == 200000
                  and int(confirmed.get("bugId") or 0) > 0, str(payload)[:300])
            status, payload = api(client, "post", f"/api/v1/bugs/drafts/{draft['id']}/confirm", {})
            check("重复确认被拒绝", code_of(payload) != 200000)

        # ---------------- P1：MCP ----------------
        status, payload = api(client, "post", "/api/v1/mcp",
                              {"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
        check("MCP 默认关闭", status == 403 and code_of(payload) != 200000)

        status, payload = api(client, "put", "/api/v1/settings/mcp",
                              {"enabled": True, "allowWrite": False})
        check("开启 MCP", code_of(payload) == 200000)

        status, payload = api(client, "post", "/api/v1/mcp",
                              {"jsonrpc": "2.0", "id": 2, "method": "initialize"})
        result = payload.get("result") or {}
        check("MCP initialize 返回协议版本", result.get("protocolVersion") is not None,
              json.dumps(payload, ensure_ascii=False)[:300])

        status, payload = api(client, "post", "/api/v1/mcp",
                              {"jsonrpc": "2.0", "id": 3, "method": "tools/list"})
        tools = (payload.get("result") or {}).get("tools") or []
        tool_names = {item["name"] for item in tools}
        check("MCP 工具清单含只读与待审写入",
              {"projects.list", "cases.search", "bugs.draft.create"} <= tool_names)

        status, payload = api(client, "post", "/api/v1/mcp", {
            "jsonrpc": "2.0", "id": 4, "method": "tools/call",
            "params": {"name": "projects.list", "arguments": {}},
        })
        content = ((payload.get("result") or {}).get("content") or [{}])[0].get("text") or ""
        check("只读工具可调用", "V2.7 验收项目" in content, content[:200])

        status, payload = api(client, "post", "/api/v1/mcp", {
            "jsonrpc": "2.0", "id": 5, "method": "tools/call",
            "params": {"name": "bugs.draft.create",
                       "arguments": {"projectId": project_id, "title": "MCP 提交的缺陷"}},
        })
        mcp_write = payload.get("result") or {}
        check("默认拒绝 MCP 写操作", bool(mcp_write.get("isError")))

        api(client, "put", "/api/v1/settings/mcp", {"enabled": True, "allowWrite": True})
        status, payload = api(client, "post", "/api/v1/mcp", {
            "jsonrpc": "2.0", "id": 6, "method": "tools/call",
            "params": {"name": "bugs.draft.create",
                       "arguments": {"projectId": project_id, "title": "MCP 提交的缺陷"}},
        })
        text = ((payload.get("result") or {}).get("content") or [{}])[0].get("text") or ""
        check("开启后写入待审区", "draftId" in text, text[:200])

        status, payload = api(client, "get", f"/api/v1/bugs/drafts?projectId={project_id}")
        mcp_draft = next((item for item in (payload.get("data") or {}).get("list") or []
                          if item.get("source") == "mcp"), None)
        check("MCP 草稿出现在待审区", mcp_draft is not None)
        if mcp_draft:
            status, payload = api(client, "post", f"/api/v1/bugs/drafts/{mcp_draft['id']}/ignore",
                                  {"note": "门禁忽略"})
            check("待审草稿可忽略", code_of(payload) == 200000)

        status, payload = api(client, "post", "/api/v1/settings/mcp/token")
        generated = payload.get("data") or {}
        raw_token = str(generated.get("token") or "")
        check("生成 MCP 令牌只返回一次明文", code_of(payload) == 200000
              and len(raw_token) > 10
              and generated.get("tokenPreview") == f"•••• {raw_token[-4:]}",
              str(payload)[:300])

        status, payload = api(client, "post", "/api/v1/mcp",
                              {"jsonrpc": "2.0", "id": 7, "method": "ping"})
        check("配置令牌后无令牌被拒", status == 403)
        status, payload = api(client, "post", "/api/v1/mcp",
                              {"jsonrpc": "2.0", "id": 8, "method": "ping"},
                              headers={"Authorization": f"Bearer {raw_token}"})
        check("携带令牌可调用", status == 200 and "result" in payload)

        status, payload = api(client, "get", "/api/v1/settings/mcp")
        settings = payload.get("data") or {}
        token_rows = settings.get("tokens") or []
        check("设置接口永不回明文令牌",
              raw_token not in json.dumps(payload, ensure_ascii=False) and bool(token_rows),
              str(payload)[:300])
        check("令牌最后使用已记录", bool(settings.get("lastUsedAt")))

        status, payload = api(client, "post", "/api/v1/settings/mcp/token")
        rotated = (payload.get("data") or {}).get("token") or ""
        check("轮换返回新令牌", code_of(payload) == 200000 and bool(rotated)
              and rotated != raw_token)
        status, _ = api(client, "post", "/api/v1/mcp",
                        {"jsonrpc": "2.0", "id": 9, "method": "ping"},
                        headers={"Authorization": f"Bearer {raw_token}"})
        check("轮换后旧令牌失效", status == 403)
        status, payload = api(client, "post", "/api/v1/mcp",
                              {"jsonrpc": "2.0", "id": 10, "method": "ping"},
                              headers={"Authorization": f"Bearer {rotated}"})
        check("轮换后新令牌可用", status == 200 and "result" in payload)

        status, payload = api(client, "get", "/api/v1/settings/mcp")
        rows = (payload.get("data") or {}).get("tokens") or []
        active = next((item for item in rows if item.get("status") == "active"), None)
        old = next((item for item in rows
                    if item.get("status") == "disabled"
                    and item.get("disableReason") == "rotated"), None)
        check("令牌记录含历史与被轮换标记", active is not None and old is not None, str(rows)[:300])

        if active is not None:
            token_id = int(active["id"])
            status, payload = api(client, "post",
                                  f"/api/v1/settings/mcp/tokens/{token_id}/disable")
            check("停用令牌", code_of(payload) == 200000, str(payload)[:200])
            status, _ = api(client, "post", "/api/v1/mcp",
                            {"jsonrpc": "2.0", "id": 11, "method": "ping"},
                            headers={"Authorization": f"Bearer {rotated}"})
            check("停用后令牌被拒", status == 403)

            status, payload = api(client, "post",
                                  f"/api/v1/settings/mcp/tokens/{token_id}/enable")
            check("停用后可以恢复启用", code_of(payload) == 200000, str(payload)[:200])
            status, _ = api(client, "post", "/api/v1/mcp",
                            {"jsonrpc": "2.0", "id": 12, "method": "ping"},
                            headers={"Authorization": f"Bearer {rotated}"})
            check("恢复启用后令牌可用", status == 200)

            status, payload = api(client, "delete",
                                  f"/api/v1/settings/mcp/tokens/{token_id}")
            check("删除令牌", code_of(payload) == 200000, str(payload)[:200])
            status, _ = api(client, "post", "/api/v1/mcp",
                            {"jsonrpc": "2.0", "id": 13, "method": "ping"},
                            headers={"Authorization": f"Bearer {rotated}"})
            check("删除后令牌不可用", status == 403)
            deleted = next((item for item in ((payload.get("data") or {}).get("tokens") or [])
                            if int(item.get("id") or 0) == token_id), None)
            check("删除后记录状态为已删除",
                  deleted is not None and deleted.get("status") == "deleted")

        audit_count = conn.execute(
            "SELECT COUNT(*) FROM sys_audit_log WHERE resource_type = 'mcp_token'"
        ).fetchone()[0]
        check("MCP 令牌操作写入审计", int(audit_count) >= 5, str(audit_count))

        status, payload = api(client, "get", "/api/v1/settings/mcp/logs")
        check("MCP 调用有留痕", len((payload.get("data") or {}).get("list") or []) > 0)

        # ---------------- P2：质量洞察 ----------------
        case_ids: list[int] = []
        for title in ("登录-A", "下单-B", "支付-C"):
            status, payload = api(client, "post", "/api/v1/test-cases", {
                "projectId": project_id, "title": title, "caseType": "functional",
                "priority": "P2", "expectedResult": "通过", "steps": [],
            })
            case_ids.append(int((payload.get("data") or {}).get("id") or 0))
        check("创建依赖用例", all(case_ids), str(case_ids))

        status, payload = api(client, "post", "/api/v1/insights/dependencies", {
            "projectId": project_id, "caseId": case_ids[1], "dependsOnCaseId": case_ids[0],
        })
        check("建立依赖 B→A", code_of(payload) == 200000, str(payload)[:200])
        status, payload = api(client, "post", "/api/v1/insights/dependencies", {
            "projectId": project_id, "caseId": case_ids[2], "dependsOnCaseId": case_ids[1],
        })
        check("建立依赖 C→B", code_of(payload) == 200000)
        status, payload = api(client, "post", "/api/v1/insights/dependencies", {
            "projectId": project_id, "caseId": case_ids[1], "dependsOnCaseId": case_ids[0],
        })
        check("重复依赖被拒绝", code_of(payload) != 200000)
        status, payload = api(client, "post", "/api/v1/insights/dependencies", {
            "projectId": project_id, "caseId": case_ids[0], "dependsOnCaseId": case_ids[2],
        })
        check("成环依赖被拒绝", code_of(payload) != 200000, str(payload)[:200])

        status, payload = api(client, "post", "/api/v1/insights/impact",
                              {"projectId": project_id, "caseIds": [case_ids[0]]})
        impact = payload.get("data") or {}
        check("影响面包含直接与依赖闭包", code_of(payload) == 200000
              and len(impact.get("direct") or []) == 1
              and len(impact.get("affected") or []) == 2
              and impact.get("total") == 3, str(impact)[:300])

        # 两轮同签名失败 → 聚类
        now = "2026-11-20T10:00:00Z"
        run_ids: list[int] = []
        for index in range(2):
            cursor = conn.execute("""
                INSERT INTO biz_test_run
                    (project_id, name, status, total, failed, created_at, updated_at, is_deleted)
                VALUES (?, ?, 'completed', 1, 1, ?, ?, 0)
            """, (project_id, f"V27 轮次 {index + 1}", now, now))
            run_id = int(cursor.lastrowid or 0)
            run_ids.append(run_id)
            conn.execute("""
                INSERT INTO biz_test_run_case
                    (run_id, case_id, seq, case_no, title, result, actual_result,
                     created_at, updated_at, is_deleted)
                VALUES (?, ?, 1, ?, '登录失败', 'failed', ?, ?, ?, 0)
            """, (run_id, case_ids[0], f"V27-{index}", f"验证码错误 code={1000 + index}",
                  now, now))
        conn.commit()

        status, payload = api(client, "get",
                              f"/api/v1/insights/failure-clusters?projectId={project_id}&rounds=5")
        clusters = (payload.get("data") or {}).get("clusters") or []
        check("跨轮次失败聚类", code_of(payload) == 200000 and len(clusters) == 1
              and clusters[0].get("count") == 2, str(clusters)[:400])
        check("聚类给出趋势与涉及用例", bool(clusters) and clusters[0].get("trend") in
              ("up", "down", "flat") and clusters[0].get("caseCount") == 1)

        status, payload = api(client, "get", f"/api/v1/insights/dependencies?projectId={project_id}")
        check("依赖列表可查询", len((payload.get("data") or {}).get("items") or []) == 2)

        # ---------------- P2：失败转缺陷三档 ----------------
        status, payload = api(client, "get", "/api/v1/settings/config")
        config_rows: list[dict[str, Any]] = payload.get("data") or []
        mode_row: dict[str, Any] = next((item for item in config_rows
                                        if item.get("key") == "automation.auto_bug_mode"), {})
        check("三档配置键默认为待审", mode_row.get("value") == "review", str(mode_row)[:200])
        check("三档配置键带取值说明", bool(mode_row.get("hint")), str(mode_row)[:200])

        status, payload = api(client, "put", "/api/v1/settings/config", {"items": [
            {"key": "automation.auto_bug_mode", "value": "half"}]})
        check("非法档位被拒绝", code_of(payload) != 200000, str(payload)[:200])
        status, payload = api(client, "get", "/api/v1/settings/config")
        kept: dict[str, Any] = next((item for item in ((payload.get("data") or []))
                                     if item.get("key") == "automation.auto_bug_mode"), {})
        check("非法档位不改动原值", kept.get("value") == "review", str(kept)[:200])

        def run_failing_scenario() -> dict[str, Any]:
            """重跑那条「断言必败」的 App 场景；每次 run_no 不同，去重逻辑不会互相吞掉。"""
            _, inner = api(client, "post",
                           f"/api/v1/api-scenarios/{fail_scenario_id}/run?projectId={project_id}")
            return inner.get("data") or {}

        def set_auto_bug(*items: dict[str, str]) -> None:
            api(client, "put", "/api/v1/settings/config", {"items": list(items)})

        set_auto_bug({"key": "automation.auto_bug_mode", "value": "off"})
        off_run = run_failing_scenario()
        check("关闭档：失败既不建缺陷也不落待审",
              off_run.get("passed") is False and not off_run.get("autoBugId")
              and not off_run.get("autoBugDraftId"),
              json.dumps(off_run, ensure_ascii=False)[:250])

        set_auto_bug({"key": "automation.auto_bug_mode", "value": "auto"})
        auto_run = run_failing_scenario()
        check("自动档：失败直接生成正式缺陷", bool(auto_run.get("autoBugId"))
              and not auto_run.get("autoBugDraftId"),
              json.dumps(auto_run, ensure_ascii=False)[:250])

        # 回归护栏：曾经传参只有 camelCase 的 runNo，去重标记退化成空编号，
        # 同项目 auto 档第二次失败就再也不建单了。
        auto_run_again = run_failing_scenario()
        check("自动档：每次失败执行各建一条缺陷",
              bool(auto_run_again.get("autoBugId"))
              and auto_run_again.get("autoBugId") != auto_run.get("autoBugId"),
              json.dumps(auto_run_again, ensure_ascii=False)[:250])

        set_auto_bug({"key": "automation.auto_bug_mode", "value": "review"})
        review_run = run_failing_scenario()
        check("待审档：失败只进待审区", int(review_run.get("autoBugDraftId") or 0) > 0
              and not review_run.get("autoBugId"),
              json.dumps(review_run, ensure_ascii=False)[:250])

        # 三档键留空 → 回退旧布尔键
        set_auto_bug({"key": "automation.auto_bug_mode", "value": ""},
                     {"key": "automation.auto_bug", "value": "true"})
        legacy_on = run_failing_scenario()
        check("旧键兼容：三档留空 + auto_bug=true 走自动", bool(legacy_on.get("autoBugId")),
              json.dumps(legacy_on, ensure_ascii=False)[:250])
        set_auto_bug({"key": "automation.auto_bug", "value": "false"})
        legacy_off = run_failing_scenario()
        check("旧键兼容：三档留空 + auto_bug=false 不产缺陷",
              not legacy_off.get("autoBugId") and not legacy_off.get("autoBugDraftId"),
              json.dumps(legacy_off, ensure_ascii=False)[:250])

        view_text = (ROOT / "frontend" / "src" / "views" / "settings"
                     / "PerformanceSettingsView.vue").read_text(encoding="utf-8")
        check("设置页给出三档控件",
              all(f'value="{name}"' in view_text for name in ("off", "review", "auto"))
              and "automation.auto_bug_mode" in view_text,
              "PerformanceSettingsView.vue 未提供三档控件")

        # 运行时体检要起本机进程探 adb / JMeter（未就绪时单档 15s 超时），
        # 保存按钮只该等配置落库，体检结果后台补齐。
        for view_name in ("PerformanceSettingsView.vue", "AutomationSettingsView.vue"):
            source = (ROOT / "frontend" / "src" / "views" / "settings"
                      / view_name).read_text(encoding="utf-8")
            head = source.split("async function save(", 1)
            save_body = head[1].split("\n}", 1)[0] if len(head) > 1 else ""
            check(f"{view_name} 保存不被运行时体检锁住",
                  "void loadRuntime()" in save_body and "await load()" not in save_body,
                  f"{view_name} 的 save() 仍在 await 运行时体检")

        conn.close()
        return 0 if _failed == 0 else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
        print(f"\nPASS={_passed} FAIL={_failed}")
        if _failures:
            print("失败项：" + "、".join(_failures))


if __name__ == "__main__":
    raise SystemExit(main())
