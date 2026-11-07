"""接口用例执行引擎。

把「接口测试」用例的扩展字段（URL / 方法 / 请求头 / 请求参数 / 请求体 /
预期状态码 / 预期响应体 / 断言规则）组装成真实 HTTP 请求并执行断言。

安全边界：
  - 请求地址必须与所绑定测试环境的应用地址同源，重定向也不允许跨源，
    防止把本机工作台当成任意地址的请求代理；
  - 地址中不允许携带账号口令；
  - 响应体只保留片段，避免大响应把库撑爆。

执行结果会写入 biz_interface_execution；来自轮次时同时回写轮次用例与
用例全局状态，失败原因可直接转缺陷。
"""

from __future__ import annotations

import json
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.case_execution_repo import CaseExecutionRepository
from ..repositories.test_case_repo import TestCaseRepository
from ..repositories.test_env_repo import TestEnvRepository
from ..repositories.test_run_repo import (
    RUN_FAILED,
    RUN_PASSED,
    TestRunCaseRepository,
    TestRunRepository,
)
from . import test_run_service

DEFAULT_TIMEOUT = 10
MAX_TIMEOUT = 60
MAX_BODY_CHARS = 20000
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_REQUEST_BODY_CHARS = 5000
SUPPORTED_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD")
SENSITIVE_HEADERS = ("authorization", "cookie", "set-cookie", "x-api-key", "proxy-authorization")

Assertion = dict[str, Any]
Detail = dict[str, Any]


# ---------------------------------------------------------------- 文本与映射

def _text(extra: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = extra.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def _unquote(text: str) -> str:
    value = text.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def _parse_mapping(text: str) -> dict[str, str]:
    """请求头 / 参数：优先按 JSON 对象解析，否则按 Key: Value 或 a=1 行解析。"""
    value = text.strip()
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        return {str(key): str(item) for key, item in data.items() if str(key).strip()}
    result: dict[str, str] = {}
    for chunk in re.split(r"[\n;]+", value):
        line = chunk.strip().strip(",")
        if not line:
            continue
        if ":" in line:
            key, item = line.split(":", 1)
        elif "=" in line:
            key, item = line.split("=", 1)
        else:
            continue
        key = _unquote(key)
        if key:
            result[key] = _unquote(item)
    return result


def _parse_query(text: str) -> dict[str, str]:
    value = text.strip()
    if not value:
        return {}
    try:
        data = json.loads(value)
    except (TypeError, ValueError):
        data = None
    if isinstance(data, dict):
        return {str(key): str(item) for key, item in data.items()}
    if "&" in value:
        return dict(urllib.parse.parse_qsl(value, keep_blank_values=True))
    return _parse_mapping(value)


def _substitute(text: str, variables: dict[str, str]) -> str:
    result = text
    for key, value in variables.items():
        result = result.replace("{{" + key + "}}", value)
        result = result.replace("{{ " + key + " }}", value)
    return result


def _auth_header(value: str) -> tuple[dict[str, str], str]:
    """把「鉴权方式」字段转成请求头，并把裸 token 暴露给 {{token}} 占位符。"""
    text = value.strip()
    if not text:
        return {}, ""
    lower = text.lower()
    if lower.startswith("bearer "):
        return {"Authorization": text}, text[7:].strip()
    if lower.startswith("basic "):
        return {"Authorization": text}, ""
    if lower.startswith("cookie:"):
        return {"Cookie": text.split(":", 1)[1].strip()}, ""
    if ":" in text and text.split(":", 1)[0].strip().lower() == "authorization":
        return {"Authorization": text.split(":", 1)[1].strip()}, ""
    if " " in text:
        return {"Authorization": text}, ""
    return {"Authorization": f"Bearer {text}"}, text


def _redact(headers: dict[str, str]) -> dict[str, str]:
    return {
        key: ("***" if key.lower() in SENSITIVE_HEADERS else value)
        for key, value in headers.items()
    }


# ---------------------------------------------------------------- 地址与请求

def _origin(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme.lower() not in ("http", "https") or not parsed.hostname:
            return None
        port = parsed.port or (443 if parsed.scheme.lower() == "https" else 80)
    except ValueError:
        return None
    return (parsed.scheme.lower(), parsed.hostname.lower(), port)


def _host_label(origin: tuple[str, str, int] | None) -> str:
    if origin is None:
        return "非法地址"
    return f"{origin[1]}:{origin[2]}"


def _build_url(raw: str, base_url: str, env_name: str) -> tuple[str, tuple[str, str, int]]:
    base = base_url.strip().rstrip("/")
    if not base:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"环境「{env_name or '未命名'}」未配置应用服务器地址，无法执行接口用例",
        )
    expected = _origin(base)
    if expected is None:
        raise AppError(ErrorCode.PARAM_ERROR, f"环境「{env_name or '未命名'}」的应用服务器地址不合法：{base}")

    target = raw.strip()
    if not target:
        raise AppError(ErrorCode.PARAM_ERROR, "接口用例缺少「接口URL」")
    target = target.replace("{{baseUrl}}", base).replace("{{base_url}}", base)
    if target.startswith("/"):
        target = base + target
    elif "://" not in target:
        target = f"{base}/{target.lstrip('/')}"

    parsed = urllib.parse.urlsplit(target)
    actual = _origin(target)
    if actual is None:
        raise AppError(ErrorCode.PARAM_ERROR, f"接口地址不合法：{target}")
    if parsed.username or parsed.password:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION, "接口地址不允许携带账号口令，请改用鉴权请求头"
        )
    if actual != expected:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"接口地址 {_host_label(actual)} 与环境「{env_name or '未命名'}」的应用地址"
            f" {_host_label(expected)} 不同源，已拒绝执行",
        )
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path or "/", parsed.query, "")
    ), expected


