"""主线 B 执行证据链门禁（GAP-32 / 33 / 34 / 35 / 36）。

一次性把「一次执行到底留下了什么」钉死成可复盘、可下钻、可回归的口径：

  1. GAP-32：三条执行路径（接口真实 HTTP / 自动化 subprocess / 手工录入）产出的证据
     字段集合必须逐个相等；手工零留痕就不落快照；未知来源、失效附件都要给中文提示。
  2. GAP-33：回归条目「标为自动」必须由服务端真跑——客户端谎报的结论一律被真实结果覆盖；
     手工条目无证据不得标记通过（清单决策 4）；PUT 单条改结论也不允许绕过真跑。
  3. GAP-34：``schema:`` 断言失败要给出具体路径差异；``lib:`` 断言库引用必须真的展开，
     引用不存在的编号 / 已停用的编号 / 库里再引用库都要红。
  4. GAP-35：失败转缺陷时把本次证据写进缺陷描述并把证据挂上 bugId，
     轮次 / 缺陷 / 来源三处下钻拿到的是同一个 evidenceId。
  5. GAP-36：需求可直挂功能点，功能点矩阵同时给需求口径与用例口径。
  6. 保留期：到期即不可见，清理只软删并留原因，不物理删。

只打本机桩 HTTP 服务，不调任何模型、不碰正式库：

    python backend/scripts/check_execution_evidence.py
"""

from __future__ import annotations

import io
import json
import pathlib
import re
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
REGRESSION_VUE = SRC / "views" / "regressions" / "RegressionListView.vue"
#: 后端 coverage 里给界面看的四个比率，字段名漂移时这条要红
COVERAGE_RATE_KEYS = ("executedRate", "autoShare", "autoRate", "evidenceRate")
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import execution_protocol  # noqa: E402

V1 = "/api/v1"
PY_OK = 'print("hello-evidence-automation")\n'

#: ExecutionResult.as_dict() 的唯一字段口径：三条路径都必须等于这一组，多一个少一个都算红
EVIDENCE_KEYS = frozenset({
    "sourceType", "sourceTypeLabel", "sourceRowId", "evidenceId", "projectId", "caseId",
    "runId", "runCaseId", "regressionId", "regressionItemId", "bugId", "envId", "envName",
    "title", "result", "resultLabel", "durationMs", "assertions", "assertionSummary",
    "requestSummary", "responseSummary", "logSummary", "attachments", "sourceRef",
    "traceId", "errorMessage", "executedBy", "executedAt",
})

#: 每条证据都必须能回答的四个问题（§4 验收标准 1 的口径）
CORE_QUESTIONS = ("sourceType", "result", "durationMs", "assertionSummary")

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
        self, method: str, path: str, payload: dict[str, Any] | None = None,
        data: Any = None, content_type: str | None = None,
    ) -> tuple[Any, dict[str, Any]]:
        if data is not None:
            resp = self.client.open(
                path, method=method, data=data, content_type=content_type or "multipart/form-data"
            )
        else:
            resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(dict[str, Any], body)

    def data(self, method: str, path: str, payload: dict[str, Any] | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 失败: code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")

    def expect(
        self, name: str, method: str, path: str, code: int, payload: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code,
              f"code={body.get('code')} msg={body.get('message')}")
        return body


class Handler(BaseHTTPRequestHandler):
    """本机桩服务：只提供确定性的 JSON，不调任何模型。"""

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
            self._send(200, {
                "code": 0,
                "data": {"id": 1, "name": "alpha", "items": [{"n": 2}, {"n": 3}]},
            })
            return
        if self.path.startswith("/typed"):
            self._send(200, {"code": 0, "data": {"id": "s-1", "name": "alpha"}})
            return
        if self.path.startswith("/bad"):
            self._send(500, {"code": 500, "message": "boom"})
            return
        self._send(404, {"code": 404})

    def do_POST(self) -> None:  # noqa: N802 - 基类签名
        self.do_GET()


def start_server() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(cast(tuple[Any, ...], server.server_address)[1])


def build_app(workdir: pathlib.Path) -> Any:
    db_path = workdir / "evidence.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8771,
        debug=False,
        db_path=db_path,
        log_path=workdir / "evidence.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")}


def make_case(api: Api, project_id: int, title: str, url: str, rules: str,
              module_id: int | None = None) -> int:
    payload: dict[str, Any] = {
        "projectId": project_id,
        "title": title,
        "caseType": "interface",
        "priority": "P1",
        "extra": {
            "接口URL": url,
            "请求方式": "GET",
            "预期状态码": "200",
            "断言规则": rules,
        },
    }
    if module_id is not None:
        payload["moduleId"] = module_id
    return int(api.data("POST", f"{V1}/test-cases", payload)["id"])


def evidence_of(api: Api, source_type: str, source_row_id: int) -> dict[str, Any]:
    return cast(dict[str, Any], api.data(
        "GET", f"{V1}/test-runs/evidence/{source_type}/{source_row_id}"
    ))


def _top_keys(text: str) -> set[str]:
    """摘要文本顶层字段集合：用来证明不同来源渲染出口径一致。"""
    try:
        parsed = json.loads(text)
    except (TypeError, ValueError):
        return set()
    return set(parsed) if isinstance(parsed, dict) else set()


def same_shape(name: str, payload: dict[str, Any], source_type: str) -> None:
    """三条路径共用一把尺子：字段集合逐个相等 + 四个核心问题都在答案里。"""
    keys = frozenset(payload.keys())
    check(f"{name} 证据字段口径与协议一致", keys == EVIDENCE_KEYS,
          f"缺={sorted(EVIDENCE_KEYS - keys)} 多={sorted(keys - EVIDENCE_KEYS)}")
    check(f"{name} 证据来源标注正确", payload.get("sourceType") == source_type,
          str(payload.get("sourceType")))
    check(f"{name} 四个核心问题齐备",
          all(key in payload and payload.get(key) is not None for key in CORE_QUESTIONS),
          str({key: payload.get(key) for key in CORE_QUESTIONS}))


# --------------------------------------------------------------------- 0. 结构底座


