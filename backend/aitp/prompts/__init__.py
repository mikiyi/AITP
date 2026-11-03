"""提示词加载与渲染。

提示词以 markdown 文件存放（默认 backend/prompts），文件名即 code。
按 mtime 缓存，改完文件立即生效，不用重启服务。
"""

from __future__ import annotations

from .registry import (
    PromptRegistry,
    PromptTemplate,
    get_registry,
    list_templates,
    render_prompt,
)

__all__ = [
    "PromptRegistry",
    "PromptTemplate",
    "get_registry",
    "list_templates",
    "render_prompt",
]
