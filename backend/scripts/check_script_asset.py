"""P1 #7/#8 验收门禁：脚本版本快照/差异/评审 + 元素库 + 探索转回放脚本。

    python backend/scripts/check_script_asset.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
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
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()
        self.scope_project_id: int | None = None

    def _scoped(self, path: str) -> str:
        if not self.scope_project_id or "projectId=" in path:
            return path
        bare = path.split("?")[0]
        if not any(part.isdigit() for part in bare.strip("/").split("/")):
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}projectId={self.scope_project_id}"

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(self._scoped(path), method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} code={body.get('code')} msg={body.get('message')}")
        result = body.get("data")
        if (method == "POST" and not self.scope_project_id
                and path.split("?")[0].rstrip("/").endswith("/projects")):
            if isinstance(result, dict) and result.get("id"):
                self.scope_project_id = int(result["id"])
        return result


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_asset.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "check.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_asset_"))
    try:
        app = build_app(workdir)
        api = Api(app)
        pid = api.data("POST", "/api/v1/projects", {"name": "资产门禁", "code": "GATE-AST"})["id"]

        scn = api.data("POST", "/api/v1/automation/scripts",
                       {"projectId": pid, "name": "版本脚本", "category": "web", "content": "print(1)\n"})
        sid = scn["id"]
        api.data("PUT", f"/api/v1/automation/scripts/{sid}", {"content": "print(2)\n", "changeNote": "改输出"})
        vers = api.data("GET", f"/api/v1/automation/scripts/{sid}/versions")["list"]
        check("内容变更自动快照旧版", len(vers) == 1 and vers[0]["changeNote"] == "改输出", str(vers))
        vid = vers[0]["id"]
        diff = api.data("GET", f"/api/v1/automation/scripts/{sid}/diff?base={vid}&target=current")["diff"]
        check("差异含旧/新行", any("-print(1)" in l for l in diff) and any("+print(2)" in l for l in diff), str(diff))
        rv = api.data("POST", f"/api/v1/automation/scripts/{sid}/versions/{vid}/review", {"status": "approved"})
        check("评审通过", rv["reviewStatus"] == "approved", str(rv))

        el = api.data("POST", "/api/v1/automation/ui-elements",
                      {"projectId": pid, "page": "登录", "name": "用户名", "selector": "#user", "kind": "css"})
        check("元素创建", el["selector"] == "#user", str(el))
        els = api.data("GET", f"/api/v1/automation/ui-elements?projectId={pid}")["list"]
        check("元素列表", len(els) == 1, str(len(els)))
        api.data("PUT", f"/api/v1/automation/ui-elements/{el['id']}", {"selector": "#username"})
        els = api.data("GET", f"/api/v1/automation/ui-elements?projectId={pid}")["list"]
        check("元素更新", els[0]["selector"] == "#username", str(els))

        sess = api.data("POST", "/api/v1/exploratory/sessions",
                        {"projectId": pid, "name": "转脚本会话", "targetUrl": "http://x.local/", "plan": []})
        ts = api.data("POST", f"/api/v1/exploratory/sessions/{sess['id']}/to-script", {})
        check("探索转回放脚本", str(ts["scriptNo"]).startswith("AUTO-W"), str(ts))
        detail = api.data("GET", f"/api/v1/automation/scripts/{ts['scriptId']}")
        check("回放脚本含 playwright", "sync_playwright" in detail["content"], detail["content"][:120])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())