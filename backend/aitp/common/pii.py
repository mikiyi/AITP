"""敏感信息识别与脱敏。

云端模型会离开本机，输入里可能带手机号、身份证、账号、生产日志、内部 SQL。
这里提供可复用的识别与脱敏能力，供 AI 调用前统一处理。
"""

from __future__ import annotations

import re

#: 类型码 → 中文名。前端展示与 AI 记录都用这一份，避免两端各写一套。
TYPE_LABELS: dict[str, str] = {
    "id_card": "身份证号",
    "phone": "手机号",
    "email": "邮箱",
    "bank_card": "银行卡号",
    "api_key": "API Key",
    "password": "账号口令",
}

PATTERN_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("id_card", re.compile(r"\b\d{17}[\dXx]\b"), "[身份证已脱敏]"),
    ("phone", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "[手机号已脱敏]"),
    ("email", re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "[邮箱已脱敏]"),
    ("bank_card", re.compile(r"(?<!\d)\d{16,19}(?!\d)"), "[卡号已脱敏]"),
    ("api_key", re.compile(r"sk-[A-Za-z0-9_\-]{8,}"), "[API Key 已脱敏]"),
    ("password", re.compile(r"(?i)\b(password|passwd|pwd|secret|token|api[_-]?key)\b\s*[:=]\s*\S+"),
     "[凭证已脱敏]"),
)


def detect_sensitive(text: str) -> list[str]:
    """返回文本中命中的敏感类型列表（去重，保持出现顺序）。"""
    if not text:
        return []
    found: list[str] = []
    for name, pattern, _ in PATTERN_RULES:
        if pattern.search(text) and name not in found:
            found.append(name)
    return found


def label_of(type_code: str) -> str:
    return TYPE_LABELS.get(type_code, type_code)


def scan_sensitive(text: str) -> tuple[str, dict[str, int]]:
    """脱敏并统计每类命中数量。

    逐条规则替换并计数，返回 (脱敏后文本, {类型码: 数量})；
    计数只保留命中过的类型，顺序与 PATTERN_RULES 一致。
    """
    if not text:
        return text, {}
    counts: dict[str, int] = {}
    masked = text
    for name, pattern, replacement in PATTERN_RULES:
        hits = len(pattern.findall(masked))
        if hits:
            counts[name] = hits
        if name == "password":
            masked = pattern.sub(lambda m: f"{m.group(1)}=[凭证已脱敏]", masked)
        else:
            masked = pattern.sub(replacement, masked)
    return masked, counts


def detect_sensitive_counts(text: str) -> dict[str, int]:
    """返回每个敏感类型的命中次数；只用于统计，不返回原文。"""
    return scan_sensitive(text)[1]


def mask_sensitive(text: str) -> str:
    """把识别到的敏感信息替换成占位文本。"""
    return scan_sensitive(text)[0]


def contains_sensitive(text: str) -> bool:
    return bool(detect_sensitive(text))