def _append_query(url: str, query: dict[str, str]) -> str:
    if not query:
        return url
    parsed = urllib.parse.urlsplit(url)
    pairs = urllib.parse.parse_qsl(parsed.query, keep_blank_values=True)
    pairs.extend(query.items())
    return urllib.parse.urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urllib.parse.urlencode(pairs), "")
    )


class _SameOriginRedirectHandler(urllib.request.HTTPRedirectHandler):
    """重定向也必须留在环境应用地址内，避免借 302 绕过同源校验。"""

    def __init__(self, origin: tuple[str, str, int]) -> None:
        super().__init__()
        self._origin = origin

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if _origin(newurl) != self._origin:
            raise urllib.error.URLError("重定向目标与环境应用地址不同源，已拒绝执行")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _decode_body(raw: bytes, charset: str) -> str:
    try:
        text = raw.decode(charset, errors="replace")
    except LookupError:
        text = raw.decode("utf-8", errors="replace")
    return text


def _send(
    url: str,
    method: str,
    headers: dict[str, str],
    data: bytes | None,
    timeout: int,
    origin: tuple[str, str, int],
) -> dict[str, Any]:
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    opener = urllib.request.build_opener(_SameOriginRedirectHandler(origin))
    started = time.monotonic()
    try:
        with opener.open(request, timeout=timeout) as response:
            raw = response.read(MAX_RESPONSE_BYTES + 1)
            charset = response.headers.get_content_charset() or "utf-8"
            elapsed = round((time.monotonic() - started) * 1000)
            return {
                "statusCode": int(response.status),
                "headers": dict(response.headers.items()),
                "body": _decode_body(raw, charset),
                "truncated": len(raw) > MAX_RESPONSE_BYTES,
                "durationMs": elapsed,
                "error": "",
            }
    except urllib.error.HTTPError as exc:
        raw = exc.read(MAX_RESPONSE_BYTES + 1)
        charset = (exc.headers.get_content_charset() if exc.headers else None) or "utf-8"
        elapsed = round((time.monotonic() - started) * 1000)
        return {
            "statusCode": int(exc.code),
            "headers": dict(exc.headers.items()) if exc.headers else {},
            "body": _decode_body(raw, charset),
            "truncated": len(raw) > MAX_RESPONSE_BYTES,
            "durationMs": elapsed,
            "error": "",
        }
    except (urllib.error.URLError, TimeoutError, socket.timeout, OSError) as exc:
        elapsed = round((time.monotonic() - started) * 1000)
        reason = getattr(exc, "reason", exc)
        if isinstance(reason, (TimeoutError, socket.timeout)):
            message = f"请求超时（超过 {timeout} 秒）"
        else:
            message = f"请求失败：{reason}"
        return {
            "statusCode": None,
            "headers": {},
            "body": "",
            "truncated": False,
            "durationMs": elapsed,
            "error": message,
        }


