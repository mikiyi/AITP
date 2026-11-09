"""GAP-35「失败转缺陷带证据 + 报告下钻」的报告侧门禁。

这一只门只管后半句：报告必须能把「所选失败样本」的执行证据真带出去。
六条断言面逐个钉死，任何一条松了都会红：

  1. 条数诚实：报告里的失败样本条数 == 统计范围内真实失败的轮次用例数。
     总数由本门禁直接数原始表，不借报告的 SQL，也不硬编码；新增一条失败后必须跟着涨。
  2. 每条样本都有证据摘要：结论 / 耗时与时间 / 证据编号 / 下钻地址 / 失败要点都非空，
     且下钻地址真能查到同一条证据。
  3. 三种格式真带样本：md / html / pdf 各自读**产物字节**断言，
     PDF 还要用 pypdf 解析出文本并命中章节关键字与证据编号——三种格式分别渲染，漏一种就红一种。
  4. 没有失败样本时不崩：详情 / 清单 / 预览 / 三种导出全部 200，证据区是中文空态；
     失败但没有证据可下钻时写「无快照」，而不是空白或 500。
  5. 口径同源：缺陷详情的证据与报告里同一条样本必须指向同一个 evidenceId，
     两边摘要文本逐字相等（都出自 execution_protocol.describe_evidence）。
  6. 只有用例级快照（手工执行留下的证据不挂轮次）时，报告样本、runCaseId 下钻、
     缺陷详情三处必须都查到同一条证据——不许「报告说有证据、详情却说没快照」。

只用临时目录里的 sqlite + ``create_app(AppConfig(...)).test_client()``；
失败样本与证据都由真实接口执行产生（execution_protocol 的 capture 落库），不手插表。
不碰正式库、不打正式服务：

    python backend/scripts/check_report_evidence.py
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
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import execution_protocol, report_exporter  # noqa: E402

V1 = "/api/v1"
FAILED = "failed"

#: 导出章节至少要交代的信息（验收标准：样本标识、结论、耗时/时间、证据链接或附件、失败要点）
REQUIRED_SAMPLE_FIELDS = (
    "sampleKey", "resultLabel", "durationMs", "executedAt",
    "evidenceId", "evidenceUrl", "drillUrl", "failurePoints", "evidenceSummary",
)

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Any) -> None:
        self.client = app.test_client()

    def call(
        self, method: str, path: str, payload: dict[str, Any] | None = None
    ) -> tuple[Any, dict[str, Any]]:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(dict[str, Any], body)

    def data(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} 失败: code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def expect(
        self, name: str, method: str, path: str, code: int, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code, f"code={body.get('code')} msg={body.get('message')}")
        return body

    def blob(self, path: str) -> tuple[Any, bytes]:
        resp = self.client.get(path)
        return resp, bytes(resp.get_data())


class Handler(BaseHTTPRequestHandler):
    """本机桩服务：只给确定性的 200 / 500，保证失败样本是真跑出来的。"""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 基类签名
        return

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:  # noqa: N802 - 基类签名
        if self.path.startswith("/ok"):
            self._send(200, {"code": 0, "data": {"id": 1, "name": "alpha"}})
            return
        if self.path.startswith("/bad"):
            self._send(500, {"code": 500, "message": "boom-in-report-gate"})
            return
        self._send(404, {"code": 404})

    def do_POST(self) -> None:  # noqa: N802 - 基类签名
        self.do_GET()


def start_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(cast(tuple[Any, ...], server.server_address)[1])


def build_app(workdir: pathlib.Path) -> Any:
    db_path = workdir / "report_evidence.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8772,
        debug=False,
        db_path=db_path,
        log_path=workdir / "report_evidence.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


# ------------------------------------------------------- 原始表口径（门禁自己独立数一遍）

def real_failed_total(
    conn: sqlite3.Connection, project_id: int, version_id: int | None = None,
    period_start: str = "", period_end: str = "",
) -> int:
    """直接数 biz_test_run_case 里真实失败的行：不借报告的 SQL，也不写死数字。"""
    where = ["rc.is_deleted = 0", "r.is_deleted = 0", "rc.result = ?", "r.project_id = ?"]
    params: list[Any] = [FAILED, project_id]
    if version_id:
        where.append("r.version_id = ?")
        params.append(version_id)
    at = "COALESCE(NULLIF(rc.executed_at, ''), r.created_at)"
    if period_start:
        where.append(f"substr({at}, 1, 10) >= ?")
        params.append(period_start)
    if period_end:
        where.append(f"substr({at}, 1, 10) <= ?")
        params.append(period_end)
    sql = (
        "SELECT COUNT(*) FROM biz_test_run_case rc JOIN biz_test_run r ON r.id = rc.run_id"
        f" WHERE {' AND '.join(where)}"
    )
    return int(conn.execute(sql, params).fetchone()[0])


def real_failed_ids(conn: sqlite3.Connection, project_id: int) -> set[int]:
    rows = conn.execute(
        "SELECT rc.id FROM biz_test_run_case rc JOIN biz_test_run r ON r.id = rc.run_id"
        " WHERE rc.is_deleted = 0 AND r.is_deleted = 0 AND rc.result = ? AND r.project_id = ?",
        (FAILED, project_id),
    ).fetchall()
    return {int(row[0]) for row in rows}


# --------------------------------------------------------------------- 种子数据与取数口

def make_interface_case(api: Api, project_id: int, title: str, path: str) -> int:
    return int(api.data("POST", f"{V1}/test-cases", {
        "projectId": project_id, "title": title, "caseType": "interface", "priority": "P1",
        "extra": {"接口URL": path, "请求方式": "GET", "预期状态码": "200",
                  "断言规则": "json:$.code = 0"},
    })["id"])


def run_interface_cases(
    api: Api, project_id: int, name: str, case_ids: list[int], env_id: int,
    version_id: int | None = None,
) -> list[dict[str, Any]]:
    """真实发起一轮接口执行：证据由 execution_protocol 落库，绝不手插表。"""
    payload: dict[str, Any] = {"projectId": project_id, "name": name, "caseIds": case_ids,
                               "envId": env_id}
    if version_id:
        payload["versionId"] = version_id
    run_id = int(api.data("POST", f"{V1}/test-runs", payload)["id"])
    rows = run_case_rows(api, run_id)
    results: list[dict[str, Any]] = []
    for row in rows:
        run_case_id = int(row["id"])
        detail = api.data(
            "POST", f"{V1}/test-runs/{run_id}/cases/{run_case_id}/interface-run", {"envId": env_id}
        )
        results.append({
            "runId": run_id,
            "runCaseId": run_case_id,
            "result": str(detail.get("result") or row.get("result") or ""),
            "evidenceId": detail.get("evidenceId"),
        })
    return results


def run_case_rows(api: Api, run_id: int) -> list[dict[str, Any]]:
    listed = api.data("GET", f"{V1}/test-runs/{run_id}/cases")
    rows = listed.get("list") if isinstance(listed, dict) else listed
    return [dict(item) for item in (rows or []) if isinstance(item, dict)]


def make_report(api: Api, project_id: int, title: str, **extra: Any) -> int:
    payload: dict[str, Any] = {"projectId": project_id, "title": title}
    payload.update(extra)
    return int(api.data("POST", f"{V1}/reports", payload)["id"])


def samples_of(
    api: Api, report_id: int, run_case_ids: list[int] | None = None
) -> dict[str, Any]:
    query = f"?runCaseIds={','.join(str(i) for i in run_case_ids)}" if run_case_ids is not None else ""
    return cast(dict[str, Any], api.data("GET", f"{V1}/reports/{report_id}/failure-samples{query}"))


def by_run_case(payload: dict[str, Any]) -> dict[int, dict[str, Any]]:
    return {int(item["runCaseId"]): item for item in payload["list"]}


def export(
    api: Api, report_id: int, fmt: str, run_case_ids: list[int] | None = None
) -> tuple[Any, bytes]:
    query = f"?format={fmt}"
    if run_case_ids is not None:
        query += "&runCaseIds=" + ",".join(str(i) for i in run_case_ids)
    return api.blob(f"{V1}/reports/{report_id}/export{query}")


def pdf_text(payload: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(payload))
    return "\n".join(str(page.extract_text() or "") for page in reader.pages)


def squeeze(text: str) -> str:
    """PDF 抽出来的中文常夹空格：断言前把空白压掉，关键字匹配才稳。"""
    return re.sub(r"\s+", "", text)


def ev_numbers(text: str) -> set[str]:
    return set(re.findall(r"EV-\d+", text))


# ------------------------------------------------------- 断言面 ①：条数与取样范围诚实

def section_counts(
    api: Api, conn: sqlite3.Connection, project_id: int, report_id: int, env_id: int,
    executed: list[dict[str, Any]], version_report_id: int, version_id: int,
) -> int:
    expected = real_failed_total(conn, project_id)
    payload = samples_of(api, report_id)
    check("失败样本条数等于真实失败执行记录数", payload["total"] == expected,
          f"报告 {payload['total']} vs 原始表 {expected}")
    check("样本清单条数与总数一致（未超渲染上限时）", len(payload["list"]) == expected,
          f"{len(payload['list'])} vs {expected}")
    detail = api.data("GET", f"{V1}/reports/{report_id}")
    check("报告详情的 failureTotal 与清单同源", detail["failureTotal"] == expected,
          f"{detail['failureTotal']} vs {expected}")
    check("报告详情带失败样本清单", isinstance(detail.get("failureSamples"), list)
          and len(detail["failureSamples"]) == expected, str(detail.get("failureSamples"))[:200])

    listed_ids = {int(item["runCaseId"]) for item in payload["list"]}
    check("清单里每条样本都是原始表里真实失败的行",
          listed_ids == real_failed_ids(conn, project_id),
          f"对称差 {sorted(listed_ids ^ real_failed_ids(conn, project_id))}")
    passed_ids = {int(item["runCaseId"]) for item in executed if item["result"] != FAILED}
    check("通过的执行没有混进失败样本（正向对照）", not (listed_ids & passed_ids),
          f"{sorted(listed_ids & passed_ids)}")
    wrong_result = [
        str(item["sampleKey"]) for item in payload["list"]
        if str(conn.execute("SELECT result FROM biz_test_run_case WHERE id = ?",
                            (int(item["runCaseId"]),)).fetchone()[0]) != FAILED
    ]
    check("样本可逐条回查原始表且结论确为失败", not wrong_result, str(wrong_result)[:250])

    # 再跑一次真实失败：条数必须跟着涨，坐实「不是硬编码」
    extra_case = make_interface_case(api, project_id, "报告门禁新增失败用例", "/bad")
    added = run_interface_cases(api, project_id, "报告门禁增量轮次", [extra_case], env_id)
    grown = samples_of(api, report_id)["total"]
    expected_after = real_failed_total(conn, project_id)
    check("新增一次真实失败后报告条数同步上涨",
          grown == expected_after == expected + 1,
          f"报告 {grown} / 原始表 {expected_after} / 原 {expected}")
    check("涨出来的那条就是新执行的失败",
          by_run_case(samples_of(api, report_id)).get(int(added[0]["runCaseId"])) is not None,
          str(added)[:200])

    # 版本范围：只该数挂到该版本的轮次
    version_expected = real_failed_total(conn, project_id, version_id=version_id)
    version_payload = samples_of(api, version_report_id)
    check("版本范围的报告只取该版本的失败样本", version_payload["total"] == version_expected,
          f"{version_payload['total']} vs {version_expected}")
    check("版本范围确实窄于全量（正向对照）", 0 < version_expected < grown,
          f"版本 {version_expected} / 全量 {grown}")
    check("版本报告的取数范围回显正确", version_payload["scope"]["versionId"] == version_id,
          str(version_payload["scope"])[:200])
    return int(grown)


# --------------------------------------------------- 断言面 ②：每条样本都要有证据摘要

def section_summaries(api: Api, report_id: int) -> None:
    payload = samples_of(api, report_id)
    samples = payload["list"]

    missing_fields = [
        f"{item.get('sampleKey')}.{field}"
        for item in samples for field in REQUIRED_SAMPLE_FIELDS
        if item.get(field) in (None, "", [])
    ]
    check("每条样本的证据字段都非空", not missing_fields, str(missing_fields)[:400])

    bad_summary = [
        str(item["sampleKey"]) for item in samples
        if f"EV-{item['evidenceId']}" not in str(item.get("evidenceSummary") or "")
    ]
    check("每条样本都取到带证据编号的执行证据摘要", not bad_summary, str(bad_summary)[:300])

    bad_protocol = [
        str(item["sampleKey"]) for item in samples
        if str(item.get("evidenceSummary")) != execution_protocol.describe_evidence(
            cast(dict[str, Any], api.data("GET", str(item["drillUrl"])))
        )
    ]
    check("样本摘要与证据协议摘要同一口径", not bad_protocol, str(bad_protocol)[:300])

    bad_points = [str(item["sampleKey"]) for item in samples if not (item.get("failurePoints") or [])]
    check("每条样本都有失败要点", not bad_points, str(bad_points)[:300])
    bad_result = [str(item["sampleKey"]) for item in samples if item.get("resultLabel") != "失败"]
    check("每条样本的结论都是「失败」", not bad_result, str(bad_result)[:300])
    bad_timing = [str(item["sampleKey"]) for item in samples if not str(item.get("executedAt") or "")]
    check("每条样本都有执行时间", not bad_timing, str(bad_timing)[:300])
    bad_total = [str(item["sampleKey"]) for item in samples if not item.get("failureTotal")]
    check("每条样本都回带全量条数（供章节写「共 N 条」）", not bad_total, str(bad_total)[:300])
    bad_drill = []
    for item in samples:
        fetched = api.data("GET", f"{V1}/test-runs/evidence?runCaseId={item['runCaseId']}") or {}
        if fetched.get("evidenceId") != item["evidenceId"]:
            bad_drill.append(f"{item['sampleKey']}->{fetched.get('evidenceId')}")
    check("按 runCaseId 查到的证据与样本一致", not bad_drill, str(bad_drill)[:300])

    section = str(payload.get("evidenceSection") or "")
    rows = len(re.findall(r"^\| `run-case:", section, flags=re.MULTILINE))
    check("证据章节为每条样本各出一行", rows == len(samples), f"{rows} vs {len(samples)}")
    check("章节口径与样本清单同出一份",
          section.strip() == report_exporter.failure_markdown_section(samples).strip(), section[:200])
    check("清单接口回报有证据的样本数",
          payload["evidenceCount"] == sum(1 for item in samples if item["hasEvidence"]),
          str(payload)[:200])


# ----------------------------------------------- 断言面 ③：md / html / pdf 都真带样本

def check_formats(
    name: str, api: Api, report_id: int, chosen: list[dict[str, Any]], excluded: list[dict[str, Any]]
) -> None:
    keys = [str(item["sampleKey"]) for item in chosen]
    numbers = {f"EV-{item['evidenceId']}" for item in chosen}
    excluded_numbers = {f"EV-{item['evidenceId']}" for item in excluded}
    ids = [int(item["runCaseId"]) for item in chosen]

    md_resp, md = export(api, report_id, "md", ids)
    md_text = md.decode("utf-8", errors="replace")
    check(f"{name}：md 产物含「失败样本执行证据」章节",
          report_exporter.FAILURE_SECTION_LABEL in md_text, f"{len(md_text)} bytes / {md_text[:150]}")
    check(f"{name}：md 产物含全部所选样本标识",
          not [key for key in keys if key not in md_text], str(keys)[:300])
    check(f"{name}：md 产物含全部所选证据编号", numbers <= ev_numbers(md_text),
          f"{sorted(numbers - ev_numbers(md_text))}")
    check(f"{name}：md 产物没有带进未选样本的证据", not (excluded_numbers & ev_numbers(md_text)),
          f"{sorted(excluded_numbers & ev_numbers(md_text))}")
    check(f"{name}：md 产物写出结论 / 耗时 / 要点栏目",
          "样本标识" in md_text and "耗时 / 执行时间" in md_text and "失败要点" in md_text,
          md_text[:200])

    html_resp, html_raw = export(api, report_id, "html", ids)
    html_text = html_raw.decode("utf-8", errors="replace")
    check(f"{name}：html 产物含「失败样本执行证据」章节",
          report_exporter.FAILURE_SECTION_LABEL in html_text, f"{len(html_text)} bytes")
    check(f"{name}：html 产物含全部所选样本标识",
          not [key for key in keys if key not in html_text], str(keys)[:300])
    check(f"{name}：html 产物含全部所选证据编号", numbers <= ev_numbers(html_text),
          f"{sorted(numbers - ev_numbers(html_text))}")
    check(f"{name}：html 产物给出可点的证据下钻链接",
          all(str(item["drillUrl"]) in html_text for item in chosen),
          str([item["drillUrl"] for item in chosen])[:300])
    check(f"{name}：html 产物没有带进未选样本的证据", not (excluded_numbers & ev_numbers(html_text)),
          f"{sorted(excluded_numbers & ev_numbers(html_text))}")

    pdf_resp, pdf_raw = export(api, report_id, "pdf", ids)
    check(f"{name}：pdf 产物是真 PDF 字节", pdf_raw[:4] == b"%PDF" and len(pdf_raw) > 1500,
          f"{pdf_raw[:8]!r} / {len(pdf_raw)} bytes")
    text = pdf_text(pdf_raw)
    tight = squeeze(text)
    check(f"{name}：pdf 解析出「失败样本执行证据」文本",
          squeeze(report_exporter.FAILURE_SECTION_LABEL) in tight, text[:200])
    check(f"{name}：pdf 解析出全部所选样本标识",
          not [key for key in keys if squeeze(key) not in tight], str(keys)[:300])
    check(f"{name}：pdf 解析出全部所选证据编号", numbers <= ev_numbers(text),
          f"{sorted(numbers - ev_numbers(text))} / {len(text)} chars")
    check(f"{name}：pdf 没有带进未选样本的证据", not (excluded_numbers & ev_numbers(text)),
          f"{sorted(excluded_numbers & ev_numbers(text))}")
    check(f"{name}：pdf 解析出结论与下钻栏目",
          "失败要点" in tight and "证据下钻" in tight and "耗时" in tight, text[:250])

    for fmt, resp in (("md", md_resp), ("html", html_resp), ("pdf", pdf_resp)):
        check(f"{name}：{fmt} 导出头部回报样本数",
              str(resp.headers.get("X-AITP-Failure-Samples") or "") == str(len(chosen)),
              str(resp.headers.get("X-AITP-Failure-Samples")))

    joined = ",".join(str(i) for i in ids)
    for fmt in ("md", "html"):
        preview = api.data("GET", f"{V1}/reports/{report_id}/preview?format={fmt}&runCaseIds={joined}")
        content = str(preview["content"])
        check(f"{name}：{fmt} 预览同样带出所选样本证据",
              all(key in content for key in keys) and numbers <= ev_numbers(content)
              and report_exporter.FAILURE_SECTION_LABEL in content, content[:200])


def section_exports(api: Api, report_id: int) -> None:
    payload = samples_of(api, report_id)
    full = payload["list"]
    chosen, excluded = full[:2], full[2:]
    check("导出断言备好 2 条所选与至少 1 条未选", len(chosen) == 2 and len(excluded) >= 1,
          f"{len(full)} 条")

    keys = [str(item["sampleKey"]) for item in full]
    numbers = {f"EV-{item['evidenceId']}" for item in full}
    _, md = export(api, report_id, "md")
    md_text = md.decode("utf-8", errors="replace")
    check("未做选择时 md 全量带出每条失败样本",
          all(key in md_text for key in keys) and numbers <= ev_numbers(md_text),
          f"{len(md)} bytes / {sorted(numbers - ev_numbers(md_text))}")
    _, html_raw = export(api, report_id, "html")
    html_text = html_raw.decode("utf-8", errors="replace")
    check("未做选择时 html 全量带出每条失败样本",
          all(key in html_text for key in keys) and numbers <= ev_numbers(html_text),
          f"{len(html_raw)} bytes / {sorted(numbers - ev_numbers(html_text))}")
    _, pdf_raw = export(api, report_id, "pdf")
    pdf_full = pdf_text(pdf_raw)
    check("未做选择时 pdf 全量带出每条失败样本",
          all(squeeze(key) in squeeze(pdf_full) for key in keys) and numbers <= ev_numbers(pdf_full),
          f"{len(pdf_raw)} bytes / {sorted(numbers - ev_numbers(pdf_full))}")
    check("未做选择时章节标题写出真实条数", f"（{len(full)} 条）" in md_text, md_text[:200])
    _, print_raw = export(api, report_id, "print")
    check("打印视图也带样本证据",
          all(key in print_raw.decode("utf-8", errors="replace") for key in keys)
          and numbers <= ev_numbers(print_raw.decode("utf-8", errors="replace")),
          f"{len(print_raw)} bytes")

    saved = api.data("POST", f"{V1}/reports/{report_id}/failure-samples",
                     {"runCaseIds": [int(item["runCaseId"]) for item in chosen]})
    check("保存所选后清单只剩所选但总数仍是真实全量",
          len(saved["list"]) == 2 and saved["total"] == len(full), str(saved)[:250])
    detail = api.data("GET", f"{V1}/reports/{report_id}")
    check("所选样本 id 集合保存后原样回显",
          {int(item) for item in saved["selected"]}
          == {int(item["runCaseId"]) for item in chosen}, str(saved["selected"])[:200])
    check("报告详情沿用保存下来的所选样本", len(detail["failureSamples"]) == 2,
          str(detail["failureSamples"])[:200])
    check("所选之外仍回报真实失败总数", detail["failureTotal"] == len(full),
          f"{detail['failureTotal']} vs {len(full)}")
    check_formats("所选子集", api, report_id, chosen, excluded)
    section = str(saved.get("evidenceSection") or "")
    check("所选时章节写明「共 N 条，以下 M 条」",
          f"共 {len(full)} 条，以下 {len(chosen)} 条" in section, section[:250])

    restored = api.data("POST", f"{V1}/reports/{report_id}/failure-samples", {"runCaseIds": []})
    check("清空选择后回到全量口径", len(restored["list"]) == len(full), str(restored)[:200])
    print(f"[INFO] 全量导出体积：md {len(md)} / html {len(html_raw)} / pdf {len(pdf_raw)} bytes")


# ----------------------------------------- 断言面 ④：没有失败样本 / 有失败但没证据

def section_period_scope(api: Api, conn: sqlite3.Connection, project_id: int, total: int) -> None:
    wide = make_report(api, project_id, "报告门禁宽区间报告",
                       periodStart="2000-01-01", periodEnd="2099-12-31")
    wide_payload = samples_of(api, wide)
    check("宽区间报告条数等于全量",
          wide_payload["total"] == total == real_failed_total(
              conn, project_id, period_start="2000-01-01", period_end="2099-12-31"),
          f"{wide_payload['total']} vs {total}")
    old = make_report(api, project_id, "报告门禁历史区间报告",
                      periodStart="2000-01-01", periodEnd="2000-12-31")
    old_payload = samples_of(api, old)
    check("历史区间没有失败执行时条数为 0", old_payload["total"] == 0 and old_payload["list"] == [],
          str(old_payload)[:200])
    check("历史区间报告同样给中文空态提示",
          "没有失败样本" in str(old_payload.get("hint") or ""), str(old_payload.get("hint")))


def section_empty(api: Api, conn: sqlite3.Connection, project_id: int, report_id: int) -> None:
    check("无失败项目原始表确实没有失败行", real_failed_total(conn, project_id) == 0,
          str(real_failed_ids(conn, project_id)))
    detail = api.data("GET", f"{V1}/reports/{report_id}")
    check("无失败样本时报告详情正常返回",
          detail["failureTotal"] == 0 and detail["failureSamples"] == [], str(detail)[:250])
    check("无失败样本时报告详情给空态提示",
          "没有失败样本" in str(detail.get("evidenceHint") or ""), str(detail.get("evidenceHint")))
    payload = samples_of(api, report_id)
    check("无失败样本时清单接口返回空数组而不是报错",
          payload["total"] == 0 and payload["list"] == [] and payload["evidenceCount"] == 0,
          str(payload)[:250])
    check("无失败样本时证据章节是 0 条空态",
          "（0 条）" in str(payload["evidenceSection"])
          and report_exporter.FAILURE_EMPTY_TEXT in str(payload["evidenceSection"]),
          str(payload["evidenceSection"])[:200])

    for fmt in ("md", "html", "print"):
        resp, raw = export(api, report_id, fmt)
        text = raw.decode("utf-8", errors="replace")
        check(f"无失败样本时 {fmt} 导出不崩且是空态章节",
              resp.status_code == 200 and report_exporter.FAILURE_EMPTY_TEXT in text
              and not ev_numbers(text), f"{resp.status_code} / {text[:150]}")
    resp, raw = export(api, report_id, "pdf")
    text = pdf_text(raw)
    check("无失败样本时 pdf 导出不崩且是空态章节",
          resp.status_code == 200 and raw[:4] == b"%PDF"
          and squeeze(report_exporter.FAILURE_EMPTY_TEXT) in squeeze(text) and not ev_numbers(text),
          f"{resp.status_code} / {len(raw)} bytes / {text[:120]}")
    preview = api.data("GET", f"{V1}/reports/{report_id}/preview?format=md")
    check("无失败样本时预览正常且是空态",
          report_exporter.FAILURE_EMPTY_TEXT in str(preview["content"])
          and preview["failureTotal"] == 0 and preview["failureSamples"] == [], str(preview)[:200])


def section_no_evidence(api: Api, report_id: int) -> None:
    payload = samples_of(api, report_id)
    samples = payload["list"]
    check("手工判失败的样本仍然进报告", len(samples) == 1, str(samples)[:250])
    sample = samples[0] if samples else {}
    check("没有证据的样本标记 hasEvidence 为假且没有证据编号",
          sample.get("hasEvidence") is False and sample.get("evidenceId") is None, str(sample)[:250])
    check("没有证据的样本仍给出失败要点", bool(sample.get("failurePoints")), str(sample)[:250])
    check("没有证据就不给下钻地址（不给一条查不到东西的链接）",
          not sample.get("evidenceUrl") and not sample.get("drillUrl"), str(sample)[:250])
    check("没有证据的样本章节写明没有可下钻快照",
          report_exporter.FAILURE_NO_EVIDENCE_TEXT in str(payload["evidenceSection"]),
          str(payload["evidenceSection"])[:250])
    _, md = export(api, report_id, "md")
    md_text = md.decode("utf-8", errors="replace")
    check("无证据样本的 md 导出不崩且写明无快照",
          "无快照" in md_text and report_exporter.FAILURE_SECTION_LABEL in md_text, md_text[:250])
    _, html_raw = export(api, report_id, "html")
    check("无证据样本的 html 导出不崩且写明无快照",
          "无快照" in html_raw.decode("utf-8", errors="replace"), f"{len(html_raw)} bytes")
    resp, raw = export(api, report_id, "pdf")
    check("无证据样本的 pdf 导出不崩且解析得出无快照",
          resp.status_code == 200 and raw[:4] == b"%PDF"
          and "无快照" in squeeze(pdf_text(raw)), f"{resp.status_code} / {len(raw)} bytes")


# ------------------------------------------------ 断言面 ⑤：缺陷详情与报告样本口径同源

def section_same_source(
    api: Api, run_id: int, run_case_id: int, report_id: int
) -> None:
    bug = api.data("POST", f"{V1}/test-runs/{run_id}/cases/{run_case_id}/bug", {})
    bug_id = int(bug["bugId"])
    bug_evidence = api.data("GET", f"{V1}/bugs/{bug_id}/evidence")
    latest = cast(dict[str, Any], bug_evidence.get("latest") or {})
    sample = by_run_case(samples_of(api, report_id)).get(run_case_id) or {}
    check("缺陷与报告样本按 runCaseId 都能对上（不靠列表下标）",
          bool(latest) and bool(sample), f"缺陷 {bool(latest)} / 样本 {bool(sample)}")
    check("缺陷详情的证据与报告样本指向同一条证据",
          int(latest.get("evidenceId") or -1) == int(sample.get("evidenceId") or -2),
          f"缺陷 EV-{latest.get('evidenceId')} vs 报告 EV-{sample.get('evidenceId')}")
    check("两侧证据编号字面一致",
          f"EV-{latest.get('evidenceId')}" == f"EV-{sample.get('evidenceId')}",
          f"{latest.get('evidenceId')} / {sample.get('evidenceId')}")
    bug_summary = execution_protocol.describe_evidence(latest)
    check("两侧证据摘要逐字相同（同一段口径）",
          bug_summary == str(sample.get("evidenceSummary")),
          f"{bug_summary[:120]} || {str(sample.get('evidenceSummary'))[:120]}")
    bug_detail = api.data("GET", f"{V1}/bugs/{bug_id}")
    check("缺陷描述里的证据编号就是报告样本那条",
          f"EV-{sample.get('evidenceId')}" in str(bug_detail.get("description") or ""),
          str(bug_detail.get("description"))[:250])
    drilled = cast(dict[str, Any], api.data("GET", str(sample.get("drillUrl"))))
    check("从报告样本下钻拿到的证据与缺陷侧同源",
          int(drilled["evidenceId"]) == int(latest["evidenceId"])
          and drilled.get("sourceType") == latest.get("sourceType")
          and drilled.get("sourceRowId") == latest.get("sourceRowId"), str(drilled)[:200])
    check("报告样本反向回显缺陷号",
          str(sample.get("bugNo")) == str(bug["bugNo"]) and int(sample.get("bugId") or 0) == bug_id,
          str(sample)[:250])
    _, md = export(api, report_id, "md", [run_case_id])
    md_text = md.decode("utf-8", errors="replace")
    check("导出里的证据编号与缺陷侧一致",
          f"EV-{sample.get('evidenceId')}" in md_text and bug_summary in md_text, md_text[:250])
    _, pdf_raw = export(api, report_id, "pdf", [run_case_id])
    check("pdf 里的证据编号与缺陷侧一致",
          f"EV-{sample.get('evidenceId')}" in pdf_text(pdf_raw), f"{len(pdf_raw)} bytes")


# ------------------------------------------------------------- 渲染上限与接口契约

def section_cap(api: Api, conn: sqlite3.Connection, project_id: int, report_id: int) -> None:
    shown_count = 2
    original = report_exporter.MAX_FAILURE_SAMPLES
    try:
        report_exporter.MAX_FAILURE_SAMPLES = shown_count
        total = real_failed_total(conn, project_id)
        payload = samples_of(api, report_id)
        shown = payload["list"][:shown_count]
        hidden = payload["list"][shown_count:]
        check("上限用例的失败样本多于渲染上限", total > shown_count, f"total={total}")
        shown_numbers = {f"EV-{item['evidenceId']}" for item in shown}
        hidden_numbers = {f"EV-{item['evidenceId']}" for item in hidden}

        _, md = export(api, report_id, "md")
        md_text = md.decode("utf-8", errors="replace")
        _, html_raw = export(api, report_id, "html")
        html_text = html_raw.decode("utf-8", errors="replace")
        pdf_raw = export(api, report_id, "pdf")[1]
        tight = squeeze(pdf_text(pdf_raw))

        check("超上限时 md 只渲染上限条数",
              len(re.findall(r"^\| `run-case:", md_text, flags=re.MULTILINE)) == shown_count,
              md_text[:250])
        check("超上限时 html 只渲染上限条数",
              html_text.count("<code>run-case:") == shown_count,
              str(html_text.count("<code>run-case:")))
        pdf_keys = set(re.findall(r"run-case:\d+", tight))
        shown_keys = {squeeze(str(item["sampleKey"])) for item in shown}
        check("超上限时 pdf 只带上限条数的样本标识",
              pdf_keys == shown_keys, f"{sorted(pdf_keys)} / {sorted(shown_keys)} / {tight[:160]}")
        check("超上限时三种格式都不丢未渲染样本之外的证据口径",
              all(f"EV-{item['evidenceId']}" in md_text for item in shown)
              and all(squeeze(item["sampleKey"]) in tight for item in shown), str(shown)[:200])
        check("超上限时未渲染样本的证据编号不会混进产物",
              not (hidden_numbers & ev_numbers(md_text)) and not (hidden_numbers & ev_numbers(html_text))
              and not (hidden_numbers & ev_numbers(pdf_text(pdf_raw))),
              f"{sorted(hidden_numbers & ev_numbers(md_text))}")
        check("超上限时三种格式都写明真实总数",
              f"共 {total} 条" in md_text and f"共 {total} 条" in html_text
              and f"共{total}条" in tight, f"{total}")
        check("渲染上限不改变接口回报的真实条数", payload["total"] == total, str(payload)[:200])
    finally:
        report_exporter.MAX_FAILURE_SAMPLES = original


def section_contract(api: Api, conn: sqlite3.Connection, project_id: int, report_id: int) -> None:
    detail = api.data("GET", f"{V1}/reports/{report_id}")
    samples = detail["failureSamples"]
    snake = [key for item in samples for key in item if "_" in str(key)]
    check("样本字段全部是驼峰命名", not snake, str(snake)[:250])
    check("样本字段覆盖导出要求的信息",
          all(field in item for item in samples for field in REQUIRED_SAMPLE_FIELDS),
          str(sorted(samples[0]))[:400])
    check("报告详情带齐证据相关字段",
          all(field in detail for field in
              ("failureTotal", "failureSamples", "failureSelected", "evidenceHint", "evidenceSection")),
          str(sorted(detail))[:400])
    section = str(detail["evidenceSection"])
    check("证据章节交代样本标识 / 结论 / 耗时时间 / 证据 / 要点",
          all(text in section for text in
              ("样本标识", "结论", "耗时 / 执行时间", "失败要点", "EV-")), section[:250])

    api.expect("报告不存在时清单接口报 404", "GET", f"{V1}/reports/999999/failure-samples",
               ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("报告不存在时预览报 404", "GET", f"{V1}/reports/999999/preview",
               ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("报告不存在时导出报 404 而不是 500", "GET", f"{V1}/reports/999999/export?format=pdf",
               ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("runCaseIds 不是数组时报参数错", "POST", f"{V1}/reports/{report_id}/failure-samples",
               ErrorCode.PARAM_ERROR, {"runCaseIds": 3})
    api.expect("越界样本 id 保存时报参数错并说明原因", "POST",
               f"{V1}/reports/{report_id}/failure-samples", ErrorCode.PARAM_ERROR,
               {"runCaseIds": [999999]})
    _, body = api.call("GET", f"{V1}/reports/{report_id}/export?format=xml")
    check("非法导出格式报参数错而不是 500", body.get("code") == ErrorCode.PARAM_ERROR, str(body)[:200])
    resp, _raw = api.call("GET", f"{V1}/reports/{report_id}/export?format=pdf")
    check("pdf 导出在缺中文字体时也不 500", resp.status_code == 200, str(resp.status_code))

    other = api.data("POST", f"{V1}/projects", {"code": "REPX", "name": "报告门禁隔壁项目"})
    foreign_report = make_report(api, int(other["id"]), "隔壁项目的报告")
    foreign = samples_of(api, foreign_report)
    check("跨项目不串失败样本（正向对照）", foreign["total"] == 0, str(foreign)[:200])
    check("隔壁项目报告的取样范围指向自己",
          int(foreign["scope"]["projectId"]) == int(other["id"]), str(foreign["scope"])[:200])
    check("原项目条数不受隔壁项目报告影响",
          samples_of(api, report_id)["total"] == real_failed_total(conn, project_id),
          str(samples_of(api, report_id))[:200])


# ------------------------------------ 断言面 ⑥：证据只在用例级时三处读口必须口径一致

def section_case_level_fallback(api: Api, conn: sqlite3.Connection) -> None:
    """手工执行留下的证据只挂用例（run_case_id 为空），三处读口不许各说一套。

    报告样本、runCaseId 下钻、缺陷详情必须都落到同一条快照上：
    只要有一处仍按「轮次级才有证据」判断，界面就会出现「报告说 EV-3、详情说无快照」。
    """
    project = api.data("POST", f"{V1}/projects", {"code": "REPE", "name": "报告用例级快照项目"})
    project_id = int(project["id"])
    case_id = int(api.data("POST", f"{V1}/test-cases", {
        "projectId": project_id, "title": "报告门禁手工留痕用例", "caseType": "functional",
    })["id"])
    executed = api.data("POST", f"{V1}/test-cases/{case_id}/execute", {
        "result": "fail", "executedBy": "gate-manual",
        "logSummary": "手工留痕：登录后首页白屏，控制台报 500",
    })
    check("用例级手工执行留下可下钻的证据快照", bool(executed.get("evidenceId")), str(executed)[:200])
    evidence_id = int(executed.get("evidenceId") or 0)
    row = conn.execute(
        "SELECT run_case_id, case_id, run_id FROM biz_execution_evidence WHERE id = ?",
        (evidence_id,),
    ).fetchone()
    check("这条快照确实只挂用例、不挂轮次",
          row is not None and not row["run_case_id"] and row["case_id"], str(row)[:200])

    run_id = int(api.data("POST", f"{V1}/test-runs", {
        "projectId": project_id, "name": "报告门禁用例级快照轮次", "caseIds": [case_id],
    })["id"])
    run_case_id = int(run_case_rows(api, run_id)[0]["id"])
    api.data("PUT", f"{V1}/test-runs/{run_id}/cases/{run_case_id}",
             {"result": FAILED, "actualResult": "轮次里同样判失败", "executedBy": "gate"})
    report_id = make_report(api, project_id, "报告门禁用例级快照报告")
    sample = by_run_case(samples_of(api, report_id)).get(run_case_id) or {}
    check("只有用例级快照的失败样本仍算「有证据」",
          sample.get("hasEvidence") is True and int(sample.get("evidenceId") or -1) == evidence_id,
          str(sample)[:250])

    drilled = cast(dict[str, Any], api.data("GET", f"{V1}/test-runs/evidence?runCaseId={run_case_id}") or {})
    check("轮次用例下钻取到同一条用例级快照", drilled.get("evidenceId") == evidence_id, str(drilled)[:200])
    by_case = cast(dict[str, Any], api.data("GET", f"{V1}/test-runs/evidence?caseId={case_id}") or {})
    check("用例定位与轮次定位是同一份证据",
          by_case.get("evidenceId") == drilled.get("evidenceId"), str(by_case)[:200])
    drill_url = str(sample.get("drillUrl") or "")
    by_source = cast(dict[str, Any], api.data("GET", drill_url) or {}) if drill_url else {}
    check("样本自带下钻地址与按轮次查到的同一条",
          int(by_source.get("evidenceId") or -1) == evidence_id, str(by_source)[:200])

    bug = api.data("POST", f"{V1}/test-runs/{run_id}/cases/{run_case_id}/bug", {})
    bug_id = int(bug["bugId"])
    bug_evidence = api.data("GET", f"{V1}/bugs/{bug_id}/evidence")
    latest = cast(dict[str, Any], bug_evidence.get("latest") or {})
    check("缺陷详情也查得到这条用例级快照",
          int(latest.get("evidenceId") or -1) == evidence_id and bug_evidence.get("total") == 1,
          str(bug_evidence)[:250])
    check("缺陷侧摘要与报告样本摘要同一口径",
          execution_protocol.describe_evidence(latest) == str(sample.get("evidenceSummary")),
          f"{str(latest)[:120]} || {str(sample.get('evidenceSummary'))[:120]}")
    check("缺陷列表回报的证据计数不是 0",
          int(api.data("GET", f"{V1}/bugs/{bug_id}").get("evidenceCount") or 0) >= 1,
          str(api.data("GET", f"{V1}/bugs/{bug_id}"))[:200])

    _, md = export(api, report_id, "md")
    md_text = md.decode("utf-8", errors="replace")
    check("用例级快照同样进 md 导出章节",
          f"EV-{evidence_id}" in ev_numbers(md_text)
          and squeeze(str(sample.get("evidenceSummary"))) in squeeze(md_text), md_text[:250])


def section_migration(conn: sqlite3.Connection) -> None:
    tables = {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    check("证据表由迁移脚本建好（门禁不手插表）", "biz_execution_evidence" in tables,
          str(sorted(tables))[:200])
    columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(biz_execution_evidence)")}
    check("报告取样依赖的证据字段齐备",
          {"source_type", "source_row_id", "run_case_id", "case_id", "expires_at"} <= columns,
          str(sorted(columns))[:300])


# ------------------------------------------------------------------------- 主流程

def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-report-evidence-"))
    server, port = start_server()
    try:
        app = build_app(workdir)
        api = Api(app)
        conn = sqlite3.connect(workdir / "report_evidence.db")
        conn.row_factory = sqlite3.Row
        base = f"http://127.0.0.1:{port}"

        section_migration(conn)

        env = api.data("POST", f"{V1}/test-envs", {
            "name": "报告证据环境", "kind": "test",
            "app": {"baseUrl": base}, "db": {"type": "sqlite", "host": ""},
        })
        env_id = int(env["id"])

        # 项目 A：真实失败 + 真实执行证据
        project = api.data("POST", f"{V1}/projects", {"code": "REPA", "name": "报告证据项目"})
        project_id = int(project["id"])
        case_bad1 = make_interface_case(api, project_id, "报告门禁失败用例一", "/bad")
        case_bad2 = make_interface_case(api, project_id, "报告门禁失败用例二", "/bad")
        case_ok = make_interface_case(api, project_id, "报告门禁通过用例", "/ok")
        executed = run_interface_cases(api, project_id, "报告门禁主轮次",
                                       [case_bad1, case_bad2, case_ok], env_id)
        failing = [item for item in executed if item["result"] == FAILED]
        check("种子数据真跑出 2 条失败", len(failing) == 2, str(executed)[:250])
        check("种子的失败执行都留下证据 id",
              all(item["evidenceId"] for item in failing), str(executed)[:250])

        report_id = int(api.data("POST", f"{V1}/reports/generate",
                                 {"projectId": project_id, "title": "报告门禁主报告"})["id"])
        version = api.data("POST", f"{V1}/project-versions", {
            "projectId": project_id, "versionCode": "V1.0.0", "name": "报告门禁版本",
        })
        version_id = int(version["id"])
        run_interface_cases(api, project_id, "报告门禁版本轮次", [case_bad2], env_id, version_id)
        version_report_id = make_report(api, project_id, "报告门禁版本报告", versionId=version_id)

        total = section_counts(api, conn, project_id, report_id, env_id, executed,
                               version_report_id, version_id)
        section_summaries(api, report_id)
        section_period_scope(api, conn, project_id, total)
        section_contract(api, conn, project_id, report_id)
        section_exports(api, report_id)

        # 缺陷 ↔ 报告：同一条证据（按 runCaseId 对齐，不靠下标）
        section_same_source(api, int(failing[0]["runId"]), int(failing[0]["runCaseId"]), report_id)

        # 项目 B：一次失败都没有 → 全链路空态
        project_b = api.data("POST", f"{V1}/projects", {"code": "REPB", "name": "报告无失败项目"})
        project_b_id = int(project_b["id"])
        ok_case = make_interface_case(api, project_b_id, "报告门禁只过用例", "/ok")
        run_interface_cases(api, project_b_id, "报告门禁只过轮次", [ok_case], env_id)
        section_empty(api, conn, project_b_id, make_report(api, project_b_id, "报告门禁无失败报告"))

        # 项目 C：手工判失败但没有执行证据
        project_c = api.data("POST", f"{V1}/projects", {"code": "REPC", "name": "报告无证据项目"})
        project_c_id = int(project_c["id"])
        manual_case = int(api.data("POST", f"{V1}/test-cases", {
            "projectId": project_c_id, "title": "报告门禁手工失败用例", "caseType": "functional",
        })["id"])
        run_c_id = int(api.data("POST", f"{V1}/test-runs", {
            "projectId": project_c_id, "name": "报告门禁手工轮次", "caseIds": [manual_case],
        })["id"])
        rc_id = int(run_case_rows(api, run_c_id)[0]["id"])
        api.data("PUT", f"{V1}/test-runs/{run_c_id}/cases/{rc_id}",
                 {"result": FAILED, "actualResult": "手工判定失败：页面无响应", "executedBy": "gate"})
        section_no_evidence(api, make_report(api, project_c_id, "报告门禁无证据报告"))

        # 项目 E：证据只在用例级（手工执行），三处读口必须口径一致
        section_case_level_fallback(api, conn)

        # 项目 D：失败样本多于渲染上限
        project_d = api.data("POST", f"{V1}/projects", {"code": "REPD", "name": "报告上限项目"})
        project_d_id = int(project_d["id"])
        bad_cases = [make_interface_case(api, project_d_id, f"报告门禁上限用例{i}", "/bad")
                     for i in range(1, 5)]
        run_interface_cases(api, project_d_id, "报告门禁上限轮次", bad_cases, env_id)
        section_cap(api, conn, project_d_id, make_report(api, project_d_id, "报告门禁上限报告"))

        conn.close()
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001 - 清理失败不影响结果
            pass
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nPASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
