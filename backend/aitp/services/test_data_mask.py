"""测试数据脱敏引擎（三期 GAP-39）。

只做本机内存中的数据变换，不联网、不调模型：
  - 自动识别：值命中 `common/pii.py` 或列名命中手机号/邮箱/身份证/卡号/口令等提示；
  - 字段规则：mask（保留首尾）、hash（确定性）、random（确定性伪随机）、enum（枚举替换）、redact（占位）；
  - 校验：敏感列必须配规则，脱敏后全表再扫一遍 PII，命中即拒绝。

原始样本只在导入请求的内存中解析；落盘的是脱敏后的副本。
"""

from __future__ import annotations

import csv
import hashlib
import hmac
import io
import json
import re
from pathlib import Path
from typing import Any, Sequence

from ..common import pii
from ..errors import AppError, ErrorCode

MAX_SAMPLE_BYTES = 20 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 100

RULE_TYPES: tuple[str, ...] = ("mask", "hash", "random", "enum", "redact")

#: 列名提示 → PII 类型；只用于「没扫到值但列名明显敏感」的场景
_COLUMN_HINTS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)(phone|mobile|tel|手机号|手机|联系电话)"), "phone"),
    (re.compile(r"(?i)(e-?mail|邮箱|电子邮件)"), "email"),
    (re.compile(r"(?i)(id[_-]?card|identity|身份证)"), "id_card"),
    (re.compile(r"(?i)(bank[_-]?card|card[_-]?no|银行卡|卡号)"), "bank_card"),
    (re.compile(r"(?i)(password|passwd|pwd|secret|token|api[_-]?key|密码|口令|密钥)"), "password"),
)

_COLUMN_NAME_RE = re.compile(r"^[^\s,，。；;]{1,80}$")


def _fail(message: str) -> AppError:
    return AppError(ErrorCode.PARAM_ERROR, message)


def normalize_rules(raw: Any, columns: Sequence[str] | None = None) -> dict[str, dict[str, Any]]:
    """校验并归一化方案规则；columns 给定时拒绝不存在的列。"""
    if raw in (None, ""):
        return {}
    if not isinstance(raw, dict):
        raise _fail("脱敏规则必须是「列名 → 规则」的对象")
    allowed = set(columns or [])
    rules: dict[str, dict[str, Any]] = {}
    for column, value in raw.items():
        name = str(column).strip()
        if not name or not _COLUMN_NAME_RE.fullmatch(name):
            raise _fail(f"非法列名：{column}")
        if allowed and name not in allowed:
            raise _fail(f"脱敏规则引用了不存在的列：{name}")
        if not isinstance(value, dict):
            raise _fail(f"列「{name}」的规则必须是对象")
        rule_type = str(value.get("type") or "").strip().lower()
        if rule_type not in RULE_TYPES:
            raise _fail(f"列「{name}」的规则类型只能是：{'、'.join(RULE_TYPES)}")
        rule: dict[str, Any] = {"type": rule_type}
        if rule_type == "mask":
            rule["keepPrefix"] = _non_negative(value.get("keepPrefix"), "keepPrefix", 64)
            rule["keepSuffix"] = _non_negative(value.get("keepSuffix"), "keepSuffix", 64)
            rule["char"] = str(value.get("char") or "*")[:1] or "*"
        elif rule_type == "hash":
            rule["length"] = _non_negative(value.get("length"), "length", 64) or 12
            rule["salt"] = str(value.get("salt") or "aitp")
        elif rule_type == "random":
            rule["length"] = _non_negative(value.get("length"), "length", 64) or 8
            charset = str(value.get("charset") or "digits").lower()
            rule["charset"] = charset if charset in ("digits", "alnum") else "digits"
            rule["prefix"] = str(value.get("prefix") or "")[:32]
        elif rule_type == "enum":
            values = value.get("values")
            if not isinstance(values, list) or not values:
                raise _fail(f"列「{name}」的枚举替换必须提供非空 values")
            rule["values"] = [str(item)[:100] for item in values][:100]
        else:
            rule["replacement"] = str(value.get("replacement") or "[已脱敏]")[:80]
        rules[name] = rule
    return rules


