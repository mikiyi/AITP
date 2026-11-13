"""验证 Web 探索性测试（GAP-45）：MCP 驱动→留证据→产物进待审池→审核转缺陷。

    python backend/scripts/check_exploratory_web.py

全部在临时库 + 本机临时 stdio MCP 桩上运行，不读写正式数据、不调模型、不起浏览器；
探索执行走任务队列，门禁轮询会话状态直到收口。
"""

from __future__ import annotations

import base64
import json
import os
import pathlib
import sqlite3
import sys
import tempfile
import time
from datetime import datetime, timedelta, timezone
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import exploratory_service as es  # noqa: E402
from flask import Flask  # noqa: E402

JsonDict = dict[str, Any]
V1 = "/api/v1"
STUB = ROOT / "backend" / "scripts" / "mock_mcp_server.py"
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[Any, JsonDict]:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} 失败: code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None) -> JsonDict:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code, f"code={body.get('code')} msg={body.get('message')}")
        return body


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "exploratory.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8771, debug=False, db_path=db_path,
        log_path=workdir / "exploratory.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def mcp_command() -> str:
    return f'"{sys.executable}" "{STUB}"'


def wait_session(api: Api, session_id: int, timeout: float = 60.0) -> JsonDict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = api.data("GET", f"{V1}/exploratory/sessions/{session_id}")
        status = detail["session"]["status"]
        if status in ("done", "failed", "cancelled"):
            return cast(JsonDict, detail)
        time.sleep(0.4)
    raise AssertionError(f"探索会话 {session_id} 执行超时")


def wait_any(api: Api, session_id: int, timeout: float = 30.0) -> str:
    """轮询直到收口，返回最终状态；不因 failed 抛错（测失败路径用）。"""
    deadline = time.time() + timeout
    status = ""
    while time.time() < deadline:
        detail = api.data("GET", f"{V1}/exploratory/sessions/{session_id}")
        status = detail["session"]["status"]
        if status in ("done", "failed", "cancelled"):
            return str(status)
        time.sleep(0.4)
    raise AssertionError(f"探索会话 {session_id} 等待超时（最后 {status}）")


