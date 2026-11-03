"""XMind 大纲解析。

支持两种 .xmind 内部格式：
- XMind 2020+/Zen：content.json
- XMind 8：content.xml

统一输出 {title, children[]} 树结构，另外支持粘贴的缩进大纲文本作为备选入口。
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
import zipfile
from io import BytesIO
from typing import Any

#: 统一的节点结构
Node = dict[str, Any]

_BULLET_RE = re.compile(r"^(?:[-*•·+]|\d+[.)、])\s*")
_TAB_WIDTH = 4


class XMindParseError(Exception):
    """xmind 文件或大纲文本无法解析。"""


def _node(title: str, children: list[Node]) -> Node:
    return {"title": title, "children": children}


def _children_of(topic: dict[str, Any]) -> list[Node]:
    """content.json 里子节点挂在 topic.children.attached。"""
    children = topic.get("children")
    result: list[Node] = []
    if isinstance(children, dict):
        attached = children.get("attached")
        if isinstance(attached, list):
            for item in attached:
                if isinstance(item, dict):
                    result.append(_node(str(item.get("title") or ""), _children_of(item)))
    return result


def _parse_content_json(raw: bytes) -> list[Node]:
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise XMindParseError("content.json 解析失败") from exc
    sheets = data if isinstance(data, list) else [data]
    roots: list[Node] = []
    for sheet in sheets:
        if not isinstance(sheet, dict):
            continue
        root_topic = sheet.get("rootTopic")
        if isinstance(root_topic, dict):
            roots.append(_node(str(root_topic.get("title") or ""), _children_of(root_topic)))
    if not roots:
        raise XMindParseError("content.json 里没有找到任何画布")
    return roots


def _xml_topic_to_node(topic: ET.Element) -> Node:
    title_el = topic.find("title")
    title = (title_el.text or "").strip() if title_el is not None else ""
    children: list[Node] = []
    children_el = topic.find("children")
    if children_el is not None:
        for topics_el in children_el.findall("topics"):
            for sub in topics_el.findall("topic"):
                children.append(_xml_topic_to_node(sub))
    return _node(title, children)


def _parse_content_xml(raw: bytes) -> list[Node]:
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        raise XMindParseError("content.xml 解析失败") from exc
    roots: list[Node] = []
    for sheet in root.findall("sheet"):
        topic = sheet.find("topic")
        if topic is not None:
            roots.append(_xml_topic_to_node(topic))
    if not roots:
        raise XMindParseError("content.xml 里没有找到任何画布")
    return roots


def parse_xmind(data: bytes) -> list[Node]:
    """解析 .xmind 文件二进制，返回画布树。"""
    try:
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise XMindParseError("不是有效的 .xmind 文件（zip 包解析失败）") from exc
    names = set(archive.namelist())
    if "content.json" in names:
        return _parse_content_json(archive.read("content.json"))
    if "content.xml" in names:
        return _parse_content_xml(archive.read("content.xml"))
    raise XMindParseError("xmind 包里既没有 content.json 也没有 content.xml")


def _indent_width(line: str) -> int:
    width = 0
    for char in line:
        if char == "\t":
            width += _TAB_WIDTH
        elif char == " ":
            width += 1
        else:
            break
    return width


def parse_outline_text(text: str) -> list[Node]:
    """解析缩进大纲文本（一行一个节点，缩进表示层级）。"""
    roots: list[Node] = []
    stack: list[tuple[int, Node]] = []
    for raw_line in text.splitlines():
        if not raw_line.strip():
            continue
        indent = _indent_width(raw_line)
        title = _BULLET_RE.sub("", raw_line.strip()).strip()
        if not title:
            continue
        node = _node(title, [])
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if stack:
            stack[-1][1]["children"].append(node)
        else:
            roots.append(node)
        stack.append((indent, node))
    return roots


def tree_to_outline_text(nodes: list[Node]) -> str:
    """把树转成缩进大纲文本，用于喂给提示词。"""
    lines: list[str] = []

    def walk(node: Node, depth: int) -> None:
        lines.append("  " * depth + str(node.get("title") or ""))
        for child in node.get("children") or []:
            walk(child, depth + 1)

    for node in nodes:
        walk(node, 0)
    return "\n".join(lines)


def subtree_to_outline_text(node: Node) -> str:
    """单个节点及其后代的缩进大纲文本。"""
    return tree_to_outline_text([node])
