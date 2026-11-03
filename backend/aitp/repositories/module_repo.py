"""四级模块树仓储：项目 → 模块 → 子模块 → 功能。

层级用邻接表 + 派生 depth 实现，不硬编码层数；node_type 只标记语义角色。
"""

from __future__ import annotations

from typing import Any, Callable, cast

from ..common.pagination import RowDict, RowList
from ..errors import AppError, ErrorCode
from .base import BaseRepository, dumps, loads

NODE_TYPE_MODULE = "module"
NODE_TYPE_SUBMODULE = "submodule"
NODE_TYPE_FEATURE = "feature"

NODE_TYPES = (NODE_TYPE_MODULE, NODE_TYPE_SUBMODULE, NODE_TYPE_FEATURE)


class ModuleRepository(BaseRepository):
    table = "biz_module"
    default_order = "sort_order"
    default_order_dir = "asc"
    order_whitelist = {"id", "name", "code", "node_type", "sort_order", "created_at", "updated_at"}
    searchable = ["name", "code", "description"]

    # ---- 读 ----
    def get_node(self, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_module WHERE id = ? AND is_deleted = 0", (pk,)
        ).fetchone()
        return dict(row) if row else None

    def list_children(self, project_id: int, parent_id: int | None = None) -> RowList:
        if parent_id is None:
            rows = self.conn.execute(
                "SELECT * FROM biz_module WHERE project_id = ? AND parent_id IS NULL"
                " AND is_deleted = 0 ORDER BY sort_order ASC, id ASC",
                (project_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM biz_module WHERE project_id = ? AND parent_id = ?"
                " AND is_deleted = 0 ORDER BY sort_order ASC, id ASC",
                (project_id, parent_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_all(self, project_id: int) -> RowList:  # type: ignore[override]  # 形参与基类不同：模块树按项目取全量
        rows = self.conn.execute(
            "SELECT * FROM biz_module WHERE project_id = ? AND is_deleted = 0"
            " ORDER BY sort_order ASC, id ASC",
            (project_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def path_of(self, pk: int) -> RowList:
        """从根到该节点的祖先链。"""
        chain: RowList = []
        seen: set[int] = set()
        current = self.get_node(pk)
        while current:
            if current["id"] in seen:
                break
            seen.add(current["id"])
            chain.append(current)
            parent_id = current.get("parent_id")
            if not parent_id:
                break
            current = self.get_node(parent_id)
        chain.reverse()
        return chain

    def path_text(self, pk: int, project_name: str = "") -> str:
        chain = self.path_of(pk)
        names = [c["name"] for c in chain]
        if project_name:
            names.insert(0, project_name)
        return "/".join(names)

    def depth_of(self, pk: int) -> int:
        """根节点 depth=0（项目层算第 1 级，所以模块是第 2 级）。"""
        return max(0, len(self.path_of(pk)) - 1)

    def child_count(self, pk: int) -> int:
        return int(
            self.conn.execute(
                "SELECT COUNT(*) FROM biz_module WHERE parent_id = ? AND is_deleted = 0", (pk,)
            ).fetchone()[0]
        )

    def is_descendant(self, candidate_id: int, ancestor_id: int) -> bool:
        """candidate 是否是 ancestor 的后代（含自身）。"""
        seen: set[int] = set()
        current_id: int | None = candidate_id
        while current_id:
            if current_id == ancestor_id:
                return True
            if current_id in seen:
                return False
            seen.add(current_id)
            row = self.conn.execute(
                "SELECT parent_id FROM biz_module WHERE id = ? AND is_deleted = 0", (current_id,)
            ).fetchone()
            current_id = row["parent_id"] if row else None
        return False

    def reference_counts(self, pk: int) -> dict[str, int]:
        counts = {
            "cases": int(self.conn.execute(
                "SELECT COUNT(*) FROM biz_test_case WHERE module_id = ? AND is_deleted = 0", (pk,)
            ).fetchone()[0]),
            "bugs": int(self.conn.execute(
                "SELECT COUNT(*) FROM biz_bug WHERE module_id = ? AND is_deleted = 0", (pk,)
            ).fetchone()[0]),
            "knowledge": int(self.conn.execute(
                "SELECT COUNT(*) FROM biz_knowledge_article WHERE module_id = ? AND is_deleted = 0", (pk,)
            ).fetchone()[0]),
            "drafts": int(self.conn.execute(
                "SELECT COUNT(*) FROM biz_knowledge_draft WHERE module_id = ? AND is_deleted = 0", (pk,)
            ).fetchone()[0]),
        }
        return counts

    # ---- 写 ----
    def create_node(self, project_id: int, name: str, node_type: str = NODE_TYPE_MODULE,
                    parent_id: int | None = None, description: str = "",
                    code: str = "") -> int:
        if node_type not in NODE_TYPES:
            raise AppError(ErrorCode.PARAM_ERROR, f"节点类型非法：{node_type}")
        if parent_id is not None:
            parent = self.get_node(parent_id)
            if not parent:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "父节点不存在")
            if parent["project_id"] != project_id:
                raise AppError(ErrorCode.PARAM_ERROR, "父节点不属于该项目")
        max_order = int(self.conn.execute(
            "SELECT COALESCE(MAX(sort_order), 0) FROM biz_module"
            " WHERE project_id = ? AND parent_id IS ?",
            (project_id, parent_id),
        ).fetchone()[0])
        return self.insert({
            "project_id": project_id,
            "parent_id": parent_id,
            "node_type": node_type,
            "name": name,
            "code": code,
            "description": description,
            "sort_order": max_order + 1,
            "status": "active",
        })

    def move_node(self, pk: int, new_parent_id: int | None) -> None:
        if new_parent_id is not None:
            if new_parent_id == pk:
                raise AppError(ErrorCode.ILLEGAL_OPERATION, "不能把节点挂到自己下面")
            if self.is_descendant(new_parent_id, pk):
                raise AppError(ErrorCode.ILLEGAL_OPERATION, "不能把节点挂到自己的子孙节点下面")
            parent = self.get_node(new_parent_id)
            if not parent:
                raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "目标父节点不存在")
            current = cast("RowDict", self.get_node(pk))
            if parent["project_id"] != current["project_id"]:
                raise AppError(ErrorCode.PARAM_ERROR, "不能跨项目移动节点")
        self.update(pk, {"parent_id": new_parent_id})

    def delete_node(self, pk: int) -> bool:
        if self.child_count(pk) > 0:
            raise AppError(ErrorCode.RESOURCE_REFERENCED, "该节点下还有子节点，无法删除")
        counts = self.reference_counts(pk)
        if any(counts.values()):
            raise AppError(
                ErrorCode.RESOURCE_REFERENCED,
                "该节点已被引用，无法删除",
                {"references": counts},
            )
        return self.soft_delete(pk)

    # ---- 树 ----
    def tree(self, project_id: int, project_name: str = "") -> RowList:
        """返回嵌套树，每个节点带 depth、路径、引用计数。"""
        nodes = self.list_all(project_id)
        children_map: dict[int | None, RowList] = {}
        for node in nodes:
            children_map.setdefault(node["parent_id"], []).append(node)

        def build(parent_id: int | None, depth: int, prefix: str) -> RowList:
            result: RowList = []
            for node in children_map.get(parent_id, []):
                path = f"{prefix}/{node['name']}" if prefix else node["name"]
                item = dict(node)
                item["depth"] = depth
                item["path"] = path
                item["references"] = self.reference_counts(node["id"])
                item["children"] = build(node["id"], depth + 1, path)
                result.append(item)
            return result

        root_prefix = project_name
        return build(None, 1, root_prefix)

    def flat_tree(self, project_id: int) -> RowList:
        """按树的先序遍历返回扁平节点列表（父节点紧跟其子树）。"""
        nodes = self.list_all(project_id)
        children_map: dict[int | None, RowList] = {}
        for node in nodes:
            children_map.setdefault(node["parent_id"], []).append(node)

        result: RowList = []
        stack: list[RowDict] = list(reversed(children_map.get(None, [])))
        while stack:
            node = stack.pop()
            result.append(node)
            children = children_map.get(node["id"], [])
            stack.extend(reversed(children))
        return result

    def feature_options(self, project_id: int, leaf_only: bool = True) -> RowList:
        """扁平的节点列表，供前端下拉。

        leaf_only=True 时只返回叶子（功能）节点；False 时返回全部节点，
        供「展示节点名称」这类需要解析任意层级的场景使用。
        """
        nodes = self.flat_tree(project_id)
        if leaf_only:
            leaf_ids = {
                n["parent_id"] for n in nodes if n["parent_id"] is not None
            }
            leaves = [n for n in nodes if n["id"] not in leaf_ids]
        else:
            leaves = nodes
        options: RowList = []
        for node in leaves:
            options.append({
                "id": node["id"],
                "name": node["name"],
                "nodeType": node["node_type"],
                "projectId": node["project_id"],
                "path": self.path_text(node["id"], project_name=""),
            })
        return options


class ModuleCapabilityRepository(BaseRepository):
    table = "biz_module_ai_capability"
    soft_delete_enabled = False
    default_order = "capability"
    default_order_dir = "asc"
    order_whitelist = {"id", "capability", "enabled", "created_at"}

    def list_by_module(self, module_id: int, only_enabled: bool = False) -> RowList:
        sql = "SELECT * FROM biz_module_ai_capability WHERE module_id = ?"
        if only_enabled:
            sql += " AND enabled = 1"
        sql += " ORDER BY capability ASC"
        rows = self.conn.execute(sql, (module_id,)).fetchall()
        result: RowList = []
        for row in rows:
            item = dict(row)
            item["config"] = loads(item.get("config_json"), {}) or {}
            item.pop("config_json", None)
            result.append(item)
        return result

    def get_binding(self, module_id: int, capability: str) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_module_ai_capability WHERE module_id = ? AND capability = ?",
            (module_id, capability),
        ).fetchone()
        if not row:
            return None
        item = dict(row)
        item["config"] = loads(item.get("config_json"), {}) or {}
        item.pop("config_json", None)
        return item

    def set_binding(self, module_id: int, capability: str, enabled: bool = True,
                    prompt_code: str | None = None, config: dict[str, Any] | None = None) -> None:
        existing = self.get_binding(module_id, capability)
        if existing:
            self.update(existing["id"], {
                "enabled": 1 if enabled else 0,
                "prompt_code": prompt_code,
                "config_json": dumps(config) if config is not None else existing.get("config_json"),
            })
            return
        self.insert({
            "module_id": module_id,
            "capability": capability,
            "prompt_code": prompt_code,
            "enabled": 1 if enabled else 0,
            "config_json": dumps(config) if config else None,
        })

    def remove_binding(self, module_id: int, capability: str) -> bool:
        cursor = self.conn.execute(
            "DELETE FROM biz_module_ai_capability WHERE module_id = ? AND capability = ?",
            (module_id, capability),
        )
        return cursor.rowcount > 0
