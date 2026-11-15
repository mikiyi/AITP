"""本地（自托管）模型不参与计费门禁：单价与 Token 套餐只属于云端档案。

用户的判断很直接：本地模型跑在自己机器上，一分钱不花，「模型档案单价」里出现
本地档案本身就是设计错误。这里把口径钉死在四处，防止任何一侧漏改：

  1) 判定：`cost.is_local_profile` = 档案列 `is_local` 或供应商内置本地标记，
     与 `resolver` 的本地判定同一口径（只看列会漏掉 provider=ollama 的老档案）；
  2) 写侧：创建/更新档案时，本地档案的单价、套餐额度一律落 0，`billing_mode` 归 api；
  3) 读侧：成本单价表把本地档案标成「不计费」，展示币种也不被本地残留值带偏；
  4) 折算：本地档案即使库里残留过单价（老库/手改库），实跑的成本仍是 NULL 而不是钱。

    python backend/scripts/check_ai_local_billing.py

全程只用临时库与本机 mock 模型，绝不碰正式库、正式服务与云端额度。
"""

from __future__ import annotations

import json
import pathlib
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

from aitp.ai import cost as ai_cost  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402

V1 = "/api/v1"
SUCCESS = 200000
PRICE_INPUT = 2000
PRICE_OUTPUT = 4000

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


# ---------------- 本机 mock 模型（绝不打云端） ----------------

