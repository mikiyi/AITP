"""项目级资产包门禁（三期 GAP-41）。

  - 导出结构包：模块/版本/需求/用例/缺陷/知识/脚本/断言规则，含 manifest；
  - 包内不含密钥字段、不含执行历史；
  - 同编码项目默认拒绝，改名导入后 id 全部重分配、外键正确重映射；
  - 预览给出计数与冲突，导入留审计。

    python backend/scripts/check_project_package.py
"""

from __future__ import annotations

import io
import json
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402

V1 = "/api/v1"
SECRET_RE = re.compile(r"(password|passwd|secret|token|api[_-]?key|credential)", re.I)
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


def build_app(workdir: pathlib.Path) -> Any:
    db_path = workdir / "package.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "package.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def seed(db_path: pathlib.Path, project_id: int) -> dict[str, int]:
    now = "2026-11-11T00:00:00Z"
    conn = sqlite3.connect(db_path)
    try:
        version = conn.execute(
            "INSERT INTO biz_project_version (project_id, version_code, name, description,"
            " status, sort_order, created_at, updated_at, is_deleted)"
            " VALUES (?, 'V1', '首版', '', 'released', 0, ?, ?, 0)",
            (project_id, now, now),
        ).lastrowid
        root = conn.execute(
            "INSERT INTO biz_module (project_id, parent_id, node_type, name, code, description,"
            " sort_order, status, created_at, updated_at, is_deleted)"
            " VALUES (?, NULL, 'module', '支付', 'PAY', '', 0, 'active', ?, ?, 0)",
            (project_id, now, now),
        ).lastrowid
        child = conn.execute(
            "INSERT INTO biz_module (project_id, parent_id, node_type, name, code, description,"
            " sort_order, status, created_at, updated_at, is_deleted)"
            " VALUES (?, ?, 'module', '回调', 'PAY-CB', '', 1, 'active', ?, ?, 0)",
            (project_id, root, now, now),
        ).lastrowid
        req = conn.execute(
            "INSERT INTO biz_requirement (project_id, req_no, title, status, priority,"
            " created_at, updated_at, is_deleted, version_id, module_id)"
            " VALUES (?, 'REQ001', '支付回调幂等', 'approved', 'P1', ?, ?, 0, ?, ?)",
            (project_id, now, now, version, child),
        ).lastrowid
        case = conn.execute(
            "INSERT INTO biz_test_case (project_id, requirement_id, case_no, title,"
            " created_at, updated_at, is_deleted, module_id, version_id)"
            " VALUES (?, ?, 'CASE001', '重复回调只入账一次', ?, ?, 0, ?, ?)",
            (project_id, req, now, now, child, version),
        ).lastrowid
        conn.execute(
            "INSERT INTO biz_bug (project_id, bug_no, title, created_at, updated_at,"
            " is_deleted, module_id, version_id)"
            " VALUES (?, 'BUG001', '重复入账', ?, ?, 0, ?, ?)",
            (project_id, now, now, child, version),
        )
        conn.execute(
            "INSERT INTO biz_knowledge_article (title, content, project_id, created_at,"
            " updated_at, is_deleted, module_id, review_status, status)"
            " VALUES ('回调幂等约定', '按订单号做幂等', ?, ?, ?, 0, ?, 'published', 'published')",
            (project_id, now, now, child),
        )
        conn.execute(
            "INSERT INTO biz_automation_script (script_no, name, project_id, module_id,"
            " version_id, created_at, updated_at, is_deleted, framework, language, file_name, content)"
            " VALUES ('AUTO001', '回调回归', ?, ?, ?, ?, ?, 0, 'pytest', 'python',"
            " 'test_callback.py', 'def test(): pass')",
            (project_id, child, version, now, now),
        )
        conn.execute(
            "INSERT INTO biz_assertion_rule (project_id, module_id, rule_no, name, rule,"
            " created_at, updated_at, is_deleted)"
            " VALUES (?, ?, 'RULE001', '金额只加一次', 'amount == 1', ?, ?, 0)",
            (project_id, child, now, now),
        )
        conn.commit()
        return {"version": int(version or 0), "root": int(root or 0), "child": int(child or 0),
                "req": int(req or 0), "case": int(case or 0)}
    finally:
        conn.close()


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-package-"))
    try:
        app = build_app(workdir)
        db_path = workdir / "package.db"
        client = app.test_client()
        project = client.post(f"{V1}/projects", json={"code": "PKG", "name": "资产包源项目"}).get_json()
        project_id = int(project["data"]["id"])
        ids = seed(db_path, project_id)

        exported = client.post(f"{V1}/project-packages/export", json={"projectId": project_id})
        check("导出返回 zip", exported.status_code == 200
              and exported.data[:2] == b"PK", str(exported.data[:20]))
        archive = zipfile.ZipFile(io.BytesIO(exported.data))
        names = set(archive.namelist())
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
        check("manifest 标记结构包", manifest["format"] == "aitp-project-package"
              and manifest["scope"]["history"] is False)
        expected = {"biz_project_version": 1, "biz_module": 2, "biz_requirement": 1,
                    "biz_test_case": 1, "biz_bug": 1, "biz_knowledge_article": 1,
                    "biz_automation_script": 1, "biz_assertion_rule": 1}
        check("资产计数完整", manifest["counts"] == expected, str(manifest["counts"]))
        check("数据文件齐全", all(f"data/{table}.json" in names for table in expected), str(names))

        secret_keys: list[str] = []
        for table in expected:
            rows = json.loads(archive.read(f"data/{table}.json").decode("utf-8"))
            for row in rows:
                secret_keys.extend(key for key in row if SECRET_RE.search(key))
        check("包内无密钥字段", not secret_keys, str(secret_keys))
        check("包内无 .env", not any(".env" in name for name in names), str(names))

        preview = client.post(f"{V1}/project-packages/preview",
                              data={"file": (io.BytesIO(exported.data), "pkg.zip")},
                              content_type="multipart/form-data").get_json()
        check("预览给出计数与冲突", preview["code"] == ErrorCode.SUCCESS
              and preview["data"]["conflict"] is True
              and preview["data"]["counts"] == expected, str(preview)[:200])

        skip = client.post(f"{V1}/project-packages/import",
                           data={"conflict": "skip", "file": (io.BytesIO(exported.data), "pkg.zip")},
                           content_type="multipart/form-data").get_json()
        check("同编码默认拒绝", skip["code"] == ErrorCode.PARAM_ERROR, str(skip)[:160])

        imported = client.post(f"{V1}/project-packages/import",
                               data={"conflict": "rename", "file": (io.BytesIO(exported.data), "pkg.zip")},
                               content_type="multipart/form-data").get_json()
        check("改名导入成功", imported["code"] == ErrorCode.SUCCESS
              and imported["data"]["code"] != "PKG", str(imported)[:200])
        new_id = int(imported["data"]["projectId"])

        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            modules = {row["code"]: int(row["id"]) for row in conn.execute(
                "SELECT id, code FROM biz_module WHERE project_id = ? AND is_deleted = 0", (new_id,))}
            parent = conn.execute(
                "SELECT parent_id FROM biz_module WHERE code = 'PAY-CB' AND project_id = ?",
                (new_id,)).fetchone()[0]
            check("模块父子关系重映射", int(parent) == modules["PAY"], f"{parent} vs {modules}")

            req = conn.execute(
                "SELECT id, module_id, version_id FROM biz_requirement"
                " WHERE project_id = ? AND is_deleted = 0", (new_id,)).fetchone()
            case = conn.execute(
                "SELECT requirement_id, module_id, version_id FROM biz_test_case"
                " WHERE project_id = ? AND is_deleted = 0", (new_id,)).fetchone()
            version_id = conn.execute(
                "SELECT id FROM biz_project_version WHERE project_id = ?", (new_id,)).fetchone()[0]
            check("用例→需求/模块/版本外键重映射",
                  int(case["requirement_id"]) == int(req["id"])
                  and int(case["module_id"]) == modules["PAY-CB"]
                  and int(case["version_id"]) == int(version_id), str(dict(case)))
            script = conn.execute(
                "SELECT env_id, version_id FROM biz_automation_script WHERE project_id = ?",
                (new_id,)).fetchone()
            check("自动化脚本不携带环境绑定", script["env_id"] is None
                  and int(script["version_id"]) == int(version_id), str(dict(script)))
            knowledge = conn.execute(
                "SELECT project_id, module_id FROM biz_knowledge_article WHERE project_id = ?",
                (new_id,)).fetchone()
            check("知识归属新项目", int(knowledge["project_id"]) == new_id
                  and int(knowledge["module_id"]) == modules["PAY-CB"], str(dict(knowledge)))
            audit_count = conn.execute(
                "SELECT COUNT(*) FROM sys_audit_log WHERE resource_type = 'project'"
                " AND resource_id = ? AND action = 'create'", (new_id,)).fetchone()[0]
            check("导入留审计", int(audit_count) >= 1, str(audit_count))
        finally:
            conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"项目资产包：PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
