"""导出能力门禁：能力卡片宣称的导出格式必须与后端真实行为一致（GAP-37）。

风险来源有两处：
  1. 导出白名单在 `ai/capability_codes.py`（渲染卡片按钮）和 `services/output_exporter.py`
     （真的落文件）各写一份，抄漏一条就变成「卡片说能导、后端报不支持」；
  2. 帮助页用「能力产出 Word / Excel」这种笼统说法，把不支持的能力也算进去了。

所以这里既比口径，也真调用一次导出函数，并解析帮助页文案。

    python backend/scripts/check_export_caps.py
"""

from __future__ import annotations

import importlib.util
import pathlib
import re
import sys
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai import capability_codes  # noqa: E402
from aitp.errors import AppError  # noqa: E402
from aitp.services import output_exporter  # noqa: E402

HELP_TS = ROOT / "frontend" / "src" / "views" / "settings" / "productHelpContent.ts"

#: 「用例生成」「XMind 转用例」可导出 Excel —— 只吃紧邻的引号串，避免把前文也扫进来
CLAIM_RE = re.compile(r"((?:「[^」]+」)+)\s*可导出\s*(Excel|Word)")
QUOTE_RE = re.compile(r"「([^」]+)」")

#: 用例类产出的真实形状是英文键 + title 必填（output_exporter._try_parse_case_json）
CASE_JSON = (
    '[{"caseNo":"TC-001","title":"手机号验证码登录","caseType":"functional",'
    '"priority":"P0","module":"登录","preconditions":"账号已注册",'
    '"steps":[{"action":"输入正确验证码","expected":"登录成功"}],"tags":"登录"}]'
)
MARKDOWN = "## 结论\n\n整体通过。\n\n| 模块 | 通过率 |\n| --- | --- |\n| 登录 | 100% |\n"

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


def call(func: Any, capability: str, output: str) -> tuple[bool, str, int]:
    """跑一次导出，返回 (是否成功, 中文降级说明, 行数)。"""
    try:
        content, count = func(capability, output)
    except AppError as exc:
        return False, str(exc.message), 0
    except Exception as exc:  # noqa: BLE001 门禁要把非预期异常也算成失败并留证据
        return False, f"非预期异常 {type(exc).__name__}: {exc}", 0
    if not content or not content.startswith(b"PK"):
        return False, f"产物不是有效文件（前缀 {content[:4]!r}）", 0
    return True, "", int(count)


def claimed_labels(code: str, feature: str) -> bool:
    spec = capability_codes.CAPABILITIES[code]
    return feature in [item["label"] for item in capability_codes.features_of(code, spec)]


def help_claims(text: str) -> dict[str, set[str]]:
    claims: dict[str, set[str]] = {"Excel": set(), "Word": set()}
    for run, fmt in CLAIM_RE.findall(text):
        claims[fmt] |= set(QUOTE_RE.findall(run))
    return claims


def main() -> int:
    check(
        "Excel 白名单与卡片同源",
        output_exporter.EXPORTABLE_CAPABILITIES is capability_codes.EXPORT_EXCEL_CAPABILITIES,
        f"{output_exporter.EXPORTABLE_CAPABILITIES} != {capability_codes.EXPORT_EXCEL_CAPABILITIES}",
    )
    check(
        "Word 白名单与卡片同源",
        output_exporter.EXPORT_WORD_CAPABILITIES is capability_codes.EXPORT_WORD_CAPABILITIES,
        f"{output_exporter.EXPORT_WORD_CAPABILITIES} != {capability_codes.EXPORT_WORD_CAPABILITIES}",
    )

    docx_installed = importlib.util.find_spec("docx") is not None
    mismatches: list[str] = []
    for code in capability_codes.CAPABILITY_CODES:
        label = capability_codes.label_of(code)
        output = CASE_JSON if code in output_exporter.EXPORT_CASE_CAPABILITIES else MARKDOWN

        excel_ok, excel_msg, excel_rows = call(output_exporter.export_output, code, output)
        if claimed_labels(code, "导出 Excel"):
            if not (excel_ok and excel_rows > 0):
                mismatches.append(f"{label} 卡片有导出 Excel 但导出失败：{excel_msg}")
        elif not excel_msg:
            mismatches.append(f"{label} 卡片没有导出 Excel 却导出成功")
        elif "暂不支持导出 Excel" not in excel_msg:
            mismatches.append(f"{label} 拒绝导出 Excel 但没给中文降级说明：{excel_msg}")

        word_ok, word_msg, word_rows = call(output_exporter.export_docx, code, output)
        if claimed_labels(code, "导出 Word"):
            if docx_installed and not (word_ok and word_rows > 0):
                mismatches.append(f"{label} 卡片有导出 Word 但导出失败：{word_msg}")
            if not docx_installed and "本机缺少 python-docx" not in word_msg:
                mismatches.append(f"{label} 缺依赖时应给中文说明：{word_msg}")
        elif not word_msg:
            mismatches.append(f"{label} 卡片没有导出 Word 却导出成功")
        elif "暂不支持导出 Word" not in word_msg:
            mismatches.append(f"{label} 拒绝导出 Word 但没给中文降级说明：{word_msg}")

    check("逐能力核对卡片宣称与真实导出行为", not mismatches, "; ".join(mismatches[:6]))
    check("宣称支持导出的能力确实有", any(
        claimed_labels(code, "导出 Excel") for code in capability_codes.CAPABILITY_CODES
    ))
    if not docx_installed:
        print("[NOTE] 本机未安装 python-docx，Word 成功路径以降级说明校验")

    help_text = HELP_TS.read_text(encoding="utf-8")
    claims = help_claims(help_text)
    excel_labels = {capability_codes.label_of(c) for c in capability_codes.EXPORT_EXCEL_CAPABILITIES}
    word_labels = {capability_codes.label_of(c) for c in capability_codes.EXPORT_WORD_CAPABILITIES}
    check("帮助页 Excel 宣称 == 后端口径", claims["Excel"] == excel_labels,
          f"文案={sorted(claims['Excel'])} 实际={sorted(excel_labels)}")
    check("帮助页 Word 宣称 == 后端口径", claims["Word"] == word_labels,
          f"文案={sorted(claims['Word'])} 实际={sorted(word_labels)}")
    check("帮助页不再笼统宣称「能力产出 Word / Excel」",
          "能力产出 Word / Excel" not in help_text)
    # 正向对照：解析规则必须真能抓到宣称，否则上面两条比对是空转
    check("解析规则能命中虚假宣称",
          help_claims("「缺陷分析」「测试计划」可导出 Excel，「缺陷分析」可导出 Word")
          == {"Excel": {"缺陷分析", "测试计划"}, "Word": {"缺陷分析"}})

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
