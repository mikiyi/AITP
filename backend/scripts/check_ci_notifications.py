"""CI 触发与 Webhook 通知验收脚本（GAP-10）。

在临时库与本地 HTTP 接收器上验证：
- 迁移 028：CI 令牌、通知渠道、发送记录三张表
- CI 令牌：生成一次即返回明文、库里只存哈希、重新生成使旧令牌失效、可吊销
- 令牌鉴权：缺少/错误令牌被拒；绑定项目后不能触发别的项目或用例
- CI 建轮次：按 caseIds 建轮次、返回轮次链接、未绑环境时明确提示
- CI 查询轮次：带通过率与自动化执行记录
- 通知渠道：全局/项目生效范围、事件与地址校验、启停、删除、测试发送
- 通知触发：轮次完成只推给全局 + 同项目渠道；载荷字段完整
- 通知失败：重试有上限、留发送记录、不影响轮次结果
- 发送记录接口返回事件筛选项（含测试通知）

    python backend/scripts/check_ci_notifications.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import socket
import sqlite3
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
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

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None, **kwargs: Any) -> JsonDict:
        _, body = self.call(method, path, payload, **kwargs)
        check(name, body.get("code") == code,
              f"code={body.get('code')} msg={body.get('message')}")
        return body


class Collector(BaseHTTPRequestHandler):
    """本地 Webhook 接收器：记录请求体，可切换返回 500。"""

    received: list[JsonDict] = []
    fail = False

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length)
        try:
            body = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            body = {"_raw": raw.decode("utf-8", errors="replace")}
        type(self).received.append({
            "path": self.path,
            "body": body,
            "contentType": self.headers.get("Content-Type"),
            "userAgent": self.headers.get("User-Agent"),
        })
        if type(self).fail:
            self.send_response(500)
            self.end_headers()
            self.wfile.write(b"boom")
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"ok")

    def log_message(self, *args: Any) -> None:
        pass


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "ci.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8766,
        debug=False,
        db_path=db_path,
        log_path=workdir / "ci.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def select_one(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> JsonDict:
    row = conn.execute(sql, params).fetchone()
    return dict(row) if row else {}


def wait_for(predicate: Any, timeout: float = 8.0, interval: float = 0.1) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return bool(predicate())


def create_case(api: Api, project_id: int, title: str, priority: str = "P0") -> JsonDict:
    return cast(JsonDict, api.data("POST", "/api/v1/test-cases", {
        "projectId": project_id,
        "title": title,
        "priority": priority,
        "steps": [{"action": "打开页面", "expected": "正常打开"}],
    }))


def create_channel(api: Api, name: str, url: str, events: list[str],
                   project_id: int | None) -> JsonDict:
    return cast(JsonDict, api.data("POST", "/api/v1/settings/notifications", {
        "name": name,
        "projectId": project_id,
        "webhookUrl": url,
        "events": events,
        "enabled": True,
    }))


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-ci-"))
    server = ThreadingHTTPServer(("127.0.0.1", 0), Collector)
    port = int(server.server_address[1])
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        app = build_app(workdir)
        api = Api(app)
        db_path = workdir / "ci.db"
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # ---------------- 迁移与表结构 ----------------
        check("迁移 028 已应用", 28 in applied_versions(conn))
        tables = {
            str(row["name"])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            ).fetchall()
        }
        check("CI 令牌表已建", "sys_ci_token" in tables)
        check("通知渠道表已建", "sys_notification_channel" in tables)
        check("通知发送记录表已建", "sys_notification_log" in tables)
        token_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sys_ci_token)")}
        check("令牌只落哈希与前缀",
              {"token_hash", "token_prefix", "project_id", "enabled"} <= token_columns,
              str(sorted(token_columns)))
        log_columns = {row["name"] for row in conn.execute("PRAGMA table_info(sys_notification_log)")}
        check("发送记录保留请求体与结果",
              {"payload_json", "status", "attempts", "http_status", "error_message"} <= log_columns,
              str(sorted(log_columns)))

        # ---------------- 项目与用例 ----------------
        project = api.data("POST", "/api/v1/projects", {"code": "CI-MAIN", "name": "CI 主项目"})
        project_id = int(project["id"])
        other = api.data("POST", "/api/v1/projects", {"code": "CI-OTHER", "name": "CI 其他项目"})
        other_project_id = int(other["id"])
        cases = [
            create_case(api, project_id, "登录成功", "P0"),
            create_case(api, project_id, "登录失败提示", "P1"),
        ]
        case_ids = [int(item["id"]) for item in cases]
        foreign_case = create_case(api, other_project_id, "别的项目用例", "P0")

        # ---------------- 令牌鉴权 ----------------
        api.expect("缺少 CI 令牌被拒绝", "POST", "/api/v1/ci/runs", ErrorCode.AUTH_REQUIRED, {
            "projectId": project_id, "caseIds": [case_ids[0]],
        })
        api.expect("错误 CI 令牌被拒绝", "POST", "/api/v1/ci/runs", ErrorCode.AUTH_REQUIRED, {
            "projectId": project_id, "caseIds": [case_ids[0]],
        }, headers={"X-AITP-CI-Token": "aitp_wrong"})

        # ---------------- 生成令牌 ----------------
        issued = api.data("POST", "/api/v1/settings/ci/token",
                          {"name": "流水线令牌", "projectId": project_id})
        token1 = str(issued.get("token") or "")
        check("生成令牌返回一次明文", token1.startswith("aitp_") and len(token1) > 20,
              str(issued)[:160])
        check("令牌前缀与状态一致",
              issued.get("tokenPrefix") == token1[:12] and issued.get("enabled") is True,
              str(issued)[:200])
        row = select_one(conn, "SELECT * FROM sys_ci_token WHERE enabled = 1 ORDER BY id DESC")
        check("库里保存哈希而非明文",
              row.get("token_hash") != token1 and len(str(row.get("token_hash"))) == 64,
              str(row)[:160])
        check("令牌绑定项目", int(row.get("project_id") or 0) == project_id, str(row)[:160])
        status = api.data("GET", "/api/v1/settings/ci")
        check("状态接口不回传明文令牌", "token" not in status, str(status)[:200])
        check("状态接口给出触发地址",
              str(status.get("endpoint") or "").endswith("/api/v1/ci/runs"),
              str(status)[:200])
        events = status.get("events") or []
        event_values = {item.get("value") for item in events}
        check("状态接口给出可订阅事件",
              {"run.completed", "gate.failed", "automation.failed", "backup.failed"} <= event_values
              and all(item.get("label") for item in events), str(status)[:240])
        audit_tokens = api.data("GET", "/api/v1/settings/audit-logs?resourceType=ci_token")
        check("生成令牌写审计", int(audit_tokens.get("total") or 0) >= 1, str(audit_tokens)[:200])

        ci_headers = {"X-AITP-CI-Token": token1}

        # ---------------- CI 建轮次 / 查轮次 ----------------
        created = api.data("POST", "/api/v1/ci/runs", {
            "name": "CI 冒烟轮次",
            "caseIds": case_ids,
            "owner": "pipeline",
        }, headers=ci_headers)
        run_id = int(created.get("runId") or 0)
        check("CI 令牌可创建轮次", run_id > 0 and created.get("total") == 2, str(created)[:240])
        check("返回轮次链接",
              str(created.get("url") or "").endswith(f"/test-runs/{run_id}"), str(created)[:240])
        check("未绑环境时给出警告",
              created.get("queued") is False
              and any("测试环境" in item for item in created.get("warnings") or []),
              str(created)[:300])
        detail = api.data("GET", f"/api/v1/ci/runs/{run_id}", headers=ci_headers)
        check("CI 可查询轮次进度",
              int(detail.get("runId") or 0) == run_id and detail.get("passRate") == 0.0,
              str(detail)[:240])
        check("轮次详情带自动化执行列表", detail.get("automationRuns") == [], str(detail)[:240])

        api.expect("绑定项目不能触发其他项目", "POST", "/api/v1/ci/runs",
                   ErrorCode.PARAM_ERROR, {
                       "projectId": other_project_id, "caseIds": [case_ids[0]],
                   }, headers=ci_headers)
        api.expect("绑定项目不能夹带其他项目用例", "POST", "/api/v1/ci/runs",
                   ErrorCode.PARAM_ERROR, {
                       "projectId": project_id, "caseIds": [int(foreign_case["id"])],
                   }, headers=ci_headers)
        api.expect("不存在的用例被拒绝", "POST", "/api/v1/ci/runs",
                   ErrorCode.PARAM_ERROR, {
                       "projectId": project_id, "caseIds": [999999],
                   }, headers=ci_headers)

        # 重新生成 → 旧令牌失效
        reissued = api.data("POST", "/api/v1/settings/ci/token",
                            {"name": "流水线令牌 v2", "projectId": project_id})
        token2 = str(reissued.get("token") or "")
        check("重新生成返回新令牌", bool(token2) and token2 != token1, token2[:16])
        api.expect("旧令牌立即失效", "GET", f"/api/v1/ci/runs/{run_id}",
                   ErrorCode.AUTH_REQUIRED, headers={"X-AITP-CI-Token": token1})
        ci_headers = {"X-AITP-CI-Token": token2}
        api.data("GET", f"/api/v1/ci/runs/{run_id}", headers=ci_headers)

        # ---------------- 通知渠道 ----------------
        collector_url = f"http://127.0.0.1:{port}/hook"
        global_channel = create_channel(
            api, "全局测试群", collector_url, ["run.completed", "backup.failed"], None
        )
        project_channel = create_channel(
            api, "主项目群", collector_url, ["run.completed"], project_id
        )
        other_channel = create_channel(
            api, "其他项目群", collector_url, ["run.completed"], other_project_id
        )
        check("全局渠道生效范围为全局", global_channel.get("projectId") is None,
              str(global_channel)[:200])
        check("项目渠道绑定项目",
              int(project_channel.get("projectId") or 0) == project_id,
              str(project_channel)[:200])
        check("渠道保存订阅事件",
              set(global_channel.get("events") or []) == {"run.completed", "backup.failed"},
              str(global_channel)[:200])

        api.expect("非法 Webhook 地址被拒绝", "POST", "/api/v1/settings/notifications",
                   ErrorCode.PARAM_ERROR, {
                       "name": "脏地址", "webhookUrl": "ftp://example.com/hook",
                       "events": ["run.completed"],
                   })
        api.expect("未知事件被拒绝", "POST", "/api/v1/settings/notifications",
                   ErrorCode.PARAM_ERROR, {
                       "name": "脏事件", "webhookUrl": collector_url,
                       "events": ["run.exploded"],
                   })
        api.expect("不存在的项目被拒绝", "POST", "/api/v1/settings/notifications",
                   ErrorCode.PARAM_ERROR, {
                       "name": "脏项目", "projectId": 999999, "webhookUrl": collector_url,
                       "events": ["run.completed"],
                   })

        updated = api.data("PUT", f"/api/v1/settings/notifications/{other_channel['id']}",
                           {"enabled": False})
        check("渠道可停用", updated.get("enabled") is False, str(updated)[:200])
        listing = api.data("GET", "/api/v1/settings/notifications")
        check("渠道列表可读", len(listing.get("list") or []) == 3, str(listing)[:240])
        check("渠道列表给出中文事件选项",
              all(item.get("label") for item in listing.get("eventOptions") or []),
              str(listing.get("eventOptions"))[:200])

        # 测试发送
        Collector.received.clear()
        tested = api.data("POST", f"/api/v1/settings/notifications/{global_channel['id']}/test")
        check("测试通知同步发送成功",
              tested.get("success") is True and int(tested.get("httpStatus") or 0) == 200,
              str(tested)[:200])
        check("接收器收到测试通知",
              wait_for(lambda: len(Collector.received) >= 1), str(Collector.received)[:300])
        test_payload = Collector.received[-1]["body"] if Collector.received else {}
        check("测试通知载荷字段完整",
              test_payload.get("event") == "test.ping"
              and test_payload.get("eventLabel") == "测试通知"
              and bool(test_payload.get("occurredAt"))
              and str(test_payload.get("link") or "").endswith("/settings/ci"),
              str(test_payload)[:300])
        test_log = select_one(
            conn,
            "SELECT * FROM sys_notification_log WHERE event = 'test.ping' ORDER BY id DESC",
        )
        check("测试发送写发送记录",
              test_log.get("status") == "success" and int(test_log.get("attempts") or 0) == 1,
              str(test_log)[:240])
        logs_api = api.data("GET", "/api/v1/settings/notification-logs?pageSize=100")
        check("发送记录接口返回事件筛选项",
              any(item.get("value") == "test.ping" for item in logs_api.get("eventOptions") or []),
              str(logs_api.get("eventOptions"))[:200])

        # ---------------- 轮次完成通知 ----------------
        Collector.received.clear()
        run2 = api.data("POST", "/api/v1/ci/runs", {
            "name": "CI 通知轮次",
            "caseIds": case_ids,
        }, headers=ci_headers)
        run2_id = int(run2.get("runId") or 0)
        api.data("POST", f"/api/v1/test-runs/{run2_id}/complete")
        check("轮次完成后异步投递通知",
              wait_for(lambda: len(Collector.received) >= 2),
              str(Collector.received)[:300])
        time.sleep(0.6)
        run_payloads = [
            item["body"] for item in Collector.received
            if item["body"].get("event") == "run.completed"
        ]
        check("只投递给全局与同项目渠道", len(run_payloads) == 2, str(
            [item.get("projectId") for item in run_payloads]))
        first = run_payloads[0] if run_payloads else {}
        raw_data = first.get("data")
        data = raw_data if isinstance(raw_data, dict) else {}
        check("轮次通知载荷带项目与链接",
              first.get("eventLabel") == "测试轮次完成"
              and int(first.get("projectId") or 0) == project_id
              and first.get("projectName") == "CI 主项目"
              and str(first.get("link") or "").endswith(f"/test-runs/{run2_id}"),
              str(first)[:360])
        check("轮次通知载荷字段完整",
              int(data.get("runId") or 0) == run2_id
              and int(data.get("total") or 0) == 2
              and "passRate" in data and "failed" in data and bool(data.get("finishedAt")),
              str(data)[:360])
        success_logs = conn.execute(
            "SELECT COUNT(*) FROM sys_notification_log"
            " WHERE event = 'run.completed' AND status = 'success'"
        ).fetchone()[0]
        check("成功投递写发送记录", int(success_logs) == 2, str(success_logs))
        task_rows = conn.execute(
            "SELECT max_attempts FROM sys_task WHERE kind = 'notification.webhook'"
            " ORDER BY id DESC LIMIT 2"
        ).fetchall()
        check("通知任务重试上限为 3 次",
              len(task_rows) == 2 and all(int(row["max_attempts"]) == 3 for row in task_rows),
              str([dict(row) for row in task_rows]))

        # ---------------- 通知失败：重试上限 + 不影响轮次 ----------------
        closed_port = socket.socket()
        closed_port.bind(("127.0.0.1", 0))
        bad_port = int(closed_port.getsockname()[1])
        closed_port.close()
        bad_channel = create_channel(
            api, "坏地址群", f"http://127.0.0.1:{bad_port}/hook", ["run.completed"], project_id
        )
        run3 = api.data("POST", "/api/v1/ci/runs", {
            "name": "CI 通知失败轮次",
            "caseIds": case_ids,
        }, headers=ci_headers)
        run3_id = int(run3.get("runId") or 0)
        api.data("POST", f"/api/v1/test-runs/{run3_id}/complete")

        def bad_log_done() -> bool:
            row = select_one(
                conn,
                "SELECT status, attempts FROM sys_notification_log"
                " WHERE channel_id = ? AND event = 'run.completed' ORDER BY id DESC",
                (int(bad_channel["id"]),),
            )
            return row.get("status") == "failed" and int(row.get("attempts") or 0) == 3

        check("失败投递按上限重试并落记录", wait_for(bad_log_done, timeout=10.0))
        run3_detail = api.data("GET", f"/api/v1/test-runs/{run3_id}")
        check("通知失败不影响轮次结果", run3_detail.get("status") == "completed",
              str(run3_detail)[:200])
        bad_log = select_one(
            conn,
            "SELECT * FROM sys_notification_log"
            " WHERE channel_id = ? AND event = 'run.completed' ORDER BY id DESC",
            (int(bad_channel["id"]),),
        )
        check("失败记录保留错误原因",
              bad_log.get("status") == "failed" and bool(bad_log.get("error_message")),
              str(bad_log)[:300])

        # ---------------- 删除与审计 ----------------
        api.data("DELETE", f"/api/v1/settings/notifications/{other_channel['id']}")
        remaining = api.data("GET", "/api/v1/settings/notifications")["list"]
        check("删除渠道后列表不再出现",
              all(int(item["id"]) != int(other_channel["id"]) for item in remaining),
              str(remaining)[:240])
        audit_notifications = api.data(
            "GET", "/api/v1/settings/audit-logs?resourceType=notification_channel&pageSize=100"
        )
        actions = {str(item.get("action")) for item in audit_notifications.get("list") or []}
        check("渠道变更写审计", {"create", "update", "delete"} <= actions, str(actions))

        revoked = api.data("DELETE", "/api/v1/settings/ci/token")
        check("令牌可吊销", revoked.get("enabled") is False, str(revoked)[:160])
        api.expect("吊销后令牌立即失效", "GET", f"/api/v1/ci/runs/{run_id}",
                   ErrorCode.AUTH_REQUIRED, headers=ci_headers)
        audit_revoked = api.data("GET", "/api/v1/settings/audit-logs?resourceType=ci_token")
        check("吊销令牌写审计",
              any(item.get("action") == "revoke"
                  for item in audit_revoked.get("list") or []),
              str(audit_revoked)[:240])

        conn.close()
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(workdir, ignore_errors=True)

    print("----------------------------------------")
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：" + "、".join(_failures))
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
