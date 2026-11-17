"""验证 A 批项目范围硬约束：片段、报告导出、场景运行归属（补充门禁）。

    python backend/scripts/check_scope_enforcement.py
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402

V1 = "/api/v1"
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

    def json(self, method: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
        body = self.client.open(path, method=method, json=payload).get_json(silent=True)
        return cast(dict[str, Any], body or {"code": -1, "message": "非 JSON", "data": None})

    def code(self, method: str, path: str, payload: dict[str, Any] | None = None) -> int:
        return int(self.json(method, path, payload).get("code") or -1)

    def data(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        body = self.json(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} 失败: {body.get('code')} {body.get('message')}")
        return body.get("data")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "scope.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8779, debug=False, db_path=db_path,
        log_path=workdir / "scope.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-scope-"))
    db_path = workdir / "scope.db"
    try:
        app = build_app(workdir)
        api = Api(app)
        pa = int(api.data("POST", f"{V1}/projects", {"code": "SCA", "name": "范围 A"})["id"])
        pb = int(api.data("POST", f"{V1}/projects", {"code": "SCB", "name": "范围 B"})["id"])

        report = api.data("POST", f"{V1}/reports", {"projectId": pa, "title": "范围报告"})
        rid = int(report["id"])
        fragment = api.data("POST", f"{V1}/reports/fragments", {
            "projectId": pa, "title": "范围片段", "content": "正文", "section": "risk",
        })
        fid = int(fragment["id"])

        # 片段：缺项目拒绝、跨项目 404、本项目可改
        check("片段更新缺 projectId 被拒绝",
              api.code("PUT", f"{V1}/reports/fragments/{fid}", {"content": "x"})
              == ErrorCode.PARAM_ERROR)
        check("片段更新跨项目按不存在处理",
              api.code("PUT", f"{V1}/reports/fragments/{fid}?projectId={pb}", {"content": "x"})
              == ErrorCode.RESOURCE_NOT_FOUND)
        check("片段更新本项目成功",
              api.code("PUT", f"{V1}/reports/fragments/{fid}?projectId={pa}", {"content": "新正文"})
              == ErrorCode.SUCCESS)
        check("片段提升为全局跨项目被拒绝",
              api.code("POST", f"{V1}/reports/fragments/{fid}/promote?projectId={pb}", {})
              == ErrorCode.RESOURCE_NOT_FOUND)

        # 报告导出：缺项目 400，跨项目 404
        check("报告导出缺 projectId 被拒绝",
              api.code("GET", f"{V1}/reports/{rid}/export?format=md") == ErrorCode.PARAM_ERROR)
        check("报告导出跨项目按不存在处理",
              api.code("GET", f"{V1}/reports/{rid}/export?format=md&projectId={pb}")
              == ErrorCode.RESOURCE_NOT_FOUND)

        # 场景运行归属：B 场景不能借 A 的运行记录
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        now = utcnow_iso()
        sid_a = int(conn.execute(
            "INSERT INTO biz_api_scenario (scenario_no, project_id, name, steps_json, status,"
            " created_at, updated_at) VALUES ('SC-A', ?, '场景 A', '[]', 'ready', ?, ?)",
            (pa, now, now),
        ).lastrowid or 0)
        sid_b = int(conn.execute(
            "INSERT INTO biz_api_scenario (scenario_no, project_id, name, steps_json, status,"
            " created_at, updated_at) VALUES ('SC-B', ?, '场景 B', '[]', 'ready', ?, ?)",
            (pb, now, now),
        ).lastrowid or 0)
        run_a = int(conn.execute(
            "INSERT INTO biz_api_scenario_run (run_no, scenario_id, project_id, passed, record_count,"
            " summary_json, duration_ms, created_at, updated_at)"
            " VALUES ('SR-A', ?, ?, 1, 1, ?, 1, ?, ?)",
            (sid_a, pa, json.dumps({"runs": [{"passed": True}]}), now, now),
        ).lastrowid or 0)
        conn.commit()
        conn.close()

        check("B 场景不能读取 A 的运行报告",
              api.code("GET", f"{V1}/api-scenarios/{sid_b}/runs/{run_a}/report?projectId={pb}")
              == ErrorCode.RESOURCE_NOT_FOUND)
        check("B 场景不能把 A 的运行转缺陷",
              api.code("POST", f"{V1}/api-scenarios/{sid_b}/runs/{run_a}/to-bug?projectId={pb}", {})
              == ErrorCode.RESOURCE_NOT_FOUND)
        own = api.client.get(f"{V1}/api-scenarios/{sid_a}/runs/{run_a}/report?projectId={pa}")
        check("A 场景正常读取自己的运行报告",
              own.status_code == 200 and own.mimetype == "text/html")
    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
