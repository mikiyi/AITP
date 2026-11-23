"""AI 供应商与协议的常量定义。"""

from __future__ import annotations

from dataclasses import dataclass

PROTOCOL_OPENAI_COMPAT = "openai_compat"
PROTOCOL_OLLAMA_NATIVE = "ollama_native"
#: Anthropic Messages 兼容协议：官方 + MiniMax / Kimi / 小米 MiMo 等订阅套餐端点
PROTOCOL_ANTHROPIC = "anthropic"


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    name: str
    label: str
    protocol: str
    default_base_url: str
    default_model: str
    env_key: tuple[str, ...] = ()
    requires_api_key: bool = True
    is_local: bool = False


PROVIDERS: dict[str, ProviderSpec] = {
    "openai": ProviderSpec(
        name="openai",
        label="OpenAI",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.openai.com/v1",
        default_model="gpt-4o-mini",
        env_key=("AITP_AI_API_KEY", "OPENAI_API_KEY"),
    ),
    "dashscope": ProviderSpec(
        name="dashscope",
        label="通义千问",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://dashscope.aliyuncs.com/compatible-mode/v1",
        default_model="qwen-max",
        env_key=("AITP_AI_API_KEY", "DASHSCOPE_API_KEY"),
    ),
    "deepseek": ProviderSpec(
        name="deepseek",
        label="DeepSeek 深度求索",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.deepseek.com/v1",
        default_model="deepseek-chat",
        env_key=("AITP_AI_API_KEY", "DEEPSEEK_API_KEY"),
    ),
    "sensenova": ProviderSpec(
        name="sensenova",
        label="商汤日日新",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://token.sensenova.cn/v1",
        default_model="sensenova-6.8-flash",
        env_key=("AITP_AI_API_KEY", "SENSENOVA_API_KEY"),
    ),
    "minimax-cn": ProviderSpec(
        name="minimax-cn",
        label="MiniMax 稀宇（国内）",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.minimaxi.com/v1",
        default_model="MiniMax-M2.7",
        env_key=("AITP_AI_API_KEY", "MINIMAX_API_KEY"),
    ),
    "minimax-global": ProviderSpec(
        name="minimax-global",
        label="MiniMax（国际）",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.minimax.io/v1",
        default_model="MiniMax-M2.7",
        env_key=("AITP_AI_API_KEY", "MINIMAX_API_KEY"),
    ),
    "bigmodel": ProviderSpec(
        name="bigmodel",
        label="智谱 BigModel",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://open.bigmodel.cn/api/paas/v4",
        default_model="glm-4-plus",
        env_key=("AITP_AI_API_KEY", "BIGMODEL_API_KEY", "ZHIPUAI_API_KEY"),
    ),
    "xiaomi-mimo": ProviderSpec(
        name="xiaomi-mimo",
        label="小米 MiMo",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.xiaomimimo.com/v1",
        default_model="mimo-v2.5",
        env_key=("AITP_AI_API_KEY", "XIAOMI_API_KEY"),
    ),
    "kimi-cn": ProviderSpec(
        name="kimi-cn",
        label="Kimi 月之暗面（国内）",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.moonshot.cn/v1",
        default_model="moonshot-v1-8k",
        env_key=("AITP_AI_API_KEY", "MOONSHOT_API_KEY"),
    ),
    "kimi-global": ProviderSpec(
        name="kimi-global",
        label="Kimi 月之暗面（国际）",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.moonshot.ai/v1",
        default_model="moonshot-v1-8k",
        env_key=("AITP_AI_API_KEY", "MOONSHOT_API_KEY"),
    ),
    "volcengine-ark": ProviderSpec(
        name="volcengine-ark",
        label="火山引擎 方舟（豆包）",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://ark.cn-beijing.volces.com/api/v3",
        default_model="doubao-1-5-pro-32k-250115",
        env_key=("AITP_AI_API_KEY", "ARK_API_KEY"),
    ),
    "tencent-hunyuan": ProviderSpec(
        name="tencent-hunyuan",
        label="腾讯云 混元",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.hunyuan.cloud.tencent.com/v1",
        default_model="hunyuan-turbos-latest",
        env_key=("AITP_AI_API_KEY", "HUNYUAN_API_KEY"),
    ),
    "siliconflow": ProviderSpec(
        name="siliconflow",
        label="硅基流动 SiliconFlow",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.siliconflow.cn/v1",
        default_model="Qwen/Qwen3-8B",
        env_key=("AITP_AI_API_KEY", "SILICONFLOW_API_KEY"),
    ),
    "openrouter": ProviderSpec(
        name="openrouter",
        label="OpenRouter",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://openrouter.ai/api/v1",
        default_model="openai/gpt-4o-mini",
        env_key=("AITP_AI_API_KEY", "OPENROUTER_API_KEY"),
    ),
    "moark": ProviderSpec(
        name="moark",
        label="模力方舟（Gitee AI）",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="https://api.moark.com/v1",
        default_model="DeepSeek-V3",
        env_key=("AITP_AI_API_KEY", "GITEE_AI_API_KEY"),
    ),
    "custom": ProviderSpec(
        name="custom",
        label="自定义兼容接口",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_base_url="",
        default_model="",
        env_key=("AITP_AI_API_KEY",),
    ),
    "ollama": ProviderSpec(
        name="ollama",
        label="本地大模型 (Ollama)",
        protocol=PROTOCOL_OLLAMA_NATIVE,
        default_base_url="http://127.0.0.1:11434",
        default_model="",
        env_key=(),
        requires_api_key=False,
        is_local=True,
    ),
}

