"""执行失败归因（V2.6.0）。

把一次接口执行的失败分成四类，决定「下一步该改什么」：

- ``bug``：服务端行为与约定不一致，应当转缺陷，不要改用例；
- ``assertion``：断言本身写错（状态码/字段路径/期望值），可以生成修复建议；
- ``environment``：超时、连接失败、鉴权过期、网关不可用，先修环境再重跑；
- ``data``：路径不存在、唯一键冲突、前置数据缺失，先修测试数据；
- ``unknown``：证据不足，只提示人工复核。

归因先用确定性规则，不依赖模型：规则可解释、可回归，也不会把环境抖动
误判成「断言写错」而让用户采纳错误建议。结果随执行记录落库，报告与
修复建议直接复用同一份结论。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..repositories.base import dumps, loads

KIND_BUG = "bug"
KIND_ASSERTION = "assertion"
KIND_ENVIRONMENT = "environment"
KIND_DATA = "data"
KIND_UNKNOWN = "unknown"

KINDS: tuple[str, ...] = (KIND_BUG, KIND_ASSERTION, KIND_ENVIRONMENT, KIND_DATA, KIND_UNKNOWN)

KIND_LABELS: dict[str, str] = {
    KIND_BUG: "疑似缺陷",
    KIND_ASSERTION: "断言错误",
    KIND_ENVIRONMENT: "环境问题",
    KIND_DATA: "数据问题",
    KIND_UNKNOWN: "待人工确认",
}

KIND_ACTIONS: dict[str, str] = {
    KIND_BUG: "接口行为与约定不一致，建议转缺陷并保留响应证据",
    KIND_ASSERTION: "断言与当前接口返回不一致，可生成修复建议后人工采纳",
    KIND_ENVIRONMENT: "先修复环境或登录态后重跑，不建议直接修改用例",
    KIND_DATA: "检查测试数据、主键冲突与前置条件后重跑",
    KIND_UNKNOWN: "现有证据不足以判断，请人工复核请求与响应",
}

#: 环境类关键词：命中即优先判环境，避免把基础设施问题写成断言建议
_ENV_WORDS = (
    "timeout", "timed out", "超时", "connection", "connect", "连接", "refused",
    "拒绝", "dns", "name resolution", "resolve", "ssl", "certificate", "证书",
    "unreachable", "network", "网络", "gateway", "502", "503", "504",
)
_ASSERT_WORDS = ("jsonpath", "$.", "字段", "不存在", "缺失", "解析", "json", "decode")
_STATUS_WORDS = ("状态码", "status", "statuscode", "http code")


def _text(value: Any) -> str:
    return str(value or "").strip()


def _failed_assertions(assertions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in assertions if isinstance(item, dict) and not item.get("passed")]


def _assertion_text(items: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in items:
        parts.append(_text(item.get("name")))
        parts.append(_text(item.get("message")))
        parts.append(_text(item.get("expected")))
    return " ".join(parts).lower()


def classify(
    *,
    status_code: int | None,
    message: str = "",
    assertions: list[dict[str, Any]] | None = None,
    passed: bool = False,
) -> dict[str, Any]:
    """按确定性规则给出归因结论，返回 kind / confidence / reason / action。"""
    if passed:
        return {"kind": "", "confidence": 1.0, "reason": "", "action": "", "rule": "passed"}

    failed = _failed_assertions(list(assertions or []))
    assert_text = _assertion_text(failed)
    message_text = _text(message)
    combined = f"{message_text} {assert_text}".lower()
    code = int(status_code) if status_code is not None else None

    def result(kind: str, confidence: float, reason: str, rule: str) -> dict[str, Any]:
        return {
            "kind": kind,
            "confidence": round(confidence, 2),
            "reason": reason,
            "action": KIND_ACTIONS[kind],
            "rule": rule,
        }

    # 1) 连请求都没发出去：网络、超时、证书、DNS
    if code is None:
        if any(word in combined for word in ("timeout", "timed out", "超时")):
            return result(KIND_ENVIRONMENT, 0.95, "请求超时，目标服务或网络不可达", "timeout")
        if any(word in combined for word in ("ssl", "certificate", "证书")):
            return result(KIND_ENVIRONMENT, 0.9, "TLS/证书校验失败", "tls")
        if any(word in combined for word in _ENV_WORDS):
            return result(KIND_ENVIRONMENT, 0.92, "连接失败，环境可能未就绪", "connect")
        if any(word in combined for word in _ASSERT_WORDS):
            return result(KIND_ASSERTION, 0.7, "响应无法解析，断言规则未执行", "unparsable")
        return result(KIND_ENVIRONMENT, 0.6, "未拿到 HTTP 响应，优先排查环境", "no-response")

    # 2) 网关/鉴权/限流：属于环境或凭据，不是用例问题
    if code in (401, 403):
        return result(KIND_ENVIRONMENT, 0.88, "鉴权失败，登录态或凭据可能已过期", "auth")
    if code in (408, 429):
        return result(KIND_ENVIRONMENT, 0.85, "请求超时或被限流，稍后重试", "throttle")
    if code in (502, 503, 504):
        return result(KIND_ENVIRONMENT, 0.9, "网关或上游服务不可用", "gateway")
    if code in (500, 501, 505):
        return result(KIND_BUG, 0.62, f"服务端返回 {code}，疑似服务异常", "server-error")

    # 3) 有断言明细：路径/状态码问题归断言，值不一致归缺陷
    if failed:
        if any(word in assert_text for word in _STATUS_WORDS):
            return result(KIND_ASSERTION, 0.8, "实际状态码与期望不一致，先核对断言", "status-assert")
        if any(word in assert_text for word in _ASSERT_WORDS):
            return result(KIND_ASSERTION, 0.76, "断言字段不存在或路径变更，先核对断言", "path-assert")
        if "响应时间" in assert_text or "duration" in assert_text:
            return result(KIND_ENVIRONMENT, 0.62, "响应耗时超出阈值，先排查环境负载", "slow")
        if 200 <= code < 300:
            return result(KIND_BUG, 0.58, "接口返回 2xx 但业务值与约定不一致", "value-mismatch")
        return result(KIND_ASSERTION, 0.5, "断言未通过，需人工确认期望是否过期", "assertion")

    # 4) 没有断言明细：按状态码粗分
    if code in (400, 409, 422):
        return result(KIND_DATA, 0.6, f"服务端返回 {code}，检查请求参数与测试数据", "bad-request")
    if code == 404:
        return result(KIND_DATA, 0.55, "资源不存在，检查路径与前置数据", "not-found")
    if 200 <= code < 300:
        return result(KIND_UNKNOWN, 0.35, "请求成功但没有可判定的断言", "no-assertion")
    return result(KIND_UNKNOWN, 0.3, f"HTTP {code} 无法自动归类", "unclassified")


def attribute_detail(
    conn: sqlite3.Connection,
    execution_id: int,
    detail: dict[str, Any],
    *,
    retry_count: int = 0,
    flaky: bool = False,
) -> dict[str, Any]:
    """把一次执行明细的归因写回 biz_interface_execution，并返回结论。"""
    verdict = classify(
        status_code=detail.get("statusCode"),
        message=_text(detail.get("message")),
        assertions=list(detail.get("assertions") or []),
        passed=str(detail.get("result") or "") == "passed",
    )
    verdict["retryCount"] = int(retry_count)
    verdict["flaky"] = bool(flaky)
    conn.execute(
        """
        UPDATE biz_interface_execution
        SET failure_kind = ?, failure_reason = ?, failure_confidence = ?,
            attribution_json = ?, retry_count = ?, flaky = ?
        WHERE id = ?
        """,
        (
            str(verdict["kind"]),
            str(verdict["reason"])[:500],
            float(verdict["confidence"]),
            dumps(verdict),
            int(retry_count),
            int(bool(flaky)),
            int(execution_id),
        ),
    )
    return verdict


def attribute_execution(conn: sqlite3.Connection, execution_id: int) -> dict[str, Any]:
    """对已存在的执行记录重新归因（规则升级或人工触发重算时用）。"""
    row = conn.execute(
        "SELECT * FROM biz_interface_execution WHERE id = ?", (int(execution_id),)
    ).fetchone()
    if row is None:
        return {}
    detail = {
        "statusCode": row["status_code"],
        "message": row["error_message"],
        "assertions": loads(row["assertions_json"], []) or [],
        "result": row["result"],
    }
    return attribute_detail(
        conn,
        execution_id,
        detail,
        retry_count=int(row["retry_count"] or 0),
        flaky=bool(row["flaky"]),
    )


def attribution_of(row: dict[str, Any]) -> dict[str, Any]:
    """从执行记录行读取归因，兼容尚未归因的历史数据。"""
    raw = loads(row.get("attribution_json"), {}) if row.get("attribution_json") else {}
    if isinstance(raw, dict) and raw.get("kind") is not None:
        return {
            "kind": str(raw.get("kind") or ""),
            "label": KIND_LABELS.get(str(raw.get("kind") or ""), "未归因"),
            "confidence": float(raw.get("confidence") or 0),
            "reason": str(raw.get("reason") or ""),
            "action": str(raw.get("action") or ""),
            "rule": str(raw.get("rule") or ""),
        }
    kind = str(row.get("failure_kind") or "")
    return {
        "kind": kind,
        "label": KIND_LABELS.get(kind, "未归因"),
        "confidence": float(row.get("failure_confidence") or 0),
        "reason": str(row.get("failure_reason") or ""),
        "action": KIND_ACTIONS.get(kind, ""),
        "rule": "",
    }


def summary(
    conn: sqlite3.Connection,
    *,
    run_id: int | None = None,
    project_id: int | None = None,
) -> dict[str, Any]:
    """按归因汇总失败分布，供执行详情与发布就绪结论使用。"""
    clauses = ["result = 'failed'", "failure_kind <> ''"]
    params: list[Any] = []
    if run_id is not None:
        clauses.append("run_id = ?")
        params.append(int(run_id))
    if project_id is not None:
        clauses.append(
            "case_id IN (SELECT id FROM biz_test_case WHERE project_id = ? AND is_deleted = 0)"
        )
        params.append(int(project_id))
    where = " AND ".join(clauses)
    rows = conn.execute(
        f"SELECT failure_kind AS kind, COUNT(*) AS c FROM biz_interface_execution "
        f"WHERE {where} GROUP BY failure_kind",
        params,
    ).fetchall()
    counts = {kind: 0 for kind in KINDS}
    for row in rows:
        counts[str(row["kind"])] = int(row["c"])
    total = sum(counts.values())
    flaky_row = conn.execute(
        f"SELECT COUNT(*) AS c FROM biz_interface_execution WHERE {where} AND flaky = 1",
        params,
    ).fetchone()
    return {
        "total": total,
        "counts": counts,
        "labels": {kind: KIND_LABELS[kind] for kind in KINDS},
        "flaky": int(flaky_row["c"] or 0) if flaky_row else 0,
        "needsAssetChange": counts[KIND_ASSERTION],
        "needsBugTicket": counts[KIND_BUG],
        "needsEnvFix": counts[KIND_ENVIRONMENT] + counts[KIND_DATA],
    }


__all__ = [
    "KINDS",
    "KIND_LABELS",
    "KIND_ACTIONS",
    "KIND_BUG",
    "KIND_ASSERTION",
    "KIND_ENVIRONMENT",
    "KIND_DATA",
    "KIND_UNKNOWN",
    "classify",
    "attribute_detail",
    "attribute_execution",
    "attribution_of",
    "summary",
]
