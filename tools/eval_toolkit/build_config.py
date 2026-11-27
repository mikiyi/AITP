"""把 12 条金融用例编译成 promptfoo 配置（promptfooconfig.yaml）。

为什么用脚本生成而不是手写：断言映射规则要可复现——
  must_contain      → contains
  forbidden_contains→ not-contains
  regex             → regex
  max_chars         → javascript（去空白后长度）
  json_keys         → contains-json + javascript 检查键
  grounded          → javascript（要求出现「来源」或「【」）
生成结果里保留了「promptfoo 没有原生 grounding / 风险分级 / 区间门禁」的证据。

用法：python build_config.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = json.loads((HERE / "cases" / "finance_cases.json").read_text(encoding="utf-8"))
PY = sys.executable
FIXTURES = str(HERE / "fixtures.py")


def _q(text: str) -> str:
    return json.dumps(text, ensure_ascii=False)


def _asserts(assertions: dict) -> list[str]:
    lines: list[str] = []
    for needle in assertions.get("must_contain", []):
        lines += ["      - type: contains", f"        value: {_q(needle)}"]
    for needle in assertions.get("forbidden_contains", []):
        lines += ["      - type: not-contains", f"        value: {_q(needle)}"]
    for pattern in assertions.get("regex", []):
        lines += ["      - type: regex", f"        value: {_q(pattern)}"]
    if "max_chars" in assertions:
        limit = int(assertions["max_chars"])
        expr = f"output.replace(/\\s/g,'').length <= {limit}"
        lines += ["      - type: javascript", f"        value: {_q(expr)}"]
    if "json_keys" in assertions:
        keys = assertions["json_keys"]
        expr = (
            "(() => { try { const o = JSON.parse(output); return "
            + " && ".join(f"{_q(k)} in o" for k in keys)
            + "; } catch (e) { return false; } })()"
        )
        lines += ["      - type: javascript", f"        value: {_q(expr)}"]
    if assertions.get("grounded"):
        expr = "output.includes('来源') || output.includes('【')"
        lines += ["      - type: javascript", f"        value: {_q(expr)}"]
    return lines


def build() -> str:
    out = [
        "# 由 build_config.py 生成：12 条金融用例 × good/bad 两版输出",
        "description: \"AITP 对拍 Promptfoo：12 条金融题，规则断言口径对齐\"",
        "prompts:",
        '  - "{{question}}"',
        "providers:",
        f'  - id: {_q("python:" + str(HERE / "provider.py"))}',
        '    label: "fixture-good"',
        "    config:",
        "      mode: good",
        f'  - id: {_q("python:" + str(HERE / "provider.py"))}',
        '    label: "fixture-bad"',
        "    config:",
        "      mode: bad",
        "tests:",
    ]
    for case in CASES:
        out += [
            f"  - description: {_q(case['id'] + ' / ' + case['category'] + ' / ' + case['severity'])}",
            "    vars:",
            f"      caseId: {_q(case['id'])}",
            f"      question: {_q(case['question'])}",
            "    assert:",
        ]
        out += _asserts(case["assertions"])
    return "\n".join(out) + "\n"


if __name__ == "__main__":
    target = HERE / "promptfoo" / "promptfooconfig.yaml"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(build(), encoding="utf-8")
    print(f"wrote {target} ({len(CASES)} cases)")
