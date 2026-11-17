"""多接口串联场景执行引擎。

不另造 HTTP/断言轮子：每步复用 interface_runner.execute（同一套接口 extra
字段口径 + 断言库），场景层只负责「顺序编排 + 变量提取/传递 + 失败策略 +
数据驱动」。变量用 {{var}} 在后续步骤的 URL/头/参数/体里引用。
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from html import escape
from typing import Any

from ..repositories.api_scenario_repo import ApiScenarioRunRepository
from ..repositories.base import loads
from ..repositories.numbering import next_no
from . import auto_bug_service, case_test_data, interface_runner

MAX_RECORDS = 20
MAX_WAIT_MS = 5000


def _extract(body: str, parsed: Any, extract: list[dict[str, Any]]) -> dict[str, Any]:
    extracted: dict[str, Any] = {}
    for ex in extract or []:
        var = str(ex.get("var") or "").strip()
        if not var:
            continue
        value: Any = None
        if ex.get("path") and parsed is not None:
            ok, val = interface_runner._json_path_get(parsed, str(ex["path"]))
            if ok:
                value = val
        elif ex.get("regex"):
            match = re.search(str(ex["regex"]), body)
            if match:
                value = match.group(1) if match.groups() else match.group(0)
        if value is not None:
            extracted[var] = value
    return extracted


def _run_browser_step(conn: sqlite3.Connection, step: dict[str, Any],
                      variables: dict[str, str]) -> dict[str, Any]:
    """P2 Web/App 场景步骤：复用探索性 MCP 客户端驱动真实浏览器（不另造引擎）。"""
    from . import exploratory_service

    t0 = time.monotonic()
    settings = exploratory_service.get_settings(conn)
    command = str(settings.get("mcpCommand") or "")
    if not command:
        return {"passed": False, "statusCode": None, "durationMs": 0,
                "request": {"method": "BROWSER", "url": ""}, "response": {"body": ""},
                "assertions": [], "message": "未配置 MCP 服务命令，无法执行浏览器步骤"}
    action = str(step.get("action") or "navigate")
    url = interface_runner._substitute(str(step.get("url") or ""), variables)
    selector = interface_runner._substitute(str(step.get("selector") or ""), variables)
    value = interface_runner._substitute(str(step.get("value") or ""), variables)
    client = exploratory_service.McpClient(command)
    try:
        client.initialize()
        client.ensure_pinned()
        if action == "navigate":
            client.call_tool("browser_navigate", {"url": url})
        elif action == "click":
            client.call_tool("browser_click", {"selector": selector})
        elif action == "type":
            client.call_tool("browser_type", {"selector": selector, "value": value})
        snap = client.call_tool("browser_snapshot", {})
        return {"passed": True, "statusCode": None,
                "durationMs": int((time.monotonic() - t0) * 1000),
                "request": {"method": "BROWSER", "url": url or selector},
                "response": {"body": str(snap.get("snapshot") or "")},
                "assertions": [], "message": ""}
    except Exception as exc:  # noqa: BLE001 - 浏览器步骤失败记入轨迹
        return {"passed": False, "statusCode": None,
                "durationMs": int((time.monotonic() - t0) * 1000),
                "request": {"method": "BROWSER", "url": url or selector},
                "response": {"body": ""}, "assertions": [], "message": str(exc)}
    finally:
        client.close()


def run_scenario(
    conn: sqlite3.Connection,
    scenario: dict[str, Any],
    env_id: int | None,
    record: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """顺序执行一个场景（单条数据记录），返回逐步轨迹与共享变量。"""
    steps = loads(scenario.get("steps_json"), []) or []
    # 步骤可用绝对 URL，环境可选；选了环境才注入 baseUrl 等变量
    env = interface_runner.load_env(conn, env_id) if env_id else {}
    variables: dict[str, str] = {str(k): str(v) for k, v in (env.get("variables") or {}).items()}
    variables.update({str(k): str(v) for k, v in (record or {}).items()})
    out: list[dict[str, Any]] = []
    overall = True
    t0 = time.monotonic()
    for idx, step in enumerate(steps, start=1):
        wait = min(int(step.get("waitMs") or 0), MAX_WAIT_MS)
        if wait > 0:
            time.sleep(wait / 1000)
        extra = step.get("extra") or {}
        if str(step.get("type") or "interface") == "browser":
            detail = _run_browser_step(conn, step, variables)
        else:
            detail = interface_runner.execute(
                extra, env, conn=conn,
                project_id=scenario.get("project_id"),
                extra_variables=variables,
            )
        body = str((detail.get("response") or {}).get("body") or "")
        parsed: Any = None
        try:
            parsed = json.loads(body)
        except (TypeError, ValueError):
            parsed = None
        extracted = _extract(body, parsed, step.get("extract") or [])
        for var, val in extracted.items():
            variables[var] = str(val)
        passed = bool(detail.get("passed"))
        out.append({
            "stepNo": idx,
            "name": str(step.get("name") or f"步骤{idx}"),
            "passed": passed,
            "statusCode": detail.get("statusCode"),
            "durationMs": detail.get("durationMs"),
            "extracted": extracted,
            "assertions": detail.get("assertions"),
            "request": detail.get("request"),
            "response": detail.get("response"),
            "message": detail.get("message"),
        })
        if not passed:
            overall = False
            if not step.get("continueOnError"):
                break
    return {
        "passed": overall,
        "steps": out,
        "variables": variables,
        "durationMs": int((time.monotonic() - t0) * 1000),
    }


def run_with_data(
    conn: sqlite3.Connection,
    scenario: dict[str, Any],
    env_id: int | None,
) -> dict[str, Any]:
    """数据驱动：有记录集则逐条跑整个场景，否则单跑；落一条执行记录。"""
    data = case_test_data.resolve_for(
        conn, scenario.get("project_id"), str(scenario.get("data_source") or "")
    )
    records = list(data.get("records") or [])[:MAX_RECORDS]
    runs = [run_scenario(conn, scenario, env_id, rec) for rec in records] if records \
        else [run_scenario(conn, scenario, env_id, None)]
    passed = all(r["passed"] for r in runs)
    duration = sum(int(r["durationMs"]) for r in runs)
    repo = ApiScenarioRunRepository(conn)
    run_no = repo.next_run_no()
    run_id = repo.insert({
        "run_no": run_no,
        "scenario_id": int(scenario["id"]),
        "project_id": int(scenario["project_id"]),
        "env_id": env_id,
        "passed": 1 if passed else 0,
        "record_count": len(runs),
        "summary_json": json.dumps({"runs": runs}, ensure_ascii=False),
        "duration_ms": duration,
    })
    conn.commit()
    result = {
        "runId": run_id, "runNo": run_no, "passed": passed,
        "recordCount": len(runs), "durationMs": duration, "runs": runs,
        "autoBugId": None,
    }
    if not passed:
        bug_id = auto_bug_service.from_scenario(
            conn, scenario, {**result, "run_id": run_id}, runs
        )
        if bug_id:
            conn.execute(
                "UPDATE biz_api_scenario_run SET summary_json = ? WHERE id = ?",
                (json.dumps({"runs": runs, "autoBugId": bug_id}, ensure_ascii=False), run_id),
            )
            conn.commit()
            result["autoBugId"] = bug_id
    return result


def trend(conn: sqlite3.Connection, scenario_id: int, limit: int = 50) -> dict[str, Any]:
    """历史通过率/耗时趋势（复用执行历史，不另存指标）。"""
    repo = ApiScenarioRunRepository(conn)
    page = repo.list({"scenario_id": scenario_id}, None, page_num=1, page_size=limit)
    points = [
        {
            "runNo": r.get("run_no"), "passed": bool(r.get("passed")),
            "durationMs": int(r.get("duration_ms") or 0),
            "recordCount": int(r.get("record_count") or 1),
            "createdAt": r.get("created_at"),
        }
        for r in reversed(page.list)
    ]
    total = len(points)
    passed = sum(1 for p in points if p["passed"])
    return {"points": points, "total": total, "passRate": round(passed * 100 / total, 1) if total else None}


def _failed_steps(run: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for rec in (run.get("runs") or []):
        for s in (rec.get("steps") or []):
            if not s.get("passed"):
                out.append(s)
    return out


def build_html(scenario: dict[str, Any], run_row: dict[str, Any]) -> str:
    """可分享的单文件 HTML 报告（内联样式，离线可开）。"""
    summary = loads(run_row.get("summary_json"), {}) or {}
    runs = summary.get("runs") or []
    parts = [
        "<!doctype html><html lang=zh><head><meta charset=utf-8>",
        f"<title>场景报告 {escape(str(scenario.get('name') or ''))}</title>",
        "<style>body{font:14px/1.6 system-ui;margin:24px;color:#222}"
        "table{border-collapse:collapse;width:100%;margin:8px 0}"
        "td,th{border:1px solid #ddd;padding:6px 8px;text-align:left;font-size:13px}"
        ".ok{color:#2e7d32}.bad{color:#c62828}.bar{height:8px;background:#409eff;border-radius:4px}"
        "pre{background:#f5f7fa;padding:8px;border-radius:6px;white-space:pre-wrap;word-break:break-all}"
        "</style></head><body>",
        f"<h1>接口场景报告 · {escape(str(scenario.get('name') or ''))}</h1>",
        f"<p>{escape(str(run_row.get('run_no') or ''))} · "
        f"{'通过' if run_row.get('passed') else '未通过'} · {int(run_row.get('duration_ms') or 0)} ms</p>",
    ]
    for ri, rec in enumerate(runs, 1):
        parts.append(f"<h2>数据记录 {ri} · {'通过' if rec.get('passed') else '未通过'}</h2>")
        parts.append("<table><tr><th>步骤</th><th>结果</th><th>HTTP</th><th>耗时</th><th>耗时瀑布</th></tr>")
        durations = [int(s.get("durationMs") or 0) for s in rec.get("steps") or []]
        maxd = max(durations, default=0) or 1
        for s in rec.get("steps") or []:
            d = int(s.get("durationMs") or 0)
            cls = "ok" if s.get("passed") else "bad"
            width = max(2, round(d * 100 / maxd))
            parts.append(
                f"<tr><td>{escape(str(s.get('name') or ''))}</td>"
                f"<td class={cls}>{'通过' if s.get('passed') else '失败'}</td>"
                f"<td>{s.get('statusCode') or '-'}</td><td>{d} ms</td>"
                f"<td><div class=bar style='width:{width}%'></div></td></tr>"
            )
        parts.append("</table>")
        for s in rec.get("steps") or []:
            if not s.get("passed"):
                parts.append(f"<h3 class=bad>失败步骤：{escape(str(s.get('name') or ''))}</h3>")
                for a in s.get("assertions") or []:
                    if not a.get("passed"):
                        parts.append(f"<p class=bad>✗ {escape(str(a.get('name') or ''))}：{escape(str(a.get('message') or ''))}</p>")
                parts.append(f"<pre>{escape(str((s.get('response') or {}).get('body') or '')[:2000])}</pre>")
    parts.append("</body></html>")
    return "".join(parts)


def create_bug_from_run(conn: sqlite3.Connection, scenario: dict[str, Any],
                        run_row: dict[str, Any]) -> dict[str, Any]:
    """失败场景一键转缺陷（复用 biz_bug，不另建表）。"""
    summary = loads(run_row.get("summary_json"), {}) or {}
    failed = _failed_steps(summary)
    lines = [f"场景「{scenario.get('name')}」执行 {run_row.get('run_no')} 未通过。", ""]
    for s in failed:
        lines.append(f"步骤 {s.get('stepNo')} {s.get('name')}：HTTP {s.get('statusCode')}")
        for a in s.get("assertions") or []:
            if not a.get("passed"):
                lines.append(f"  ✗ {a.get('name')}：{a.get('message')}")
    bug_no = next_no(conn, "biz_bug", "bug_no", "BUG")
    from ..repositories.bug_repo import BugRepository
    bug_id = BugRepository(conn).insert({
        "project_id": int(scenario["project_id"]),
        "bug_no": bug_no,
        "title": f"[接口场景] {scenario.get('name')} 执行失败",
        "severity": "P2", "priority": "P2", "status": "open", "bug_type": "interface",
        "description": "\n".join(lines),
        "actual_result": f"{len(failed)} 个步骤失败",
        "reporter": "api-scenario",
    })
    conn.commit()
    return {"bugId": bug_id, "bugNo": bug_no}