def _non_negative(raw: Any, field: str, limit: int) -> int:
    if raw in (None, ""):
        return 0
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise _fail(f"{field} 必须是整数") from None
    if value < 0 or value > limit:
        raise _fail(f"{field} 必须在 0~{limit} 之间")
    return value


def detect_columns(
    columns: Sequence[str], rows: Sequence[dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """返回敏感列 → {types, hits}；值扫描优先于列名提示。"""
    detected: dict[str, dict[str, Any]] = {}
    for column in columns:
        types: list[str] = []
        hits = 0
        for row in rows[:50]:
            text = str(row.get(column) or "")
            found = pii.detect_sensitive(text)
            if found:
                hits += 1
                for name in found:
                    if name not in types:
                        types.append(name)
        if not types:
            for pattern, pii_type in _COLUMN_HINTS:
                if pattern.search(column):
                    types.append(pii_type)
                    break
        if types:
            detected[column] = {"types": types, "hits": hits}
    return detected


def auto_rules(detected: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """按命中类型给一版默认可用的规则，仍允许用户改。"""
    rules: dict[str, dict[str, Any]] = {}
    for column, info in detected.items():
        types = info.get("types") or []
        if "password" in types or "api_key" in types:
            rules[column] = {"type": "redact", "replacement": "[已脱敏]"}
        elif "phone" in types:
            rules[column] = {"type": "mask", "keepPrefix": 3, "keepSuffix": 4, "char": "*"}
        elif "email" in types:
            rules[column] = {"type": "hash", "length": 12, "salt": "aitp"}
        else:
            rules[column] = {"type": "mask", "keepPrefix": 0, "keepSuffix": 0, "char": "*"}
    return rules


def _hash_text(value: str, salt: str, length: int) -> str:
    return hashlib.sha256(f"{salt}:{value}".encode("utf-8")).hexdigest()[:length]


def _random_text(value: str, rule: dict[str, Any]) -> str:
    length = int(rule.get("length") or 8)
    charset = "0123456789" if rule.get("charset") == "digits" else "0123456789abcdefghijklmnopqrstuvwxyz"
    digest = hmac.new(b"aitp-random", value.encode("utf-8"), hashlib.sha256).digest()
    chars = [charset[digest[index % len(digest)] % len(charset)] for index in range(length)]
    return f"{rule.get('prefix') or ''}{''.join(chars)}"


def mask_value(value: Any, rule: dict[str, Any]) -> Any:
    """按规则处理单个值；空值保持空值，非字符串先转字符串。"""
    if value is None or value == "":
        return value
    text = str(value)
    rule_type = rule.get("type")
    if rule_type == "mask":
        keep_prefix = int(rule.get("keepPrefix") or 0)
        keep_suffix = int(rule.get("keepSuffix") or 0)
        if keep_prefix + keep_suffix >= len(text):
            return str(rule.get("char") or "*") * len(text)
        char = str(rule.get("char") or "*")
        middle = len(text) - keep_prefix - keep_suffix
        return f"{text[:keep_prefix]}{char * middle}{text[len(text) - keep_suffix:] if keep_suffix else ''}"
    if rule_type == "hash":
        return _hash_text(text, str(rule.get("salt") or "aitp"), int(rule.get("length") or 12))
    if rule_type == "random":
        return _random_text(text, rule)
    if rule_type == "enum":
        values = rule.get("values") or []
        if not values:
            return ""
        digest = hashlib.sha256(f"aitp-enum:{text}".encode("utf-8")).digest()
        return values[digest[0] % len(values)]
    return str(rule.get("replacement") or "[已脱敏]")


def apply_rules(
    rows: Sequence[dict[str, Any]], rules: dict[str, dict[str, Any]]
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    counts: dict[str, int] = {column: 0 for column in rules}
    masked: list[dict[str, Any]] = []
    for row in rows:
        output = dict(row)
        for column, rule in rules.items():
            if column not in output:
                continue
            before = output[column]
            output[column] = mask_value(before, rule)
            if str(before or "") != str(output[column] or ""):
                counts[column] += 1
        masked.append(output)
    return masked, counts


def validate_masked(
    masked_rows: Sequence[dict[str, Any]], columns: Sequence[str]
) -> list[str]:
    """全表复扫；返回仍含 PII 的列名。空列表表示通过。"""
    leaks: list[str] = []
    for column in columns:
        for row in masked_rows:
            if pii.contains_sensitive(str(row.get(column) or "")):
                leaks.append(column)
                break
    return leaks


def _normalize_headers(raw: Sequence[Any]) -> list[str]:
    headers: list[str] = []
    used: set[str] = set()
    for index, value in enumerate(raw):
        name = str(value or "").strip() or f"col{index + 1}"
        name = name[:80]
        base = name
        suffix = 2
        while name in used:
            name = f"{base}_{suffix}"
            suffix += 1
        used.add(name)
        headers.append(name)
    return headers


def _rows_from_matrix(headers: list[str], matrix: Sequence[Sequence[Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for values in matrix:
        if all(value in (None, "") for value in values):
            continue
        row = {header: ("" if value is None else str(value)) for header, value in zip(headers, values)}
        rows.append(row)
    return rows


def parse_sample(data: bytes, filename: str) -> tuple[str, list[str], list[dict[str, Any]]]:
    """解析 CSV / JSON / XLSX 为列名与行；原始字节只在本次调用内使用。"""
    if not data:
        raise _fail("样本文件为空")
    if len(data) > MAX_SAMPLE_BYTES:
        raise _fail(f"样本文件不能超过 {MAX_SAMPLE_BYTES // 1024 // 1024} MB")
    suffix = Path(filename or "").suffix.lower()
    if suffix == ".csv":
        text = _decode_csv(data)
        reader = csv.reader(io.StringIO(text))
        all_rows = [row for row in reader if any(str(cell).strip() for cell in row)]
        if not all_rows:
            raise _fail("CSV 样本没有表头")
        headers = _normalize_headers(all_rows[0])
        if len(headers) > MAX_COLUMNS:
            raise _fail(f"列数不能超过 {MAX_COLUMNS}")
        rows = _rows_from_matrix(headers, all_rows[1:])
        return "csv", headers, _limit_rows(rows)
    if suffix == ".json":
        try:
            payload = json.loads(data.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise _fail(f"JSON 样本无法解析：{exc}") from None
        if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
            raise _fail("JSON 样本必须是对象数组")
        raw_headers: list[str] = []
        for item in payload:
            for key in item:
                if str(key) not in raw_headers:
                    raw_headers.append(str(key))
        headers = _normalize_headers(raw_headers)
        if len(headers) > MAX_COLUMNS:
            raise _fail(f"列数不能超过 {MAX_COLUMNS}")
        rows = [
            {header: ("" if item.get(key) is None else str(item.get(key))) for header, key in zip(headers, raw_headers)}
            for item in payload if isinstance(item, dict)
        ]
        return "json", headers, _limit_rows(rows)
    if suffix == ".xlsx":
        try:
            from openpyxl import load_workbook
        except ImportError:
            raise _fail("本机缺少 openpyxl，无法解析 Excel 样本") from None
        try:
            workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            sheet = workbook[workbook.sheetnames[0]]
            matrix = [list(row) for row in sheet.iter_rows(values_only=True)]
        except Exception as exc:  # noqa: BLE001 - 损坏文件统一转参数错误
            raise _fail(f"Excel 样本无法解析：{exc}") from None
        if not matrix:
            raise _fail("Excel 样本没有表头")
        headers = _normalize_headers(matrix[0])
        if len(headers) > MAX_COLUMNS:
            raise _fail(f"列数不能超过 {MAX_COLUMNS}")
        rows = _rows_from_matrix(headers, matrix[1:])
        return "xlsx", headers, _limit_rows(rows)
    raise _fail("样本格式只支持 .csv / .json / .xlsx")


def _decode_csv(data: bytes) -> str:
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise _fail("CSV 编码无法识别，请使用 UTF-8 或 GB18030")


def _limit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    if len(rows) > MAX_ROWS:
        raise _fail(f"样本行数不能超过 {MAX_ROWS}")
    return rows
