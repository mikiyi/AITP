"""用例多行步骤/预期 + 测试数据多格式验收（BUG-44/45 回归门禁）。

在临时库上验证：
- 测试数据解析：内联文本/JSON、txt/csv/xlsx/json/sql 文件、URL 拒绝、未知格式拒绝
- 预览 API 出参（kind/rowCount/columns/sampleRows）
- 多行步骤与预期按行配对：API 落库往返 + xlsx 导入配对
- Python 自动化执行消费测试数据（AITP_TEST_DATA / _FILE / _JSON）
- JMeter 命令把 CSVDataSet 指向解析后的 testdata.csv（或 -J 属性）

    python backend/scripts/check_case_test_data.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import json
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
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.repositories.automation_repo import AutomationScriptRepository  # noqa: E402
from aitp.services import automation_service, case_test_data, xlsx_importer  # noqa: E402
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


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_case_test_data.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def check_resolver(workdir: pathlib.Path) -> None:
    inline = case_test_data.resolve("u1,p1\nu2,p2")
    check("内联文本按行解析", inline["kind"] == "inline-text" and inline["rowCount"] == 2,
          str(inline)[:200])
    inline_json = case_test_data.resolve('[{"user":"u1","pwd":"p1"},{"user":"u2","pwd":"p2"}]')
    check("内联 JSON 解析出记录集",
          inline_json["rowCount"] == 2 and inline_json["columns"] == ["user", "pwd"],
          str(inline_json)[:200])
    check("空值返回 empty 不报错", case_test_data.resolve("")["kind"] == "empty")

    txt = workdir / "data.txt"
    txt.write_text("user,pwd\nu1,p1\nu2,p2\n", encoding="utf-8")
    d = case_test_data.resolve(str(txt))
    check("txt 文件首行作表头", d["kind"] == "txt" and d["rowCount"] == 2
          and d["records"][0]["user"] == "u1", str(d)[:200])

    csvf = workdir / "data.csv"
    csvf.write_text("user,pwd\nu1,p1\n", encoding="utf-8")
    check("csv 文件解析", case_test_data.resolve(str(csvf))["rowCount"] == 1)

    from openpyxl import Workbook
    wb = Workbook()
    ws = wb.active
    ws.append(["user", "pwd"])
    ws.append(["u1", "p1"])
    ws.append(["u2", "p2"])
    xlsx = workdir / "data.xlsx"
    wb.save(str(xlsx))
    d = case_test_data.resolve(str(xlsx))
    check("Excel 文件解析出记录集", d["kind"] == "excel" and d["rowCount"] == 2
          and d["records"][1]["pwd"] == "p2", str(d)[:200])

    jsonf = workdir / "data.json"
    jsonf.write_text('[{"user":"u1"}]', encoding="utf-8")
    check("json 文件解析", case_test_data.resolve(str(jsonf))["rowCount"] == 1)

    sqlf = workdir / "data.sql"
    sqlf.write_text("INSERT INTO t VALUES (1);\n", encoding="utf-8")
    d = case_test_data.resolve(str(sqlf))
    check("sql 文件按脚本文本", d["kind"] == "sql" and "INSERT" in d["text"], str(d)[:200])

    try:
        case_test_data.resolve("https://example.com/data.csv")
        check("URL 被拒绝", False, "未抛错")
    except AppError as exc:
        check("URL 被拒绝", exc.code == ErrorCode.PARAM_ERROR, str(exc))
    bad = workdir / "data.bin"
    bad.write_bytes(b"\x00\x01")
    try:
        case_test_data.resolve(str(bad))
        check("未知格式被拒绝", False, "未抛错")
    except AppError as exc:
        check("未知格式被拒绝", exc.code == ErrorCode.PARAM_ERROR, str(exc))


def check_preview_and_steps(api: Api, project_id: int) -> None:
    preview = api.data("POST", "/api/v1/test-cases/preview-test-data",
                       {"value": '[{"user":"u1"},{"user":"u2"}]'})
    check("预览 API 出参齐全",
          preview["rowCount"] == 2 and preview["columns"] == ["user"]
          and preview["sampleRows"][0] == ["user"] and len(preview["sampleRows"]) == 3,
          str(preview)[:200])

    case = api.data("POST", "/api/v1/test-cases", {
        "projectId": project_id, "title": "多行步骤用例", "caseType": "functional",
        "steps": [{"action": "打开页面", "expected": "页面加载"},
                  {"action": "点击提交", "expected": "提示成功"}],
        "expectedResult": "页面加载\n提示成功",
        "extra": {"测试数据": '[{"user":"u1","pwd":"p1"},{"user":"u2","pwd":"p2"}]'},
    })
    detail = api.data("GET", f"/api/v1/test-cases/{case['id']}")
    check("多行步骤落库往返保配对",
          [s["action"] for s in detail["steps"]] == ["打开页面", "点击提交"]
          and [s["expected"] for s in detail["steps"]] == ["页面加载", "提示成功"],
          str(detail["steps"])[:200])
    check("整体预期保留多行", detail["expectedResult"] == "页面加载\n提示成功",
          str(detail["expectedResult"])[:100])

    paired = xlsx_importer.pair_steps("1.打开页面\n2.点击提交", "页面加载\n提示成功")
    check("xlsx 导入步骤/预期按行配对",
          [s["expected"] for s in paired] == ["页面加载", "提示成功"], str(paired)[:200])
    return None


def check_execution_consumption(api: Api, workdir: pathlib.Path, project_id: int) -> None:
    case = api.data("POST", "/api/v1/test-cases", {
        "projectId": project_id, "title": "数据驱动用例", "caseType": "functional",
        "extra": {"测试数据": '[{"user":"u1","pwd":"p1"},{"user":"u2","pwd":"p2"}]'},
    })
    script = api.data("POST", "/api/v1/automation/scripts", {
        "name": "消费测试数据", "category": "web",
        "caseMatchMode": "case_no", "caseMatchValue": case["caseNo"],
        "content": (
            "import os, json, pathlib\n"
            "print('TD=' + os.environ.get('AITP_TEST_DATA', ''))\n"
            "p = os.environ.get('AITP_TEST_DATA_FILE', '')\n"
            "print('FILE_EXISTS=' + str(bool(p) and pathlib.Path(p).is_file()))\n"
            "j = os.environ.get('AITP_TEST_DATA_JSON', '')\n"
            "print('JSON=' + (pathlib.Path(j).read_text(encoding='utf-8') if j else ''))\n"
        ),
    })
    import sqlite3
    db_path = workdir / "check_case_test_data.db"
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        row = AutomationScriptRepository(connection).get(int(script["id"]))
        result = automation_service.execute_script(
            connection, dict(row or {}), db_path, timeout_seconds=30,
        )
        stdout = str(result.get("stdout") or "")
        check("Python 执行注入 AITP_TEST_DATA", "TD=" in stdout and "u1" in stdout, stdout[:300])
        check("Python 执行落 testdata.csv", "FILE_EXISTS=True" in stdout, stdout[:300])
        check("Python 执行落 testdata.json", '"user": "u2"' in stdout or '"user":"u2"' in stdout,
              stdout[:300])
    finally:
        connection.close()


def check_jmeter_csv(workdir: pathlib.Path) -> None:
    import sqlite3
    db_path = workdir / "check_case_test_data.db"
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    try:
        home = workdir / "jmeter"
        (home / "bin").mkdir(parents=True, exist_ok=True)
        (home / "bin" / "jmeter.bat").write_text("@echo off\n", encoding="utf-8")
        from aitp.repositories.sys_config_repo import SysConfigRepository
        SysConfigRepository(connection).set_value(
            "performance.jmeter.home", str(home), "string", "", "global", 1
        )
        connection.commit()
        jmx = workdir / "perf.jmx"
        jmx.write_text(
            '<jmeterTestPlan><hashTree><CSVDataSet guiclass="TestBeanGUI">'
            '<stringProp name="filename">placeholder.csv</stringProp>'
            '</CSVDataSet></hashTree></jmeterTestPlan>',
            encoding="utf-8",
        )
        run_dir = workdir / "run"
        run_dir.mkdir(parents=True, exist_ok=True)
        resolved = case_test_data.resolve('[{"user":"u1"},{"user":"u2"}]')
        (run_dir / "testdata.csv").write_text(case_test_data.to_csv(resolved), encoding="utf-8-sig")
        command = automation_service._jmeter_command(
            connection, {"file_name": "perf.jmx"}, jmx, run_dir, test_data=resolved,
        )
        copied = run_dir / "perf.jmx"
        check("JMeter 命令指向 run 目录 jmx 副本", str(copied) in command, str(command)[:300])
        check("JMeter CSVDataSet 改指 testdata.csv",
              copied.is_file() and "testdata.csv" in copied.read_text(encoding="utf-8"),
              str(command)[:300])
        check("JMeter 带 -JaitpTestDataFile 属性",
              any("aitpTestDataFile=" in part for part in command), str(command)[:300])
    finally:
        connection.close()


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_case_td_"))
    try:
        app = build_app(workdir)
        api = Api(app)
        project = api.data("POST", "/api/v1/projects", {"name": "门禁用例数据", "code": "GATE-CTD"})
        project_id = int(project["id"])
        check_resolver(workdir)
        check_preview_and_steps(api, project_id)
        check_execution_consumption(api, workdir, project_id)
        check_jmeter_csv(workdir)
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