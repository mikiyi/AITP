"""真实浏览器 MCP 服务器（Web 探索性测试默认驱动）。

与 mock_mcp_server.py 同一套 stdio JSON-RPC 协议与钉住工具口径，但背后是
真实 Chromium（Playwright）：会真的拉起浏览器窗口、真的点击/输入/截图/抓控制台与网络。
mock 仅用于门禁与演示（确定性注入异常），真实探索请用本脚本。

    python backend/scripts/browser_mcp_server.py

环境变量：
    AITP_MCP_HEADLESS=1   无头模式（默认有头，便于人工观察探索过程）

依赖：pip install playwright && python -m playwright install chromium
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

MAX_SNAPSHOT_CHARS = 4000
MAX_EVENTS = 200


class RealBrowser:
    def __init__(self) -> None:
        self._pw: Any = None
        self._browser: Any = None
        self._page: Any = None
        self._console: list[dict[str, Any]] = []
        self._requests: list[dict[str, Any]] = []
        self._snap = 0
        self._shot = 0

    def _ensure(self) -> Any:
        if self._page is not None:
            return self._page
        from playwright.sync_api import sync_playwright

        self._pw = sync_playwright().start()
        headless = os.environ.get("AITP_MCP_HEADLESS", "").strip().lower() in ("1", "true", "yes")
        self._browser = self._pw.chromium.launch(headless=headless)
        self._page = self._browser.new_page()
        self._page.on("console", self._on_console)
        self._page.on("response", self._on_response)
        return self._page

    def _on_console(self, message: Any) -> None:
        self._console.append({"level": str(message.type), "text": str(message.text)})
        del self._console[:-MAX_EVENTS]

    def _on_response(self, response: Any) -> None:
        self._requests.append({
            "url": str(response.url),
            "method": str(response.request.method),
            "status": int(response.status),
            "traceId": f"trace-{len(self._requests) + 1:03d}",
        })
        del self._requests[:-MAX_EVENTS]

    def handle(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "initialize":
            return {
                "protocolVersion": "2024-11-05",
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "aitp-real-browser-mcp", "version": "1.0"},
            }
        if method == "tools/list":
            return {"tools": [
                {"name": name, "description": f"real browser {name}", "inputSchema": {"type": "object"}}
                for name in PINNED
            ]}
        if method == "tools/call":
            return self.call_tool(str(params.get("name") or ""), params.get("arguments") or {})
        raise ValueError(f"未知方法：{method}")

    def call_tool(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        page = self._ensure()
        payload: dict[str, Any]
        if name == "browser_navigate":
            url = str(args.get("url") or "")
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            payload = {"url": str(page.url), "title": str(page.title())}
        elif name == "browser_click":
            page.click(str(args.get("selector") or ""), timeout=10000)
            payload = {"ok": True}
        elif name == "browser_type":
            page.fill(str(args.get("selector") or ""), str(args.get("value") or ""), timeout=10000)
            payload = {"ok": True}
        elif name == "browser_snapshot":
            self._snap += 1
            text = str(page.evaluate("() => (document.body ? document.body.innerText : '')"))
            payload = {
                "snapshot": text[:MAX_SNAPSHOT_CHARS],
                "url": str(page.url),
                "traceId": f"trace-{self._snap:03d}",
            }
        elif name == "browser_screenshot":
            self._shot += 1
            data = page.screenshot(timeout=15000)
            payload = {
                "fileName": f"shot-{self._shot:03d}.png",
                "content": base64.b64encode(data).decode("ascii"),
            }
        elif name == "browser_console_messages":
            payload = {"messages": self._console}
            self._console = []
        elif name == "browser_network_requests":
            payload = {"requests": self._requests}
            self._requests = []
        else:
            raise ValueError(f"未知工具：{name}")
        return {"content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
                "isError": False}

    def close(self) -> None:
        import contextlib

        with contextlib.suppress(Exception):
            if self._browser is not None:
                self._browser.close()
        with contextlib.suppress(Exception):
            if self._pw is not None:
                self._pw.stop()


def main() -> int:
    browser = RealBrowser()
    try:
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
                result = browser.handle(str(request.get("method") or ""), request.get("params") or {})
                response = {"jsonrpc": "2.0", "id": request_id, "result": result}
            except Exception as exc:  # noqa: BLE001 - 工具异常作为 JSON-RPC error 返回
                response = {"jsonrpc": "2.0", "id": request_id,
                            "error": {"code": -32000, "message": str(exc)}}
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    finally:
        browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())