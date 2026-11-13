"""智能路由回归：18 张能力卡片每张至少一条代表性输入，必须匹配到正确能力。

    python backend/scripts/check_routing.py

只测纯函数（关键词打分 + AI/关键词融合），不依赖模型服务，结果稳定可复现。
覆盖三类历史问题：
- 本地小模型固定回复把关键词命中挤掉；
- 引号里的描述被当成意图；
- 冒号后的处理对象抢走指令段的权重。

退出码 0 = 全部通过。
"""

from __future__ import annotations

import sys
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.capability_codes import CAPABILITIES, LAB_CAPABILITIES  # noqa: E402
from aitp.services.lab_service import (  # noqa: E402
    fuse_capability_routes,
    score_capabilities,
)

#: 每张卡片一条最贴近真实用法的输入
CASES: tuple[tuple[str, str], ...] = (
    ("requirement_confirm", "帮我评审一下登录需求是否清晰可测"),
    ("testpoint_generation", "帮我输出一版登录功能的测试点脑图"),
    ("case_generation", "帮我生成一批登录模块的测试用例"),
    ("xmind_to_case", "把这个 xmind 大纲转成用例"),
    ("bug_analysis", "支付回调重复推送导致金额翻倍，帮我分析根因"),
    ("log_analysis", "帮我排查这段日志里的异常"),
    ("sql_analysis", "分析这条慢查询为什么没走索引"),
    ("regression_generation", "整理下这周的回归清单"),
    ("report_generation", "生成一份本迭代的测试报告"),
    ("project_progress_board", "看看这个项目的进度和里程碑"),
    ("api_test_design", "给 /api/orders 接口做接口测试设计"),
    ("test_plan", "帮我制定这个版本的测试计划"),
    ("case_review", "评审一下这批用例的覆盖度"),
    ("bug_classification", "这个缺陷归类到哪个模块"),
    ("risk_assessment", "评估一下这次发布的上线风险"),
    ("automation_script", "我给网页版微信写一个web自动化的demo脚本"),
    ("performance_script", "我给微信写一个发朋友圈的性能测试的demo脚本"),
    ("test_data_generation", "帮我造一批测试数据，假数据要覆盖订单状态的边界值和异常组合"),
    ("exploratory_web", "对这个站点做 Web 探索性测试，发现的异常要留证据包交人工审核"),
    ("prompt_optimize", "帮我对下来语句进行“从需求拆解测试点，输出 JSON 树用于渲染脑图”提示词优化"),
)


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
        print(f"\n路由回归: PASS={self.passed} FAIL={self.failed}")
        for item in self.failures:
            print(f"  [FAIL] {item}")
        return 1 if self.failed else 0


def _top(text: str) -> tuple[str, float]:
    routes = score_capabilities(text, top_k=4)
    if not routes:
        return "", 0.0
    return str(routes[0]["capability"]), float(routes[0]["score"])


def check_coverage(checker: Checker) -> None:
    cards = set(LAB_CAPABILITIES)
    covered = {code for code, _ in CASES}
    checker.check("能力卡片数量", len(LAB_CAPABILITIES) == 20, f"实际 {len(LAB_CAPABILITIES)} 张")
    checker.check(
        "用例覆盖全部卡片",
        cards == covered,
        f"缺少 {sorted(cards - covered)}；多出 {sorted(covered - cards)}",
    )
    checker.check(
        "用例中的能力都存在",
        all(code in CAPABILITIES for code, _ in CASES),
        str([code for code, _ in CASES if code not in CAPABILITIES]),
    )


