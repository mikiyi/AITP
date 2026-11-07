"""报告趋势与版本对比：按天/周聚合执行与缺陷，比较同项目两个版本。"""

from __future__ import annotations

import sqlite3
from datetime import date, datetime, timedelta
from typing import Any

from ..common.time_utils import parse_iso
from ..errors import AppError, ErrorCode

GRANULARITIES = {"day": "按天", "week": "按周"}
DEFAULT_PERIODS = {"day": 14, "week": 8}
MAX_PERIODS = {"day": 90, "week": 52}

GATE_STATUS_LABELS = {"passed": "通过", "blocked": "阻断"}

TREND_METRICS = (
    ("executions", "执行数"),
    ("passRate", "通过率(%)"),
    ("newBugs", "新增缺陷"),
    ("closedBugs", "关闭缺陷"),
    ("p0p1Open", "P0/P1 遗留"),
)

COMPARE_METRICS = (
    ("caseTotal", "用例总数", "个", "neutral"),
    ("executed", "已执行", "个", "neutral"),
    ("executeRate", "执行率", "%", "higher"),
    ("passRate", "通过率", "%", "higher"),
    ("bugTotal", "缺陷总数", "个", "lower"),
    ("openBugs", "未闭环缺陷", "个", "lower"),
    ("p0p1Open", "P0/P1 遗留", "个", "lower"),
    ("defectDensity", "缺陷密度", "个/用例", "lower"),
    ("runs", "测试轮次", "个", "neutral"),
)


def _local_date(value: Any) -> date | None:
    parsed = parse_iso(str(value or ""))
    return parsed.date() if parsed else None


def _build_buckets(granularity: str, periods: int, today: date) -> list[tuple[date, date, str]]:
    """连续时间桶；最后一桶截止今天，保证无数据的时间段也能补 0。"""
    if granularity == "week":
        anchor = today - timedelta(days=today.weekday())
        starts = [anchor - timedelta(weeks=periods - 1 - index) for index in range(periods)]
        buckets: list[tuple[date, date, str]] = []
        for start in starts:
            end = min(start + timedelta(days=6), today)
            buckets.append((start, end, f"{start:%m-%d} 起"))
        return buckets
    starts = [today - timedelta(days=periods - 1 - index) for index in range(periods)]
    return [(start, start, f"{start:%m-%d}") for start in starts]


def _bucket_index(buckets: list[tuple[date, date, str]], value: date) -> int | None:
    for index, (start, end, _label) in enumerate(buckets):
        if start <= value <= end:
            return index
    return None


def _project_exists(conn: sqlite3.Connection, project_id: int) -> None:
    row = conn.execute(
        "SELECT id FROM biz_project WHERE id = ? AND is_deleted = 0", (project_id,)
    ).fetchone()
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")


