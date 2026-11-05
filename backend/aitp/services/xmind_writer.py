"""XMind 文件生成。

与 xmind_parser 对应的写入侧：把统一树结构 {title, children[]} 写成
XMind Zen 格式的 .xmind（本质是 zip，含 content.json / metadata.json /
manifest.json）。XMind 2020+ 与多数兼容工具按 content.json 读取。
"""

from __future__ import annotations

import json
import zipfile
from io import BytesIO
from typing import Any

from ..common.time_utils import utcnow_iso

#: 统一的节点结构：{title, children[]}
Node = dict[str, Any]


def _to_topic(node: Node) -> dict[str, Any]:
    """统一树节点 → XMind Zen 的 topic 结构。"""
    topic: dict[str, Any] = {"id": _gen_id(), "title": str(node.get("title") or "")}
    children = node.get("children") or []
    if children:
        topic["children"] = {"attached": [_to_topic(child) for child in children]}
    return topic


def _gen_id() -> str:
    import uuid

    return uuid.uuid4().hex


def build_content_json(title: str, nodes: list[Node]) -> list[dict[str, Any]]:
    """构造 content.json：一个画布，根节点为 title，子节点为 nodes。"""
    root_children = [_to_topic(node) for node in nodes]
    root_topic: dict[str, Any] = {"id": _gen_id(), "title": title}
    if root_children:
        root_topic["children"] = {"attached": root_children}
    sheet = {
        "id": _gen_id(),
        "class": "sheet",
        "title": title,
        "rootTopic": root_topic,
    }
    return [sheet]


def build_xmind(title: str, nodes: list[Node]) -> bytes:
    """把树写成 .xmind 二进制（zip 包）。"""
    content = build_content_json(title, nodes)
    now = utcnow_iso()
    metadata = {
        "creator": {
            "name": "AITestPilot",
            "version": "1.0.0",
        },
        "dataStructureVersion": "2",
    }
    manifest: dict[str, Any] = {
        "file-entries": {
            "content.json": {},
            "metadata.json": {},
        }
    }
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("content.json", json.dumps(content, ensure_ascii=False))
        archive.writestr("metadata.json", json.dumps(metadata, ensure_ascii=False))
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False))
        # XMind Zen 需要一个非空的 marker，避免部分版本判为损坏
        archive.writestr(".dat", json.dumps({"timestamp": now}, ensure_ascii=False))
    return buffer.getvalue()
