"""提示词库校验：模板可解析、变量齐全、渲染无残留、能力与模板对齐。

  python backend/scripts/check_prompts.py

退出码 0 = 全部通过。
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.capability_codes import CAPABILITIES, LAB_CAPABILITIES  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.prompts.registry import PromptRegistry  # noqa: E402

PROMPTS_DIR = ROOT / "backend" / "prompts"

#: 每个能力的样例变量，用于验证渲染链路
SAMPLES: dict[str, dict[str, Any]] = {
    "requirement-review": {"requirement": "手机号+验证码登录，验证码 5 分钟有效", "projectName": "示例电商"},
    "testpoint-generation": {"requirement": "手机号+验证码登录", "projectName": "示例电商", "moduleCount": "3"},
    "case-generation": {"requirement": "手机号+验证码登录", "projectName": "示例电商", "caseTypes": "functional", "count": "5"},
    "xmind-to-case": {"xmindOutline": "登录\n  验证码\n    超时", "projectName": "示例电商", "caseTypes": "functional"},
    "bug-analysis": {
        "bugTitle": "支付回调金额翻倍", "bugDescription": "重复推送导致金额翻倍",
        "reproSteps": "1. 下单 2. 重复回调", "expectedResult": "金额只扣一次",
        "actualResult": "金额扣两次", "env": "测试环境", "version": "v1.2.0",
    },
    "log-analysis": {"logContent": "NullPointerException at OrderService:88", "errorType": "NPE", "context": "支付"},
    "sql-analysis": {"sqlContent": "SELECT * FROM orders", "schemaDesc": "orders(id, amount)", "intent": "查订单"},
    "regression-generation": {"changeScope": "支付回调改造", "affectedModules": "交易/支付", "projectName": "示例电商"},
    "report-generation": {
        "projectName": "示例电商", "reportType": "迭代", "periodStart": "2026-10-01",
        "periodEnd": "2026-10-31", "metricsJson": "{}", "caseSummary": "10 条", "bugSummary": "2 个",
    },
    "project-progress-board": {
        "projectName": "示例电商", "progressData": "完成 60%", "period": "第 3 周", "milestones": "登录上线",
    },
    "knowledge-qa": {"question": "登录失败怎么排查", "context": "[K1] 验证码 5 分钟有效"},
    "prompt-optimizer": {"capability": "用例生成", "capabilityGoal": "生成用例", "rawText": "写点用例"},
    "intent-router": {"rawText": "支付回调重复推送", "capabilityCatalog": "- bug_analysis"},
    "module-router": {"rawText": "支付回调重复推送", "moduleCatalog": "- id=1｜示例电商/交易/支付"},
    "case-review": {"requirement": "登录", "criteria": "覆盖率", "cases": "[]"},
    "bug-classification": {"bugTitle": "空指针", "bugDescription": "NPE", "modulePath": "交易/支付"},
    "requirement-clarify": {"projectName": "示例电商", "requirement": "下单"},
    "test-plan": {"projectName": "示例电商", "period": "第 3 周", "requirementSummary": "登录下单", "modules": "登录"},
    "api-test-design": {"projectName": "示例电商", "baseUrl": "http://127.0.0.1/api", "apiDescription": "下单接口"},
    "risk-assessment": {"projectName": "示例电商", "changeScope": "支付改造", "metricsJson": "{}"},
}


class Checker:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0
        self.failures: list[str] = []

    def check(self, name: str, condition: bool, detail: str = "") -> bool:
        if condition:
            self.passed += 1
        else:
            self.failed += 1
            self.failures.append(f"{name}: {detail}")
        return condition

    def report(self) -> int:
        print(f"\n提示词校验: PASS={self.passed} FAIL={self.failed}")
        for item in self.failures:
            print(f"  [FAIL] {item}")
        return 1 if self.failed else 0


def main() -> int:
    parser = argparse.ArgumentParser(description="AITP 提示词库校验")
    parser.add_argument("--prompts-dir", default=str(PROMPTS_DIR))
    args = parser.parse_args()

    checker = Checker()
    registry = PromptRegistry(pathlib.Path(args.prompts_dir))

    templates = registry.list_templates()
    checker.check("模板数量", len(templates) >= 20, f"只找到 {len(templates)} 个模板")
    checker.check(
        "模板编号合法",
        all(t.code for t in templates),
        "存在空编号模板",
    )

    # 每个模板：样例变量齐全 + 渲染无残留
    for template in templates:
        sample = SAMPLES.get(template.code)
        if sample is None:
            checker.check(f"{template.code} 有样例变量", False, "缺少样例变量，无法验证渲染")
            continue
        missing = [v for v in template.variables if v not in sample]
        checker.check(
            f"{template.code} 样例覆盖变量",
            not missing,
            f"样例缺少：{'、'.join(missing)}",
        )
        try:
            rendered = registry.render(template.code, registry.fill_defaults(template.code, sample))
        except AppError as exc:
            checker.check(f"{template.code} 可渲染", False, f"{exc.code} {exc.message}")
            continue
        checker.check(
            f"{template.code} 渲染无残留",
            "{{" not in rendered and "}}" not in rendered,
            "渲染结果残留 {{}}",
        )
        checker.check(f"{template.code} 正文非空", len(rendered) > 80, f"正文过短（{len(rendered)} 字）")

    # 能力与模板对齐
    for code, spec in CAPABILITIES.items():
        checker.check(f"{code} 有提示词编号", bool(spec.prompt_code), "prompt_code 为空")
        if not spec.prompt_code:
            continue
        try:
            template = registry.get(spec.prompt_code)
        except AppError as exc:
            checker.check(f"{code} 提示词存在", False, f"{exc.message}")
            continue
        checker.check(
            f"{code} 变量与模板一致",
            set(spec.variables) == set(template.variables),
            f"能力声明 {sorted(spec.variables)} vs 模板 {sorted(template.variables)}",
        )

    # 缺必填变量 → 120102 并列出缺失名
    try:
        registry.render("requirement-review", {"projectName": "示例电商"})
        checker.check("缺变量报错", False, "未抛出 120102")
    except AppError as exc:
        checker.check(
            "缺变量报错",
            exc.code == ErrorCode.PROMPT_VAR_MISSING,
            f"错误码 {exc.code}，期望 {ErrorCode.PROMPT_VAR_MISSING}",
        )
        data = exc.data if isinstance(exc.data, dict) else {}
        checker.check(
            "缺变量列出缺失名",
            data.get("missing") == ["requirement"],
            f"missing={data.get('missing')}",
        )

    # 缺失模板 → 120101
    try:
        registry.get("not-exists-prompt")
        checker.check("缺失模板报错", False, "未抛出 120101")
    except AppError as exc:
        checker.check(
            "缺失模板报错",
            exc.code == ErrorCode.PROMPT_NOT_FOUND,
            f"错误码 {exc.code}，期望 {ErrorCode.PROMPT_NOT_FOUND}",
        )

    # 非法编号不被当成路径穿越
    try:
        registry.get("../../etc/passwd")
        checker.check("非法编号被拒", False, "未拒绝非法编号")
    except AppError as exc:
        checker.check(
            "非法编号被拒",
            exc.code == ErrorCode.PROMPT_NOT_FOUND,
            f"错误码 {exc.code}，期望 {ErrorCode.PROMPT_NOT_FOUND}",
        )

    # 10 个实验室能力都有模板
    for code in LAB_CAPABILITIES:
        spec = CAPABILITIES[code]
        checker.check(f"实验室能力 {code} 有模板", bool(spec.prompt_code), "缺少提示词编号")

    return checker.report()


if __name__ == "__main__":
    raise SystemExit(main())
