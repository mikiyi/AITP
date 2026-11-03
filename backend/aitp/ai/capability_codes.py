"""AI 能力编号目录。

编号在功能点能力绑定、智能路由、前端能力卡片三处共用，
放在一处避免各端各写一份导致对不上。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

CAP_REQUIREMENT_REVIEW = "requirement_review"
CAP_TESTPOINT_GENERATION = "testpoint_generation"
CAP_CASE_GENERATION = "case_generation"
CAP_XMIND_TO_CASE = "xmind_to_case"
CAP_BUG_ANALYSIS = "bug_analysis"
CAP_LOG_ANALYSIS = "log_analysis"
CAP_SQL_ANALYSIS = "sql_analysis"
CAP_REGRESSION_GENERATION = "regression_generation"
CAP_REPORT_GENERATION = "report_generation"
CAP_PROJECT_PROGRESS_BOARD = "project_progress_board"

CAP_KNOWLEDGE_QA = "knowledge_qa"
CAP_PROMPT_OPTIMIZE = "prompt_optimize"

# 输出格式
OUTPUT_MARKDOWN = "markdown"
OUTPUT_JSON = "json"


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    code: str
    label: str
    goal: str
    keywords: tuple[str, ...] = field(default_factory=tuple)
    prompt_code: str = ""
    variables: tuple[str, ...] = field(default_factory=tuple)
    output_format: str = OUTPUT_MARKDOWN
    mindmap: bool = False
    adoptable: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "label": self.label,
            "goal": self.goal,
            "keywords": list(self.keywords),
            "promptCode": self.prompt_code,
            "variables": list(self.variables),
            "outputFormat": self.output_format,
            "mindmap": self.mindmap,
            "adoptable": self.adoptable,
        }


CAPABILITIES: dict[str, CapabilitySpec] = {
    CAP_REQUIREMENT_REVIEW: CapabilitySpec(
        CAP_REQUIREMENT_REVIEW, "需求评审", "评审需求描述的完整性、可测性与歧义",
        ("需求", "评审", "验收标准", "prd", "requirement", "需求分析", "需求澄清"),
        prompt_code="requirement-review",
        variables=("requirement", "projectName"),
    ),
    CAP_TESTPOINT_GENERATION: CapabilitySpec(
        CAP_TESTPOINT_GENERATION, "测试点脑图", "从需求拆出测试点并输出脑图大纲",
        ("测试点", "脑图", "大纲", "测试思路", "testpoint", "测试场景", "测试维度"),
        prompt_code="testpoint-generation",
        variables=("requirement", "projectName", "moduleCount"),
        output_format=OUTPUT_JSON,
        mindmap=True,
    ),
    CAP_CASE_GENERATION: CapabilitySpec(
        CAP_CASE_GENERATION, "用例生成", "生成可执行的测试用例",
        ("用例", "testcase", "测试用例", "case", "步骤", "预期结果", "生成用例", "用例设计"),
        prompt_code="case-generation",
        variables=("requirement", "caseTypes", "count", "projectName"),
        output_format=OUTPUT_JSON,
        adoptable=True,
    ),
    CAP_XMIND_TO_CASE: CapabilitySpec(
        CAP_XMIND_TO_CASE, "XMind 转用例", "把 xmind 大纲转成测试用例",
        ("xmind", "脑图转", "大纲转", "mindmap", "xmind文件"),
        prompt_code="xmind-to-case",
        variables=("xmindOutline", "caseTypes", "projectName"),
        output_format=OUTPUT_JSON,
        adoptable=True,
    ),
    CAP_BUG_ANALYSIS: CapabilitySpec(
        CAP_BUG_ANALYSIS, "Bug 分析", "定位缺陷根因并给出修复建议",
        ("bug", "缺陷", "报错", "异常", "复现", "根因", "defect",
         "失败", "不对", "错误", "翻倍", "重复", "崩溃", "无法"),
        prompt_code="bug-analysis",
        variables=(
            "bugTitle", "bugDescription", "reproSteps",
            "expectedResult", "actualResult", "env", "version",
        ),
    ),
    CAP_LOG_ANALYSIS: CapabilitySpec(
        CAP_LOG_ANALYSIS, "日志排查", "从日志中定位异常原因",
        ("日志", "log", "堆栈", "stacktrace", "排查", "traceback", "exception", "报错信息"),
        prompt_code="log-analysis",
        variables=("logContent", "errorType", "context"),
    ),
    CAP_SQL_ANALYSIS: CapabilitySpec(
        CAP_SQL_ANALYSIS, "SQL 分析", "分析 SQL 正确性、性能与风险",
        ("sql", "查询", "索引", "慢查询", "数据库", "表结构", "查询语句"),
        prompt_code="sql-analysis",
        variables=("sqlContent", "schemaDesc", "intent"),
    ),
    CAP_REGRESSION_GENERATION: CapabilitySpec(
        CAP_REGRESSION_GENERATION, "回归清单", "按变更范围生成回归测试清单",
        ("回归", "regression", "变更影响", "回归范围", "影响范围", "回归测试"),
        prompt_code="regression-generation",
        variables=("changeScope", "affectedModules", "projectName"),
        output_format=OUTPUT_JSON,
        adoptable=True,
    ),
    CAP_REPORT_GENERATION: CapabilitySpec(
        CAP_REPORT_GENERATION, "报告生成", "生成测试报告",
        ("报告", "report", "测试总结", "质量报告", "周报", "月报"),
        prompt_code="report-generation",
        variables=(
            "projectName", "reportType", "periodStart", "periodEnd",
            "metricsJson", "caseSummary", "bugSummary",
        ),
    ),
    CAP_PROJECT_PROGRESS_BOARD: CapabilitySpec(
        CAP_PROJECT_PROGRESS_BOARD, "进度看板", "生成项目进度看板",
        ("进度", "看板", "里程碑", "progress", "board", "周进度", "项目进展"),
        prompt_code="project-progress-board",
        variables=("projectName", "progressData", "period", "milestones"),
    ),
    CAP_KNOWLEDGE_QA: CapabilitySpec(
        CAP_KNOWLEDGE_QA, "知识问答", "基于知识库检索并回答",
        ("知识", "问答", "查一下", "kb"),
        prompt_code="knowledge-qa",
        variables=("question", "context"),
    ),
    CAP_PROMPT_OPTIMIZE: CapabilitySpec(
        CAP_PROMPT_OPTIMIZE, "提示词优化", "改写用户输入为更清晰的提示词",
        ("优化", "改写", "润色", "prompt"),
        prompt_code="prompt-optimizer",
        variables=("capability", "capabilityGoal", "rawText"),
    ),
}

CAPABILITY_CODES: tuple[str, ...] = tuple(CAPABILITIES.keys())

#: 可以挂到功能点上的能力（提示词优化是工具，不占功能点能力位）
BINDABLE_CAPABILITIES: tuple[str, ...] = (
    CAP_REQUIREMENT_REVIEW, CAP_TESTPOINT_GENERATION, CAP_CASE_GENERATION,
    CAP_XMIND_TO_CASE, CAP_BUG_ANALYSIS, CAP_LOG_ANALYSIS, CAP_SQL_ANALYSIS,
    CAP_REGRESSION_GENERATION, CAP_REPORT_GENERATION, CAP_PROJECT_PROGRESS_BOARD,
    CAP_KNOWLEDGE_QA,
)

#: AI 实验室首页展示的 10 个主能力
LAB_CAPABILITIES: tuple[str, ...] = (
    CAP_REQUIREMENT_REVIEW, CAP_TESTPOINT_GENERATION, CAP_CASE_GENERATION,
    CAP_XMIND_TO_CASE, CAP_BUG_ANALYSIS, CAP_LOG_ANALYSIS, CAP_SQL_ANALYSIS,
    CAP_REGRESSION_GENERATION, CAP_REPORT_GENERATION, CAP_PROJECT_PROGRESS_BOARD,
)


def is_known(capability: str) -> bool:
    return capability in CAPABILITIES


def label_of(capability: str) -> str:
    spec = CAPABILITIES.get(capability)
    return spec.label if spec else capability


def spec_of(capability: str) -> CapabilitySpec | None:
    return CAPABILITIES.get(capability)
