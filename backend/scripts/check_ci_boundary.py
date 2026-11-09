"""CI Token 访问边界收敛门禁（GAP-24）。

在临时库上验证收紧后的边界，绝不连正式库：
- 白名单：CI 令牌只能命中 CI 触发路由，越界路径（含 /api/v1/ci/ 下的非触发路由）被拒
- 项目范围：绑定项目的令牌不能触发别的项目
- 有效期：过期令牌立即失效；生成时可选有效期会写进库并在列表可见
- 吊销：一键吊销与单个吊销后，同一令牌立即失效
- 审计区分：CI 调用在审计里单独标记来源，越界拒绝也留痕
- 正常链路：带有效令牌的真实 CI 触发（建轮次 + 查轮次）仍然可用

    python backend/scripts/check_ci_boundary.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

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


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "boundary.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8771,
        debug=False,
        db_path=db_path,
        log_path=workdir / "boundary.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def create_case(api: Api, project_id: int, title: str) -> JsonDict:
    return cast(JsonDict, api.data("POST", "/api/v1/test-cases", {
        "projectId": project_id,
        "title": title,
        "priority": "P0",
        "steps": [{"action": "打开页面", "expected": "正常打开"}],
    }))


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-ci-boundary-"))
    try:
        app = build_app(workdir)
        api = Api(app)
        db_path = workdir / "boundary.db"
        conn = sqlite3.connect(db_path, check_same_thread=False)
        conn.row_factory = sqlite3.Row

        # ---------------- 迁移与新列 ----------------
        check("迁移 046 已应用", 46 in applied_versions(conn))
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(sys_ci_token)")}
        check("令牌表新增过期/吊销列", {"expires_at", "revoked_at"} <= cols, str(sorted(cols)))

        # ---------------- 数据准备 ----------------
        project = api.data("POST", "/api/v1/projects", {"code": "BD-MAIN", "name": "边界主项目"})
        project_id = int(project["id"])
        other = api.data("POST", "/api/v1/projects", {"code": "BD-OTHER", "name": "边界其他项目"})
        other_project_id = int(other["id"])
        case = create_case(api, project_id, "登录成功")
        case_id = int(case["id"])
        foreign_case = create_case(api, other_project_id, "别的项目用例")

        # ---------------- 生成令牌（可选有效期） ----------------
        issued = api.data("POST", "/api/v1/settings/ci/token", {
            "name": "边界令牌", "projectId": project_id, "expiresInDays": 30,
        })
        token = str(issued.get("token") or "")
        ci_headers = {"X-AITP-CI-Token": token}
        check("生成令牌返回一次明文", token.startswith("aitp_") and len(token) > 20, token[:16])
        check("状态接口回传到期时间与有效状态",
              bool(issued.get("expiresAt")) and issued.get("status") == "active"
              and issued.get("expired") is False, str(issued)[:200])
        row = dict(conn.execute(
            "SELECT * FROM sys_ci_token WHERE enabled = 1 ORDER BY id DESC"
        ).fetchone())
        check("有效期写入库（expires_at 非空）", bool(row.get("expires_at")), str(row)[:160])

        tokens = api.data("GET", "/api/v1/settings/ci/tokens")
        listing = tokens.get("list") or []
        check("令牌列表能看过期/吊销状态",
              bool(listing) and bool(listing[0].get("expiresAt")) and "revoked" in listing[0],
              str(listing)[:220])
        check("令牌列表不回传明文或哈希",
              all("tokenHash" not in item for item in listing), str(listing)[:160])

        # ---------------- 正常 CI 触发链路可用 ----------------
        created = api.data("POST", "/api/v1/ci/runs", {
            "name": "边界冒烟", "caseIds": [case_id],
        }, headers=ci_headers)
        run_id = int(created.get("runId") or 0)
        check("有效令牌可创建轮次（白名单命中）", run_id > 0 and created.get("total") == 1,
              str(created)[:220])
        detail = api.data("GET", f"/api/v1/ci/runs/{run_id}", headers=ci_headers)
        check("有效令牌可查询轮次（白名单命中）",
              int(detail.get("runId") or 0) == run_id, str(detail)[:200])

        # ---------------- 项目范围 ----------------
        api.expect("绑定项目不能触发其他项目", "POST", "/api/v1/ci/runs",
                   ErrorCode.PARAM_ERROR, {
                       "projectId": other_project_id, "caseIds": [case_id],
                   }, headers=ci_headers)
        api.expect("绑定项目不能夹带别的项目用例", "POST", "/api/v1/ci/runs",
                   ErrorCode.PARAM_ERROR, {
                       "projectId": project_id, "caseIds": [int(foreign_case["id"])],
                   }, headers=ci_headers)

        # ---------------- 白名单：越界路径被拒 ----------------
        api.expect("有效令牌访问 /ci/ 下非触发路由被拒（越界）", "GET", "/api/v1/ci/whatever",
                   ErrorCode.ILLEGAL_OPERATION, headers=ci_headers)
        api.expect("有效令牌用错误方法命中 runs 被拒", "POST", f"/api/v1/ci/runs/{run_id}",
                   ErrorCode.ILLEGAL_OPERATION, headers=ci_headers)

        # ---------------- 审计区分 ----------------
        audit_ci = api.data("GET", "/api/v1/settings/audit-logs?resourceType=ci_token&pageSize=100")
        check("CI 越界拒绝写审计并标记来源 ci",
              any(str(item.get("actor", "")).startswith("ci:")
                  and (item.get("detail") or {}).get("source") == "ci"
                  and (item.get("detail") or {}).get("outcome") == "denied"
                  for item in audit_ci.get("list") or []),
              str(audit_ci.get("list"))[:280])
        audit_runs = api.data(
            "GET", "/api/v1/settings/audit-logs?resourceType=test_run&pageSize=100"
        )
        check("CI 建轮次审计标记来源 ci",
              any((item.get("detail") or {}).get("source") == "ci"
                  for item in audit_runs.get("list") or []),
              str(audit_runs.get("list"))[:280])

        # ---------------- 过期后失效 ----------------
        conn.execute(
            "UPDATE sys_ci_token SET expires_at = '2000-01-01T00:00:00.000Z'"
            " WHERE id = ?", (int(row["id"]),),
        )
        conn.commit()
        api.expect("过期令牌立即失效", "GET", f"/api/v1/ci/runs/{run_id}",
                   ErrorCode.AUTH_REQUIRED, headers=ci_headers)
        check("过期令牌在列表标记为 expired",
              (api.data("GET", "/api/v1/settings/ci/tokens").get("list") or [{}])[0].get("status")
              == "expired")

        # 恢复有效期，验证吊销链路
        conn.execute("UPDATE sys_ci_token SET expires_at = NULL WHERE id = ?", (int(row["id"]),))
        conn.commit()
        api.data("GET", f"/api/v1/ci/runs/{run_id}", headers=ci_headers)  # 重新可用

        # ---------------- 吊销后失效（单个） ----------------
        api.data("POST", f"/api/v1/settings/ci/token/{int(row['id'])}/revoke")
        check("单个吊销写入 revoked_at",
              conn.execute("SELECT revoked_at FROM sys_ci_token WHERE id = ?",
                           (int(row["id"]),)).fetchone()[0] is not None)
        api.expect("吊销后同一令牌立即失效", "GET", f"/api/v1/ci/runs/{run_id}",
                   ErrorCode.AUTH_REQUIRED, headers=ci_headers)

        # ---------------- 一键吊销后失效 + 旧数据兼容 ----------------
        reissued = api.data("POST", "/api/v1/settings/ci/token", {"name": "全量令牌"})
        token2 = str(reissued.get("token") or "")
        check("不带有效期时状态为有效（旧令牌兼容=不过期）",
              reissued.get("expiresAt") is None and reissued.get("status") == "active",
              str(reissued)[:160])
        ci_headers2 = {"X-AITP-CI-Token": token2}
        api.data("POST", "/api/v1/ci/runs",
                 {"projectId": project_id, "caseIds": [case_id]}, headers=ci_headers2)
        api.data("DELETE", "/api/v1/settings/ci/token")
        api.expect("一键吊销后令牌立即失效", "POST", "/api/v1/ci/runs",
                   ErrorCode.AUTH_REQUIRED,
                   {"projectId": project_id, "caseIds": [case_id]}, headers=ci_headers2)

        conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("----------------------------------------")
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：" + "、".join(_failures))
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