def section_schema(conn: sqlite3.Connection) -> None:
    versions = applied_versions(conn)
    check("迁移 049 已应用", 49 in versions, str(sorted(versions)[-4:]))
    check("执行证据表已建立",
          conn.execute("SELECT name FROM sqlite_master WHERE name='biz_execution_evidence'"
                       ).fetchone() is not None)
    check("断言库表已建立",
          conn.execute("SELECT name FROM sqlite_master WHERE name='biz_assertion_rule'"
                       ).fetchone() is not None)
    item_cols = columns(conn, "biz_regression_item")
    check("回归条目带执行方式与证据列",
          {"exec_mode", "case_id", "env_id", "evidence_id"} <= item_cols, str(sorted(item_cols)))
    check("需求带功能点列（GAP-36）", "module_id" in columns(conn, "biz_requirement"))
    # 正向对照：结构扫描规则本身能用——不存在的表必须扫不出列，否则上面两条是空转
    check("结构扫描规则能命中缺失列", "exec_mode" not in columns(conn, "biz_regression"),
          str(columns(conn, "biz_regression")))
    check("证据保留期配置已入库",
          conn.execute("SELECT config_value FROM sys_config WHERE config_key ="
                       " 'execution.evidence_retention_days'").fetchone() is not None)


# --------------------------------------------------------------------- 1. GAP-32


def section_protocol(api: Api, conn: sqlite3.Connection, project_id: int,
                     env_id: int) -> None:
    # 1) 接口路径
    ok_case = make_case(api, project_id, "查询详情", "/ok", "json:$.code = 0")
    ran = api.data("POST", f"{V1}/test-cases/{ok_case}/interface-run", {"envId": env_id})
    check("接口执行返回证据 id", isinstance(ran.get("evidenceId"), int), str(ran)[:200])
    interface_evidence = evidence_of(api, "interface", int(ran["executionId"]))
    same_shape("接口", interface_evidence, "interface")
    check("接口证据带请求摘要", "/ok" in str(interface_evidence["requestSummary"]),
          str(interface_evidence["requestSummary"])[:200])
    check("接口证据带响应摘要", "statusCode" in str(interface_evidence["responseSummary"]),
          str(interface_evidence["responseSummary"])[:200])
    check("接口证据断言明细非空", len(interface_evidence["assertions"]) >= 2,
          str(interface_evidence["assertions"])[:200])
    check("接口证据带 traceId（联动 GAP-23）", bool(interface_evidence["traceId"]),
          str(interface_evidence["traceId"]))
    check("接口证据指回原表", interface_evidence["sourceRef"].get("table")
          == "biz_interface_execution", str(interface_evidence["sourceRef"])[:200])

    # 2) 自动化路径
    script = api.data("POST", f"{V1}/automation/scripts",
                      {"name": "证据链冒烟", "category": "api", "projectId": project_id,
                       "content": PY_OK})
    api.data("POST", f"{V1}/automation/scripts/{int(script['id'])}/run", {})
    auto_row = conn.execute(
        "SELECT source_row_id FROM biz_execution_evidence WHERE source_type = 'automation'"
        " ORDER BY id DESC LIMIT 1"
    ).fetchone()
    check("自动化执行落了证据快照", auto_row is not None)
    if auto_row is not None:
        auto_evidence = evidence_of(api, "automation", int(auto_row["source_row_id"]))
        same_shape("自动化", auto_evidence, "automation")
        check("自动化证据把 stdout 存成日志摘要",
              "hello-evidence-automation" in str(auto_evidence["logSummary"]),
              str(auto_evidence["logSummary"])[:200])
        check("自动化证据把命令行存成请求摘要",
              "commandLine" in str(auto_evidence["requestSummary"]),
              str(auto_evidence["requestSummary"])[:200])
        check("自动化成功结论归一成 passed", auto_evidence["result"] == "passed",
              str(auto_evidence["result"]))
        # 自动化证据必须带项目归属：否则「按项目筛证据 / 按项目设保留期」整类漏掉
        scoped = execution_protocol.evidence_counts(conn, project_id)
        check("自动化证据落在脚本所属项目口径内", scoped["automation"] >= 1, str(scoped)[:200])
        elsewhere = execution_protocol.evidence_counts(conn, 999999)
        check("按项目筛证据真的在筛（正向对照：别的项目看不到）",
              all(elsewhere[key] == 0 for key in execution_protocol.SOURCE_TYPES),
              str(elsewhere)[:200])

    # 3) 手工路径
    manual_case = make_case(api, project_id, "手工核对下单", "/ok", "")
    manual = api.data("POST", f"{V1}/test-cases/{manual_case}/execute", {
        "result": "pass", "remark": "复现了一次超时", "logSummary": "HTTP 500 order timeout",
        "durationMs": 1200, "environment": "证据链测试环境",
    })
    check("手工录入返回证据 id", isinstance(manual.get("evidenceId"), int), str(manual)[:200])
    manual_evidence = evidence_of(api, "manual", int(manual["executionId"]))
    same_shape("手工", manual_evidence, "manual")
    check("手工证据把备注存成日志摘要", "order timeout" in str(manual_evidence["logSummary"]),
          str(manual_evidence["logSummary"])[:200])
    check("手工证据结论口径归一", manual_evidence["result"] == "passed",
          str(manual_evidence["result"]))

    # 4) 手工零留痕不落快照（一期「只剩一句失败」的正向对照）
    bare = api.data("POST", f"{V1}/test-cases/{manual_case}/execute", {"result": "pass"})
    check("手工零留痕不落证据", bare.get("evidenceId") is None, str(bare)[:200])

    # 5) 附件证据：复用既有附件体系，不新建存储通道
    _, upload_body = api.call(
        "POST", f"{V1}/lab/attachments/document",
        data={"file": (io.BytesIO("现场截图说明".encode("utf-8")), "现场说明.txt")},
        content_type="multipart/form-data",
    )
    check("证据附件上传成功", upload_body.get("code") == ErrorCode.SUCCESS, str(upload_body)[:200])
    attachment_id = str((upload_body.get("data") or {}).get("attachmentId") or "")
    check("附件上传回具体编号", bool(attachment_id), str(upload_body)[:200])
    with_file = api.data("POST", f"{V1}/test-cases/{manual_case}/execute", {
        "result": "fail", "attachmentIds": [attachment_id],
    })
    file_evidence = evidence_of(api, "manual", int(with_file["executionId"]))
    check("手工证据挂上附件引用",
          len(file_evidence["attachments"]) == 1
          and file_evidence["attachments"][0]["attachmentId"] == attachment_id,
          str(file_evidence["attachments"])[:200])
    check("附件证据给可访问预览地址",
          str(file_evidence["attachments"][0].get("previewUrl") or "").startswith(f"{V1}/lab/"),
          str(file_evidence["attachments"])[:200])
    api.expect("失效附件编号被拒绝", "POST", f"{V1}/test-cases/{manual_case}/execute",
               ErrorCode.PARAM_ERROR, {"result": "pass", "attachmentIds": ["nope-不存在"]})

    # 6) 读取侧守门
    api.expect("未知执行来源被拒绝", "GET", f"{V1}/test-runs/evidence/teleport/1",
               ErrorCode.PARAM_ERROR)
    _, missing = api.call("GET", f"{V1}/test-runs/evidence/interface/999999")
    check("查不到证据时给中文提示而不是崩",
          missing.get("code") == ErrorCode.RESOURCE_NOT_FOUND
          and "保留期" in str(missing.get("message") or ""), str(missing)[:200])
    _, blank = api.call("GET", f"{V1}/test-runs/evidence?evidenceId=999999")
    check("按 id 查不到时回空对象（前端显示空态）",
          blank.get("code") == ErrorCode.SUCCESS and blank.get("data") is None, str(blank)[:200])
    api.expect("缺定位参数时给出可读提示", "GET", f"{V1}/test-runs/evidence",
               ErrorCode.PARAM_ERROR)

    # 7) 一次执行一份：重跑覆盖而不是堆快照
    api.data("POST", f"{V1}/test-cases/{ok_case}/interface-run", {"envId": env_id})
    count = int(conn.execute(
        "SELECT COUNT(*) FROM biz_execution_evidence WHERE source_type = 'interface'"
        " AND source_row_id IN (SELECT id FROM biz_interface_execution WHERE case_id = ?)",
        (ok_case,),
    ).fetchone()[0])
    rows = int(conn.execute(
        "SELECT COUNT(*) FROM biz_interface_execution WHERE case_id = ?", (ok_case,)
    ).fetchone()[0])
    check("一次执行一份证据（重跑不重复堆快照）", count == rows, f"证据={count} 执行={rows}")
    matrix = execution_protocol.field_matrix(conn, project_id)
    check("证据字段矩阵按全部来源给达标率",
          len(matrix["bySource"]) == len(execution_protocol.SOURCE_TYPES)
          and all("requiredFields" in item for item in matrix["bySource"]),
          str(matrix)[:300])
    check("字段矩阵有正向达标条目", matrix["compliant"] >= 1, str(matrix)[:200])