# ---------------------------------------------------------------- 断言

def _json_path_get(data: Any, path: str) -> tuple[bool, Any]:
    text = path.strip()
    if text.startswith("$"):
        text = text[1:]
    if text.startswith("."):
        text = text[1:]
    if not text:
        return True, data
    current = data
    for key, index in re.findall(r"\.?([^.\[\]]+)|\[(\d+)\]", text):
        if key:
            if not isinstance(current, dict) or key not in current:
                return False, None
            current = current[key]
        else:
            if not isinstance(current, list) or int(index) >= len(current):
                return False, None
            current = current[int(index)]
    return True, current


def _equals(actual: Any, expected: Any) -> bool:
    if isinstance(expected, str):
        return str(actual) == expected
    return bool(actual == expected)


def _subset_match(actual: Any, expected: Any, path: str = "$") -> tuple[bool, str, Any]:
    if isinstance(expected, dict):
        if not isinstance(actual, dict):
            return False, path, actual
        for key, value in expected.items():
            if key not in actual:
                return False, f"{path}.{key}", None
            matched, failed_path, actual_value = _subset_match(
                actual[key], value, f"{path}.{key}"
            )
            if not matched:
                return False, failed_path, actual_value
        return True, "", actual
    if isinstance(expected, list):
        if not isinstance(actual, list) or len(actual) != len(expected):
            return False, path, actual
        for index, value in enumerate(expected):
            matched, failed_path, actual_value = _subset_match(
                actual[index], value, f"{path}[{index}]"
            )
            if not matched:
                return False, failed_path, actual_value
        return True, "", actual
    return (_equals(actual, expected), path, actual)


def _format_value(value: Any) -> str:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False)
    else:
        text = str(value)
    return text if len(text) <= 200 else text[:200] + "…"


def _parse_status_expectation(text: str) -> tuple[list[int], list[int], list[tuple[int, int]], str]:
    codes: list[int] = []
    prefixes: list[int] = []
    ranges: list[tuple[int, int]] = []
    raw = text.strip()
    for part in re.split(r"[,，\s]+", raw):
        if not part:
            continue
        exact = re.fullmatch(r"\d{3}", part)
        band = re.fullmatch(r"([1-5])xx", part, re.I)
        span = re.fullmatch(r"(\d{3})-(\d{3})", part)
        if exact:
            codes.append(int(part))
        elif band:
            prefixes.append(int(band.group(1)))
        elif span:
            ranges.append((int(span.group(1)), int(span.group(2))))
    if not codes and not prefixes and not ranges:
        codes = [200]
        raw = "200"
    return codes, prefixes, ranges, raw or "200"


def _status_matches(
    status: int,
    codes: list[int],
    prefixes: list[int],
    ranges: list[tuple[int, int]],
) -> bool:
    if status in codes:
        return True
    if status // 100 in prefixes:
        return True
    return any(low <= status <= high for low, high in ranges)


def _parse_rule(line: str) -> tuple[str, str, str]:
    """解析一行断言：目标 + 比较符 + 期望值。解析失败按「响应体包含」处理。"""
    pattern = re.compile(
        r"^(json:[^\s]+|json\s+[^\s]+|header:[^\s:]+|header\s+[^\s:]+|body|status)"
        r"\s*(==|=|!=|not_contains|contains|not_exists|exists|regex|~|"
        r"等于|包含|不包含|正则|存在|不存在)\s*(.*)$",
        re.I,
    )
    matched = pattern.match(line.strip())
    if not matched:
        return "body", "contains", line.strip()
    target = matched.group(1).strip()
    op = matched.group(2).strip().lower()
    expected = _unquote(matched.group(3).strip())
    ops = {
        "==": "=",
        "等于": "=",
        "包含": "contains",
        "不包含": "not_contains",
        "正则": "regex",
        "~": "regex",
        "存在": "exists",
        "不存在": "not_exists",
    }
    return target, ops.get(op, op), expected


