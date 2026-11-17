"""验证报告 HTML/打印导出的 Markdown 净化（A2）。

    python backend/scripts/check_report_xss.py

只跑渲染层，不建库、不调模型：确认 attr_list、javascript 链接与原始 HTML
不会进入导出文件，同时常规表格/代码块/安全链接仍然保留。
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.services import report_exporter  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    print(f"[FAIL] {name}  {detail}")
    return False


def render(content: str, print_view: bool = False) -> str:
    report = {
        "title": "XSS 报告",
        "summary": "",
        "content": "",
        "period_start": "2026-11-01",
        "period_end": "2026-11-16",
    }
    items = [{
        "section": "overview", "title": "概览", "content": content, "metric": {},
    }]
    return report_exporter.render_html(report, items, print_view=print_view)


def sanitized(content: str) -> str:
    """只看正文净化结果，避免把打印按钮自带的 onclick 算成漏洞。"""
    return report_exporter._markdown_to_html(report_exporter._escape_html_tags(content))


def has_event_attr(html: str) -> bool:
    return re.search(r"<[a-zA-Z][^>]*\son[a-zA-Z]+\s*=", html) is not None


def main() -> int:
    cases = {
        "attr_list 事件属性": '[x](/a){: onclick="alert(1)"}',
        "javascript 链接": "[a](javascript:alert(1))",
        "大小写/空白混淆": "[a](JaVaScRiPt:alert(1)) [b](java\nscript:alert(1))",
        "原始 HTML 事件": '<img src=x onerror=alert(1)>',
        "data URI 链接": "[a](data:text/html,<script>alert(1)</script>)",
    }
    for name, payload in cases.items():
        html = render(payload, print_view=True)
        body = sanitized(payload)
        lowered = html.lower()
        check(f"{name}：不出现事件属性", not has_event_attr(body), body[:300])
        check(f"{name}：不出现危险 scheme",
              "javascript:" not in lowered and "data:text/html" not in lowered,
              html[:300])
        check(f"{name}：不出现 script 标签", "<script" not in lowered, html[:300])

    safe = render(
        "| 指标 | 值 |\n| --- | --- |\n| 通过 | 1 |\n\n"
        "```python\nprint('ok')\n```\n\n"
        "[AITP](https://example.com/docs)",
        print_view=True,
    )
    check("表格仍正常渲染", "<table>" in safe and "<th>指标</th>" in safe, safe[:400])
    check("代码块仍正常渲染", 'class="language-python"' in safe, safe[:400])
    check("安全外链保留", 'href="https://example.com/docs"' in safe, safe[:400])
    check("打印按钮仍在", "window.print()" in safe, safe[:300])

    plain = render('<b>加粗</b><script>alert(1)</script>')
    check("原始 HTML 被转义", "&lt;b&gt;" in plain and "<script" not in plain.lower(),
          plain[:300])

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
