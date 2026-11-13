"""提示词库校验：模板可解析、变量齐全、渲染无残留、能力与模板对齐、使用次数有来源。

  python backend/scripts/check_prompts.py

退出码 0 = 全部通过。
"""

from __future__ import annotations

import argparse
import pathlib
import sqlite3
import sys
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.capability_codes import CAPABILITIES, LAB_CAPABILITIES  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.prompts.registry import PromptRegistry  # noqa: E402
from aitp.services import prompt_usage  # noqa: E402

PROMPTS_DIR = ROOT / "backend" / "prompts"

#: GAP-27 上下文占位的样例取值：模拟装配器注入的条目文本（门禁只验渲染链路，不查库）
CTX_MODULES = "- moduleId=12｜支付回调：完整路径：交易/支付/回调"
CTX_CASES = "- TC-0007｜验证码超时-重发-成功：类型：functional｜优先级：P1"
CTX_BUGS = "- BUG-0031｜支付回调金额翻倍：严重级：major｜状态：fixed"
CTX_KNOWLEDGE = "- K:5｜支付回调幂等要点：分类：踩坑记录｜重复推送需按订单号幂等"


def _ctx(*kinds: str) -> dict[str, str]:
    mapping = {
        "modules": CTX_MODULES,
        "cases": CTX_CASES,
        "bugs": CTX_BUGS,
        "knowledge": CTX_KNOWLEDGE,
    }
    return {f"context{name.capitalize()}": mapping[name] for name in kinds}


