"""AITP 功能用例自动执行脚本。

按 `docs/AITP-功能测试用例.md` 中每条用例的「覆盖接口」执行一次真实请求，
校验业务码与响应结构，把结果写入 `docs/AITP-功能测试执行结果.md`。

用法：.venv/Scripts/python.exe scripts/exec_functional_cases.py
"""
from __future__ import annotations

import base64
import io
import json
import mimetypes
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
API = os.environ.get("AITP_API", "http://127.0.0.1:8760/api/v1")
REPORT = ROOT / "docs" / "AITP-功能测试执行结果.md"
TMP = ROOT / "data" / "_exec_tmp"

OK_CODE = 200000

RESULTS: list[dict] = []
IDS: dict[str, object] = {}
STAMP = uuid.uuid4().hex[:8]


# --------------------------------------------------------------------------- http
class Resp:
    def __init__(self, status: int, raw: bytes, headers: dict):
        self.status = status
        self.raw = raw
        self.headers = headers
        self.json = None
        if "json" in (headers.get("Content-Type") or ""):
            try:
                self.json = json.loads(raw.decode("utf-8"))
            except Exception:
                self.json = None

    @property
    def code(self):
        return (self.json or {}).get("code")

    @property
    def data(self):
        return (self.json or {}).get("data")

    @property
    def message(self):
        return (self.json or {}).get("message")


def request(method: str, path: str, body=None, files=None, raw_body=None, headers=None) -> Resp:
    url = path if path.startswith("http") else API + path
    hdrs = {"Accept": "application/json"}
    if headers:
        hdrs.update(headers)
    payload = None
    if files is not None:
        boundary = "----aitp" + uuid.uuid4().hex
        buf = io.BytesIO()
        for key, value in (body or {}).items():
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
            buf.write(f"{value}\r\n".encode())
        for key, (filename, content) in files.items():
            ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
            buf.write(f"--{boundary}\r\n".encode())
            buf.write(
                f'Content-Disposition: form-data; name="{key}"; filename="{filename}"\r\n'.encode()
            )
            buf.write(f"Content-Type: {ctype}\r\n\r\n".encode())
            buf.write(content if isinstance(content, bytes) else content.encode())
            buf.write(b"\r\n")
        buf.write(f"--{boundary}--\r\n".encode())
        payload = buf.getvalue()
        hdrs["Content-Type"] = f"multipart/form-data; boundary={boundary}"
    elif raw_body is not None:
        payload = raw_body if isinstance(raw_body, bytes) else raw_body.encode()
        hdrs.setdefault("Content-Type", "application/json")
    elif body is not None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        hdrs.setdefault("Content-Type", "application/json")

    req = urllib.request.Request(url, data=payload, method=method, headers=hdrs)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            return Resp(resp.status, resp.read(), dict(resp.headers))
    except urllib.error.HTTPError as err:
        return Resp(err.code, err.read(), dict(err.headers))
    except Exception as err:  # 连接层异常
        return Resp(0, str(err).encode(), {})


# --------------------------------------------------------------------------- 断言
def record(case: str, endpoint: str, ok: bool, detail: str = "") -> None:
    RESULTS.append(
        {"case": case, "endpoint": endpoint, "ok": bool(ok), "detail": detail[:600]}
    )
    flag = "PASS" if ok else "FAIL"
    print(f"[{flag}] {case} {endpoint} {detail if not ok else ''}")


def call(
    case: str,
    method: str,
    path: str,
    body=None,
    files=None,
    raw_body=None,
    expect_code=OK_CODE,
    expect_file=False,
    save=None,
    check=None,
    note=None,
):
    """执行一次请求并断言。check 收到 data 返回 (ok, detail)。"""
    resp = request(method, path, body=body, files=files, raw_body=raw_body)
    endpoint = f"{method} {path}"
    if expect_file:
        ok = resp.status == 200 and len(resp.raw) > 0
        if ok and resp.json is not None:
            ok = False
            detail = "期望文件流，实际返回 JSON"
        else:
            detail = f"status={resp.status} bytes={len(resp.raw)}"
        record(case, endpoint, ok, detail if not ok else "")
        return resp

    if resp.status != 200:
        record(case, endpoint, False, f"HTTP {resp.status}: {resp.raw[:200]!r}")
        return resp
    if resp.json is None:
        record(case, endpoint, False, f"响应非 JSON: {resp.raw[:200]!r}")
        return resp
    if resp.code != expect_code:
        record(case, endpoint, False, f"业务码 {resp.code} / 期望 {expect_code}：{resp.message}")
        return resp
    if check is not None:
        ok, detail = check(resp.data)
        record(case, endpoint, ok, detail)
        return resp
    detail = note or ""
    record(case, endpoint, True, detail)
    return resp


def capture(key: str, extract):
    def _inner(data):
        value = extract(data)
        IDS[key] = value
        return (value is not None, f"{key}={value}")

    return _inner


def gid(key: str):
    """从已捕获的 id 构造路径。"""

    def _build(path: str) -> str:
        return re.sub(r"<(\w+)>", lambda m: str(IDS.get(m.group(1), 0)), path)

    return _build


# --------------------------------------------------------------------------- 固定素材
def make_xlsx() -> bytes:
    try:
        from openpyxl import Workbook

        wb = Workbook()
        ws = wb.active
        ws.title = "功能测试用例"
        ws.append([
            "用例编号", "用例标题/描述", "所属模块/功能点", "关联需求", "优先级", "用例类型",
            "前置条件", "测试步骤", "测试数据", "预期结果", "实际结果",
            "执行状态", "执行人", "执行时间", "关联缺陷", "备注",
        ])
        ws.append(["", "冒烟登录用例", "", "", "P0", "功能测试", "已存在账号",
                   "1. 打开登录页\n2. 输入账号密码", "", "登录成功进入首页", "",
                   "未执行", "", "", "", ""])
        ws.append(["", "冒烟退出用例", "", "", "P1", "功能测试", "已登录",
                   "1. 点击退出", "", "回到登录页", "",
                   "未执行", "", "", "", ""])
        buf = io.BytesIO()
        wb.save(buf)
        return buf.getvalue()
    except Exception as err:  # pragma: no cover
        print("xlsx 生成失败：", err)
        return b""


