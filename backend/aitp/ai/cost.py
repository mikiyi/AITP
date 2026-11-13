"""GAP-30 AI 成本核算与预算：单价口径、微元折算、日/月汇总与软阈值判定。

口径（与迁移 `047_ai_run_reports.sql` 的注释严格一致）：

- 金额一律用「微元」整数（1 元 = 1_000_000 微元），避免浮点误差与四舍五入争议；
- 单价是「每 1000 token 的微元数」，分入参 / 出参两项，存在 `sys_ai_profile`；
- **两个单价都为 0 = 未配价**：此时 `cost_micros` 落 NULL 而不是 0，
  因为 0 会被日/月汇总当成「免费」计入，把未配价伪装成零成本；
- 预算是**软阈值**：只给提示，绝不拦截调用（工程清单第 7 节决策 3），
  单次调用 token 上限同理，只做「接近/超出」提示，不强制降级。

配置键（047 已播种前四个，其余按需 upsert，无需新迁移）：

- `ai.budget.daily_micros` / `ai.budget.monthly_micros`：日 / 月软阈值（微元，0=不设限）
- `ai.budget.warn_ratio`：达到阈值的百分之几开始提示（1~100）
- `ai.budget.call_tokens`：单次调用 token 软上限（全局）
- `ai.budget.call_tokens.<capability>`：按能力覆盖
- `ai.budget.call_tokens.project.<projectId>`：按项目覆盖
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Mapping

MICROS_PER_YUAN = 1_000_000
TOKENS_PER_UNIT = 1000

CONFIG_DAILY = "ai.budget.daily_micros"
CONFIG_MONTHLY = "ai.budget.monthly_micros"
CONFIG_WARN_RATIO = "ai.budget.warn_ratio"
CONFIG_CALL_TOKENS = "ai.budget.call_tokens"
CALL_TOKENS_CAP_PREFIX = "ai.budget.call_tokens."
CALL_TOKENS_PROJECT_PREFIX = "ai.budget.call_tokens.project."

DEFAULT_WARN_RATIO = 80
MIN_WARN_RATIO = 1
MAX_WARN_RATIO = 100
SUPPORTED_CURRENCIES = ("CNY", "USD", "EUR")
DEFAULT_CURRENCY = "CNY"

LEVEL_OK = "ok"
LEVEL_WARN = "warn"
LEVEL_OVER = "over"
LEVEL_LABELS = {LEVEL_OK: "未触及", LEVEL_WARN: "接近阈值", LEVEL_OVER: "已超阈值"}

#: 记录表没有 project_id，项目维度只能顺着产生它的任务收敛（与 ai_record_repo 同一口径）
PROJECT_SCOPE_SQL = (
    " AND EXISTS (SELECT 1 FROM biz_ai_task t WHERE t.id = biz_ai_record.task_id"
    " AND t.is_deleted = 0 AND t.project_id = ?)"
)


# ---------------- 单价 ----------------

@dataclass(slots=True)
class TokenPrice:
    """一个模型档案的 token 单价（微元 / 1000 token）。"""

    input_micros: int = 0
    output_micros: int = 0
    currency: str = DEFAULT_CURRENCY

    @property
    def is_priced(self) -> bool:
        """两项单价都为 0 视为未配价：未配价只报 token，不报钱。"""
        return self.input_micros > 0 or self.output_micros > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "priced": self.is_priced,
            "priceInputMicros": self.input_micros,
            "priceOutputMicros": self.output_micros,
            "priceCurrency": self.currency,
            "priceUnit": "微元 / 1000 token",
            "priceInputYuan": micros_to_yuan(self.input_micros),
            "priceOutputYuan": micros_to_yuan(self.output_micros),
        }


def micros_to_yuan(micros: int | None) -> float | None:
    """微元转元（展示用，6 位小数）；未配价保持 None，不能塌成 0。"""
    if micros is None:
        return None
    return round(int(micros) / MICROS_PER_YUAN, 6)


def normalize_currency(value: Any) -> str:
    text = str(value or "").strip().upper()
    return text if text in SUPPORTED_CURRENCIES else DEFAULT_CURRENCY


def normalize_price_micros(value: Any, name: str) -> int:
    """单价必须是非负整数微元；容错接受字符串，但不接受小数与负数。"""
    text = str(value if value is not None else "").strip()
    if not text:
        return 0
    try:
        number = int(text)
    except ValueError as exc:
        raise ValueError(f"{name} 必须是整数微元") from exc
    if number < 0:
        raise ValueError(f"{name} 不能为负数")
    return number


def price_of_profile(row: Mapping[str, Any] | None) -> TokenPrice:
    """从档案行取单价；列缺失（047 之前的老库）就是未配价。"""
    if not row:
        return TokenPrice()
    try:
        return TokenPrice(
            input_micros=normalize_price_micros(row.get("price_input_micros"), "priceInputMicros"),
            output_micros=normalize_price_micros(row.get("price_output_micros"), "priceOutputMicros"),
            currency=normalize_currency(row.get("price_currency")),
        )
    except ValueError:
        # 库里存了脏值（手改库/迁移中断）不该让成本核算整体崩，退化成未配价
        return TokenPrice()


def price_for_profile(conn: sqlite3.Connection, profile_id: int | None) -> TokenPrice:
    """按档案 id 读单价；没有档案 id（走环境变量的兜底候选）就是未配价。"""
    if not profile_id:
        return TokenPrice()
    row = conn.execute(
        "SELECT price_input_micros, price_output_micros, price_currency"
        " FROM sys_ai_profile WHERE id = ?",
        (int(profile_id),),
    ).fetchone()
    if row is None:
        return TokenPrice()
    return price_of_profile(dict(row))


# ---------------- Token Plan（套餐/订阅额度，迁移 060） ----------------

BILLING_API = "api"
BILLING_TOKEN_PLAN = "token_plan"
SUPPORTED_BILLING_MODES = (BILLING_API, BILLING_TOKEN_PLAN)


def normalize_billing_mode(value: Any) -> str:
    text = str(value or "").strip().lower()
    return text if text in SUPPORTED_BILLING_MODES else BILLING_API


def is_token_plan_row(row: Mapping[str, Any] | None) -> bool:
    return normalize_billing_mode((row or {}).get("billing_mode")) == BILLING_TOKEN_PLAN


def token_plan_summary(row: Mapping[str, Any] | None) -> dict[str, Any]:
    """套餐额度口径：总额 / 已用 / 剩余；未设额度（0）= 不限量，只统计不扣减。"""
    quota = int((row or {}).get("token_quota") or 0)
    used = int((row or {}).get("token_used") or 0)
    return {
        "billingMode": normalize_billing_mode((row or {}).get("billing_mode")),
        "tokenQuota": quota,
        "tokenUsed": used,
        "tokenRemaining": max(0, quota - used) if quota > 0 else 0,
        "unlimited": quota <= 0,
    }


def deduct_token_plan(conn: sqlite3.Connection, profile_id: int | None, total_tokens: int) -> int:
    """套餐模式按本次 totalTokens 累加已用额度，返回扣减后的剩余（未设额度返回 0）。

    只更新 token_used，不动金额；并发调用靠 SQLite 行锁串行化，累加用 SQL 自增避免读改写竞态。
    """
    if not profile_id or total_tokens <= 0:
        return 0
    conn.execute(
        "UPDATE sys_ai_profile SET token_used = token_used + ?,"
        " updated_at = updated_at WHERE id = ? AND billing_mode = 'token_plan'",
        (int(total_tokens), int(profile_id)),
    )
    row = conn.execute(
        "SELECT token_quota, token_used FROM sys_ai_profile WHERE id = ?", (int(profile_id),)
    ).fetchone()
    if row is None:
        return 0
    return int(token_plan_summary(dict(row))["tokenRemaining"])


def price_table(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """全部档案的单价 + 实际用量，供设置页与成本视图共用一套口径。"""
    rows = conn.execute(
        "SELECT id, name, provider, model, price_input_micros, price_output_micros,"
        " price_currency, billing_mode, token_quota, token_used"
        " FROM sys_ai_profile WHERE is_deleted = 0 ORDER BY id",
    ).fetchall()
    usage = spend_by_profile(conn)
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        profile_id = int(row["id"])
        price = price_of_profile(row)
        item = usage.get(profile_id) or Spend()
        result.append({
            "profileId": profile_id,
            "profileName": str(row.get("name") or ""),
            "provider": str(row.get("provider") or ""),
            "model": str(row.get("model") or ""),
            **price.as_dict(),
            **token_plan_summary(row),
            "calls": item.calls,
            "costMicros": item.micros,
            "costYuan": micros_to_yuan(item.micros),
        })
    return result


# ---------------- 折算 ----------------

def round_micros(tokens: int, micros_per_unit: int) -> int:
    """按「每 1000 token 的微元数」折算，四舍五入（.5 进位）。"""
    if tokens <= 0 or micros_per_unit <= 0:
        return 0
    return (int(tokens) * int(micros_per_unit) + TOKENS_PER_UNIT // 2) // TOKENS_PER_UNIT


def cost_micros_of(
    usage: Mapping[str, Any] | None,
    price: TokenPrice,
    *,
    prompt_tokens: int | None = None,
    completion_tokens: int | None = None,
) -> int | None:
    """token 用量 × 档案单价 = 本次花费（微元）；未配价返回 None（不是 0）。"""
    if not price.is_priced:
        return None
    data = usage or {}
    prompt = (prompt_tokens if prompt_tokens is not None
              else int(data.get("promptTokens") or 0))
    completion = (completion_tokens if completion_tokens is not None
                  else int(data.get("completionTokens") or 0))
    return (round_micros(prompt, price.input_micros)
            + round_micros(completion, price.output_micros))


def cost_report(
    usage: Mapping[str, Any] | None,
    price: TokenPrice,
    *,
    capability: str = "",
    profile_id: int | None = None,
    profile_name: str = "",
    model: str = "",
) -> dict[str, Any]:
    """一次调用的成本结论：既是要落库的字段，也是要给界面看的口径。

    `costMicros` / `currency` 两个键名是 `ai_record_service.create_record` 认的契约，
    改了就会让成本列永远写不进去。
    """
    data = usage or {}
    prompt = int(data.get("promptTokens") or 0)
    completion = int(data.get("completionTokens") or 0)
    total = int(data.get("totalTokens") or prompt + completion)
    micros = cost_micros_of(data, price)
    report = {
        "priced": micros is not None,
        "costMicros": micros,
        "costYuan": micros_to_yuan(micros),
        "currency": price.currency,
        "promptTokens": prompt,
        "completionTokens": completion,
        "totalTokens": total,
        "estimated": bool(data.get("estimated")),
        "promptCostMicros": (round_micros(prompt, price.input_micros) if price.is_priced else None),
        "completionCostMicros": (round_micros(completion, price.output_micros) if price.is_priced else None),
        "profileId": profile_id,
        "profileName": profile_name,
        "model": model,
        "capability": capability,
    }
    report.update(price.as_dict())
    return report


# ---------------- 配置 ----------------

def _config_value(conn: sqlite3.Connection, key: str, default: str) -> str:
    row = conn.execute(
        "SELECT config_value FROM sys_config WHERE config_key = ?", (key,)
    ).fetchone()
    if row is None or row[0] is None:
        return default
    return str(row[0])


def _int_config(conn: sqlite3.Connection, key: str, default: int) -> int:
    raw = _config_value(conn, key, str(default))
    try:
        return max(0, int(str(raw).strip() or default))
    except (TypeError, ValueError):
        return default


def set_int_config(conn: sqlite3.Connection, key: str, value: int, description: str = "") -> None:
    """写一个整数配置项（不存在就插入）：预算与 token 软上限都靠它，不需要新迁移。"""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    conn.execute(
        "INSERT INTO sys_config (config_key, config_value, value_type, scope, description,"
        " is_system, created_at, updated_at)"
        " VALUES (?, ?, 'int', 'global', ?, 1, ?, ?)"
        " ON CONFLICT(config_key) DO UPDATE SET config_value = excluded.config_value,"
        " updated_at = excluded.updated_at",
        (key, str(int(value)), description, now, now),
    )


@dataclass(slots=True)
class BudgetConfig:
    """预算口径：日/月软阈值 + 告警比例 + 单次调用 token 软上限。"""

    daily_micros: int = 0
    monthly_micros: int = 0
    warn_ratio: int = DEFAULT_WARN_RATIO
    call_tokens: int = 0
    call_tokens_capability: int = 0
    call_tokens_project: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "dailyMicros": self.daily_micros,
            "dailyYuan": micros_to_yuan(self.daily_micros) if self.daily_micros else 0,
            "monthlyMicros": self.monthly_micros,
            "monthlyYuan": micros_to_yuan(self.monthly_micros) if self.monthly_micros else 0,
            "warnRatio": self.warn_ratio,
            "callTokens": self.call_tokens,
            "callTokensCapability": self.call_tokens_capability,
            "callTokensProject": self.call_tokens_project,
            "hardBlock": False,
        }

    def effective_call_tokens(self, capability: str = "", project_id: int | None = None) -> int:
        """单次调用 token 软上限：项目 > 能力 > 全局，取第一个设了正值的上限。"""
        for value in (
            self.call_tokens_project if project_id else 0,
            self.call_tokens_capability if capability else 0,
            self.call_tokens,
        ):
            if value > 0:
                return int(value)
        return 0


def read_budget(
    conn: sqlite3.Connection,
    *,
    capability: str = "",
    project_id: int | None = None,
) -> BudgetConfig:
    ratio = _int_config(conn, CONFIG_WARN_RATIO, DEFAULT_WARN_RATIO)
    return BudgetConfig(
        daily_micros=_int_config(conn, CONFIG_DAILY, 0),
        monthly_micros=_int_config(conn, CONFIG_MONTHLY, 0),
        warn_ratio=min(MAX_WARN_RATIO, max(MIN_WARN_RATIO, ratio or DEFAULT_WARN_RATIO)),
        call_tokens=_int_config(conn, CONFIG_CALL_TOKENS, 0),
        call_tokens_capability=(_int_config(conn, f"{CALL_TOKENS_CAP_PREFIX}{capability}", 0)
                                if capability else 0),
        call_tokens_project=(_int_config(conn, f"{CALL_TOKENS_PROJECT_PREFIX}{project_id}", 0)
                             if project_id else 0),
    )


# ---------------- 汇总 ----------------

@dataclass(slots=True)
class Spend:
    """一段范围内花掉的钱：未配价的调用单独计数，绝不混进金额里。"""

    micros: int = 0
    calls: int = 0
    priced_calls: int = 0
    unpriced_calls: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "calls": self.calls,
            "pricedCalls": self.priced_calls,
            "unpricedCalls": self.unpriced_calls,
            "costMicros": self.micros,
            "costYuan": micros_to_yuan(self.micros),
            "promptTokens": self.prompt_tokens,
            "completionTokens": self.completion_tokens,
            "totalTokens": self.prompt_tokens + self.completion_tokens,
        }

    def __add__(self, other: "Spend") -> "Spend":
        return Spend(
            micros=self.micros + other.micros,
            calls=self.calls + other.calls,
            priced_calls=self.priced_calls + other.priced_calls,
            unpriced_calls=self.unpriced_calls + other.unpriced_calls,
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
        )


_AGG_COLUMNS = (
    "COUNT(*) AS calls,"
    " COALESCE(SUM(CASE WHEN cost_micros IS NOT NULL THEN 1 ELSE 0 END), 0) AS priced_calls,"
    " COALESCE(SUM(CASE WHEN cost_micros IS NULL THEN 1 ELSE 0 END), 0) AS unpriced_calls,"
    " COALESCE(SUM(cost_micros), 0) AS micros,"
    " COALESCE(SUM(CASE WHEN usage_json LIKE '{%' THEN"
    " CAST(json_extract(usage_json, '$.promptTokens') AS INTEGER) ELSE 0 END), 0) AS prompt_tokens,"
    " COALESCE(SUM(CASE WHEN usage_json LIKE '{%' THEN"
    " CAST(json_extract(usage_json, '$.completionTokens') AS INTEGER) ELSE 0 END), 0) AS completion_tokens"
)


def _period_sql(day: str = "", month: str = "") -> tuple[str, list[Any]]:
    if day:
        return " AND substr(created_at, 1, 10) = ?", [day]
    if month:
        return " AND substr(created_at, 1, 7) = ?", [month]
    return "", []


def query_spend(
    conn: sqlite3.Connection,
    *,
    day: str = "",
    month: str = "",
    project_id: int | None = None,
    capability: str = "",
) -> Spend:
    """按日 / 按月（UTC 前缀匹配，与 utcnow_iso 的存储格式一致）汇总花费。"""
    period_sql, params = _period_sql(day, month)
    where = "".join((" is_deleted = 0", period_sql))
    if capability:
        where += " AND capability = ?"
        params.append(capability)
    if project_id:
        where += PROJECT_SCOPE_SQL
        params.append(int(project_id))
    row = conn.execute(
        f"SELECT {_AGG_COLUMNS} FROM biz_ai_record WHERE {where}", params
    ).fetchone()
    if row is None:
        return Spend()
    data = dict(row)
    return Spend(
        micros=int(data.get("micros") or 0),
        calls=int(data.get("calls") or 0),
        priced_calls=int(data.get("priced_calls") or 0),
        unpriced_calls=int(data.get("unpriced_calls") or 0),
        prompt_tokens=int(data.get("prompt_tokens") or 0),
        completion_tokens=int(data.get("completion_tokens") or 0),
    )


def spend_by_profile(conn: sqlite3.Connection) -> dict[int, Spend]:
    """按模型档案聚合：档案是单价的宿主，成本视图要能回答「哪个档案在烧钱」。"""
    rows = conn.execute(
        "SELECT COALESCE(t.profile_id, 0) AS profile_id,"
        " COUNT(*) AS calls,"
        " COALESCE(SUM(CASE WHEN r.cost_micros IS NOT NULL THEN 1 ELSE 0 END), 0) AS priced_calls,"
        " COALESCE(SUM(CASE WHEN r.cost_micros IS NULL THEN 1 ELSE 0 END), 0) AS unpriced_calls,"
        " COALESCE(SUM(r.cost_micros), 0) AS micros,"
        " COALESCE(SUM(CASE WHEN r.usage_json LIKE '{%' THEN"
        " CAST(json_extract(r.usage_json, '$.promptTokens') AS INTEGER) ELSE 0 END), 0) AS prompt_tokens,"
        " COALESCE(SUM(CASE WHEN r.usage_json LIKE '{%' THEN"
        " CAST(json_extract(r.usage_json, '$.completionTokens') AS INTEGER) ELSE 0 END), 0)"
        " AS completion_tokens"
        " FROM biz_ai_record r"
        " LEFT JOIN biz_ai_task t ON t.id = r.task_id AND t.is_deleted = 0"
        " WHERE r.is_deleted = 0 AND r.cost_micros IS NOT NULL"
        " GROUP BY COALESCE(t.profile_id, 0)",
    ).fetchall()
    result: dict[int, Spend] = {}
    for raw in rows:
        data = dict(raw)
        profile_id = int(data.get("profile_id") or 0)
        if not profile_id:
            continue
        result[profile_id] = Spend(
            micros=int(data.get("micros") or 0),
            calls=int(data.get("calls") or 0),
            priced_calls=int(data.get("priced_calls") or 0),
            unpriced_calls=int(data.get("unpriced_calls") or 0),
            prompt_tokens=int(data.get("prompt_tokens") or 0),
            completion_tokens=int(data.get("completion_tokens") or 0),
        )
    return result


def _grouped(conn: sqlite3.Connection, column: str, *, project_id: int | None) -> list[dict[str, Any]]:
    where = " r.is_deleted = 0 AND r.cost_micros IS NOT NULL"
    params: list[Any] = []
    join = " LEFT JOIN biz_ai_task t ON t.id = r.task_id AND t.is_deleted = 0"
    if project_id:
        where += " AND t.project_id = ?"
        params.append(int(project_id))
    rows = conn.execute(
        f"SELECT {column} AS bucket, COALESCE(SUM(r.cost_micros), 0) AS micros,"
        " COUNT(*) AS calls, COALESCE(SUM(CASE WHEN r.usage_json LIKE '{%'"
        " THEN CAST(json_extract(r.usage_json, '$.totalTokens') AS INTEGER) ELSE 0 END), 0) AS tokens"
        f" FROM biz_ai_record r{join} WHERE{where}"
        f" GROUP BY {column} ORDER BY micros DESC",
        params,
    ).fetchall()
    return [dict(row) for row in rows]


def spend_by_capability(
    conn: sqlite3.Connection, *, project_id: int | None = None
) -> list[dict[str, Any]]:
    """成本视图按能力拆分（验收标准 3）。"""
    from ..ai.capability_codes import label_of  # 局部导入：避免与能力注册表的循环依赖

    return [
        {
            "capability": str(row.get("bucket") or ""),
            "capabilityLabel": label_of(str(row.get("bucket") or "")),
            "costMicros": int(row.get("micros") or 0),
            "costYuan": micros_to_yuan(int(row.get("micros") or 0)),
            "calls": int(row.get("calls") or 0),
            "totalTokens": int(row.get("tokens") or 0),
        }
        for row in _grouped(conn, "r.capability", project_id=project_id)
    ]


def spend_by_project(
    conn: sqlite3.Connection, *, project_id: int | None = None
) -> list[dict[str, Any]]:
    """成本视图按项目拆分（验收标准 3）。"""
    rows = _grouped(conn, "t.project_id", project_id=project_id)
    names = _project_names(conn, [int(r["bucket"]) for r in rows if r.get("bucket")])
    return [
        {
            "projectId": int(row.get("bucket") or 0) if row.get("bucket") is not None else None,
            "projectName": names.get(int(row.get("bucket") or 0), "（无项目归属）"),
            "costMicros": int(row.get("micros") or 0),
            "costYuan": micros_to_yuan(int(row.get("micros") or 0)),
            "calls": int(row.get("calls") or 0),
            "totalTokens": int(row.get("tokens") or 0),
        }
        for row in rows
    ]


def _project_names(conn: sqlite3.Connection, ids: Iterable[int]) -> dict[int, str]:
    wanted = [int(item) for item in ids if item]
    if not wanted:
        return {}
    marks = ", ".join("?" for _ in wanted)
    rows = conn.execute(
        f"SELECT id, name FROM biz_project WHERE id IN ({marks})", wanted
    ).fetchall()
    return {int(row["id"]): str(row["name"] or "") for row in rows}


def level_of(spend_micros: int, limit_micros: int, warn_ratio: int) -> str:
    """软阈值分级：0 阈值 = 不设限，永远 ok；到 warn_ratio% 就 warn；超了 over。"""
    if limit_micros <= 0:
        return LEVEL_OK
    if spend_micros > limit_micros:
        return LEVEL_OVER
    trigger = (limit_micros * max(MIN_WARN_RATIO, min(MAX_WARN_RATIO, warn_ratio))) // 100
    if spend_micros >= trigger:
        return LEVEL_WARN
    return LEVEL_OK


def ratio_of(spend_micros: int, limit_micros: int) -> float:
    if limit_micros <= 0:
        return 0.0
    return round(spend_micros * 100.0 / limit_micros, 2)


def _period_block(name: str, spend: Spend, limit_micros: int, warn_ratio: int) -> dict[str, Any]:
    level = level_of(spend.micros, limit_micros, warn_ratio)
    return {
        "name": name,
        **spend.as_dict(),
        "limitMicros": limit_micros,
        "limitYuan": micros_to_yuan(limit_micros) if limit_micros else 0,
        "ratioPercent": ratio_of(spend.micros, limit_micros),
        "warnRatio": warn_ratio,
        "level": level,
        "levelLabel": LEVEL_LABELS[level],
    }


def call_token_hint(total_tokens: int, limit_tokens: int) -> str:
    """单次调用 token 的软提示文案；没设上限或没到 80% 就是空串。"""
    if limit_tokens <= 0 or total_tokens <= 0:
        return ""
    ratio = total_tokens * 100.0 / limit_tokens
    if ratio > 100:
        return (f"本次调用约 {total_tokens} token，已超过设置的单次上限 {limit_tokens} token"
                "（仅提示，不拦截，也不会自动降级）。")
    if ratio >= DEFAULT_WARN_RATIO:
        return (f"本次调用约 {total_tokens} token，已用到设置的单次上限 {limit_tokens} token 的"
                f"{int(ratio)}%（仅提示，不拦截）。")
    return ""


def budget_status(
    conn: sqlite3.Connection,
    *,
    project_id: int | None = None,
    capability: str = "",
    total_tokens: int = 0,
) -> dict[str, Any]:
    """日/月预算与单次 token 软上限的当前状态；永远是提示，不参与任何拦截决策。"""
    config = read_budget(conn, capability=capability, project_id=project_id)
    month = utc_day()[:7]
    today = utc_day()
    daily = query_spend(conn, day=today, project_id=project_id)
    monthly = query_spend(conn, month=month, project_id=project_id)
    day_block = _period_block("today", daily, config.daily_micros, config.warn_ratio)
    month_block = _period_block("month", monthly, config.monthly_micros, config.warn_ratio)
    limit = config.effective_call_tokens(capability, project_id)
    hints: list[str] = []
    for block in (day_block, month_block):
        if block["level"] == LEVEL_OVER and block["limitMicros"]:
            hints.append(
                f"{_period_label(block['name'])} AI 花费 "
                f"{block['costYuan']:.6f} {currency_of(conn)}，已超过软阈值 "
                f"{block['limitYuan']:.6f}（{config.warn_ratio}% 起提醒）：不拦截调用，"
                "可在「AI 设置 → AI大模型成本与预算」里调整阈值或补配单价。"
            )
        elif block["level"] == LEVEL_WARN and block["limitMicros"]:
            hints.append(
                f"{_period_label(block['name'])} AI 花费已达软阈值的 "
                f"{block['ratioPercent']}%（阈值 {block['limitYuan']:.6f} {currency_of(conn)}）。"
            )
    hint = call_token_hint(total_tokens, limit)
    if hint:
        hints.append(hint)
    return {
        "currency": currency_of(conn),
        "budget": config.as_dict(),
        "today": day_block,
        "month": month_block,
        "callTokenLimit": limit,
        "callTokenHint": hint,
        "warnings": hints,
        "blocking": False,
        "unpricedCalls": int(month_block["unpricedCalls"]),
    }


def _period_label(name: str) -> str:
    return "今日" if name == "today" else "本月"


def summary(
    conn: sqlite3.Connection,
    *,
    project_id: int | None = None,
    capability: str = "",
    days: int = 0,
) -> dict[str, Any]:
    """成本视图的读侧：总量 + 按能力/项目/档案拆分 + 预算状态（可按项目、能力过滤）。"""
    today = utc_day()
    month = today[:7]
    scoped = query_spend(conn, project_id=project_id, capability=capability)
    payload: dict[str, Any] = {
        "day": today,
        "monthLabel": month,
        "currency": currency_of(conn),
        "today": query_spend(conn, day=today, project_id=project_id, capability=capability).as_dict(),
        "month": query_spend(conn, month=month, project_id=project_id, capability=capability).as_dict(),
        "scoped": scoped.as_dict(),
        "byCapability": spend_by_capability(conn, project_id=project_id),
        "byProject": spend_by_project(conn, project_id=project_id),
        "byProfile": price_table(conn),
        "budget": budget_status(conn, project_id=project_id, capability=capability),
    }
    if days > 0:
        payload["daily"] = daily_trend(conn, days=days, project_id=project_id)
    if project_id or capability:
        payload["filter"] = {"projectId": project_id, "capability": capability}
    return payload


def daily_trend(
    conn: sqlite3.Connection,
    *,
    days: int,
    project_id: int | None = None,
) -> list[dict[str, Any]]:
    """近 N 天逐日花费：成本视图的折线数据源。"""
    out: list[dict[str, Any]] = []
    for offset in range(max(1, int(days))):
        day = utc_day(offset)
        out.append({"day": day, **query_spend(conn, day=day, project_id=project_id).as_dict()})
    return out


def currency_of(conn: sqlite3.Connection) -> str:
    """展示用币种：取第一个配了单价的档案的币种，都没配就是 CNY。"""
    row = conn.execute(
        "SELECT price_currency FROM sys_ai_profile WHERE is_deleted = 0"
        " AND (price_input_micros > 0 OR price_output_micros > 0)"
        " ORDER BY id LIMIT 1",
    ).fetchone()
    if row is None:
        return DEFAULT_CURRENCY
    return normalize_currency(row[0])


def utc_day(offset_days: int = 0) -> str:
    """UTC 的 YYYY-MM-DD；记录时间戳就是 utcnow_iso，前缀匹配才能对上。"""
    moment = datetime.now(timezone.utc) - timedelta(days=max(0, int(offset_days)))
    return moment.strftime("%Y-%m-%d")
