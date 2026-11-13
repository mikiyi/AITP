"""外部平台字段映射公共逻辑（GAP-40）。

映射语义：AITP 字段 → 源平台原始字段路径，支持 a.b、a[].b（拼接）、a|b（兜底）。
优先级/严重度统一归一到 P0~P3；数字与常见命名（Jira/禅道/中文）都识别。
"""

from __future__ import annotations

from typing import Any

PRIORITY_BY_NUMBER = {1: "P0", 2: "P1", 3: "P2", 4: "P3"}
PRIORITY_BY_NAME = {
    "highest": "P0", "high": "P1", "medium": "P2", "low": "P3", "lowest": "P3",
    "blocker": "P0", "critical": "P0", "major": "P1", "minor": "P2", "trivial": "P3",
    "紧急": "P0", "高": "P1", "中": "P2", "低": "P3", "最高": "P0", "最低": "P3",
    "p0": "P0", "p1": "P1", "p2": "P2", "p3": "P3",
}


def get_path(obj: Any, path: str) -> Any:
    """支持 a.b、a[].b（取数组每项的 b 拼接）、a|b（先 a 后 b 兜底）。"""
    for variant in path.split("|"):
        value = _resolve(obj, variant.split("."))
        if value not in (None, ""):
            return value
    return None


def _resolve(obj: Any, parts: list[str]) -> Any:
    if not parts:
        return obj
    part = parts[0]
    rest = parts[1:]
    if part.endswith("[]"):
        key = part[:-2]
        items = obj.get(key) if isinstance(obj, dict) else None
        if not isinstance(items, list):
            return None
        values = [_resolve(item, rest) for item in items]
        return "\n".join(str(value) for value in values if value not in (None, "")) or None
    if isinstance(obj, dict):
        return _resolve(obj.get(part), rest)
    return None


def normalize_level(raw: Any) -> str:
    if isinstance(raw, bool):
        return "P2"
    if isinstance(raw, (int, float)):
        return PRIORITY_BY_NUMBER.get(int(raw), "P3" if int(raw) > 4 else "P2")
    key = str(raw or "").strip().lower()
    if key.isdigit():
        return PRIORITY_BY_NUMBER.get(int(key), "P3" if int(key) > 4 else "P2")
    return PRIORITY_BY_NAME.get(key, "P2")


def mapping_for(kind: str, mapping: dict[str, Any]) -> dict[str, str]:
    fields = mapping.get(kind)
    if not isinstance(fields, dict):
        return {}
    return {str(k): str(v) for k, v in fields.items()}


def apply_mapping(item: dict[str, Any], kind: str, mapping: dict[str, Any]) -> dict[str, Any]:
    """把一条源平台原始记录按映射转成 AITP 输入；title 为空视为无效。"""
    mapped: dict[str, Any] = {}
    for target, path in mapping_for(kind, mapping).items():
        value = get_path(item, path)
        if value is None:
            continue
        if target == "steps":
            text = str(value)
            mapped[target] = [{"step": line} for line in text.splitlines() if line.strip()]
        elif target in ("priority", "severity"):
            mapped[target] = normalize_level(value)
        else:
            mapped[target] = str(value)
    if not str(mapped.get("title") or "").strip():
        mapped["title"] = ""
    return mapped
