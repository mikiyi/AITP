"""需求批量拆分导入门禁（四期专项 D）。

    python backend/scripts/check_requirement_import.py

覆盖六段：
  1. 迁移 080：批次表、指纹唯一索引、需求表 import_id 列；
  2. 指纹：同一文档稳定、不同文档不同；
  3. 输出 Schema：合法条目通过、缺标题被拦、优先级越界回落；
  4. 拆分：批次与条数落库、同文档复用批次并提示重复、无模型降级不抛错；
  5. 批量采纳：编号递增、来源/状态/批次回填、功能点路径解析、重复采纳幂等；
  6. 隔离与边界：跨项目批次互不可见、空标题条目跳过、空列表直接拒。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.output_schema import validate_output  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.services import lab_service  # noqa: E402
from aitp.services import requirement_import_service as svc  # noqa: E402

NOW = "2026-11-23T00:00:00+00:00"

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


def new_db(path: pathlib.Path) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def add_project(conn: sqlite3.Connection, code: str) -> int:
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 0)",
        (code, f"项目{code}", NOW, NOW),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def add_module(conn: sqlite3.Connection, project_id: int, name: str,
               parent_id: int | None = None, node_type: str = "module") -> int:
    conn.execute(
        "INSERT INTO biz_module (project_id, parent_id, node_type, name, sort_order,"
        " status, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 0, 'active', ?, ?, 0)",
        (project_id, parent_id, node_type, name, NOW, NOW),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


DOC = "登录需求：手机号 + 验证码登录，60 秒限流，连续 5 次错误锁定 15 分钟。"

STUB_ITEMS: list[dict[str, Any]] = [
    {"title": "手机号验证码登录", "description": "有效验证码可登录",
     "acceptance": "1. 5 分钟内登录成功", "modulePath": "登录/验证码", "priority": "P0"},
    {"title": "验证码发送频率限制", "description": "60 秒内只能发一次",
     "acceptance": "1. 第 61 秒可再发", "modulePath": "登录/验证码", "priority": "P1"},
    {"title": "登录失败次数锁定", "description": "连续 5 次错误锁定 15 分钟",
     "acceptance": "1. 第 6 次提示剩余时间", "modulePath": "登录/账号安全", "priority": "P1"},
]


def stub_runner(items: list[dict[str, Any]], *, generated: bool = True) -> svc.Runner:
    """桩：完全按传入条目当作模型产出（已过 schema 的规范化结果）。"""
    def run(_conn: sqlite3.Connection, _project_id: int, _document: str,
            _profile_id: int | None, _max_items: int) -> dict[str, Any]:
        return {
            "generated": generated,
            "items": items,
            "schema": {"ok": generated, "passed": len(items)},
            "message": "" if generated else "模型没有返回可用的需求条目",
            "degraded": False,
        }
    return run


def migration_checks(conn: sqlite3.Connection) -> None:
    table = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='biz_requirement_import'"
    ).fetchone()
    check("迁移 080 建了导入批次表", table is not None)
    index = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='index'"
        " AND name='uk_biz_requirement_import_fingerprint'"
    ).fetchone()
    check("批次表有指纹唯一索引", index is not None)
    columns = {str(row["name"]) for row in conn.execute(
        "PRAGMA table_info(biz_requirement)").fetchall()}
    check("需求表新增 import_id 列", "import_id" in columns, str(sorted(columns)))


def fingerprint_checks() -> None:
    first = svc.fingerprint(DOC)
    check("指纹长度固定 16 位", len(first) == 16 and len(svc.fingerprint(DOC)) == 16, first)
    check("同一文档指纹稳定", first == svc.fingerprint(f"  {DOC}  "))
    check("不同文档指纹不同", first != svc.fingerprint(DOC + "另加退款需求"))


def schema_checks() -> None:
    report = validate_output("requirement_import", STUB_ITEMS)
    check("合法拆分条目全部通过", report.ok and report.passed == 3, report.message)
    check("缺省字段按宽松口径补默认值",
          report.value[0]["modulePath"] == "登录/验证码"
          and report.value[0]["priority"] == "P0", str(report.value[0])[:160])
    bad = validate_output("requirement_import", [{"description": "没有标题"}])
    check("缺标题的条目整条拦下", bad.ok is False and bad.rejected == 1, bad.message)
    messy = validate_output("requirement_import", [
        {"title": "优先级越界", "priority": "P9"},
    ])
    check("优先级越界回落默认 P2（不拦条目）",
          messy.ok and messy.value[0]["priority"] == "P2", str(messy.value)[:160])


def split_checks(conn: sqlite3.Connection, project_id: int) -> int:
    first = svc.split(conn, project_id=project_id, text=DOC, file_name="登录需求.md",
                      runner=stub_runner(STUB_ITEMS))
    check("拆分返回全部条目", first["generated"] and len(first["items"]) == 3,
          str(first)[:200])
    check("首次拆分建了批次并回带 importId",
          bool(first["importId"]) and first["duplicated"] is False, str(first)[:160])
    batch = conn.execute(
        "SELECT * FROM biz_requirement_import WHERE id = ?", (first["importId"],)
    ).fetchone()
    check("批次记录条数与文件名", int(batch["item_count"]) == 3
          and str(batch["file_name"]) == "登录需求.md", str(dict(batch))[:200])

    second = svc.split(conn, project_id=project_id, text=DOC,
                       runner=stub_runner(STUB_ITEMS))
    check("同一文档再次拆分复用批次并标记重复",
          second["duplicated"] is True and second["importId"] == first["importId"],
          str(second)[:160])
    count = int(conn.execute(
        "SELECT COUNT(*) FROM biz_requirement_import WHERE project_id = ?",
        (project_id,),
    ).fetchone()[0])
    check("重复拆分不会新增批次", count == 1, str(count))

    original = lab_service.prepare_run
    try:
        def boom(*_args: Any, **_kwargs: Any) -> Any:
            raise AppError(ErrorCode.AI_CREDENTIAL_MISSING, "无可用模型")

        lab_service.prepare_run = boom
        degraded = svc.split(conn, project_id=project_id, text=DOC + "无模型版")
    finally:
        lab_service.prepare_run = original
    check("无模型时降级为 generated=False 而不是抛错",
          degraded["generated"] is False and degraded["degraded"] is True
          and "AI" in degraded["message"], str(degraded)[:200])
    check("降级时不落批次、不产生条目",
          degraded["importId"] is None and degraded["items"] == [], str(degraded)[:160])
    return int(first["importId"])


def adopt_checks(conn: sqlite3.Connection, project_id: int, import_id: int) -> None:
    login_id = add_module(conn, project_id, "登录", node_type="module")
    verify_id = add_module(conn, project_id, "验证码", login_id, node_type="feature")
    add_module(conn, project_id, "账号安全", login_id, node_type="feature")

    result = svc.adopt(conn, project_id=project_id, items=STUB_ITEMS, import_id=import_id)
    check("批量采纳全部落库", result["created"] == 3 and result["skipped"] == 0,
          str(result)[:200])
    rows = conn.execute(
        "SELECT * FROM biz_requirement WHERE project_id = ? ORDER BY id", (project_id,)
    ).fetchall()
    check("需求来源为 AI、状态为草稿", all(
        str(row["source"]) == "ai" and str(row["status"]) == "draft" for row in rows),
        str([(row["source"], row["status"]) for row in rows]))
    check("需求编号按 REQ 前缀递增", [str(row["req_no"]) for row in rows]
          == ["REQ-001", "REQ-002", "REQ-003"],
          str([row["req_no"] for row in rows]))
    check("需求回指导入批次", all(int(row["import_id"]) == import_id for row in rows),
          str([row["import_id"] for row in rows]))
    check("功能点路径解析到真实节点", int(rows[0]["module_id"]) == verify_id,
          str([rows[0]["module_id"], verify_id]))
    check("正文含描述与验收要点",
          "验收要点" in str(rows[0]["content"]) and "有效验证码可登录" in str(rows[0]["content"]),
          str(rows[0]["content"])[:160])

    again = svc.adopt(conn, project_id=project_id, items=STUB_ITEMS,
                      fingerprint_value=svc.fingerprint(DOC))
    check("重复采纳按外部键全部跳过", again["created"] == 0 and again["skipped"] == 3,
          str(again)[:200])
    total = int(conn.execute(
        "SELECT COUNT(*) FROM biz_requirement WHERE project_id = ?", (project_id,)
    ).fetchone()[0])
    check("重复采纳没有多写需求", total == 3, str(total))

    batch = conn.execute(
        "SELECT * FROM biz_requirement_import WHERE id = ?", (import_id,)
    ).fetchone()
    check("批次已采纳数累计正确", int(batch["adopted_count"]) == 3, str(dict(batch))[:160])

    partial = svc.adopt(conn, project_id=project_id, items=[
        {"title": "", "description": "空标题"},
        {"title": "退款申请", "description": "用户可发起退款", "priority": "P2"},
    ])
    check("空标题条目跳过、其余照常采纳",
          partial["created"] == 1 and partial["skipped"] == 1, str(partial)[:200])

    try:
        svc.adopt(conn, project_id=project_id, items=[])
        check("空列表直接拒绝", False, "未抛出 100100")
    except AppError as exc:
        check("空列表直接拒绝", exc.code == ErrorCode.PARAM_ERROR, str(exc.code))


def isolation_checks(conn: sqlite3.Connection, project_id: int) -> None:
    other = add_project(conn, "IMPORT-B")
    imports = svc.list_imports(conn, other)
    check("另一个项目看不到别家导入批次", imports == [], str(imports)[:160])
    result = svc.adopt(conn, project_id=other, items=[
        {"title": "其他项目需求", "priority": "P1"},
    ])
    check("跨项目采纳落自己的库", result["created"] == 1, str(result)[:160])
    rows = conn.execute(
        "SELECT project_id FROM biz_requirement WHERE title = '其他项目需求'"
    ).fetchall()
    check("跨项目需求归属正确", bool(rows) and int(rows[0]["project_id"]) == other,
          str([dict(r) for r in rows]))
    first_batch = conn.execute(
        "SELECT project_id FROM biz_requirement_import WHERE id = ?", (result["importId"],)
    ).fetchone()
    check("跨项目批次归属正确", int(first_batch["project_id"]) == other,
          str(dict(first_batch)))


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-req-import-"))
    conn = new_db(workdir / "import.db")
    try:
        migration_checks(conn)
        fingerprint_checks()
        schema_checks()
        project_id = add_project(conn, "IMPORT-A")
        import_id = split_checks(conn, project_id)
        adopt_checks(conn, project_id, import_id)
        isolation_checks(conn, project_id)
        batches = svc.list_imports(conn, project_id)
        doc_batch = next(
            (item for item in batches if item["fingerprint"] == svc.fingerprint(DOC)), {}
        )
        check("批次列表回带条数与已采纳数",
              doc_batch.get("itemCount") == 3 and doc_batch.get("adoptedCount") == 3,
              str(batches)[:200])
    finally:
        conn.close()

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    for item in _failures:
        print(f"  [FAIL] {item}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
