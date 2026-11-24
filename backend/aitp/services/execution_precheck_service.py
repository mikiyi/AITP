"""执行前置自检（V2.6.0）。

批量执行前先做一次静态体检，把「跑了才发现环境没配、用例没地址、断言为空」
挡在前面，避免产生一批没有诊断价值的失败记录。

只做确定性检查，不发网络请求：自检本身不能成为新的不稳定源。网络连通性
在环境健康探测里已经覆盖，这里聚焦执行配置是否完整。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..repositories.base import loads
from ..repositories.test_env_repo import TestEnvRepository
from . import auth_session_service

SUPPORTED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")

STATUS_PASS = "pass"
STATUS_WARN = "warn"
STATUS_BLOCK = "block"


def _item(key: str, label: str, status: str, detail: str, *, fix: str = "") -> dict[str, Any]:
    return {"key": key, "label": label, "status": status, "detail": detail, "fix": fix}


def _interface_rows(conn: sqlite3.Connection, run_id: int | None, project_id: int) -> list[dict[str, Any]]:
    if run_id is None:
        return []
    rows = conn.execute(
        """
        SELECT r.id, r.case_id, r.case_no, r.title, r.case_type,
               COALESCE(c.extra_json, '{}') AS extra_json
        FROM biz_test_run_case r
        LEFT JOIN biz_test_case c ON c.id = r.case_id
        WHERE r.run_id = ? AND r.is_deleted = 0
        ORDER BY r.seq, r.id
        """,
        (int(run_id),),
    ).fetchall()
    out: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        extra = loads(item.get("extra_json"), {}) or {}
        item["extra"] = extra if isinstance(extra, dict) else {}
        if str(item.get("case_type") or "") == "interface":
            out.append(item)
    return out


def _env_of(
    conn: sqlite3.Connection, env_id: int | None,
) -> dict[str, Any] | None:
    if env_id is None:
        return None
    return TestEnvRepository(conn).get_by_id(int(env_id))


def check(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    run_id: int | None = None,
    env_id: int | None = None,
    case_rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """返回可直接展示的自检结果：items + blockers + warnings + canRun。"""
    items: list[dict[str, Any]] = []
    rows = case_rows if case_rows is not None else _interface_rows(conn, run_id, project_id)

    # 1) 用例范围
    if not rows:
        items.append(_item("cases", "可执行用例", STATUS_BLOCK,
                           "本轮没有接口测试用例，批量执行不会产生结果",
                           fix="先在轮次里加入接口用例"))
    else:
        items.append(_item("cases", "可执行用例", STATUS_PASS, f"{len(rows)} 条接口用例待执行"))

    # 2) 环境
    env = _env_of(conn, env_id)
    if env is None:
        items.append(_item("env", "执行环境", STATUS_BLOCK, "未绑定或找不到测试环境",
                           fix="在轮次详情选择执行环境"))
    else:
        base_url = str(env.get("app_base_url") or "").strip()
        if not base_url:
            items.append(_item("env", "执行环境", STATUS_BLOCK,
                               f"环境「{env.get('name')}」未配置应用地址",
                               fix="在「设置 → 测试环境」补全应用地址"))
        elif not base_url.lower().startswith(("http://", "https://")):
            items.append(_item("env", "执行环境", STATUS_BLOCK,
                               "应用地址必须以 http:// 或 https:// 开头",
                               fix="修正环境的应用地址"))
        else:
            items.append(_item("env", "执行环境", STATUS_PASS,
                               f"{env.get('name')} · {base_url}"))

    # 3) 登录态（只提示，不阻断：很多接口环境不需要登录）
    role = str((env or {}).get("auth_role") or "default")
    session = None
    try:
        if env is not None:
            sessions = auth_session_service.list_sessions(
                conn, project_id=project_id, env_id=int(env["id"]),
            )
            session = next(
                (row for row in sessions if str(row.get("role") or "") == role), None,
            ) or (sessions[0] if sessions else None)
    except Exception:  # noqa: BLE001 - 自检不能因为读档案失败而阻断执行
        session = None
    if session is None:
        items.append(_item("auth", "登录态", STATUS_WARN,
                           "未找到该环境的登录态档案；需要登录的接口会返回 401/403",
                           fix="在「设置 → 测试环境 → 登录态档案」导入 storageState"))
    elif session.get("expired"):
        items.append(_item("auth", "登录态", STATUS_WARN,
                           "登录态已过期，执行前请重新导入",
                           fix="重新导入 storageState 后预检"))
    else:
        items.append(_item("auth", "登录态", STATUS_PASS,
                           f"角色 {session.get('role')} · 可用"))

    # 4) 用例定义：地址、方法、断言
    missing_url: list[str] = []
    bad_method: list[str] = []
    no_assert: list[str] = []
    unresolved: list[str] = []
    for row in rows:
        extra = row.get("extra") or {}
        url = str(extra.get("接口URL") or extra.get("url") or extra.get("URL") or "").strip()
        method = str(extra.get("请求方式") or extra.get("method") or "GET").upper()
        rules = str(extra.get("断言规则") or "").strip()
        label = str(row.get("case_no") or row.get("title") or row.get("id"))
        if not url:
            missing_url.append(label)
            continue
        if method not in SUPPORTED_METHODS:
            bad_method.append(label)
        if not rules:
            no_assert.append(label)
        if "{{" in url:
            unresolved.append(label)

    if missing_url:
        items.append(_item("definition", "用例定义", STATUS_BLOCK,
                           f"{len(missing_url)} 条用例未填接口地址：{'、'.join(missing_url[:5])}"
                           + ("…" if len(missing_url) > 5 else ""),
                           fix="补齐接口 URL 后再执行"))
    elif bad_method:
        items.append(_item("definition", "用例定义", STATUS_BLOCK,
                           f"{len(bad_method)} 条用例的请求方法不支持：{'、'.join(bad_method[:5])}",
                           fix="改为 GET/POST/PUT/PATCH/DELETE/HEAD"))
    else:
        items.append(_item("definition", "用例定义", STATUS_PASS, "接口地址与请求方法齐全"))

    if unresolved:
        items.append(_item("variables", "变量替换", STATUS_WARN,
                           f"{len(unresolved)} 条用例的地址仍含未替换变量：{'、'.join(unresolved[:5])}",
                           fix="确认项目全局变量或环境变量已配置"))
    if no_assert:
        items.append(_item("assertion", "断言覆盖", STATUS_WARN,
                           f"{len(no_assert)} 条用例没有断言规则，执行只能验证「有响应」",
                           fix="补充预期状态码或断言规则"))

    blockers = [f"{item['label']}：{item['detail']}" for item in items if item["status"] == STATUS_BLOCK]
    warnings = [f"{item['label']}：{item['detail']}" for item in items if item["status"] == STATUS_WARN]
    return {
        "canRun": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "items": items,
        "summary": {
            "cases": len(rows),
            "blocked": len(blockers),
            "warnings": len(warnings),
        },
    }


__all__ = ["check", "STATUS_PASS", "STATUS_WARN", "STATUS_BLOCK"]
