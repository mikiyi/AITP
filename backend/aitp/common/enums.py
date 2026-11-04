"""业务枚举：项目/需求/用例/缺陷/知识/报告的状态与取值。"""

from __future__ import annotations

PROJECT_STATUS = ("active", "archived")

REQUIREMENT_STATUS = ("draft", "reviewing", "approved", "implemented", "rejected")

# 用例类型与编号前缀一一对应，沿用 testSKill\generate_test_cases.py 的 6 张用例表
CASE_TYPE = ("functional", "interface", "boundary", "security", "ui", "exception")

CASE_PREFIX = {
    "functional": "FT",
    "interface": "IT",
    "boundary": "BT",
    "security": "ST",
    "ui": "UIT",
    "exception": "ET",
}

# 执行状态：与 xlsx「执行状态」列取值一致（未执行/通过/失败/阻塞）
CASE_STATUS = ("not_executed", "pass", "fail", "blocked")

CASE_STATUS_LABEL = {
    "not_executed": "未执行",
    "pass": "通过",
    "fail": "失败",
    "blocked": "阻塞",
}

CASE_PRIORITY = ("P0", "P1", "P2", "P3")

CASE_SOURCE = ("manual", "ai_generated", "imported", "review")

BUG_STATUS = ("open", "assigned", "fixed", "retest", "reopened", "closed", "rejected")

BUG_SEVERITY = ("P0", "P1", "P2", "P3")

BUG_TYPE = ("functional", "interface", "boundary", "security", "ui", "exception", "performance", "other")

BUG_RELATION = ("related", "caused_by", "blocked_by")

# 知识分类直接存中文；自定义分类存 sys_knowledge_category，完整清单以接口为准
KNOWLEDGE_CATEGORY = ("通用", "测试方法", "经验总结", "踩坑记录", "会议纪要", "安全测试")

KNOWLEDGE_SOURCE = ("manual", "ai_generated", "bug", "session", "imported")

REPORT_TYPE = ("summary", "iteration", "regression", "acceptance")

REPORT_STATUS = ("draft", "published", "archived")

REPORT_SECTION = ("overview", "case", "bug", "coverage", "risk", "conclusion")

AI_TASK_STATUS = ("pending", "running", "success", "failed", "cancelled")

AI_TASK_TYPE = (
    "case_generation",
    "case_review",
    "requirement_confirm",
    "bug_analysis",
    "bug_classification",
    "report_generation",
    "knowledge_qa",
    "general_chat",
    "automation_script",
    "performance_script",
    "test_data_generation",
)

# 缺陷状态机：允许的流转
BUG_TRANSITIONS: dict[str, tuple[str, ...]] = {
    "open": ("assigned", "fixed", "rejected", "closed"),
    "assigned": ("open", "fixed", "rejected", "closed"),
    "fixed": ("retest", "reopened", "closed"),
    "retest": ("fixed", "closed", "reopened"),
    "reopened": ("assigned", "fixed", "closed"),
    "closed": ("reopened",),
    "rejected": ("open", "closed"),
}

# 处于这些状态的缺陷算「未闭环」
BUG_OPEN_STATUSES = ("open", "assigned", "fixed", "retest", "reopened")
