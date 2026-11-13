"""读取指定作用域的环境变量。

AI 档案可以选择「从本地环境变量取 Key」：
- Windows：「用户变量」读 HKCU\\Environment，「系统变量」读 HKLM 下的
  Session Manager\\Environment。直接读注册表而不是 os.environ，
  这样设置完变量不用重启服务、也不用重新登录即可生效。
- macOS / Linux：环境变量是进程级的，没有用户/系统之分，两种作用域都读进程环境。

读不到时返回空串，由调用方给出「变量不存在」这类可操作的提示。
"""

from __future__ import annotations

import importlib
import os
from typing import Any

SCOPE_USER = "user"
SCOPE_SYSTEM = "system"
SCOPES = (SCOPE_USER, SCOPE_SYSTEM)

SCOPE_LABELS = {
    SCOPE_USER: "用户变量",
    SCOPE_SYSTEM: "系统变量",
}


def normalize_scope(value: object) -> str:
    scope = str(value or "").strip().lower()
    return scope if scope in SCOPES else SCOPE_USER


def _read_registry(name: str, scope: str) -> str:
    # winreg 只有 Windows 才有：反射导入而不是直接 import，macOS / Linux 上的
    # mypy --strict 才不会把 Windows 专属属性判成「模块无此属性」
    try:
        winreg: Any = importlib.import_module("winreg")
    except ImportError:
        return ""
    if scope == SCOPE_SYSTEM:
        root = winreg.HKEY_LOCAL_MACHINE
        path = r"SYSTEM\CurrentControlSet\Control\Session Manager\Environment"
    else:
        root = winreg.HKEY_CURRENT_USER
        path = r"Environment"
    try:
        with winreg.OpenKey(root, path) as key:
            value, _ = winreg.QueryValueEx(key, name)
    except OSError:
        return ""
    if isinstance(value, str):
        return str(winreg.ExpandEnvironmentStrings(value)).strip()
    return ""


def read_scoped_env(name: str, scope: str) -> str:
    """按作用域读取环境变量；名字为空或不存在时返回空串。"""
    clean = str(name or "").strip()
    if not clean:
        return ""
    if os.name == "nt":
        return _read_registry(clean, normalize_scope(scope))
    return str(os.environ.get(clean) or "").strip()


def scope_label(scope: str) -> str:
    return SCOPE_LABELS.get(normalize_scope(scope), SCOPE_LABELS[SCOPE_USER])
