"""离线测试知识包检索（四期专项 G）。

把专业测试知识（对抗性字符串、边界值模板、SFDIPOT 启发式、注入探测样例）内置为
`backend/prompts/heuristics/*.yaml` 静态资产，执行能力卡前按关键词**本地检索**命中片段，
作为上下文附加块注入提示词。

三条口径：
  - **零 LLM 调用、零 token 成本**：纯本地文件读取 + 关键词命中，不进计费口径；
  - **只发命中片段**：不整包塞进提示词，且与 `context_assembler` 的档位限量统一约束；
  - **不阻塞**：字典缺失或解析失败只记一条 note 返回空结果，主链路照常。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable

import yaml  # type: ignore[import-untyped]

from ..logging_setup import log_event

logger = logging.getLogger(__name__)

#: 字典目录（随仓库版本化）
PACKS_DIR = Path(__file__).resolve().parents[2] / "prompts" / "heuristics"

#: 单条命中块注入提示词时的字数上限
MAX_HIT_CHARS = 320


@dataclass(slots=True)
class HeuristicCategory:
    """一个知识分类：命中关键词、说明与样例值。"""

    pack: str
    pack_label: str
    category_id: str
    label: str
    keywords: tuple[str, ...]
    description: str
    samples: tuple[str, ...] = field(default_factory=tuple)


@dataclass(slots=True)
class HeuristicHit:
    """一次检索命中的分类；`text` 是实际注入提示词的片段。"""

    ref: str
    title: str
    text: str
    score: int = 0

    @property
    def chars(self) -> int:
        return len(self.title) + len(self.text)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ref": self.ref,
            "title": self.title,
            "text": self.text,
            "chars": self.chars,
            "score": self.score,
        }


_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def _load_pack(path: Path) -> list[dict[str, Any]]:
    """读取并缓存一份字典；文件改动（mtime 变化）自动失效重载。"""
    key = str(path)
    try:
        mtime = path.stat().st_mtime
    except OSError:
        return []
    cached = _cache.get(key)
    if cached is not None and cached[0] == mtime:
        return cached[1]
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:  # noqa: BLE001 - 字典坏了不该阻塞主链路
        log_event(logger, "heuristics_load_failed", level=logging.WARNING,
                  pack=path.name, error=str(exc)[:200])
        return []
    categories = raw.get("categories") if isinstance(raw, dict) else None
    items = [item for item in categories or [] if isinstance(item, dict)]
    _cache[key] = (mtime, items)
    return items


def _all_packs() -> list[tuple[dict[str, Any], list[dict[str, Any]]]]:
    """按文件名稳定顺序加载全部字典（含包级元数据）。"""
    out: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
    if not PACKS_DIR.is_dir():
        return out
    for path in sorted(PACKS_DIR.glob("*.yaml")):
        try:
            meta = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        except (OSError, yaml.YAMLError):  # noqa: BLE001
            continue
        if isinstance(meta, dict):
            out.append((meta, _load_pack(path)))
    return out


def _text_of(category: dict[str, Any]) -> tuple[str, str, tuple[str, ...]]:
    label = str(category.get("label") or category.get("id") or "")
    description = str(category.get("description") or "")
    samples = tuple(str(item) for item in (category.get("samples") or []))
    return label, description, samples


def _render(label: str, description: str, samples: tuple[str, ...]) -> str:
    body = description
    if samples:
        body = f"{body}；样例：{' / '.join(samples)}" if body else f"样例：{' / '.join(samples)}"
    clean = " ".join(str(body).split())
    return clean[:MAX_HIT_CHARS] + ("…" if len(clean) > MAX_HIT_CHARS else "")


def _score(category: dict[str, Any], keywords: Iterable[str]) -> int:
    """命中分：keywords 命中权重最高，标签与说明次之。"""
    haystack = " ".join(
        [str(category.get("label") or ""), str(category.get("description") or "")]
        + [str(item) for item in (category.get("keywords") or [])]
    ).lower()
    score = 0
    for token in keywords:
        low = str(token).strip().lower()
        if len(low) < 2:
            continue
        if low in haystack:
            score += 2
    return score


def search(
    keywords: list[str],
    *,
    capability: str | None = None,
    limit: int = 2,
) -> list[HeuristicHit]:
    """按能力 + 关键词检索命中分类；无关键词或无命中时给默认分类（仍受 limit 约束）。"""
    if limit <= 0:
        return []
    hits: list[tuple[int, str, str, str, str]] = []
    for meta, categories in _all_packs():
        pack = str(meta.get("pack") or "")
        pack_label = str(meta.get("label") or pack)
        allowed = [str(item) for item in (meta.get("capabilities") or [])]
        if capability and allowed and capability not in allowed:
            continue
        default_id = str(meta.get("default_category") or "")

        scored: list[tuple[int, dict[str, Any]]] = []
        for category in categories:
            score = _score(category, keywords)
            if score > 0:
                scored.append((score, category))
        if not scored:
            for category in categories:
                if str(category.get("id") or "") == default_id:
                    scored.append((1, category))
                    break
        for score, category in scored:
            label, description, samples = _text_of(category)
            ref = f"{pack}:{category.get('id') or ''}"
            hits.append((score, ref, f"{pack_label}·{label}", _render(
                label, description, samples), pack))

    ordered = sorted(hits, key=lambda item: (-item[0], item[1]))
    seen: set[str] = set()
    out: list[HeuristicHit] = []
    for score, ref, title, text, _pack in ordered:
        if ref in seen:
            continue
        seen.add(ref)
        out.append(HeuristicHit(ref, title, text, score))
        if len(out) >= limit:
            break
    return out


def available_packs() -> list[dict[str, Any]]:
    """字典清单（帮助页与门禁共用，宣称口径与实际资产同源）。"""
    out: list[dict[str, Any]] = []
    for meta, categories in _all_packs():
        out.append({
            "pack": str(meta.get("pack") or ""),
            "label": str(meta.get("label") or ""),
            "capabilities": [str(item) for item in (meta.get("capabilities") or [])],
            "categoryCount": len(categories),
        })
    return out