def _make_assertion(
    name: str,
    passed: bool,
    expected: str,
    actual: Any,
    message: str = "",
) -> Assertion:
    return {
        "name": name,
        "passed": passed,
        "expected": expected,
        "actual": _format_value(actual),
        "message": "" if passed else message,
    }


def _eval_rule(
    line: str,
    status: int,
    headers: dict[str, str],
    body: str,
    parsed_json: Any,
    json_ok: bool,
) -> Assertion:
    target, op, expected = _parse_rule(line)
    lower = target.lower()
    actual: Any = None
    exists = True
    name = f"断言：{line}"

    if lower == "status":
        actual = status
        name = "状态码"
    elif lower == "body":
        actual = body
        name = "响应体"
    elif lower.startswith("header:"):
        header_name = target.split(":", 1)[1].strip()
        header_map = {key.lower(): value for key, value in headers.items()}
        actual = header_map.get(header_name.lower())
        exists = actual is not None
        name = f"响应头 {header_name}"
    else:
        path = target[5:].strip() if lower.startswith("json:") else target[4:].strip()
        name = f"JSONPath {path}"
        if not json_ok:
            return _make_assertion(name, False, expected, "非 JSON 响应", "响应体不是合法 JSON")
        exists, actual = _json_path_get(parsed_json, path)

    if op in ("exists", "not_exists"):
        passed = exists if op == "exists" else not exists
        message = "字段不存在" if op == "exists" and not exists else ""
        if op == "not_exists" and exists:
            message = f"字段存在，实际值 {_format_value(actual)}"
        return _make_assertion(name, passed, "存在" if op == "exists" else "不存在", actual, message)

    if not exists:
        return _make_assertion(name, False, expected, "字段不存在", "字段不存在")

    if op == "contains":
        passed = expected in str(actual)
        message = f"未包含「{expected}」"
    elif op == "not_contains":
        passed = expected not in str(actual)
        message = f"不应包含「{expected}」"
    elif op == "regex":
        try:
            passed = re.search(expected, str(actual)) is not None
        except re.error as exc:
            return _make_assertion(name, False, expected, actual, f"正则表达式非法：{exc}")
        message = f"未匹配正则 {expected}"
    elif op == "!=":
        passed = not _equals(actual, _scalar(expected))
        message = f"不应等于 {expected}"
    else:
        passed = _equals(actual, _scalar(expected))
        message = f"期望 {expected}，实际 {_format_value(actual)}"
    return _make_assertion(name, passed, expected, actual, message)


def _scalar(text: str) -> Any:
    value = text.strip()
    if not value:
        return ""
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return value


def _evaluate(
    extra: dict[str, Any],
    status: int,
    headers: dict[str, str],
    body: str,
) -> list[Assertion]:
    parsed_json: Any = None
    json_ok = False
    if body.strip():
        try:
            parsed_json = json.loads(body)
            json_ok = True
        except (TypeError, ValueError):
            json_ok = False

    assertions: list[Assertion] = []
    codes, prefixes, ranges, expected_status = _parse_status_expectation(
        _text(extra, "预期状态码")
    )
    status_ok = _status_matches(status, codes, prefixes, ranges)
    assertions.append(
        _make_assertion(
            "状态码",
            status_ok,
            expected_status,
            status,
            f"期望 {expected_status}，实际 {status}",
        )
    )

    expected_body = _text(extra, "预期响应体")
    if expected_body:
        try:
            expected_json = json.loads(expected_body)
        except (TypeError, ValueError):
            contains = expected_body in body
            assertions.append(
                _make_assertion(
                    "预期响应体",
                    contains,
                    expected_body,
                    body[:200],
                    "响应体未包含预期内容",
                )
            )
        else:
            if not json_ok:
                assertions.append(
                    _make_assertion("预期响应体", False, expected_body, body[:200], "响应体不是合法 JSON")
                )
            else:
                matched, path, actual_value = _subset_match(parsed_json, expected_json)
                assertions.append(
                    _make_assertion(
                        "预期响应体",
                        matched,
                        expected_body,
                        actual_value,
                        f"字段 {path} 与预期不一致",
                    )
                )

    rules = _text(extra, "断言规则")
    if rules:
        for line in re.split(r"[\n;]+", rules):
            rule = line.strip()
            if rule:
                assertions.append(_eval_rule(rule, status, headers, body, parsed_json, json_ok))
    return assertions