PNG_1PX = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


# --------------------------------------------------------------------------- 主流程
def main() -> int:
    TMP.mkdir(parents=True, exist_ok=True)
    q = urllib.parse.quote

    # ---------------------------------------------------------------- 基础数据
    call("TC-AST-01", "GET", "/health")
    call("TC-AST-01", "GET", "/projects/options")
    call("TC-AST-01", "GET", "/projects?pageNum=1&pageSize=5")

    call(
        "TC-AST-01",
        "POST",
        "/projects",
        {"code": f"EXEC{STAMP}", "name": f"执行验证项目{STAMP}", "description": "自动化用例执行", "owner": "qa"},
        check=capture("projectId", lambda d: (d or {}).get("id")),
    )
    pid = IDS.get("projectId", 0)
    call("TC-AST-01", "GET", f"/projects/{pid}", check=lambda d: (d.get("code") == f"EXEC{STAMP}", "项目详情不匹配"))
    call(
        "TC-AST-02",
        "PUT",
        f"/projects/{pid}",
        {"name": f"执行验证项目{STAMP}-改", "status": "active"},
        check=lambda d: (d.get("name") == f"执行验证项目{STAMP}-改", "改名未生效"),
    )

    call(
        "TC-SET-21",
        "POST",
        "/project-versions",
        {"projectId": pid, "versionCode": "1.0.0", "name": "首个版本", "description": "执行用", "status": "released"},
        check=capture("versionId", lambda d: (d or {}).get("id")),
    )
    vid = IDS.get("versionId", 0)
    call("TC-SET-21", "GET", f"/project-versions?projectId={pid}", check=lambda d: (len(d.get("list", [])) >= 1, "版本列表为空"))
    call("TC-SET-21", "GET", f"/project-versions/{vid}")
    call("TC-SET-22", "PUT", f"/project-versions/{vid}", {"name": "首个版本-改", "status": "in_progress"})
    call("TC-SET-21", "GET", "/project-versions?projectId=999999")

    # 模块树
    call(
        "TC-SET-16",
        "POST",
        "/modules",
        {"projectId": pid, "name": "一级模块", "nodeType": "module", "description": "执行用"},
        check=capture("moduleL1", lambda d: (d or {}).get("id")),
    )
    call(
        "TC-SET-16",
        "POST",
        "/modules",
        {"projectId": pid, "name": "二级模块", "nodeType": "submodule", "parentId": IDS.get("moduleL1")},
        check=capture("moduleL2", lambda d: (d or {}).get("id")),
    )
    call(
        "TC-SET-16",
        "POST",
        "/modules",
        {"projectId": pid, "name": "功能点", "nodeType": "feature", "parentId": IDS.get("moduleL2")},
        check=capture("moduleId", lambda d: (d or {}).get("id")),
    )
    call("TC-SET-16", "GET", f"/modules/tree?projectId={pid}")
    call("TC-SET-17", "PUT", f"/modules/{IDS.get('moduleId')}", {"name": "功能点-改", "description": "改过"})
    call("TC-SET-19", "GET", f"/modules/options?projectId={pid}")
    call("TC-SET-19", "GET", f"/modules/options?projectId={pid}&all=1")
    call("TC-SET-18", "GET", f"/modules/{IDS.get('moduleId')}")
    call(
        "TC-SET-20",
        "PUT",
        f"/modules/{IDS.get('moduleId')}/capabilities",
        {"capabilities": [{"capability": "case_generation", "enabled": True}]},
    )
    call("TC-SET-20", "GET", f"/modules/{IDS.get('moduleId')}/capabilities")
    call("TC-SET-17", "POST", f"/modules/{IDS.get('moduleL1')}/move", {"parentId": None})

    # 需求
    call(
        "TC-AST-04",
        "POST",
        "/requirements",
        {
            "projectId": pid,
            "title": f"执行验证需求{STAMP}",
            "content": "需求内容",
            "priority": "P1",
            "status": "draft",
            "source": "manual",
        },
        check=capture("requirementId", lambda d: (d or {}).get("id")),
    )
    rid = IDS.get("requirementId", 0)
    call("TC-AST-04", "GET", f"/requirements/{rid}")
    call("TC-AST-05", "PUT", f"/requirements/{rid}", {"title": f"执行验证需求{STAMP}-改", "status": "reviewing", "versionId": vid})
    call("TC-AST-04", "GET", f"/requirements?projectId={pid}&pageNum=1&pageSize=5")
    call("TC-AST-04", "GET", f"/requirements/options?projectId={pid}")
    call("TC-AST-06", "GET", "/requirements/export?projectId=1&pageNum=1&pageSize=5", expect_file=True)

    # 用例
    call(
        "TC-AST-08",
        "POST",
        "/test-cases",
        {
            "projectId": pid,
            "title": f"执行验证用例{STAMP}",
            "caseType": "functional",
            "priority": "P0",
            "status": "not_executed",
            "requirementId": rid,
            "moduleId": IDS.get("moduleId"),
            "preconditions": "前置",
            "steps": ["步骤一"],
            "expectedResult": "预期",
            "source": "manual",
        },
        check=capture("caseId", lambda d: (d or {}).get("id")),
    )
    cid = IDS.get("caseId", 0)
    call("TC-AST-08", "GET", f"/test-cases/{cid}")
    call("TC-AST-09", "PUT", f"/test-cases/{cid}", {"title": f"执行验证用例{STAMP}-改", "priority": "P1", "versionId": vid})
    call("TC-AST-07", "GET", f"/test-cases?projectId={pid}&pageNum=1&pageSize=5")
    call("TC-AST-12", "GET", f"/test-cases/tree?projectId={pid}")
    call("TC-AST-11", "GET", f"/test-cases/coverage?projectId={pid}")
    call("TC-AST-11", "GET", f"/test-cases/coverage/modules?projectId={pid}")
    call("TC-AST-10", "POST", f"/test-cases/{cid}/execute", {"result": "pass", "durationMs": 120, "environment": "local", "executedBy": "qa"})
    call("TC-AST-08", "GET", "/test-cases/export?projectId=1&pageNum=1&pageSize=5", expect_file=True)
    call(
        "TC-AST-08",
        "POST",
        "/test-cases/adopt",
        {
            "projectId": pid,
            "moduleId": IDS.get("moduleId"),
            "requirementId": rid,
            "cases": [{"title": f"采纳用例{STAMP}", "caseType": "functional", "priority": "P1", "steps": ["步骤"], "expectedResult": "预期"}],
        },
        check=lambda d: (d.get("count", 0) >= 1, f"采纳数量异常 {d}"),
    )
    xlsx = make_xlsx()
    if xlsx:
        (TMP / "cases.xlsx").write_bytes(xlsx)
        call(
            "TC-AST-08",
            "POST",
            "/test-cases/import",
            {"projectId": str(pid)},
            files={"file": ("cases.xlsx", xlsx)},
            check=lambda d: (isinstance(d, dict), f"导入结果异常 {d}"),
        )

    # 缺陷
    call(
        "TC-AST-13",
        "POST",
        "/bugs",
        {
            "projectId": pid,
            "title": f"执行验证缺陷{STAMP}",
            "severity": "P1",
            "bugType": "functional",
            "description": "缺陷描述",
            "steps": ["复现步骤"],
            "expectedResult": "预期",
            "actualResult": "实际",
            "assignee": "qa",
            "reporter": "qa",
            "moduleId": IDS.get("moduleId"),
        },
        check=capture("bugId", lambda d: (d or {}).get("id")),
    )
    bid = IDS.get("bugId", 0)
    call("TC-AST-13", "GET", f"/bugs/{bid}")
    call("TC-AST-14", "PUT", f"/bugs/{bid}", {"title": f"执行验证缺陷{STAMP}-改", "severity": "P2"})
    call("TC-AST-15", "POST", f"/bugs/{bid}/transition", {"toStatus": "assigned", "operator": "qa", "comment": "分派"})
    call("TC-AST-15", "POST", f"/bugs/{bid}/transition", {"toStatus": "fixed", "operator": "qa"})
    call("TC-AST-15", "POST", f"/bugs/{bid}/transition", {"toStatus": "retest", "operator": "qa"})
    call("TC-AST-15", "POST", f"/bugs/{bid}/transition", {"toStatus": "closed", "operator": "qa"})
    call("TC-AST-16", "GET", f"/bugs/{bid}/logs")
    call("TC-AST-17", "POST", f"/bugs/{bid}/cases", {"caseIds": [cid], "relationType": "related"})
    call("TC-AST-17", "DELETE", f"/bugs/{bid}/cases", {"caseIds": [cid]})
    call("TC-AST-18", "GET", "/bugs/stats")
    call("TC-AST-19", "GET", "/bugs/export?projectId=1&pageNum=1&pageSize=5", expect_file=True)
    call("TC-AST-13", "GET", "/bugs?projectId=1&pageNum=1&pageSize=5")

    # SQL 分析
    call(
        "TC-AST-20",
        "POST",
        "/sql-analyses",
        {"projectId": pid, "title": f"执行验证SQL{STAMP}", "sqlText": "select 1", "analysis": "分析", "riskLevel": "low", "moduleId": IDS.get("moduleId")},
        check=capture("sqlId", lambda d: (d or {}).get("id")),
    )
    call("TC-AST-20", "GET", f"/sql-analyses/{IDS.get('sqlId')}")
    call("TC-AST-20", "PUT", f"/sql-analyses/{IDS.get('sqlId')}", {"title": f"执行验证SQL{STAMP}-改", "riskLevel": "high"})
    call("TC-AST-21", "GET", "/sql-analyses?projectId=1&pageNum=1&pageSize=5")

    # 回归清单
    call(
        "TC-AST-22",
        "POST",
        "/regressions",
        {"projectId": pid, "title": f"执行验证回归{STAMP}", "changeScope": "登录模块改造", "affectedModules": "登录", "riskLevel": "medium"},
        check=capture("regressionId", lambda d: (d or {}).get("id")),
    )
    regid = IDS.get("regressionId", 0)
    call("TC-AST-22", "GET", f"/regressions/{regid}")
    call("TC-AST-23", "PUT", f"/regressions/{regid}", {"title": f"执行验证回归{STAMP}-改", "riskLevel": "high"})
    call(
        "TC-AST-24",
        "POST",
        f"/regressions/{regid}/items",
        {"title": "回归项一", "priority": "P1", "module": "登录", "reason": "改造影响", "verifyTarget": "登录接口"},
        check=capture("regressionItemId", lambda d: (d or {}).get("id")),
    )
    call("TC-AST-25", "GET", f"/regressions/{regid}/items")
    call("TC-AST-25", "PUT", f"/regressions/{regid}/items/{IDS.get('regressionItemId')}", {"title": "回归项一-改", "result": "passed"})
    call("TC-AST-26", "POST", f"/regressions/{regid}/execute", {"results": [{"itemId": IDS.get("regressionItemId"), "result": "passed", "remark": "通过"}]})
    call("TC-AST-22", "GET", "/regressions?projectId=1&pageNum=1&pageSize=5")

    # 报告
    call(
        "TC-AST-28",
        "POST",
        "/reports",
        {"projectId": pid, "title": f"执行验证报告{STAMP}", "reportType": "summary", "summary": "摘要", "versionId": vid},
        check=capture("reportId", lambda d: (d or {}).get("id")),
    )
    repid = IDS.get("reportId", 0)
    call("TC-AST-28", "GET", f"/reports/{repid}")
    call("TC-AST-29", "PUT", f"/reports/{repid}", {"title": f"执行验证报告{STAMP}-改", "status": "published"})
    call("TC-AST-30", "PUT", f"/reports/{repid}/items", {"items": [{"section": "overview", "title": "概述", "content": "内容", "sortOrder": 0}]})
    call("TC-AST-30", "GET", f"/reports/{repid}/items")
    call("TC-AST-31", "GET", f"/reports/{repid}/stats")
    call("TC-AST-32", "GET", f"/reports/{repid}/export?format=md", expect_file=True)
    call("TC-AST-32", "GET", f"/reports/{repid}/export?format=html", expect_file=True)
    call("TC-AST-32", "GET", f"/reports/{repid}/export?format=pdf", expect_file=True)
    call("TC-AST-32", "GET", f"/reports/{repid}/export?format=print", expect_file=True)
    call("TC-AST-33", "POST", "/reports/generate", {"projectId": pid, "reportType": "iteration", "title": f"生成报告{STAMP}"})
    call("TC-AST-33", "POST", "/reports/adopt", {"projectId": pid, "title": f"采纳报告{STAMP}", "content": "## 章节一\n内容\n## 章节二\n内容2"})
    call("TC-AST-27", "GET", "/reports/templates")
    call("TC-AST-27", "GET", "/reports?projectId=1&pageNum=1&pageSize=5")

    # 知识库
    call("TC-KNW-04", "POST", "/knowledge/categories", {"name": f"执行分类{STAMP}"}, check=capture("categoryId", lambda d: (d or {}).get("id")))
    call(
        "TC-KNW-03",
        "POST",
        "/knowledge/drafts",
        {"title": f"执行验证知识{STAMP}", "content": "知识正文", "category": f"执行分类{STAMP}", "summary": "摘要", "tags": "执行,验证"},
        check=capture("draftId", lambda d: (d or {}).get("id")),
    )
    did = IDS.get("draftId", 0)
    call("TC-KNW-01", "GET", "/knowledge/drafts?pageNum=1&pageSize=5")
    call("TC-KNW-07", "PUT", f"/knowledge/drafts/{did}", {"title": f"执行验证知识{STAMP}-改", "content": "知识正文改"})
    call("TC-KNW-04", "GET", "/knowledge/categories")
    call("TC-KNW-05", "POST", f"/knowledge/drafts/{did}/review", {"action": "approve", "reviewer": "qa"})
    call("TC-KNW-05", "POST", f"/knowledge/drafts/{did}/review", {"action": "reject", "comment": "内容需补充"}, expect_code=140105)
    call(
        "TC-KNW-06",
        "POST",
        "/knowledge/drafts/batch-review",
        {"draftIds": [did], "action": "approve", "reviewer": "qa"},
        check=lambda d: (isinstance(d, dict), f"批量审核结果异常 {d}"),
    )
    call("TC-KNW-01", "GET", "/knowledge/articles?pageNum=1&pageSize=5")
    call("TC-KNW-02", "GET", "/knowledge/search?keyword=%E6%89%A7%E8%A1%8C&pageNum=1&pageSize=5")
    call("TC-KNW-01", "GET", "/knowledge/tags")
    call("TC-KNW-13", "GET", "/knowledge/storage")
    call("TC-KNW-08", "GET", "/knowledge/articles/export?pageNum=1&pageSize=5", expect_file=True)

    # 正式库条目：找一条已通过文章
    resp = request("GET", "/knowledge/articles?pageNum=1&pageSize=50")
    article = None
    if resp.json and isinstance(resp.data, dict):
        for item in resp.data.get("list", []):
            if item.get("status") in ("published", "active", None) or item.get("reviewStatus") in ("approved", None):
                article = item
                break
    if article:
        aid = article.get("id")
        IDS["articleId"] = aid
        call("TC-KNW-11", "GET", f"/knowledge/articles/{aid}")
        call("TC-KNW-11", "GET", f"/knowledge/articles/{aid}/refs")
        call("TC-KNW-13", "GET", f"/knowledge/articles/{aid}/versions")
        call("TC-KNW-12", "PUT", f"/knowledge/articles/{aid}", {"title": f"执行验证知识{STAMP}-已发布改", "content": "已发布内容", "changeLog": "执行修改"})
        versions = request("GET", f"/knowledge/articles/{aid}/versions")
        ver_list = (versions.data or {}).get("list") if versions.json and isinstance(versions.data, dict) else versions.data
        if ver_list:
            v1 = ver_list[0].get("version") or ver_list[0].get("versionNo")
            call("TC-KNW-13", "GET", f"/knowledge/articles/{aid}/versions/{v1}")
            call("TC-KNW-13", "GET", f"/knowledge/articles/{aid}/diff?from=1&to={v1}")
            call("TC-KNW-13", "POST", f"/knowledge/articles/{aid}/versions/{v1}/review", {"action": "approve", "reviewer": "qa"})
        call("TC-KNW-09", "POST", f"/knowledge/articles/{aid}/revoke", {})
        call("TC-KNW-09", "POST", f"/knowledge/articles/{aid}/restore", {})
        call("TC-KNW-14", "POST", "/knowledge/ask", {"question": "登录失败怎么办", "mode": "kb"})
        call("TC-KNW-14", "POST", "/knowledge/ask", {"question": "登录失败怎么办", "mode": "llm"})
        call("TC-KNW-14", "POST", "/knowledge/ask", {"question": "登录失败怎么办", "mode": "hybrid"})
    else:
        record("TC-KNW-11", "GET /knowledge/articles/{id}", False, "正式库无可用条目，无法验证")

    original = (request("GET", "/knowledge/storage").data) or {}
    call("TC-SET-13", "PUT", "/knowledge/storage", {"fileMode": "db_only", "autoReindex": False})
    call("TC-SET-13", "GET", "/knowledge/storage")
    # 重建索引只在 mirror 模式读目录文件，切回来才测得到「以文件为准」这条主链路
    request("PUT", "/knowledge/storage", {"fileMode": "mirror", "autoReindex": False})
    call("TC-SET-15", "POST", "/knowledge/reindex", {})
    request("PUT", "/knowledge/storage", {
        "fileMode": str(original.get("fileMode") or "mirror"),
        "autoReindex": bool(original.get("autoReindex")),
    })

    # 自动化脚本
    call(
        "TC-AST-36",
        "POST",
        "/automation/scripts",
        {
            "name": f"执行验证脚本{STAMP}",
            "category": "web",
            "framework": "selenium",
            "language": "python",
            "projectId": pid,
            "content": "print('ok')",
            "runMode": "script",
            "entryCommand": "print('ok')",
            "timeoutSeconds": 30,
            "status": "ready",
        },
        check=capture("scriptId", lambda d: (d or {}).get("id")),
    )
    sid = IDS.get("scriptId", 0)
    call("TC-AST-36", "GET", f"/automation/scripts/{sid}")
    call("TC-AST-37", "PUT", f"/automation/scripts/{sid}", {"name": f"执行验证脚本{STAMP}-改", "remark": "改过"})
    call("TC-AST-38", "POST", f"/automation/scripts/{sid}/run", {"timeoutSeconds": 20})
    call("TC-AST-39", "GET", "/automation/runs?pageNum=1&pageSize=5")
    call("TC-AST-34", "GET", "/automation/categories")
    call("TC-AST-35", "GET", "/automation/runtime")
    call("TC-AST-33", "GET", "/automation/scripts?pageNum=1&pageSize=5")
    call("TC-AST-41", "POST", "/automation/scripts/from-output", {"output": "```python\nprint('x')\n```", "category": "web", "name": f"AI脚本{STAMP}"})

    # 验证沙箱
    call(
        "TC-WBS-08",
        "POST",
        "/validation/cases",
        {"name": f"执行验证案例{STAMP}", "inputText": "登录页", "description": "描述", "capability": "case_generation", "variables": {"module": "登录"}},
        check=capture("validationId", lambda d: (d or {}).get("id")),
    )
    valid = IDS.get("validationId", 0)
    call("TC-WBS-09", "GET", "/validation/cases?pageNum=1&pageSize=5")
    call("TC-WBS-09", "GET", f"/validation/cases/{valid}")
    call("TC-WBS-10", "PUT", f"/validation/cases/{valid}", {"name": f"执行验证案例{STAMP}-改", "description": "改过"})
    call("TC-WBS-12", "POST", f"/validation/cases/{valid}/run", {"projectId": pid})
    call("TC-WBS-13", "POST", "/validation/run-all", {"projectId": pid})

    # 关联
    call("TC-WBS-14", "GET", "/relations/types")
    call(
        "TC-WBS-14",
        "POST",
        "/relations",
        {"sourceType": "requirement", "sourceId": rid, "relationType": "verifies", "targetType": "case", "targetId": cid, "remark": "执行关联"},
        check=capture("relationId", lambda d: (d or {}).get("id")),
    )
    call("TC-WBS-15", "GET", f"/relations/of/requirement/{rid}")
    call("TC-WBS-16", "GET", "/relations?pageNum=1&pageSize=5")

    # 工作台
    call(
        "TC-WBS-02",
        "POST",
        "/workbench/entries",
        {
            "code": f"ENTRY{STAMP}",
            "name": f"执行验证入口{STAMP}",
            "capability": "case_generation",
            "description": "描述",
            "workflow": [
                {"capability": "case_generation", "name": "生成用例", "moduleId": IDS.get("moduleId"), "variables": {"projectName": "执行验证项目", "requirement": "登录功能需求"}}
            ],
        },
        check=capture("entryId", lambda d: (d or {}).get("id")),
    )
    eid = IDS.get("entryId", 0)
    call("TC-WBS-01", "GET", "/workbench/entries?pageNum=1&pageSize=5")
    call("TC-WBS-07", "GET", f"/workbench/entries/{eid}")
    call("TC-WBS-03", "PUT", f"/workbench/entries/{eid}", {"name": f"执行验证入口{STAMP}-改", "description": "改过"})
    call("TC-WBS-04", "POST", f"/workbench/entries/{eid}/publish", {})
    call("TC-WBS-05", "POST", f"/workbench/entries/{eid}/run", {"variables": {}})
    call("TC-HOM-01", "GET", "/workbench/overview?projectId=1")
    call("TC-WBS-18", "GET", "/workbench/coverage?projectId=1")
    call("TC-WBS-18", "GET", "/workbench/executions?pageNum=1&pageSize=5")

    # 任务清单
    call(
        "TC-TOD-04",
        "POST",
        "/todos",
        {"title": f"执行验证任务{STAMP}", "description": "描述", "status": "open", "priority": "high", "category": "执行验证", "projectId": pid},
        check=capture("todoId", lambda d: (d or {}).get("id")),
    )
    tid = IDS.get("todoId", 0)
    call("TC-TOD-01", "GET", "/todos?pageNum=1&pageSize=5")
    call("TC-TOD-01", "GET", "/todos/stats")
    call("TC-TOD-03", "GET", "/todos/categories")
    call("TC-TOD-14", "GET", f"/todos/{tid}")
    call("TC-TOD-06", "POST", f"/todos/{tid}/toggle", {})
    call("TC-TOD-07", "PUT", f"/todos/{tid}", {"isPinned": True})
    call("TC-TOD-08", "POST", f"/todos/{tid}/move", {"direction": "down"})
    call("TC-TOD-13", "PUT", f"/todos/{tid}", {"title": f"执行验证任务{STAMP}-改", "description": "改过"})
    call("TC-TOD-10", "POST", "/todos/batch-update", {"ids": [tid], "status": "done"})
    call("TC-TOD-19", "POST", "/todos/reorder", {"ids": [tid]})
    call("TC-TOD-17", "GET", "/todos/export?pageNum=1&pageSize=5", expect_file=True)

    # 测试环境
    call(
        "TC-SET-35",
        "POST",
        "/test-envs",
        {"name": f"执行验证环境{STAMP}", "description": "描述", "app": {"baseUrl": "http://127.0.0.1:8760", "healthPath": "/api/v1/health"}, "db": {"type": "mysql", "host": "127.0.0.1", "port": 3306, "name": "demo", "username": "root"}},
        check=capture("envId", lambda d: (d or {}).get("id")),
    )
    envid = IDS.get("envId", 0)
    call("TC-SET-35", "GET", "/test-envs")
    call("TC-SET-36", "GET", f"/test-envs/{envid}")
    call("TC-SET-36", "PUT", f"/test-envs/{envid}", {"description": "改过", "app": {"baseUrl": "http://127.0.0.1:8760", "healthPath": "/api/v1/health"}})
    call("TC-SET-40", "POST", f"/test-envs/{envid}/password", {"password": "Passw0rd!2026"})
    call("TC-SET-38", "POST", f"/test-envs/{envid}/probe", {})
    call("TC-SET-39", "POST", "/test-envs/probe-draft", {"app": {"baseUrl": "http://127.0.0.1:8760", "healthPath": "/api/v1/health"}})
    call("TC-SET-41", "PUT", f"/test-envs/{envid}", {"isDefault": True})

    # 文档模板
    call(
        "TC-SET-24",
        "POST",
        "/doc-templates",
        {"name": f"执行验证模板{STAMP}", "kind": "case", "description": "描述", "content": "模板内容", "columns": ["标题", "步骤"], "styleHint": "简洁"},
        check=capture("templateId", lambda d: (d or {}).get("id")),
    )
    tpid = IDS.get("templateId", 0)
    call("TC-SET-23", "GET", "/doc-templates")
    call("TC-SET-27", "GET", "/doc-templates/options?kind=case")
    call("TC-SET-24", "GET", f"/doc-templates/{tpid}")
    call("TC-SET-24", "PUT", f"/doc-templates/{tpid}", {"description": "改过"})
    call("TC-SET-25", "POST", "/doc-templates/upload", {}, files={"file": ("tpl.md", b"# template\ncontent")})

    # AI 档案
    call(
        "TC-SET-05",
        "POST",
        "/settings/ai/profiles",
        {"name": f"执行验证档案{STAMP}", "provider": "ollama", "model": "mock-qwen3:1.7b", "baseUrl": "http://127.0.0.1:8791", "isActive": True},
        check=capture("profileId", lambda d: (d or {}).get("id")),
    )
    prid = IDS.get("profileId", 0)
    call("TC-SET-04", "GET", "/settings/ai/profiles")
    call("TC-SET-04", "GET", "/settings/ai/providers")
    call("TC-SET-06", "GET", f"/settings/ai/profiles/{prid}")
    call("TC-SET-06", "PUT", f"/settings/ai/profiles/{prid}", {"model": "mock-qwen3:1.7b", "temperature": 0.5})
    call("TC-SET-07", "PUT", f"/settings/ai/profiles/{prid}", {"isActive": False})
    call("TC-SET-08", "POST", f"/settings/ai/profiles/{prid}/api-key", {"apiKey": "sk-exec-test-key"})
    call("TC-SET-09", "GET", "/settings/ai/status")
    call("TC-SET-09", "POST", "/settings/ai/selfcheck", {"profileId": prid})
    call("TC-SET-10", "POST", "/settings/ai/models", {"profileId": prid})

    # 全局配置
    call("TC-SET-12", "GET", "/settings/config")
    call("TC-SET-12", "PUT", "/settings/config", {"items": [{"key": "ai.timeout.default", "value": "60", "valueType": "int"}]})
    call("TC-SET-33", "PUT", "/settings/config", {"items": [{"key": "automation.python.timeout", "value": "300", "valueType": "int"}]})

    # 操作日志
    call("TC-SET-01", "GET", "/settings/audit-logs?pageNum=1&pageSize=10")
    call("TC-SET-02", "GET", "/settings/audit-logs?pageNum=1&pageSize=10&action=create")
    call("TC-SET-02", "GET", "/settings/audit-logs?pageNum=1&pageSize=10&resourceType=requirement")
    call("TC-SET-03", "GET", f"/settings/audit-logs/resources?resourceType=requirement&resourceId={rid}")

    # 角标
    call("TC-GLB-12", "GET", "/badges")
    call("TC-GLB-13", "POST", "/badges/bugs/increase", {"count": 2})
    call("TC-GLB-12", "POST", "/badges/bugs/clear", {})

    # 元信息与检索
    call("TC-GLB-09", "GET", "/meta/enums")
    call("TC-GLB-09", "GET", "/meta/capabilities")
    call("TC-GLB-09", "GET", "/meta/providers")
    call("TC-GLB-06", "GET", "/search?q=%E6%89%A7%E8%A1%8C&pageNum=1&pageSize=5")
    call("TC-GLB-05", "GET", "/settings/ai/status")

    # AI 实验室
    call("TC-LAB-01", "GET", "/lab/capabilities")
    call("TC-LAB-01", "GET", "/lab/main-capabilities")
    call("TC-LAB-39", "GET", "/lab/prompts")
    prompts = request("GET", "/lab/prompts")
    prompt_code = ""
    if prompts.json:
        items = prompts.data if isinstance(prompts.data, list) else (prompts.data or {}).get("list", [])
        for item in items:
            code = (item or {}).get("code")
            if code:
                prompt_code = code
                break
    IDS["promptCode"] = prompt_code
    if prompt_code:
        call("TC-LAB-39", "GET", f"/lab/prompts/{prompt_code}")
        call("TC-LAB-40", "POST", "/ai/prompts/preview", {"code": prompt_code, "variables": {"projectName": "执行验证项目", "apiDescription": "登录接口 GET /api/v1/login"}})
    else:
        record("TC-LAB-39", "GET /lab/prompts/{code}", False, "提示词清单为空，取不到合法提示词编号")
    call("TC-LAB-37", "GET", "/lab/sessions?pageNum=1&pageSize=5")
    call("TC-LAB-36", "GET", "/lab/tasks?pageNum=1&pageSize=5")
    call("TC-LAB-34", "GET", "/ai-records?pageNum=1&pageSize=5")
    recs = request("GET", "/ai-records?pageNum=1&pageSize=5")
    if recs.json and isinstance(recs.data, dict) and recs.data.get("list"):
        rec = recs.data["list"][0]
        call("TC-LAB-35", "GET", f"/ai-records/{rec.get('id')}")
        call("TC-LAB-35", "POST", f"/ai-records/{rec.get('id')}/review", {"status": "approved", "note": "执行审核"})

    # 文档附件 / 图片附件
    call(
        "TC-LAB-31",
        "POST",
        "/lab/attachments/document",
        {},
        files={"file": ("doc.txt", "需求文档正文：登录功能".encode())},
        check=capture("attachmentId", lambda d: (d or {}).get("attachmentId") or (d or {}).get("id")),
    )
    aid2 = IDS.get("attachmentId")
    if aid2:
        call("TC-LAB-31", "GET", f"/lab/attachments/{aid2}", expect_file=True)
        call("TC-LAB-33", "GET", f"/lab/attachments/{aid2}/payload", expect_code=140100)
        call("TC-LAB-33", "DELETE", f"/lab/attachments/{aid2}", {})
    call(
        "TC-LAB-32",
        "POST",
        "/lab/attachments/image",
        {"dataUrl": "data:image/png;base64," + base64.b64encode(PNG_1PX).decode()},
        check=capture("imageId", lambda d: (d or {}).get("attachmentId") or (d or {}).get("id")),
    )
    if IDS.get("imageId"):
        call("TC-LAB-33", "GET", f"/lab/attachments/{IDS['imageId']}/payload")
        call("TC-LAB-32", "DELETE", f"/lab/attachments/{IDS['imageId']}", {})

    # xmind 解析 / 导出
    call("TC-LAB-29", "POST", "/lab/xmind/parse", {"outlineText": "登录\n  账号密码\n  验证码\n支付\n  下单\n  支付"})
    call(
        "TC-LAB-26",
        "POST",
        "/lab/output/export-xmind",
        {"title": "测试点脑图", "nodes": [{"title": "登录", "children": [{"title": "账号密码"}]}]},
        expect_file=True,
    )

    # 智能路由 / 预检
    call("TC-LAB-25", "POST", "/lab/route", {"text": "设计登录功能的测试用例", "projectId": pid, "topK": 3})
    call("TC-LAB-25", "POST", "/lab/preflight", {"text": "设计登录功能的测试用例", "projectId": pid})
    call("TC-LAB-25", "POST", "/lab/preflight", {"capabilities": ["case_generation"], "variables": {}, "projectId": pid})

    # 产出采纳
    call("TC-LAB-18", "POST", "/lab/output/adopt", {"capability": "case_generation", "output": "1. 打开登录页\n2. 输入账号密码\n3. 点击登录", "target": "requirement", "projectId": pid, "title": f"采纳需求{STAMP}"})
    call("TC-LAB-18", "POST", "/lab/output/adopt", {"capability": "bug_analysis", "output": "缺陷描述正文", "target": "bug", "projectId": pid, "title": f"采纳缺陷{STAMP}"})
    call("TC-LAB-18", "POST", "/lab/output/adopt", {"capability": "regression_generation", "output": "回归范围说明", "target": "regression", "projectId": pid, "title": f"采纳回归{STAMP}"})
    call("TC-LAB-18", "POST", "/lab/output/adopt", {"capability": "sql_analysis", "output": "select * from users", "target": "sql", "projectId": pid, "title": f"采纳SQL{STAMP}"})

    # 能力执行（走 mock 模型）
    call("TC-LAB-03", "POST", "/lab/requirement_confirm/run", {"variables": {"projectName": "执行验证项目", "requirement": "登录功能需求"}})
    call("TC-LAB-03", "POST", "/lab/case_generation/run", {"variables": {"projectName": "执行验证项目", "requirement": "登录功能"}})
    call("TC-LAB-06", "POST", "/lab/prompt_optimize/run", {"variables": {"rawText": "写测试用例"}})

    # 导出
    call(
        "TC-LAB-20",
        "POST",
        "/lab/output/export",
        {"capability": "case_generation", "output": "1. 打开登录页\n2. 输入账号密码", "format": "xlsx"},
        expect_file=True,
    )

    # 测试数据
    call(
        "TC-LAB-27",
        "POST",
        "/test-data/files",
        {"projectId": pid, "moduleId": IDS.get("moduleId"), "ext": "json", "sizeBytes": 1024, "fileCount": 2, "tokenMode": "sequence", "namePrefix": "exec"},
        check=capture("batchId", lambda d: (d or {}).get("id") or ((d or {}).get("batch") or {}).get("id")),
    )
    bid2 = IDS.get("batchId")
    call("TC-LAB-27", "GET", "/test-data/formats")
    call("TC-LAB-27", "GET", "/test-data/batches?pageNum=1&pageSize=5")
    if bid2:
        call("TC-LAB-27", "GET", f"/test-data/batches/{bid2}")
        call("TC-LAB-27", "GET", f"/test-data/batches/{bid2}/archive", expect_file=True)

    # 备份：创建是异步任务，必须轮询到本次新生成的备份再下载/恢复，
    # 不能直接取列表第一条，否则会拿旧备份把当前库回滚。
    def backup_names() -> set[str]:
        resp = request("GET", "/settings/backups")
        data = resp.data if resp.json else None
        if not isinstance(data, dict):
            return set()
        return {str(item.get("name")) for item in data.get("list") or []}

    before_names = backup_names()
    call("TC-SET-28", "POST", "/settings/backups", {})
    backup_name = None
    deadline = time.time() + 90
    while time.time() < deadline:
        created = backup_names() - before_names
        if created:
            backup_name = sorted(created)[-1]
            break
        time.sleep(1)
    call("TC-SET-28", "GET", "/settings/backups")
    if backup_name:
        name_q = q(str(backup_name))
        call("TC-SET-29", "GET", f"/settings/backups/{name_q}/download", expect_file=True)

    # 清理（删除类）
    call("TC-WBS-17", "DELETE", f"/relations/{IDS.get('relationId')}", {})
    call("TC-WBS-11", "DELETE", f"/validation/cases/{valid}", {})
    call("TC-WBS-06", "DELETE", f"/workbench/entries/{eid}", {})
    call("TC-AST-40", "DELETE", f"/automation/scripts/{sid}", {})
    call("TC-AST-45", "DELETE", f"/reports/{repid}", {})
    call("TC-AST-29", "DELETE", f"/sql-analyses/{IDS.get('sqlId')}", {})
    call("TC-AST-14", "DELETE", f"/bugs/{bid}", {})
    call("TC-AST-15", "DELETE", f"/test-cases/{cid}", {})
    call("TC-TOD-12", "POST", "/todos/batch-delete", {"ids": [tid]})
    call("TC-SET-26", "DELETE", f"/doc-templates/{tpid}", {})
    call("TC-SET-37", "DELETE", f"/test-envs/{envid}", {})
    call("TC-SET-11", "DELETE", f"/settings/ai/profiles/{prid}", {})
    call("TC-KNW-04", "DELETE", f"/knowledge/categories/{IDS.get('categoryId')}", {})
    # 版本下还有需求时会被「先迁移或删除资产」拦住，清完资产再删版本
    call("TC-AST-04", "DELETE", f"/requirements/{rid}")
    call("TC-SET-22", "DELETE", f"/project-versions/{vid}", {})
    clean_parent = call(
        "TC-SET-16", "POST", "/modules",
        {"projectId": pid, "name": "待删模块", "nodeType": "module"},
        check=capture("cleanModuleId", lambda d: (d or {}).get("id")),
    )
    clean_id = IDS.get("cleanModuleId")
    if clean_id:
        call("TC-SET-18", "DELETE", f"/modules/{clean_id}", {})
    call("TC-SET-18", "DELETE", f"/modules/{IDS.get('moduleId')}", {}, expect_code=140104)
    call("TC-SET-18", "DELETE", f"/modules/{IDS.get('moduleL2')}", {}, expect_code=140104)
    call("TC-SET-18", "DELETE", f"/modules/{IDS.get('moduleL1')}", {}, expect_code=140104)
    call("TC-AST-01", "DELETE", f"/projects/{pid}", {})

    # 备份恢复放最后
    if backup_name:
        name_q = q(str(backup_name))
        call("TC-SET-31", "POST", f"/settings/backups/{name_q}/restore", {"confirm": str(backup_name)})

    write_report()
    return 0 if all(r["ok"] for r in RESULTS) else 1


def write_report() -> None:
    total = len(RESULTS)
    failed = [r for r in RESULTS if not r["ok"]]
    lines = [
        "# AITP 功能测试执行结果",
        "",
        f"- 执行时间：{time.strftime('%Y-%m-%d %H:%M:%S')}",
        f"- 执行端点次数：{total}",
        f"- 通过：{total - len(failed)}　失败：{len(failed)}",
        "",
        "## 失败明细",
        "",
    ]
    if failed:
        lines += ["| 用例 | 端点 | 现象 |", "|---|---|---|"]
        for r in failed:
            detail = r["detail"].replace("|", "／").replace("\n", " ")
            lines.append(f"| {r['case']} | `{r['endpoint']}` | {detail} |")
    else:
        lines.append("本次执行全部通过。")
    lines += ["", "## 全部执行明细", "", "| 用例 | 端点 | 结果 |", "|---|---|---|"]
    for r in RESULTS:
        lines.append(f"| {r['case']} | `{r['endpoint']}` | {'通过' if r['ok'] else '失败'} |")
    REPORT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\n报告已写入 {REPORT}")
    print(f"总 {total}，失败 {len(failed)}")


if __name__ == "__main__":
    sys.exit(main())