# --------------------------------------------------------------------- 2. GAP-33


def section_regression(api: Api, conn: sqlite3.Connection, project_id: int, env_id: int) -> int:
    pass_case = make_case(api, project_id, "回归真跑用例", "/ok", "json:$.code = 0")
    fail_case = make_case(api, project_id, "回归真跑必失败用例", "/ok", "json:$.code = 999")
    functional = api.data("POST", f"{V1}/test-cases", {
        "projectId": project_id, "title": "功能点回归（手工类型）", "caseType": "functional",
    })

    reg = api.data("POST", f"{V1}/regressions",
                   {"projectId": project_id, "title": "上线回归", "changeScope": "支付"})
    reg_id = int(reg["id"])
    auto_item = api.data("POST", f"{V1}/regressions/{reg_id}/items", {
        "title": "支付回调重放", "execMode": "auto", "caseId": pass_case, "envId": env_id,
    })
    manual_item = api.data("POST", f"{V1}/regressions/{reg_id}/items", {"title": "对账单人工核对"})

    # 1) 自动条目：客户端谎报 failed，服务端必须用真实执行结果覆盖
    executed = api.data("POST", f"{V1}/regressions/{reg_id}/execute", {"results": [
        {"itemId": int(auto_item["id"]), "result": "failed", "remark": "前端随口报的"},
        {"itemId": int(manual_item["id"]), "result": "not_executed"},
    ]})
    ran = (executed.get("executed") or [{}])[0]
    check("自动条目走服务端真跑并回结果", ran.get("itemId") == int(auto_item["id"])
          and ran.get("result") == "passed", str(executed)[:300])
    check("真跑产出接口执行流水", isinstance(ran.get("executionId"), int), str(ran)[:200])
    check("真跑回写真实断言明细", len(ran.get("assertions") or []) >= 2, str(ran)[:200])
    after = api.data("GET", f"{V1}/regressions/{reg_id}/items")
    item = next(row for row in after["list"] if row["id"] == int(auto_item["id"]))
    check("谎报结论被真实结果覆盖", item["result"] == "passed", str(item)[:200])
    check("自动条目回写执行方式", item["execMode"] == "auto", str(item)[:120])
    check("自动条目绑定证据 id", isinstance(item.get("evidenceId"), int), str(item)[:200])
    regression_evidence = evidence_of(api, "regression", int(auto_item["id"]))
    same_shape("回归", regression_evidence, "regression")
    check("回归证据与条目回写的证据是同一份",
          regression_evidence["evidenceId"] == item["evidenceId"],
          f"{regression_evidence['evidenceId']} vs {item['evidenceId']}")
    check("回归证据带真实请求摘要", "/ok" in str(regression_evidence["requestSummary"]),
          str(regression_evidence["requestSummary"])[:200])
    check("回归证据带真实响应摘要", "statusCode" in str(regression_evidence["responseSummary"]),
          str(regression_evidence["responseSummary"])[:200])

    # 同一次真跑在两条链路上必须渲染成同一份摘要（GAP-32「一套结构」的硬口径）
    twin = evidence_of(api, "interface", int(ran["executionId"]))
    iface_keys = _top_keys(str(twin["responseSummary"]))
    regression_keys = _top_keys(str(regression_evidence["responseSummary"]))
    check("接口与回归的响应摘要字段逐个相同",
          iface_keys == regression_keys >= {"statusCode", "headers", "body"},
          f"{sorted(iface_keys)} vs {sorted(regression_keys)}")
    check("接口与回归的断言明细条数相同",
          len(twin["assertions"]) == len(regression_evidence["assertions"]) >= 2,
          f"{len(twin['assertions'])} vs {len(regression_evidence['assertions'])}")

    # 2) 改绑定仍走 PUT，但结论只能由真跑写入：PUT 带 result 一律拒绝（不留假执行后门）
    api.data("PUT", f"{V1}/regressions/{reg_id}/items/{int(auto_item['id'])}", {
        "execMode": "auto", "caseId": fail_case, "envId": env_id,
    })
    lied = api.expect("自动条目 PUT 改结论被拒绝（不留假执行后门）", "PUT",
                      f"{V1}/regressions/{reg_id}/items/{int(auto_item['id'])}",
                      ErrorCode.ILLEGAL_OPERATION, {"result": "passed"})
    refuse_text = str(lied.get("message") or "")
    check("拒绝文案指向真跑入口与替代写法",
          "/execute" in refuse_text and "manual" in refuse_text, refuse_text[:220])
    untouched = api.data("GET", f"{V1}/regressions/{reg_id}/items")
    still = next(row for row in untouched["list"] if row["id"] == int(auto_item["id"]))
    check("被拒的 PUT 没有把结论写进去（正向对照：谎报落不进库）",
          still["result"] == "passed", str(still)[:200])
    check("PUT 改绑定仍然生效", still.get("caseId") == fail_case, str(still)[:200])

    # 真跑确实会失败：同一条改绑到必失败用例后由 execute 得出结论
    ran_fail = api.data("POST", f"{V1}/regressions/{reg_id}/execute",
                        {"results": [{"itemId": int(auto_item["id"])}]})
    failure = (ran_fail.get("executed") or [{}])[0]
    check("execute 真跑把结论改成 failed（结论只来自服务端）",
          failure.get("result") == "failed", str(ran_fail)[:300])
    after_fail = api.data("GET", f"{V1}/regressions/{reg_id}/items")
    failed_item = next(row for row in after_fail["list"] if row["id"] == int(auto_item["id"]))
    check("真跑结果已回写条目并换了指向新证据",
          failed_item["result"] == "failed"
          and isinstance(failed_item.get("evidenceId"), int), str(failed_item)[:250])
    refreshed = evidence_of(api, "regression", int(auto_item["id"]))
    check("同一条证据被真跑刷新（一次执行一份，不堆旧结论）",
          refreshed["result"] == "failed" and refreshed["evidenceId"] == failed_item["evidenceId"],
          f"{refreshed['result']} / {refreshed['evidenceId']}",)

    # 3) 手工条目：无证据不得标记通过（决策 4）
    rejected = api.expect("手工条目无证据标记通过被拒绝", "PUT",
                          f"{V1}/regressions/{reg_id}/items/{int(manual_item['id'])}",
                          ErrorCode.PARAM_ERROR, {"result": "passed", "remark": "看一眼"})
    message = str(rejected.get("message") or "")
    check("拒绝提示写清该补什么", "attachmentIds" in message and "logSummary" in message, message)
    api.expect("备注不算证据：仅备注仍被拒绝", "PUT",
               f"{V1}/regressions/{reg_id}/items/{int(manual_item['id'])}",
               ErrorCode.PARAM_ERROR, {"result": "passed", "remark": "只写了备注"})

    # 4) 批量回写同一条规则
    api.expect("批量回写无证据标记通过被拒绝", "POST", f"{V1}/regressions/{reg_id}/execute",
               ErrorCode.PARAM_ERROR,
               {"results": [{"itemId": int(manual_item["id"]), "result": "passed"}]})
    batched = api.data("POST", f"{V1}/regressions/{reg_id}/execute", {"results": [
        {"itemId": int(manual_item["id"]), "result": "passed",
         "logSummary": "对账单三方一致，截图已存"}]})
    check("带日志摘要的手工条目可标记通过",
          int(manual_item["id"]) in (batched.get("updated") or []), str(batched)[:200])
    manual_evidence = evidence_of(api, "regression", int(manual_item["id"]))
    check("手工条目证据记录的是提交里的证据",
          "三方一致" in str(manual_evidence["logSummary"]),
          str(manual_evidence["logSummary"])[:200])

    # 5) 定位缺失的守门（正向对照：auto 判定不是空转）
    api.expect("自动条目未绑定用例时给出可执行提示", "POST", f"{V1}/regressions/{reg_id}/items",
               ErrorCode.PARAM_ERROR, {"title": "没绑用例", "execMode": "auto"})
    api.expect("执行方式只能是 auto/manual", "POST", f"{V1}/regressions/{reg_id}/items",
               ErrorCode.PARAM_ERROR, {"title": "乱标", "execMode": "robot"})
    refused = api.expect("非接口用例不能标成自动执行", "POST", f"{V1}/regressions/{reg_id}/items",
                         ErrorCode.ILLEGAL_OPERATION,
                         {"title": "绑了功能用例", "caseId": int(functional["id"]),
                          "execMode": "auto"})
    check("拒绝文案点名要换成接口用例", "接口用例" in str(refused.get("message") or ""),
          str(refused.get("message"))[:200])
    detail = api.data("GET", f"{V1}/regressions/{reg_id}")
    coverage = detail.get("coverage") or {}
    check("回归清单给执行覆盖率与自动占比",
          {"executedRate", "autoRate", "evidenceRate"} <= set(coverage), str(coverage)[:200])
    if REGRESSION_VUE.is_file():
        view = REGRESSION_VUE.read_text(encoding="utf-8")
        # 用 \b 而不是子串：改名叫 autoShareX 也必须判红，子串比对会假绿
        missing = [key for key in COVERAGE_RATE_KEYS
                   if not re.search(rf"coverage\s*\?\.\s*{key}\b", view)]
        check("回归明细页把四个覆盖率都读到（字段名与后端一致）", not missing,
              f"界面未引用 {missing}；后端返回 {sorted(coverage)}")
        check("明细页的覆盖率用服务端口径而不是前端自算",
              "coverageRows" in view and re.search(r"detail\.value\?\.coverage\b", view) is not None,
              view[:120])
        # 只算不渲染等于没有界面：模板里必须真的挂上这块
        check("覆盖率面板渲染在回归明细抽屉里",
              '<el-descriptions v-if="detail.coverage"' in view
              and 'v-for="row in coverageRows"' in view
              and '{{ row.rateText }}' in view,
              view[view.find('<el-drawer v-model="detailVisible"'):view.find('<el-drawer v-model="detailVisible"') + 400])
        owners = [p.name for p in (SRC / "views").rglob("*.vue")
                  if all(re.search(rf"coverage\s*\?\.\s*{key}\b",
                                   p.read_text(encoding="utf-8", errors="ignore"))
                         for key in COVERAGE_RATE_KEYS)]
        check("四个覆盖率的消费方只有回归视图", owners == ["RegressionListView.vue"], str(owners))
    else:
        check("回归明细页存在", False, f"未找到 {REGRESSION_VUE}")
    check("手工条目也留下证据 id",
          any(row.get("evidenceId") for row in detail.get("items") or []),
          str(detail)[:200])
    return reg_id


