"""推理模型「关闭思考模式」链路回归门禁（档案额外参数 → 解析 → 请求体字段）。

锁死这个体验问题：本地 qwen3:8b 问一句话要等 1 分钟——问答是非流式的，必须等
思考 token 全部生成完才返回。档案勾上「关闭思考模式」（extra_json.thinking=off）
后实测 57 秒 → 13 秒，前提是这个标记真的一路传到请求体里。

    python backend/scripts/check_ai_thinking.py

纯离线：只调 resolver 与两个适配器的 _body，不发 HTTP、不碰正式库与本地服务。
断言分三类：
  1) 解析：thinking=off 才置位；缺失、其它值、坏 JSON、没有 extra 都不置位；
  2) Ollama 原生：置位时下发 think=false，未置位时请求体不带 think 字段；
  3) OpenAI 兼容：置位时同时下发 reasoning_effort=none 与
     chat_template_kwargs.enable_thinking=false，未置位时两个字段都不带（默认语义不变）。
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.adapters.ollama_native import OllamaNativeAdapter  # noqa: E402
from aitp.ai.adapters.openai_compat import OpenAICompatAdapter  # noqa: E402
from aitp.ai.resolver import _build_config  # noqa: E402
from aitp.ai.types import Message, ResolvedAIConfig  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


def profile_with(extra_json: str | None) -> dict[str, Any]:
    profile: dict[str, Any] = {
        "id": 1,
        "name": "思考门禁档案",
        "provider": "ollama",
        "protocol": "openai_compat",
        "base_url": "http://127.0.0.1:11434",
        "model": "qwen3:8b",
        "is_local": 1,
    }
    if extra_json is not None:
        profile["extra_json"] = extra_json
    return profile


def build(profile: dict[str, Any]) -> ResolvedAIConfig:
    return _build_config(profile, key_path=None, require_key=False)


def main() -> int:
    # ① 解析层
    check("extra_json.thinking=off 解析为关闭思考",
          build(profile_with('{"thinking": "off"}')).disable_thinking)
    check("extra 直接是 dict 时也认 thinking=off",
          build({**profile_with(None), "extra": {"thinking": "off"}}).disable_thinking)
    check("thinking 其它值不置位",
          not build(profile_with('{"thinking": "on"}')).disable_thinking)
    check("extra 里没有 thinking 不置位",
          not build(profile_with('{"other": 1}')).disable_thinking)
    check("没有 extra_json 不置位", not build(profile_with(None)).disable_thinking)
    check("坏 JSON 不置位且不抛异常", not build(profile_with("{not-json")).disable_thinking)

    # ② Ollama 原生协议：think 字段
    native = OllamaNativeAdapter()
    messages = [Message(role="user", content="1+1等于几？")]
    off_cfg = build({**profile_with('{"thinking": "off"}'), "protocol": "ollama_native"})
    on_cfg = build({**profile_with(None), "protocol": "ollama_native"})
    off_body = native._body(off_cfg, messages, False)
    on_body = native._body(on_cfg, messages, False)
    check("原生协议关闭思考时下发 think=false", off_body.get("think") is False,
          f"body={off_body}")
    check("原生协议默认不带 think 字段", "think" not in on_body, f"body={on_body}")
    check("原生协议仍带 options.num_predict",
          "num_predict" in off_body.get("options", {}), f"body={off_body}")

    # ③ OpenAI 兼容协议：reasoning_effort 与 chat_template_kwargs
    compat = OpenAICompatAdapter()
    off_body = compat._body(off_cfg, messages, False)
    on_body = compat._body(on_cfg, messages, False)
    check("兼容协议关闭思考时下发 reasoning_effort=none",
          off_body.get("reasoning_effort") == "none", f"body={off_body}")
    check("兼容协议关闭思考时下发 chat_template_kwargs.enable_thinking=false",
          off_body.get("chat_template_kwargs") == {"enable_thinking": False},
          f"body={off_body}")
    check("兼容协议默认不带这两个字段",
          "reasoning_effort" not in on_body and "chat_template_kwargs" not in on_body,
          f"body={on_body}")

    # ④ 档案额外参数经 API 落库的键名与前端约定一致（extra.thinking）
    check("关闭思考不影响 max_tokens 下发",
          off_body.get("max_tokens") == on_body.get("max_tokens") == 4096,
          f"off={off_body.get('max_tokens')} on={on_body.get('max_tokens')}")

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
