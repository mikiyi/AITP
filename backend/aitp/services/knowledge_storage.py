"""知识库 Markdown 落盘：双写文件、以文件为准重建索引、可迁移目录。

DB 是权威数据源，写入顺序为「DB 提交 → 写 .md → 回写 checksum」；
写文件失败不回滚 DB，只提示可在设置页重建索引修复。
"""

from __future__ import annotations

import re
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from ..common.security import content_checksum
from ..common.time_utils import utcnow_iso
from ..config import PROJECT_ROOT
from ..errors import AppError, ErrorCode
from ..repositories.sys_config_repo import SysConfigRepository

CONFIG_PATH = "knowledge.storage_path"
CONFIG_MODE = "knowledge.file_mode"
CONFIG_AUTO_REINDEX = "knowledge.auto_reindex"
CONFIG_ASSISTANT_MODE = "knowledge.assistant_default_mode"

MODE_MIRROR = "mirror"
MODE_DB_ONLY = "db_only"
FILE_MODES = (MODE_MIRROR, MODE_DB_ONLY)

ASSISTANT_MODE_KB = "kb"
ASSISTANT_MODE_LLM = "llm"
ASSISTANT_MODE_HYBRID = "hybrid"
ASSISTANT_MODES = (ASSISTANT_MODE_KB, ASSISTANT_MODE_LLM, ASSISTANT_MODE_HYBRID)
DEFAULT_ASSISTANT_MODE = ASSISTANT_MODE_HYBRID

DEFAULT_DIR_NAME = "knowledge"

_ILLEGAL_CHARS = re.compile(r'[\\/:*?"<>|]')
_SPACES = re.compile(r"\s+")
_DOTS = re.compile(r"\.{2,}")
_MAX_NAME_LEN = 120
_FRONTMATTER_FENCE = "---"


# ---------------- 配置读取 ----------------

def file_mode(conn: sqlite3.Connection) -> str:
    raw = str(SysConfigRepository(conn).get_value(CONFIG_MODE, MODE_MIRROR) or "").strip().lower()
    return raw if raw in FILE_MODES else MODE_MIRROR


def auto_reindex_enabled(conn: sqlite3.Connection) -> bool:
    raw = SysConfigRepository(conn).get_value(CONFIG_AUTO_REINDEX, "false")
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


def assistant_default_mode(conn: sqlite3.Connection) -> str:
    """问答助手打开时预选的回答模式；配置损坏时回落到 hybrid。"""
    raw = str(
        SysConfigRepository(conn).get_value(CONFIG_ASSISTANT_MODE, DEFAULT_ASSISTANT_MODE) or ""
    ).strip().lower()
    return raw if raw in ASSISTANT_MODES else DEFAULT_ASSISTANT_MODE


def storage_root(conn: sqlite3.Connection) -> Path:
    """当前生效的知识库根目录；配置为空时用项目根下的 knowledge 目录。"""
    raw = str(SysConfigRepository(conn).get_value(CONFIG_PATH, "") or "").strip()
    if raw:
        return Path(raw).expanduser().resolve()
    return (PROJECT_ROOT / DEFAULT_DIR_NAME).resolve()


def set_storage_path(conn: sqlite3.Connection, path: str) -> Path:
    """写入新的存放目录；必须是绝对路径，目录不存在时自动创建。

    知识库会把 Markdown 落到这个根目录下，根目录本身跑偏
    （相对路径、穿越串）会让后续 safe_join 形同虚设，这里先挡住。
    """
    if not isinstance(path, str) or not path.strip():
        raise AppError(ErrorCode.PARAM_ERROR, "知识库存放目录不能为空")
    raw = path.strip()
    candidate = Path(raw).expanduser()
    if not candidate.is_absolute():
        raise AppError(ErrorCode.PARAM_ERROR, "知识库存放目录必须是绝对路径")
    resolved = candidate.resolve()
    if raw.replace("\\", "/").split("/")[-1] in ("..", ".") or ".." in Path(raw).parts:
        raise AppError(ErrorCode.PARAM_ERROR, "知识库存放目录不能包含 .. 或 . 段")
    if resolved == resolved.parent:
        raise AppError(ErrorCode.PARAM_ERROR, "知识库存放目录不能是盘符根目录")
    resolved.mkdir(parents=True, exist_ok=True)
    SysConfigRepository(conn).set_value(CONFIG_PATH, str(resolved), "string", "知识库 Markdown 存放根目录")
    return resolved


# ---------------- 文件名与路径 ----------------

