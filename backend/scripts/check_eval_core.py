"""Eval 核心闭环门禁（设计 §八 check_eval_core，12 项断言）。

覆盖：评测集命名/软删/归档、EC 编号不复用、导入干跑与幂等、状态机、
取消幂等、单条隔离、超时不重试、预算拦截、断点续跑、reaper 回收、
端态断言与禁调工具。全程临时库，零 token。

用法：python backend/scripts/check_eval_core.py
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from typing import Any

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.services import eval_runner, eval_service  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, func: Callable[[], None]) -> None:
    try:
        func()
    except Exception as exc:  # noqa: BLE001 - 门禁脚本需要把任何异常都记成失败
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
    else:
        RESULTS.append((name, True, ""))


def _new_conn() -> sqlite3.Connection:
    directory = pathlib.Path(tempfile.mkdtemp(prefix="aitp_eval_core_"))
    db_path = directory / "eval.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("EVAL-CORE", "评测核心自检项目", now, now),
    )
    conn.commit()
    return conn


def _project_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT id FROM biz_project LIMIT 1").fetchone()["id"])


def _suite(conn: sqlite3.Connection, name: str) -> int:
    return eval_service.create_suite(
        conn,
        {"projectId": _project_id(conn), "name": name, "visibility": "private", "targetType": "llm"},
    )


def _case(conn: sqlite3.Connection, suite_id: int, title: str, **extra: Any) -> int:
    payload: dict[str, Any] = {
        "suiteId": suite_id,
        "projectId": _project_id(conn),
        "title": title,
        "severity": "S1",
        "assertions": {"must_contain": [title]},
    }
    payload.update(extra)
    return eval_service.add_case(conn, payload)


def _run(conn: sqlite3.Connection, suite_id: int, purpose: str) -> int:
    run = eval_service.create_run(
        conn,
        {"projectId": _project_id(conn), "suiteId": suite_id, "config": {"temperature": 0}, "purpose": purpose},
    )
    return int(run["runId"])


def _expect_error(code: int, func: Callable[[], object]) -> None:
    try:
        func()
    except AppError as exc:
        assert exc.code == code, f"错误码 {exc.code} != 期望 {code}：{exc.message}"
        return
    raise AssertionError(f"期望抛出 {code}，实际未抛出")


def test_suite_rules() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "命名规则集")
    _expect_error(ErrorCode.PARAM_ERROR, lambda: _suite(conn, "命名规则集"))
    _case(conn, suite_id, "可删除用例")
    eval_service.delete_suite(conn, suite_id)
    again = _suite(conn, "命名规则集")
    _case(conn, again, "归档前用例")
    eval_service.update_suite(conn, again, {"status": "archived"})
    _expect_error(ErrorCode.ILLEGAL_OPERATION, lambda: _case(conn, again, "归档后用例"))
    _expect_error(ErrorCode.ILLEGAL_OPERATION, lambda: _run(conn, again, "archived-run"))


def test_case_no_not_reused() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "编号集")
    first = _case(conn, suite_id, "用例一")
    second = _case(conn, suite_id, "用例二")
    _case(conn, suite_id, "用例三")
    eval_service.delete_case(conn, second)
    fourth = _case(conn, suite_id, "用例四")
    row = conn.execute("SELECT case_no FROM biz_eval_case WHERE id = ?", (fourth,)).fetchone()
    assert row["case_no"] == "EC-004", row["case_no"]
    assert conn.execute("SELECT case_no FROM biz_eval_case WHERE id = ?", (first,)).fetchone()["case_no"] == "EC-001"


def test_import_dry_run_and_idempotent() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "导入集")
    items = [
        {"title": "导入一", "severity": "S1", "assertions": {"must_contain": ["甲"]}},
        {"title": "导入二", "severity": "S0", "assertions": {"forbidden_contains": ["乙"]}},
        {"title": "导入三", "severity": "S2", "assertions": {"max_chars": 80}},
    ]
    before = conn.execute("SELECT COUNT(*) AS n FROM biz_eval_case WHERE suite_id = ?", (suite_id,)).fetchone()["n"]
    dry = eval_service.import_cases(conn, suite_id, {"cases": items, "dryRun": True})
    after = conn.execute("SELECT COUNT(*) AS n FROM biz_eval_case WHERE suite_id = ?", (suite_id,)).fetchone()["n"]
    assert dry["dryRun"] is True and dry["accepted"] == 3 and before == after, dry
    real = eval_service.import_cases(conn, suite_id, {"cases": items})
    assert real["accepted"] == 3 and len(real["created"]) == 3, real
    repeat = eval_service.import_cases(conn, suite_id, {"cases": items})
    assert repeat["accepted"] == 0 and repeat["skipped"] == 3, repeat
    total = conn.execute("SELECT COUNT(*) AS n FROM biz_eval_case WHERE suite_id = ?", (suite_id,)).fetchone()["n"]
    assert total == 3, total


def test_state_machine() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "状态机集")
    _case(conn, suite_id, "状态机用例")
    run_id = _run(conn, suite_id, "state")
    assert conn.execute("SELECT status FROM biz_eval_run WHERE id = ?", (run_id,)).fetchone()["status"] == "queued"

    seen: list[str] = []
    original = eval_runner.judge_case

    def spy(case: dict[str, Any], entry: dict[str, Any]) -> dict[str, Any]:
        row = conn.execute("SELECT status FROM biz_eval_run WHERE id = ?", (run_id,)).fetchone()
        seen.append(str(row["status"]))
        return original(case, entry)

    eval_runner.judge_case = spy
    try:
        eval_service.execute_replay(conn, run_id, [{"caseNo": "EC-001", "output": "状态机用例"}])
    finally:
        eval_runner.judge_case = original
    assert seen == ["running"], seen
    assert conn.execute("SELECT status FROM biz_eval_run WHERE id = ?", (run_id,)).fetchone()["status"] == "finished"


def test_cancel_idempotent() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "取消集")
    _case(conn, suite_id, "取消用例")
    run_id = _run(conn, suite_id, "cancel")
    first = eval_service.cancel_run(conn, run_id)
    second = eval_service.cancel_run(conn, run_id)
    assert first["status"] == "cancelled" and first["idempotent"] is False, first
    assert second["status"] == "cancelled" and second["idempotent"] is True, second


def test_single_case_isolation() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "隔离集")
    broken = _case(
        conn,
        suite_id,
        "坏评分用例",
        rubric_json={"dimensions": [{"key": "instruction", "weight": 1, "scale": 5}]},
        assertions={"max_chars": 100},
    )
    fine = _case(conn, suite_id, "正常用例", assertions={"must_contain": ["正常"]})
    run_id = _run(conn, suite_id, "isolation")
    detail = eval_service.execute_replay(
        conn,
        run_id,
        [
            {"caseNo": "EC-001", "output": "随便", "dimensionScores": {"instruction": "abc"}},
            {"caseNo": "EC-002", "output": "这是正常回答"},
        ],
    )
    verdicts = {int(row["case_id"]): row["verdict"] for row in detail["results"]}
    assert verdicts[broken] == "error", verdicts
    assert verdicts[fine] == "pass", verdicts
    assert detail["run"]["status"] == "finished" and detail["run"]["errors"] == 1


def test_timeout_no_retry() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "超时集")
    case_id = _case(conn, suite_id, "超时用例")
    run_id = _run(conn, suite_id, "timeout")
    detail = eval_service.execute_replay(
        conn,
        run_id,
        [{"caseNo": "EC-001", "output": "", "error": "infra.timeout", "attemptCount": 3}],
    )
    rows = [row for row in detail["results"] if int(row["case_id"]) == case_id]
    assert len(rows) == 1, rows
    assert rows[0]["verdict"] == "error" and rows[0]["failure_reason"] == "infra.timeout", rows[0]
    assert int(rows[0]["attempt_count"]) == 3, rows[0]


def test_budget_block() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "预算集")
    _case(conn, suite_id, "预算用例一")
    _case(conn, suite_id, "预算用例二")
    run_id = _run(conn, suite_id, "budget")
    _expect_error(
        ErrorCode.EVAL_BUDGET_EXCEEDED,
        lambda: eval_runner.execute_run(conn, run_id, [{"caseNo": "EC-001", "output": "预算用例一"}], {"maxCases": 1}),
    )
    _expect_error(
        ErrorCode.EVAL_BUDGET_EXCEEDED,
        lambda: eval_runner.execute_run(
            conn,
            run_id,
            [{"caseNo": "EC-001", "output": "预算用例一", "tokensIn": 100, "tokensOut": 100}],
            {"maxTokens": 10},
        ),
    )
    status = conn.execute("SELECT status FROM biz_eval_run WHERE id = ?", (run_id,)).fetchone()["status"]
    assert status == "queued", status


def test_resume_only_missing() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "续跑集")
    first = _case(conn, suite_id, "已完成用例", assertions={"max_chars": 100})
    second = _case(conn, suite_id, "待补用例")
    run_id = _run(conn, suite_id, "resume")
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO biz_eval_result (run_id, case_id, case_no, output_text, verdict, created_at, updated_at)"
        " VALUES (?, ?, 'EC-001', 'KEEP-ORIGINAL', 'pass', ?, ?)",
        (run_id, first, now, now),
    )
    conn.commit()
    detail = eval_service.resume_run(
        conn,
        run_id,
        [{"caseNo": "EC-001", "output": "不应重跑"}, {"caseNo": "EC-002", "output": "待补用例"}],
    )
    rows = {int(row["case_id"]): row for row in detail["results"]}
    assert str(rows[first]["output_text"]) == "KEEP-ORIGINAL", rows[first]
    assert int(rows[second]["case_id"]) == second and rows[second]["verdict"] == "pass", rows.get(second)
    assert len(detail["results"]) == 2


def test_reaper_stale_run() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "僵死集")
    _case(conn, suite_id, "僵死用例")
    run_id = _run(conn, suite_id, "stale")
    conn.execute(
        "UPDATE biz_eval_run SET status = 'running', heartbeat_at = '2000-01-01T00:00:00Z',"
        " worker_token = 'stale-token' WHERE id = ?",
        (run_id,),
    )
    conn.commit()
    reaped = eval_runner.reap_stale_runs(conn, timeout_seconds=60)
    assert run_id in reaped, reaped
    row = conn.execute("SELECT status, last_error, worker_token FROM biz_eval_run WHERE id = ?", (run_id,)).fetchone()
    assert row["status"] == "failed" and row["last_error"] == "infra.stale_run" and row["worker_token"] == "", dict(row)


def test_end_state_mismatch_fails() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "端态集")
    _case(
        conn,
        suite_id,
        "下单端态用例",
        assertions={"end_state": {"kind": "http", "checks": [{"request": "GET /o/1", "expect": {"status": "filled"}}]}},
    )
    run_id = _run(conn, suite_id, "end-state")
    detail = eval_service.execute_replay(
        conn,
        run_id,
        [{"caseNo": "EC-001", "output": "订单已提交", "endState": {"GET /o/1": {"status": "pending"}}}],
    )
    row = detail["results"][0]
    assert row["verdict"] == "fail", row
    assert "assert.end_state_mismatch" in str(row["failure_reason"]), row["failure_reason"]


def test_forbidden_tool_fails() -> None:
    conn = _new_conn()
    suite_id = _suite(conn, "工具集")
    _case(
        conn,
        suite_id,
        "禁调工具用例",
        assertions={"max_chars": 100},
        tools_expected={"required": ["trade"], "forbidden": ["email"]},
    )
    run_id = _run(conn, suite_id, "forbidden-tool")
    detail = eval_service.execute_replay(
        conn,
        run_id,
        [
            {
                "caseNo": "EC-001",
                "output": "已处理",
                "toolTrace": [{"tool": "trade", "args": {}}, {"tool": "email", "args": {}}],
            }
        ],
    )
    row = detail["results"][0]
    assert row["verdict"] == "fail", row
    assert "tool.forbidden_called:email" in str(row["failure_reason"]), row["failure_reason"]


def main() -> int:
    check("评测集命名唯一 / 软删后可重建 / 归档禁止新增与跑批", test_suite_rules)
    check("EC 编号按集自增且不复用软删编号", test_case_no_not_reused)
    check("导入干跑不改库、重复导入按标题幂等", test_import_dry_run_and_idempotent)
    check("状态机 queued→running→finished", test_state_machine)
    check("取消幂等：重复取消不报错", test_cancel_idempotent)
    check("单条异常不拖垮整批", test_single_case_isolation)
    check("超时不重试：attemptCount 如实记录", test_timeout_no_retry)
    check("预算拦截（160106）不改状态", test_budget_block)
    check("断点续跑只补未完成用例", test_resume_only_missing)
    check("reaper 把心跳超时的 running 置 failed", test_reaper_stale_run)
    check("端态断言不符 → fail", test_end_state_mismatch_fails)
    check("禁调工具 → fail", test_forbidden_tool_fails)

    failed = [item for item in RESULTS if not item[1]]
    for name, passed, message in RESULTS:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" —— {message}" if message else ""))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())