"""断言增强：JSON Schema 校验 + 可复用断言库（GAP-34）。

一期接口断言只有文本算子（状态码 / JSONPath / 正则 / 包含），比的是字符串，
响应结构变了也判不出来。本模块补两件事：

  1. **JSON Schema 校验**：内置一个零依赖的 draft-07 子集校验器（不打外网、
     不装新依赖），失败逐条给出「具体路径 + 期望 + 实际」差异，而不是只说「不匹配」；
  2. **断言库**：把「目标 + 算子 + 期望」抽成条目存进 ``biz_assertion_rule``，
     按项目共享（``module_id`` 为空）或按功能点共享（指向该节点，子树内可见），
     用例的「断言规则」里写 ``lib:<规则编号>`` 即引用，展开逻辑与文本算子同一套语法。

边界：不做性能断言、不做 GraphQL 专用断言、不做 $ref 跨文件引用。
"""

from __future__ import annotations

import json
import re
import sqlite3
from typing import Any, Callable

from ..errors import AppError, ErrorCode
from ..repositories.base import BaseRepository

#: 支持的 type 关键字（draft-07 基础类型 + draft 2019 的 null 写法）
SCHEMA_TYPES = (
    "object", "array", "string", "number", "integer", "boolean", "null", "none",
)

#: 递归与错误条数上限：结构再深也不能把一次执行拖死
MAX_SCHEMA_DEPTH = 24
MAX_SCHEMA_ERRORS = 50

#: 断言库引用写法：``lib:AR-0001`` / ``lib:AR-0001#状态码``
LIBRARY_PREFIX = "lib:"

#: 断言库里允许的算子白名单（与 interface_runner 的文本算子保持同名同义）
ASSERTION_OPERATORS = (
    "=", "!=", "contains", "not_contains", "regex", "exists", "not_exists",
    ">", ">=", "<", "<=", "not_empty", "is_empty", "one_of", "type",
)

#: 断言目标前缀：响应头 / JSONPath / Schema / 状态码 / 响应体
TARGET_PREFIXES = ("status", "body", "header:", "json:", "$", "schema:", "schema ")

#: 数值边界关键字：签名写死成 ``Callable[[float, float], bool]``，
#: 这样 mypy --strict 不会把 validate_instance 里的 cmp_op(...) 判成无类型调用。
NumericCmp = Callable[[float, float], bool]
NUMERIC_BOUNDS: tuple[tuple[str, NumericCmp, str], ...] = (
    ("minimum", lambda left, right: left < right, "不能小于"),
    ("maximum", lambda left, right: left > right, "不能大于"),
    ("exclusiveMinimum", lambda left, right: left <= right, "必须大于"),
    ("exclusiveMaximum", lambda left, right: left >= right, "必须小于"),
)


class AssertionRuleRepository(BaseRepository):
    """断言库条目：只按项目 + 功能点取，不提供跨项目读取。"""

    table = "biz_assertion_rule"
    default_order = "rule_no"
    default_order_dir = "asc"
    order_whitelist = {"id", "rule_no", "name", "module_id", "enabled", "created_at"}
    searchable = ["rule_no", "name", "rule", "description"]

    def list_visible(
        self, project_id: int, module_ids: set[int] | None = None
    ) -> list[dict[str, Any]]:
        """项目级（module_id 为空）+ 命中功能点集合的条目。"""
        sql = (
            "SELECT * FROM biz_assertion_rule WHERE project_id = ? AND is_deleted = 0"
            " AND (module_id IS NULL"
        )
        params: list[Any] = [int(project_id)]
        if module_ids:
            placeholders = ",".join("?" for _ in module_ids)
            sql += f" OR module_id IN ({placeholders})"
            params.extend(sorted(int(item) for item in module_ids))
        sql += ") ORDER BY rule_no ASC, id ASC"
        return [dict(row) for row in self.conn.execute(sql, params).fetchall()]

    def get_by_no(self, project_id: int, rule_no: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM biz_assertion_rule WHERE project_id = ? AND rule_no = ?"
            " AND is_deleted = 0",
            (int(project_id), str(rule_no)),
        ).fetchone()
        return dict(row) if row else None


# --------------------------------------------------------------------- 编号与功能点


