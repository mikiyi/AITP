"""AI 能力编号目录。

编号在功能点能力绑定、智能路由、前端能力卡片三处共用，
放在一处避免各端各写一份导致对不上。
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any

CAP_REQUIREMENT_CONFIRM = "requirement_confirm"
CAP_TESTPOINT_GENERATION = "testpoint_generation"
CAP_CASE_GENERATION = "case_generation"
CAP_XMIND_TO_CASE = "xmind_to_case"
CAP_BUG_ANALYSIS = "bug_analysis"
CAP_LOG_ANALYSIS = "log_analysis"
CAP_SQL_ANALYSIS = "sql_analysis"
CAP_REGRESSION_GENERATION = "regression_generation"
CAP_REPORT_GENERATION = "report_generation"
CAP_PROJECT_PROGRESS_BOARD = "project_progress_board"

CAP_PROMPT_OPTIMIZE = "prompt_optimize"

# 提示词库里已就绪、但此前未开成能力入口的提示词对应的能力
CAP_API_TEST_DESIGN = "api_test_design"
CAP_TEST_PLAN = "test_plan"
CAP_CASE_REVIEW = "case_review"
CAP_BUG_CLASSIFICATION = "bug_classification"
CAP_RISK_ASSESSMENT = "risk_assessment"

# 脚本类能力：产物是可直接运行的脚本文件
CAP_AUTOMATION_SCRIPT = "automation_script"
CAP_PERFORMANCE_SCRIPT = "performance_script"

# 测试数据生成：SQL 分支走大模型，文件分支全程本地
CAP_TEST_DATA_GENERATION = "test_data_generation"

# 顺序执行的父记录不是可执行能力，只用于把整条流水线归成一条记录
CAP_COMPOSITE = "composite"

#: 非能力入口的伪能力中文名：不能出现在能力清单里，但记录列表要显示中文
PSEUDO_CAPABILITY_LABELS: dict[str, str] = {
    CAP_COMPOSITE: "多入口顺序执行",
}

# 输出格式
OUTPUT_MARKDOWN = "markdown"
OUTPUT_JSON = "json"

#: 能力分组：core=闭环主能力，extended=提示词库里补充的能力
CATEGORY_CORE = "core"
CATEGORY_EXTENDED = "extended"

#: 支持导出 Excel 的能力（用例 JSON → 单表 / Markdown → 分节表）
EXPORT_EXCEL_CAPABILITIES = (CAP_CASE_GENERATION, CAP_XMIND_TO_CASE, CAP_API_TEST_DESIGN)
#: 支持导出 Word 的能力：用例类 + 报告
EXPORT_WORD_CAPABILITIES = (*EXPORT_EXCEL_CAPABILITIES, CAP_REPORT_GENERATION)
#: 支持导出 Xmind 脑图文件的能力（产出测试点大纲，可下载 .xmind）
EXPORT_XMIND_CAPABILITIES = (CAP_TESTPOINT_GENERATION,)
#: 支持产出物模板的能力
TEMPLATE_CAPABILITIES = (
    CAP_CASE_GENERATION, CAP_XMIND_TO_CASE, CAP_API_TEST_DESIGN,
    CAP_REPORT_GENERATION, CAP_TESTPOINT_GENERATION,
)
#: 产物可直接并入自动化资产的能力
AUTOMATION_CAPABILITIES = (CAP_AUTOMATION_SCRIPT, CAP_PERFORMANCE_SCRIPT)


#: 上下文种类（GAP-27）：能力声明需要哪几类项目内事实，
#: 具体召回与上限实现见 `services/context_assembler.py`。
CTX_MODULE = "module"
CTX_CASE = "case"
CTX_BUG = "bug"
CTX_KNOWLEDGE = "knowledge"

#: 上下文档位：与 `services/context_assembler.py` 的 TIER_* 同一套取值
TIER_LITE = "lite"
TIER_STANDARD = "standard"
TIER_FULL = "full"

#: 上下文注入提示词的变量名：与 `services/context_assembler.py` 的 VARIABLE_OF_KIND 同一套，
#: 只并入该能力实际声明的种类（没声明的种类模板里不出现，避免注入无意义的「未召回到」段落）。
#: 模板（backend/prompts/*.md）必须有同名 optional 占位，`scripts/check_prompts.py` 校验两边一致。
CONTEXT_VARIABLE_OF_KIND: dict[str, str] = {
    CTX_MODULE: "contextModules",
    CTX_CASE: "contextCases",
    CTX_BUG: "contextBugs",
    CTX_KNOWLEDGE: "contextKnowledge",
}

#: 固定顺序，保证同一声明在不同进程里得到同一变量表
CONTEXT_VARIABLES: tuple[str, ...] = (
    "contextModules", "contextCases", "contextBugs", "contextKnowledge",
)


def context_variables_of(kinds: tuple[str, ...]) -> tuple[str, ...]:
    """声明的上下文种类 → 提示词占位变量名（按 CONTEXT_VARIABLES 的顺序）。"""
    wanted = {CONTEXT_VARIABLE_OF_KIND[kind] for kind in kinds if kind in CONTEXT_VARIABLE_OF_KIND}
    return tuple(name for name in CONTEXT_VARIABLES if name in wanted)


def _with_context_variables(spec: "CapabilitySpec") -> "CapabilitySpec":
    """声明了上下文种类 → 自动带上对应占位变量，避免声明与模板两头对不上。"""
    if not spec.context_kinds:
        return spec
    extra = tuple(
        name for name in context_variables_of(tuple(spec.context_kinds))
        if name not in spec.variables
    )
    return replace(spec, variables=tuple(spec.variables) + extra)


def features_of(code: str, spec: "CapabilitySpec") -> list[dict[str, str]]:
    """卡片上展示的能力特性；只列真实支持的功能，避免文案与实际不符。"""
    items: list[dict[str, str]] = []
    if code in EXPORT_EXCEL_CAPABILITIES:
        items.append({"key": "excel", "label": "导出 Excel"})
    if code in EXPORT_WORD_CAPABILITIES:
        items.append({"key": "word", "label": "导出 Word"})
    if code in EXPORT_XMIND_CAPABILITIES:
        items.append({"key": "xmind", "label": "导出 Xmind"})
    if code in TEMPLATE_CAPABILITIES:
        items.append({"key": "template", "label": "产出物模板"})
    if spec.adoptable:
        items.append({"key": "adopt", "label": "采纳为用例"})
    if code == CAP_REPORT_GENERATION:
        items.append({"key": "adopt_report", "label": "采纳为报告"})
    if code in AUTOMATION_CAPABILITIES:
        items.append({"key": "automation", "label": "加入自动化"})
    if code == CAP_TEST_DATA_GENERATION:
        items.append({"key": "test_files", "label": "测试文件本地生成"})
    items.append({"key": "knowledge", "label": "加入知识库"})
    items.append({"key": "history", "label": "历史产出"})
    return items


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
    category: str = CATEGORY_CORE
    #: 本能力需要哪些项目上下文（GAP-27）：module/case/bug/knowledge；空 = 不装配
    context_kinds: tuple[str, ...] = field(default_factory=tuple)
    #: 上下文档位覆盖（lite/standard/full）；留空按系统配置或默认「标准」档
    context_tier: str = ""

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
            "category": self.category,
            "contextKinds": list(self.context_kinds),
            "contextTier": self.context_tier,
            "features": features_of(self.code, self),
        }


CAPABILITIES: dict[str, CapabilitySpec] = {
    CAP_REQUIREMENT_CONFIRM: CapabilitySpec(
        CAP_REQUIREMENT_CONFIRM, "需求确认", "确认需求是否清晰可测，并产出待确认问题与验收标准建议",
        ("需求", "评审", "验收标准", "prd", "requirement", "需求分析", "需求澄清",
         "确认", "待确认", "clarify", "歧义"),
        prompt_code="requirement-confirm",
        variables=("requirement", "projectName"),
        context_kinds=(CTX_MODULE, CTX_KNOWLEDGE),
    ),
    CAP_TESTPOINT_GENERATION: CapabilitySpec(
        CAP_TESTPOINT_GENERATION, "测试点脑图", "从需求拆出测试点大纲，可导出 Xmind 脑图文件",
        ("测试点", "脑图", "大纲", "测试思路", "testpoint", "测试场景", "测试维度"),
        prompt_code="testpoint-generation",
        variables=("requirement", "projectName", "moduleCount", "outputTemplate"),
        output_format=OUTPUT_JSON,
        mindmap=True,
        context_kinds=(CTX_MODULE, CTX_CASE, CTX_KNOWLEDGE),
    ),
    CAP_CASE_GENERATION: CapabilitySpec(
        CAP_CASE_GENERATION, "用例生成", "生成可执行的测试用例",
        ("用例", "testcase", "测试用例", "case", "步骤", "预期结果", "生成用例", "用例设计"),
        prompt_code="case-generation",
        variables=("requirement", "caseTypes", "count", "projectName", "outputTemplate"),
        output_format=OUTPUT_JSON,
        adoptable=True,
        context_kinds=(CTX_MODULE, CTX_CASE, CTX_BUG, CTX_KNOWLEDGE),
    ),
    CAP_XMIND_TO_CASE: CapabilitySpec(
        CAP_XMIND_TO_CASE, "XMind 转用例", "把 xmind 大纲转成测试用例",
        ("xmind", "脑图转", "大纲转", "mindmap", "xmind文件"),
        prompt_code="xmind-to-case",
        variables=("xmindOutline", "caseTypes", "projectName", "outputTemplate"),
        output_format=OUTPUT_JSON,
        adoptable=True,
        context_kinds=(CTX_MODULE, CTX_CASE),
    ),
    CAP_BUG_ANALYSIS: CapabilitySpec(
        CAP_BUG_ANALYSIS, "缺陷分析", "定位缺陷根因并给出修复建议",
        ("bug", "缺陷", "报错", "异常", "复现", "根因", "defect", "缺陷分析",
         "失败", "不对", "错误", "翻倍", "重复", "崩溃", "无法"),
        prompt_code="bug-analysis",
        variables=(
            "bugTitle", "bugDescription", "reproSteps",
            "expectedResult", "actualResult", "env", "version",
        ),
        context_kinds=(CTX_MODULE, CTX_BUG, CTX_KNOWLEDGE),
    ),
    CAP_LOG_ANALYSIS: CapabilitySpec(
        CAP_LOG_ANALYSIS, "日志排查", "从日志中定位异常原因",
        ("日志", "log", "堆栈", "stacktrace", "排查", "traceback", "exception", "报错信息", "日志分析"),
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
        ("回归", "regression", "变更影响", "回归范围", "影响范围", "回归测试", "回归清单"),
        prompt_code="regression-generation",
        variables=("changeScope", "affectedModules", "projectName"),
        output_format=OUTPUT_JSON,
        adoptable=True,
        context_kinds=(CTX_MODULE, CTX_CASE, CTX_BUG),
    ),
    CAP_REPORT_GENERATION: CapabilitySpec(
        CAP_REPORT_GENERATION, "报告生成", "生成测试报告",
        ("报告", "report", "测试总结", "测试报告", "生成报告", "质量报告", "周报", "月报"),
        prompt_code="report-generation",
        variables=(
            "projectName", "reportType", "periodStart", "periodEnd",
            "metricsJson", "caseSummary", "bugSummary", "outputTemplate",
        ),
    ),
    CAP_PROJECT_PROGRESS_BOARD: CapabilitySpec(
        CAP_PROJECT_PROGRESS_BOARD, "进度看板", "生成项目进度看板",
        ("进度", "看板", "里程碑", "progress", "board", "周进度", "项目进展", "进度看板"),
        prompt_code="project-progress-board",
        variables=("projectName", "progressData", "period", "milestones"),
    ),
    CAP_PROMPT_OPTIMIZE: CapabilitySpec(
        CAP_PROMPT_OPTIMIZE, "提示词优化", "改写用户输入为更清晰的提示词",
        ("优化", "改写", "润色", "prompt", "提示词", "提示词优化", "优化提示词"),
        prompt_code="prompt-optimizer",
        variables=("capability", "capabilityGoal", "rawText"),
    ),
    CAP_API_TEST_DESIGN: CapabilitySpec(
        CAP_API_TEST_DESIGN, "接口测试设计", "为接口设计覆盖正常、异常与安全的测试用例",
        ("接口", "api", "接口测试", "接口用例", "接口文档", "endpoint", "url"),
        prompt_code="api-test-design",
        variables=("projectName", "apiDescription", "baseUrl", "outputTemplate"),
    ),
    CAP_TEST_PLAN: CapabilitySpec(
        CAP_TEST_PLAN, "测试计划", "为版本定测试范围、策略与准入准出标准",
        ("测试计划", "计划", "排期", "准入", "准出", "测试范围", "testplan"),
        prompt_code="test-plan",
        variables=("projectName", "period", "requirementSummary", "modules"),
    ),
    CAP_CASE_REVIEW: CapabilitySpec(
        CAP_CASE_REVIEW, "用例评审", "评审用例覆盖率与质量并给出修改建议",
        ("用例评审", "评审用例", "覆盖分析", "用例质量", "覆盖矩阵", "casereview",
         "评审", "覆盖度", "覆盖率"),
        prompt_code="case-review",
        variables=("requirement", "criteria", "cases"),
    ),
    CAP_BUG_CLASSIFICATION: CapabilitySpec(
        CAP_BUG_CLASSIFICATION, "缺陷归类", "判断缺陷类型、严重级别与归属模块",
        ("缺陷归类", "归类", "分类", "严重级别", "归属模块", "分诊", "定级"),
        prompt_code="bug-classification",
        variables=("bugTitle", "bugDescription", "modulePath"),
    ),
    CAP_RISK_ASSESSMENT: CapabilitySpec(
        CAP_RISK_ASSESSMENT, "风险评估", "按变更范围与质量数据评估发布风险",
        ("风险", "发布风险", "风险清单", "缓解措施", "上线风险", "risk"),
        prompt_code="risk-assessment",
        variables=("projectName", "changeScope", "metricsJson"),
    ),
    CAP_AUTOMATION_SCRIPT: CapabilitySpec(
        CAP_AUTOMATION_SCRIPT, "自动化脚本生成", "按目标类型与框架生成可直接运行的 Python 自动化脚本",
        ("自动化", "脚本", "selenium", "playwright", "appium", "hypium", "uiautomator2",
         "pytest", "接口自动化", "web自动化", "app自动化", "网页自动化", "移动端自动化",
         "ui自动化", "自动化测试", "自动化脚本", "测试脚本", "autoscript", "代码生成",
         "鸿蒙", "harmonyos", "hos"),
        prompt_code="automation-script",
        variables=(
            "projectName", "targetType", "framework", "language",
            "appPlatforms", "scenario", "baseUrl", "extraRequirements",
        ),
    ),
    CAP_PERFORMANCE_SCRIPT: CapabilitySpec(
        CAP_PERFORMANCE_SCRIPT, "性能脚本生成", "生成可直接运行的 JMeter 性能测试脚本",
        ("性能", "压测", "性能测试", "性能脚本", "压力测试", "负载测试", "并发", "tps",
         "jmeter", "jmx", "performance"),
        prompt_code="performance-script",
        variables=(
            "projectName", "scenario", "baseUrl", "threads",
            "duration", "rampUp", "dataFile", "extraRequirements",
        ),
    ),
    CAP_TEST_DATA_GENERATION: CapabilitySpec(
        CAP_TEST_DATA_GENERATION, "测试数据生成",
        "按描述生成可执行的造数 SQL，或按格式与大小批量生成本地测试文件",
        ("测试数据", "造数", "造数据", "假数据", "样本数据", "模拟数据",
         "mock数据", "mock 数据", "testdata", "测试文件", "大文件", "空文件"),
        prompt_code="test-data-generation",
        variables=("dialect", "intent", "projectName", "schemaDesc", "rowCount"),
    ),
}

CAPABILITY_CODES: tuple[str, ...] = tuple(CAPABILITIES.keys())

#: 上下文声明与变量表同步：声明了 `context_kinds` 的能力自动并入 `CONTEXT_VARIABLES`，
#: 模板（backend/prompts/*.md）必须同时提供这些 optional 占位，否则 check_prompts 会红。
CAPABILITIES.update({
    code: _with_context_variables(spec) for code, spec in CAPABILITIES.items()
})

#: 可以挂到功能点上的能力（提示词优化是工具，不占功能点能力位）
BINDABLE_CAPABILITIES: tuple[str, ...] = (
    CAP_REQUIREMENT_CONFIRM, CAP_TESTPOINT_GENERATION, CAP_CASE_GENERATION,
    CAP_XMIND_TO_CASE, CAP_BUG_ANALYSIS, CAP_LOG_ANALYSIS, CAP_SQL_ANALYSIS,
    CAP_REGRESSION_GENERATION, CAP_REPORT_GENERATION, CAP_PROJECT_PROGRESS_BOARD,
    CAP_API_TEST_DESIGN, CAP_TEST_PLAN,
    CAP_CASE_REVIEW, CAP_BUG_CLASSIFICATION, CAP_RISK_ASSESSMENT,
    CAP_AUTOMATION_SCRIPT, CAP_PERFORMANCE_SCRIPT, CAP_TEST_DATA_GENERATION,
)

#: AI 实验室首页展示的主能力（质量闭环主干）
LAB_CAPABILITIES: tuple[str, ...] = (
    CAP_REQUIREMENT_CONFIRM, CAP_TESTPOINT_GENERATION, CAP_CASE_GENERATION,
    CAP_XMIND_TO_CASE, CAP_BUG_ANALYSIS, CAP_LOG_ANALYSIS, CAP_SQL_ANALYSIS,
    CAP_REGRESSION_GENERATION, CAP_REPORT_GENERATION, CAP_PROJECT_PROGRESS_BOARD,
    CAP_API_TEST_DESIGN, CAP_TEST_PLAN,
    CAP_CASE_REVIEW, CAP_BUG_CLASSIFICATION, CAP_RISK_ASSESSMENT,
    CAP_AUTOMATION_SCRIPT, CAP_PERFORMANCE_SCRIPT, CAP_TEST_DATA_GENERATION,
    CAP_PROMPT_OPTIMIZE,
)

#: 扩展能力位：此前提示词库里的补充能力已全部并入核心能力，保留空位以兼容既有引用
LAB_EXTENDED_CAPABILITIES: tuple[str, ...] = ()


def is_known(capability: str) -> bool:
    return capability in CAPABILITIES


def label_of(capability: str) -> str:
    spec = CAPABILITIES.get(capability)
    if spec:
        return spec.label
    return PSEUDO_CAPABILITY_LABELS.get(capability, capability)


def spec_of(capability: str) -> CapabilitySpec | None:
    return CAPABILITIES.get(capability)
