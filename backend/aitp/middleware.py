"""请求中间件：traceId 生成与回显、访问日志、本机同源防护、访问口令与请求体上限。

AITP 默认只监听 127.0.0.1。这里补上浏览器侧的防线：
Host 校验挡住 DNS rebinding，Origin/Referer/Sec-Fetch-Site 校验挡住恶意网页
对本机写接口的盲打（POST/PATCH 即使读不到响应，动作照样会执行）。
启用本地访问口令后，除健康检查与登录/状态外的 /api 接口都要求有效会话；
口令挡的是「借用本机浏览器的人」，不挡本机进程与文件系统。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, current_app, g, request

from .envelope import fail
from .errors import ErrorCode
from .db.connection import get_db
from .services import access_service, audit_service, ci_service

_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
_UNSAFE_METHODS = frozenset({"POST", "PATCH", "PUT", "DELETE"})
#: 通配监听时无法再靠 Host 名判断边界，交给显式开关自己负责
_ANY_HOST = frozenset({"0.0.0.0", "::", "*"})
#: 未登录也必须能访问：健康检查用于前端状态条，登录/状态用于渲染登录页
_AUTH_OPEN_PATHS = frozenset({
    "/api/v1/health",
    "/api/v1/auth/status",
    "/api/v1/auth/login",
})
#: CI 路由由令牌自行鉴权；带令牌头时先经 ci_service 边界判定（白名单+哈希+有效期+吊销），
#: 全部通过才绕过访问口令，否则显式拒绝。GAP-24 收紧，不再是「带令牌头即放行」。
_CI_PREFIX = "/api/v1/ci/"

#: 附件本身限 32MB，这里留出 multipart 余量
DEFAULT_BODY_LIMIT = 40 * 1024 * 1024
#: 备份上传是唯一的大体积入口，单独放宽；取两者最大值作为全局兜底
BODY_LIMIT_OVERRIDES = {"/api/v1/settings/backups/upload": 210 * 1024 * 1024}
GLOBAL_BODY_LIMIT = max(DEFAULT_BODY_LIMIT, *BODY_LIMIT_OVERRIDES.values())


def _host_of(value: str) -> str:
    """从 Origin/Referer/Host 里取主机名，忽略端口与 IPv6 中括号。"""
    if not value:
        return ""
    parts = urlsplit(value if "//" in value else f"//{value}")
    return (parts.hostname or "").lower()


def _netloc_of(value: str) -> str:
    parts = urlsplit(value if "//" in value else f"//{value}")
    return (parts.netloc or "").lower()


def _allowed_hosts() -> frozenset[str] | None:
    cfg_host = str(current_app.config.get("AITP_HOST", "127.0.0.1")).lower()
    if cfg_host in _ANY_HOST:
        return None
    return _LOOPBACK_HOSTS | {cfg_host}


def register_middleware(app: Flask) -> None:
    app.config.setdefault("MAX_CONTENT_LENGTH", GLOBAL_BODY_LIMIT)

    @app.before_request
    def _before() -> Response | None:
        g.trace_id = request.headers.get("X-Trace-Id") or uuid.uuid4().hex
        g.start_ts = time.perf_counter()
        if not request.path.startswith("/api/"):
            return None
        return _guard()

    @app.after_request
    def _after(resp: Response) -> Response:
        resp.headers["X-Trace-Id"] = getattr(g, "trace_id", "")
        start = getattr(g, "start_ts", None)
        if start is not None:
            resp.headers["X-Response-Time-Ms"] = str(int((time.perf_counter() - start) * 1000))
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


def _guard() -> Response | None:
    limit = BODY_LIMIT_OVERRIDES.get(request.path, DEFAULT_BODY_LIMIT)
    size = request.content_length
    if size is not None and size > limit:
        return fail(ErrorCode.BODY_TOO_LARGE, f"请求体过大，上限 {limit // 1024 // 1024}MB")

    allowed = _allowed_hosts()
    if allowed is not None and _host_of(request.host) not in allowed:
        return fail(ErrorCode.CROSS_SITE_BLOCKED, "非法的 Host，请求已被拒绝")

    if request.method in _UNSAFE_METHODS and not _same_site():
        return fail(ErrorCode.CROSS_SITE_BLOCKED, "跨站请求已被拒绝：AITP 只接受本机页面的写入")

    token = request.headers.get("X-AITP-CI-Token")
    if token and request.path.startswith(_CI_PREFIX):
        # 边界收紧：只有「路由白名单 + 哈希 + 有效期 + 吊销」全过，才允许绕过访问口令；
        # 否则按 401/越界语义显式拒绝，不再静默当普通请求放行。
        return _ci_boundary(token)

    if request.path not in _AUTH_OPEN_PATHS and not _access_allowed():
        return fail(ErrorCode.AUTH_REQUIRED, "需要访问口令，请先登录")
    return None


def _ci_boundary(token: str) -> Response | None:
    """CI 令牌访问边界判定；放行返回 None，拒绝返回错误包封并留一条审计。"""
    conn = get_db()
    result = ci_service.evaluate_ci_request(conn, request.method, request.path, token)
    if result.allowed:
        return None
    try:
        audit_service.record(
            conn,
            audit_service.AUDIT_ACTION_EXECUTE,
            audit_service.RESOURCE_CI_TOKEN,
            None,
            str((result.row or {}).get("name") or "") or "ci-denied",
            {"source": "ci", "outcome": "denied", "reason": result.reason,
             "path": request.path, "method": request.method},
            actor="ci:denied",
            result="denied",
        )
        conn.commit()
    except Exception:  # noqa: BLE001 - 审计失败不能改变拒绝结论本身
        conn.rollback()
    return fail(result.code, result.message)


def _access_token() -> str:
    header = request.headers.get("Authorization", "")
    if header.lower().startswith("bearer "):
        return header[7:].strip()
    return request.cookies.get(access_service.COOKIE_NAME, "")


def _access_allowed() -> bool:
    """未启用口令时全部放行；启用后必须带有效会话（Cookie 或 Bearer）。"""
    data_dir = Path(str(current_app.config.get("AITP_DB_PATH", ""))).parent
    if not access_service.is_enabled(data_dir):
        return True
    return access_service.is_valid(_access_token())


def _same_site() -> bool:
    """浏览器发出的信号优先；没有信号时退回 Origin/Referer 比对。"""
    if request.headers.get("Sec-Fetch-Site", "").lower() == "cross-site":
        return False
    source = request.headers.get("Origin", "") or request.headers.get("Referer", "")
    if not source:
        # 命令行与自动化测试不带这两个头：本机工具不把它们当必要条件
        return True
    allowed = _allowed_hosts()
    if allowed is not None and _host_of(source) in allowed:
        # 本机页面的地址未必等于接口地址：前端 dev 代理会让页面留在 5180、请求转给后端端口，
        # 逐端口比对会把正常开发环境拦死，所以这里只认主机名。
        return True
    return _netloc_of(source) == (request.host or "").lower()
