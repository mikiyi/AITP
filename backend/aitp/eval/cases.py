"""用例校验与规则断言。

断言只做确定性、可复现的检查；Rubric / LLM-as-judge 是后续阶段（judge_mode=rule 之外）。
"""

from __future__ import annotations

import json
import re
from typing import Any

from ..errors import AppError, ErrorCode

SEVERITIES = {"S0", "S1", "S2"}
CATEGORIES = {"golden", "bad", "adversarial", "regression"}
ALLOWED_ASSERTIONS = {
    "must_contain",
    "forbidden_contains",
    "regex",
    "max_chars",
    "json_keys",
    "grounded",
}


def next_case_no(existing: int) -> str:
    """EC-001 起，按评测集自增；软删不复用编号（调用方传当前最大计数）。"""

    return f"EC-{existing + 1:03d}"


def _as_str_list(value: Any, field: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or any(not isinstance(item, str) or not item.strip() for item in value):
        raise AppError(ErrorCode.PARAM_ERROR, f"{field} 必须是非空字符串数组")
    return [item.strip() for item in value]


def normalize_case(data: dict[str, Any]) -> dict[str, Any]:
    """校验用例入参并返回归一化结果（不含 case_no / id 等由服务层生成的字段）。"""

    title = str(data.get("title") or "").strip()
    if not title:
        raise AppError(ErrorCode.PARAM_ERROR, "用例标题不能为空")

    category = str(data.get("category") or "golden")
    if category not in CATEGORIES:
        raise AppError(ErrorCode.PARAM_ERROR, f"category 取值必须是 {sorted(CATEGORIES)} 之一")

    severity = str(data.get("severity") or "S1")
    if severity not in SEVERITIES:
        raise AppError(ErrorCode.PARAM_ERROR, f"severity 取值必须是 {sorted(SEVERITIES)} 之一")

    assertions = data.get("assertions") or {}
    if not isinstance(assertions, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "assertions 必须是对象")
    unknown = set(assertions) - ALLOWED_ASSERTIONS
    if unknown:
        raise AppError(ErrorCode.PARAM_ERROR, f"assertions 含未知键：{sorted(unknown)}")

    normalized: dict[str, Any] = {"assertions": {}}
    for key in ("must_contain", "forbidden_contains"):
        if key in assertions:
            normalized["assertions"][key] = _as_str_list(assertions[key], key)
    if "regex" in assertions:
        patterns = _as_str_list(assertions["regex"], "regex")
        for pattern in patterns:
            try:
                re.compile(pattern)
            except re.error as exc:
                raise AppError(ErrorCode.PARAM_ERROR, f"regex 非法：{pattern}（{exc}）") from exc
        normalized["assertions"]["regex"] = patterns
    if "max_chars" in assertions:
        try:
            limit = int(assertions["max_chars"])
        except (TypeError, ValueError):
            raise AppError(ErrorCode.PARAM_ERROR, "max_chars 必须是整数") from None
        if limit <= 0:
            raise AppError(ErrorCode.PARAM_ERROR, "max_chars 必须大于 0")
        normalized["assertions"]["max_chars"] = limit
    if "json_keys" in assertions:
        normalized["assertions"]["json_keys"] = _as_str_list(assertions["json_keys"], "json_keys")
    if "grounded" in assertions:
        normalized["assertions"]["grounded"] = bool(assertions["grounded"])

    if not normalized["assertions"]:
        raise AppError(ErrorCode.PARAM_ERROR, "用例至少要有一条断言（MVP 不支持空用例）")

    input_json = data.get("input_json")
    if input_json is not None and not isinstance(input_json, (dict, list)):
        raise AppError(ErrorCode.PARAM_ERROR, "input_json 必须是对象或数组")

    normalized.update(
        {
            "title": title,
            "category": category,
            "severity": severity,
            "expected_type": str(data.get("expected_type") or "assert"),
            "expected_text": str(data.get("expected_text") or ""),
            "effective_date": str(data.get("effective_date") or ""),
            "tags": str(data.get("tags") or ""),
            "source": str(data.get("source") or "manual"),
            "source_ref": str(data.get("source_ref") or ""),
            "difficulty": int(data.get("difficulty") or 2),
            "input_json": input_json or {},
            "rubric_json": data.get("rubric_json") or [],
            "relevant_docs": data.get("relevant_docs") or [],
        }
    )
    return normalized


def evaluate_assertions(output: str, assertions: dict[str, Any]) -> list[str]:
    """返回失败原因列表；空列表 = 全部断言通过。"""

    text = output or ""
    reasons: list[str] = []

    for needle in assertions.get("must_contain") or []:
        if needle not in text:
            reasons.append(f"assert.contain_missing:{needle}")
    for needle in assertions.get("forbidden_contains") or []:
        if needle and needle in text:
            reasons.append(f"assert.forbidden_hit:{needle}")
    for pattern in assertions.get("regex") or []:
        if not re.search(pattern, text):
            reasons.append(f"assert.regex_mismatch:{pattern}")

    limit = assertions.get("max_chars")
    if limit and len(re.sub(r"\s+", "", text)) > int(limit):
        reasons.append(f"assert.too_long:{limit}")

    keys = assertions.get("json_keys") or []
    if keys:
        try:
            payload = json.loads(text)
        except (TypeError, ValueError):
            reasons.append("assert.schema_invalid:not_json")
        else:
            missing = [key for key in keys if not isinstance(payload, dict) or key not in payload]
            if missing:
                reasons.append(f"assert.schema_invalid:{','.join(missing)}")

    if assertions.get("grounded") and "来源" not in text and "【" not in text:
        reasons.append("assert.hallucination:no_source")

    return reasons
