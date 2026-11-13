"""模块树验收脚本（BUG-43 回归门禁）。

在临时库上验证：
- /modules/tree 出参契约：任意深度都带 camelCase 的 id/nodeType/parentId
- 创建未传 nodeType 时按父级顺推：顶级=module、模块下=submodule、子模块下=feature
- 显式 nodeType 被尊重；功能点下再挂子节点被深度上限拒绝
- 节点能力绑定接口对合法 id 正常（不再出现 undefined 路径）
- 迁移 059：按深度回填层级角色，软删行不受影响

    python backend/scripts/check_module_tree.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile
from collections.abc import Iterator
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_module_tree.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_module_tree.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def walk(nodes: list[JsonDict], depth: int = 1) -> Iterator[tuple[JsonDict, int]]:
    for node in nodes:
        yield node, depth
        yield from walk(node.get("children") or [], depth + 1)


def check_tree_contract(api: Api, project_id: int) -> None:
    top = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "模块A", "nodeType": "module",
    })
    mid = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "子模块A", "nodeType": "submodule",
        "parentId": top["id"],
    })
    api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "功能A", "nodeType": "feature",
        "parentId": mid["id"],
    })
    tree = api.data("GET", f"/api/v1/modules/tree?projectId={project_id}")["tree"]
    levels = list(walk(tree))
    check("树回传三级结构", len(levels) == 3, str(len(levels)))
    check("任意深度都带 camelCase 契约字段",
          all(isinstance(n.get("id"), int) and n.get("nodeType") in
              ("module", "submodule", "feature") and "parentId" in n
              for n, _ in levels),
          str([{k: n.get(k) for k in ("id", "nodeType", "node_type")} for n, _ in levels])[:300])
    check("深度与角色对应",
          [n.get("nodeType") for n, _ in levels] == ["module", "submodule", "feature"],
          str([n.get("nodeType") for n, _ in levels]))
    check("节点带路径与引用计数",
          all(str(n.get("path") or "").count("/") >= d - 1 and "references" in n
              for n, d in levels),
          str([n.get("path") for n, _ in levels])[:200])

    caps = api.data("GET", f"/api/v1/modules/{mid['id']}/capabilities")
    check("合法 id 的能力绑定接口正常", isinstance(caps, dict), str(caps)[:200])


def check_create_defaults(api: Api, project_id: int) -> None:
    top = api.data("POST", "/api/v1/modules", {"projectId": project_id, "name": "默认顶级"})
    check("未传层级顶级默认 module", top.get("nodeType") == "module", str(top)[:200])
    mid = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "默认二级", "parentId": top["id"],
    })
    check("模块下未传层级默认 submodule", mid.get("nodeType") == "submodule", str(mid)[:200])
    leaf = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "默认三级", "parentId": mid["id"],
    })
    check("子模块下未传层级默认 feature", leaf.get("nodeType") == "feature", str(leaf)[:200])

    explicit = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "显式功能", "parentId": top["id"],
        "nodeType": "feature",
    })
    check("显式层级被尊重", explicit.get("nodeType") == "feature", str(explicit)[:200])

    _, body = api.call("POST", "/api/v1/modules", {
        "projectId": project_id, "name": "越界四级", "parentId": leaf["id"],
    })
    check("功能点下挂子节点被拒绝", body.get("code") == ErrorCode.ILLEGAL_OPERATION,
          str(body)[:200])


def check_case_tree_contract(api: Api, project_id: int) -> None:
    """用例树与模块树同一出参契约（BUG-43 同类：/test-cases/tree 曾回 raw 行）。"""
    tree = api.data("GET", f"/api/v1/modules/tree?projectId={project_id}")["tree"]
    leaf = next(n for n, d in walk(tree) if d == 3)
    api.data("POST", "/api/v1/test-cases", {
        "projectId": project_id, "title": "树契约用例", "moduleId": leaf["id"],
        "caseType": "ui", "priority": "P2",
    })
    data = api.data("GET", f"/api/v1/test-cases/tree?projectId={project_id}")
    levels = list(walk(data["list"]))
    check("用例树任意深度带 camelCase 契约",
          all(isinstance(n.get("id"), int) and n.get("nodeType") in
              ("module", "submodule", "feature") for n, _ in levels),
          str([n.get("nodeType") for n, _ in levels])[:200])
    check("用例树节点带用例计数字段",
          all("caseCount" in n and "cases" in n for n, _ in levels),
          str([{k: n.get(k) for k in ("caseCount",)} for n, _ in levels])[:200])
    hit = next((n for n, _ in levels if n["id"] == leaf["id"]), {})
    check("用例挂到功能点节点", int(hit.get("caseCount") or 0) >= 1, str(hit)[:200])


def check_backfill_migration(workdir: pathlib.Path, project_id: int) -> None:
    """059 回填：整棵错标 'module' 的树按深度归位，软删行不动。"""
    db_path = workdir / "check_module_tree.db"
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "INSERT INTO biz_module (project_id, parent_id, node_type, name, sort_order, status,"
            " is_deleted, created_at, updated_at)"
            " VALUES (?, NULL, 'module', '回填根', 1, 'active', 0, '', '')",
            (project_id,),
        )
        root_id = int(cur.lastrowid or 0)
        cur = conn.execute(
            "INSERT INTO biz_module (project_id, parent_id, node_type, name, sort_order, status,"
            " is_deleted, created_at, updated_at)"
            " VALUES (?, ?, 'module', '回填中', 1, 'active', 0, '', '')",
            (project_id, root_id),
        )
        mid_id = int(cur.lastrowid or 0)
        conn.execute(
            "INSERT INTO biz_module (project_id, parent_id, node_type, name, sort_order, status,"
            " is_deleted, created_at, updated_at)"
            " VALUES (?, ?, 'module', '回填叶', 1, 'active', 0, '', '')",
            (project_id, mid_id),
        )
        conn.execute(
            "INSERT INTO biz_module (project_id, parent_id, node_type, name, sort_order, status,"
            " is_deleted, created_at, updated_at)"
            " VALUES (?, NULL, 'feature', '软删行', 1, 'active', 1, '', '')",
            (project_id,),
        )
        conn.commit()
        sql = (ROOT / "backend" / "aitp" / "db" / "migrations"
               / "059_module_role_by_depth.sql").read_text(encoding="utf-8")
        conn.executescript(sql)
        roles = dict(conn.execute(
            "SELECT name, node_type FROM biz_module WHERE project_id = ? AND is_deleted = 0",
            (project_id,),
        ).fetchall())
        check("059 按深度回填层级角色",
              roles.get("回填根") == "module" and roles.get("回填中") == "submodule"
              and roles.get("回填叶") == "feature",
              str(roles))
        deleted = conn.execute(
            "SELECT node_type FROM biz_module WHERE name = '软删行'",
        ).fetchone()
        check("059 不碰软删行", deleted is not None and deleted[0] == "feature",
              str(deleted))
    finally:
        conn.close()


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_module_tree_"))
    try:
        app = build_app(workdir)
        api = Api(app)
        project = api.data("POST", "/api/v1/projects", {"name": "门禁模块树", "code": "GATE-MT"})
        project_id = int(project["id"])

        check_tree_contract(api, project_id)
        check_create_defaults(api, project_id)
        check_case_tree_contract(api, project_id)
        check_backfill_migration(workdir, project_id)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())