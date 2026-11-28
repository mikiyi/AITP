"""Rubric 解析、加权与阈值裁决（设计 §6.4）。

Rubric 结构：
    {
      "dimensions": [{"key", "weight", "scale": 5, "criteria"}],
      "must_pass": ["factuality", "safety"],
      "pass_threshold": 3.5
    }

裁决规则：
  - 必过维度（must_pass）任一低于 pass_threshold → 整体 fail（reason=rubric.must_pass）；
  - 加权均分低于 pass_threshold → fail（reason=rubric.below_threshold）；
  - 各维度先按自身 scale 归一到 [0,1] 再加权，允许不同维度不同量表；
  - 最终分统一量纲到 0–100，供判定链与规则分做 0.6/0.4 合成。
"""

from __future__ import annotations

from typing import Any

from ..errors import AppError, ErrorCode

PASS_REASON_MUST = "rubric.must_pass"
PASS_REASON_BELOW = "rubric.below_threshold"


def validate_rubric(rubric: Any) -> dict[str, Any]:
    """校验并归一化 Rubric；非法结构抛 100100。空 Rubric 返回空结构（表示不启用）。"""

    if rubric in (None, [], {}):
        return {"dimensions": [], "must_pass": [], "pass_threshold": 0.0}
    if not isinstance(rubric, dict):
        raise AppError(ErrorCode.PARAM_ERROR, "rubric 必须是对象")
    dimensions = rubric.get("dimensions") or []
    if not isinstance(dimensions, list) or not dimensions:
        raise AppError(ErrorCode.PARAM_ERROR, "rubric.dimensions 至少包含一个维度")

    normalized_dims: list[dict[str, Any]] = []
    seen: set[str] = set()
    weight_sum = 0.0
    for raw in dimensions:
        if not isinstance(raw, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "rubric 维度必须是对象")
        key = str(raw.get("key") or "").strip()
        if not key:
            raise AppError(ErrorCode.PARAM_ERROR, "rubric 维度 key 不能为空")
        if key in seen:
            raise AppError(ErrorCode.PARAM_ERROR, f"rubric 维度重复：{key}")
        seen.add(key)
        try:
            weight = float(raw.get("weight", 0.0))
            scale = int(raw.get("scale", 5))
        except (TypeError, ValueError):
            raise AppError(ErrorCode.PARAM_ERROR, f"rubric 维度 {key} 的 weight/scale 非法") from None
        if weight < 0:
            raise AppError(ErrorCode.PARAM_ERROR, f"rubric 维度 {key} 的 weight 不能为负")
        if scale <= 0:
            raise AppError(ErrorCode.PARAM_ERROR, f"rubric 维度 {key} 的 scale 必须大于 0")
        normalized_dims.append(
            {
                "key": key,
                "weight": weight,
                "scale": scale,
                "criteria": str(raw.get("criteria") or ""),
            }
        )
        weight_sum += weight

    must_pass = rubric.get("must_pass") or []
    if not isinstance(must_pass, list) or any(not isinstance(item, str) for item in must_pass):
        raise AppError(ErrorCode.PARAM_ERROR, "rubric.must_pass 必须是字符串数组")
    unknown = set(must_pass) - seen
    if unknown:
        raise AppError(ErrorCode.PARAM_ERROR, f"must_pass 引用了不存在的维度：{sorted(unknown)}")
    if weight_sum <= 0:
        raise AppError(ErrorCode.PARAM_ERROR, "rubric 维度权重之和必须大于 0")

    try:
        threshold = float(rubric.get("pass_threshold", 0.0))
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, "rubric.pass_threshold 必须是数字") from None
    return {
        "dimensions": normalized_dims,
        "must_pass": list(must_pass),
        "pass_threshold": threshold,
    }


def score_rubric(
    rubric: dict[str, Any], dimension_scores: dict[str, float]
) -> dict[str, Any]:
    """按 Rubric 裁决。

    dimension_scores: 维度 key → 原始打分（量表由该维度 scale 决定）。
    返回 0–100 综合分、逐维归一结果、必过失败项与是否通过。
    """

    dimensions = rubric.get("dimensions") or []
    if not dimensions:
        return {"score": None, "dimensions": [], "mustPassFailed": [], "passed": True, "reasons": []}

    weight_sum = sum(float(item["weight"]) for item in dimensions)
    weighted = 0.0
    detail: list[dict[str, Any]] = []
    must_failed: list[str] = []
    threshold = float(rubric.get("pass_threshold") or 0.0)
    must_pass = set(rubric.get("must_pass") or [])

    for dim in dimensions:
        key = str(dim["key"])
        scale = max(1, int(dim["scale"]))
        raw = dimension_scores.get(key)
        if raw is None:
            normalized = 0.0
        else:
            normalized = max(0.0, min(1.0, float(raw) / scale))
        weighted += float(dim["weight"]) * normalized
        detail.append(
            {
                "key": key,
                "raw": raw,
                "scale": scale,
                "normalized": round(normalized, 6),
                "weight": float(dim["weight"]),
            }
        )
        if key in must_pass and (raw is None or float(raw) < threshold):
            must_failed.append(key)

    average_on_scale = (weighted / weight_sum) if weight_sum > 0 else 0.0
    score100 = round(average_on_scale * 100.0, 4)
    reasons: list[str] = []
    if must_failed:
        reasons.append(f"{PASS_REASON_MUST}:{','.join(must_failed)}")
    if average_on_scale * (dimensions[0]["scale"] or 1) < threshold and not must_failed:
        reasons.append(PASS_REASON_BELOW)
    return {
        "score": score100,
        "dimensions": detail,
        "mustPassFailed": must_failed,
        "passed": not reasons,
        "reasons": reasons,
    }


def instruction_followed(rubric_result: dict[str, Any]) -> bool | None:
    """从 Rubric 裁决结果取指令维度是否遵循；无指令维度返回 None（不计入分母）。"""

    for dim in rubric_result.get("dimensions") or []:
        if dim.get("key") == "instruction":
            return "instruction" not in rubric_result.get("mustPassFailed", []) and float(
                dim.get("normalized") or 0.0
            ) >= 0.6
    return None