# --------------------------------------------------------------------- 3. GAP-34


def section_assertions(api: Api, project_id: int, env_id: int,
                       module_id: int) -> None:
    strict = '{"type":"object","required":["id"],"properties":{"id":{"type":"string"}}}'
    bad_case = make_case(api, project_id, "响应结构校验（应为字符串）", "/ok",
                         f"schema:$.data = {strict}")
    detail = api.data("POST", f"{V1}/test-cases/{bad_case}/interface-run", {"envId": env_id})
    failed = [item for item in detail["assertions"] if not item["passed"]]
    check("schema 断言能判出结构不符", detail["result"] == "failed" and bool(failed),
          str(detail)[:300])
    text = json.dumps(failed, ensure_ascii=False)
    check("schema 失败给出具体路径差异", "$.id" in text, text[:300])
    check("schema 失败说明期望类型", "类型应为 string" in text, text[:300])
    check("schema 失败说明实际类型", "integer" in text, text[:300])

    good_case = make_case(api, project_id, "响应结构校验（符合）", "/typed",
                          f"schema:$.data = {strict}")
    good = api.data("POST", f"{V1}/test-cases/{good_case}/interface-run", {"envId": env_id})
    check("结构符合时 schema 断言通过", good["result"] == "passed", str(good)[:300])

    api.expect("坏 schema 直接给中文提示", "POST", f"{V1}/test-cases", ErrorCode.PARAM_ERROR, {
        "projectId": project_id, "title": "坏 schema", "caseType": "interface",
        "extra": {"接口URL": "/ok", "断言规则": "schema:$.data = {type:object"},
    })
    # 正向对照：拦的是「坏 schema」，不是所有 schema / 文本算子——否则上面那条是空转
    wellformed = api.data("POST", f"{V1}/test-cases", {
        "projectId": project_id, "title": "合法 schema 照存", "caseType": "interface",
        "extra": {"接口URL": "/ok", "请求方式": "GET", "断言规则": f"schema:$.data = {strict}"},
    })
    check("合法 JSON 断言规则照常入库（正向对照）", bool(wellformed.get("id")), str(wellformed)[:200])
    plain_rule = api.data("POST", f"{V1}/test-cases", {
        "projectId": project_id, "title": "纯文本算子不受影响", "caseType": "interface",
        "extra": {"接口URL": "/ok", "请求方式": "GET", "断言规则": "json:$.code = 0"},
    })
    check("非 schema 文本算子不被保存侧拦下（正向对照）", bool(plain_rule.get("id")),
          str(plain_rule)[:200])
    api.expect("schema 断言缺等号也给写法示例", "POST", f"{V1}/test-cases",
               ErrorCode.PARAM_ERROR, {
                   "projectId": project_id, "title": "缺等号", "caseType": "interface",
                   "extra": {"接口URL": "/ok", "断言规则": "schema:$.data"},
               })
    api.expect("改坏已有用例的 schema 同样被拒", "PUT", f"{V1}/test-cases/{bad_case}",
               ErrorCode.PARAM_ERROR,
               {"extra": {"接口URL": "/ok", "请求方式": "GET",
                          "断言规则": "schema:$ = [型]"}},)

    # 断言库：编号引用必须真的被展开
    rule = api.data("POST", f"{V1}/test-cases/assertion-rules", {
        "projectId": project_id, "name": "业务码为零", "rule": "json:$.code = 0",
        "description": "所有查询接口通用",
    })
    check("断言库返回编号", str(rule.get("ruleNo") or "").startswith("AR-"), str(rule)[:200])
    check("断言库返回引用串", rule.get("reference") == f"lib:{rule['ruleNo']}", str(rule)[:200])
    lib_case = make_case(api, project_id, "引用断言库", "/ok", f"lib:{rule['ruleNo']}")
    lib_detail = api.data("POST", f"{V1}/test-cases/{lib_case}/interface-run", {"envId": env_id})
    check("断言库引用被展开成真实规则",
          any(item["name"] == "JSONPath $.code" and item["passed"]
              for item in lib_detail["assertions"]),
          str(lib_detail["assertions"])[:300])

    module_rule = api.data("POST", f"{V1}/test-cases/assertion-rules", {
        "projectId": project_id, "moduleId": module_id, "name": "功能点级断言",
        "rule": "json:$.data.name = alpha",
    })
    visible = api.data("GET",
                       f"{V1}/test-cases/assertion-rules?projectId={project_id}&moduleId={module_id}")
    numbers = {item["ruleNo"] for item in visible["list"]}
    check("功能点级断言对该功能点可见", module_rule["ruleNo"] in numbers, str(numbers))
    check("项目级断言对项目内功能点也可见", rule["ruleNo"] in numbers, str(numbers))
    project_only = api.data("GET", f"{V1}/test-cases/assertion-rules?projectId={project_id}")
    check("不传功能点时只给项目级断言",
          module_rule["ruleNo"] not in {item["ruleNo"] for item in project_only["list"]},
          str(project_only)[:200])

    api.expect("断言规则不能为空", "POST", f"{V1}/test-cases/assertion-rules",
               ErrorCode.PARAM_ERROR, {"projectId": project_id, "name": "空规则"})
    api.expect("断言库里不能再引用断言库", "POST", f"{V1}/test-cases/assertion-rules",
               ErrorCode.PARAM_ERROR,
               {"projectId": project_id, "name": "套娃", "rule": f"lib:{rule['ruleNo']}"})
    other = api.data("POST", f"{V1}/projects", {"code": "EVID2", "name": "断言库隔壁项目"})
    api.expect("跨项目功能点不能挂断言", "POST", f"{V1}/test-cases/assertion-rules",
               ErrorCode.ILLEGAL_OPERATION,
               {"projectId": int(other["id"]), "moduleId": module_id, "name": "脏引用",
                "rule": "status = 200"})

    ghost_case = make_case(api, project_id, "引用不存在的断言", "/ok", "lib:AR-9999")
    api.expect("引用不存在的断言编号被拒绝", "POST",
               f"{V1}/test-cases/{ghost_case}/interface-run", ErrorCode.RESOURCE_NOT_FOUND,
               {"envId": env_id})
    disabled = api.data("POST", f"{V1}/test-cases/assertion-rules", {
        "projectId": project_id, "name": "将被停用的断言", "rule": "status = 200",
    })
    disable_case = make_case(api, project_id, "引用已停用断言", "/ok", f"lib:{disabled['ruleNo']}")
    api.data("PUT", f"{V1}/test-cases/assertion-rules/{int(disabled['id'])}", {"enabled": False})
    api.expect("引用已停用的断言编号被拒绝", "POST",
               f"{V1}/test-cases/{disable_case}/interface-run", ErrorCode.ILLEGAL_OPERATION,
               {"envId": env_id})
    api.data("DELETE", f"{V1}/test-cases/assertion-rules/{int(disabled['id'])}")
    after_delete = api.data("GET", f"{V1}/test-cases/assertion-rules?projectId={project_id}")
    check("删除后断言库条目不再可见",
          disabled["ruleNo"] not in {item["ruleNo"] for item in after_delete["list"]},
          str([item["ruleNo"] for item in after_delete["list"]])[:200])

    # 断言库页要能直接复用列表组件：出参必须是全站分页口径，且 keyword 真的在筛
    paged = api.data("GET",
                     f"{V1}/test-cases/assertion-rules?projectId={project_id}"
                     f"&moduleId={module_id}&pageSize=1")
    check("断言库清单给前端表格分页口径",
          {"list", "total", "pageNum", "pageSize", "pages"} <= set(paged)
          and len(paged["list"]) == 1 and int(paged["total"]) >= 2
          and int(paged["pages"]) >= 2, str(paged)[:220])
    paged_kw = api.data(
        "GET", f"{V1}/test-cases/assertion-rules?projectId={project_id}&keyword=业务码")
    check("断言库按关键字筛只命中一条（正向对照：搜索不是摆设）",
          int(paged_kw["total"]) == 1 and paged_kw["list"][0]["name"] == "业务码为零",
          str(paged_kw)[:220])
    paged_miss = api.data(
        "GET", f"{V1}/test-cases/assertion-rules?projectId={project_id}&keyword=不存在的关键字")
    check("断言库筛不匹配时为空（正向对照）", int(paged_miss["total"]) == 0, str(paged_miss)[:200])
    api.expect("断言库清单必须带项目", "GET", f"{V1}/test-cases/assertion-rules",
               ErrorCode.PARAM_ERROR)
    renamed = api.data("PUT", f"{V1}/test-cases/assertion-rules/{int(rule['id'])}",
                       {"name": "业务码为零（改名）", "description": "改过了"})
    check("断言库可改名并回显",
          renamed["name"] == "业务码为零（改名）" and renamed["description"] == "改过了",
          str(renamed)[:200])
    api.expect("断言库改成坏 schema 被拒", "PUT", f"{V1}/test-cases/assertion-rules/{int(rule['id'])}",
               ErrorCode.PARAM_ERROR, {"rule": "schema:$ = {type:object"})


