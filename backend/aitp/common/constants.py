"""AI 供应商与协议的常量定义。"""

from __future__ import annotations

from dataclasses import dataclass

PROTOCOL_OPENAI_COMPAT = "openai_compat"
PROTOCOL_OLLAMA_NATIVE = "ollama_native"


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
        label="DeepSeek",
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
