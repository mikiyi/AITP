"""Promptfoo Python provider：离线回放 12 条金融用例的好/坏输出。

promptfoo 约定：
    def call_api(prompt, options, context) -> {"output": "..."}
prompt 渲染后就是用例的问题原文，按问题匹配用例；config.mode 决定给 good 还是 bad。

比 exec provider 稳：exec 的 stdin 协议在不同版本间有差异（实测 0.126 不传 vars），
python provider 的 prompt 一定拿得到。
"""

from __future__ import annotations

import json
from pathlib import Path

CASES = json.loads(
    (Path(__file__).resolve().parent / "cases" / "finance_cases.json").read_text(encoding="utf-8")
)
BY_QUESTION = {case["question"]: case for case in CASES}
BY_ID = {case["id"]: case for case in CASES}


def call_api(prompt, options, context):  # noqa: ANN001, ARG001 - promptfoo 固定签名
    config = (options or {}).get("config") or {}
    mode = str(config.get("mode") or "good")

    case = BY_QUESTION.get(str(prompt).strip())
    if case is None:
        for candidate in BY_ID.values():
            if candidate["id"] in str(prompt):
                case = candidate
                break
    if case is None:
        return {"output": f"case not found: {str(prompt)[:40]}"}
    return {"output": case.get(mode, "")}
