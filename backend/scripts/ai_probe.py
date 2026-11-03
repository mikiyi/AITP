"""AI 连接层探针 CLI：直接验证 provider 连通性、流式增量与错误映射。

    python backend/scripts/ai_probe.py --provider openai --base-url http://127.0.0.1:8790/v1 \
        --model mock-gpt-4o-mini --api-key sk-test --stream

退出码：0 成功 / 1 参数错误 / 2 未配置凭证(110102) / 3 鉴权失败(110103) /
        4 服务不可达(110105) / 5 其他 AI 错误
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
cast(Any, sys.stderr).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.client import AIClient  # noqa: E402
from aitp.ai.errors import AIError  # noqa: E402
from aitp.ai.resolver import ensure_credential, resolve  # noqa: E402
from aitp.ai.types import Message, ResolvedAIConfig  # noqa: E402
from aitp.common.constants import PROTOCOL_OLLAMA_NATIVE, PROTOCOL_OPENAI_COMPAT  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 1
EXIT_NO_CREDENTIAL = 2
EXIT_AUTH = 3
EXIT_UNREACHABLE = 4
EXIT_OTHER = 5


def _build_config(args: argparse.Namespace) -> ResolvedAIConfig:
    if args.env:
        return resolve(use_db=False)

    protocol = args.protocol
    if not protocol:
        protocol = PROTOCOL_OLLAMA_NATIVE if args.provider == "ollama" else PROTOCOL_OPENAI_COMPAT

    return ensure_credential(
        ResolvedAIConfig(
            provider=args.provider,
            protocol=protocol,
            base_url=args.base_url,
            model=args.model,
            api_key=args.api_key or "",
            temperature=args.temperature,
            max_tokens=args.max_tokens,
            timeout=args.timeout,
            sources={
                "provider": "cli",
                "baseUrl": "cli",
                "model": "cli",
                "apiKey": "cli" if args.api_key else "none",
            },
        )
    )


def _exit_code_for(error: AIError) -> int:
    from aitp.errors import ErrorCode

    if error.code == ErrorCode.AI_CREDENTIAL_MISSING:
        return EXIT_NO_CREDENTIAL
    if error.code == ErrorCode.AI_AUTH_FAILED:
        return EXIT_AUTH
    if error.code == ErrorCode.AI_UNREACHABLE:
        return EXIT_UNREACHABLE
    return EXIT_OTHER


def main() -> int:
    parser = argparse.ArgumentParser(description="AITP AI 连接层探针")
    parser.add_argument("--env", action="store_true", help="使用环境变量中的 AI 配置")
    parser.add_argument("--provider", default="openai")
    parser.add_argument("--protocol", default="")
    parser.add_argument("--base-url", default="")
    parser.add_argument("--model", default="")
    parser.add_argument("--api-key", default="")
    parser.add_argument("--temperature", type=float, default=0.7)
    parser.add_argument("--max-tokens", type=int, default=512)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--stream", action="store_true", help="走流式并统计首帧/总耗时")
    parser.add_argument("--list-models", action="store_true", help="仅拉取模型列表")
    parser.add_argument("--prompt", default="ping")
    args = parser.parse_args()

    try:
        config = _build_config(args)
    except AIError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return _exit_code_for(exc)

    client = AIClient(config)

    try:
        if args.list_models:
            started = time.perf_counter()
            models = client.list_models()
            print(
                json.dumps(
                    {
                        "ok": True,
                        "code": 200000,
                        "provider": config.provider,
                        "protocol": config.protocol,
                        "endpoint": config.base_url,
                        "latencyMs": int((time.perf_counter() - started) * 1000),
                        "models": [m.as_dict() for m in models],
                    },
                    ensure_ascii=False,
                )
            )
            return EXIT_OK

        if args.stream:
            started = time.perf_counter()
            first_chunk_ms = None
            chunks = 0
            text_parts: list[str] = []
            usage = None
            finish_reason = "stop"
            for event in client.chat_stream([Message(role="user", content=args.prompt)]):
                if first_chunk_ms is None and event.type == "delta":
                    first_chunk_ms = int((time.perf_counter() - started) * 1000)
                if event.type == "delta":
                    chunks += 1
                    text_parts.append(event.content)
                    print(f"[{int((time.perf_counter() - started) * 1000):>6}ms] delta {event.content!r}", file=sys.stderr)
                elif event.type == "usage":
                    usage = event.usage
                elif event.type == "done":
                    finish_reason = event.finish_reason or "stop"
                elif event.type == "reasoning":
                    print(f"[{int((time.perf_counter() - started) * 1000):>6}ms] reasoning {event.content!r}", file=sys.stderr)
            total_ms = int((time.perf_counter() - started) * 1000)
            print(
                json.dumps(
                    {
                        "ok": True,
                        "code": 200000,
                        "provider": config.provider,
                        "protocol": config.protocol,
                        "model": config.model,
                        "finishReason": finish_reason,
                        "usage": usage.as_dict() if usage else None,
                        "reply": "".join(text_parts),
                        "chunkCount": chunks,
                        "firstChunkMs": first_chunk_ms,
                        "totalStreamMs": total_ms,
                    },
                    ensure_ascii=False,
                )
            )
            return EXIT_OK

        result = client.chat([Message(role="user", content=args.prompt)])
        print(
            json.dumps(
                {
                    "ok": True,
                    "code": 200000,
                    "provider": config.provider,
                    "protocol": config.protocol,
                    "model": result.model,
                    "latencyMs": result.latency_ms,
                    "finishReason": result.finish_reason,
                    "usage": result.usage.as_dict(),
                    "reply": result.content,
                },
                ensure_ascii=False,
            )
        )
        return EXIT_OK

    except AIError as exc:
        print(json.dumps({"ok": False, "code": exc.code, "message": exc.message}, ensure_ascii=False))
        return _exit_code_for(exc)
    except Exception as exc:  # pragma: no cover - 兜底
        print(json.dumps({"ok": False, "code": 199000, "message": str(exc)}, ensure_ascii=False))
        return EXIT_OTHER


if __name__ == "__main__":
    sys.exit(main())
