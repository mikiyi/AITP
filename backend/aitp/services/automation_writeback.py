"""自动化执行结果回写用例 / 轮次。

脚本声明自己覆盖哪些用例（按用例编号或标签），执行结束后：

1. 优先解析 JUnit XML（pytest 由服务端自动补 `--junitxml`）；
2. 没有 JUnit 时按 stdout 里的 pytest 结果行解析，解析不全只回写解析到的部分；
3. 完全解析不到时按退出码整体回写，并保留原始输出与提示，不静默丢弃。

每条回写都会落在 biz_automation_case_result 里，轮次里的用例同时走
test_run_service.record_result，和人工执行共用一套流水与全局状态。
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from ..common.time_utils import utcnow_iso
from ..repositories.automation_repo import (
    MATCH_MODE_CASE_NO,
    MATCH_MODE_TAG,
    WRITEBACK_FALLBACK,
    WRITEBACK_PARSED,
    WRITEBACK_PARTIAL,
    WRITEBACK_SKIPPED,
)
from ..repositories.test_case_repo import TestCaseRepository
from ..repositories.test_run_repo import (
    RUN_BLOCKED,
    RUN_FAILED,
    RUN_PASSED,
    RUN_SKIPPED,
)
from . import test_run_service

RESULT_TO_RUN = {
    "passed": RUN_PASSED,
    "failed": RUN_FAILED,
    "blocked": RUN_BLOCKED,
    "skipped": RUN_SKIPPED,
}

#: 一个用例可能对应多个测试函数，失败优先级最高
_RESULT_WEIGHT = {"passed": 0, "skipped": 1, "blocked": 2, "failed": 3}

_SPLIT_RE = re.compile(r"[,，;；\s]+")
#: pytest -v 结果行：tests/test_login.py::test_ft_001 PASSED 42%
_PYTEST_LINE_RE = re.compile(
    r"^(?P<file>[\w./\\-]+\.py)::(?P<name>[\w\[\]\-.]+)\s+"
    r"(?P<status>PASSED|FAILED|ERROR|SKIPPED)",
    re.IGNORECASE,
)
#: pytest 失败汇总行：FAILED tests/test_login.py::test_ft_001 - AssertionError
_PYTEST_FAILED_RE = re.compile(
    r"^(?:FAILED|ERROR)\s+(?P<file>[\w./\\-]+\.py)::(?P<name>[\w\[\]\-.]+)",
    re.IGNORECASE,
)


def _text(value: Any) -> str:
    return str(value or "").strip()


def split_values(raw: Any) -> list[str]:
    return [item for item in _SPLIT_RE.split(_text(raw)) if item]


def _norm(value: str) -> str:
    return re.sub(r"[^0-9a-z]+", "", value.lower())


def _matches_case_no(label: str, case_no: str) -> bool:
    needle = _norm(case_no)
    return bool(needle) and needle in _norm(label)


# ---------------------------------------------------------------------
# 用例范围
# ---------------------------------------------------------------------

def resolve_cases(conn: sqlite3.Connection, script: dict[str, Any]) -> list[dict[str, Any]]:
    """按脚本的映射方式圈定覆盖用例；没配置时返回空列表。"""
    values = split_values(script.get("case_match_value"))
    if not values:
        return []
    mode = _text(script.get("case_match_mode")) or MATCH_MODE_CASE_NO
    project_id = script.get("project_id")
    filters: list[str] = ["is_deleted = 0"]
    params: list[Any] = []
    if project_id not in (None, ""):
        filters.append("project_id = ?")
        params.append(int(project_id))

    if mode == MATCH_MODE_TAG:
        likes = " OR ".join("tags LIKE ?" for _ in values)
        filters.append(f"({likes})")
        params.extend(f"%{value}%" for value in values)
    else:
        placeholders = ",".join("?" for _ in values)
        filters.append(f"UPPER(case_no) IN ({placeholders})")
        params.extend(value.upper() for value in values)

    rows = conn.execute(
        f"SELECT id, case_no, title, tags FROM biz_test_case WHERE {' AND '.join(filters)}"
        " ORDER BY id",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


# ---------------------------------------------------------------------
# 结果解析
# ---------------------------------------------------------------------

def find_junit_file(run_dir: Path) -> Path | None:
    """优先用服务端强制生成的 junit.xml，其次取运行目录里最新的 xml。"""
    preferred = run_dir / "junit.xml"
    if preferred.is_file():
        return preferred
    candidates = sorted(
        (path for path in run_dir.glob("*.xml") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    return candidates[0] if candidates else None


def parse_junit(path: Path) -> list[dict[str, Any]]:
    """解析 JUnit XML；文件损坏时返回空列表，由调用方走兜底逻辑。"""
    try:
        root = ElementTree.parse(path).getroot()
    except (ElementTree.ParseError, OSError):
        return []
    outcomes: list[dict[str, Any]] = []
    for node in root.iter("testcase"):
        result = "passed"
        message = ""
        for tag, mapped in (("failure", "failed"), ("error", "failed"), ("skipped", "skipped")):
            child = node.find(tag)
            if child is not None:
                result = mapped
                message = _text(child.get("message") or (child.text or ""))[:500]
                break
        name = _text(node.get("name"))
        classname = _text(node.get("classname"))
        outcomes.append({
            "name": name,
            "classname": classname,
            "label": f"{classname}.{name}" if classname else name,
            "result": result,
            "message": message,
        })
    return outcomes


def parse_stdout(stdout: str) -> list[dict[str, Any]]:
    """从 pytest 输出里捞结果行；只返回确实标了状态的那些用例。"""
    outcomes: dict[str, dict[str, Any]] = {}
    for raw in (stdout or "").splitlines():
        line = raw.strip()
        match = _PYTEST_LINE_RE.match(line)
        if match:
            status = match.group("status").upper()
            result = {
                "PASSED": "passed",
                "SKIPPED": "skipped",
            }.get(status, "failed")
            label = f"{match.group('file')}::{match.group('name')}"
            outcomes[label] = {
                "name": match.group("name"),
                "classname": match.group("file"),
                "label": label,
                "result": result,
                "message": line[:500],
            }
            continue
        failed = _PYTEST_FAILED_RE.match(line)
        if failed:
            label = f"{failed.group('file')}::{failed.group('name')}"
            outcomes.setdefault(label, {
                "name": failed.group("name"),
                "classname": failed.group("file"),
                "label": label,
                "result": "failed",
                "message": line[:500],
            })
    return list(outcomes.values())


# ---------------------------------------------------------------------
# 回写
# ---------------------------------------------------------------------

def _merge(existing: dict[str, Any] | None, outcome: dict[str, Any]) -> dict[str, Any]:
    if existing is None:
        return {
            "result": outcome["result"],
            "testName": outcome["label"],
            "message": outcome["message"],
        }
    if _RESULT_WEIGHT[outcome["result"]] > _RESULT_WEIGHT[existing["result"]]:
        existing["result"] = outcome["result"]
        existing["message"] = outcome["message"]
    existing["testName"] = f"{existing['testName']}、{outcome['label']}"
    return existing


def _match_case(outcome: dict[str, Any], cases: list[dict[str, Any]]) -> dict[str, Any] | None:
    label = f"{outcome.get('classname', '')}.{outcome.get('name', '')}"
    for case in cases:
        if _matches_case_no(label, _text(case.get("case_no"))):
            return case
    return None


def _summary_text(results: list[dict[str, Any]], unmatched: int, note: str = "") -> str:
    passed = sum(1 for item in results if item["result"] == "passed")
    failed = sum(1 for item in results if item["result"] == "failed")
    skipped = len(results) - passed - failed
    parts = [f"回写 {len(results)} 条：通过 {passed}、失败 {failed}"]
    if skipped:
        parts.append(f"跳过/阻塞 {skipped}")
    text = "，".join(parts)
    if unmatched:
        text += f"；{unmatched} 个测试未匹配到用例"
    if note:
        text += f"；{note}"
    return text


def _env_name(conn: sqlite3.Connection, env_id: Any) -> str:
    if env_id in (None, ""):
        return ""
    row = conn.execute(
        "SELECT name FROM biz_test_env WHERE id = ?", (int(env_id),)
    ).fetchone()
    return _text(row["name"]) if row else ""


def _store_result(
    conn: sqlite3.Connection,
    *,
    automation_run_id: int,
    test_run_id: int | None,
    case: dict[str, Any],
    result: str,
    test_name: str,
    message: str,
    mapped_by: str,
    executed_by: str,
    env_name: str,
) -> dict[str, Any]:
    case_id = int(case["id"])
    run_case_id: int | None = None
    run_result = RESULT_TO_RUN.get(result, RUN_FAILED)

    if test_run_id:
        row = conn.execute(
            "SELECT id FROM biz_test_run_case WHERE run_id = ? AND case_id = ?"
            " AND is_deleted = 0 ORDER BY id LIMIT 1",
            (int(test_run_id), case_id),
        ).fetchone()
        if row is not None:
            run_case_id = int(row["id"])
            test_run_service.record_result(
                conn,
                int(test_run_id),
                run_case_id,
                result=run_result,
                actual_result=message,
                executed_by=executed_by,
            )

    if run_case_id is None:
        # 不在轮次里：仍然更新用例全局状态，并留一条执行流水
        conn.execute(
            """
            INSERT INTO biz_case_execution
                (case_id, result, environment, version, executed_by, executed_at, remark,
                 created_at, updated_at)
            VALUES (?, ?, ?, '', ?, ?, ?, datetime('now'), datetime('now'))
            """,
            (
                case_id,
                {"passed": "pass", "failed": "fail", "skipped": "not_executed"}.get(
                    result, "blocked"
                ),
                env_name,
                executed_by,
                utcnow_iso(),
                message,
            ),
        )
        global_status = {"passed": "pass", "failed": "fail", "skipped": "not_executed"}.get(
            result, "blocked"
        )
        TestCaseRepository(conn).update(case_id, {"status": global_status})

    conn.execute(
        """
        INSERT INTO biz_automation_case_result
            (automation_run_id, test_run_id, run_case_id, case_id, case_no, test_name,
             result, message, mapped_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            automation_run_id,
            test_run_id,
            run_case_id,
            case_id,
            _text(case.get("case_no")),
            test_name[:200],
            result,
            message,
            mapped_by,
            utcnow_iso(),
        ),
    )
    return {
        "caseId": case_id,
        "caseNo": _text(case.get("case_no")),
        "title": _text(case.get("title")),
        "result": result,
        "runCaseId": run_case_id,
        "mappedBy": mapped_by,
    }