@dataclass(frozen=True, slots=True)
class PlanSpec:
    """订阅套餐（Agent / Coding / Token Plan）的专用填写口径。

    套餐与「按量 API」的地址、Key 类型、协议常不同：例如小米 Token 套餐用
    token-plan-cn.* 地址与 tp-/ttp- Key；MiniMax / Kimi 的 Coding 套餐走
    Anthropic 兼容端点（api.*/anthropic）与订阅 Key。前端据此在选「订阅套餐」时
    自动带出正确地址与协议，并提示 Key 格式。
    """

    label: str
    base_url: str
    protocol: str
    default_model: str = ""
    key_hint: str = ""


#: 供应商 → 套餐预设（没有条目的供应商只支持按量 API）
PLANS: dict[str, PlanSpec] = {
    "xiaomi-mimo": PlanSpec(
        label="Token 套餐",
        base_url="https://token-plan-cn.xiaomimimo.com/v1",
        protocol=PROTOCOL_OPENAI_COMPAT,
        default_model="mimo-v2.5-pro",
        key_hint="tp-…（个人）/ ttp-…（团队）套餐 Key",
    ),
    "minimax-cn": PlanSpec(
        label="Coding 套餐",
        base_url="https://api.minimaxi.com/anthropic",
        protocol=PROTOCOL_ANTHROPIC,
        default_model="MiniMax-M2.7",
        key_hint="sk-cp-… 订阅 Key（非按量 Key）",
    ),
    "minimax-global": PlanSpec(
        label="Coding 套餐",
        base_url="https://api.minimax.io/anthropic",
        protocol=PROTOCOL_ANTHROPIC,
        default_model="MiniMax-M2.7",
        key_hint="sk-cp-… 订阅 Key（非按量 Key）",
    ),
    "kimi-cn": PlanSpec(
        label="Coding 套餐",
        base_url="https://api.moonshot.cn/anthropic",
        protocol=PROTOCOL_ANTHROPIC,
        key_hint="Coding 套餐专用 Key",
    ),
    "kimi-global": PlanSpec(
        label="Coding 套餐",
        base_url="https://api.moonshot.ai/anthropic",
        protocol=PROTOCOL_ANTHROPIC,
        key_hint="Coding 套餐专用 Key",
    ),
    "volcengine-ark": PlanSpec(
        label="Agent / Coding 套餐",
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        protocol=PROTOCOL_OPENAI_COMPAT,
        key_hint="套餐控制台生成的 Key；模型填推理接入点 ep-…",
    ),
}

SOURCE_DB = "db"
SOURCE_ENV = "env"
SOURCE_DEFAULT = "default"
SOURCE_NONE = "none"
SOURCE_DRAFT = "draft"

# 数据落地属性：决定输入是否会离开本机
LOCALITY_LOCAL = "local"          # 本机自托管（Ollama / LM Studio / llama.cpp）
LOCALITY_CLOUD = "cloud"          # 第三方云端服务
LOCALITY_CUSTOM = "custom"        # 自定义地址，需用户自行判断是否出网

# AI 调用默认超时秒数：本地规则即时返回，AI 受模型延迟与超时约束
AI_DEFAULT_TIMEOUT = 60.0

# 数据外发边界
EGRESS_NONE = "none"              # 数据不出本机
EGRESS_THIRD_PARTY = "third_party"  # 输入会上传到第三方，存在 PII / 内部 SQL / 生产日志外泄风险

# 降级原因
DEGRADE_UNREACHABLE = "unreachable"
DEGRADE_TIMEOUT = "timeout"
DEGRADE_AUTH_FAILED = "auth_failed"
DEGRADE_NO_CREDENTIAL = "no_credential"
DEGRADE_MODEL_MISSING = "model_missing"
DEGRADE_KEY_UNREADABLE = "key_unreadable"
DEGRADE_EMPTY_OUTPUT = "empty_output"
