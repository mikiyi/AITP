"""离线 mock AI 服务，用于在没有真实 API Key 时验证 AI 连接层。

  --port 8790            OpenAI 兼容协议（SSE 流式 + 非流式 + /v1/models）
  --ollama-port 8791     Ollama 原生协议（NDJSON 流式 + /api/tags）

用法：
    python backend/scripts/mock_openai_server.py --port 8790 --ollama-port 8791
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

STREAM_CHUNKS = ["pong", " from", " AITP", " mock", " server", " ok"]
CHUNK_DELAY = 0.2

#: JSON 能力（用例数组）返回值
MOCK_CASES: list[dict[str, Any]] = [
    {
        "title": "验证码登录-正常场景-输入有效验证码",
        "caseType": "functional",
        "priority": "P0",
        "preconditions": "手机号已注册",
        "steps": [{"action": "输入手机号与有效验证码", "expected": "登录成功并跳转首页"}],
        "expectedResult": "登录成功并跳转首页",
        "tags": "登录,验证码",
    },
    {
        "title": "验证码登录-异常场景-验证码已过期",
        "caseType": "exception",
        "priority": "P1",
        "preconditions": "无",
        "steps": [{"action": "输入手机号与过期验证码", "expected": "提示验证码已过期"}],
        "expectedResult": "提示验证码已过期",
        "tags": "登录,验证码,异常",
    },
    {
        "title": "验证码登录-正常场景-Token 正确返回",
        "caseType": "functional",
        "priority": "P0",
        "preconditions": "手机号已注册且验证码有效",
        "steps": [{"action": "登录成功后抓取响应体", "expected": "返回 accessToken 与过期时间"}],
        "expectedResult": "响应体含 accessToken、expiresIn 字段",
        "tags": "登录,Token",
    },
    {
        "title": "验证码登录-接口-发送验证码",
        "caseType": "interface",
        "priority": "P0",
        "preconditions": "手机号已注册",
        "steps": [{"action": "POST /api/auth/sms/send 传入手机号", "expected": "返回 code=0 并下发验证码"}],
        "expectedResult": "HTTP 200 且 code=0",
        "tags": "登录,接口",
    },
    {
        "title": "验证码登录-边界-验证码 60 秒临界",
        "caseType": "boundary",
        "priority": "P1",
        "preconditions": "验证码下发后第 59 秒",
        "steps": [{"action": "第 59 秒提交验证码", "expected": "校验通过并登录成功"}],
        "expectedResult": "59 秒可通过，61 秒被拒",
        "tags": "登录,验证码,边界",
    },
    {
        "title": "验证码登录-安全-连续错误锁定",
        "caseType": "security",
        "priority": "P0",
        "preconditions": "账号未锁定",
        "steps": [{"action": "连续 5 次输入错误验证码", "expected": "账号锁定 15 分钟且提示剩余时间"}],
        "expectedResult": "第 5 次失败后账号被锁定",
        "tags": "登录,安全,锁定",
    },
    {
        "title": "验证码登录-UI-登录页展示",
        "caseType": "ui",
        "priority": "P3",
        "preconditions": "已打开登录页",
        "steps": [{"action": "检查登录页文案与输入框", "expected": "手机号、验证码输入框与倒计时展示正确"}],
        "expectedResult": "页面元素与设计稿一致",
        "tags": "登录,UI",
    },
    {
        "title": "验证码登录-异常-网络超时",
        "caseType": "exception",
        "priority": "P2",
        "preconditions": "模拟弱网",
        "steps": [{"action": "断网后提交登录", "expected": "提示网络异常且可重试"}],
        "expectedResult": "提示「网络异常，请稍后重试」",
        "tags": "登录,异常,网络",
    },
]

#: 脑图能力（测试点树）返回值，结构与 testpoint-generation 提示词约定一致
MOCK_TESTPOINTS: dict[str, Any] = {
    "name": "登录测试点",
    "children": [
        {
            "name": "正常流程",
            "children": [
                {"name": "有效验证码登录成功", "priority": "P0", "desc": "验证码有效时登录成功并跳转首页", "children": []}
            ],
        },
        {
            "name": "异常分支",
            "children": [
                {"name": "验证码超时", "priority": "P1", "desc": "验证码过期后提示并可重新获取", "children": []},
                {"name": "验证码错误", "priority": "P1", "desc": "验证码不匹配时提示错误", "children": []},
            ],
        },
    ],
}

_capability_re = re.compile(r"^- ([a-z_]+)（.*?关键词：(.*)$", re.MULTILINE)
_module_re = re.compile(r"^- id=(\d+)｜(.*?)｜", re.MULTILINE)


def _reply_for(prompt: str) -> str:
    """按提示词内容返回对应的 mock 产出，尽量贴近真实模型行为。"""
    if "功能点清单" in prompt:
        match = _module_re.search(prompt)
        module_id = int(match.group(1)) if match else 1
        path = match.group(2) if match else ""
        return json.dumps(
            {"routes": [{"moduleId": module_id, "path": path, "score": 0.88,
                         "reason": f"文本指向「{path}」"}]},
            ensure_ascii=False,
        )
    if "能力目录" in prompt:
        return json.dumps(_route_capabilities(prompt), ensure_ascii=False)
    if "JSON 数组" in prompt:
        return json.dumps(MOCK_CASES, ensure_ascii=False)
    if "JSON 树" in prompt or "负责把需求拆成" in prompt:
        return json.dumps(MOCK_TESTPOINTS, ensure_ascii=False)
    return "pong from AITP mock server ok"


def _split_reply(reply: str, size: int = 80) -> list[str]:
    """把整段回复切成流式分片；所有分片拼接后与原文完全一致。"""
    if not reply:
        return [""]
    if reply == "".join(STREAM_CHUNKS):
        return list(STREAM_CHUNKS)
    return [reply[index:index + size] for index in range(0, len(reply), size)]


def _route_capabilities(prompt: str) -> dict[str, Any]:
    """按能力目录里的关键词给输入文本打分，返回最匹配的能力。"""
    raw_text = prompt
    routes: list[dict[str, Any]] = []
    for match in _capability_re.finditer(prompt):
        code = match.group(1)
        keywords = [k.strip() for k in match.group(2).split("、") if k.strip()]
        hits = [k for k in keywords if k and k in raw_text]
        if not hits:
            continue
        routes.append({
            "capability": code,
            "score": round(min(0.95, 0.5 + 0.15 * len(hits)), 3),
            "reason": f"文本包含关键词：{'、'.join(hits[:3])}",
        })
    if not routes:
        fallback = _capability_re.search(prompt)
        routes = [{
            "capability": fallback.group(1) if fallback else "requirement_confirm",
            "score": 0.4,
            "reason": "默认候选",
        }]
    routes.sort(key=lambda item: item["score"], reverse=True)
    return {"routes": routes[:3]}


def _prompt_of(body: dict[str, Any]) -> str:
    """取出请求里的全部提示词文本。"""
    parts: list[str] = []
    for message in body.get("messages") or []:
        if isinstance(message, dict):
            parts.append(str(message.get("content") or ""))
    return "\n".join(parts)


_lock = threading.Lock()


def _log(port: int, message: str) -> None:
    with _lock:
        print(f"[{time.strftime('%H:%M:%S')}] mock:{port} {message}", flush=True)


class _BaseHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    port: int

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 屏蔽默认 stderr 日志
        pass

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        raw = self.rfile.read(length)
        try:
            return cast("dict[str, Any]", json.loads(raw.decode("utf-8")))
        except Exception:
            return {}

    def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self) -> bool:
        auth = self.headers.get("Authorization") or ""
        return auth == "Bearer sk-test" or auth == "Bearer sk-good"


class OpenAIHandler(_BaseHandler):
    port = 8790

    def do_GET(self) -> None:
        if self.path.rstrip("/") in ("/v1/models", "/models"):
            _log(self.port, f"GET {self.path}")
            self._send_json({"data": [{"id": "mock-gpt-4o-mini"}, {"id": "mock-gpt-4o"}]})
            return
        self._send_json({"error": {"message": "not found"}}, status=404)

    def do_POST(self) -> None:
        body = self._read_json()
        if not self.path.rstrip("/").endswith("/chat/completions"):
            self._send_json({"error": {"message": "not found"}}, status=404)
            return
        if not self._auth_ok():
            _log(self.port, f"POST {self.path} -> 401 (bad key)")
            self._send_json({"error": {"message": "Incorrect API key provided"}}, status=401)
            return

        model = body.get("model") or "mock-gpt-4o-mini"
        stream = bool(body.get("stream"))
        reply = _reply_for(_prompt_of(body))

        if not stream:
            _log(self.port, f"POST {self.path} model={model} 非流式")
            self._send_json(
                {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion",
                    "model": model,
                    "choices": [
                        {
                            "index": 0,
                            "message": {"role": "assistant", "content": reply},
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
                }
            )
            return

        _log(self.port, f"POST {self.path} model={model} 流式")
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        try:
            for index, chunk in enumerate(_split_reply(reply)):
                payload = {
                    "id": "chatcmpl-mock",
                    "object": "chat.completion.chunk",
                    "model": model,
                    "choices": [{"index": 0, "delta": {"content": chunk}, "finish_reason": None}],
                }
                self._write_chunk(f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8"))
                time.sleep(CHUNK_DELAY)
            final = {
                "id": "chatcmpl-mock",
                "object": "chat.completion.chunk",
                "model": model,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 9, "total_tokens": 21},
            }
            self._write_chunk(f"data: {json.dumps(final, ensure_ascii=False)}\n\n".encode("utf-8"))
            self._write_chunk(b"data: [DONE]\n\n")
        except (BrokenPipeError, ConnectionResetError):
            _log(self.port, "客户端中断流式响应")
        finally:
            self._write_chunk(b"")

    def _write_chunk(self, data: bytes) -> None:
        if not data:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()


class OllamaHandler(_BaseHandler):
    port = 8791

    def do_GET(self) -> None:
        if self.path.rstrip("/") == "/api/tags":
            _log(self.port, f"GET {self.path}")
            self._send_json(
                {
                    "models": [
                        {
                            "name": "mock-qwen2.5:7b",
                            "model": "mock-qwen2.5:7b",
                            "size": 1234567890,
                            "details": {
                                "family": "qwen2",
                                "parameter_size": "7.6B",
                                "quantization_level": "Q4_K_M",
                            },
                        },
                        {
                            "name": "mock-qwen3:1.7b",
                            "model": "mock-qwen3:1.7b",
                            "size": 987654321,
                            "details": {
                                "family": "qwen3",
                                "parameter_size": "1.7B",
                                "quantization_level": "Q4_K_M",
                            },
                        },
                    ]
                }
            )
            return
        if self.path.rstrip("/") == "/api/version":
            self._send_json({"version": "0.5.4-mock"})
            return
        self._send_json({"error": "not found"}, status=404)

    def do_POST(self) -> None:
        body = self._read_json()
        if not self.path.rstrip("/").endswith("/api/chat"):
            self._send_json({"error": "not found"}, status=404)
            return

        model = body.get("model") or "mock-qwen2.5:7b"
        stream = bool(body.get("stream"))
        reply = _reply_for(_prompt_of(body))

        if not stream:
            _log(self.port, f"POST {self.path} model={model} 非流式")
            self._send_json(
                {
                    "model": model,
                    "created_at": "2026-01-01T00:00:00Z",
                    "message": {"role": "assistant", "content": reply},
                    "done": True,
                    "done_reason": "stop",
                    "total_duration": 878123456,
                    "prompt_eval_count": 12,
                    "eval_count": 9,
                }
            )
            return

        _log(self.port, f"POST {self.path} model={model} 流式")
        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()

        try:
            for chunk in _split_reply(reply):
                payload = {
                    "model": model,
                    "created_at": "2026-01-01T00:00:00Z",
                    "message": {"role": "assistant", "content": chunk},
                    "done": False,
                }
                line = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
                self._write_chunk(line)
                time.sleep(CHUNK_DELAY)
            final = {
                "model": model,
                "created_at": "2026-01-01T00:00:00Z",
                "message": {"role": "assistant", "content": ""},
                "done": True,
                "done_reason": "stop",
                "total_duration": 878123456,
                "prompt_eval_count": 12,
                "eval_count": 9,
            }
            self._write_chunk((json.dumps(final, ensure_ascii=False) + "\n").encode("utf-8"))
        except (BrokenPipeError, ConnectionResetError):
            _log(self.port, "客户端中断流式响应")
        finally:
            self._write_chunk(b"")

    def _write_chunk(self, data: bytes) -> None:
        if not data:
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
            return
        self.wfile.write(f"{len(data):X}\r\n".encode("ascii"))
        self.wfile.write(data)
        self.wfile.write(b"\r\n")
        self.wfile.flush()


def serve(port: int, handler_cls: type[_BaseHandler]) -> None:
    handler_cls.port = port
    server = ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
    server.daemon_threads = True
    _log(port, f"mock server 监听 http://127.0.0.1:{port}")
    server.serve_forever()


def main() -> None:
    parser = argparse.ArgumentParser(description="AITP 离线 mock AI 服务")
    parser.add_argument("--port", type=int, default=8790, help="OpenAI 兼容协议端口")
    parser.add_argument("--ollama-port", type=int, default=8791, help="Ollama 原生协议端口")
    args = parser.parse_args()

    threads = []
    if args.ollama_port:
        threads.append(threading.Thread(target=serve, args=(args.ollama_port, OllamaHandler), daemon=True))
    threads.append(threading.Thread(target=serve, args=(args.port, OpenAIHandler), daemon=True))
    for t in threads:
        t.start()
    print("[mock] 全部就绪，Ctrl+C 退出。", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[mock] 已退出。", flush=True)


if __name__ == "__main__":
    main()
