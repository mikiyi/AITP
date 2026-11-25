"""质量洞察服务（V2.7.0）：跨轮次失败聚类 + 用例依赖与变更影响面。

只做确定性计算，不调用模型：
  - 失败聚类：最近 N 轮失败结果按「标题 + 实际结果」归一化签名聚合；
  - 用例依赖：显式 depends_on 关系，服务层保证同项目、禁自依赖、禁环；
  - 影响面：直接变更用例 + 需求关联用例 + 依赖反向闭包，附最近失败率作为风险权重。
"""

from __future__ import annotations

import re
import sqlite3
from collections import defaultdict, deque
from typing import Any

from ..common.time_utils import utcnow_iso

MAX_ROUNDS = 20
MAX_CLUSTERS = 20
MAX_CASE_SAMPLES = 10

_NOISE_RE = re.compile(r"[0-9a-f]{8,}|[0-9]+")
_SPACE_RE = re.compile(r"\s+")
_PUNCT_RE = re.compile(r"[^\w\u4e00-\u9fff]+")


def _signature(title: str, actual: str) -> str:
    """把编号/时间/地址等易变片段归一化，保留可读主干作为聚类键。"""
    raw = f"{title} || {actual}"
    normalized = _NOISE_RE.sub("#", raw.lower())
    normalized = _PUNCT_RE.sub("", normalized)
    normalized = _SPACE_RE.sub("", normalized)
    return normalized[:120] or "unknown"


def _recent_runs(conn: sqlite3.Connection, project_id: int | None,
                 rounds: int) -> list[dict[str, Any]]:
    rounds = max(1, min(int(rounds or 5), MAX_ROUNDS))
    params: list[Any] = []
    where = "WHERE is_deleted = 0"
    if project_id:
        where += " AND project_id = ?"
        params.append(int(project_id))
    rows = conn.execute(
        f"SELECT id, name, finished_at, created_at FROM biz_test_run {where} "
        "ORDER BY id DESC LIMIT ?", (*params, rounds)
    ).fetchall()
    return [dict(row) for row in rows]


def failure_clusters(conn: sqlite3.Connection, project_id: int | None,
                     rounds: int = 5) -> dict[str, Any]:
    """跨轮次失败聚类：同一签名在最近 N 轮里出现的次数、涉及用例与趋势。"""
    runs = _recent_runs(conn, project_id, rounds)
    if not runs:
        return {"rounds": [], "clusters": [], "totalFailures": 0, "runCount": 0}
    run_ids = [int(run["id"]) for run in runs]
    placeholders = ",".join("?" for _ in run_ids)
    rows = conn.execute(f"""
        SELECT run_id, case_id, case_no, title, result, actual_result, remark, created_at
        FROM biz_test_run_case
        WHERE is_deleted = 0 AND result IN ('failed', 'blocked') AND run_id IN ({placeholders})
    """, run_ids).fetchall()

    run_order = {run_id: index for index, run_id in enumerate(reversed(run_ids))}
    buckets: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        key = _signature(str(item.get("title") or ""),
                         str(item.get("actual_result") or item.get("remark") or ""))
        bucket = buckets.setdefault(key, {
            "key": key, "label": str(item.get("title") or "")[:80],
            "count": 0, "caseMap": {}, "runSet": set(), "firstSeen": "",
            "lastSeen": "", "perRun": defaultdict(int),
        })
        bucket["count"] += 1
        bucket["caseMap"][int(item["case_id"])] = {
            "caseId": int(item["case_id"]), "caseNo": item.get("case_no") or "",
            "title": item.get("title") or "",
        }
        bucket["runSet"].add(int(item["run_id"]))
        bucket["perRun"][int(item["run_id"])] += 1
        seen = str(item.get("created_at") or "")
        if seen and (not bucket["firstSeen"] or seen < bucket["firstSeen"]):
            bucket["firstSeen"] = seen
        if seen > bucket["lastSeen"]:
            bucket["lastSeen"] = seen

    clusters = []
    for bucket in buckets.values():
        series = [bucket["perRun"].get(run_id, 0) for run_id in run_ids]
        previous = series[:-1]
        baseline = sum(previous) / len(previous) if previous else 0
        latest = series[-1] if series else 0
        trend = "up" if latest > baseline else ("down" if latest < baseline else "flat")
        cases = list(bucket["caseMap"].values())
        clusters.append({
            "key": bucket["key"],
            "label": bucket["label"],
            "count": bucket["count"],
            "caseCount": len(cases),
            "cases": cases[:MAX_CASE_SAMPLES],
            "runIds": sorted(bucket["runSet"]),
            "runNames": [run["name"] for run in runs if int(run["id"]) in bucket["runSet"]],
            "firstSeen": bucket["firstSeen"],
            "lastSeen": bucket["lastSeen"],
            "trend": trend,
            "trendLabel": {"up": "上升", "down": "下降", "flat": "持平"}[trend],
            "series": series,
        })
    clusters.sort(key=lambda item: (-int(item["count"]), item["lastSeen"]))
    return {
        "rounds": [
            {"id": int(run["id"]), "name": run["name"],
             "finishedAt": run.get("finished_at") or run.get("created_at")}
            for run in runs
        ],
        "runCount": len(runs),
        "totalFailures": len(rows),
        "clusters": clusters[:MAX_CLUSTERS],
        "runOrder": run_order,
    }