# --------------------------------------------------------------------- 4. GAP-35


def section_bug_evidence(api: Api, conn: sqlite3.Connection, project_id: int,
                         env_id: int) -> None:
    fail_case = make_case(api, project_id, "注定失败的接口用例", "/bad", "json:$.code = 0")
    run = api.data("POST", f"{V1}/test-runs",
                   {"projectId": project_id, "name": "证据链轮次", "caseIds": [fail_case],
                    "envId": env_id})
    run_id = int(run["id"])
    rows = api.data("GET", f"{V1}/test-runs/{run_id}/cases")
    run_case = (rows.get("list") or rows)[0]
    run_case_id = int(run_case["id"])
    detail = api.data("POST", f"{V1}/test-runs/{run_id}/cases/{run_case_id}/interface-run",
                      {"envId": env_id})
    check("轮次接口执行返回证据 id", isinstance(detail.get("evidenceId"), int), str(detail)[:250])

    bug = api.data("POST", f"{V1}/test-runs/{run_id}/cases/{run_case_id}/bug", {})
    check("失败转缺陷回证据 id", isinstance(bug.get("evidenceId"), int), str(bug)[:250])
    bug_id = int(bug["bugId"])
    description = str(conn.execute(
        "SELECT description FROM biz_bug WHERE id = ?", (bug_id,)
    ).fetchone()[0] or "")
    check("缺陷描述带执行证据块", "执行证据" in description, description[:200])
    check("缺陷描述带证据编号", f"EV-{bug['evidenceId']}" in description, description[:250])
    check("缺陷描述带失败断言", "失败断言" in description or "断言" in description,
          description[:250])
    check("缺陷描述给下钻地址", f"{V1}/test-runs/evidence/" in description, description[:300])

    by_run_case = api.data("GET", f"{V1}/test-runs/evidence?runCaseId={run_case_id}")
    by_bug = api.data("GET", f"{V1}/test-runs/evidence?bugId={bug_id}")
    by_source = evidence_of(api, "interface", int(detail["executionId"]))
    run_evidence = api.data("GET", f"{V1}/test-runs/{run_id}/evidence")
    check("轮次 / 缺陷 / 来源三处下钻到同一份证据",
          by_run_case["evidenceId"] == bug["evidenceId"] == by_source["evidenceId"],
          f"{by_run_case['evidenceId']} / {bug['evidenceId']} / {by_source['evidenceId']}")
    check("缺陷维度能查到证据清单",
          any(item["evidenceId"] == bug["evidenceId"] for item in by_bug["list"]),
          str(by_bug)[:250])
    check("轮次维度能查到同一份证据",
          any(item["evidenceId"] == bug["evidenceId"] for item in run_evidence["list"]),
          str(run_evidence)[:250])
    check("证据反向挂上 bugId",
          int(conn.execute("SELECT COUNT(*) FROM biz_execution_evidence WHERE bug_id = ?",
                           (bug_id,)).fetchone()[0]) >= 1)
    empty = api.data("GET", f"{V1}/test-runs/evidence?bugId=999999")
    check("无关缺陷查不到证据（正向对照）", empty["list"] == [], str(empty)[:150])

    # 缺陷详情自己的读口：详情页不必知道 runCaseId / executionId 也能拿到证据
    bug_evidence = api.data("GET", f"{V1}/bugs/{bug_id}/evidence")
    check("缺陷详情有证据读口", bug_evidence["total"] >= 1
          and bug_evidence["latest"] is not None, str(bug_evidence)[:200])
    check("缺陷读口的最新一份就是转缺陷时挂上的那份",
          int((bug_evidence.get("latest") or {}).get("evidenceId") or 0) == int(bug["evidenceId"]),
          f"{(bug_evidence.get('latest') or {}).get('evidenceId')} vs {bug['evidenceId']}")
    check("缺陷读口给出可下钻的原始请求摘要",
          "/bad" in str((bug_evidence.get("latest") or {}).get("requestSummary") or ""),
          str((bug_evidence.get("latest") or {}).get("requestSummary"))[:200])
    bug_detail = api.data("GET", f"{V1}/bugs/{bug_id}")
    check("缺陷详情回显证据条数", int(bug_detail.get("evidenceCount") or 0) >= 1,
          str(bug_detail)[:200])
    orphan = api.data("POST", f"{V1}/bugs", {"projectId": project_id, "title": "手工提的缺陷"})
    orphan_evidence = api.data("GET", f"{V1}/bugs/{int(orphan['id'])}/evidence")
    check("没有证据的缺陷读口给空清单与原因（正向对照）",
          orphan_evidence["total"] == 0 and orphan_evidence["latest"] is None
          and "证据" in str(orphan_evidence.get("hint") or ""), str(orphan_evidence)[:250])
    api.expect("缺陷不存在时读口报 404 而不是空清单", "GET", f"{V1}/bugs/999999/evidence",
               ErrorCode.RESOURCE_NOT_FOUND)


