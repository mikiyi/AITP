"""知识召回的领域同义词表（三期 D1 / GAP-38）。

只解决「零字面重叠」的高频问法：同义不同词、但业务上指向同一个知识主题。
不做通用语义模型，也不替代 2~5 字 n-gram 兜底；新增词条时按「同一业务主题」分组，
组内任一词出现在查询里，就把组内其他词作为低权重召回补充。

边界：
  - 仅用于知识检索的兜底分支（精确匹配 0 命中时才生效）；
  - 管理页/全局搜索的精确语义不变；
  - 词表保持小而可读，不引入外部词典或向量模型。
"""

from __future__ import annotations

#: 同一组词指向同一类知识主题；避免把「登录」这种超宽词单独成组。
SYNONYM_GROUPS: tuple[tuple[str, ...], ...] = (
    ("页面卡住", "页面卡顿", "转圈", "加载慢", "白屏", "卡死", "无响应",
     "弱网", "异步", "loading"),
    ("付款通知", "支付通知", "回调通知", "重复推送", "重复通知", "回调重复",
     "幂等", "重复入账"),
    ("账号凭证", "登录态", "会话", "会话过期", "凭证过期", "登录超时",
     "验证码", "登录"),
    ("小数", "浮点数", "浮点", "精度", "四舍五入", "金额", "Decimal", "分为单位"),
    ("管理员接口", "后台接口", "管理接口", "越权", "权限", "水平越权", "垂直越权"),
    ("重复提交", "连续点击", "连点", "防重", "禁用按钮", "幂等"),
    ("日志", "排查", "定位", "报错", "异常", "traceId"),
)

#: 单次扩展最多补多少个词，避免长问句把 OR 条件撑爆
MAX_SYNONYMS = 12


def expand_synonyms(text: str, limit: int = MAX_SYNONYMS) -> list[str]:
    """返回与 text 命中同一主题组的补充词；原词本身不再重复返回。"""
    lowered = text.lower()
    expanded: list[str] = []
    for group in SYNONYM_GROUPS:
        if not any(term.lower() in lowered for term in group):
            continue
        for term in group:
            if term.lower() in lowered:
                continue
            if term not in expanded:
                expanded.append(term)
                if len(expanded) >= limit:
                    return expanded
    return expanded