def trend(
    conn: sqlite3.Connection,
    project_id: int,
    granularity: str = "day",
    periods: int | None = None,
    version_id: int | None = None,
    *,
    today: date | None = None,
) -> dict[str, Any]:
    """执行数/通过率/缺陷进出/P0P1 遗留的时间序列，空档补 0。

    version_id 给了就只统计该版本：趋势卡选到某个版本时不能还是全项目口径。
    """
    _project_exists(conn, project_id)
    v_sql = " AND version_id = ?" if version_id else ""
    v_alias = " AND {a}.version_id = ?" if version_id else ""
    v_params: list[Any] = [int(version_id)] if version_id else []
    if granularity not in GRANULARITIES:
        raise AppError(ErrorCode.PARAM_ERROR, "granularity 只能是 day 或 week")
    size = periods or DEFAULT_PERIODS[granularity]
    if size < 2 or size > MAX_PERIODS[granularity]:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"periods 取值范围 {2}-{MAX_PERIODS[granularity]}",
        )

    current = today or datetime.now().date()
    buckets = _build_buckets(granularity, size, current)
    executions = [0] * size
    passed = [0] * size
    new_bugs = [0] * size
    closed_bugs = [0] * size

    for row in conn.execute(
        "SELECT e.executed_at AS at, e.result AS result"
        " FROM biz_case_execution e"
        " JOIN biz_test_case c ON c.id = e.case_id"
        f" WHERE c.project_id = ? AND e.is_deleted = 0{v_alias.format(a='c')}",
        (project_id, *v_params),
    ).fetchall():
        day = _local_date(row["at"])
        index = _bucket_index(buckets, day) if day else None
        if index is None:
            continue
        executions[index] += 1
        if str(row["result"] or "") == "pass":
            passed[index] += 1

    for row in conn.execute(
        f"SELECT created_at AS at FROM biz_bug WHERE project_id = ? AND is_deleted = 0{v_sql}",
        (project_id, *v_params),
    ).fetchall():
        day = _local_date(row["at"])
        index = _bucket_index(buckets, day) if day else None
        if index is not None:
            new_bugs[index] += 1

    for row in conn.execute(
        "SELECT l.created_at AS at FROM biz_bug_status_log l"
        " JOIN biz_bug b ON b.id = l.bug_id"
        f" WHERE b.project_id = ? AND b.is_deleted = 0 AND l.to_status = 'closed'"
        f"{v_alias.format(a='b')}",
        (project_id, *v_params),
    ).fetchall():
        day = _local_date(row["at"])
        index = _bucket_index(buckets, day) if day else None
        if index is not None:
            closed_bugs[index] += 1

    high_bugs: list[tuple[date, date | None]] = []
    for row in conn.execute(
        "SELECT b.created_at AS created_at, MIN(l.created_at) AS closed_at"
        " FROM biz_bug b"
        " LEFT JOIN biz_bug_status_log l ON l.bug_id = b.id AND l.to_status = 'closed'"
        f" WHERE b.project_id = ? AND b.is_deleted = 0 AND b.severity IN ('P0', 'P1')"
        f"{v_alias.format(a='b')}"
        " GROUP BY b.id",
        (project_id, *v_params),
    ).fetchall():
        created = _local_date(row["created_at"])
        if created is None:
            continue
        high_bugs.append((created, _local_date(row["closed_at"])))

    p0p1_open: list[int] = []
    for _start, end, _label in buckets:
        p0p1_open.append(
            sum(
                1
                for created, closed in high_bugs
                if created <= end and (closed is None or closed > end)
            )
        )

    pass_rate = [
        round(passed[index] / executions[index] * 100, 2) if executions[index] else 0.0
        for index in range(size)
    ]
    total_executions = sum(executions)
    values: dict[str, list[float]] = {
        "executions": [float(item) for item in executions],
        "passRate": pass_rate,
        "newBugs": [float(item) for item in new_bugs],
        "closedBugs": [float(item) for item in closed_bugs],
        "p0p1Open": [float(item) for item in p0p1_open],
    }
    return {
        "projectId": project_id,
        "versionId": version_id,
        "granularity": granularity,
        "granularityLabel": GRANULARITIES[granularity],
        "startDate": buckets[0][0].isoformat(),
        "endDate": buckets[-1][1].isoformat(),
        "buckets": [label for _start, _end, label in buckets],
        "series": [
            {"key": key, "label": label, "values": values[key]}
            for key, label in TREND_METRICS
        ],
        "totals": {
            "executions": total_executions,
            "passRate": round(sum(passed) / total_executions * 100, 2) if total_executions else 0.0,
            "newBugs": sum(new_bugs),
            "closedBugs": sum(closed_bugs),
            "p0p1Open": p0p1_open[-1],
        },
    }


def trend_markdown(payload: dict[str, Any]) -> str:
    """把趋势数据落成 Markdown 表格，报告导出与页面共用同一份内容。"""
    labels = [item["label"] for item in payload["series"]]
    lines = [
        f"统计范围：{payload['startDate']} 至 {payload['endDate']}（{payload['granularityLabel']}）",
        "",
        "| 周期 | " + " | ".join(labels) + " |",
        "| --- | " + " | ".join("---" for _ in labels) + " |",
    ]
    series = {item["key"]: item["values"] for item in payload["series"]}
    for index, bucket in enumerate(payload["buckets"]):
        cells = []
        for key, _label in TREND_METRICS:
            value = series[key][index]
            cells.append(f"{value:.2f}".rstrip("0").rstrip(".") if key == "passRate" else str(int(value)))
        lines.append(f"| {bucket} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def _version_row(
    conn: sqlite3.Connection, project_id: int, version_id: int
) -> dict[str, Any]:
    row = conn.execute(
        "SELECT id, project_id, version_code, name, status"
        " FROM biz_project_version WHERE id = ? AND is_deleted = 0",
        (version_id,),
    ).fetchone()
    if not row or int(row["project_id"]) != project_id:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "版本不存在或不属于该项目")
    return dict(row)