def writeback(
    conn: sqlite3.Connection,
    script: dict[str, Any],
    run_row: dict[str, Any],
    db_path: Path,
    *,
    test_run_id: int | None = None,
    exit_code: int | None = None,
    run_status: str = "",
    stdout: str = "",
    stderr: str = "",
    error_message: str = "",
) -> dict[str, Any]:
    """把一次自动化执行结果回写到用例 / 轮次，返回回写结论。"""
    automation_run_id = int(run_row.get("id") or 0)
    run_no = _text(run_row.get("run_no"))
    cases = resolve_cases(conn, script)
    run_dir = Path(db_path).parent / "automation" / "runs" / run_no
    junit_path = find_junit_file(run_dir)
    outcomes = parse_junit(junit_path) if junit_path else []
    parsed_from = "junit" if outcomes else ""

    if not outcomes:
        outcomes = parse_stdout(stdout)
        parsed_from = "stdout" if outcomes else ""

    if not cases and not outcomes:
        summary = "脚本未配置覆盖用例（映射方式 + 映射值），本次不回写"
        _finish_run(conn, automation_run_id, test_run_id, WRITEBACK_SKIPPED, summary)
        return {"status": WRITEBACK_SKIPPED, "summary": summary, "cases": []}

    if not cases and outcomes:
        summary = (
            f"解析到 {len(outcomes)} 个测试，但脚本没配置覆盖用例，无法回写"
            f"（未找到 {junit_path.name if junit_path else 'JUnit 结果'}）"
        )
        _finish_run(conn, automation_run_id, test_run_id, WRITEBACK_SKIPPED, summary)
        return {
            "status": WRITEBACK_SKIPPED,
            "summary": summary,
            "unmatched": [item["label"] for item in outcomes],
            "cases": [],
        }

    executed_by = f"自动化 {run_no}".strip()
    env_name = _env_name(conn, script.get("env_id"))
    merged: dict[int, dict[str, Any]] = {}
    unmatched: list[str] = []
    mapped_by: dict[int, str] = {}

    for outcome in outcomes:
        case = _match_case(outcome, cases)
        reason = "case_no"
        if case is None and len(cases) == 1:
            case = cases[0]
            reason = "script"
        if case is None:
            unmatched.append(outcome["label"])
            continue
        case_id = int(case["id"])
        merged[case_id] = _merge(merged.get(case_id), outcome)
        mapped_by.setdefault(case_id, reason)

    missing = [case for case in cases if int(case["id"]) not in merged]
    note = ""
    if outcomes:
        write_status = WRITEBACK_PARTIAL if (unmatched or missing) else WRITEBACK_PARSED
        if missing:
            note = f"{len(missing)} 条用例没有对应测试结果，保持原状态"
    else:
        # 解析不到任何逐个结果：按整体退出码回写
        write_status = WRITEBACK_FALLBACK
        tail = _text(error_message) or _text(stderr)[-300:] or _text(stdout)[-300:]
        overall = "passed" if run_status == "success" else "failed"
        for case in cases:
            merged[int(case["id"])] = {
                "result": overall,
                "testName": "（整体退出码）",
                "message": (
                    "未解析到 JUnit / 用例级输出，按脚本整体退出码回写"
                    f"（退出码 {exit_code if exit_code is not None else '-'}）"
                    + (f"：{tail}" if tail else "")
                ),
            }
            mapped_by[int(case["id"])] = "script"
        note = "未解析到用例级结果，按退出码回写"

    results: list[dict[str, Any]] = []
    for case in cases:
        case_id = int(case["id"])
        merged_result = merged.get(case_id)
        if merged_result is None:
            continue
        message = _text(merged_result.get("message")) or _text(merged_result.get("testName"))
        results.append(_store_result(
            conn,
            automation_run_id=automation_run_id,
            test_run_id=test_run_id,
            case=case,
            result=str(merged_result["result"]),
            test_name=_text(merged_result.get("testName")),
            message=message,
            mapped_by=mapped_by.get(case_id, "case_no"),
            executed_by=executed_by,
            env_name=env_name,
        ))

    summary = _summary_text(results, len(unmatched), note)
    if parsed_from == "junit" and junit_path is not None:
        summary += f"；来源 {junit_path.name}"
    _finish_run(conn, automation_run_id, test_run_id, write_status, summary)
    return {
        "status": write_status,
        "summary": summary,
        "unmatched": unmatched,
        "cases": results,
    }


def _finish_run(
    conn: sqlite3.Connection,
    automation_run_id: int,
    test_run_id: int | None,
    status: str,
    summary: str,
) -> None:
    conn.execute(
        "UPDATE biz_automation_run SET test_run_id = ?, writeback_status = ?,"
        " writeback_summary = ? WHERE id = ?",
        (test_run_id, status, summary, automation_run_id),
    )


def case_results(conn: sqlite3.Connection, automation_run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_automation_case_result WHERE automation_run_id = ? ORDER BY id",
        (automation_run_id,),
    ).fetchall()
    return [
        {
            "id": row["id"],
            "caseId": row["case_id"],
            "caseNo": row["case_no"],
            "testName": row["test_name"],
            "result": row["result"],
            "message": row["message"],
            "mappedBy": row["mapped_by"],
            "runCaseId": row["run_case_id"],
            "testRunId": row["test_run_id"],
        }
        for row in rows
    ]