def slugify(title: str) -> str:
    text = _ILLEGAL_CHARS.sub("_", title or "").strip().strip(".")
    text = _DOTS.sub("_", text)
    text = _SPACES.sub(" ", text)
    return text or "untitled"


def file_name(article_id: int, title: str) -> str:
    name = f"{article_id}-{slugify(title)}"
    if len(name) > _MAX_NAME_LEN:
        name = name[:_MAX_NAME_LEN]
    return f"{name}.md"


def safe_join(root: Path, relative: str) -> Path:
    """拼接并校验路径，禁止越出根目录（防目录穿越）。"""
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise AppError(ErrorCode.PARAM_ERROR, "知识库文件路径越界")
    return target


# ---------------- 文件格式 ----------------

def _tags_list(tags: str) -> list[str]:
    return [t.strip() for t in (tags or "").replace("，", ",").split(",") if t.strip()]


def render_article(article: dict[str, Any]) -> str:
    """渲染成「YAML frontmatter + Markdown 正文」。"""
    tags = ", ".join(_tags_list(str(article.get("tags") or "")))
    lines = [
        _FRONTMATTER_FENCE,
        f"id: {article.get('id')}",
        f"title: {article.get('title') or ''}",
        f"version: {article.get('current_version') or 1}",
        f"category: {article.get('category') or ''}",
        f"tags: [{tags}]",
        f"review_status: {article.get('review_status') or 'published'}",
        f"updated_at: {article.get('updated_at') or utcnow_iso()}",
        _FRONTMATTER_FENCE,
        "",
        str(article.get("content") or ""),
    ]
    return "\n".join(lines)