def list_dependencies(conn: sqlite3.Connection, project_id: int | None) -> dict[str, Any]:
    params: list[Any] = []
    where = ""
    if project_id:
        where = "WHERE d.project_id = ?"
        params.append(int(project_id))
    rows = conn.execute(f"""
        SELECT d.id, d.case_id, d.depends_on_case_id, d.note, d.created_at,
               c.case_no AS case_no, c.title AS case_title,
               p.case_no AS dep_case_no, p.title AS dep_case_title
        FROM biz_case_dependency d
        JOIN biz_test_case c ON c.id = d.case_id
        JOIN biz_test_case p ON p.id = d.depends_on_case_id
        {where}
        ORDER BY d.id DESC
    """, params).fetchall()
    items = [{
        "id": row["id"],
        "caseId": row["case_id"],
        "caseNo": row["case_no"],
        "caseTitle": row["case_title"],
        "dependsOnCaseId": row["depends_on_case_id"],
        "dependsOnCaseNo": row["dep_case_no"],
        "dependsOnCaseTitle": row["dep_case_title"],
        "note": row["note"] or "",
        "createdAt": row["created_at"],
    } for row in rows]
    return {"total": len(items), "items": items}


def _case_project(conn: sqlite3.Connection, case_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, project_id, case_no, title FROM biz_test_case "
        "WHERE id = ? AND is_deleted = 0", (int(case_id),)
    ).fetchone()
    return dict(row) if row else None


def _depends_chain_reaches(conn: sqlite3.Connection, start: int, target: int) -> bool:
    """沿 depends_on 正向走：若从 start 的依赖闭包里能回到 target，则新增会成环。"""
    visited: set[int] = set()
    queue: deque[int] = deque([start])
    while queue:
        current = queue.popleft()
        if current == target:
            return True
        if current in visited:
            continue
        visited.add(current)
        for row in conn.execute(
            "SELECT depends_on_case_id FROM biz_case_dependency WHERE case_id = ?", (current,)
        ).fetchall():
            queue.append(int(row["depends_on_case_id"]))
    return False