def next_rule_no(conn: sqlite3.Connection, project_id: int) -> str:
    """断言库编号：AR-0001 递增，与需求/缺陷编号同口径（读放进写事务）。"""
    from ..repositories.numbering import begin_claim

    begin_claim(conn)
    row = conn.execute(
        "SELECT MAX(rule_no) FROM biz_assertion_rule WHERE project_id = ?", (int(project_id),)
    ).fetchone()
    highest = 0
    for part in str(row[0] or "").split("-"):
        if part.isdigit():
            highest = max(highest, int(part))
    return f"AR-{highest + 1:04d}"


def module_scope_ids(conn: sqlite3.Connection, module_id: int | None) -> set[int]:
    """用例所在功能点 + 其全部祖先节点：挂在祖先上的断言对子树内用例可见。"""
    if not module_id:
        return set()
    ids: set[int] = set()
    current: int | None = int(module_id)
    guard = 0
    while current and guard < 32:
        row = conn.execute(
            "SELECT id, parent_id FROM biz_module WHERE id = ? AND is_deleted = 0", (current,)
        ).fetchone()
        if row is None:
            break
        ids.add(int(row["id"]))
        parent = row["parent_id"]
        current = int(parent) if parent is not None else None
        guard += 1
    return ids


# --------------------------------------------------------------------- JSON Schema 子集


def parse_schema(text: Any, *, field: str = "schema") -> dict[str, Any]:
    """把断言里的 schema 文本解析成对象；非法时给中文可执行提示，不吞异常。"""
    if isinstance(text, dict):
        return dict(text)
    raw = str(text or "").strip()
    if not raw:
        raise AppError(ErrorCode.PARAM_ERROR, f"{field} 不能为空：请填写 JSON Schema 对象")
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{field} 不是合法 JSON：{exc}；请检查是否漏了引号或逗号",
        ) from None
    if not isinstance(data, dict):
        raise AppError(ErrorCode.PARAM_ERROR, f"{field} 必须是 JSON 对象，例如 {{'type':'object'}}")
    return data


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def type_name(value: Any) -> str:
    """对外用的类型名（与 schema 的 type 关键字同一口径）。"""
    return _type_name(value)


def is_empty_value(value: Any) -> bool:
    """空值判定：None / 空串 / 空集合 / 0 / False 都算「空」，与手工核对习惯一致。"""
    if value is None:
        return True
    if isinstance(value, (str, list, tuple, dict, set)):
        return len(value) == 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 0
    if isinstance(value, bool):
        return not value
    return False


def _type_matches(value: Any, expected: str) -> bool:
    actual = _type_name(value)
    if expected in ("none", "null"):
        return actual == "null"
    if expected == "number":
        return actual in ("number", "integer")
    if expected == "integer":
        return actual == "integer"
    return actual == expected


def _join(path: str, key: str) -> str:
    if not key:
        return path
    if key.startswith("["):
        return f"{path}{key}"
    return f"{path}.{key}"


def _error(path: str, keyword: str, expected: Any, actual: Any, message: str) -> dict[str, Any]:
    return {
        "path": path,
        "keyword": keyword,
        "expected": _plain(expected),
        "actual": _plain(actual),
        "message": message,
    }


def _plain(value: Any) -> Any:
    if isinstance(value, (dict, list)):
        text = json.dumps(value, ensure_ascii=False, default=str)
        return text if len(text) <= 200 else text[:200] + "…"
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    text = str(value)
    return text if len(text) <= 200 else text[:200] + "…"


def _resolve_ref(root: dict[str, Any], ref: str, instance: Any, path: str,
                 errors: list[dict[str, Any]], depth: int) -> None:
    text = str(ref or "")
    if not text.startswith("#"):
        _add_error(errors, _error(path, "$ref", text, None, "只支持本文件内引用（# 开头）"))
        return
    node: Any = root
    for part in text.lstrip("#").strip("/").split("/"):
        if not part:
            continue
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            _add_error(errors, _error(path, "$ref", text, None, "引用目标不存在"))
            return
        node = node[key]
    validate_instance(instance, node, path, errors, depth + 1, root)


def _add_error(errors: list[dict[str, Any]], item: dict[str, Any]) -> None:
    if len(errors) < MAX_SCHEMA_ERRORS:
        errors.append(item)