def parse_article(text: str) -> tuple[dict[str, Any], str]:
    """解析 frontmatter + 正文；没有 frontmatter 时只返回正文。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return {}, text
    meta: dict[str, Any] = {}
    end = -1
    for index in range(1, len(lines)):
        if lines[index].strip() == _FRONTMATTER_FENCE:
            end = index
            break
        line = lines[index].strip()
        if not line or ":" not in line:
            continue
        key, _, raw = line.partition(":")
        key = key.strip()
        value = raw.strip()
        if value.startswith("[") and value.endswith("]"):
            meta[key] = [v.strip() for v in value[1:-1].split(",") if v.strip()]
            continue
        if key in {"id", "version"}:
            try:
                meta[key] = int(value)
            except ValueError:
                meta[key] = value
            continue
        meta[key] = value
    if end < 0:
        return {}, text
    body = "\n".join(lines[end + 1:]).lstrip("\n")
    return meta, body


# ---------------- 写入 / 删除 ----------------

def write_article(conn: sqlite3.Connection, article: dict[str, Any]) -> str:
    """把条目写成 .md，返回相对路径；db_only 模式返回空串。"""
    if file_mode(conn) == MODE_DB_ONLY:
        return ""
    root = storage_root(conn)
    root.mkdir(parents=True, exist_ok=True)
    relative = file_name(int(article["id"]), str(article.get("title") or ""))
    target = safe_join(root, relative)
    target.write_text(render_article(article), encoding="utf-8")
    return relative


def remove_article_file(conn: sqlite3.Connection, relative: str) -> bool:
    """删除落盘文件；文件不存在不算失败。"""
    if not relative:
        return False
    root = storage_root(conn)
    target = safe_join(root, relative)
    if not target.exists():
        return False
    target.unlink()
    return True


def file_status(conn: sqlite3.Connection, article: dict[str, Any]) -> dict[str, Any]:
    """对比 DB 指纹与磁盘内容，判断文件是否被外部改过或丢失。"""
    relative = str(article.get("file_path") or "")
    if file_mode(conn) == MODE_DB_ONLY or not relative:
        return {"mode": file_mode(conn), "filePath": relative, "exists": False, "changed": False}
    root = storage_root(conn)
    try:
        target = safe_join(root, relative)
    except AppError:
        return {"mode": file_mode(conn), "filePath": relative, "exists": False, "changed": False}
    if not target.exists():
        return {"mode": file_mode(conn), "filePath": relative, "exists": False, "changed": True}
    try:
        text = target.read_text(encoding="utf-8")
    except OSError:
        return {"mode": file_mode(conn), "filePath": relative, "exists": True, "changed": True}
    _, body = parse_article(text)
    changed = content_checksum(body) != str(article.get("content_checksum") or "")
    return {"mode": file_mode(conn), "filePath": relative, "exists": True, "changed": changed}


# ---------------- 重建索引 ----------------

def reindex(conn: sqlite3.Connection) -> dict[str, Any]:
    """扫描目录里的 .md，以文件为准回填数据库并重建全文索引。

    「以文件为准」也适用于软删过的条目：删除接口清文件失败会留下孤儿 .md，
    这类 id 在库里仍然占着主键，走插入分支必然撞库，所以命中软删行时改它、
    顺手把它复活，并在返回的 revived 计数里说明「已删除的条目又回来了」。
    """
    from ..repositories.knowledge_repo import KnowledgeRepository

    root = storage_root(conn)
    mode = file_mode(conn)
    if mode == MODE_DB_ONLY:
        # 仅数据库模式下目录里的 .md 是历史遗留，读它们会把旧正文盖回库里；
        # 这里只按库里重建全文索引。
        _rebuild_fts(conn)
        return {
            "root": str(root),
            "mode": mode,
            "scanned": 0,
            "imported": 0,
            "updated": 0,
            "revived": 0,
            "skipped": 0,
        }
    repo = KnowledgeRepository(conn)
    scanned = 0
    imported = 0
    updated = 0
    revived = 0
    skipped = 0
    seen_ids: set[int] = set()
    if root.exists():
        # 同一 id 可能有历史残留的多份 .md（改名前后各一份），取修改时间最新的那份，
        # 否则按文件名排序会让旧内容盖掉新内容。
        paths = sorted(
            root.glob("*.md"), key=lambda p: (p.stat().st_mtime, p.name), reverse=True
        )
        for path in paths:
            scanned += 1
            try:
                text = path.read_text(encoding="utf-8")
            except OSError:
                skipped += 1
                continue
            meta, body = parse_article(text)
            article_id = meta.get("id")
            if not isinstance(article_id, int):
                skipped += 1
                continue
            if article_id in seen_ids:
                skipped += 1
                continue
            seen_ids.add(article_id)
            relative = path.relative_to(root).as_posix()
            payload: dict[str, Any] = {
                "title": str(meta.get("title") or path.stem),
                "content": body,
                "content_checksum": content_checksum(body),
                "file_path": relative,
                "review_status": str(meta.get("review_status") or "published"),
            }
            category = meta.get("category")
            if category:
                payload["category"] = str(category)
            tags = meta.get("tags")
            if isinstance(tags, list):
                payload["tags"] = ",".join(str(t) for t in tags)
            version = meta.get("version")
            if isinstance(version, int):
                payload["current_version"] = version
            existing = repo.raw_one(
                "SELECT id, is_deleted FROM biz_knowledge_article WHERE id = ?", (article_id,)
            )
            if existing:
                if int(existing["is_deleted"] or 0):
                    payload["is_deleted"] = 0
                    revived += 1
                repo.update(article_id, payload)
                updated += 1
            else:
                payload["source_type"] = "manual"
                payload["id"] = article_id
                repo.insert(payload)
                imported += 1
    _rebuild_fts(conn)
    return {
        "root": str(root),
        "mode": mode,
        "scanned": scanned,
        "imported": imported,
        "updated": updated,
        "revived": revived,
        "skipped": skipped,
    }


def _rebuild_fts(conn: sqlite3.Connection) -> None:
    conn.execute("INSERT INTO biz_knowledge_fts(biz_knowledge_fts) VALUES('rebuild')")


# ---------------- 目录迁移 ----------------

def migrate(conn: sqlite3.Connection, target_path: str) -> dict[str, Any]:
    """把落盘文件复制到新目录并以新目录重建索引。

    只清理由本模块写出的 .md 文件，不动目录里的其它文件。
    """
    from ..repositories.knowledge_repo import KnowledgeRepository

    source = storage_root(conn)
    target = Path(target_path).expanduser().resolve()
    if target == source:
        raise AppError(ErrorCode.PARAM_ERROR, "目标目录与当前目录相同")
    target.mkdir(parents=True, exist_ok=True)

    copied: list[str] = []
    if source.exists():
        for path in sorted(source.glob("*.md")):
            shutil.copy2(path, safe_join(target, path.name))
            copied.append(path.name)

    SysConfigRepository(conn).set_value(CONFIG_PATH, str(target), "string", "知识库 Markdown 存放根目录")
    conn.commit()

    result = reindex(conn)
    result["copied"] = len(copied)
    result["source"] = str(source)
    result["target"] = str(target)

    removed = 0
    for name in copied:
        old = safe_join(source, name)
        if old.exists():
            old.unlink()
            removed += 1
    result["removedFromSource"] = removed
    return result