def _scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...]) -> int:
    row = conn.execute(sql, params).fetchone()
    return int(row[0] or 0) if row else 0


def _version_metrics(
    conn: sqlite3.Connection, project_id: int, version_id: int
) -> dict[str, Any]:
    version = _version_row(conn, project_id, version_id)
    case_total = _scalar(
        conn,
        "SELECT COUNT(*) FROM biz_test_case"
        " WHERE project_id = ? AND version_id = ? AND is_deleted = 0",
        (project_id, version_id),
    )
    bug_total = _scalar(
        conn,
        "SELECT COUNT(*) FROM biz_bug"
        " WHERE project_id = ? AND version_id = ? AND is_deleted = 0",
        (project_id, version_id),
    )
    open_bugs = _scalar(
        conn,
        "SELECT COUNT(*) FROM biz_bug"
        " WHERE project_id = ? AND version_id = ? AND is_deleted = 0"
        " AND status NOT IN ('closed', 'rejected')",
        (project_id, version_id),
    )
    p0p1_open = _scalar(
        conn,
        "SELECT COUNT(*) FROM biz_bug"
        " WHERE project_id = ? AND version_id = ? AND is_deleted = 0"
        " AND severity IN ('P0', 'P1') AND status NOT IN ('closed', 'rejected')",
        (project_id, version_id),
    )

    run_counts: dict[str, int] = {}
    for row in conn.execute(
        "SELECT rc.result AS result, COUNT(*) AS total"
        " FROM biz_test_run_case rc"
        " JOIN biz_test_run r ON r.id = rc.run_id"
        " WHERE r.project_id = ? AND r.version_id = ?"
        " AND r.is_deleted = 0 AND rc.is_deleted = 0"
        " GROUP BY rc.result",
        (project_id, version_id),
    ).fetchall():
        run_counts[str(row["result"] or "")] = int(row["total"])

    executed = sum(
        run_counts.get(key, 0) for key in ("passed", "failed", "blocked", "skipped")
    )
    passed = run_counts.get("passed", 0)
    runs = _scalar(
        conn,
        "SELECT COUNT(*) FROM biz_test_run"
        " WHERE project_id = ? AND version_id = ? AND is_deleted = 0",
        (project_id, version_id),
    )
    gate = conn.execute(
        "SELECT status, conclusion, created_at FROM biz_release_gate"
        " WHERE project_id = ? AND version_id = ? AND is_deleted = 0"
        " ORDER BY id DESC LIMIT 1",
        (project_id, version_id),
    ).fetchone()
    gate_status = str((gate["status"] if gate else "") or "")
    return {
        "versionId": version_id,
        "versionCode": str(version.get("version_code") or ""),
        "versionName": str(version.get("name") or ""),
        "caseTotal": case_total,
        "executed": executed,
        "passed": passed,
        "executeRate": round(executed / case_total * 100, 2) if case_total else 0.0,
        "passRate": round(passed / executed * 100, 2) if executed else 0.0,
        "bugTotal": bug_total,
        "openBugs": open_bugs,
        "p0p1Open": p0p1_open,
        "defectDensity": round(bug_total / case_total, 4) if case_total else 0.0,
        "runs": runs,
        "gateStatus": gate_status,
        "gateStatusLabel": GATE_STATUS_LABELS.get(gate_status, "未评估"),
        "gateConclusion": str((gate["conclusion"] if gate else "") or ""),
    }


def compare_versions(
    conn: sqlite3.Connection,
    project_id: int,
    base_version_id: int,
    target_version_id: int,
) -> dict[str, Any]:
    """同一项目两个版本的用例/执行/缺陷/准入并排对比。"""
    _project_exists(conn, project_id)
    if base_version_id == target_version_id:
        raise AppError(ErrorCode.PARAM_ERROR, "两个版本不能相同")
    base = _version_metrics(conn, project_id, base_version_id)
    target = _version_metrics(conn, project_id, target_version_id)
    metrics: list[dict[str, Any]] = []
    for key, label, unit, direction in COMPARE_METRICS:
        base_value = base[key]
        target_value = target[key]
        metrics.append({
            "key": key,
            "label": label,
            "unit": unit,
            "direction": direction,
            "higherIsBetter": direction == "higher",
            "neutral": direction == "neutral",
            "base": base_value,
            "target": target_value,
            "delta": round(float(target_value) - float(base_value), 4),
        })
    return {
        "projectId": project_id,
        "base": base,
        "target": target,
        "metrics": metrics,
    }
