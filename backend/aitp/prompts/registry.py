"""提示词注册表：按 code 加载 markdown 模板并渲染变量。

约定：
- 提示词文件放在 AITP_PROMPTS_DIR（默认 backend/prompts），文件名即 code
- 文件开头可选 YAML frontmatter（title / description）
- 正文里的 `{{变量名}}` 会被替换；未提供的变量直接报错，不留残缺模板
"""

from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..errors import AppError, ErrorCode

#: 只把「合法标识符」当变量，避免把 JSON 示例里的 {{ }} 误判
_VAR_RE = re.compile(r"\{\{\s*([A-Za-z_][A-Za-z0-9_]*)\s*\}\}")
_FRONTMATTER_RE = re.compile(r"\A---\s*\n(.*?)\n---\s*\n", re.DOTALL)
_CODE_RE = re.compile(r"[a-z0-9][a-z0-9-]{0,60}")

#: 可选变量缺省时的占位文案，避免提示词里出现空段落
OPTIONAL_PLACEHOLDER = "（未提供）"


@dataclass(frozen=True, slots=True)
class PromptTemplate:
    code: str
    path: Path
    title: str
    description: str
    body: str
    variables: tuple[str, ...]
    optional: tuple[str, ...]
    mtime: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "title": self.title,
            "description": self.description,
            "variables": list(self.variables),
            "optional": list(self.optional),
            "path": str(self.path),
            "mtime": self.mtime,
        }


class PromptRegistry:
    """按 code 取提示词模板，带 mtime 缓存。"""

    def __init__(self, root: Path | None = None) -> None:
        self._root = root
        self._cache: dict[str, PromptTemplate] = {}
        self._lock = threading.Lock()

    @property
    def root(self) -> Path:
        if self._root is not None:
            return self._root
        from flask import current_app

        return Path(current_app.config["AITP_PROMPTS_DIR"])

    def path_of(self, code: str) -> Path:
        if not _CODE_RE.fullmatch(code or ""):
            raise AppError(ErrorCode.PROMPT_NOT_FOUND, f"提示词编号非法：{code}")
        return self.root / f"{code}.md"

    def get(self, code: str) -> PromptTemplate:
        path = self.path_of(code)
        try:
            mtime = path.stat().st_mtime
        except OSError:
            raise AppError(ErrorCode.PROMPT_NOT_FOUND, f"提示词不存在：{code}") from None

        cached = self._cache.get(code)
        if cached is not None and cached.mtime == mtime:
            return cached

        template = self._load(code, path, mtime)
        with self._lock:
            self._cache[code] = template
        return template

    def render(self, code: str, variables: dict[str, Any] | None = None) -> str:
        template = self.get(code)
        values = variables or {}
        required = [name for name in template.variables if name not in template.optional]
        missing = [
            name for name in required
            if not str(values.get(name, "") or "").strip()
        ]
        if missing:
            raise AppError(
                ErrorCode.PROMPT_VAR_MISSING,
                f"提示词 {code} 缺少变量：{'、'.join(missing)}",
                {"missing": missing, "code": code},
            )
        rendered = _VAR_RE.sub(lambda m: str(values.get(m.group(1), "")), template.body)
        leftover = _VAR_RE.search(rendered)
        if leftover:
            raise AppError(
                ErrorCode.PROMPT_VAR_MISSING,
                f"提示词 {code} 变量未替换：{leftover.group(1)}",
                {"missing": [leftover.group(1)], "code": code},
            )
        return rendered.strip()

    def fill_defaults(self, code: str, variables: dict[str, Any] | None = None) -> dict[str, Any]:
        """补齐缺失的可选变量，返回可直接渲染的变量表。"""
        template = self.get(code)
        values = dict(variables or {})
        for name in template.optional:
            if not str(values.get(name, "") or "").strip():
                values[name] = OPTIONAL_PLACEHOLDER
        return values

    def list_templates(self) -> list[PromptTemplate]:
        root = self.root
        if not root.is_dir():
            return []
        result: list[PromptTemplate] = []
        for path in sorted(root.glob("*.md")):
            code = path.stem
            if not _CODE_RE.fullmatch(code):
                continue
            try:
                result.append(self.get(code))
            except AppError:
                continue
        return result

    def _load(self, code: str, path: Path, mtime: float) -> PromptTemplate:
        try:
            raw = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AppError(ErrorCode.PROMPT_NOT_FOUND, f"提示词读取失败：{code}") from exc

        title = ""
        description = ""
        optional: tuple[str, ...] = ()
        match = _FRONTMATTER_RE.match(raw)
        if match:
            for line in match.group(1).splitlines():
                if ":" not in line:
                    continue
                key, _, value = line.partition(":")
                key = key.strip().lower()
                value = value.strip().strip('"').strip("'")
                if key in ("title", "name"):
                    title = value
                elif key == "description":
                    description = value
                elif key == "optional":
                    optional = tuple(
                        item.strip() for item in value.replace("，", ",").split(",") if item.strip()
                    )
            raw = raw[match.end():]

        body = raw.strip()
        variables = tuple(dict.fromkeys(_VAR_RE.findall(body)))
        return PromptTemplate(
            code=code,
            path=path,
            title=title or code,
            description=description,
            body=body,
            variables=variables,
            optional=tuple(name for name in optional if name in variables),
            mtime=mtime,
        )


_registry: PromptRegistry | None = None
_registry_lock = threading.Lock()


def get_registry() -> PromptRegistry:
    global _registry
    if _registry is None:
        with _registry_lock:
            if _registry is None:
                _registry = PromptRegistry()
    return _registry


def render_prompt(code: str, variables: dict[str, Any] | None = None) -> str:
    return get_registry().render(code, variables)


def list_templates() -> list[dict[str, Any]]:
    return [item.as_dict() for item in get_registry().list_templates()]
