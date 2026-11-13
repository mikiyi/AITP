"""自动化测试框架的唯一权威登记表。

框架编码、中文名、适用目标类型与 App 平台、需要预装的包、需要的外部命令行
都只写在这一份里：入库校验、运行时体检、前端下拉、AI 提示词都从这里取，
免得「App 自动化管理」「AI 实验室」「设置」三处各写一份框架清单对不上。

平台与框架是硬绑定：鸿蒙（HOS）用 Hypium，iOS / Android 用 Appium。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: 目标类型 / 脚本类别，与 automation_repo.SCRIPT_CATEGORIES 同名同值
TARGET_WEB = "web"
TARGET_APP = "app"
TARGET_API = "api"
TARGET_PERFORMANCE = "performance"

#: App 平台编码，与前端「APP 平台」多选项一致
PLATFORM_IOS = "ios"
PLATFORM_ANDROID = "android"
PLATFORM_HOS = "hos"

PLATFORM_LABELS: dict[str, str] = {
    PLATFORM_IOS: "iOS",
    PLATFORM_ANDROID: "Android",
    PLATFORM_HOS: "HOS（鸿蒙）",
}


@dataclass(frozen=True)
class AutomationFramework:
    """一个框架的完整描述；运行时字段由 automation_service 体检时补上。"""

    code: str
    label: str
    targets: tuple[str, ...]
    platforms: tuple[str, ...] = ()
    package: str = ""
    command: str = ""
    setup_hint: str = ""


FRAMEWORKS: tuple[AutomationFramework, ...] = (
    AutomationFramework(
        code="selenium",
        label="Selenium",
        targets=(TARGET_WEB,),
        package="selenium",
        setup_hint="在下方所示解释器里 pip install selenium；Selenium 4.6+ 会自动匹配浏览器驱动，本机需装好 Chrome / Edge / Firefox 之一",
    ),
    AutomationFramework(
        code="playwright",
        label="Playwright",
        targets=(TARGET_WEB,),
        package="playwright",
        setup_hint="在下方所示解释器里 pip install playwright，再执行 python -m playwright install chromium 下载浏览器内核",
    ),
    AutomationFramework(
        code="requests",
        label="Requests",
        targets=(TARGET_API,),
        package="requests",
        setup_hint="pip install requests",
    ),
    AutomationFramework(
        code="pytest",
        label="Pytest",
        targets=(TARGET_WEB, TARGET_APP, TARGET_API),
        package="pytest",
        setup_hint="pip install pytest",
    ),
    AutomationFramework(
        code="unittest",
        label="Unittest",
        targets=(TARGET_API,),
        setup_hint="Python 标准库，无需额外安装",
    ),
    AutomationFramework(
        code="appium",
        label="Appium（iOS / Android）",
        targets=(TARGET_APP,),
        platforms=(PLATFORM_IOS, PLATFORM_ANDROID),
        package="Appium-Python-Client",
        command="appium",
        setup_hint=(
            "iOS 与 Android 的 App 自动化用 Appium："
            "解释器里 pip install Appium-Python-Client，本机另需 Appium Server 并在执行前启动"
        ),
    ),
    AutomationFramework(
        code="uiautomator2",
        label="UiAutomator2（Android）",
        targets=(TARGET_APP,),
        platforms=(PLATFORM_ANDROID,),
        package="uiautomator2",
        setup_hint="Android 备用方案：pip install uiautomator2，设备需开启 USB 调试",
    ),
    AutomationFramework(
        code="hypium",
        label="Hypium（鸿蒙 HOS）",
        targets=(TARGET_APP,),
        platforms=(PLATFORM_HOS,),
        package="hypium",
        command="hdc",
        setup_hint=(
            "鸿蒙（HarmonyOS）的 App 自动化用 Hypium："
            "解释器里 pip install hypium，本机另需 DevEco 自带的 hdc 命令行工具"
        ),
    ),
    AutomationFramework(
        code="jmeter",
        label="JMeter",
        targets=(TARGET_PERFORMANCE,),
        setup_hint="在「设置 → 性能测试管理设置」填写 JMeter 安装根目录",
    ),
)

FRAMEWORK_INDEX: dict[str, AutomationFramework] = {item.code: item for item in FRAMEWORKS}


def framework_codes(target: str | None = None) -> tuple[str, ...]:
    """可选框架编码；给了 target 就只返回该目标类型下的框架。"""
    if not target:
        return tuple(FRAMEWORK_INDEX)
    return tuple(item.code for item in FRAMEWORKS if target in item.targets)


def frameworks_for(target: str | None = None) -> tuple[AutomationFramework, ...]:
    if not target:
        return FRAMEWORKS
    return tuple(item for item in FRAMEWORKS if target in item.targets)


def normalize_platforms(platforms: list[str] | tuple[str, ...] | str | None) -> list[str]:
    """把「ios,android」这类字符串或列表统一成去重后的小写编码。"""
    if isinstance(platforms, str):
        raw: list[str] = platforms.replace("，", ",").split(",")
    else:
        raw = list(platforms or [])
    known = set(PLATFORM_LABELS)
    result: list[str] = []
    for item in raw:
        code = str(item or "").strip().lower()
        if code in known and code not in result:
            result.append(code)
    return result


def matches_platforms(item: AutomationFramework, platforms: list[str]) -> bool:
    """平台限定为空表示通用（如 Pytest）；否则所选平台必须落在框架支持范围内。"""
    if not item.platforms:
        return True
    if not platforms:
        return True
    return all(platform in item.platforms for platform in platforms)


def options_for(target: str | None, platforms: list[str] | None = None) -> list[dict[str, Any]]:
    """给前端与提示词用的框架候选清单，按目标类型与平台过滤。"""
    chosen = platforms or []
    items = []
    for item in frameworks_for(target):
        if not matches_platforms(item, chosen):
            continue
        items.append({
            "code": item.code,
            "label": item.label,
            "targets": list(item.targets),
            "platforms": list(item.platforms),
            "platformLabels": [PLATFORM_LABELS.get(code, code) for code in item.platforms],
            "package": item.package,
            "command": item.command,
            "setupHint": item.setup_hint,
        })
    return items


def default_for(target: str | None, platforms: list[str] | None = None) -> str:
    """目标类型 + 平台下的默认框架：鸿蒙落到 Hypium，iOS / Android 落到 Appium。"""
    candidates = options_for(target, platforms)
    # 平台专属框架优先于通用 runner（Pytest），否则 App 默认会落到 Pytest
    bound = [item for item in candidates if item["platforms"]]
    pool = bound or candidates
    return str(pool[0]["code"]) if pool else ""


def label_of(code: str | None) -> str:
    item = FRAMEWORK_INDEX.get(str(code or ""))
    return item.label if item else str(code or "")
