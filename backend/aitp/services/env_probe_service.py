"""测试环境健康探测服务。

只做本机发起的可达性检查，绝不把环境信息发给任何大模型：
  - 应用服务器：HTTP GET 健康路径，看状态码与响应耗时
  - 数据库服务器：TCP 端口握手，看能否连上、握手耗时
  - 资源指标：本机目标直接采本机 CPU/内存/磁盘；远程目标读可选监控地址
凭据只在需要登录验证时由调用方在服务端取用，探测本身不回显任何明文。
"""

from __future__ import annotations

import socket
import time
import urllib.error
import urllib.request
from typing import Any

from ..repositories.test_env_repo import DB_DEFAULT_PORTS
from . import server_metrics_service

#: 单项探测的超时秒数；本机探测不该久等
PROBE_TIMEOUT = 5.0


def _probe_http(base_url: str, health_path: str) -> dict[str, Any]:
    """应用服务器 HTTP 健康探测：GET base_url + health_path。"""
    base = base_url.strip().rstrip("/")
    path = health_path.strip()
    if not base:
        return {"ok": False, "status": "unconfigured", "message": "未配置应用服务器地址"}
    url = f"{base}{path if path.startswith('/') else '/' + path}"
    started = time.monotonic()
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=PROBE_TIMEOUT) as resp:
            elapsed_ms = round((time.monotonic() - started) * 1000)
            return {
                "ok": True,
                "status": "up",
                "httpStatus": int(resp.status),
                "latencyMs": elapsed_ms,
                "message": f"应用服务可达（HTTP {resp.status}，{elapsed_ms}ms）",
            }
    except urllib.error.HTTPError as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        # 4xx/5xx 说明端口可连但服务状态异常，健康监测不能判为就绪
        return {
            "ok": False,
            "status": "down",
            "httpStatus": int(exc.code),
            "latencyMs": elapsed_ms,
            "message": f"应用服务有响应但状态异常（HTTP {exc.code}，{elapsed_ms}ms）",
        }
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        reason = getattr(exc, "reason", exc)
        return {
            "ok": False,
            "status": "down",
            "latencyMs": elapsed_ms,
            "message": f"应用服务不可达：{reason}",
        }


def _probe_tcp(host: str, port: int) -> dict[str, Any]:
    """数据库服务器 TCP 端口探测：能握手即视为可达。"""
    if not host:
        return {"ok": False, "status": "unconfigured", "message": "未配置数据库主机"}
    if port <= 0 or port > 65535:
        return {"ok": False, "status": "invalid", "message": "数据库端口非法"}
    started = time.monotonic()
    try:
        with socket.create_connection((host, port), timeout=PROBE_TIMEOUT):
            elapsed_ms = round((time.monotonic() - started) * 1000)
            return {
                "ok": True,
                "status": "up",
                "latencyMs": elapsed_ms,
                "message": f"数据库端口 {port} 可达（{elapsed_ms}ms）",
            }
    except (OSError, TimeoutError) as exc:
        elapsed_ms = round((time.monotonic() - started) * 1000)
        return {
            "ok": False,
            "status": "down",
            "latencyMs": elapsed_ms,
            "message": f"数据库 {host}:{port} 不可达：{exc}",
        }


def probe_env(
    *,
    app_servers: list[dict[str, str]] | None = None,
    db_type: str,
    db_host: str,
    db_port: int | None,
    db_metrics_url: str = "",
) -> dict[str, Any]:
    """对一套环境的多台应用服务器与数据库各探一次，返回结构化结果与资源指标。

    应用侧 ok 是「全部配置的服务器都可达」；单台时消息原样返回，
    多台时给出 N/M 汇总与异常地址，首页再按 servers 逐台展示。
    """
    servers: list[dict[str, Any]] = []
    for item in app_servers or []:
        base_url = str(item.get("base_url") or "").strip()
        health_path = str(item.get("health_path") or "")
        metrics_url = str(item.get("metrics_url") or "")
        if base_url:
            result = _probe_http(base_url, health_path)
            result["metrics"] = server_metrics_service.collect_metrics(
                host=server_metrics_service.host_of_url(base_url),
                metrics_url=metrics_url,
            )
        else:
            result = {
                "ok": False,
                "status": "unconfigured",
                "latencyMs": 0,
                "message": "未配置应用服务器地址",
                "metrics": server_metrics_service.unavailable_metrics("未配置应用服务器"),
            }
        result["baseUrl"] = base_url
        servers.append(result)

    if not servers:
        app_result: dict[str, Any] = {
            "ok": False,
            "status": "unconfigured",
            "latencyMs": 0,
            "message": "未配置应用服务器",
            "metrics": server_metrics_service.unavailable_metrics("未配置应用服务器"),
            "servers": [],
        }
    else:
        up = sum(1 for server in servers if server.get("ok"))
        ok = up == len(servers)
        if len(servers) == 1:
            message = str(servers[0].get("message") or "")
        elif ok:
            message = f"{len(servers)} 台应用服务器全部可达"
        else:
            down = "；".join(
                f"{server.get('baseUrl') or '未配置'}（{server.get('message')}）"
                for server in servers if not server.get("ok")
            )
            message = f"{up}/{len(servers)} 台可达；异常：{down}"
        app_result = {
            "ok": ok,
            "status": "up" if ok else "down",
            "latencyMs": max(int(server.get("latencyMs") or 0) for server in servers),
            "message": message,
            # 首台指标保留兼容；首页按 servers 逐台取
            "metrics": servers[0].get("metrics"),
            "servers": servers,
        }
        if servers[0].get("httpStatus") is not None:
            app_result["httpStatus"] = servers[0].get("httpStatus")

    port = int(db_port) if db_port is not None and str(db_port) != "" else DB_DEFAULT_PORTS.get(db_type, 0)
    if db_type == "sqlite":
        # SQLite 是本地文件，没有网络端口可探；健康监测按「配置了文件路径」记为就绪
        db_result: dict[str, Any] = {
            "ok": bool(db_host or db_port),
            "status": "up" if (db_host or db_port) else "unconfigured",
            "latencyMs": 0,
            "message": "SQLite 为本地文件库，无网络端口探测" if (db_host or db_port) else "未配置数据库文件路径",
        }
        db_result["metrics"] = server_metrics_service.local_metrics()
    else:
        db_result = _probe_tcp(db_host, port)
        db_result["metrics"] = (
            server_metrics_service.collect_metrics(host=db_host, metrics_url=db_metrics_url)
            if db_host.strip()
            else server_metrics_service.unavailable_metrics("未配置数据库主机")
        )

    return {
        "app": app_result,
        "db": db_result,
        "overall": "healthy" if app_result.get("ok") and db_result.get("ok") else "unhealthy",
    }