# --------------------------------------------------------------------- 5. GAP-36


def section_requirement_module(api: Api, project_id: int, module_id: int, env_id: int) -> None:
    requirement = api.data("POST", f"{V1}/requirements", {
        "projectId": project_id, "title": "支付回调幂等", "moduleId": module_id,
    })
    check("需求可挂功能点", int(requirement["moduleId"]) == module_id, str(requirement)[:200])
    check("需求返回功能点路径", bool(requirement.get("modulePath")), str(requirement)[:200])
    fetched = api.data("GET", f"{V1}/requirements/{int(requirement['id'])}")
    check("需求详情回显功能点", int(fetched["moduleId"]) == module_id, str(fetched)[:200])
    listed = api.data("GET", f"{V1}/requirements?projectId={project_id}&moduleId={module_id}")
    check("需求列表可按功能点筛", any(row["id"] == requirement["id"] for row in listed["list"]),
          str(listed)[:200])
    other = api.data("GET", f"{V1}/requirements?projectId={project_id}&moduleId=999999")
    check("筛不存在的功能点返回空（正向对照）",
          all(row["id"] != requirement["id"] for row in other["list"]), str(other)[:200])
    cleared = api.data("PUT", f"{V1}/requirements/{int(requirement['id'])}", {"moduleId": None})
    check("需求可摘掉功能点", cleared.get("moduleId") is None, str(cleared)[:200])
    back = api.data("PUT", f"{V1}/requirements/{int(requirement['id'])}", {"moduleId": module_id})
    check("需求可重新挂上功能点", int(back["moduleId"]) == module_id, str(back)[:200])
    neighbor = api.data("POST", f"{V1}/projects", {"code": "EVID3", "name": "追溯隔壁项目"})
    api.expect("跨项目功能点不能挂需求", "POST", f"{V1}/requirements",
               ErrorCode.ILLEGAL_OPERATION,
               {"projectId": int(neighbor["id"]), "title": "脏引用", "moduleId": module_id})

    # 蛇形写法也必须过同一套归属校验：WRITE_FIELDS 收了 module_id，但不能绕过检查
    neighbor_module = api.data("POST", f"{V1}/modules", {
        "projectId": int(neighbor["id"]), "name": "隔壁功能点", "nodeType": "module",
    })
    api.expect("蛇形 module_id 不能绕过跨项目校验", "PUT",
               f"{V1}/requirements/{int(requirement['id'])}", ErrorCode.ILLEGAL_OPERATION,
               {"module_id": int(neighbor_module["id"])})
    snake_back = api.data("PUT", f"{V1}/requirements/{int(requirement['id'])}",
                          {"module_id": None})
    check("蛇形写法可摘掉功能点", snake_back.get("moduleId") is None, str(snake_back)[:200])
    snake_ok = api.data("PUT", f"{V1}/requirements/{int(requirement['id'])}",
                        {"module_id": module_id})
    check("蛇形 module_id 确实落库（正向对照：不是被丢掉的字段）",
          int(snake_ok["moduleId"]) == module_id, str(snake_ok)[:200])

    # 功能点口径要同时看得见需求与用例：先在本功能点下建用例并真跑一次留证据
    module_case = make_case(api, project_id, "功能点下的接口用例", "/ok",
                             "json:$.code = 0", module_id=module_id)
    sibling = api.data("POST", f"{V1}/modules", {
        "projectId": project_id, "name": "退款/对账", "nodeType": "module",
    })
    sibling_id = int(sibling["id"])
    other_case = make_case(api, project_id, "别的功能点的用例", "/ok",
                            "json:$.code = 0", module_id=sibling_id)
    ran = api.data("POST", f"{V1}/test-cases/{module_case}/interface-run", {"envId": env_id})
    check("功能点下的用例真跑留下证据 id", isinstance(ran.get("evidenceId"), int), str(ran)[:200])

    matrix = api.data("GET", f"{V1}/traceability/modules?projectId={project_id}")
    row: dict[str, Any] = next((item for item in matrix["list"] if item["moduleId"] == module_id), {})
    check("功能点矩阵给需求口径", int(row.get("requirementCount") or 0) >= 1, str(row)[:250])
    check("功能点矩阵给用例口径", int(row.get("caseCount") or 0) >= 1, str(row)[:250])
    check("功能点矩阵两类口径同时在场",
          {"requirementCount", "subtreeRequirementCount", "caseCount", "subtreeCaseCount"}
          <= set(row), str(sorted(row))[:300])
    check("未挂功能点的需求单独计数",
          "unboundRequirements" in (matrix.get("summary") or {}), str(matrix.get("summary"))[:200])

    drill = api.data("GET", f"{V1}/traceability/module/{module_id}")
    check("功能点下钻列出需求",
          any(item["id"] == int(requirement["id"]) for item in drill["requirements"]),
          str(drill["requirements"])[:250])
    check("功能点下钻列出用例", bool(drill["cases"]), str(drill)[:250])
    check("下钻用例就是挂在功能点上的那一条",
          any(item["id"] == module_case for item in drill["cases"]), str(drill["cases"])[:250])
    check("用例带最近证据 id（追溯可下钻到证据）",
          any(item.get("evidenceId") for item in drill["cases"]),
          str(drill["cases"])[:250])
    drill_evidence = evidence_of(api, "interface", int(ran["executionId"]))
    bound: dict[str, Any] = next(
        (item for item in drill["cases"] if item["id"] == module_case), {})
    check("下钻给的证据 id 与真跑那份是同一个",
          int(bound.get("evidenceId") or 0) == int(drill_evidence["evidenceId"]),
          f"{bound.get('evidenceId')} vs {drill_evidence['evidenceId']}")
    # 正向对照：别的功能点的用例不得混进本功能点下钻（否则「按功能点下钻」是假的）
    check("别的功能点的用例不出现在本功能点下钻（正向对照）",
          all(item["id"] != other_case for item in drill["cases"]),
          str(drill["cases"])[:250])
    sibling_drill = api.data("GET", f"{V1}/traceability/module/{sibling_id}")
    check("下钻按功能点各归各（正向对照）",
          any(item["id"] == other_case for item in sibling_drill["cases"])
          and all(item["id"] != module_case for item in sibling_drill["cases"]),
          str(sibling_drill["cases"])[:250])
    sibling_row: dict[str, Any] = next(
        (item for item in matrix["list"] if item["moduleId"] == sibling_id), {})
    check("矩阵的用例口径按功能点分开", int(sibling_row.get("caseCount") or 0) >= 1,
          str(matrix["list"])[:300])
    api.expect("不存在的功能点给出明确提示", "GET", f"{V1}/traceability/module/999999",
               ErrorCode.RESOURCE_NOT_FOUND)