#: 每个能力的样例变量，用于验证渲染链路
SAMPLES: dict[str, dict[str, Any]] = {
    "requirement-confirm": {"requirement": "手机号+验证码登录，验证码 5 分钟有效", "projectName": "示例电商",
                            **_ctx("modules", "knowledge")},
    "testpoint-generation": {"requirement": "手机号+验证码登录", "projectName": "示例电商", "moduleCount": "3",
                            "outputTemplate": "{\"name\": \"登录\", \"children\": []}",
                            **_ctx("modules", "cases", "knowledge")},
    "case-generation": {"requirement": "手机号+验证码登录", "projectName": "示例电商", "caseTypes": "functional", "count": "5",
                      "outputTemplate": "| 用例编号 | 用例标题 | 优先级 |",
                      **_ctx("modules", "cases", "bugs", "knowledge")},
    "xmind-to-case": {"xmindOutline": "登录\n  验证码\n    超时", "projectName": "示例电商", "caseTypes": "functional",
                      "outputTemplate": "| 用例编号 | 用例标题 | 优先级 |", **_ctx("modules", "cases")},
    "bug-analysis": {
        "bugTitle": "支付回调金额翻倍", "bugDescription": "重复推送导致金额翻倍",
        "reproSteps": "1. 下单 2. 重复回调", "expectedResult": "金额只扣一次",
        "actualResult": "金额扣两次", "env": "测试环境", "version": "v1.2.0",
        **_ctx("modules", "bugs", "knowledge"),
    },
    "log-analysis": {"logContent": "NullPointerException at OrderService:88", "errorType": "NPE", "context": "支付"},
    "sql-analysis": {"sqlContent": "SELECT * FROM orders", "schemaDesc": "orders(id, amount)", "intent": "查订单"},
    "regression-generation": {"changeScope": "支付回调改造", "affectedModules": "交易/支付", "projectName": "示例电商",
                              **_ctx("modules", "cases", "bugs")},
    "report-generation": {
        "projectName": "示例电商", "reportType": "迭代", "periodStart": "2026-10-01",
        "periodEnd": "2026-10-31", "metricsJson": "{}", "caseSummary": "10 条", "bugSummary": "2 个",
        "outputTemplate": "## 测试概述\n## 执行情况",
    },
    "project-progress-board": {
        "projectName": "示例电商", "progressData": "完成 60%", "period": "第 3 周", "milestones": "登录上线",
    },
    "knowledge-assistant": {"question": "登录失败怎么排查", "context": "[K1] 验证码 5 分钟有效"},
    "knowledge-organize": {
        "articles": "[A1] 标题：支付回调重复推送\n分类：踩坑记录\n正文：…",
        "categories": "通用、测试方法、踩坑记录",
    },
    "prompt-optimizer": {"capability": "用例生成", "capabilityGoal": "生成用例", "rawText": "写点用例"},
    "intent-router": {"rawText": "支付回调重复推送", "capabilityCatalog": "- bug_analysis"},
    "module-router": {"rawText": "支付回调重复推送", "moduleCatalog": "- id=1｜示例电商/交易/支付"},
    "case-review": {"requirement": "登录", "criteria": "覆盖率", "cases": "[]"},
    "bug-classification": {"bugTitle": "空指针", "bugDescription": "NPE", "modulePath": "交易/支付"},
    "test-plan": {"projectName": "示例电商", "period": "第 3 周", "requirementSummary": "登录下单", "modules": "登录"},
    "api-test-design": {"projectName": "示例电商", "baseUrl": "http://127.0.0.1/api", "apiDescription": "下单接口",
                       "outputTemplate": "## 接口概览\n## 用例清单"},
    "risk-assessment": {"projectName": "示例电商", "changeScope": "支付改造", "metricsJson": "{}"},
    "automation-script": {
        "projectName": "示例电商", "targetType": "web", "framework": "selenium", "language": "python",
        "appPlatforms": "无", "scenario": "登录后查询订单列表",
        "baseUrl": "http://127.0.0.1", "extraRequirements": "失败截图",
    },
    "performance-script": {
        "projectName": "示例电商", "scenario": "下单接口压测", "baseUrl": "http://127.0.0.1",
        "threads": "20", "duration": "60", "rampUp": "10",
        "dataFile": "无", "extraRequirements": "无",
    },
    "test-data-generation": {
        "projectName": "示例电商", "dialect": "MySQL 8.0",
        "intent": "给订单表造一批数据，覆盖三种订单状态和金额为 0 的边界值",
        "schemaDesc": "orders(id, user_id, amount, status, created_at)", "rowCount": "20",
    },
    "exploratory-web": {
        "projectName": "示例电商", "targetUrl": "http://127.0.0.1:8760/",
        "maxSteps": "20", "maxSeconds": "180",
    },
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
        registry.render("requirement-confirm", {"projectName": "示例电商"})
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

    # 实验室能力都有模板
    for code in LAB_CAPABILITIES:
        spec = CAPABILITIES[code]
        checker.check(f"实验室能力 {code} 有模板", bool(spec.prompt_code), "缺少提示词编号")

    # 「用过 N 次」口径：每张卡片都得有一个计数来源
    #   绑定能力的模板由 biz_ai_record 统计，幕后调用的（路由/问答助手/自动整理）
    #   由 sys_prompt_usage 统计；两边都不沾的模板会永远显示 0 次。
    bound = {spec.prompt_code for spec in CAPABILITIES.values() if spec.prompt_code}
    for template in templates:
        checker.check(
            f"{template.code} 使用次数有来源",
            template.code in bound or template.code in prompt_usage.TRACKED,
            "既没绑定能力，也不在内部调用计数清单里，卡片会永远显示 0 次",
        )
    checker.check(
        "内部计数清单不与能力重叠",
        not (set(prompt_usage.TRACKED) & bound),
        f"重叠：{sorted(set(prompt_usage.TRACKED) & bound)}",
    )

    # 内部计数的读写：累加与白名单过滤
    usage_sql = next(
        (ROOT / "backend" / "aitp" / "db" / "migrations").glob("*prompt_usage.sql"), None
    )
    checker.check("内部计数迁移存在", usage_sql is not None, "找不到 sys_prompt_usage 迁移")
    if usage_sql is not None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(usage_sql.read_text(encoding="utf-8"))
        prompt_usage.record(conn, "knowledge-assistant")
        prompt_usage.record(conn, "knowledge-assistant")
        prompt_usage.record(conn, "capability-prompt-not-tracked")
        usage_counts = prompt_usage.counts(conn)
        checker.check(
            "内部调用计数累加",
            usage_counts.get("knowledge-assistant") == 2,
            f"{usage_counts}",
        )
        checker.check(
            "清单外的模板不计数",
            "capability-prompt-not-tracked" not in usage_counts,
            f"{usage_counts}",
        )
        conn.close()

    return checker.report()


if __name__ == "__main__":
    raise SystemExit(main())