def _safe_compile(pattern: Any) -> re.Pattern[str] | None:
    try:
        return re.compile(str(pattern))
    except re.error:
        return None


def _compiled_patterns(value: Any) -> list[re.Pattern[str]]:
    """patternProperties 的正则清单：坏表达式在这里静默跳过，由上面的分支报错。"""
    if not isinstance(value, dict):
        return []
    compiled = [_safe_compile(pattern) for pattern in value]
    return [item for item in compiled if item is not None]


def validate_instance(
    instance: Any,
    schema: Any,
    path: str = "$",
    errors: list[dict[str, Any]] | None = None,
    depth: int = 0,
    root: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """按 draft-07 子集校验一个值，返回差异列表（空列表 = 通过）。"""
    collected: list[dict[str, Any]] = errors if errors is not None else []
    top = root if root is not None else (schema if isinstance(schema, dict) else {})
    if depth > MAX_SCHEMA_DEPTH:
        _add_error(collected, _error(path, "depth", f"≤{MAX_SCHEMA_DEPTH}", None, "schema 嵌套过深"))
        return collected
    if schema is True or schema == {}:
        return collected
    if schema is False:
        _add_error(collected, _error(path, "false", "不允许出现", instance, "该位置不允许有值"))
        return collected
    if not isinstance(schema, dict):
        _add_error(collected, _error(path, "schema", "对象", _type_name(schema),
                                    "schema 片段必须是对象或布尔"))
        return collected

    if "$ref" in schema:
        _resolve_ref(top, str(schema["$ref"]), instance, path, collected, depth)
        return collected

    if schema.get("nullable") is False and instance is None:
        _add_error(collected, _error(path, "nullable", "非 null", None, "该字段不允许为 null"))

    expected_type = schema.get("type")
    if expected_type is not None:
        names = [expected_type] if isinstance(expected_type, str) else (
            [item for item in expected_type if isinstance(item, str)]
            if isinstance(expected_type, list) else []
        )
        if names and not any(_type_matches(instance, name) for name in names):
            _add_error(
                collected,
                _error(path, "type", " / ".join(names), _type_name(instance),
                       f"类型应为 {' / '.join(names)}，实际是 {_type_name(instance)}"),
            )
            return collected

    if "const" in schema and instance != schema["const"]:
        _add_error(collected, _error(path, "const", schema["const"], instance, "值必须完全相等"))

    if "enum" in schema:
        allowed = schema["enum"] if isinstance(schema["enum"], list) else []
        if instance not in allowed:
            _add_error(collected, _error(path, "enum", allowed, instance, "取值不在允许清单里"))

    if isinstance(instance, str):
        if "minLength" in schema and len(instance) < int(schema["minLength"] or 0):
            _add_error(collected, _error(path, "minLength", schema["minLength"], len(instance),
                                        f"长度不足 {schema['minLength']}"))
        if "maxLength" in schema and len(instance) > int(schema["maxLength"] or 0):
            _add_error(collected, _error(path, "maxLength", schema["maxLength"], len(instance),
                                        f"长度超过 {schema['maxLength']}"))
        if "pattern" in schema:
            try:
                matched = re.search(str(schema["pattern"]), instance) is not None
            except re.error as exc:
                _add_error(collected, _error(path, "pattern", schema["pattern"], None,
                                            f"正则表达式非法：{exc}"))
                matched = True
            if not matched:
                _add_error(collected, _error(path, "pattern", schema["pattern"], instance,
                                            "文本未匹配正则"))

    if isinstance(instance, (int, float)) and not isinstance(instance, bool):
        for keyword, cmp_op, verb in NUMERIC_BOUNDS:
            if keyword in schema:
                bound = schema[keyword]
                if isinstance(bound, (int, float)) and cmp_op(instance, bound):
                    _add_error(collected, _error(path, keyword, bound, instance,
                                                f"{verb} {bound}"))
        if "multipleOf" in schema:
            step = schema["multipleOf"]
            if isinstance(step, (int, float)) and step and instance % step != 0:
                _add_error(collected, _error(path, "multipleOf", step, instance,
                                            f"不是 {step} 的整数倍"))

    if isinstance(instance, list):
        if "minItems" in schema and len(instance) < int(schema["minItems"] or 0):
            _add_error(collected, _error(path, "minItems", schema["minItems"], len(instance),
                                        "数组元素偏少"))
        if "maxItems" in schema and len(instance) > int(schema["maxItems"] or 0):
            _add_error(collected, _error(path, "maxItems", schema["maxItems"], len(instance),
                                        "数组元素偏多"))
        items = schema.get("items")
        if isinstance(items, list):
            for index, item in enumerate(instance):
                if index < len(items):
                    validate_instance(item, items[index], f"{path}[{index}]",
                                      collected, depth + 1, top)
        elif items is not None:
            for index, item in enumerate(instance):
                validate_instance(item, items, f"{path}[{index}]", collected, depth + 1, top)

    if isinstance(instance, dict):
        if "minProperties" in schema and len(instance) < int(schema["minProperties"] or 0):
            _add_error(collected, _error(path, "minProperties", schema["minProperties"],
                                        len(instance), "字段数偏少"))
        if "maxProperties" in schema and len(instance) > int(schema["maxProperties"] or 0):
            _add_error(collected, _error(path, "maxProperties", schema["maxProperties"],
                                        len(instance), "字段数偏多"))
        required = schema.get("required")
        if isinstance(required, list):
            for key in required:
                if isinstance(key, str) and key not in instance:
                    _add_error(collected, _error(_join(path, key), "required", "必须存在", None,
                                                "缺少必填字段"))
        properties = schema.get("properties")
        pattern_properties = schema.get("patternProperties")
        known: set[str] = set()
        if isinstance(properties, dict):
            for key, sub in properties.items():
                if not isinstance(key, str):
                    continue
                known.add(key)
                if key in instance:
                    validate_instance(instance[key], sub, _join(path, key),
                                      collected, depth + 1, top)
        if isinstance(pattern_properties, dict):
            for pattern, sub in pattern_properties.items():
                try:
                    matcher = re.compile(str(pattern))
                except re.error as exc:
                    _add_error(collected, _error(path, "patternProperties", pattern, None,
                                                f"正则表达式非法：{exc}"))
                    continue
                for key, value in instance.items():
                    if matcher.search(str(key)):
                        known.add(str(key))
                        validate_instance(value, sub, _join(path, str(key)),
                                          collected, depth + 1, top)
        additional = schema.get("additionalProperties")
        if additional is not None:
            pattern_ok = _compiled_patterns(pattern_properties)
            for key, value in instance.items():
                name = str(key)
                if name in known or any(p.search(name) for p in pattern_ok):
                    continue
                if additional is False:
                    _add_error(collected, _error(_join(path, name), "additionalProperties",
                                                "不允许多余字段", value, "出现了 schema 里没有的字段"))
                elif isinstance(additional, dict):
                    validate_instance(value, additional, _join(path, name),
                                      collected, depth + 1, top)

    for combinator, verb in (
        ("allOf", "全部满足"),
        ("anyOf", "至少满足一项"),
        ("oneOf", "恰好满足一项"),
    ):
        branches = schema.get(combinator)
        if not isinstance(branches, list) or not branches:
            continue
        passed = 0
        first_failure: list[dict[str, Any]] = []
        for branch in branches:
            branch_errors: list[dict[str, Any]] = []
            validate_instance(instance, branch, path, branch_errors, depth + 1, top)
            if branch_errors:
                first_failure = first_failure or branch_errors
            else:
                passed += 1
        needed = len(branches) if combinator == "allOf" else 1
        if passed < needed:
            _add_error(
                collected,
                _error(
                    path, combinator, verb,
                    str(first_failure[0].get("message")) if first_failure else "",
                    f"组合约束未{verb}：{passed}/{len(branches)} 个分支通过",
                ),
            )
        elif combinator == "oneOf" and passed > 1:
            _add_error(collected, _error(path, "oneOf", "恰好一项", f"命中 {passed} 项",
                                        "同时满足多个分支"))

    if "not" in schema:
        forbidden: list[dict[str, Any]] = []
        validate_instance(instance, schema["not"], path, forbidden, depth + 1, top)
        if not forbidden:
            _add_error(collected, _error(path, "not", "不满足该 schema", instance,
                                        "值命中了明确禁止的 schema"))

    return collected


def validate_schema(instance: Any, schema: dict[str, Any]) -> list[dict[str, Any]]:
    """对外的校验入口：返回差异清单（空 = 通过）。"""
    return validate_instance(instance, schema, "$", None, 0, schema)


def format_schema_errors(errors: list[dict[str, Any]], limit: int = 10) -> str:
    """把差异清单拼成一句人能照着改的中文提示。"""
    if not errors:
        return "校验通过"
    head = errors[:limit]
    lines = [f"{item['path']}：{item['message']}" for item in head]
    if len(errors) > len(head):
        lines.append(f"……另有 {len(errors) - len(head)} 处差异")
    return "；".join(lines)


# --------------------------------------------------------------------- 断言库展开


def rule_reference(line: str) -> str:
    """识别 ``lib:AR-0001`` / ``lib:AR-0001#名称`` 写法，返回规则编号；不是引用返回空串。"""
    text = line.strip()
    if not text.lower().startswith(LIBRARY_PREFIX):
        return ""
    body = text[len(LIBRARY_PREFIX):].strip()
    if not body:
        return ""
    return body.split("#", 1)[0].strip()


def expand_rules(
    conn: sqlite3.Connection,
    project_id: int | None,
    module_id: int | None,
    raw_text: str,
) -> list[str]:
    """把用例「断言规则」文本拆成行，并把 ``lib:`` 引用展开成真实规则。

    没挂项目时不允许引用断言库（否则会把别的项目的断言带进来）。
    """
    lines: list[str] = []
    for chunk in str(raw_text or "").replace("；", ";").replace("\r", "\n").split("\n"):
        for piece in chunk.split(";"):
            rule = piece.strip()
            if not rule:
                continue
            ref = rule_reference(rule)
            if not ref:
                lines.append(rule)
                continue
            if not project_id:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    f"引用断言库 {ref} 前必须先确定用例所属项目，请在用例里选择项目后再引用",
                )
            row = AssertionRuleRepository(conn).get_by_no(int(project_id), ref)
            if row is None:
                raise AppError(
                    ErrorCode.RESOURCE_NOT_FOUND,
                    f"断言库里没有编号 {ref} 的规则：请到「执行底座 → 断言库」确认编号，"
                    "或改用项目内已有的规则编号",
                )
            if not int(row.get("enabled") or 0):
                raise AppError(
                    ErrorCode.ILLEGAL_OPERATION,
                    f"断言规则 {ref}（{row.get('name')}）已停用，请重新启用或从断言规则里去掉该引用",
                )
            lines.append(str(row.get("rule") or ""))
    return lines


