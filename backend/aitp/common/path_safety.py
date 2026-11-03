"""路径安全：把用户输入的相对路径规范化到指定根目录内。

规则：
  - 先把路径统一成 POSIX 分隔符，再用 normpath 消化掉所有 `..`
  - 剥离前导 `/` 与 `../`，杜绝目录穿越
  - 结果必须仍在根目录内（resolve 后 is_relative_to）
  - 隐藏文件（以 . 开头）一律拒绝，避免把 .env / .secret_key 之类的文件发出去
"""

from __future__ import annotations

import posixpath
from pathlib import Path

BLOCKED_SUFFIXES = (".env", ".secret_key", ".db", ".db-wal", ".db-shm", ".py", ".pyc", ".log")


def normalize_relative_path(user_path: str) -> str:
    """把任意输入折叠成安全的相对路径（不含前导 ../）。"""
    raw = (user_path or "").replace("\\", "/")
    # 先补一个根斜杠，normpath 会把 ../ 折叠掉而不是保留
    normalized = posixpath.normpath("/" + raw)
    normalized = normalized.lstrip("/")
    if normalized in ("", "."):
        return ""
    # normpath 之后仍可能残留 ../（理论上不会），再兜一层
    parts = [p for p in normalized.split("/") if p not in ("", ".", "..")]
    return "/".join(parts)


def safe_join(root: Path, user_path: str) -> Path | None:
    """把用户路径安全地拼到 root 下；越界或非法返回 None。"""
    try:
        base = root.resolve()
    except OSError:
        return None

    relative = normalize_relative_path(user_path)
    if not relative:
        return base

    candidate = (base / relative).resolve()
    try:
        if not candidate.is_relative_to(base):
            return None
    except ValueError:  # 跨盘符等极端情况
        return None
    return candidate


def is_blocked_name(relative_path: str) -> bool:
    """拒绝隐藏文件与敏感后缀。"""
    name = (relative_path or "").rsplit("/", 1)[-1]
    if not name:
        return True
    if name.startswith("."):
        return True
    return name.lower().endswith(BLOCKED_SUFFIXES)