# --------------------------------------------------------------------- 6. 保留期


def section_retention(api: Api, conn: sqlite3.Connection, project_id: int,
                      env_id: int) -> None:
    execution_protocol.set_retention_days(conn, 30, project_id)
    # 门禁自己的连接写完必须立刻放锁：否则 test_client 的写请求会撞 database is locked
    conn.commit()
    check("项目级保留期写入配置",
          execution_protocol.retention_days(conn, project_id) == 30,
          str(execution_protocol.retention_days(conn, project_id)))
    check("未覆盖项目回落到全局默认", execution_protocol.retention_days(conn, 999999) >= 1)
    scoped_case = make_case(api, project_id, "保留期用例", "/ok", "json:$.code = 0")
    scoped = api.data("POST", f"{V1}/test-cases/{scoped_case}/interface-run", {"envId": env_id})
    evidence_id = int(scoped["evidenceId"])
    execution_id = int(scoped["executionId"])
    row = conn.execute("SELECT expires_at FROM biz_execution_evidence WHERE id = ?",
                       (evidence_id,)).fetchone()
    check("按项目保留期写入到期时间", bool(str(row["expires_at"] or "")), str(row)[:120])

    conn.execute(
        "UPDATE biz_execution_evidence SET expires_at = '2000-01-01T00:00:00Z' WHERE id = ?",
        (evidence_id,),
    )
    conn.commit()
    check("到期证据按 id 直查不可见", execution_protocol.get_evidence(
        conn, evidence_id) is None)
    api.expect("到期证据按来源查给出明确提示", "GET",
               f"{V1}/test-runs/evidence/interface/{execution_id}",
               ErrorCode.RESOURCE_NOT_FOUND)
    purged = execution_protocol.purge_expired(conn)
    conn.commit()
    check("清理任务只处理到期条目",
          purged["matched"] >= 1 and evidence_id in purged["ids"], str(purged)[:200])
    state = conn.execute("SELECT is_deleted, purge_reason FROM biz_execution_evidence WHERE id = ?",
                         (evidence_id,)).fetchone()
    check("到期条目软删并留原因",
          int(state["is_deleted"]) == 1 and "保留期" in str(state["purge_reason"]),
          str(dict(state))[:200])
    check("清理不是物理删",
          conn.execute("SELECT COUNT(*) FROM biz_execution_evidence WHERE id = ?",
                       (evidence_id,)).fetchone()[0] == 1)
    dry = execution_protocol.purge_expired(conn, dry_run=True)
    check("试运行不动数据", dry["purged"] == 0 and dry["matched"] == 0, str(dry)[:150])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-evidence-"))
    server, port = start_server()
    try:
        app = build_app(workdir)
        api = Api(app)
        base = f"http://127.0.0.1:{port}"
        conn = sqlite3.connect(workdir / "evidence.db")
        conn.row_factory = sqlite3.Row

        section_schema(conn)

        env = api.data("POST", f"{V1}/test-envs", {
            "name": "证据链测试环境", "kind": "test",
            "app": {"baseUrl": base}, "db": {"type": "sqlite", "host": ""},
        })
        env_id = int(env["id"])
        project = api.data("POST", f"{V1}/projects", {"code": "EVID", "name": "证据链项目"})
        project_id = int(project["id"])
        module = api.data("POST", f"{V1}/modules", {
            "projectId": project_id, "name": "支付/回调", "nodeType": "module",
        })
        module_id = int(module["id"])

        section_protocol(api, conn, project_id, env_id)
        section_regression(api, conn, project_id, env_id)
        section_assertions(api, project_id, env_id, module_id)
        section_bug_evidence(api, conn, project_id, env_id)
        section_requirement_module(api, project_id, module_id, env_id)
        section_retention(api, conn, project_id, env_id)

        counts = execution_protocol.evidence_counts(conn, project_id)
        # 探索性来源由 check_exploratory_web.py 专门覆盖，这里只要求传统四类落库
        legacy_sources = [key for key in execution_protocol.SOURCE_TYPES
                          if key != execution_protocol.SOURCE_EXPLORATORY]
        check("四类来源都有证据落库",
              all(counts[key] >= 1 for key in legacy_sources), str(counts))
        conn.close()
    finally:
        try:
            server.shutdown()
            server.server_close()
        except Exception:  # noqa: BLE001 - 清理失败不影响结果
            pass
    print(f"\nPASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