# ---------------------------------------------------------------- 执行主流程

def execute(
    extra: dict[str, Any],
    env: dict[str, Any],
    *,
    timeout: int = DEFAULT_TIMEOUT,
) -> Detail:
    """按接口用例扩展字段发一次真实请求，返回请求、响应与断言明细。"""
    timeout = max(1, min(MAX_TIMEOUT, int(timeout or DEFAULT_TIMEOUT)))
    method = (_text(extra, "请求方式", "请求方法", "method") or "GET").upper()
    if method not in SUPPORTED_METHODS:
        raise AppError(ErrorCode.PARAM_ERROR, f"不支持的请求方式：{method}")

    base_url = str(env.get("app_base_url") or "")
    raw_url = _text(extra, "接口URL", "接口url", "URL", "url")
    url, origin = _build_url(raw_url, base_url, str(env.get("name") or ""))

    auth_headers, token = _auth_header(_text(extra, "token/鉴权方式", "鉴权方式", "token"))
    variables = {"baseUrl": base_url.rstrip("/"), "base_url": base_url.rstrip("/"), "token": token}

    headers = _parse_mapping(_text(extra, "请求头"))
    headers = {key: _substitute(value, variables) for key, value in headers.items()}
    for key, value in auth_headers.items():
        headers.setdefault(key, _substitute(value, variables))
    url = _substitute(url, variables)

    query = _parse_query(_text(extra, "请求参数"))
    query = {key: _substitute(value, variables) for key, value in query.items()}
    url = _append_query(url, query)

    raw_body = _substitute(_text(extra, "请求体"), variables)
    data = raw_body.encode("utf-8") if raw_body else None
    if data is not None and not any(key.lower() == "content-type" for key in headers):
        try:
            json.loads(raw_body)
            headers["Content-Type"] = "application/json"
        except (TypeError, ValueError):
            headers["Content-Type"] = "application/x-www-form-urlencoded"

    response = _send(url, method, headers, data, timeout, origin)
    body_text = response["body"]
    truncated = bool(response["truncated"]) or len(body_text) > MAX_BODY_CHARS
    body_text = body_text[:MAX_BODY_CHARS]
    status_code = response["statusCode"]

    assertions: list[Assertion] = []
    if status_code is not None:
        assertions = _evaluate(extra, status_code, response["headers"], body_text)
    failed = [item for item in assertions if not item["passed"]]
    passed = not response["error"] and bool(assertions) and not failed

    request_body = raw_body[:MAX_REQUEST_BODY_CHARS]
    detail: Detail = {
        "result": RUN_PASSED if passed else RUN_FAILED,
        "passed": passed,
        "statusCode": status_code,
        "durationMs": response["durationMs"],
        "message": response["error"] or (
            f"{len(assertions)} 项断言全部通过" if passed
            else f"{len(failed)} 项断言失败"
        ),
        "request": {
            "method": method,
            "url": url,
            "headers": _redact(headers),
            "body": request_body,
            "bodyTruncated": len(raw_body) > MAX_REQUEST_BODY_CHARS,
        },
        "response": {
            "status": status_code,
            "headers": _redact(response["headers"]),
            "body": body_text,
            "bodyTruncated": truncated,
        },
        "assertions": assertions,
    }
    return detail


