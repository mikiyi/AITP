"""AI 输出 JSON Schema 校验层（GAP-28）。

只实现 JSON Schema 的一个子集：`required / type / enum / items / 可选嵌套`，
用标准库手写，不引第三方校验框架（清单 GAP-28 的边界）。

严格 / 宽松口径（清单红线：关键字段严格、描述类字段宽松）：

- `strict=True` 的字段：缺失、类型不可转换、枚举不命中、数组不足项 → error，
  所在条目被拦下，不进产出。
- 其余字段一律宽松：能转换就转换、缺了补默认值、白名单外的多余字段裁掉，
  只记 warn，不拦条目——避免「schema 过严把模型逼进反复重试」。

被拦下的条目不静默丢：原文、序号、原因都留在 `SchemaReport.dropped` 里，
界面与门禁都能逐条查到是哪条字段不合。

重试口径：`MAX_SCHEMA_RETRIES = 1`，即一次调用最多因为校验不合格再打一次模型，
重试请求会把具体校验错误回传给模型（见 `services/lab_service.py`）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

# ---- 字段类型（JSON Schema 子集）----
TYPE_STRING = "string"
TYPE_INTEGER = "integer"
TYPE_NUMBER = "number"
TYPE_BOOLEAN = "boolean"
TYPE_OBJECT = "object"
TYPE_ARRAY = "array"
TYPE_ANY = "any"

#: 根结构形态
ROOT_ARRAY = "array"
ROOT_OBJECT = "object"

# ---- 问题码 ----
CODE_MISSING = "missing"
CODE_TYPE = "type"
CODE_ENUM = "enum"
CODE_EMPTY = "empty"
CODE_SHAPE = "shape"
CODE_PARSE = "parse"

LEVEL_ERROR = "error"
LEVEL_WARN = "warn"

#: 校验失败后的自动重试上限（红线：不无限烧 token，最多一次）
MAX_SCHEMA_RETRIES = 1

#: 字符串字段进入产出前的长度上限，超出即截断并留痕
MAX_TEXT_CHARS = 4000

#: 返回体里最多带多少条明细，再多只报总数（一次产出可能上百条）
MAX_ISSUES_IN_PAYLOAD = 20
MAX_DROPPED_IN_PAYLOAD = 20

CASE_TYPES = (
    "functional", "interface", "boundary", "security", "ui", "exception",
    "performance", "compatibility", "usability", "automation",
)
PRIORITIES = ("P0", "P1", "P2", "P3")


@dataclass(frozen=True, slots=True)
class FieldRule:
    """一个字段的校验规则；`strict=False` 即「描述类字段宽松」。"""

    name: str
    type: str = TYPE_STRING
    required: bool = False
    strict: bool = False
    enum: tuple[str, ...] = ()
    default: Any = None
    #: 模型常用的别名字段：按顺序取第一个有值的，归一到 name
    aliases: tuple[str, ...] = ()
    #: type=array 时的元素规则
    item: "FieldRule | None" = None
    #: type=object 时的子字段白名单（也是数组元素为对象时的规则）
    fields: tuple[FieldRule, ...] = ()
    min_items: int = 0
    max_len: int = MAX_TEXT_CHARS
    #: 对象内至少有一个字段非空（用于 steps 元素「操作/预期」二选一）
    require_any_of: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class SchemaSpec:
    """一个能力的输出 schema。"""

    capability: str
    label: str
    root: str
    unit: str
    fields: tuple[FieldRule, ...] = ()
    item: FieldRule | None = None

    @property
    def whitelist(self) -> tuple[str, ...]:
        rules = self.item.fields if self.item is not None else self.fields
        return tuple(rule.name for rule in rules)


@dataclass(slots=True)
class Issue:
    """一条校验结论；`level=error` 会让所在条目被拦下。"""

    path: str
    code: str
    field_name: str
    expected: str
    actual: str
    message: str
    level: str = LEVEL_ERROR

    def as_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "code": self.code,
            "field": self.field_name,
            "expected": self.expected,
            "actual": self.actual,
            "message": self.message,
            "level": self.level,
        }


@dataclass(slots=True)
class DroppedItem:
    """被拦下 / 被丢弃条目的留痕：路径、标题、原因、原文。"""

    path: str
    title: str
    reasons: list[str] = field(default_factory=list)
    raw: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "title": self.title, "reasons": self.reasons, "raw": self.raw}


class _Sink:
    """一次（子）归一过程中累计的中间结果。"""

    __slots__ = ("issues", "dropped", "trimmed", "filled", "clipped")

    def __init__(self) -> None:
        self.issues: list[Issue] = []
        self.dropped: list[DroppedItem] = []
        self.trimmed: list[str] = []
        self.filled: list[str] = []
        self.clipped: list[str] = []

    def error(self, path: str, code: str, name: str, expected: str, actual: str, msg: str) -> None:
        self.issues.append(Issue(path, code, name, expected, actual, msg, LEVEL_ERROR))

    def warn(self, path: str, code: str, name: str, expected: str, actual: str, msg: str) -> None:
        self.issues.append(Issue(path, code, name, expected, actual, msg, LEVEL_WARN))

    @property
    def has_error(self) -> bool:
        return any(issue.level == LEVEL_ERROR for issue in self.issues)

    def errors_here(self) -> list[str]:
        return [issue.message for issue in self.issues if issue.level == LEVEL_ERROR]

    def merge(self, other: "_Sink") -> None:
        self.issues.extend(other.issues)
        self.dropped.extend(other.dropped)
        self.trimmed.extend(other.trimmed)
        self.filled.extend(other.filled)
        self.clipped.extend(other.clipped)


@dataclass(slots=True)
class SchemaReport:
    """一次输出校验的完整结论；界面、门禁、落库都读它。"""

    capability: str
    label: str
    root: str
    parsed: bool
    ok: bool
    total: int = 0
    passed: int = 0
    rejected: int = 0
    retried: int = 0
    value: Any = None
    issues: list[Issue] = field(default_factory=list)
    dropped: list[DroppedItem] = field(default_factory=list)
    trimmed: list[str] = field(default_factory=list)
    filled: list[str] = field(default_factory=list)
    clipped: list[str] = field(default_factory=list)
    message: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "capability": self.capability,
            "schemaLabel": self.label,
            "root": self.root,
            "parsed": self.parsed,
            "ok": self.ok,
            "total": self.total,
            "passed": self.passed,
            "rejected": self.rejected,
            "retried": self.retried,
            "retryLimit": MAX_SCHEMA_RETRIES,
            "message": self.message,
            "issues": [issue.as_dict() for issue in self.issues[:MAX_ISSUES_IN_PAYLOAD]],
            "issueCount": len(self.issues),
            "dropped": [item.as_dict() for item in self.dropped[:MAX_DROPPED_IN_PAYLOAD]],
            "droppedCount": len(self.dropped),
            "trimmedFields": self.trimmed[:MAX_DROPPED_IN_PAYLOAD],
            "filledDefaults": self.filled[:MAX_DROPPED_IN_PAYLOAD],
            "clippedFields": self.clipped[:MAX_DROPPED_IN_PAYLOAD],
        }


# ---------------- 能力 schema 定义 ----------------

_STEP_RULE = FieldRule(
    name="step",
    type=TYPE_OBJECT,
    fields=(
        FieldRule("action", TYPE_STRING, aliases=("step", "operation", "操作"), max_len=1000),
        FieldRule("expected", TYPE_STRING,
                  aliases=("expect", "expectedResult", "result", "预期"), max_len=1000),
    ),
    require_any_of=("action", "expected"),
)

CASE_ITEM = FieldRule(
    name="case",
    type=TYPE_OBJECT,
    fields=(
        FieldRule("title", TYPE_STRING, required=True, strict=True, max_len=200),
        FieldRule("caseType", TYPE_STRING, enum=CASE_TYPES, default="functional",
                  aliases=("type", "kind")),
        FieldRule("priority", TYPE_STRING, enum=PRIORITIES, default="P2"),
        FieldRule("preconditions", TYPE_STRING, default="无", aliases=("precondition",),
                  max_len=2000),
        FieldRule("steps", TYPE_ARRAY, required=True, strict=True, min_items=1,
                  item=_STEP_RULE, default=[]),
        FieldRule("expectedResult", TYPE_STRING, default="",
                  aliases=("expected", "verifyTarget"), max_len=2000),
        FieldRule("tags", TYPE_STRING, default="", aliases=("module", "category"), max_len=200),
    ),
)

_REGRESSION_ITEM = FieldRule(
    name="regression",
    type=TYPE_OBJECT,
    fields=(
        FieldRule("title", TYPE_STRING, required=True, strict=True, max_len=200),
        FieldRule("module", TYPE_STRING, default="", aliases=("path",), max_len=200),
        FieldRule("priority", TYPE_STRING, enum=PRIORITIES, default="P1"),
        FieldRule("reason", TYPE_STRING, default="", aliases=("cause",), max_len=2000),
        FieldRule("verifyTarget", TYPE_STRING, default="", aliases=("verify",), max_len=2000),
    ),
)

def _tp_node(depth: int) -> FieldRule:
    """测试点节点规则：脑图最深三层，用有限递归展开而不是自引用。"""
    leaf = depth <= 0
    children: tuple[FieldRule, ...] = ()
    if not leaf:
        children = (FieldRule("children", TYPE_ARRAY, default=[], item=_tp_node(depth - 1)),)
    return FieldRule(
        name="node",
        type=TYPE_OBJECT,
        fields=(
            FieldRule("name", TYPE_STRING, required=True, strict=True, aliases=("title",),
                      max_len=200),
            FieldRule("priority", TYPE_STRING, enum=PRIORITIES, default=""),
            FieldRule("desc", TYPE_STRING, default="", aliases=("description", "note"),
                      max_len=1000),
        ) + children,
    )


#: 给 5 层余量：模板要求最深三层，多给两层避免深一点的树被当成多余字段裁掉
_TP_NODE = _tp_node(5)

SCHEMAS: dict[str, SchemaSpec] = {
    "case_generation": SchemaSpec("case_generation", "测试用例", ROOT_ARRAY, "用例", item=CASE_ITEM),
    "xmind_to_case": SchemaSpec(
        "xmind_to_case", "测试用例（脑图转换）", ROOT_ARRAY, "用例", item=CASE_ITEM,
    ),
    "regression_generation": SchemaSpec(
        "regression_generation", "回归清单", ROOT_ARRAY, "回归点", item=_REGRESSION_ITEM,
    ),
    "testpoint_generation": SchemaSpec(
        "testpoint_generation", "测试点脑图", ROOT_OBJECT, "节点",
        fields=(
            FieldRule("name", TYPE_STRING, required=True, strict=True, aliases=("title",),
                      max_len=200),
            FieldRule("children", TYPE_ARRAY, default=[], item=_TP_NODE),
        ),
    ),
}


def schema_of(capability: str) -> SchemaSpec | None:
    return SCHEMAS.get(capability)


def has_schema(capability: str) -> bool:
    return capability in SCHEMAS


def schema_capabilities() -> tuple[str, ...]:
    """已定义 schema 的能力（门禁按它逐个覆盖）。"""
    return tuple(SCHEMAS.keys())


# ---------------- 归一辅助 ----------------

class _Invalid:
    """占位：本值归一失败；错误级别由调用方的 strict 策略决定。"""

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return "<invalid>"


_INVALID = _Invalid()
_UNSET = object()


def _is_blank(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict, tuple)):
        return len(value) == 0
    return False


def _preview(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, str):
        text = value.strip().replace("\n", " ")
        return text[:40] + ("…" if len(text) > 40 else "")
    if isinstance(value, (list, tuple)):
        return f"array({len(value)})"
    if isinstance(value, dict):
        return f"object({len(value)})"
    return str(value)[:40]


def _fallback_of(rule: FieldRule) -> Any:
    if rule.default is not None:
        return rule.default
    if rule.type == TYPE_ARRAY:
        return []
    if rule.type == TYPE_OBJECT:
        return {}
    if rule.type in (TYPE_INTEGER, TYPE_NUMBER):
        return 0
    if rule.type == TYPE_BOOLEAN:
        return False
    if rule.type == TYPE_ANY:
        return None
    return ""


def _expectation(rule: FieldRule) -> str:
    parts = [rule.type]
    if rule.enum:
        parts.append("取值必须是 " + "|".join(rule.enum))
    if rule.min_items:
        parts.append(f"至少 {rule.min_items} 项")
    if rule.required:
        parts.append("必填")
    return "，".join(parts)


def _coerce_scalar(rule: FieldRule, value: Any) -> tuple[Any, str]:
    """把标量按目标类型转换；转不动返回 (None, 原因)。"""
    if rule.type in (TYPE_STRING, TYPE_ANY):
        if isinstance(value, str):
            return value, ""
        if isinstance(value, bool):
            return ("true" if value else "false"), ""
        if isinstance(value, (int, float)):
            return str(value), ""
        if value is None:
            return "", ""
        return json.dumps(value, ensure_ascii=False), ""
    if rule.type == TYPE_BOOLEAN:
        if isinstance(value, bool):
            return value, ""
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in ("true", "1", "yes", "是"):
                return True, ""
            if lowered in ("false", "0", "no", "否"):
                return False, ""
        if isinstance(value, (int, float)):
            return bool(value), ""
        return None, f"不是布尔值（{_preview(value)}）"
    if rule.type in (TYPE_INTEGER, TYPE_NUMBER):
        number: float | None = None
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            number = float(value)
        elif isinstance(value, str):
            try:
                number = float(value.strip())
            except ValueError:
                number = None
        if number is None:
            return None, f"不是数字（{_preview(value)}）"
        return (int(number), "") if rule.type == TYPE_INTEGER else (number, "")
    return value, ""


def _fail(rule: FieldRule, value: Any, path: str, sink: _Sink, reason: str) -> Any:
    """同一处不合，strict 记 error（拦条目），宽松记 warn（不拦）。"""
    if rule.strict or (rule.required and rule.type in (TYPE_ARRAY, TYPE_OBJECT)):
        sink.error(path, CODE_TYPE, rule.name, _expectation(rule), _preview(value),
                   f"「{path}」不合规：{reason}")
    else:
        sink.warn(path, CODE_TYPE, rule.name, _expectation(rule), _preview(value),
                  f"「{path}」{reason}，已按宽松策略处理")
    return _INVALID


def _title_of(item: Any) -> str:
    if isinstance(item, dict):
        for key in ("title", "name", "module"):
            value = item.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()[:80]
    return ""


def _dump(value: Any) -> str:
    try:
        return json.dumps(value, ensure_ascii=False)[:1500]
    except (TypeError, ValueError):
        return str(value)[:1500]


# ---------------- 结构化归一 ----------------

def _norm_fields(
    rules: tuple[FieldRule, ...], data: dict[str, Any], path: str, sink: _Sink
) -> dict[str, Any]:
    """白名单归一：多余字段裁掉、别名归一、缺失按 strict/宽松分别处理。"""
    known = set()
    for rule in rules:
        known.add(rule.name)
        known.update(rule.aliases)
    for name in data:
        if name not in known:
            sink.trimmed.append(f"{path}.{name}" if path else name)

    out: dict[str, Any] = {}
    for rule in rules:
        child_path = f"{path}.{rule.name}" if path else rule.name
        value: Any = _UNSET
        came_from = rule.name
        if rule.name in data:
            value = data[rule.name]
        else:
            for alias in rule.aliases:
                if alias in data and not _is_blank(data[alias]):
                    value = data[alias]
                    came_from = alias
                    break
        if value is _UNSET or value is None:
            if rule.required and rule.strict and _is_blank(rule.default):
                sink.error(child_path, CODE_MISSING, rule.name, _expectation(rule),
                           "字段不存在" if value is _UNSET else "字段为空",
                           f"「{child_path}」缺失或为空（关键字段，必填）")
                out[rule.name] = _fallback_of(rule)
                continue
            default = _fallback_of(rule)
            if not _is_blank(default):
                sink.filled.append(child_path)
                sink.warn(child_path, CODE_MISSING, rule.name, "缺省即可", "字段缺失",
                          f"「{child_path}」缺失，已补默认值 {_preview(default)}")
            out[rule.name] = default
            continue

        normalized = _norm_value(rule, value, child_path, sink)
        if isinstance(normalized, _Invalid):
            out[rule.name] = _fallback_of(rule)
        else:
            out[rule.name] = normalized
        if came_from != rule.name:
            sink.filled.append(f"{child_path}←{came_from}")
    return out


def _norm_object(rule: FieldRule, data: dict[str, Any], path: str, sink: _Sink) -> None:
    """对象内「至少一个字段非空」的硬约束；不满足记 error。"""
    if not rule.require_any_of:
        return
    for name in rule.require_any_of:
        if not _is_blank(data.get(name)):
            return
    expected = "、".join(rule.require_any_of)
    sink.error(path, CODE_EMPTY, rule.name, f"{expected} 至少一个非空",
               "全部为空", f"「{path}」没有内容：{expected} 至少要有一个")


def _norm_value(rule: FieldRule, value: Any, path: str, sink: _Sink) -> Any:
    if rule.type == TYPE_OBJECT:
        if not isinstance(value, dict):
            return _fail(rule, value, path, sink, "应为对象")
        out = _norm_fields(rule.fields, value, path, sink)
        _norm_object(rule, out, path, sink)
        return out

    if rule.type == TYPE_ARRAY:
        raw = value
        if isinstance(value, dict) and len(value) == 1:
            only = next(iter(value.values()))
            if isinstance(only, list):
                sink.warn(path, CODE_SHAPE, rule.name, "数组", "被包在对象里",
                          f"「{path}」被包在对象里，已展开成数组")
                raw = only
        if not isinstance(raw, list):
            return _fail(rule, value, path, sink, "应为数组")
        items: list[Any] = []
        for index, element in enumerate(raw):
            element_path = f"{path}[{index}]"
            item_rule = rule.item
            if item_rule is None:
                items.append(element)
                continue
            if item_rule.type == TYPE_OBJECT:
                if not isinstance(element, dict):
                    if _is_blank(element):
                        sink.dropped.append(DroppedItem(element_path, "", ["空条目"], _dump(element)))
                        continue
                    sink.error(element_path, CODE_TYPE, item_rule.name, "对象",
                               _preview(element), f"「{element_path}」不是对象")
                    continue
                child = _Sink()
                normalized = _norm_fields(item_rule.fields, element, element_path, child)
                _norm_object(item_rule, normalized, element_path, child)
                sink.merge(child)
                if child.has_error:
                    sink.dropped.append(DroppedItem(
                        element_path, _title_of(element), child.errors_here(), _dump(element)))
                    continue
                items.append(normalized)
                continue
            normalized_item = _norm_value(item_rule, element, element_path, sink)
            if not isinstance(normalized_item, _Invalid):
                items.append(normalized_item)
        if len(items) < rule.min_items:
            return _fail(rule, raw, path, sink,
                         f"至少 {rule.min_items} 项，实际只剩 {len(items)} 项")
        return items

    coerced, reason = _coerce_scalar(rule, value)
    if coerced is None and reason:
        return _fail(rule, value, path, sink, reason)
    if isinstance(coerced, str):
        if rule.enum:
            candidate = coerced.strip()
            lookup = {item.lower(): item for item in rule.enum}
            fixed = lookup.get(candidate.lower())
            if candidate and fixed is None:
                if rule.strict:
                    return _fail(rule, value, path, sink,
                                 f"取值「{candidate}」不在允许列表：{'|'.join(rule.enum)}")
                sink.warn(path, CODE_ENUM, rule.name, "|".join(rule.enum), candidate,
                          f"「{path}」取值「{candidate}」不在列表，已回落到默认值")
                return _INVALID
                # 调用方按 _INVALID 补默认值，并已在上面记了 warn
            if fixed is not None and fixed != candidate:
                coerced = fixed
                sink.filled.append(path)
        limit = rule.max_len or MAX_TEXT_CHARS
        if len(coerced) > limit:
            sink.clipped.append(path)
            coerced = coerced[:limit].rstrip() + "…"
    return coerced


# ---------------- 对外入口 ----------------

def validate_output(capability: str, value: Any) -> SchemaReport:
    """解析后的模型输出 → 校验 + 规范化。

    `value` 是 `lab_service.extract_json` 的结果；None 表示连 JSON 都没解析出来。
    """
    spec = schema_of(capability)
    if spec is None:
        report = SchemaReport(
            capability=capability, label="", root="", parsed=value is not None,
            ok=value is not None, value=value,
            message="该能力未定义输出 schema，本次只做 JSON 解析"
            if value is not None else "输出不是合法 JSON",
        )
        return report
    if value is None:
        sink = _Sink()
        sink.error("", CODE_PARSE, "根节点", "可解析的 JSON", "解析失败",
                   "模型输出里找不到可解析的 JSON，本次没有采纳任何条目")
        return SchemaReport(
            capability=capability, label=spec.label, root=spec.root, parsed=False, ok=False,
            issues=sink.issues, message="输出不是合法 JSON，已保留原文",
        )
    if spec.root == ROOT_ARRAY:
        return _validate_array(spec, value)
    return _validate_object(spec, value)


def _validate_array(spec: SchemaSpec, value: Any) -> SchemaReport:
    sink = _Sink()
    item_rule = spec.item
    if item_rule is None:  # pragma: no cover - schema 定义错误，启动即该发现
        sink.error("", CODE_SHAPE, "根节点", "条目规则", "缺失", "schema 未定义条目规则")
        return SchemaReport(spec.capability, spec.label, spec.root, True, False,
                            issues=sink.issues, message="schema 未定义条目规则")

    data = value
    if not isinstance(data, list):
        if isinstance(data, dict):
            sink.warn("", CODE_SHAPE, "根节点", "JSON 数组", "单个对象",
                      "输出是单个对象，已按一条处理")
            data = [data]
        else:
            sink.error("", CODE_SHAPE, "根节点", "JSON 数组", _preview(value),
                        f"输出应为 JSON 数组，实际是 {type(value).__name__}")
            return SchemaReport(spec.capability, spec.label, spec.root, True, False,
                                issues=sink.issues, message="输出结构不是数组，未采纳任何条目")

    items: list[Any] = []
    for index, element in enumerate(data):
        path = f"[{index}]"
        if not isinstance(element, dict):
            sink.error(path, CODE_TYPE, "条目", "对象", _preview(element),
                       f"第 {index + 1} 条不是对象，已拦下")
            sink.dropped.append(DroppedItem(path, "", ["条目不是对象"], _dump(element)))
            continue
        child = _Sink()
        normalized = _norm_fields(item_rule.fields, element, path, child)
        _norm_object(item_rule, normalized, path, child)
        sink.merge(child)
        if child.has_error:
            sink.dropped.append(DroppedItem(
                path, _title_of(element), child.errors_here() or ["关键字段不合规"],
                _dump(element)))
            continue
        items.append(normalized)

    total = len(data)
    passed = len(items)
    ok = passed > 0
    return SchemaReport(
        capability=spec.capability, label=spec.label, root=spec.root, parsed=True, ok=ok,
        total=total, passed=passed, rejected=total - passed, value=items,
        issues=sink.issues, dropped=sink.dropped,
        trimmed=list(dict.fromkeys(sink.trimmed)),
        filled=list(dict.fromkeys(sink.filled)),
        clipped=list(dict.fromkeys(sink.clipped)),
        message=_summary(total, passed, total - passed, ok, spec.unit),
    )


def _validate_object(spec: SchemaSpec, value: Any) -> SchemaReport:
    sink = _Sink()
    if not isinstance(value, dict):
        sink.error("", CODE_SHAPE, "根节点", "JSON 对象", _preview(value),
                   f"输出应为 JSON 对象，实际是 {type(value).__name__}")
        return SchemaReport(spec.capability, spec.label, spec.root, True, False,
                            issues=sink.issues, message="输出结构不是对象，未采纳任何条目")
    normalized = _norm_fields(spec.fields, value, "", sink)
    nodes = _count_nodes(normalized)
    dropped_nodes = len(sink.dropped)
    # 只有根层字段（name / children）不合规才判整个产出失败；
    # 被摘掉的子节点已经进了 dropped 清单，不该反过来判树无效
    bad_root = _has_root_error(sink)
    ok = not bad_root and nodes > 0
    message = _summary(nodes + dropped_nodes, nodes, dropped_nodes, ok, spec.unit)
    if bad_root:
        message = f"根节点关键字段不合规：{bad_root[0]}"
    return SchemaReport(
        capability=spec.capability, label=spec.label, root=spec.root, parsed=True, ok=ok,
        total=nodes + dropped_nodes if not bad_root else 1,
        passed=nodes if ok else 0,
        rejected=dropped_nodes if ok else 1,
        value=normalized if ok else None,
        issues=sink.issues, dropped=sink.dropped,
        trimmed=list(dict.fromkeys(sink.trimmed)),
        filled=list(dict.fromkeys(sink.filled)),
        clipped=list(dict.fromkeys(sink.clipped)),
        message=message,
    )


def _has_root_error(sink: _Sink) -> list[str]:
    """根层字段错误（路径不带下标）；子节点错误不算。"""
    return [
        issue.message for issue in sink.issues
        if issue.level == LEVEL_ERROR and "[" not in issue.path
    ]


def _count_nodes(data: Any) -> int:
    if not isinstance(data, dict):
        return 0
    total = 1
    for child in data.get("children") or []:
        total += _count_nodes(child)
    return total


def _summary(total: int, passed: int, rejected: int, ok: bool, unit: str) -> str:
    if total == 0:
        return f"模型没有给出任何{unit}"
    if not ok:
        return f"{total} 条{unit}都不满足关键字段要求，已全部拦下"
    tail = f"；{rejected} 条{unit}被拦下" if rejected else ""
    return f"{passed} 条{unit}通过校验{tail}"


def retry_feedback(report: SchemaReport) -> str:
    """把校验错误写成给模型看的修正指令（重试请求里的 user 消息）。"""
    lines = ["上一次输出未通过本机字段校验，请修正后重新输出，只输出修正后的 JSON："]
    for issue in report.issues[:12]:
        if issue.level != LEVEL_ERROR:
            continue
        lines.append(f"- {issue.path or '根节点'}：{issue.message}（期望 {issue.expected}）")
    for item in report.dropped[:8]:
        if item.reasons:
            lines.append(
                f"- 已丢弃 {item.path}（{item.title or '无标题'}）：" + "；".join(item.reasons[:3])
            )
    if not any(line.startswith("- ") for line in lines):
        lines.append(f"- {report.message or '字段不合规'}")
    lines.append("要求：保持原有输出结构；关键字段不得省略；不要输出解释文字或代码块围栏。")
    return "\n".join(lines)
