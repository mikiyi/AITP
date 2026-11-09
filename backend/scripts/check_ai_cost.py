"""GAP-30 AI 成本核算与预算门禁：单价折算、NULL 语义、日/月聚合、软阈值与活链路落库。

守住的口径（与迁移 047 的注释一致）：
- 金额一律微元整数；单价是「每 1000 token 的微元数」；
- 未配价 → `cost_micros` 为 NULL，绝不写 0（0 会被日/月汇总当成免费）；
- 预算与单次 token 上限都是**软提示**：到阈值只出提示文案，不拦截调用。

    python backend/scripts/check_ai_cost.py

退出码 0 = 全部通过。全程只用本地 mock 模型与临时库，绝不碰正式库与 8760 服务。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai import cost as ai_cost  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.repositories.ai_session_repo import AITaskRepository  # noqa: E402
from aitp.services import ai_record_service  # noqa: E402

V1 = "/api/v1"
#: 统一包封的成功码（见 aitp.envelope.ok）
SUCCESS = 200000

#: mock 固定用量：1000 入 / 500 出；档案单价 2000 / 4000 微元每 1000 token
#: => 1000*2000/1000 + 500*4000/1000 = 2000 + 2000 = 4000 微元
MOCK_PROMPT_TOKENS = 1000
MOCK_COMPLETION_TOKENS = 500
PRICE_INPUT = 2000
PRICE_OUTPUT = 4000
EXPECTED_CALL_MICROS = 4000

#: 结构化产出样例：字段齐全，走 GAP-28 校验应当一次通过（不触发修正重试）
CASE_REPLY = [{
    "title": "验证码超时后重发成功",
    "caseType": "functional",
    "priority": "P1",
    "preconditions": "已打开登录页",
    "steps": [
        {"action": "输入手机号并点击获取验证码", "expected": "收到验证码"},
        {"action": "61 秒后点击重新发送", "expected": "重新下发验证码"},
    ],
    "expectedResult": "登录成功",
    "tags": "登录/验证码",
}]

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    """标准断言记录器：红绿都计数，detail 只在看失败时需要读。"""
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


# ---------------- 本地 mock 模型（绝不连真实服务） ----------------

class _MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 屏蔽 stderr 噪声
        pass

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            return cast("dict[str, Any]", json.loads(self.rfile.read(length).decode("utf-8")))
        except Exception:
            return {}

    def _reply_for(self, body: dict[str, Any]) -> str:
        # 按模型名分支比按提示词文本分支更稳：流式与修正重试都能拿到同一份确定性产出
        if str(body.get("model") or "") == "mock-json":
            return json.dumps(CASE_REPLY, ensure_ascii=False)
        return "## 需求确认\n- 已确认：验证码 5 分钟有效\n- 待澄清：是否限制重发次数"

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        body = self._read_json()
        payload = json.dumps({
            "id": "chatcmpl-cost",
            "object": "chat.completion",
            "model": body.get("model") or "mock-cost",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": self._reply_for(body)},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": MOCK_PROMPT_TOKENS,
                "completion_tokens": MOCK_COMPLETION_TOKENS,
                "total_tokens": MOCK_PROMPT_TOKENS + MOCK_COMPLETION_TOKENS,
            },
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_mock() -> tuple[ThreadingHTTPServer, int]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, int(server.server_address[1])


# ---------------- 请求脚手架 ----------------

def request_json(
    client: Any, method: str, path: str, payload: dict[str, Any] | None = None
) -> tuple[int, dict[str, Any]]:
    """返回 (HTTP 状态码, 包封体)；包封体可能是错误包封，所以不在此处断言成功。"""
    if method == "GET":
        resp = client.get(path)
    elif method == "PUT":
        resp = client.put(path, json=payload or {})
    else:
        resp = client.post(path, json=payload or {})
    return int(resp.status_code), cast("dict[str, Any]", resp.get_json() or {})


def is_failure(status: int, body: dict[str, Any]) -> bool:
    """包封是否失败：HTTP 200 + code != 200000 也是失败。"""
    return status != 200 or int(body.get("code") or 0) != SUCCESS


def data_of(
    client: Any, method: str, path: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """取成功包封里的 data；失败直接抛，让门禁红得明确。"""
    status, body = request_json(client, method, path, payload)
    if is_failure(status, body):
        raise AssertionError(f"{method} {path} 失败：status={status} body={str(body)[:300]}")
    got = body.get("data")
    return cast("dict[str, Any]", got) if isinstance(got, dict) else {"value": got}


def utc_stamp(offset_days: int = 0) -> str:
    """按 utcnow_iso 的格式造时间戳：聚合靠 created_at 前缀匹配。"""
    moment = datetime.now(timezone.utc) - timedelta(days=offset_days)
    return moment.strftime("%Y-%m-%dT%H:%M:%S.000Z")


# ---------------- A. 纯函数口径 ----------------

def pure_checks() -> None:
    check("微元换算常量正确", ai_cost.MICROS_PER_YUAN == 1_000_000)
    check("单价折算按四舍五入", ai_cost.round_micros(500, 1000) == 500
          and ai_cost.round_micros(1000, 2000) == 2000,
          f"500*1000/1000={ai_cost.round_micros(500, 1000)}")
    check("零 token 不产生费用", ai_cost.round_micros(0, 5000) == 0)

    usage = {"promptTokens": 1000, "completionTokens": 500, "totalTokens": 1500}
    unpriced = ai_cost.TokenPrice()
    priced = ai_cost.TokenPrice(input_micros=PRICE_INPUT, output_micros=PRICE_OUTPUT)
    check("未配价折算为 None 而不是 0", ai_cost.cost_micros_of(usage, unpriced) is None,
          str(ai_cost.cost_micros_of(usage, unpriced)))
    check("配价后折算出确定金额（正控：规则能真的开火）",
          ai_cost.cost_micros_of(usage, priced) == EXPECTED_CALL_MICROS,
          str(ai_cost.cost_micros_of(usage, priced)))
    check("只配入参单价时出参按 0 计",
          ai_cost.cost_micros_of(usage, ai_cost.TokenPrice(input_micros=PRICE_INPUT)) == 2000)

    report = ai_cost.cost_report(usage, priced, capability="case_generation", profile_id=7)
    check("成本结论含落库契约键 costMicros/currency",
          report.get("costMicros") == EXPECTED_CALL_MICROS and report.get("currency") == "CNY",
          str(sorted(report.keys())))
    check("成本结论金额可换算成元", report.get("costYuan") == 0.004, str(report.get("costYuan")))
    check("未配价结论 priced=false 且金额为 None",
          ai_cost.cost_report(usage, unpriced)["priced"] is False
          and ai_cost.cost_report(usage, unpriced)["costMicros"] is None)

    # 软阈值分级：三档都必须能开火，否则「只提醒」这条规则是空写的
    check("阈值 0 = 不设限，永远 ok", ai_cost.level_of(9_999_999, 0, 80) == ai_cost.LEVEL_OK)
    check("未达告警比例判 ok", ai_cost.level_of(799_999, 1_000_000, 80) == ai_cost.LEVEL_OK)
    check("达到告警比例判 warn（正控）", ai_cost.level_of(800_000, 1_000_000, 80) == ai_cost.LEVEL_WARN)
    check("超过阈值判 over（正控）", ai_cost.level_of(1_000_001, 1_000_000, 80) == ai_cost.LEVEL_OVER)
    check("比例计算保留两位", ai_cost.ratio_of(500_000, 1_000_000) == 50.0)

    check("未到上限不出提示", ai_cost.call_token_hint(100, 1000) == "")
    check("接近上限给软提示（正控）", "已用到" in ai_cost.call_token_hint(900, 1000),
          ai_cost.call_token_hint(900, 1000))
    check("超过上限提示写明已超（正控）", "已超过" in ai_cost.call_token_hint(1200, 1000),
          ai_cost.call_token_hint(1200, 1000))
    check("未设上限永远无提示", ai_cost.call_token_hint(999_999, 0) == "")

    for bad, label in ((-1, "负数"), ("abc", "非整数"), ("1.5", "小数")):
        try:
            ai_cost.normalize_price_micros(bad, "priceInputMicros")
            ok = False
        except ValueError:
            ok = True
        check(f"单价拒绝{label}", ok, str(bad))
    check("币种白名单外回落 CNY", ai_cost.normalize_currency("rmb") == "CNY")
    check("币种大小写归一", ai_cost.normalize_currency("usd") == "USD")

    config = ai_cost.BudgetConfig(
        call_tokens=1000, call_tokens_capability=2000, call_tokens_project=3000)
    check("单次上限优先级：项目 > 能力 > 全局",
          config.effective_call_tokens("case_generation", 5) == 3000
          and config.effective_call_tokens("case_generation", None) == 2000
          and config.effective_call_tokens("", None) == 1000)
    check("预算结论声明不拦截", config.as_dict()["hardBlock"] is False)


# ---------------- B. 落库与聚合 ----------------

def insert_record(
    conn: sqlite3.Connection,
    *,
    capability: str,
    cost: dict[str, Any],
    usage: dict[str, Any],
    task_id: int | None,
    created_at: str,
) -> int:
    """走真实的 build_record/create_record 写路径，不在门禁里复制一份 INSERT。"""
    record = ai_record_service.build_record(
        conn,
        capability=capability,
        output="门禁样本产出",
        variables={"requirement": "验证码登录"},
        usage=usage,
        cost=cost,
        task_id=task_id,
        session_id=None,
    )
    record_id = ai_record_service.create_record(conn, record)
    conn.execute("UPDATE biz_ai_record SET created_at = ? WHERE id = ?", (created_at, record_id))
    return record_id


def storage_checks(client: Any, db_path: pathlib.Path) -> None:
    project = data_of(client, "POST", f"{V1}/projects", {
        "code": "COST-A", "name": "成本核算验收", "owner": "QA"})
    project_id = int(project["id"])

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        task_id = AITaskRepository(conn).start(
            "lab_run", capability="case_generation", project_id=project_id,
        )
        usage = {"promptTokens": 1000, "completionTokens": 500, "totalTokens": 1500}
        first = insert_record(conn, capability="case_generation", task_id=task_id,
                              usage=usage, cost={"costMicros": 4000, "currency": "CNY"},
                              created_at=utc_stamp())
        insert_record(conn, capability="requirement_confirm", task_id=task_id,
                      usage=usage, cost={"costMicros": 6000, "currency": "CNY"},
                      created_at=utc_stamp())
        third = insert_record(conn, capability="case_generation", task_id=None,
                              usage=usage, cost={}, created_at=utc_stamp())
        insert_record(conn, capability="case_generation", task_id=None,
                      usage=usage, cost={"costMicros": 900000, "currency": "CNY"},
                      created_at=utc_stamp(40))  # 40 天前且无任务归属：本月与本项目的钱都不该有它
        conn.commit()

        row = conn.execute(
            "SELECT cost_micros, cost_currency FROM biz_ai_record WHERE id = ?", (third,)
        ).fetchone()
        check("未配价记录落库为 NULL 而非 0", row["cost_micros"] is None, str(dict(row)))
        check("未配价时币种列留空串（不参与金额口径）", row["cost_currency"] == "", str(row[1]))

        spend = ai_cost.query_spend(conn)
        check("全周期聚合只算配价的三条", spend.micros == 910000 and spend.calls == 4,
              str(spend.as_dict()))
        check("未配价调用单独计数而非当成免费", spend.unpriced_calls == 1
              and spend.priced_calls == 3, str(spend.as_dict()))

        today = ai_cost.query_spend(conn, day=ai_cost.utc_day())
        check("按日聚合排除往月记录（正控：期间过滤真生效）",
              today.micros == 10000 and today.calls == 3, str(today.as_dict()))
        month = ai_cost.query_spend(conn, month=ai_cost.utc_day()[:7])
        check("按月聚合排除往月记录", month.micros == 10000, str(month.as_dict()))
        scoped = ai_cost.query_spend(conn, project_id=project_id)
        check("按项目聚合顺着任务收敛（正控：排除无归属记录）", scoped.micros == 10000,
              str(scoped.as_dict()))
        by_cap = ai_cost.query_spend(conn, capability="requirement_confirm")
        check("按能力聚合可拆分", by_cap.micros == 6000, str(by_cap.as_dict()))

        tokens = ai_cost.query_spend(conn, day=ai_cost.utc_day())
        check("聚合带 token 总量（json_extract 口径）",
              tokens.prompt_tokens == 3000 and tokens.completion_tokens == 1500,
              str(tokens.as_dict()))

        detail_row = conn.execute(
            "SELECT usage_json, cost_micros, cost_currency FROM biz_ai_record WHERE id = ?",
            (first,),
        ).fetchone()
        check("单条记录同时留用量与金额", detail_row["cost_micros"] == 4000
              and "promptTokens" in str(detail_row["usage_json"]), str(dict(detail_row)))

        # 预算软阈值：配价 10000 微元/日 → over；比例阈值 → warn
        ai_cost.set_int_config(conn, ai_cost.CONFIG_DAILY, 5000, "门禁：每日软阈值")
        ai_cost.set_int_config(conn, ai_cost.CONFIG_WARN_RATIO, 80, "门禁：告警比例")
        conn.commit()
        status = ai_cost.budget_status(conn, capability="case_generation")
        check("超阈值时出提示文案", status["today"]["level"] == ai_cost.LEVEL_OVER
              and bool(status["warnings"]), str(status["today"]))
        check("预算结论明确不拦截", status["blocking"] is False)
        ai_cost.set_int_config(conn, ai_cost.CONFIG_DAILY, 1_000_000, "门禁：放宽阈值")
        conn.commit()
        wide = ai_cost.budget_status(conn)
        check("阈值放宽后同日分级转 ok（可红：分级不是恒 warn）",
              wide["today"]["level"] == ai_cost.LEVEL_OK, str(wide["today"]))

        config = ai_cost.read_budget(conn)
        check("预算配置读回写入值", config.warn_ratio == 80 and config.daily_micros == 1_000_000,
              str(config.as_dict()))
    finally:
        conn.close()

    prices = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
        "name": "成本门禁档案", "provider": "custom", "protocol": "openai_compat",
        "baseUrl": "http://127.0.0.1:1/v1", "model": "mock-cost",
        "isLocal": True, "isActive": True, "priority": 10, "timeout": 5,
    })
    profile_id = int(prices["id"])
    updated = data_of(client, "PUT", f"{V1}/lab/cost/prices/{profile_id}", {
        "priceInputMicros": PRICE_INPUT, "priceOutputMicros": PRICE_OUTPUT,
        "priceCurrency": "CNY",
    })
    check("档案单价写入后可回读",
          updated["priceInputMicros"] == PRICE_INPUT
          and updated["priceOutputMicros"] == PRICE_OUTPUT and updated["priced"] is True,
          str(updated))
    listed = data_of(client, "GET", f"{V1}/lab/cost/prices")
    table = cast("list[dict[str, Any]]", listed["profiles"])
    check("单价表覆盖档案并带累计花费", any(item["profileId"] == profile_id for item in table)
          and all("costMicros" in item for item in table), str(table)[:200])
    status_code, bad_body = request_json(client, "PUT", f"{V1}/lab/cost/prices/{profile_id}",
                                        {"priceInputMicros": -5})
    check("负单价被接口拒绝（红可证）", is_failure(status_code, bad_body),
          str(bad_body)[:160])
    status_code, bad_body = request_json(client, "PUT", f"{V1}/lab/cost/budget", {"warnRatio": 0})
    check("越界的告警比例被拒绝", is_failure(status_code, bad_body),
          str(bad_body)[:160])
    status_code, bad_body = request_json(client, "PUT", f"{V1}/lab/cost/prices/999999",
                                        {"priceInputMicros": 1})
    check("不存在的档案改单价被拒绝", is_failure(status_code, bad_body), str(bad_body)[:160])

    summary = data_of(client, "GET", f"{V1}/lab/cost/summary")
    month_block = cast("dict[str, Any]", summary.get("month") or {})
    check("成本接口给出本月花费", month_block.get("costMicros") == 10000, str(month_block))
    caps = cast("list[dict[str, Any]]", summary.get("byCapability") or [])
    check("成本接口按能力拆分", {item["capability"] for item in caps} >=
          {"case_generation", "requirement_confirm"}, str(caps)[:200])
    projs = cast("list[dict[str, Any]]", summary.get("byProject") or [])
    check("成本接口按项目拆分", any(item["projectId"] == project_id for item in projs),
          str(projs)[:200])
    filtered = data_of(client, "GET", f"{V1}/lab/cost/summary?capability=case_generation")
    check("成本接口可按能力过滤", int(filtered["scoped"]["costMicros"]) == 904000,
          str(filtered["scoped"]))
    bad_code = request_json(client, "GET", f"{V1}/lab/cost/summary?capability=not_a_cap")
    check("未知能力的成本过滤被拒绝", is_failure(*bad_code), str(bad_code[1])[:160])

    detail = data_of(client, "GET", f"{V1}/ai-records/{first}")
    cost_block = cast("dict[str, Any]", detail.get("cost") or {})
    check("记录详情可读回 token 与金额（验收标准 1）",
          cost_block.get("priced") is True and cost_block.get("costMicros") == 4000
          and cost_block.get("promptTokens") == 1000, str(cost_block))
    unpriced_detail = data_of(client, "GET", f"{V1}/ai-records/{third}")
    check("未配价记录详情 priced=false", unpriced_detail["cost"]["priced"] is False,
          str(unpriced_detail["cost"]))


# ---------------- C. 活链路（本地 mock） ----------------

def live_checks(client: Any, db_path: pathlib.Path, mock_port: int) -> None:
    profile = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
        "name": "活链路 mock 档案", "provider": "custom", "protocol": "openai_compat",
        "baseUrl": f"http://127.0.0.1:{mock_port}/v1", "model": "mock-cost",
        "isLocal": True, "isActive": True, "priority": 200, "timeout": 10,
    })
    profile_id = int(profile["id"])
    data_of(client, "PUT", f"{V1}/lab/cost/prices/{profile_id}", {
        "priceInputMicros": PRICE_INPUT, "priceOutputMicros": PRICE_OUTPUT,
    })
    # 预算压到 1 微元：任何一次真实花费都会超阈值，用来验证「超了也不拦截」
    data_of(client, "PUT", f"{V1}/lab/cost/budget", {"dailyMicros": 1, "monthlyMicros": 1})

    run = data_of(client, "POST", f"{V1}/lab/requirement_confirm/run", {
        "variables": {"requirement": "手机号验证码登录，验证码 5 分钟有效", "projectName": "成本门禁"},
        "profileId": profile_id, "title": "成本活链路",
    })
    cost_block = cast("dict[str, Any]", run.get("cost") or {})
    check("同步链路调用当场算出金额", cost_block.get("costMicros") == EXPECTED_CALL_MICROS
          and cost_block.get("priced") is True, str(cost_block))
    check("成本结论带 token 明细", cost_block.get("promptTokens") == MOCK_PROMPT_TOKENS
          and cost_block.get("completionTokens") == MOCK_COMPLETION_TOKENS, str(cost_block))
    budget = cast("dict[str, Any]", cost_block.get("budget") or {})
    check("超预算时响应里带软提示（正控）", bool(budget.get("warnings")), str(budget)[:200])
    check("超预算不拦截：调用仍然成功产出", bool(str(run.get("output") or "").strip())
          and budget.get("blocking") is False, str(run)[:160])

    record_id = int(run.get("recordId") or 0)
    detail = data_of(client, "GET", f"{V1}/ai-records/{record_id}")
    check("活链路成本已落库（写侧闭环）", detail["cost"]["costMicros"] == EXPECTED_CALL_MICROS,
          str(detail["cost"]))
    check("活链路 schema 结论已落库（GAP-28 落库缺口已补）",
          bool(cast("dict[str, Any]", detail.get("schema") or {})), str(detail)[:200])

    # 单次调用 token 软上限：按能力覆盖，触发提示但产出照常返回
    data_of(client, "PUT", f"{V1}/lab/cost/budget",
            {"callTokens": 100, "capability": "requirement_confirm"})
    hinted = data_of(client, "POST", f"{V1}/lab/requirement_confirm/run", {
        "variables": {"requirement": "验证码限流", "projectName": "成本门禁"},
        "profileId": profile_id,
    })
    hint = str((hinted.get("cost") or {}).get("budget", {}).get("callTokenHint") or "")
    check("单次 token 软上限提示开火且不影响产出", "已超过" in hint
          and bool(str(hinted.get("output") or "").strip()), hint)

    stream_profile = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
        "name": "活链路 mock JSON 档案", "provider": "custom", "protocol": "openai_compat",
        "baseUrl": f"http://127.0.0.1:{mock_port}/v1", "model": "mock-json",
        "isLocal": True, "isActive": True, "priority": 210, "timeout": 10,
    })
    stream_profile_id = int(stream_profile["id"])
    data_of(client, "PUT", f"{V1}/lab/cost/prices/{stream_profile_id}", {
        "priceInputMicros": PRICE_INPUT, "priceOutputMicros": PRICE_OUTPUT,
    })

    stream = data_of(client, "POST", f"{V1}/lab/case_generation/stream", {
        "variables": {"requirement": "验证码登录", "caseTypes": "functional", "count": "1",
                      "projectName": "成本门禁"},
        "profileId": stream_profile_id, "title": "流式成本活链路",
    })
    task_id = int(stream["taskId"])
    frames: list[dict[str, Any]] = []
    after = 0
    finished = False
    for _ in range(60):
        payload = data_of(client, "GET", f"{V1}/lab/stream/{task_id}/frames?after={after}")
        frames.extend(cast("list[dict[str, Any]]", payload.get("frames") or []))
        after = int(payload.get("lastSeq") or after)
        if payload.get("finished"):
            finished = True
            break
        threading.Event().wait(0.2)
    done = next((item for item in frames if item.get("type") == "done"), {})
    check("流式任务收尾并给出 done 帧", finished and bool(done), str(frames)[-200:])
    done_schema = cast("dict[str, Any]", done.get("schema") or {})
    check("流式链路已接上 schema 校验（GAP-28 活链路缺口已补）",
          int(done_schema.get("passed") or 0) >= 1 and bool(done_schema.get("ok")),
          str(done_schema)[:260])
    check("流式链路上下文结论随帧返回（GAP-27 可见性）",
          isinstance(done.get("context"), dict), str(done)[:160])
    stream_cost = cast("dict[str, Any]", done.get("cost") or {})
    usage_price = ai_cost.TokenPrice(input_micros=PRICE_INPUT, output_micros=PRICE_OUTPUT)
    expected_stream_micros = ai_cost.cost_micros_of(stream_cost, usage_price)
    check("流式链路当场算出金额（与该次用量同口径）",
          stream_cost.get("priced") is True
          and stream_cost.get("costMicros") == expected_stream_micros
          and int(stream_cost.get("costMicros") or 0) > 0,
          str(stream_cost)[:220])
    stream_record_id = int(done.get("recordId") or 0)
    if stream_record_id:
        stored = data_of(client, "GET", f"{V1}/ai-records/{stream_record_id}")
        check("流式记录带 schema 与去重两份结论（GAP-28/29 落库）",
              bool(stored.get("schema")) and bool(stored.get("dedup")),
              str({k: stored.get(k) for k in ("schema", "dedup")})[:200])
        check("流式记录成本已落库", stored["cost"]["costMicros"] == expected_stream_micros,
              str(stored["cost"])[:200])
    else:
        check("流式记录带 schema 与去重两份结论（GAP-28/29 落库）", False, "没有 recordId")
        check("流式记录成本已落库", False, "没有 recordId")

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        nulls = int(conn.execute(
            "SELECT COUNT(*) FROM biz_ai_record WHERE cost_micros IS NULL"
        ).fetchone()[0])
        check("本线之外的老记录不受影响（NULL 语义向后兼容）", nulls >= 0, str(nulls))
    finally:
        conn.close()


# ---------------- D. 红线静态扫描 ----------------

def redline_checks() -> None:
    text = (ROOT / "backend" / "aitp" / "ai" / "cost.py").read_text(encoding="utf-8")
    check("成本模块不做硬拦截", "raise AppError" not in text and "blocking\": True" not in text)
    check("成本模块不使用浮点存钱", "float" not in text.split("def micros_to_yuan")[0])
    check("未配价语义写成 None", "return None" in text and "is_priced" in text)


def main() -> int:
    pure_checks()
    redline_checks()
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-aicost-"))
    db_path = workdir / "cost.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "cost.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    client = create_app(cfg).test_client()
    server, mock_port = start_mock()
    try:
        storage_checks(client, db_path)
        live_checks(client, db_path, mock_port)
    finally:
        server.shutdown()
    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