def _actual_text(detail: Detail) -> str:
    """回写到轮次用例的实际结果：一眼能看出 HTTP 状态、耗时与失败断言。"""
    status_code = detail.get("statusCode")
    lines: list[str] = []
    if status_code is None:
        lines.append(str(detail.get("message") or "请求失败"))
    else:
        lines.append(f"HTTP {status_code}（{detail.get('durationMs') or 0}ms）")
    assertions = detail.get("assertions") or []
    failed = [item for item in assertions if not item.get("passed")]
    if failed:
        lines.append("断言失败：")
        for item in failed:
            lines.append(f"- {item.get('name')}：{item.get('message')}")
    elif assertions:
        lines.append(f"断言：{len(assertions)} 项全部通过")
    response = detail.get("response") or {}
    snippet = str(response.get("body") or "")
    if failed and snippet:
        lines.append("响应片段：" + snippet[:1000])
    return "\n".join(lines)[:10000]


def record_detail(
    conn: sqlite3.Connection,
    *,
    case_id: int,
    run_id: int | None,
    run_case_id: int | None,
    env_id: int | None,
    detail: Detail,
    executed_by: str = "",
) -> int:
    request = detail.get("request") or {}
    response = detail.get("response") or {}
    cursor = conn.execute(
        """
        INSERT INTO biz_interface_execution
            (case_id, run_id, run_case_id, env_id, method, url, status_code,
             duration_ms, result, request_json, response_json, assertions_json,
             error_message, executed_by, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            case_id,
            run_id,
            run_case_id,
            env_id,
            str(request.get("method") or ""),
            str(request.get("url") or ""),
            detail.get("statusCode"),
            int(detail.get("durationMs") or 0),
            str(detail.get("result") or RUN_FAILED),
            dumps(request),
            dumps(response),
            dumps(detail.get("assertions") or []),
            str(detail.get("message") or "")[:2000],
            executed_by[:64],
            utcnow_iso(),
        ),
    )
    return int(cursor.lastrowid or 0)


def _load_env(conn: sqlite3.Connection, env_id: int | None) -> dict[str, Any]:
    if env_id is None:
        raise AppError(ErrorCode.PARAM_ERROR, "请先选择执行环境")
    env = TestEnvRepository(conn).get_by_id(int(env_id))
    if env is None:
        raise AppError(ErrorCode.PARAM_ERROR, "执行环境不存在或已删除")
    return env


def run_case(
    conn: sqlite3.Connection,
    case_id: int,
    env_id: int | None,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    executed_by: str = "local",
) -> Detail:
    """从用例库直接执行一条接口用例，并回写用例全局状态与执行流水。"""
    case = TestCaseRepository(conn).get_detail(case_id)
    if case is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    if str(case.get("case_type") or "") != "interface":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有「接口测试」类型的用例可以自动执行")
    env = _load_env(conn, env_id)
    detail = execute(case.get("extra") or {}, env, timeout=timeout)

    execution_id = record_detail(
        conn,
        case_id=case_id,
        run_id=None,
        run_case_id=None,
        env_id=int(env["id"]),
        detail=detail,
        executed_by=executed_by,
    )
    status = "pass" if detail["result"] == RUN_PASSED else "fail"
    TestCaseRepository(conn).update(case_id, {"status": status})
    CaseExecutionRepository(conn).record(
        case_id,
        status,
        duration_ms=detail.get("durationMs"),
        environment=str(env.get("name") or ""),
        executed_by=executed_by,
        remark=str(detail.get("message") or "")[:2000],
    )
    detail["executionId"] = execution_id
    detail["envId"] = int(env["id"])
    detail["envName"] = str(env.get("name") or "")
    return detail


def _run_run_case(
    conn: sqlite3.Connection,
    run: dict[str, Any],
    row: dict[str, Any],
    env: dict[str, Any],
    timeout: int,
    executed_by: str,
) -> Detail:
    extra = row.get("extra")
    if not isinstance(extra, dict):
        extra = loads(row.get("extra_json"), {}) or {}
    detail = execute(extra, env, timeout=timeout)
    record_detail(
        conn,
        case_id=int(row["case_id"]),
        run_id=int(run["id"]),
        run_case_id=int(row["id"]),
        env_id=int(env["id"]),
        detail=detail,
        executed_by=executed_by,
    )
    result = test_run_service.record_result(
        conn,
        int(run["id"]),
        int(row["id"]),
        result=RUN_PASSED if detail["result"] == RUN_PASSED else RUN_FAILED,
        actual_result=_actual_text(detail),
        executed_by=executed_by,
    )
    detail["runCase"] = result["runCase"]
    detail["run"] = result["run"]
    detail["envId"] = int(env["id"])
    detail["envName"] = str(env.get("name") or "")
    return detail


def run_run_case(
    conn: sqlite3.Connection,
    run_id: int,
    run_case_id: int,
    *,
    env_id: int | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    executed_by: str = "local",
) -> Detail:
    """执行轮次里的一条接口用例：用快照定义，结果回写轮次与用例状态。"""
    run = TestRunRepository(conn).get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    row = TestRunCaseRepository(conn).get_of_run(run_id, run_case_id)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "轮次用例不存在")
    if str(row.get("case_type") or "") != "interface":
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "只有「接口测试」类型的用例可以自动执行")
    env = _load_env(conn, env_id if env_id is not None else run.get("env_id"))
    return _run_run_case(conn, run, row, env, timeout, executed_by)


def run_batch(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    env_id: int | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    executed_by: str = "local",
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[str, Any]:
    """顺序执行轮次内全部接口用例；单条配置错误只失败该条，不中断整批。"""
    run = TestRunRepository(conn).get(run_id)
    if run is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试轮次不存在")
    env = _load_env(conn, env_id if env_id is not None else run.get("env_id"))
    rows = [
        row for row in TestRunCaseRepository(conn).list_by_run(run_id)
        if str(row.get("case_type") or "") == "interface"
    ]
    if not rows:
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "轮次里没有「接口测试」类型的用例")

    total = len(rows)
    passed = 0
    failed = 0
    details: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        duration_ms: int | None = None
        try:
            detail = _run_run_case(conn, run, row, env, timeout, executed_by)
            result = str(detail.get("result") or RUN_FAILED)
            status_code = detail.get("statusCode")
            duration_ms = detail.get("durationMs")
            message = str(detail.get("message") or "")
        except AppError as exc:
            result = RUN_FAILED
            status_code = None
            message = str(exc)
            test_run_service.record_result(
                conn,
                run_id,
                int(row["id"]),
                result=RUN_FAILED,
                actual_result=message,
                executed_by=executed_by,
            )
        if result == RUN_PASSED:
            passed += 1
        else:
            failed += 1
        details.append({
            "runCaseId": int(row["id"]),
            "caseId": int(row["case_id"]),
            "caseNo": row.get("case_no"),
            "title": row.get("title"),
            "result": result,
            "statusCode": status_code,
            "durationMs": duration_ms,
            "message": message,
        })
        if on_progress is not None:
            on_progress(index, total)
        conn.commit()
    return {
        "runId": run_id,
        "envId": int(env["id"]),
        "envName": str(env.get("name") or ""),
        "total": total,
        "passed": passed,
        "failed": failed,
        "details": details,
    }


def execution_summary(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "caseId": row.get("case_id"),
        "runId": row.get("run_id"),
        "runCaseId": row.get("run_case_id"),
        "envId": row.get("env_id"),
        "method": row.get("method"),
        "url": row.get("url"),
        "statusCode": row.get("status_code"),
        "durationMs": row.get("duration_ms"),
        "result": row.get("result"),
        "request": loads(row.get("request_json"), {}) or {},
        "response": loads(row.get("response_json"), {}) or {},
        "assertions": loads(row.get("assertions_json"), []) or [],
        "message": row.get("error_message") or "",
        "executedBy": row.get("executed_by"),
        "createdAt": row.get("created_at"),
    }


def latest_execution(
    conn: sqlite3.Connection, *, run_case_id: int | None = None,
    case_id: int | None = None,
) -> dict[str, Any] | None:
    if run_case_id is not None:
        row = conn.execute(
            "SELECT * FROM biz_interface_execution WHERE run_case_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (run_case_id,),
        ).fetchone()
    elif case_id is not None:
        row = conn.execute(
            "SELECT * FROM biz_interface_execution WHERE case_id = ?"
            " ORDER BY id DESC LIMIT 1",
            (case_id,),
        ).fetchone()
    else:
        return None
    return execution_summary(dict(row)) if row else None
