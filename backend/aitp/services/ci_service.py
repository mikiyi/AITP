"""CI 访问边界服务（GAP-24）。

CI 令牌是本项目里唯一绕过本地访问口令的业务入口，所以边界必须收敛：
- 只允许命中白名单里的「CI 触发」路由；
- 令牌本身要过哈希、吊销、有效期三道关；
- 未通过一律显式拒绝（返回既有错误码），绝不静默当普通请求放行。

这里只做「能不能进这扇门」的判定；令牌绑定项目（projectId）的范围校验在
api/ci.py 处理请求体时进行——中间件读不到 JSON 体，项目范围只能交给业务层。
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import ErrorCode
from ..repositories.ci_repo import CiTokenRepository

#: CI 令牌可命中的路由白名单：(HTTP 方法, 路径正则)。
#: 只覆盖真正的 CI 触发接口；新增 CI 路由必须显式登记，否则默认被边界挡住。
CI_ALLOWED_ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("POST", re.compile(r"^/api/v1/ci/runs$")),
    ("GET", re.compile(r"^/api/v1/ci/runs/\d+$")),
)

CI_PREFIX = "/api/v1/ci/"


def is_ci_path(path: str) -> bool:
    """是否落在 CI 前缀下（中间件据此决定是否走令牌边界判定）。"""
    return path.startswith(CI_PREFIX)


def is_ci_route_allowed(method: str, path: str) -> bool:
    """请求是否命中白名单：方法 + 路径都要匹配，避免借前缀越界到别的 CI 路由。"""
    verb = (method or "").upper()
    return any(verb == allowed and pattern.match(path) for allowed, pattern in CI_ALLOWED_ROUTES)


def token_hash_of(raw_token: str) -> str:
    return hashlib.sha256((raw_token or "").encode("utf-8")).hexdigest()


def is_expired(expires_at: Any, now: str) -> bool:
    """expires_at 为空表示不过期（旧令牌迁移后默认如此）；否则按 ISO8601 严格比较。"""
    if not expires_at:
        return False
    return str(expires_at) <= now


@dataclass
class CiAccessResult:
    """一次 CI 边界判定的结论。allowed=True 时 middleware 才放行绕过口令。"""

    allowed: bool
    code: int
    message: str
    row: dict[str, Any] | None = None
    reason: str = ""


def verify_token(conn: sqlite3.Connection, raw_token: str) -> CiAccessResult:
    """哈希 + 吊销 + 有效期三重校验，返回令牌行或拒绝结论。

    只查哈希不查吊销/过期是本轮要消灭的漏洞：enabled=0 或 revoked_at 非空
    或 expires_at 已过，任一命中即判定不可用（AUTH_REQUIRED，即 401 语义）。
    """
    now = utcnow_iso()
    row = CiTokenRepository(conn).verify(token_hash_of(raw_token), now)
    if row is None:
        # verify 只回可用行；这里再按哈希定位一次，区分「不存在」与「被吊销 / 已过期」
        return _diagnose(conn, raw_token, now)
    return CiAccessResult(True, ErrorCode.SUCCESS, "ok", dict(row), "ok")


def _diagnose(conn: sqlite3.Connection, raw_token: str, now: str) -> CiAccessResult:
    repo = CiTokenRepository(conn)
    row = repo.find_by_token_hash(token_hash_of(raw_token))
    if row is None:
        return CiAccessResult(False, ErrorCode.AUTH_REQUIRED, "CI 令牌无效", None, "invalid")
    if not int(row.get("enabled") or 0) or row.get("revoked_at"):
        return CiAccessResult(False, ErrorCode.AUTH_REQUIRED, "CI 令牌已吊销", dict(row), "revoked")
    if is_expired(row.get("expires_at"), now):
        return CiAccessResult(False, ErrorCode.AUTH_REQUIRED, "CI 令牌已过期", dict(row), "expired")
    return CiAccessResult(False, ErrorCode.AUTH_REQUIRED, "CI 令牌无效", dict(row), "invalid")


def evaluate_ci_request(
    conn: sqlite3.Connection,
    method: str,
    path: str,
    raw_token: str,
) -> CiAccessResult:
    """中间件对「带 CI 令牌头的 /api/v1/ci/ 请求」的统一判定入口。

    顺序：先看路径是否越界（越界与令牌是否有效无关，一律拒绝），
    再校验令牌本身。返回 allowed=True 才允许绕过本地访问口令。
    """
    if not is_ci_route_allowed(method, path):
        return CiAccessResult(
            False,
            ErrorCode.ILLEGAL_OPERATION,
            "CI 令牌只能访问 CI 触发接口，请求路径越界",
            None,
            "out_of_scope",
        )
    return verify_token(conn, raw_token)
