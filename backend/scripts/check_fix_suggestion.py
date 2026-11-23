"""AI 修复建议门禁（四期专项 A）。

    python backend/scripts/check_fix_suggestion.py

覆盖六段：
  1. 迁移 076：建议表、编号全局唯一索引、软删不占索引；
  2. 取号：AI_FIX-001 起、软删不复用；
  3. 输出 Schema：合法建议通过、非法 target 被拦；
  4. 上下文装配：失败摘要与旧值取自真实用例与执行记录；
  5. 生成 → 待审 → 采纳落库 → 终态锁定（不可二次采纳、忽略后不可采纳）；
  6. 降级与隔离：无模型返回机械 diff 不落库；跨项目一律不存在；target=bug 不可自动采纳。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.output_schema import validate_output  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.services import fix_suggestion_service as svc  # noqa: E402

NOW = "2026-11-23T00:00:00+00:00"

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


def new_db(path: pathlib.Path) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def add_project(conn: sqlite3.Connection, code: str) -> int:
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 0)",
        (code, f"项目{code}", NOW, NOW),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def add_case(conn: sqlite3.Connection, project_id: int, case_no: str,
             extra: dict[str, Any]) -> int:
    conn.execute(
        "INSERT INTO biz_test_case (project_id, case_no, title, case_type, priority, status,"
        " steps_json, extra_json, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, '接口断言用例', 'interface', 'P1', 'not_executed', '[]', ?, ?, ?, 0)",
        (project_id, case_no, json.dumps(extra, ensure_ascii=False), NOW, NOW),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def add_execution(conn: sqlite3.Connection, case_id: int, *, result: str = "fail") -> int:
    assertions = [
        {"name": "状态码", "passed": True, "expected": "200", "actual": "200", "message": ""},
        {"name": "JSONPath $.code", "passed": False, "expected": "0", "actual": "1001",
         "message": "期望 0，实际 1001"},
    ]
    conn.execute(
        "INSERT INTO biz_interface_execution (case_id, run_id, run_case_id, env_id, method, url,"
        " status_code, duration_ms, result, request_json, response_json, assertions_json,"
        " error_message, executed_by, created_at)"
        " VALUES (?, NULL, NULL, NULL, 'GET', 'http://127.0.0.1/x', 200, 12, ?, '{}', ?, ?, '', 'local', ?)",
        (
            case_id, result,
            json.dumps({"status": 200, "body": '{"code": 1001}'}, ensure_ascii=False),
            json.dumps(assertions, ensure_ascii=False),
            NOW,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


STUB_SUGGESTION = {
    "analysis": "JSONPath $.code 期望 0，实际返回 1001，接口真实行为已变。",
    "target": "assertion",
    "suggestion": "把断言 $.code = 0 改为 $.code = 1001",
    "newAssertion": "status = 200\njson:$.code = 1001",
    "newExpectedBody": "",
    "newExpectedStatus": "",
    "reason": "响应体 code 稳定返回 1001",
}


def stub_generator(payload: dict[str, Any]) -> Any:
    """返回一个桩生成器：完全按传入 payload 当作模型产出的结构化建议。"""
    def run(_variables: dict[str, Any]) -> dict[str, Any]:
        return {
            "parsed": payload,
            "raw": json.dumps(payload, ensure_ascii=False),
            "model": "stub-model",
            "usage": {"totalTokens": 42},
            "cost": {"costMicros": None},
            "degrade": {"degraded": False},
        }

    return run


def no_model_generator(_variables: dict[str, Any]) -> dict[str, Any]:
    raise AppError(ErrorCode.AI_CREDENTIAL_MISSING, "没有可用模型")


def schema_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "schema.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("迁移 076 建了建议表", "biz_fix_suggestion" in tables, str(sorted(tables)))
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uk_biz_fix_suggestion_no'"
        ).fetchone()
        check("建议编号有全局唯一索引", row is not None, "索引缺失")
        check(
            "唯一索引只约束未删除行",
            row is not None and "is_deleted = 0" in str(row["sql"]),
            str(row["sql"] if row else ""),
        )
    finally:
        conn.close()


def numbering_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "numbering.db")
    try:
        pid = add_project(conn, "PN")
        cid = add_case(conn, pid, "IT-001", {})
        eid = add_execution(conn, cid)
        conn.commit()
        first = svc.generate(conn, eid, generator=stub_generator(STUB_SUGGESTION))
        conn.commit()
        no = first["suggestion"]["suggestionNo"]
        check("首条修复建议编号 AI_FIX-001", no == "AI_FIX-001", no)
        conn.execute("UPDATE biz_fix_suggestion SET is_deleted = 1")
        conn.commit()
        eid2 = add_execution(conn, cid)
        conn.commit()
        second = svc.generate(conn, eid2, generator=stub_generator(STUB_SUGGESTION))
        conn.commit()
        no2 = second["suggestion"]["suggestionNo"]
        check("软删后编号不复用（顺延 AI_FIX-002）", no2 == "AI_FIX-002", no2)
    finally:
        conn.close()


def output_schema_checks() -> None:
    ok_report = validate_output("fix_suggestion", dict(STUB_SUGGESTION))
    check("合法修复建议通过 Schema 校验", ok_report.ok, ok_report.message)
    bad = dict(STUB_SUGGESTION)
    bad["target"] = "delete_database"
    bad_report = validate_output("fix_suggestion", bad)
    check("非法 target 被 Schema 拦下", not bad_report.ok, bad_report.message)
    missing = {"target": "assertion", "suggestion": "改一下"}
    miss_report = validate_output("fix_suggestion", missing)
    check("缺少关键字段被拦下", not miss_report.ok, miss_report.message)


def _seed(workdir: pathlib.Path, name: str) -> tuple[sqlite3.Connection, int, int, int]:
    conn = new_db(workdir / f"{name}.db")
    pid = add_project(conn, f"P{name[:4].upper()}")
    cid = add_case(conn, pid, "IT-010", {
        "断言规则": "status = 200\njson:$.code = 0",
        "预期状态码": "200",
        "预期响应体": '{"code": 0}',
    })
    eid = add_execution(conn, cid)
    conn.commit()
    return conn, pid, cid, eid


def variable_checks(workdir: pathlib.Path) -> None:
    conn, pid, _cid, eid = _seed(workdir, "vars")
    try:
        execution = dict(conn.execute(
            "SELECT * FROM biz_interface_execution WHERE id = ?", (eid,)
        ).fetchone())
        variables = svc.build_variables(conn, execution)
        check("失败摘要含失败断言", "JSONPath $.code" in variables["failedSummary"],
              variables["failedSummary"])
        check("旧断言规则取自用例 extra",
              "json:$.code = 0" in variables["oldAssertions"], variables["oldAssertions"])
        check("实际响应摘要含状态码与响应体",
              "1001" in variables["responseSummary"], variables["responseSummary"])
    finally:
        conn.close()


def lifecycle_checks(workdir: pathlib.Path) -> None:
    conn, pid, cid, eid = _seed(workdir, "life")
    try:
        created = svc.generate(conn, eid, project_id=pid,
                               generator=stub_generator(STUB_SUGGESTION))
        conn.commit()
        check("生成成功并返回待审建议", created["generated"] is True, str(created))
        suggestion = created["suggestion"]
        sid = int(suggestion["id"])
        check("初始状态为待审", suggestion["status"] == svc.STATUS_PENDING, suggestion["status"])
        check("diff 预览含新旧差异", "json:$.code = 1001" in suggestion["diffPreview"],
              suggestion["diffPreview"])

        listed = svc.list_suggestions(conn, project_id=pid)
        check("列表能查到该建议", listed["total"] == 1, str(listed["total"]))
        check("状态计数正确", listed["counts"][svc.STATUS_PENDING] == 1, str(listed["counts"]))

        adopted = svc.adopt(conn, sid, project_id=pid, actor="local")
        conn.commit()
        check("采纳后状态为已采纳", adopted["suggestion"]["status"] == svc.STATUS_APPLIED,
              adopted["suggestion"]["status"])
        row = conn.execute(
            "SELECT extra_json FROM biz_test_case WHERE id = ?", (cid,)
        ).fetchone()
        extra = json.loads(str(row["extra_json"]))
        check("用例断言规则已按建议更新",
              extra.get("断言规则") == "status = 200\njson:$.code = 1001",
              str(extra.get("断言规则")))

        blocked = False
        try:
            svc.adopt(conn, sid, project_id=pid)
        except AppError:
            blocked = True
        check("已采纳建议不可二次采纳", blocked, "二次采纳未被拦下")

        check("跨项目查不到该建议（隔离）",
              _not_found(lambda: svc.get_suggestion(conn, sid, pid + 999)), "跨项目可见")
        check("不属于该项目的执行记录不能生成建议",
              _not_found(lambda: svc.generate(
                  conn, eid, project_id=pid + 999, generator=stub_generator(STUB_SUGGESTION))),
              "跨项目生成未被拦下")
    finally:
        conn.close()


def dismiss_checks(workdir: pathlib.Path) -> None:
    conn, pid, _cid, eid = _seed(workdir, "dism")
    try:
        created = svc.generate(conn, eid, project_id=pid,
                               generator=stub_generator(STUB_SUGGESTION))
        conn.commit()
        sid = int(created["suggestion"]["id"])
        dismissed = svc.dismiss(conn, sid, project_id=pid)
        conn.commit()
        check("忽略后状态为已忽略",
              dismissed["suggestion"]["status"] == svc.STATUS_DISMISSED,
              dismissed["suggestion"]["status"])
        blocked = False
        try:
            svc.adopt(conn, sid, project_id=pid)
        except AppError:
            blocked = True
        check("已忽略建议不可再采纳", blocked, "忽略后仍可采纳")
    finally:
        conn.close()


def degrade_and_bug_checks(workdir: pathlib.Path) -> None:
    conn, pid, _cid, eid = _seed(workdir, "degr")
    try:
        degraded = svc.generate(conn, eid, project_id=pid, generator=no_model_generator)
        conn.commit()
        check("无模型时返回降级标记", degraded["generated"] is False, str(degraded))
        check("无模型时给出机械 diff", "期望 0" in degraded.get("mechanicalDiff", ""),
              str(degraded.get("mechanicalDiff")))
        count = conn.execute("SELECT COUNT(*) AS c FROM biz_fix_suggestion").fetchone()["c"]
        check("无模型时不落库", int(count) == 0, str(count))

        bug_payload = dict(STUB_SUGGESTION)
        bug_payload["target"] = "bug"
        bug_payload["newAssertion"] = ""
        created = svc.generate(conn, eid, project_id=pid,
                               generator=stub_generator(bug_payload))
        conn.commit()
        sid = int(created["suggestion"]["id"])
        blocked = False
        try:
            svc.adopt(conn, sid, project_id=pid)
        except AppError:
            blocked = True
        check("判定为接口缺陷的建议不可自动采纳（须人工转缺陷）", blocked, "被自动采纳了")
    finally:
        conn.close()


def _not_found(fn: Any) -> bool:
    try:
        fn()
    except AppError as exc:
        return exc.code == ErrorCode.RESOURCE_NOT_FOUND
    return False


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-fix-"))
    try:
        schema_checks(workdir)
        numbering_checks(workdir)
        output_schema_checks()
        variable_checks(workdir)
        lifecycle_checks(workdir)
        dismiss_checks(workdir)
        degrade_and_bug_checks(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        for item in _failures:
            print(f"  [FAIL] {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