def add_dependency(conn: sqlite3.Connection, project_id: int | None, case_id: int,
                   depends_on_case_id: int, note: str = "") -> dict[str, Any]:
    case = _case_project(conn, case_id)
    dependency = _case_project(conn, depends_on_case_id)
    if case is None or dependency is None:
        raise ValueError("用例不存在或已删除")
    if int(case["project_id"]) != int(dependency["project_id"]):
        raise ValueError("依赖双方必须属于同一项目")
    project = int(project_id or case["project_id"])
    if project != int(case["project_id"]):
        raise ValueError("projectId 与用例所属项目不一致")
    if int(case_id) == int(depends_on_case_id):
        raise ValueError("用例不能依赖自身")
    exists = conn.execute(
        "SELECT 1 FROM biz_case_dependency WHERE case_id = ? AND depends_on_case_id = ?",
        (int(case_id), int(depends_on_case_id)),
    ).fetchone()
    if exists:
        raise ValueError("该依赖关系已存在")
    if _depends_chain_reaches(conn, int(depends_on_case_id), int(case_id)):
        raise ValueError("该依赖会形成环，已拒绝")
    now = utcnow_iso()
    cursor = conn.execute("""
        INSERT INTO biz_case_dependency
            (project_id, case_id, depends_on_case_id, note, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (project, int(case_id), int(depends_on_case_id), str(note or "")[:200], now, now))
    return {"id": int(cursor.lastrowid or 0), "caseId": int(case_id),
            "dependsOnCaseId": int(depends_on_case_id)}


def delete_dependency(conn: sqlite3.Connection, dependency_id: int) -> None:
    cursor = conn.execute("DELETE FROM biz_case_dependency WHERE id = ?", (int(dependency_id),))
    if cursor.rowcount == 0:
        raise ValueError("依赖关系不存在")


def _failure_rate(conn: sqlite3.Connection, case_id: int) -> float:
    row = conn.execute("""
        SELECT COUNT(*) AS total, SUM(CASE WHEN result = 'failed' THEN 1 ELSE 0 END) AS failed
        FROM (
            SELECT result FROM biz_test_run_case
            WHERE case_id = ? AND is_deleted = 0 AND result IN ('passed', 'failed')
            ORDER BY id DESC LIMIT 20
        )
    """, (int(case_id),)).fetchone()
    total = int(row["total"] or 0) if row else 0
    failed = int(row["failed"] or 0) if row else 0
    return round(failed / total * 100, 1) if total else 0.0


def impact(conn: sqlite3.Connection, project_id: int | None,
           case_ids: list[int] | None = None,
           requirement_ids: list[int] | None = None) -> dict[str, Any]:
    """计算变更影响面：直接变更 + 需求关联 + 依赖闭包。"""
    case_ids = [int(item) for item in (case_ids or [])]
    requirement_ids = [int(item) for item in (requirement_ids or [])]
    resolved_project = project_id
    if resolved_project is None and case_ids:
        first = _case_project(conn, case_ids[0])
        resolved_project = int(first["project_id"]) if first else None
    if resolved_project is None:
        return {"projectId": None, "direct": [], "affected": [], "total": 0}

    direct: dict[int, dict[str, Any]] = {}
    for case_id in case_ids:
        case = _case_project(conn, case_id)
        if case and int(case["project_id"]) == int(resolved_project):
            direct[case_id] = {**case, "reason": "直接变更"}
    if requirement_ids:
        placeholders = ",".join("?" for _ in requirement_ids)
        rows = conn.execute(f"""
            SELECT id, project_id, case_no, title FROM biz_test_case
            WHERE is_deleted = 0 AND project_id = ? AND requirement_id IN ({placeholders})
        """, (int(resolved_project), *requirement_ids)).fetchall()
        for row in rows:
            direct.setdefault(int(row["id"]), {**dict(row), "reason": "需求关联"})

    # 反向 BFS：谁依赖了变更用例
    affected: dict[int, dict[str, Any]] = {}
    queue: deque[tuple[int, int]] = deque((case_id, 0) for case_id in direct)
    visited = set(direct)
    while queue:
        current, depth = queue.popleft()
        rows = conn.execute("""
            SELECT d.case_id, c.case_no, c.title
            FROM biz_case_dependency d
            JOIN biz_test_case c ON c.id = d.case_id
            WHERE d.depends_on_case_id = ? AND c.is_deleted = 0
        """, (current,)).fetchall()
        for row in rows:
            dependent_id = int(row["case_id"])
            if dependent_id in visited or dependent_id in direct:
                continue
            visited.add(dependent_id)
            affected[dependent_id] = {
                "caseId": dependent_id, "caseNo": row["case_no"], "title": row["title"],
                "depth": depth + 1, "reason": f"依赖链第 {depth + 1} 层",
            }
            queue.append((dependent_id, depth + 1))

    def _enrich(item: dict[str, Any], case_id: int) -> dict[str, Any]:
        return {
            "caseId": case_id, "caseNo": item.get("case_no") or item.get("caseNo") or "",
            "title": item.get("title") or "", "reason": item.get("reason") or "",
            "depth": item.get("depth", 0), "failureRate": _failure_rate(conn, case_id),
        }

    direct_list = sorted(
        (_enrich(item, case_id) for case_id, item in direct.items()),
        key=lambda item: (-item["failureRate"], item["caseId"]),
    )
    affected_list = sorted(
        (_enrich(item, case_id) for case_id, item in affected.items()),
        key=lambda item: (item["depth"], -item["failureRate"]),
    )
    return {
        "projectId": int(resolved_project),
        "direct": direct_list,
        "affected": affected_list,
        "total": len(direct_list) + len(affected_list),
        "requirementIds": requirement_ids,
    }


__all__ = [
    "add_dependency", "delete_dependency", "failure_clusters", "impact", "list_dependencies",
]