def validate_rule_text(text: Any) -> str:
    """断言库里存的规则必须能被执行器解析：校验语法并原样返回。"""
    rule = str(text or "").strip()
    if not rule:
        raise AppError(ErrorCode.PARAM_ERROR, "断言规则不能为空，例如 json:$.code = 0")
    lowered = rule.lower()
    if lowered.startswith(LIBRARY_PREFIX):
        raise AppError(ErrorCode.PARAM_ERROR, "断言库里不能再引用断言库，请直接写具体规则")
    if "schema" in lowered.split("=")[0][:12]:
        # schema 类规则存的是 JSON 文本，落库前先解析一遍，坏 schema 不进库
        split = split_schema_rule(rule)
        parse_schema(split[1], field="断言规则的 schema")
    return rule


def split_schema_rule(line: str) -> tuple[str, str]:
    """把 ``schema:$.data = {json}`` 拆成（目标, schema 文本）。"""
    text = line.strip()
    head, separator, tail = ("", "", "")
    for op in (" = ", "=", " 等于 ", " contains "):
        found = text.find(op)
        if found > 0:
            head, separator, tail = text[:found], op, text[found + len(op):]
            break
    if not separator:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "schema 断言要写成「schema:<路径> = <JSON Schema>」，例如 "
            'schema:$ = {"type":"object","required":["code"]}',
        )
    return head.strip(), tail.strip()


def schema_target(head: str) -> str:
    """从 ``schema:$.data`` / ``schema $.data`` 里取 JSONPath 目标，缺省为整份响应。"""
    lowered = head.lower()
    for marker in ("schema:", "schema "):
        if lowered.startswith(marker):
            value = head[len(marker):].strip()
            return value or "$"
    return "$"
