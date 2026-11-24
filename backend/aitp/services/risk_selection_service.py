"""风险驱动智能选测（V2.6.0）。

不是「全量回归」，也不是拍脑袋挑几条，而是按可解释的权重算出本轮最该跑的
用例集合：

- 优先级：P0/P1 天然高风险；
- 近期失败：最近 30 天失败过的用例更容易再次失败；
- 变更影响：变更描述或指定模块命中的用例风险更高；
- 稳定性：flaky 用例需要更多观察；
- 未执行：长期没跑过的用例存在「覆盖假象」。

每条入选结果都带理由与分值明细，用户能看懂为什么选它，也能手工调整。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from . import case_stability_service

PRIORITY_WEIGHT = {"P0": 40.0, "P1": 28.0, "P2": 14.0, "P3": 6.0}
DEFAULT_LIMIT = 50
MAX_LIMIT = 300
WINDOW_DAYS = 30

_CN_STOP = ("改造", "优化", "调整", "修复", "需求", "功能", "版本", "迭代", "模块", "支付", "登录")


def _tokens(text: str) -> list[str]:
    """从变更描述里提取可匹配的片段：按分隔符拆，并保留 2 字以上中文词。"""
    raw = str(text or "")
    parts = [item.strip() for item in raw.replace("，", ",").replace("、", ",").replace("/", ",").split(",")]
    tokens: list[str] = []
    for part in parts:
        if not part:
            continue
        tokens.append(part)
        if len(part) > 2:
            for size in (2, 3):
                for index in range(len(part) - size + 1):
                    piece = part[index:index + size]
                    if piece not in _CN_STOP and piece not in tokens:
                        tokens.append(piece)
    return tokens[:24]


def _module_paths(conn: sqlite3.Connection, project_id: int) -> dict[int, str]:
    rows = conn.execute(
        "SELECT id, name, parent_id FROM biz_module WHERE project_id = ? AND is_deleted = 0",
        (int(project_id),),
    ).fetchall()
    by_id = {int(row["id"]): dict(row) for row in rows}
    cache: dict[int, str] = {}

    def path_of(module_id: int) -> str:
        if module_id in cache:
            return cache[module_id]
        parts: list[str] = []
        current = module_id
        seen: set[int] = set()
        while current in by_id and current not in seen:
            seen.add(current)
            parts.append(str(by_id[current]["name"] or ""))
            parent = by_id[current].get("parent_id")
            current = int(parent) if parent not in (None, "") else 0
        cache[module_id] = "/".join(reversed([item for item in parts if item]))
        return cache[module_id]

    return {module_id: path_of(module_id) for module_id in by_id}


def _failure_counts(conn: sqlite3.Connection, project_id: int) -> dict[int, int]:
    rows = conn.execute(
        """
        SELECT e.case_id AS case_id, COUNT(*) AS c
        FROM biz_interface_execution e
        JOIN biz_test_case c ON c.id = e.case_id
        WHERE c.project_id = ? AND c.is_deleted = 0
          AND e.result = 'failed'
          AND e.created_at >= datetime('now', ?)
        GROUP BY e.case_id
        """,
        (int(project_id), f"-{WINDOW_DAYS} days"),
    ).fetchall()
    return {int(row["case_id"]): int(row["c"]) for row in rows}


def select(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    version_id: int | None = None,
    change_scope: str = "",
    module_ids: list[int] | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    limit = max(1, min(MAX_LIMIT, int(limit or DEFAULT_LIMIT)))
    where = ["is_deleted = 0", "project_id = ?"]
    params: list[Any] = [int(project_id)]
    if version_id is not None:
        where.append("version_id = ?")
        params.append(int(version_id))
    rows = [
        dict(row) for row in conn.execute(
            "SELECT id, case_no, title, priority, module_id, status, case_type, created_at"
            f" FROM biz_test_case WHERE {' AND '.join(where)}",
            params,
        ).fetchall()
    ]
    if not rows:
        return {
            "items": [], "total": 0, "selected": 0, "mandatoryP0": 0,
            "basis": {"changeScope": change_scope, "moduleIds": module_ids or [],
                      "versionId": version_id, "limit": limit},
        }

    paths = _module_paths(conn, project_id)
    failures = _failure_counts(conn, project_id)
    wanted_modules = {int(item) for item in (module_ids or []) if int(item) > 0}
    tokens = _tokens(change_scope)
    stability = case_stability_service.stability_map(conn, [int(row["id"]) for row in rows[:1000]])

    scored: list[dict[str, Any]] = []
    for row in rows:
        case_id = int(row["id"])
        priority = str(row.get("priority") or "P2").upper()
        if priority not in PRIORITY_WEIGHT:
            priority = "P2"
        module_id = row.get("module_id")
        path = paths.get(int(module_id), "") if module_id not in (None, "") else ""
        score = PRIORITY_WEIGHT[priority]
        reasons: list[str] = [f"{priority} 用例"]
        breakdown: dict[str, float] = {"priority": PRIORITY_WEIGHT[priority]}

        hit_module = module_id not in (None, "") and int(module_id) in wanted_modules
        hit_text = bool(tokens) and any(token and (token in path or token in str(row.get("title") or ""))
                                        for token in tokens)
        if hit_module:
            score += 25
            breakdown["module"] = 25
            reasons.append(f"命中指定模块「{path or module_id}」")
        elif hit_text:
            score += 22
            breakdown["changeScope"] = 22
            reasons.append("命中变更描述")

        failed = failures.get(case_id, 0)
        if failed:
            bonus = min(18.0, failed * 6.0)
            score += bonus
            breakdown["recentFailure"] = bonus
            reasons.append(f"{WINDOW_DAYS} 天内失败 {failed} 次")

        stable = stability.get(case_id) or {}
        if stable.get("flaky"):
            score += 6
            breakdown["flaky"] = 6
            reasons.append("结果不稳定，需重点观察")

        if str(row.get("status") or "") in ("not_executed", ""):
            score += 8
            breakdown["neverExecuted"] = 8
            reasons.append("尚未执行过")

        scored.append({
            "caseId": case_id,
            "caseNo": str(row.get("case_no") or ""),
            "title": str(row.get("title") or ""),
            "priority": priority,
            "moduleId": module_id,
            "module": path,
            "caseType": str(row.get("case_type") or ""),
            "score": round(score, 1),
            "reasons": reasons,
            "breakdown": breakdown,
            "mandatory": priority == "P0",
        })

    scored.sort(key=lambda item: (-float(item["score"]), item["caseId"]))
    mandatory = [item for item in scored if item["mandatory"]]
    rest = [item for item in scored if not item["mandatory"]]
    items = (mandatory + rest)[:limit]
    return {
        "items": items,
        "total": len(scored),
        "selected": len(items),
        "mandatoryP0": len(mandatory),
        "basis": {
            "changeScope": change_scope,
            "moduleIds": sorted(wanted_modules),
            "versionId": version_id,
            "limit": limit,
            "windowDays": WINDOW_DAYS,
            "weights": {"priority": PRIORITY_WEIGHT, "module": 25, "changeScope": 22,
                        "recentFailure": "6/次（上限 18）", "flaky": 6, "neverExecuted": 8},
        },
    }


__all__ = ["select", "PRIORITY_WEIGHT", "DEFAULT_LIMIT", "MAX_LIMIT"]