class _MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 屏蔽 stderr 噪声
        pass

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = cast("dict[str, Any]", json.loads(self.rfile.read(length).decode("utf-8")))
        except Exception:
            body = {}
        payload = json.dumps({
            "id": "chatcmpl-local",
            "object": "chat.completion",
            "model": body.get("model") or "mock-local",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": "## 需求确认\n- 已确认：门禁样本"},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 1000, "completion_tokens": 500, "total_tokens": 1500},
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

def request_json(client: Any, method: str, path: str,
                 payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    if method == "GET":
        resp = client.get(path)
    elif method == "PUT":
        resp = client.put(path, json=payload or {})
    else:
        resp = client.post(path, json=payload or {})
    return int(resp.status_code), cast("dict[str, Any]", resp.get_json() or {})


def is_failure(status: int, body: dict[str, Any]) -> bool:
    return status != 200 or int(body.get("code") or 0) != SUCCESS


def data_of(client: Any, method: str, path: str,
            payload: dict[str, Any] | None = None) -> dict[str, Any]:
    status, resp = request_json(client, method, path, payload)
    if is_failure(status, resp):
        raise AssertionError(f"{method} {path} 失败：status={status} body={str(resp)[:300]}")
    got = resp.get("data")
    return cast("dict[str, Any]", got) if isinstance(got, dict) else {"value": got}


def code_of(client: Any, method: str, path: str,
            payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    """只取包封码，用于断言「这条写路径确实被挡住」。"""
    status, resp = request_json(client, method, path, payload)
    return int(resp.get("code") or 0), resp


def local_profile(name: str, base_url: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name, "provider": "ollama", "protocol": "openai_compat",
        "baseUrl": base_url, "model": "mock-local",
        "isLocal": True, "isActive": True, "priority": 300, "timeout": 10,
    }
    payload.update(extra)
    return payload


def inject_legacy_price(db_path: pathlib.Path, profile_id: int) -> None:
    """模拟老库/手改库里「本地档案却带着单价」的残骸：绕过接口直接写列。"""
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE sys_ai_profile SET price_input_micros = ?, price_output_micros = ?,"
            " billing_mode = 'token_plan', token_quota = 999 WHERE id = ?",
            (PRICE_INPUT, PRICE_OUTPUT, profile_id),
        )
        conn.commit()
    finally:
        conn.close()


def table_row(client: Any, profile_id: int) -> dict[str, Any]:
    listed = data_of(client, "GET", f"{V1}/lab/cost/prices")
    rows = cast("list[dict[str, Any]]", listed["profiles"])
    return next(row for row in rows if int(row["profileId"]) == profile_id)


def main() -> int:
    # ---------- ① 判定口径 ----------
    check("档案列 is_local 判本地",
          ai_cost.is_local_profile({"is_local": 1, "provider": "custom"}))
    check("供应商内置本地标记也判本地（ollama 老档案没写列也算）",
          ai_cost.is_local_profile({"is_local": 0, "provider": "ollama"}))
    check("云端档案判非本地（正控：规则不是永远为真）",
          ai_cost.is_local_profile({"is_local": 0, "provider": "custom"}) is False)
    check("空档案按非本地处理", ai_cost.is_local_profile(None) is False)

    # ---------- 建库与接口 ----------
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-localbill-"))
    db_path = workdir / "local.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "local.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    client = create_app(cfg).test_client()
    server, mock_port = start_mock()
    base_url = f"http://127.0.0.1:{mock_port}/v1"
    try:
        # ---------- ② 写侧：创建本地档案不接受计费字段 ----------
        local = data_of(client, "POST", f"{V1}/settings/ai/profiles", local_profile(
            "本地档案（带单价传参）", base_url,
            priceInputMicros=PRICE_INPUT, priceOutputMicros=PRICE_OUTPUT,
            billingMode="token_plan", tokenQuota=999,
        ))
        local_id = int(local["id"])
        check("本地档案创建后单价落 0",
              local["priceInputMicros"] == 0 and local["priceOutputMicros"] == 0, str(local))
        check("本地档案创建后计费模式归 api、套餐额度落 0",
              local["billingMode"] == "api" and local["tokenQuota"] == 0, str(local))
        check("本地档案创建后 isLocal 为真", bool(local["isLocal"]), str(local))

        cloud = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
            "name": "云端档案", "provider": "custom", "protocol": "openai_compat",
            "baseUrl": base_url, "model": "mock-cloud", "apiKey": "gate-mock-key",
            "isActive": True, "priority": 290, "timeout": 10,
            "priceInputMicros": PRICE_INPUT, "priceOutputMicros": PRICE_OUTPUT,
            "billingMode": "token_plan", "tokenQuota": 5000,
        })
        cloud_id = int(cloud["id"])
        check("云端档案的单价与套餐额度照常写入（正控）",
              cloud["priceInputMicros"] == PRICE_INPUT and cloud["billingMode"] == "token_plan"
              and cloud["tokenQuota"] == 5000, str(cloud))

        # ---------- ③ 单价接口：本地拒绝、云端放行 ----------
        code, resp = code_of(client, "PUT", f"{V1}/lab/cost/prices/{local_id}",
                             {"priceInputMicros": PRICE_INPUT, "priceOutputMicros": PRICE_OUTPUT})
        check("给本地档案配单价被拒绝", code == int(ErrorCode.ILLEGAL_OPERATION), str(resp)[:200])
        check("拒绝文案写明本地不计费", "本地模型" in str(resp.get("message") or ""),
              str(resp.get("message")))
        priced = data_of(client, "PUT", f"{V1}/lab/cost/prices/{cloud_id}",
                         {"priceInputMicros": PRICE_INPUT, "priceOutputMicros": PRICE_OUTPUT})
        check("给云端档案配单价仍然成功（正控）",
              priced["priceInputMicros"] == PRICE_INPUT and priced["priced"] is True, str(priced))

        # ---------- ④ 更新侧：云端转本地顺带清掉历史计费 ----------
        switched = data_of(client, "PUT", f"{V1}/settings/ai/profiles/{cloud_id}",
                           {"isLocal": True, "billingMode": "token_plan", "tokenQuota": 5000})
        check("云端档案转为本地后单价清零",
              switched["priceInputMicros"] == 0 and switched["priceOutputMicros"] == 0,
              str(switched))
        check("云端档案转为本地后套餐额度清零、模式归 api",
              switched["billingMode"] == "api" and switched["tokenQuota"] == 0, str(switched))

        # ---------- ⑤ 读侧：成本单价表 ----------
        local_row = table_row(client, local_id)
        check("单价表把本地档案标成不计费",
              local_row["isLocal"] is True and local_row["priced"] is False, str(local_row))
        switched_row = table_row(client, cloud_id)
        check("转为本地后单价表同样按不计费呈现",
              switched_row["isLocal"] is True and switched_row["priced"] is False,
              str(switched_row))

        priced_cloud = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
            "name": "云端美元档案", "provider": "custom", "protocol": "openai_compat",
            "baseUrl": base_url, "model": "mock-cloud-2", "apiKey": "gate-mock-key",
            "isActive": False, "priority": 1, "timeout": 10,
            "priceInputMicros": PRICE_INPUT, "priceCurrency": "USD",
        })
        priced_cloud_id = int(priced_cloud["id"])
        check("云端档案仍然能配单价（正控：清零不是全局失效）",
              priced_cloud["priceInputMicros"] == PRICE_INPUT, str(priced_cloud))

        # ---------- ⑥ 老库残骸：本地档案带着单价也不参与成本与币种 ----------
        inject_legacy_price(db_path, local_id)
        residue = table_row(client, local_id)
        check("本地档案残留单价仍按不计费呈现",
              residue["priced"] is False and residue["priceInputMicros"] == 0, str(residue))
        check("本地档案残留套餐额度也按 api/不限量呈现",
              residue["billingMode"] == "api" and residue["tokenQuota"] == 0, str(residue))
        scoped = get_standalone_conn(db_path)
        try:
            check("展示币种不被本地残留单价带偏（取云端档案的 USD）",
                  ai_cost.currency_of(scoped) == "USD", ai_cost.currency_of(scoped))
        finally:
            scoped.close()

        run = data_of(client, "POST", f"{V1}/lab/requirement_confirm/run", {
            "variables": {"requirement": "验证码登录，验证码 5 分钟有效", "projectName": "本地不计费门禁"},
            "profileId": local_id, "title": "本地不计费活链路",
        })
        cost_block = cast("dict[str, Any]", run.get("cost") or {})
        check("本地档案实跑成本为 None（不是按残留单价算钱）",
              cost_block.get("costMicros") is None and cost_block.get("priced") is False,
              str(cost_block))
        record_id = int(run.get("recordId") or 0)
        detail = data_of(client, "GET", f"{V1}/ai-records/{record_id}")
        check("本地档案记录落库 cost_micros 为 NULL",
              detail["cost"]["costMicros"] is None, str(detail["cost"]))
        check("单价表把云端档案继续按配价呈现（正控）",
              table_row(client, priced_cloud_id)["priced"] is True, "")
        check("本地档案实跑仍然成功产出（计费口径变化不影响可用性）",
              bool(str(run.get("output") or "").strip()), str(run)[:160])
    finally:
        server.shutdown()

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
