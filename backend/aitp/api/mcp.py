"""AITP MCP 服务端（V2.7.0）：JSON-RPC 2.0 over HTTP。

端点：``POST /api/v1/mcp``，支持 MCP 的 initialize / tools/list / tools/call / ping。
安全边界：
  - 只接受本机回环请求；可在设置页额外配置令牌（Authorization: Bearer 或 X-AITP-MCP-Token）。
  - 默认只读；写工具 ``bugs.draft.create`` 需要显式开启「允许写操作」，
    且只写入缺陷待审区，不直接创建正式缺陷。
  - 每次调用留痕到 ``sys_mcp_call_log``，不记录完整参数，避免泄露业务数据。
"""

from __future__ import annotations

import json
import time
from typing import Any

from flask import Blueprint, Response, request

from ..common.time_utils import utcnow_iso
from ..repositories.sys_config_repo import SysConfigRepository
from ..services import mcp_service, mcp_token_service

bp = Blueprint("mcp", __name__)

PROTOCOL_VERSION = "2024-11-05"
SERVER_NAME = "aitp"
SERVER_VERSION = "2.8.0"
MAX_LOG_DETAIL = 300

_LOOPBACK = {"127.0.0.1", "::1", "localhost", None}


def _config(conn: Any) -> dict[str, Any]:
    repo = SysConfigRepository(conn)
    active, token_required = mcp_token_service.state(conn)
    return {
        "enabled": str(repo.get_value("mcp.enabled", "0") or "0").strip().lower() in ("1", "true", "on"),
        "token": active,
        "tokenRequired": token_required,
        "allowWrite": str(repo.get_value("mcp.allow_write", "0") or "0").strip().lower() in ("1", "true", "on"),
    }


def _rpc_error(request_id: Any, code: int, message: str, http_status: int = 200) -> Response:
    payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    return Response(json.dumps(payload, ensure_ascii=False), status=http_status,
                    mimetype="application/json")


def _authorized(conn: Any, config: dict[str, Any]) -> bool:
    """回环 + 令牌校验；令牌只比对哈希，命中后更新最后使用时间与客户端。"""
    if request.remote_addr not in _LOOPBACK:
        return False
    if not config["tokenRequired"]:
        return True
    header = request.headers.get("Authorization", "")
    supplied = header[7:].strip() if header.lower().startswith("bearer ") else ""
    supplied = supplied or request.headers.get("X-AITP-MCP-Token", "").strip()
    row = mcp_token_service.verify(conn, supplied)
    if row is None:
        return False
    mcp_token_service.touch(conn, int(row["id"]), request.headers.get("User-Agent") or "")
    return True


def _log(conn: Any, method: str, tool: str, status: str, started: float,
         detail: str = "") -> None:
    conn.execute("""
        INSERT INTO sys_mcp_call_log
            (method, tool, status, duration_ms, detail, client, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (
        method[:80], tool[:120], status, int((time.monotonic() - started) * 1000),
        detail[:MAX_LOG_DETAIL],
        (request.headers.get("User-Agent") or "")[:200], utcnow_iso(),
    ))
    conn.commit()


def _result(request_id: Any, result: dict[str, Any]) -> Response:
    payload = {"jsonrpc": "2.0", "id": request_id, "result": result}
    return Response(json.dumps(payload, ensure_ascii=False), mimetype="application/json")


def _initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
        "instructions": (
            "AITP 本地测试工作台。默认只读；写操作只进入缺陷待审区，需要人工在 AITP 中确认。"
        ),
    }


@bp.route("", methods=["POST"])
@bp.route("/", methods=["POST"])
def handle_rpc() -> Response:
    started = time.monotonic()
    conn = _conn()
    config = _config(conn)
    if not config["enabled"]:
        return _rpc_error(None, -32000, "AITP MCP 服务未开启，请到「设置 · MCP 服务」启用", 403)
    if not _authorized(conn, config):
        return _rpc_error(None, -32001, "未通过本机回环或令牌校验", 403)

    try:
        payload = request.get_json(silent=True)
    except Exception:  # noqa: BLE001
        payload = None
    if not isinstance(payload, dict):
        return _rpc_error(None, -32700, "请求体不是合法 JSON-RPC 对象")
    request_id = payload.get("id")
    method = str(payload.get("method") or "")
    raw_params: Any = payload.get("params")
    params: dict[str, Any] = raw_params if isinstance(raw_params, dict) else {}
    if not method:
        return _rpc_error(request_id, -32600, "缺少 method")

    if method == "initialize":
        _log(conn, method, "", "ok", started)
        return _result(request_id, _initialize_result())
    if method in ("notifications/initialized", "initialized"):
        _log(conn, method, "", "ok", started)
        return Response("", status=202)
    if method == "ping":
        _log(conn, method, "", "ok", started)
        return _result(request_id, {})
    if method == "tools/list":
        _log(conn, method, "", "ok", started)
        return _result(request_id, {"tools": mcp_service.tool_definitions()})
    if method == "tools/call":
        name = str(params.get("name") or "")
        arguments = params.get("arguments") if isinstance(params.get("arguments"), dict) else {}
        if not name:
            _log(conn, method, "", "error", started, "缺少工具名")
            return _rpc_error(request_id, -32602, "tools/call 缺少 name")
        if mcp_service.is_write_tool(name) and not config["allowWrite"]:
            _log(conn, method, name, "denied", started, "写操作未开启")
            return _result(request_id, {
                "content": [{"type": "text", "text": "写操作未开启：请在 AITP「设置 · MCP 服务」允许写操作"}],
                "isError": True,
            })
        try:
            data = mcp_service.call_tool(conn, name, arguments)
        except mcp_service.McpError as exc:
            _log(conn, method, name, "error", started, str(exc))
            return _result(request_id, {
                "content": [{"type": "text", "text": str(exc)}], "isError": True,
            })
        except Exception as exc:  # noqa: BLE001 - 工具异常统一回给客户端
            _log(conn, method, name, "error", started, str(exc))
            return _result(request_id, {
                "content": [{"type": "text", "text": f"工具执行失败：{exc}"}], "isError": True,
            })
        _log(conn, method, name, "ok", started)
        return _result(request_id, {
            "content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
            "isError": False,
        })

    _log(conn, method, "", "error", started, "method not found")
    return _rpc_error(request_id, -32601, f"未知方法：{method}")


def _conn() -> Any:
    from .helpers import db  # 延迟导入，避免蓝图加载顺序问题

    return db()


__all__ = ["bp"]
