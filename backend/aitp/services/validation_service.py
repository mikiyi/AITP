"""案例验证沙箱。

把「行为不能漂移」这件事变成可执行的夹具：

- 每个案例给一段输入，写清楚期望命中哪些能力、期望出现哪些缺项、
  期望触发哪些行为安全规则；
- 跑一次沙箱，实际结果与期望逐条比对，差在哪条直接标出来；
- 结果回写案例表，下次打开就能看到上次是不是通过的。

这正是固定走查脚本里那几条断言的产品化：脚本给人看，沙箱给产品用。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.validation_case_repo import (
    CASE_DISABLED,
    CASE_PUBLISHED,
    ValidationCaseRepository,
)
from . import ai_record_service, lab_service


def _expected_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise AppError(ErrorCode.PARAM_ERROR, f"{field} 必须是数组")
    return [str(item).strip() for item in value if str(item).strip()]


def _match_missing(actual: list[dict[str, Any]], expected: list[str]) -> list[str]:
    """缺项按子串匹配：期望「信息不足」，实际出现「现有信息不足」也算命中。"""
    texts = [
        f"{item.get('label') or ''} {item.get('detail') or ''}"
        for item in actual
    ]
    missed: list[str] = []
    for want in expected:
        if not any(want in text for text in texts):
            missed.append(want)
    return missed


def run_case(
    conn: sqlite3.Connection,
    pk: int,
    *,
    project_id: int | None = None,
    profile_id: int | None = None,
    run_capability: bool = True,
) -> dict[str, Any]:
    """跑一个验证案例，返回逐条比对结果。"""
    repo = ValidationCaseRepository(conn)
    row = repo.get_detail(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "验证案例不存在")

    input_text = str(row.get("input_text") or "")
    expected_routes = list(row.get("expectedRoutes") or [])
    expected_missing = list(row.get("expectedMissing") or [])
    expected_rules = list(row.get("expectedRules") or [])
    variables = dict(row.get("variables") or {})

    errors: list[str] = []

    # 1) 路由命中
    routed = lab_service.route_text(conn, input_text, project_id=project_id, top_k=3)
    actual_routes = [
        {"capability": str(item.get("capability")), "score": item.get("score"),
         "reason": item.get("reason")}
        for item in (routed.get("capabilityRoutes") or [])
    ]
    actual_codes = [item["capability"] for item in actual_routes]
    missed_routes = [code for code in expected_routes if code not in actual_codes]
    if missed_routes:
        errors.append(f"路由未命中：{'、'.join(missed_routes)}（实际：{'、'.join(actual_codes) or '无'}）")

    # 2) 缺项与安全规则：需要真实产出物
    capability = str(row.get("capability") or "")
    output = ""
    model = ""
    latency_ms = 0
    actual_missing: list[dict[str, Any]] = []
    actual_rules: list[str] = []
    review_required = False

    need_output = bool(expected_missing or expected_rules) or bool(capability)
    if run_capability and need_output:
        target = capability or (actual_codes[0] if actual_codes else "")
        if target:
            try:
                result = lab_service.run_capability(
                    conn, target, variables, profile_id=profile_id,
                )
            except Exception as exc:  # noqa: BLE001 - 沙箱要能记录失败而不是崩掉
                errors.append(f"能力执行失败：{exc}")
                result = None
            if result is not None:
                output = result.output
                model = result.model
                latency_ms = result.latency_ms
                review_required = result.review_required
                evidence = ai_record_service.extract_evidence(conn, output, result.refs)
                actual_missing = ai_record_service.extract_missing(
                    output, variables, lab_service.optional_variables_of(result.prompt_code),
                )
                risks = ai_record_service.detect_risks(output, evidence, actual_missing)
                actual_rules = [str(item.get("rule")) for item in risks]
        else:
            errors.append("没有可执行的能力，路由也没给出候选")

    missed_missing = _match_missing(actual_missing, expected_missing)
    if missed_missing:
        errors.append(f"缺项未出现：{'、'.join(missed_missing)}")
    missed_rules = [rule for rule in expected_rules if rule not in actual_rules]
    if missed_rules:
        errors.append(f"安全规则未触发：{'、'.join(missed_rules)}（实际：{'、'.join(actual_rules) or '无'}）")

    payload = {
        "passed": not errors,
        "checkedAt": utcnow_iso(),
        "route": {
            "source": routed.get("source"),
            "actual": actual_routes,
            "expected": expected_routes,
            "missed": missed_routes,
        },
        "missing": {
            "actual": actual_missing,
            "expected": expected_missing,
            "missed": missed_missing,
        },
        "rules": {
            "actual": actual_rules,
            "expected": expected_rules,
            "missed": missed_rules,
        },
        "errors": errors,
        "output": output,
        "model": model,
        "latencyMs": latency_ms,
        "reviewRequired": review_required,
    }
    repo.save_result(pk, payload)
    return payload


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    """把逐条案例的结果汇成通过率汇总，全量跑批按这个口径产出任务结果。"""
    passed = sum(1 for item in results if item["passed"])
    return {
        "total": len(results),
        "passed": passed,
        "failed": len(results) - passed,
        "passRate": round(passed / len(results), 4) if results else 0.0,
        "results": results,
    }


def create_case(
    conn: sqlite3.Connection,
    *,
    name: str,
    input_text: str,
    description: str = "",
    capability: str = "",
    variables: dict[str, Any] | None = None,
    expected_routes: list[str] | None = None,
    expected_missing: list[str] | None = None,
    expected_rules: list[str] | None = None,
    status: str = CASE_PUBLISHED,
) -> int:
    repo = ValidationCaseRepository(conn)
    if not str(input_text or "").strip():
        raise AppError(ErrorCode.PARAM_ERROR, "案例输入不能为空")
    return repo.insert({
        "name": str(name or "未命名案例"),
        "description": str(description or ""),
        "input_text": str(input_text),
        "capability": str(capability or ""),
        "variables_json": _dump(variables or {}),
        "expected_routes_json": _dump(_expected_list(expected_routes, "expectedRoutes")),
        "expected_missing_json": _dump(_expected_list(expected_missing, "expectedMissing")),
        "expected_rules_json": _dump(_expected_list(expected_rules, "expectedRules")),
        "status": status if status in (CASE_PUBLISHED, CASE_DISABLED) else CASE_PUBLISHED,
    })


def update_case(conn: sqlite3.Connection, pk: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = ValidationCaseRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "验证案例不存在")
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = str(data["name"] or "")
    if "description" in data:
        payload["description"] = str(data["description"] or "")
    if "inputText" in data:
        if not str(data["inputText"] or "").strip():
            raise AppError(ErrorCode.PARAM_ERROR, "案例输入不能为空")
        payload["input_text"] = str(data["inputText"])
    if "capability" in data:
        payload["capability"] = str(data["capability"] or "")
    if "variables" in data:
        variables = data["variables"]
        if not isinstance(variables, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "variables 必须是对象")
        payload["variables_json"] = _dump(variables)
    if "expectedRoutes" in data:
        payload["expected_routes_json"] = _dump(_expected_list(data["expectedRoutes"], "expectedRoutes"))
    if "expectedMissing" in data:
        payload["expected_missing_json"] = _dump(_expected_list(data["expectedMissing"], "expectedMissing"))
    if "expectedRules" in data:
        payload["expected_rules_json"] = _dump(_expected_list(data["expectedRules"], "expectedRules"))
    if "status" in data:
        status = str(data["status"])
        if status not in (CASE_PUBLISHED, CASE_DISABLED):
            raise AppError(ErrorCode.PARAM_ERROR, "案例状态非法")
        payload["status"] = status
    repo.update(pk, payload)
    detail = repo.get_detail(pk)
    if detail is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "验证案例不存在")
    return detail


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)
