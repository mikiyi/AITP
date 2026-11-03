"""AI 行为安全规则。

这些规则会随每次 AI 调用一起下发，并在产出物上做人工复核标记。
目的：AI 只做辅助，不替人做结论。
"""

from __future__ import annotations

import re

SAFETY_RULES = """
## 行为安全规则（必须遵守）
1. **证据不足要明说**：所给信息不足以判断时，必须写「现有信息不足，无法判断」并列出还需要哪些信息；禁止用推测填补空白。
2. **禁止编造**：不得编造不存在的知识库标题、缺陷编号、用例编号、接口路径或历史数据。缺陷编号由系统统一分配，只能给出「建议标题」和「建议严重级别」。
3. **不做上线结论**：不得给出「可以上线」「建议发布」「已通过验收」之类的批准性结论；只能输出「风险点 / 待人工确认项」。
4. **高风险必须人工复核**：涉及 P0/P1 缺陷、核心资金链路、权限与数据安全、或提示词（Prompt）测试结果时，必须在结论末尾追加「【需人工复核】」并说明复核要点。
5. **数据边界**：若输入疑似包含手机号、身份证、账号、生产日志或内部 SQL，请先提示用户改用本地模型或自行脱敏，再继续处理。
6. **不给通过/不通过**：除上述「需人工复核」标记外，不得输出代替人工决策的通过/不通过结论。
""".strip()

REVIEW_MARK = "【需人工复核】"

_P0_P1_PATTERN = re.compile(r"(P0|P1)\b")
_PROMPT_TEST_PATTERN = re.compile(r"(提示词|prompt|指令)\s*(测试|回归)", re.IGNORECASE)


def needs_human_review(text: str) -> bool:
    """判断产出物是否涉及 P0/P1 或 Prompt 测试结果，需要人工复核。"""
    if not text:
        return False
    return bool(_P0_P1_PATTERN.search(text) or _PROMPT_TEST_PATTERN.search(text))


def mark_review_required(text: str) -> str:
    """在产出物末尾追加人工复核标记（已存在则不重复追加）。"""
    if not text or REVIEW_MARK in text:
        return text
    return f"{text.rstrip()}\n\n{REVIEW_MARK}以上结论由 AI 辅助生成，请人工复核后再采纳。"


def build_safety_prompt(extra: str = "") -> str:
    """把安全规则拼进系统提示词。"""
    parts = [SAFETY_RULES]
    if extra:
        parts.append(extra.strip())
    return "\n\n".join(parts)
