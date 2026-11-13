"""钉住工具 MCP 桩服务器（GAP-45 门禁用）。

stdio JSON-RPC：从 stdin 逐行读请求、向 stdout 逐行写响应，模拟浏览器工具。
不依赖真实浏览器/模型，异常由环境变量确定性注入：

    AITP_MCP_ANOMALY_STEPS   逗号分隔的快照序号（1 起），这些步注入异常，如 "2,4"
    AITP_MCP_ANOMALY_KIND    http / console / page / business（默认 http）
    AITP_MCP_MISSING_TOOL    置为某工具名时 tools/list 不返回它（测钉住校验）

用法：python backend/scripts/mock_mcp_server.py
"""

from __future__ import annotations

import base64
import json
import os
import sys
from typing import Any

PINNED = [
    "browser_navigate", "browser_click", "browser_type", "browser_snapshot",
    "browser_screenshot", "browser_console_messages", "browser_network_requests",
]


def _anomaly_steps() -> set[int]:
    raw = os.environ.get("AITP_MCP_ANOMALY_STEPS", "")
    result: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            result.add(int(part))
    return result


def _kind() -> str:
    return os.environ.get("AITP_MCP_ANOMALY_KIND", "http").strip().lower()


class Stub:
    def __init__(self) -> None:
        self.snapshot_count = 0
        self.current_url = ""
        self.anomaly = False
        self.anomaly_kind = "http"
        self.shot_count = 0

    def handle(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aitp-stub-mcp", "version": "1.0"},
            }
        if method == "tools/list":
            missing = os.environ.get("AITP_MCP_MISSING_TOOL", "")
            tools = [
                {"name": name, "description": f"stub {name}", "inputSchema": {"type": "object"}}
                for name in PINNED if name != missing
            ]
            return {"tools": tools}
        if method == "tools/call":
            return self.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
        raise ValueError(f"未知方法：{method}")

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        payload: dict[str, Any]
        if name == "browser_navigate":
            self.current_url = str(args.get("url") or "")
            payload = {"url": self.current_url, "title": "Stub 页面"}
        elif name in ("browser_click", "browser_type"):
            payload = {"ok": True}
        elif name == "browser_snapshot":
            self.snapshot_count += 1
            self.anomaly = self.snapshot_count in _anomaly_steps()
            self.anomaly_kind = _kind()
            text = "订单列表 页面正常 共 12 条"
            if self.anomaly and self.anomaly_kind == "page":
                text = "Internal Server Error (500) 页面崩溃"
            elif self.anomaly and self.anomaly_kind == "business":
                text = '响应 {"code":1,"msg":"余额不足"}'
            payload = {"snapshot": text, "url": self.current_url,
                       "traceId": f"trace-{self.snapshot_count:03d}"}
        elif name == "browser_screenshot":
            self.shot_count += 1
            payload = {
                "fileName": f"shot-{self.shot_count:03d}.png",
                "content": base64.b64encode(b"stub-png-bytes").decode("ascii"),
            }
        elif name == "browser_console_messages":
            messages = []
            if self.anomaly and self.anomaly_kind == "console":
                messages = [{"level": "error", "text": "Uncaught TypeError: cannot read property 'id'"}]
            payload = {"messages": messages}
        elif name == "browser_network_requests":
            requests = [{"url": self.current_url + "/api/orders", "method": "GET", "status": 200}]
            if self.anomaly and self.anomaly_kind == "http":
                requests = [{"url": self.current_url + "/api/orders", "method": "GET",
                             "status": 500, "traceId": f"trace-{self.snapshot_count:03d}"}]
            payload = {"requests": requests}
        else:
            raise ValueError(f"未知工具：{name}")
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": False}


def main() -> int:
    stub = Stub()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            continue
        request_id = request.get("id")
        try:
            result = stub.handle(str(request.get("method") or ""), request.get("params") or {})
            response = {"jsonrpc": "2.0", "id": request_id, "result": result}
        except Exception as exc:  # noqa: BLE001 - 桩把异常作为 JSON-RPC error 返回
            response = {"jsonrpc": "2.0", "id": request_id,
                        "error": {"code": -32000, "message": str(exc)}}
        sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())