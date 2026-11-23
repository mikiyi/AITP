"""编号唯一性门禁：库层唯一索引兜底 + 存量消歧 + 取号口径不被绕过。

    python backend/scripts/check_numbering_uniqueness.py

盯的是「取号算对之后，库到底拦不拦」这一段：
next_no 系列只保证同一时刻取到空号，真正的重复靠唯一索引挡在门外。
之前需求 / 场景 / 场景执行三张表只有取号事务、没有索引，任何新调用点漏接
begin_claim 就会静默写出重复号，本门禁把这条底线固化下来；
需求的外部导入幂等键也一并校成与用例 / 缺陷同列同口径。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.db.migrator import init_db, run_migrations  # noqa: E402

_passed = 0
_failed = 0
_failures: list[str] = []

NOW = "2026-11-23T00:00:00+00:00"

#: 表 -> (编号列, 唯一索引列, 是否按项目收敛)
NUMBERED_TABLES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("biz_requirement", "req_no", "uk_biz_requirement_no", ("project_id", "req_no")),
    ("biz_api_scenario", "scenario_no", "uk_biz_api_scenario_no", ("scenario_no",)),
    ("biz_api_scenario_run", "run_no", "uk_biz_api_scenario_run_no", ("run_no",)),
]

#: 外部导入幂等键：三类资产必须同列同口径（需求曾在 052 漏写成普通索引）
EXTERNAL_KEY_INDEXES: list[tuple[str, str]] = [
    ("biz_requirement", "uk_biz_requirement_external"),
    ("biz_test_case", "uk_biz_test_case_external"),
    ("biz_bug", "uk_biz_bug_external"),
]
EXTERNAL_KEY_COLUMNS: tuple[str, ...] = ("project_id", "external_platform", "external_id")
#: 052 给需求建的旧普通索引名，升级后不该再残留
LEGACY_EXTERNAL_INDEX = "idx_biz_requirement_external"


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


def unique_index_columns(conn: sqlite3.Connection, index_name: str) -> list[str]:
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name = ?", (index_name,)
    ).fetchone()
    if row is None or not row[0]:
        return []
    body = str(row[0]).split("(", 1)[1]
    body = body.split(")", 1)[0]
    return [part.strip().strip("`") for part in body.split(",")]


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
    return cast("int", conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def insert_requirement(conn: sqlite3.Connection, project_id: int, req_no: str) -> None:
    conn.execute(
        "INSERT INTO biz_requirement (project_id, req_no, title, content, source,"
        " status, priority, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, '正文', '手工录入', 'draft', 'P2', ?, ?, 0)",
        (project_id, req_no, f"需求{req_no}", NOW, NOW),
    )


def insert_scenario(conn: sqlite3.Connection, project_id: int, scenario_no: str) -> None:
    conn.execute(
        "INSERT INTO biz_api_scenario (scenario_no, project_id, name, description,"
        " steps_json, data_source, status, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, '', '[]', 'inline', 'ready', ?, ?, 0)",
        (scenario_no, project_id, f"场景{scenario_no}", NOW, NOW),
    )


def insert_scenario_run(conn: sqlite3.Connection, project_id: int, run_no: str) -> None:
    conn.execute(
        "INSERT INTO biz_api_scenario_run (run_no, scenario_id, project_id, passed,"
        " record_count, summary_json, duration_ms, created_at, updated_at, is_deleted)"
        " VALUES (?, 1, ?, 1, 1, '[]', 1, ?, ?, 0)",
        (run_no, project_id, NOW, NOW),
    )


def insert_external_requirement(
    conn: sqlite3.Connection, project_id: int, req_no: str, external_id: str
) -> None:
    """同项目、同平台、同外部 id 的外部导入行；req_no 另给，用来隔离是哪个索引在拦。"""
    conn.execute(
        "INSERT INTO biz_requirement (project_id, req_no, title, content, source, status,"
        " priority, external_platform, external_id, external_url, created_at, updated_at,"
        " is_deleted) VALUES (?, ?, ?, '正文', 'jira', 'draft', 'P2', 'jira', ?, '', ?, ?, 0)",
        (project_id, req_no, f"外部{req_no}", external_id, NOW, NOW),
    )


INSERTERS = {
    "biz_requirement": insert_requirement,
    "biz_api_scenario": insert_scenario,
    "biz_api_scenario_run": insert_scenario_run,
}

#: 资产主编号列（表 -> 本表自己发号的列）。引用列不在此列：
#: biz_test_run_case.case_no 是从用例带过来的快照、biz_automation_run.script_no 是外键、
#: biz_exploration_step.step_no 是步骤序号、biz_automation_script_version.version_no 是版本号，
#: 它们的唯一性由各自主表负责，不该在本表上再建编号唯一索引。
MASTER_NUMBER_COLUMNS: dict[str, tuple[str, ...]] = {
    "biz_requirement": ("req_no",),
    "biz_test_case": ("case_no",),
    "biz_bug": ("bug_no",),
    "biz_report": ("report_no",),
    "biz_todo": ("todo_no",),
    "biz_automation_script": ("script_no",),
    "biz_automation_run": ("run_no",),
    "biz_sql_analysis": ("sql_no",),
    "biz_sql_script": ("script_no",),
    "biz_test_data_batch": ("batch_no",),
    "biz_assertion_rule": ("rule_no",),
    "biz_execution_matrix": ("matrix_no",),
    "biz_archive_bundle": ("bundle_no",),
    "biz_exploration_session": ("session_no",),
    "biz_test_schedule": ("schedule_no",),
    "biz_api_scenario": ("scenario_no",),
    "biz_api_scenario_run": ("run_no",),
    "biz_fix_suggestion": ("suggestion_no",),
}

#: 源码里 next_no 系列允许出现的编号列名；出现这里没有的列名说明新增了编号却没兜底
REGISTERED_NUMBERING_COLUMNS: frozenset[str] = frozenset(
    column for columns in MASTER_NUMBER_COLUMNS.values() for column in columns
)

_NUMBERING_CALL = re.compile(
    r"next_(?:no|no_monotonic|case_no)\(\s*[^,]+,\s*(?:self\.table|\"[a-z_]+\")\s*,\s*\"([a-z_]+)\""
)


def column_is_unique_indexed(conn: sqlite3.Connection, table: str, column: str) -> bool:
    for idx in conn.execute(f"PRAGMA index_list({table})"):
        if idx["unique"] != 1:
            continue
        if column in unique_index_columns(conn, str(idx["name"])):
            return True
    return False


def scanned_numbering_columns() -> set[str]:
    """扫后端源码，收集所有走统一取号器的编号列名。"""
    found: set[str] = set()
    for path in (ROOT / "backend" / "aitp").rglob("*.py"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if "next_no(" not in text and "next_case_no(" not in text and "next_no_monotonic(" not in text:
            continue
        found.update(_NUMBERING_CALL.findall(text))
    return found


def drop_indexes_for_upgrade_test(conn: sqlite3.Connection) -> None:
    """退回 075 之前的状态，模拟老库覆盖安装。"""
    for _, _, index_name, _ in NUMBERED_TABLES:
        conn.execute(f"DROP INDEX IF EXISTS {index_name}")
    conn.execute("DROP INDEX IF EXISTS uk_biz_requirement_external")
    conn.execute(
        f"CREATE INDEX IF NOT EXISTS {LEGACY_EXTERNAL_INDEX}"
        " ON biz_requirement(project_id, external_platform, external_id)"
        " WHERE external_id <> '' AND is_deleted = 0"
    )
    conn.execute("DELETE FROM sys_schema_version WHERE version = 75")
    conn.commit()


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-check-numbering-"))
    try:
        # ---------- 1) 索引存在且列组合正确 ----------
        db = workdir / "fresh.db"
        conn = new_db(db)
        for table, no_column, index_name, columns in NUMBERED_TABLES:
            names = [r["name"] for r in conn.execute(f"PRAGMA index_list({table})") if r["unique"] == 1]
            check(f"{table} 编号唯一索引存在", index_name in names, f"实际唯一索引={names}")
            got = unique_index_columns(conn, index_name)
            check(
                f"{index_name} 列组合为 {columns}",
                tuple(got) == columns,
                f"实际={tuple(got)}",
            )
        # ---------- 2) 绿：同号重复插入被库层拦下 ----------
        pa = add_project(conn, "PA")
        pb = add_project(conn, "PB")
        for table, no_column, index_name, columns in NUMBERED_TABLES:
            project_scoped = "project_id" in columns
            inserter = INSERTERS[table]
            first_no = {"biz_requirement": "REQ-001", "biz_api_scenario": "SCN-001",
                        "biz_api_scenario_run": "SCNR-001"}[table]
            inserter(conn, pa, first_no)
            conn.commit()
            same_project_blocked = False
            try:
                inserter(conn, pa, first_no)
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                same_project_blocked = True
            check(f"{table} 同项目（同域）同号被唯一索引拦下", same_project_blocked,
                  "插入成功了，说明索引没生效")
            # 按项目收敛的编号允许跨项目同号；全局编号则跨项目也要拦下
            cross_project_blocked = False
            try:
                inserter(conn, pb, first_no)
                conn.commit()
            except sqlite3.IntegrityError:
                conn.rollback()
                cross_project_blocked = True
            check(
                f"{table} 跨项目同号{'放行（项目内口径）' if project_scoped else '仍拦下（全局口径）'}",
                cross_project_blocked != project_scoped,
                f"projectScoped={project_scoped} crossBlocked={cross_project_blocked}",
            )
        conn.close()

        # ---------- 3) 存量重复：老库覆盖安装能消歧 ----------
        dirty = workdir / "dirty.db"
        conn = new_db(dirty)
        drop_indexes_for_upgrade_test(conn)
        pa = add_project(conn, "PA")
        pb = add_project(conn, "PB")
        insert_requirement(conn, pa, "REQ-001")
        insert_requirement(conn, pa, "REQ-001")
        insert_requirement(conn, pa, "REQ-001")
        insert_requirement(conn, pa, "REQ-002")
        insert_requirement(conn, pb, "REQ-001")
        conn.execute(
            "INSERT INTO biz_requirement (project_id, req_no, title, content, source, status,"
            " priority, external_platform, external_id, created_at, updated_at, is_deleted)"
            " VALUES (?, 'JIRA-123', '外部导入映射号', '正文', 'jira', 'draft', 'P2', 'jira', '123', ?, ?, 0)",
            (pa, NOW, NOW),
        )
        # 并发导入同一个外部 id：应用层 _exists 失效时留下的同键同号重复行
        conn.execute(
            "INSERT INTO biz_requirement (project_id, req_no, title, content, source, status,"
            " priority, external_platform, external_id, created_at, updated_at, is_deleted)"
            " VALUES (?, 'JIRA-123', '外部导入重复行', '正文', 'jira', 'draft', 'P2', 'jira', '123', ?, ?, 0)",
            (pa, NOW, NOW),
        )
        insert_scenario(conn, pa, "SCN-001")
        insert_scenario(conn, pb, "SCN-001")
        insert_scenario(conn, pa, "SCN-002")
        insert_scenario_run(conn, pa, "SCNR-001")
        insert_scenario_run(conn, pb, "SCNR-001")
        conn.commit()
        req_count_before = int(
            conn.execute("SELECT COUNT(*) FROM biz_requirement").fetchone()[0])
        keep_first = conn.execute(
            "SELECT id FROM biz_requirement WHERE project_id = ? AND title = '需求REQ-001'"
            " ORDER BY id LIMIT 1",
            (pa,),
        ).fetchone()["id"]
        keep_external = conn.execute(
            "SELECT id FROM biz_requirement WHERE external_id = '123' ORDER BY id LIMIT 1"
        ).fetchone()["id"]
        dup_external = conn.execute(
            "SELECT id FROM biz_requirement WHERE external_id = '123' ORDER BY id DESC LIMIT 1"
        ).fetchone()["id"]
        #: 迁移不该碰的行：本身就不重复（同项目内唯一 / 跨项目同号 / 外部映射号）
        untouched = {
            r["id"]: r["req_no"]
            for r in conn.execute(
                "SELECT id, req_no FROM biz_requirement"
                " WHERE title IN ('需求REQ-002', '外部导入映射号')"
                " OR (project_id = ? AND title = '需求REQ-001')",
                (pb,),
            )
        }
        conn.close()

        conn = sqlite3.connect(dirty)
        conn.row_factory = sqlite3.Row
        applied = run_migrations(conn)
        check("老库覆盖安装 075 不因存量重复而失败", 75 in applied, f"实际应用={sorted(applied)}")
        for table, no_column, _, columns in NUMBERED_TABLES:
            keys = ", ".join(columns)
            dup = conn.execute(
                f"SELECT COUNT(*) FROM (SELECT {keys} FROM {table} WHERE is_deleted = 0"
                f" GROUP BY {keys} HAVING COUNT(*) > 1)"
            ).fetchone()[0]
            check(f"{table} 消歧后无重复", int(dup) == 0, f"重复组={dup}")
        rows = {r["id"]: r["req_no"] for r in conn.execute("SELECT id, req_no FROM biz_requirement")}
        check(
            "撞号行保留 id 最小的一条不动",
            rows[keep_first] == "REQ-001",
            f"实际={rows[keep_first]}",
        )
        check(
            "无重复的行编号一条没改",
            all(rows[rid] == no for rid, no in untouched.items()),
            f"期望={untouched} 实际={rows}",
        )
        check(
            "确有行被消歧（不是空跑）",
            any(rid != keep_first and rid not in untouched and rows[rid] != "REQ-001" for rid in rows),
            "没有任何行被重排，消歧分支没被走到",
        )
        scn_rows = [r["scenario_no"] for r in conn.execute(
            "SELECT scenario_no FROM biz_api_scenario ORDER BY id")]
        check(
            "场景撞号顺延到同组最大号之后",
            scn_rows[0] == "SCN-001" and scn_rows[1] == "SCN-003" and scn_rows[2] == "SCN-002",
            f"实际={scn_rows}",
        )
        scnr_rows = [r["run_no"] for r in conn.execute(
            "SELECT run_no FROM biz_api_scenario_run ORDER BY id")]
        check("场景执行撞号同样消歧", scnr_rows == ["SCNR-001", "SCNR-002"], f"实际={scnr_rows}")
        # 外部幂等键：升级前存量同键多行也不该让迁移失败
        ext_dup = conn.execute(
            "SELECT COUNT(*) FROM (SELECT project_id, external_platform, external_id"
            " FROM biz_requirement WHERE is_deleted = 0 AND external_id <> ''"
            " GROUP BY project_id, external_platform, external_id HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        check("需求外部幂等键消歧后无重复", int(ext_dup) == 0, f"重复组={ext_dup}")
        legacy_present = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='index' AND name = ?",
            (LEGACY_EXTERNAL_INDEX,),
        ).fetchone() is not None
        live_ext_indexes = [
            str(r["name"])
            for r in conn.execute("PRAGMA index_list(biz_requirement)")
            if r["unique"] == 1 and "external" in str(r["name"])
        ]
        check(
            "老库升级后旧普通索引被唯一索引替换",
            not legacy_present and live_ext_indexes == ["uk_biz_requirement_external"],
            f"残留普通索引={legacy_present} 外部唯一索引={live_ext_indexes}",
        )
        ext_rows = {
            r["id"]: (r["external_id"], r["req_no"])
            for r in conn.execute(
                "SELECT id, external_id, req_no FROM biz_requirement WHERE is_deleted = 0")
        }
        check(
            "同键重复导入保留 id 最小行的外部映射",
            ext_rows[keep_external][0] == "123",
            f"实际={ext_rows[keep_external]}",
        )
        check(
            "重复行只解除外部标记，编号顺延到本组最大号之后",
            ext_rows[dup_external][0] == "" and ext_rows[dup_external][1].startswith("REQ-"),
            f"实际={ext_rows[dup_external]}",
        )
        check(
            "消歧没有删除任何需求行",
            int(conn.execute("SELECT COUNT(*) FROM biz_requirement").fetchone()[0])
            == req_count_before,
            f"升级前={req_count_before} 升级后={conn.execute('SELECT COUNT(*) FROM biz_requirement').fetchone()[0]}",
        )
        conn.close()

        # ---------- 4) 消歧后取号仍拿到干净空号 ----------
        conn = sqlite3.connect(dirty)
        conn.row_factory = sqlite3.Row
        from aitp.repositories.numbering import next_no

        nxt = next_no(conn, "biz_requirement", "req_no", "REQ", pa)
        used = {r["req_no"] for r in conn.execute(
            "SELECT req_no FROM biz_requirement WHERE project_id = ? AND is_deleted = 0", (pa,))}
        check("消歧后需求取号仍避开已用号", nxt not in used, f"取到={nxt} 已用={sorted(used)}")
        nxt_scn = next_no(conn, "biz_api_scenario", "scenario_no", "SCN")
        used_scn = {r["scenario_no"] for r in conn.execute(
            "SELECT scenario_no FROM biz_api_scenario WHERE is_deleted = 0")}
        check("消歧后场景取号仍避开已用号", nxt_scn not in used_scn, f"取到={nxt_scn} 已用={sorted(used_scn)}")
        conn.close()

        # ---------- 5) 口径提醒：软删行不占号（与取号 WHERE is_deleted=0 一致） ----------
        conn = sqlite3.connect(dirty)
        conn.row_factory = sqlite3.Row
        conn.execute(
            "UPDATE biz_requirement SET is_deleted = 1 WHERE project_id = ? AND req_no = ?",
            (pa, "REQ-001"),
        )
        soft_reuse_ok = True
        try:
            insert_requirement(conn, pa, "REQ-001")
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            soft_reuse_ok = False
        check("软删后同号可复用（部分索引口径）", soft_reuse_ok, "唯一索引没带 WHERE is_deleted = 0")
        conn.close()

        # ---------- 6) 主编号列清单：每个都有唯一索引兜底 ----------
        conn = new_db(workdir / "catalog.db")
        missing = [
            f"{table}.{column}"
            for table, columns in MASTER_NUMBER_COLUMNS.items()
            for column in columns
            if not column_is_unique_indexed(conn, table, column)
        ]
        check("全部资产主编号列都有唯一索引兜底", not missing, f"未兜底：{missing}")

        # ---------- 7) 取号调用点不得游离在清单之外 ----------
        # 新加一个 next_no(..., "xxx_no", ...) 却忘了登记进上面的清单 = 新表漏索引，
        # 本断言把它逼出来（引用列如 case_no 快照、step_no 序号不算主编号，不进清单）。
        referenced = scanned_numbering_columns()
        unregistered = sorted(referenced - set(REGISTERED_NUMBERING_COLUMNS))
        check(
            "源码取号调用点的编号列都已登记并兜底",
            not unregistered,
            f"未登记（请补唯一索引并加入本门禁清单）：{unregistered}",
        )
        conn.close()

        # ---------- 8) 外部导入幂等键：三类资产同口径 ----------
        # 052 给用例 / 缺陷建的是 UNIQUE，需求漏成了普通索引——同一条「同项目 + 同平台 +
        # 同外部 id 只应有一条」，需求只有应用层 _exists 在拦。这里固化成同列唯一，
        # 并拦住旧索引回流。
        conn = new_db(workdir / "external.db")
        pa = add_project(conn, "PA")
        pb = add_project(conn, "PB")
        for table, index_name in EXTERNAL_KEY_INDEXES:
            names = [
                str(r["name"])
                for r in conn.execute(f"PRAGMA index_list({table})") if r["unique"] == 1
            ]
            check(f"{table} 外部幂等键唯一索引存在", index_name in names, f"实际唯一索引={names}")
            ext_cols = tuple(unique_index_columns(conn, index_name))
            check(f"{index_name} 列组合与用例 / 缺陷一致", ext_cols == EXTERNAL_KEY_COLUMNS, f"实际={ext_cols}")
        check(
            "需求表不再残留 052 的普通外部索引",
            conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='index' AND name = ?",
                (LEGACY_EXTERNAL_INDEX,),
            ).fetchone() is None,
            "旧索引仍在，需求外部导入仍无库层兜底",
        )
        insert_external_requirement(conn, pa, "JIRA-1", "1")
        conn.commit()
        blocked_by = ""
        try:
            # req_no 换一个，确保拦住它的是外部键索引而不是 (project_id, req_no)
            insert_external_requirement(conn, pa, "JIRA-2", "1")
            conn.commit()
        except sqlite3.IntegrityError as exc:
            conn.rollback()
            blocked_by = str(exc)
        check(
            "同项目同平台同外部 id 的第二条被库层拦下",
            "external_id" in blocked_by,
            f"没拦下或拦在别处：{blocked_by}",
        )
        cross_ok = True
        try:
            insert_external_requirement(conn, pb, "JIRA-1", "1")
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            cross_ok = False
        check("不同项目导入同一外部 id 放行（项目内口径）", cross_ok)
        manual_ok = True
        try:
            # 两行本地需求的 (project_id, '', '') 完全相同：非部分索引会误拦
            insert_requirement(conn, pa, "REQ-900")
            insert_requirement(conn, pa, "REQ-901")
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            manual_ok = False
        check(
            "外部键为空的本地需求不受该索引约束",
            manual_ok,
            "唯一索引没带 WHERE external_id <> ''，会误伤本地需求",
        )
        conn.close()

        # ---------- 9) 矩阵取号确实进写事务（begin_claim 生效） ----------
        # 修复前 _next_matrix_no 的 SELECT 发生在任何事务之外：两个连接各自读到同一个
        # 空号，后提交的那个撞唯一索引报错。现在取号先拿写锁，第二个连接必须等待。
        race = workdir / "race.db"
        conn_a = new_db(race)
        conn_b = sqlite3.connect(race)
        conn_b.row_factory = sqlite3.Row
        conn_a.execute("PRAGMA busy_timeout=1000")
        conn_b.execute("PRAGMA busy_timeout=250")
        from aitp.services.execution_matrix_service import _next_matrix_no

        no_first = _next_matrix_no(conn_a)
        waited = False
        try:
            _next_matrix_no(conn_b)
        except sqlite3.OperationalError:
            waited = True
        check("执行矩阵取号期间第二个连接被写锁挡住", waited,
              "第二个连接立刻取到号，说明取号仍在事务之外")
        conn_a.rollback()
        no_after_release = _next_matrix_no(conn_b)
        check("取号本身不落库，释放后仍取到同一空号", no_after_release == no_first,
              f"第一次={no_first} 释放后={no_after_release}")
        conn_a.close()
        conn_b.close()
        return 1 if _failed else 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    code = main()
    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    raise SystemExit(code)
