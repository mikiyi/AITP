"""Token Plan（套餐/订阅额度）模式门禁：计费模式、额度扣减、不计金额、字段暴露。

口径（迁移 060）：
- billing_mode='api'        : 按量 API，按档案单价折算金额（现状不变）；
- billing_mode='token_plan' : 套餐/订阅额度，同一 OpenAI 兼容接口 + 套餐凭证，
  按额度扣减 token、显示剩余，cost_micros 落 NULL（不按量计金额）。

全部在系统临时目录的独立 sqlite 库上跑（init_db 应用 060），不碰 data/aitp.db、不调模型。

    python backend/scripts/check_token_plan.py

退出码：FAIL=0 为 0，任何 FAIL 为 1。
"""

from __future__ import annotations

import pathlib
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.db.migrator import init_db  # noqa: E402
from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.repositories.ai_profile_repo import AIProfileRepository  # noqa: E402
from aitp.services import ai_record_service  # noqa: E402
from aitp.ai import cost as ai_cost  # noqa: E402
from aitp.ai import resolver  # noqa: E402

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


def record(pid: int, tokens: int, micros: int | None) -> dict[str, Any]:
    return {
        "taskId": None, "capability": "case_generation", "model": "m",
        "usage": {"promptTokens": tokens // 2, "completionTokens": tokens // 2,
                  "totalTokens": tokens},
        "cost": {"profileId": pid, "costMicros": micros, "currency": "CNY"},
    }


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="aitp-tokenplan-"))
    db = tmp / "tp.db"
    init_db(db)
    conn = get_standalone_conn(db)
    repo = AIProfileRepository(conn)

    tp = repo.insert({"name": "套餐", "provider": "custom", "protocol": "openai_compat",
                      "base_url": "http://x", "model": "m", "billing_mode": "token_plan",
                      "token_quota": 10000, "token_used": 0})
    api = repo.insert({"name": "按量", "provider": "custom", "protocol": "openai_compat",
                       "base_url": "http://x", "model": "m", "billing_mode": "api",
                       "price_input_micros": 1000, "price_output_micros": 2000})
    conn.commit()

    # 字段暴露
    pub = AIProfileRepository.to_public(repo.get_by_id(tp) or {})
    check("to_public 暴露 billingMode", pub.get("billingMode") == "token_plan", str(pub.get("billingMode")))
    check("to_public 暴露额度/剩余", pub.get("tokenQuota") == 10000 and pub.get("tokenRemaining") == 10000,
          str(pub))

    # resolver 携带计费模式
    cfg = resolver.resolve(profile=repo.get_by_id(tp), require_key=False)
    check("resolver 携带 billing_mode", cfg.is_token_plan, cfg.billing_mode)
    check("resolver 携带额度", cfg.token_quota == 10000, str(cfg.token_quota))

    # 套餐调用：扣减 + 不计金额
    rid = ai_record_service.create_record(conn, record(tp, 1200, 999))
    conn.commit()
    row = repo.get_by_id(tp) or {}
    check("套餐按 totalTokens 扣减", int(row.get("token_used") or 0) == 1200, str(row.get("token_used")))
    check("套餐剩余正确", ai_cost.token_plan_summary(row)["tokenRemaining"] == 8800, "")
    cm = conn.execute("SELECT cost_micros FROM biz_ai_record WHERE id=?", (rid,)).fetchone()[0]
    check("套餐 cost_micros 为 NULL", cm is None, str(cm))

    # 按量调用：保留金额、不扣额度
    rid2 = ai_record_service.create_record(conn, record(api, 1000, 1500))
    conn.commit()
    cm2 = conn.execute("SELECT cost_micros FROM biz_ai_record WHERE id=?", (rid2,)).fetchone()[0]
    check("按量 cost_micros 保留", cm2 == 1500, str(cm2))
    check("按量不扣额度", int((repo.get_by_id(api) or {}).get("token_used") or 0) == 0, "")

    # 成本面板带套餐口径
    table = {r["profileId"]: r for r in ai_cost.price_table(conn)}
    check("price_table 带套餐口径", table[tp]["billingMode"] == "token_plan"
          and table[tp]["tokenRemaining"] == 8800, str(table.get(tp)))

    # 归一与边界
    check("billing_mode 归一回落 api", ai_cost.normalize_billing_mode("bogus") == "api", "")
    check("未设额度剩余为0", ai_cost.token_plan_summary({"billing_mode": "token_plan", "token_quota": 0})["tokenRemaining"] == 0, "")

    conn.close()
    print(f"\nPASS={_passed} FAIL={_failed}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
