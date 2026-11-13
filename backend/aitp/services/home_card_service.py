"""首页卡片定制：可显示/隐藏与排序的首页卡片区。

固定区（知识库问答助手、数据概览、功能快捷入口）不参与定制，
这里只维护可定制区；布局以 JSON 形式存在 sys_config 的 home.cards。
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.base import dumps, loads
from ..repositories.sys_config_repo import SysConfigRepository

HOME_CARDS_KEY = "home.cards"
MAX_CARDS = 30


@dataclass(frozen=True)
class HomeCard:
    key: str
    label: str
    description: str
    width: str = "half"
    #: 固定卡片始终显示、始终排在最前，不出现在可定制列表里
    locked: bool = False
    default_visible: bool = True


ASSISTANT_CARD = HomeCard("assistant", "知识库问答助手", "基于本机知识库正文回答，附出处", "full")
OVERVIEW_CARD = HomeCard("overview", "数据概览", "资产数量与核心指标", "full", locked=True)
SHORTCUT_CARD = HomeCard("shortcuts", "功能快捷入口", "按使用频率排序的常用入口", "full", locked=True)

#: 首页可定制卡片：顺序即默认顺序，width=full 的卡片独占一行
CUSTOMIZABLE_CARDS: tuple[HomeCard, ...] = (
    ASSISTANT_CARD,
    HomeCard("recentTasks", "最近任务", "最近 5 条 AI 任务、状态与耗时", "full"),
    HomeCard("caseStatus", "用例执行状态", "未执行 / 通过 / 失败 / 阻塞占比"),
    HomeCard("bugStatus", "缺陷状态分布", "新建到关闭各状态的数量占比"),
    HomeCard("projectProgress", "项目进度", "需求确认、用例执行与缺陷关闭进度"),
    HomeCard("moduleTree", "模块结构", "功能点树，点击直达模块管理"),
    HomeCard("aiUsage", "AI 能力使用", "任务量 Top 8 的能力分布", default_visible=False),
    HomeCard("bugSeverity", "缺陷严重级别", "P0–P3 缺陷数量对比", default_visible=False),
    HomeCard("recentReports", "最近测试报告", "最新 5 份报告的类型与状态", default_visible=False),
    HomeCard("planOverview", "计划与轮次总览", "版本→计划→轮次层级、甘特与状态看板", "full"),
    HomeCard("envHealth", "测试环境健康监测", "应用与数据库可达性、资源曲线", "full"),
)

LOCKED_CARDS: tuple[HomeCard, ...] = (OVERVIEW_CARD, SHORTCUT_CARD)
ALL_CARDS: tuple[HomeCard, ...] = LOCKED_CARDS + CUSTOMIZABLE_CARDS

_BY_KEY: dict[str, HomeCard] = {card.key: card for card in ALL_CARDS}
_CUSTOMIZABLE_KEYS: tuple[str, ...] = tuple(card.key for card in CUSTOMIZABLE_CARDS)


def _summary(card: HomeCard, *, visible: bool, order: int) -> dict[str, Any]:
    return {
        "key": card.key,
        "label": card.label,
        "description": card.description,
        "width": card.width,
        "locked": card.locked,
        "visible": visible,
        "order": order,
    }


def default_layout() -> list[dict[str, Any]]:
    """未配置时的首页布局：固定区在前，可定制区按默认顺序与默认可见性。"""
    items = [_summary(card, visible=True, order=index) for index, card in enumerate(LOCKED_CARDS)]
    for offset, card in enumerate(CUSTOMIZABLE_CARDS):
        items.append(_summary(card, visible=card.default_visible, order=len(LOCKED_CARDS) + offset))
    return items


def normalize(raw: Any) -> list[dict[str, Any]]:
    """把任意来源的布局洗成合法布局：丢掉未知 key，补齐漏配卡片。"""
    rows = raw if isinstance(raw, list) else []
    seen: set[str] = set()
    custom: list[dict[str, Any]] = []
    for row in rows[:MAX_CARDS]:
        if not isinstance(row, dict) or not isinstance(row.get("key"), str):
            continue
        card = _BY_KEY.get(str(row["key"]))
        if card is None or card.locked or card.key in seen:
            continue
        seen.add(card.key)
        custom.append(_summary(card, visible=bool(row.get("visible", True)), order=len(custom)))
    # 新增卡片或配置残缺时按默认顺序补到末尾，保证首页不会出现空白卡
    for card in CUSTOMIZABLE_CARDS:
        if card.key not in seen:
            custom.append(_summary(card, visible=card.default_visible, order=len(custom)))
    merged = [_summary(card, visible=True, order=index) for index, card in enumerate(LOCKED_CARDS)]
    for item in custom:
        merged.append(item)
    # order 始终按最终排布重算，避免脏数据里的旧序号留下空洞
    for index, item in enumerate(merged):
        item["order"] = index
    return merged


def load(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    # 配置读不出或格式坏了都退回默认布局，首页不能因此白屏
    raw = loads(SysConfigRepository(conn).get_value(HOME_CARDS_KEY, ""), None)
    return default_layout() if raw is None else normalize(raw)


def save(conn: sqlite3.Connection, raw: Any) -> list[dict[str, Any]]:
    if not isinstance(raw, list) or not raw:
        raise AppError(ErrorCode.PARAM_ERROR, "cards 必须是非空数组")
    layout = normalize(raw)
    # 只持久化可定制区，固定区由代码保证，避免配置里塞进不可控的卡
    payload = [
        {"key": item["key"], "visible": bool(item["visible"])}
        for item in layout
        if not item["locked"]
    ]
    SysConfigRepository(conn).set_value(
        HOME_CARDS_KEY,
        dumps(payload),
        value_type="json",
        description="首页卡片显示与排序",
    )
    return layout