def write_log(workdir: pathlib.Path) -> tuple[pathlib.Path, str, str]:
    """写一份带时间戳的日志：off=-8 在窗口外，off=0 在窗口内。"""
    now = datetime.now(timezone.utc)
    inside = (now + timedelta(seconds=0)).strftime("%Y-%m-%dT%H:%M:%SZ")
    outside = (now + timedelta(seconds=-8)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_path = workdir / "app.log"
    log_path.write_text(
        f"{outside} INFO order service boot\n"
        f"{inside} ERROR payment callback duplicate amount=200\n",
        encoding="utf-8",
    )
    return log_path, inside, outside


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-exploratory-"))
    db_path = workdir / "exploratory.db"
    try:
        app = build_app(workdir)
        api = Api(app)
        project_id = int(api.data("POST", f"{V1}/projects", {"code": "EXP", "name": "探索项目"})["id"])

        # ---- 迁移与表 ----
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        check("迁移 056 已应用", 56 in applied_versions(conn))
        check("探索会话表已建立", conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='biz_exploration_session'"
        ).fetchone() is not None)
        check("探索步骤表已建立", conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='biz_exploration_step'"
        ).fetchone() is not None)
        check("待审池带 bug_id 列", "bug_id" in {
            r[1] for r in conn.execute("PRAGMA table_info(biz_knowledge_draft)").fetchall()})
        conn.close()

        # ---- 配置读写与边界 ----
        settings = api.data("GET", f"{V1}/exploratory/settings")
        check("默认配置可读", settings["maxSteps"] == 20 and settings["logWindowSeconds"] == 5,
              str(settings))
        check("钉住工具齐全", len(settings["pinnedTools"]) == 7, str(settings.get("pinnedTools")))
        bad = api.expect("步数越界被拒", "PUT", f"{V1}/exploratory/settings",
                          ErrorCode.PARAM_ERROR, {"maxSteps": 999})
        check("越界返回参数错误", bad["code"] == ErrorCode.PARAM_ERROR, str(bad))
        bad_window = api.expect("日志窗口越界被拒", "PUT", f"{V1}/exploratory/settings",
                                 ErrorCode.PARAM_ERROR, {"logWindowSeconds": 0})
        check("窗口下界=1", bad_window["code"] == ErrorCode.PARAM_ERROR, str(bad_window))
        too_many_logs = api.expect("日志来源超 5 个被拒", "PUT", f"{V1}/exploratory/settings",
                                    ErrorCode.PARAM_ERROR, {
                                        "logSources": [{"name": f"s{i}", "path": f"/tmp/{i}.log"}
                                                       for i in range(6)]})
        check("日志来源上限 5", too_many_logs["code"] == ErrorCode.PARAM_ERROR, str(too_many_logs))
        log_path, _inside, outside = write_log(workdir)
        saved = api.data("PUT", f"{V1}/exploratory/settings", {
            "mcpCommand": mcp_command(), "logWindowSeconds": 5,
            "logSources": [{"name": "app", "path": str(log_path), "source": "file"}],
        })
        check("配置保存成功", saved["mcpCommand"] == mcp_command() and len(saved["logSources"]) == 1,
              str(saved))

        meta = api.data("GET", f"{V1}/exploratory/meta")
        check("meta 提供驱动/状态/异常枚举",
              len(meta["drivers"]) == 2 and len(meta["statuses"]) == 6 and len(meta["anomalyTypes"]) == 4,
              str(meta)[:200])

        # ---- 未配置 MCP 命令时拒绝运行 ----
        conn = sqlite3.connect(db_path)
        conn.execute("UPDATE sys_config SET config_value=? WHERE config_key=?",
                     (json.dumps({**es.DEFAULT_SETTINGS, "mcpCommand": ""}, ensure_ascii=False),
                      es.SETTINGS_KEY))
        conn.commit()
        sess_no_mcp = api.data("POST", f"{V1}/exploratory/sessions", {
            "projectId": project_id, "targetUrl": "http://x.local/", "plan": [],
        })
        api.expect("无 MCP 命令拒绝运行", "POST",
                   f"{V1}/exploratory/sessions/{sess_no_mcp['id']}/run",
                   ErrorCode.ILLEGAL_OPERATION)
        # 恢复配置
        api.data("PUT", f"{V1}/exploratory/settings", {"mcpCommand": mcp_command(),
                                                        "logSources": [{"name": "app", "path": str(log_path)}]})
        conn.close()

        # ---- 钉住工具缺失时运行失败（服务缺工具）----
        plan_missing = [
            {"action": "navigate", "url": "http://stub.local/"},
            {"action": "snapshot"},
        ]
        s_missing = api.data("POST", f"{V1}/exploratory/sessions", {
            "projectId": project_id, "targetUrl": "http://stub.local/", "driver": "scripted",
            "plan": plan_missing, "maxSteps": 2,
        })
        check("会话创建为草稿", s_missing["status"] == "draft", str(s_missing["status"]))
        # 直接调 service 层：注入缺失工具环境，同步执行以拿到 McpError
        os.environ["AITP_MCP_MISSING_TOOL"] = "browser_snapshot"
        try:
            srow = api.data("GET", f"{V1}/exploratory/sessions/{s_missing['id']}")
            from aitp.services import exploratory_service as _es
            direct = sqlite3.connect(db_path)
            direct.row_factory = sqlite3.Row
            try:
                _es.execute_session(direct, s_missing["id"], db_path)
                check("缺工具时探索失败", False, "should have raised")
            except Exception as exc:
                check("缺工具时探索失败", "缺少钉住工具" in str(exc), str(exc)[:120])
            finally:
                direct.close()
        finally:
            os.environ.pop("AITP_MCP_MISSING_TOOL", None)
        api.data("POST", f"{V1}/exploratory/sessions/{s_missing['id']}/run")
        _resp, body = api.call("POST", f"{V1}/exploratory/sessions/{s_missing['id']}/run")
        check("重复运行被拒", body["code"] == ErrorCode.ILLEGAL_OPERATION, str(body))

        # ---- 主流程：异常注入（http_error on step 2 & 4）----
        os.environ["AITP_MCP_ANOMALY_STEPS"] = "2,4"
        os.environ["AITP_MCP_ANOMALY_KIND"] = "http"
        log_inside = None
        now = datetime.now(timezone.utc)
        main_plan = [
            {"action": "navigate", "url": "http://stub.local/orders"},
            {"action": "click", "selector": "#reload"},
            {"action": "type", "selector": "#password", "value": "SuperSecret123!"},
            {"action": "snapshot"},
            {"action": "click", "selector": "#pay"},
        ]
        sess = api.data("POST", f"{V1}/exploratory/sessions", {
            "projectId": project_id, "name": "下单探索", "targetUrl": "http://stub.local/orders",
            "driver": "scripted", "plan": main_plan, "maxSteps": 5,
        })
        check("会话编号 EX- 前缀", str(sess["sessionNo"]).startswith("EX-"), sess["sessionNo"])
        api.data("POST", f"{V1}/exploratory/sessions/{sess['id']}/run")
        detail = wait_session(api, sess["id"])
        srow = detail["session"]
        check("会话执行完成", srow["status"] == "done", srow.get("errorMessage"))
        check("未降级（有脚本驱动）", not srow["degraded"], str(srow.get("degraded")))
        check("步数收口", srow["stepDone"] == 5 and srow["stepTotal"] == 5, str(srow["stepDone"]))
        check("异常步数为 2", srow["anomalyCount"] == 2, str(srow["anomalyCount"]))

        steps = detail["steps"]
        check("步骤按序留痕", [s["stepNo"] for s in steps] == [1, 2, 3, 4, 5], str([s["stepNo"] for s in steps]))
        anom = [s for s in steps if s["isAnomaly"]]
        check("异常步定位正确", [s["stepNo"] for s in anom] == [2, 4], str([s["stepNo"] for s in anom]))
        check("异常类型为接口错误",
              all(s["anomalyType"] == "http_error" for s in anom), str([s["anomalyType"] for s in anom]))
        check("检测锚点非空", all(s["detectedAt"] for s in anom), str([s["detectedAt"] for s in anom]))
        ok_step = [s for s in steps if s["stepNo"] == 3][0]
        check("正常步无锚点", not ok_step["detectedAt"] and not ok_step["isAnomaly"], str(ok_step))

        # ---- 输入脱敏 ----
        type_step = [s for s in steps if s["stepNo"] == 3][0]
        check("口令输入已脱敏", type_step["inputMasked"] == "[输入已脱敏]", str(type_step["inputMasked"]))
        check("脱敏后无明文口令", "SuperSecret" not in (type_step["inputMasked"] or ""),
              str(type_step["inputMasked"]))

        # ---- 证据包：双锚点窗口 / 3 截图 / traceId / 网络 ----
        ev_ids = [s["evidenceId"] for s in anom]
        check("每个异常步都有证据", all(ev_ids), str(ev_ids))
        first_ev = api.data("GET", f"{V1}/exploratory/steps/{anom[0]['id']}/evidence")
        check("证据来源为 exploratory", first_ev["sourceType"] == "exploratory", str(first_ev["sourceType"]))
        check("证据结论为失败", first_ev["result"] == "failed", str(first_ev["result"]))
        check("异常强制 3 张截图", len(first_ev["attachments"]) == 3,
              str(len(first_ev["attachments"])))
        att = first_ev["attachments"][0]
        check("截图内容非空 base64", bool(base64.b64decode(att.get("data") or "") or att.get("filePath")),
              str(att))
        check("证据带 traceId", bool(first_ev["traceId"]), str(first_ev.get("traceId")))
        ref = first_ev["sourceRef"]
        check("证据引用异常步", ref.get("table") == "biz_exploration_step" and ref.get("id"),
              str(ref)[:200])
        check("证据网络快照含 5xx", "500" in (first_ev["requestSummary"] or ""),
              first_ev["requestSummary"][:200])
        log_summary = first_ev["logSummary"] or ""
        check("日志切片命中窗口内行", "payment callback duplicate" in log_summary, log_summary[:120])
        check("日志切片排除窗口外行", "order service boot" not in log_summary, log_summary[:120])

        # ---- 产物进待审池，不直接写缺陷 ----
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        bug_count = int(conn.execute("SELECT COUNT(*) FROM biz_bug").fetchone()[0])
        conn.close()
        check("探索异常未直接写缺陷库", bug_count == 0, str(bug_count))
        drafts = api.data("GET", f"{V1}/knowledge/drafts", )
        draft_list = drafts["list"]
        exp_drafts = [d for d in draft_list if d["sourceType"] == "exploratory"]
        check("异常产物进待审池", len(exp_drafts) >= 1, str(len(exp_drafts)))
        check("产物标记需人工审核", all(d.get("reviewRequired") for d in exp_drafts),
              str([d.get("reviewRequired") for d in exp_drafts]))
        first_draft = exp_drafts[0]
        check("产物引用探索步", first_draft["refType"] == "exploration_step" and first_draft["refId"],
              str((first_draft.get("refType"), first_draft.get("refId"))))

        # ---- 去重：同会话第二个相同异常被跳过 ----
        check("重复异常被去重跳过（只 1 条产物）", len(exp_drafts) == 1, str(len(exp_drafts)))

        # ---- 审核通过 → 转缺陷 + 证据挂载 ----
        review = api.data("POST", f"{V1}/knowledge/drafts/{first_draft['id']}/review",
                          {"action": "approve", "reviewer": "门禁", "comment": "确认是真缺陷"})
        bug_id = review.get("bugId")
        check("审核通过转出缺陷", bool(bug_id), str(review))
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        bug = conn.execute("SELECT * FROM biz_bug WHERE id=?", (int(bug_id),)).fetchone()
        check("缺陷已落库且 open", bug is not None and bug["status"] == "open", str(dict(bug) if bug else None))
        check("缺陷挂探索步证据", int(conn.execute(
            "SELECT COUNT(*) FROM biz_execution_evidence WHERE source_type='exploratory' AND bug_id=?",
            (int(bug_id),)).fetchone()[0]) >= 1)
        draft_row = conn.execute("SELECT review_status, bug_id FROM biz_knowledge_draft WHERE id=?",
                                 (int(first_draft["id"]),)).fetchone()
        check("待审条目标记已通过并登记 bug_id",
              draft_row["review_status"] == "approved" and int(draft_row["bug_id"] or 0) == int(bug_id),
              str(dict(draft_row)))
        conn.close()

        # 重复审核被拒
        api.expect("重复审核被拒", "POST",
                   f"{V1}/knowledge/drafts/{first_draft['id']}/review",
                   ErrorCode.ILLEGAL_OPERATION, {"action": "approve"})

        # ---- 无模型降级（driver=model 但无可用模型）----
        _original = getattr(es, "resolve_candidates")
        setattr(es, "resolve_candidates", lambda *a, **k: [])
        try:
            os.environ["AITP_MCP_ANOMALY_STEPS"] = ""
            model_plan = [{"action": "snapshot"}]
            msess = api.data("POST", f"{V1}/exploratory/sessions", {
                "projectId": project_id, "name": "模型驱动探索", "targetUrl": "http://stub.local/",
                "driver": "model", "plan": model_plan, "maxSteps": 1,
            })
            api.data("POST", f"{V1}/exploratory/sessions/{msess['id']}/run")
            mdetail = wait_session(api, msess["id"])
            mrow = mdetail["session"]
            check("无模型时降级完成探索", mrow["status"] == "done", mrow.get("errorMessage"))
            check("降级标记为真", bool(mrow["degraded"]), str(mrow.get("degraded")))
            check("降级说明已记录", "降级" in json.dumps(mrow.get("summary", {}), ensure_ascii=False)
                  or "降级" in json.dumps(mdetail.get("session", {}).get("summary", {}), ensure_ascii=False),
                  str(mrow.get("summary")))
        finally:
            setattr(es, "resolve_candidates", _original)
            os.environ.pop("AITP_MCP_ANOMALY_STEPS", None)
            os.environ.pop("AITP_MCP_ANOMALY_KIND", None)

        # ---- 跨项目隔离 ----
        other = int(api.data("POST", f"{V1}/projects", {"code": "EXP2", "name": "另一项目"})["id"])
        cross = api.expect("跨项目读会话按不存在处理", "GET",
                           f"{V1}/exploratory/sessions/{sess['id']}?projectId={other}",
                           ErrorCode.RESOURCE_NOT_FOUND)
        check("跨项目隔离返回 404", cross["code"] == ErrorCode.RESOURCE_NOT_FOUND, str(cross))

        # ---- 删除与非法删除 ----
        api.expect("探索进行中/已完成可删除", "DELETE", f"{V1}/exploratory/sessions/{msess['id']}",
                   ErrorCode.SUCCESS)
        # ---- 审计留痕 ----
        conn = sqlite3.connect(db_path)
        audits = int(conn.execute(
            "SELECT COUNT(*) FROM sys_audit_log WHERE resource_type='exploration_session'").fetchone()[0])
        conn.close()
        check("探索会话有审计留痕", audits >= 1, str(audits))

    finally:
        os.environ.pop("AITP_MCP_ANOMALY_STEPS", None)
        os.environ.pop("AITP_MCP_ANOMALY_KIND", None)
        os.environ.pop("AITP_MCP_MISSING_TOOL", None)
    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())