def check_cases(checker: Checker) -> None:
    for expected, text in CASES:
        routes = score_capabilities(text, top_k=4)
        checker.check(f"{expected} 有候选", bool(routes), f"输入：{text}")
        if not routes:
            continue
        top = routes[0]
        checker.check(
            f"{expected} 排第一",
            top.get("capability") == expected,
            f"输入「{text}」实际第一：{top.get('capability')}（{top.get('score')}）",
        )
        checker.check(
            f"{expected} 置信度足够",
            float(top.get("score") or 0) >= 0.7,
            f"输入「{text}」分数 {top.get('score')}",
        )
        checker.check(
            f"{expected} 带命中理由",
            bool(top.get("reason")) and top.get("source") == "fallback",
            f"reason={top.get('reason')} source={top.get('source')}",
        )


def check_fusion(checker: Checker) -> None:
    """模拟本地小模型：固定返回三条 0.95 的无关候选。"""
    ai_routes = [
        {"capability": "requirement_confirm", "label": "需求确认", "score": 0.95, "reason": "AI 判断"},
        {"capability": "testpoint_generation", "label": "测试点脑图", "score": 0.95, "reason": "AI 判断"},
        {"capability": "case_generation", "label": "用例生成", "score": 0.95, "reason": "AI 判断"},
    ]
    for expected, text in (
        ("automation_script", "我给网页版微信写一个web自动化的demo脚本"),
        ("performance_script", "我给微信写一个发朋友圈的性能测试的demo脚本"),
        ("prompt_optimize", "帮我对下来语句进行“从需求拆解测试点，输出 JSON 树用于渲染脑图”提示词优化"),
    ):
        keyword_routes = score_capabilities(text, top_k=6)
        fused = fuse_capability_routes(ai_routes, keyword_routes, top_k=3)
        checker.check(
            f"融合后 {expected} 仍排第一",
            bool(fused) and fused[0].get("capability") == expected,
            f"实际：{[(item.get('capability'), item.get('score')) for item in fused]}",
        )
        checker.check(
            f"融合后 {expected} 标记来源",
            bool(fused) and fused[0].get("source") in ("ai", "ai+fallback", "fallback"),
            str(fused[0] if fused else None),
        )

    # 没有关键词命中时，AI 候选仍然保留
    fused = fuse_capability_routes(ai_routes, score_capabilities("帮我看看这个", 6), top_k=3)
    checker.check("无关键词时保留 AI 候选", len(fused) == 3, str(len(fused)))
    checker.check(
        "无关键词时来源仍为 ai",
        all(item.get("source") == "ai" for item in fused),
        str([item.get("source") for item in fused]),
    )


def check_regressions(checker: Checker) -> None:
    # 引号里的描述只是处理对象，不能抢走意图
    text = "帮我对下来语句进行“从需求拆解测试点，输出 JSON 树用于渲染脑图”提示词优化"
    routes = {item["capability"]: item["score"] for item in score_capabilities(text, 6)}
    checker.check(
        "引号内关键词降权",
        routes.get("prompt_optimize", 0) > routes.get("testpoint_generation", 0),
        f"提示词优化={routes.get('prompt_optimize')} 测试点脑图={routes.get('testpoint_generation')}",
    )

    # 冒号后是处理对象，指令段优先
    top, score = _top("帮我优化一下这段提示词：支付回调重复推送导致金额翻倍怎么办")
    checker.check("冒号后内容不改变意图", top == "prompt_optimize", f"实际第一：{top}（{score}）")

    # 单泛词不能压过双命中
    scored = score_capabilities("我给微信写一个发朋友圈的性能测试的demo脚本", 6)
    ranking = [item["capability"] for item in scored]
    checker.check("性能测试优先于脚本", ranking[:1] == ["performance_script"], str(ranking))

    # 能力名在正文中视为强命中
    top, _ = _top("帮我把这条缺陷归一下类")
    checker.check("通俗说法仍命中相关能力", top in ("bug_classification", "bug_analysis"), f"实际：{top}")


def main() -> int:
    checker = Checker()
    check_coverage(checker)
    check_cases(checker)
    check_fusion(checker)
    check_regressions(checker)
    return checker.report()


if __name__ == "__main__":
    raise SystemExit(main())
