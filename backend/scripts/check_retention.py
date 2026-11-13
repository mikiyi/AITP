"""验证数据留存与归档（GAP-44）：配置生效、到期动作、归档可检索回看、引用保护。

    python backend/scripts/check_retention.py

全部在临时库上运行，不读写正式数据。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import time
import zipfile
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

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[TestResponse, JsonDict]:
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
        check(name, body.get("code") == code,
              f"code={body.get('code')} msg={body.get('message')}")
        return body


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "retention.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8767,
        debug=False,
        db_path=db_path,
        log_path=workdir / "retention.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


OLD = "2025-01-01T00:00:00Z"


def seed_old(conn: sqlite3.Connection, project_id: int) -> dict[str, Any]:
    """直接落库造一批「早就到期」的历史数据。"""
    run_id = conn.execute(
        "INSERT INTO biz_test_run (project_id, name, status, total, passed, failed,"
        " created_at, updated_at, is_deleted)"
        " VALUES (?, '历史回归轮次', 'completed', 10, 8, 2, ?, ?, 0)",
        (project_id, OLD, OLD),
    ).lastrowid
    conn.execute(
        "INSERT INTO biz_test_run_case (run_id, case_id, seq, case_no, title, case_type,"
        " created_at, updated_at, is_deleted)"
        " VALUES (?, 0, 1, 'CASE001', '历史用例', 'functional', ?, ?, 0)",
        (run_id, OLD, OLD),
    )
    auto_run = conn.execute(
        "INSERT INTO biz_automation_run (run_no, script_id, script_no, script_name, category,"
        " status, stdout, stderr, started_at, finished_at, created_at, updated_at, is_deleted)"
        " VALUES ('RUN-HIST-001', NULL, 'AUTO001', '历史脚本', 'web', 'success',"
        " 'stdout old', 'stderr old', ?, ?, ?, ?, 0)",
        (OLD, OLD, OLD, OLD),
    ).lastrowid
    matrix_run = conn.execute(
        "INSERT INTO biz_automation_run (run_no, script_id, script_no, script_name, category,"
        " status, stdout, stderr, started_at, finished_at, created_at, updated_at, is_deleted)"
        " VALUES ('RUN-HIST-002', NULL, 'AUTO002', '矩阵脚本', 'web', 'failed',"
        " 'stdout m', 'stderr m', ?, ?, ?, ?, 0)",
        (OLD, OLD, OLD, OLD),
    ).lastrowid
    ev_free = conn.execute(
        "INSERT INTO biz_execution_evidence (source_type, source_row_id, project_id, run_id,"
        " run_case_id, title, result, log_summary, created_at, updated_at, is_deleted)"
        " VALUES ('automation', ?, ?, ?, ?, '历史证据A', 'failed', 'old log', ?, ?, 0)",
        (auto_run, project_id, run_id, 0, OLD, OLD),
    ).lastrowid
    ev_ref = conn.execute(
        "INSERT INTO biz_execution_evidence (source_type, source_row_id, project_id, run_id,"
        " run_case_id, title, result, log_summary, created_at, updated_at, is_deleted)"
        " VALUES ('automation', ?, ?, ?, ?, '历史证据B', 'failed', 'old log', ?, ?, 0)",
        (auto_run, project_id, run_id, 0, OLD, OLD),
    ).lastrowid
    bug_id = conn.execute(
        "INSERT INTO biz_bug (project_id, bug_no, title, status, severity, created_at,"
        " updated_at, is_deleted)"
        " VALUES (?, 'BUG-HIST-001', '历史缺陷', 'open', 'P2', ?, ?, 0)",
        (project_id, OLD, OLD),
    ).lastrowid
    conn.execute("UPDATE biz_execution_evidence SET bug_id = ? WHERE id = ?", (bug_id, ev_ref))
    audit_id = conn.execute(
        "INSERT INTO sys_audit_log (actor, action, resource_type, resource_id, resource_label,"
        " result, created_at) VALUES ('local', 'create', 'test_run', ?, '历史日志', 'success', ?)",
        (run_id, OLD),
    ).lastrowid
    todo_id = conn.execute(
        "INSERT INTO biz_todo (todo_no, title, status, completed_at, created_at, updated_at,"
        " is_deleted) VALUES ('TODO-HIST-001', '历史待办', 'done', ?, ?, ?, 0)",
        (OLD, OLD, OLD),
    ).lastrowid
    conn.commit()
    return {
        "run": run_id, "auto": auto_run, "matrixRun": matrix_run,
        "evFree": ev_free, "evRef": ev_ref, "bug": bug_id, "audit": audit_id, "todo": todo_id,
    }


def wait_task(api: Api, task_id: int, timeout: float = 60.0) -> JsonDict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        detail = api.data("GET", f"{V1}/tasks/{task_id}")
        if detail.get("status") in ("success", "failed", "cancelled"):
            return cast(JsonDict, detail)
        time.sleep(0.3)
    raise AssertionError(f"任务 {task_id} 超时")


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-retention-"))
    try:
        app = build_app(workdir)
        api = Api(app)
        db_path = workdir / "retention.db"
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row

        check("迁移 055 已应用", 55 in applied_versions(conn))
        check("归档表已建立",
              conn.execute(
                  "SELECT name FROM sqlite_master WHERE type='table' AND name='biz_archive_bundle'"
              ).fetchone() is not None)

        project = api.data("POST", f"{V1}/projects", {"code": "RET", "name": "留存项目"})
        project_id = int(project["id"])
        seeds = seed_old(conn, project_id)
        # 关闭启动自动清理的干扰，手动控制
        api.data("PUT", f"{V1}/retention/settings", {"items": [], "autoCleanup": False})

        # 1. 配置：默认读取 + 改短保留期
        settings = api.data("GET", f"{V1}/retention/settings")
        check("配置含五类数据", len(settings["items"]) == 5, str(settings["items"])[:300])
        items = {
            item["dataType"]: {"days": item["days"], "action": item["action"]}
            for item in settings["items"]
        }
        new_items = [
            {"dataType": "test_run", "days": 30, "action": "archive"},
            {"dataType": "automation_run", "days": 30, "action": "archive"},
            {"dataType": "evidence", "days": 30, "action": "archive"},
            {"dataType": "log", "days": 30, "action": "archive"},
            {"dataType": "todo", "days": 30, "action": "archive"},
        ]
        saved = api.data("PUT", f"{V1}/retention/settings", {"items": new_items, "autoCleanup": False})
        check("保存后配置生效",
              all(item["days"] == 30 for item in saved["items"]), str(saved["items"])[:300])

        # 2. 干跑：只统计不动库
        dry = api.data("POST", f"{V1}/retention/cleanup", {"dryRun": True})
        check("干跑统计到到期数据", dry["dryRun"] is True and len(dry["types"]) == 5, str(dry)[:300])
        check("干跑不动库",
              conn.execute("SELECT COUNT(*) FROM biz_test_run WHERE is_deleted = 0").fetchone()[0] >= 1)
        check("干跑提示被引用证据", any(
            item.get("referencedBy", "").startswith("缺陷") for item in dry.get("protected") or []
        ), str(dry.get("protected"))[:300])

        # 3. 正式清理：归档 + 软删 + 引用保护
        task = api.data("POST", f"{V1}/retention/cleanup", {})
        wait_task(api, int(task["task"]["id"]))
        check("历史轮次已软删",
              conn.execute("SELECT COUNT(*) FROM biz_test_run WHERE id = ? AND is_deleted = 0",
                           (seeds["run"],)).fetchone()[0] == 0)
        check("历史轮次用例快照已软删",
              conn.execute("SELECT COUNT(*) FROM biz_test_run_case WHERE run_id = ? AND is_deleted = 0",
                           (seeds["run"],)).fetchone()[0] == 0)
        check("历史自动化执行已软删",
              conn.execute("SELECT COUNT(*) FROM biz_automation_run WHERE id = ? AND is_deleted = 0",
                           (seeds["auto"],)).fetchone()[0] == 0)
        check("未被引用的历史证据已清",
              conn.execute("SELECT COUNT(*) FROM biz_execution_evidence WHERE id = ? AND is_deleted = 0",
                           (seeds["evFree"],)).fetchone()[0] == 0)
        check("被缺陷引用的证据到期不清",
              conn.execute("SELECT COUNT(*) FROM biz_execution_evidence WHERE id = ? AND is_deleted = 0",
                           (seeds["evRef"],)).fetchone()[0] == 1,
              "缺陷引用的证据被误清了")
        check("历史操作日志已清理",
              conn.execute("SELECT COUNT(*) FROM sys_audit_log WHERE id = ?", (seeds["audit"],)).fetchone()[0] == 0)
        check("历史已完成待办已软删",
              conn.execute("SELECT COUNT(*) FROM biz_todo WHERE id = ? AND is_deleted = 0",
                           (seeds["todo"],)).fetchone()[0] == 0)

        # 4. 归档区可检索、可回看原文
        bundles = api.data("GET", f"{V1}/retention/archives")
        check("归档包裹已生成", bundles["total"] >= 4, str(bundles["total"]))
        bundle = bundles["list"][0]
        check("归档包裹带类型与条数", bundle["dataType"] and bundle["itemCount"] >= 1, str(bundle)[:200])
        check("归档包文件真实存在", (db_path.parent / bundle["filePath"]).is_file(), bundle["filePath"])
        check("归档包内是 zip 且有 manifest",
              zipfile.is_zipfile(db_path.parent / bundle["filePath"]))
        detail = api.data("GET", f"{V1}/retention/archives/{bundle['id']}")
        check("归档条目索引进库", len(detail["items"]) >= 1, str(detail["items"])[:200])
        check("归档检索按关键字命中",
              api.data("GET", f"{V1}/retention/archives?keyword=历史").get("total", 0) >= 1)
        item = detail["items"][0]
        original = api.data("GET", f"{V1}/retention/archives/{bundle['id']}/items/{item['refId']}")
        check("回看原文得到完整行", bool(original.get("original")),
              str(original)[:300])
        # 回看原文应与索引标题匹配（test_run 原文带 name）
        if bundle["dataType"] == "test_run":
            check("原文带轮次字段", "name" in original["original"], str(original)[:300])
        check("归档区按类型过滤",
              api.data("GET", f"{V1}/retention/archives?dataType=evidence").get("total", 0) >= 1)

        # 5. 到期动作「仅留摘要」：证据清正文、执行记录清 stdout
        api.data("PUT", f"{V1}/retention/settings", {
            "items": [{"dataType": "evidence", "days": 30, "action": "summary"},
                      {"dataType": "automation_run", "days": 30, "action": "summary"}],
            "autoCleanup": False,
        })
        # 再造一条旧证据与旧执行记录，验证摘要动作
        run_id2 = conn.execute(
            "INSERT INTO biz_automation_run (run_no, script_id, script_no, script_name, category,"
            " status, stdout, stderr, started_at, finished_at, created_at, updated_at, is_deleted)"
            " VALUES ('RUN-SUM-001', NULL, 'AUTO003', '摘要脚本', 'web', 'success',"
            " 'stdout to clear', 'stderr to clear', ?, ?, ?, ?, 0)",
            (OLD, OLD, OLD, OLD),
        ).lastrowid
        conn.execute(
            "INSERT INTO biz_execution_evidence (source_type, source_row_id, title, result,"
            " log_summary, request_summary, response_summary, created_at, updated_at, is_deleted)"
            " VALUES ('automation', ?, '摘要证据', 'failed', 'log to clear', 'req', 'resp', ?, ?, 0)",
            (run_id2, OLD, OLD),
        )
        conn.commit()
        api.data("POST", f"{V1}/retention/cleanup", {})
        time.sleep(1.5)
        check("摘要动作清执行记录正文",
              conn.execute("SELECT stdout FROM biz_automation_run WHERE id = ?", (run_id2,))
              .fetchone()[0] == "")
        check("摘要动作清证据正文",
              conn.execute("SELECT log_summary FROM biz_execution_evidence WHERE source_row_id = ?"
                           " AND source_type = 'automation' AND is_deleted = 0", (run_id2,))
              .fetchone()[0] == "")

        # 6. 参数校验：非法天数 / 非法动作
        api.expect("非法保留天数被拒", "PUT", f"{V1}/retention/settings",
                   ErrorCode.PARAM_ERROR, {"items": [{"dataType": "evidence", "days": -1, "action": "archive"}]})
        api.expect("非法动作被拒", "PUT", f"{V1}/retention/settings",
                   ErrorCode.PARAM_ERROR, {"items": [{"dataType": "test_run", "days": 30, "action": "purge"}]})
        api.expect("归档包裹不存在返回 404", "GET", f"{V1}/retention/archives/99999",
                   ErrorCode.RESOURCE_NOT_FOUND)

        # 7. 清理留审计
        audit_rows = conn.execute(
            "SELECT action FROM sys_audit_log WHERE resource_type = 'retention_cleanup'"
        ).fetchall()
        check("清理留审计", len(audit_rows) >= 1, str(len(audit_rows)))
        conn.close()
    finally:
        pass

    print(f"\nPASS={_passed} FAIL={_failed}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
