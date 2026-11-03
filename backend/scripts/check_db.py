"""验证数据层验收项：PRAGMA、外键约束、种子数据量、软删、并发写。

    python backend/scripts/check_db.py
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile
import threading
import time
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services.knowledge_storage import CONFIG_PATH  # noqa: E402
from seed_demo import seed  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


EXPECTED_COUNTS = {
    "biz_project": 1,
    "biz_requirement": 3,
    "biz_test_case": 12,
    "biz_bug": 5,
    "biz_case_bug_rel": 3,
    "biz_knowledge_article": 6,
    "biz_report": 1,
    "biz_ai_session": 1,
    "biz_ai_message": 4,
}


def check_pragmas(conn: sqlite3.Connection) -> None:
    journal = conn.execute("PRAGMA journal_mode").fetchone()[0]
    check("journal_mode = wal", journal.lower() == "wal", f"实际={journal}")

    fk = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    check("foreign_keys = 1", int(fk) == 1, f"实际={fk}")

    busy = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    check("busy_timeout = 5000", int(busy) == 5000, f"实际={busy}")


def check_foreign_keys(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "INSERT INTO biz_test_case (project_id, title, created_at, updated_at)"
            " VALUES (999999, '越界用例', '2026-11-03 00:00:00', '2026-11-03 00:00:00')"
        )
        conn.rollback()
        check("外键约束拦截非法引用", False, "插入成功，未报错")
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        check("外键约束拦截非法引用", True, str(exc))

    try:
        conn.execute(
            "INSERT INTO biz_report (project_id, report_no, title, report_type,"
            " period_start, period_end, status, created_at, updated_at)"
            " VALUES (999999, 'RPT-X', 'x', 'summary', '2026-01-01', '2026-01-31',"
            " 'draft', '2026-11-03 00:00:00', '2026-11-03 00:00:00')"
        )
        conn.rollback()
        check("外键约束覆盖多表", False, "插入成功，未报错")
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        check("外键约束覆盖多表", True, str(exc))


def check_counts(conn: sqlite3.Connection) -> None:
    for table, expected in EXPECTED_COUNTS.items():
        actual = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        check(f"种子数据 {table} = {expected}", actual == expected, f"实际={actual}")


def check_soft_delete(conn: sqlite3.Connection) -> None:
    before = conn.execute(
        "SELECT COUNT(*) FROM biz_test_case WHERE is_deleted = 0"
    ).fetchone()[0]
    conn.execute("UPDATE biz_test_case SET is_deleted = 1 WHERE id = (SELECT MIN(id) FROM biz_test_case)")
    conn.commit()
    after = conn.execute("SELECT COUNT(*) FROM biz_test_case WHERE is_deleted = 0").fetchone()[0]
    conn.execute("UPDATE biz_test_case SET is_deleted = 0 WHERE id = (SELECT MIN(id) FROM biz_test_case)")
    conn.commit()
    check("软删标记生效", after == before - 1, f"前={before} 后={after}")

    row = conn.execute("SELECT is_deleted FROM biz_test_case WHERE id = (SELECT MIN(id) FROM biz_test_case)").fetchone()
    check("软删可恢复", int(row[0]) == 0)


def check_repositories(conn: sqlite3.Connection) -> None:
    from aitp.errors import AppError
    from aitp.repositories import (
        BugRepository,
        CaseExecutionRepository,
        KnowledgeRepository,
        ProjectRepository,
        ReportRepository,
        RequirementRepository,
        TestCaseRepository,
    )

    projects = ProjectRepository(conn)
    requirements = RequirementRepository(conn)
    cases = TestCaseRepository(conn)
    bugs = BugRepository(conn)
    knowledge = KnowledgeRepository(conn)
    reports = ReportRepository(conn)
    executions = CaseExecutionRepository(conn)

    project = projects.list(filters={"status": "active"}, page_num=1, page_size=1).list[0]
    project_id = project["id"]

    check("项目仓储按状态过滤", projects.count(filters={"status": "active"}) == 1)
    check("需求仓储按项目过滤", len(requirements.list_by_project(project_id)) == 3)

    # 编号生成 + 软删后编号可复用（用例编号按类型分前缀：FT/IT/BT/ST/UIT/ET）
    next_case_no = cases.next_case_no(project_id, "functional")
    check("功能用例编号自动递增", next_case_no == "FT-004", f"实际={next_case_no}")
    check("接口用例编号独立计数", cases.next_case_no(project_id, "interface") == "IT-003",
          cases.next_case_no(project_id, "interface"))

    ft_case = cases.conn.execute(
        "SELECT id FROM biz_test_case WHERE case_no = 'FT-001' AND is_deleted = 0"
    ).fetchone()
    cases.soft_delete(int(ft_case["id"]))
    conn.commit()
    reused = cases.next_case_no(project_id, "functional")
    check("软删后编号可复用", reused == "FT-001", f"实际={reused}")
    conn.execute("UPDATE biz_test_case SET is_deleted = 0 WHERE id = ?", (int(ft_case["id"]),))
    conn.commit()

    # 缺陷状态机
    bug = bugs.conn.execute(
        "SELECT id, status FROM biz_bug WHERE bug_no = 'BUG-001' AND is_deleted = 0"
    ).fetchone()
    bug_id = int(bug["id"])
    # 复位到初始状态，保证脚本可重复执行
    conn.execute("UPDATE biz_bug SET status = 'open' WHERE id = ?", (bug_id,))
    conn.commit()
    log_before = len(bugs.list_logs(bug_id))
    try:
        bugs.transition(bug_id, "retest")
        check("非法状态流转被拦截", False, "未抛异常")
    except AppError as exc:
        check("非法状态流转被拦截", exc.code == 140102, f"code={exc.code}")
    check("非法流转不写流转日志", len(bugs.list_logs(bug_id)) == log_before,
          f"前={log_before} 后={len(bugs.list_logs(bug_id))}")

    bugs.transition(bug_id, "assigned", operator="yichengren", comment="指派")
    bugs.transition(bug_id, "fixed", operator="yichengren", comment="已修复")
    bugs.transition(bug_id, "retest", operator="yichengren", comment="待回归")
    check("合法流转写入日志", len(bugs.list_logs(bug_id)) == log_before + 3,
          f"日志数={len(bugs.list_logs(bug_id))}")

    # 覆盖矩阵与实际用例数一致
    matrix = cases.coverage_matrix(project_id)
    total_in_matrix = sum(row["case_count"] for row in matrix["list"])
    actual_cases = cases.count(filters={"project_id": project_id})
    check("覆盖矩阵用例数与实际一致", total_in_matrix == actual_cases,
          f"矩阵={total_in_matrix} 实际={actual_cases}")
    check("覆盖矩阵需求数与实际一致", len(matrix["list"]) == 3, f"矩阵行数={len(matrix['list'])}")

    # 知识检索
    result = knowledge.search("弱网")
    check("知识仓储混合检索可用", len(result["list"]) > 0, f"hits={len(result['list'])}")

    # 用例-缺陷关联
    case_id = cases.list(filters={"project_id": project_id}, page_num=1, page_size=1).list[0]["id"]
    linked = bugs.linked_case_ids(bug_id)
    check("缺陷-用例关联可读", isinstance(linked, list))

    # 执行记录
    executions.record(case_id, "pass", duration_ms=120)
    conn.commit()
    check("执行记录可写入", cast("dict[str, Any]", executions.latest_by_case(case_id))["result"] == "pass")
    counts = executions.result_counts(project_id)
    check("执行结果聚合可用", counts.get("pass", 0) >= 1, str(counts))

    # 报告
    report = reports.list(filters={"project_id": project_id}, page_num=1, page_size=1).list[0]
    detail = cast("dict[str, Any]", reports.get_detail(report["id"]))
    check("报告分段可读", len(detail["items"]) == 3, f"分段数={len(detail['items'])}")
    check("报告编号自动递增", reports.next_report_no(project_id) == "RPT-002",
          reports.next_report_no(project_id))


def check_v2_module_tree(conn: sqlite3.Connection) -> None:
    """四级模块树 + AI 能力绑定。"""
    from aitp.errors import AppError
    from aitp.repositories import ModuleCapabilityRepository, ModuleRepository

    modules = ModuleRepository(conn)
    caps = ModuleCapabilityRepository(conn)
    project = conn.execute(
        "SELECT id, name FROM biz_project WHERE code = 'DEMO-EC' AND is_deleted = 0"
    ).fetchone()
    if not project:
        check("模块树演示项目存在", False, "缺少 DEMO-EC 项目")
        return
    project_id = int(project["id"])
    project_name = project["name"]

    module_id = modules.create_node(project_id, "登录模块", "module")
    sub_id = modules.create_node(project_id, "手机号登录", "submodule", parent_id=module_id)
    feature_id = modules.create_node(project_id, "验证码时效", "feature", parent_id=sub_id)
    conn.commit()

    check("模块树 depth 派生正确", modules.depth_of(feature_id) == 2,
          f"实际={modules.depth_of(feature_id)}")
    full_path = modules.path_text(feature_id, project_name=project_name)
    check("四级路径拼接完整",
          full_path == f"{project_name}/登录模块/手机号登录/验证码时效", f"实际={full_path}")

    tree = modules.tree(project_id, project_name=project_name)
    feature_node = _find_tree_node(tree, feature_id)
    check("树形接口返回层级深度", feature_node is not None and feature_node["depth"] == 3,
          str(feature_node))
    check("树形接口返回路径", feature_node is not None
          and full_path in feature_node["path"], str(feature_node))

    # 改父防成环
    try:
        modules.move_node(module_id, feature_id)
        check("改父防成环（挂到自己的子孙下）", False, "未抛异常")
    except AppError as exc:
        check("改父防成环（挂到自己的子孙下）", exc.code == 140105, f"code={exc.code}")
    try:
        modules.move_node(module_id, module_id)
        check("改父防成环（挂到自己下面）", False, "未抛异常")
    except AppError as exc:
        check("改父防成环（挂到自己下面）", exc.code == 140105, f"code={exc.code}")

    # 有子节点不可删
    try:
        modules.delete_node(sub_id)
        check("有子节点的节点不可删", False, "未抛异常")
    except AppError as exc:
        check("有子节点的节点不可删", exc.code == 140104, f"code={exc.code}")

    # AI 能力绑定
    caps.set_binding(feature_id, "log_analysis", enabled=True, prompt_code="log-analysis",
                     config={"errorType": "timeout"})
    conn.commit()
    binding = caps.get_binding(feature_id, "log_analysis")
    check("能力绑定写入成功", binding is not None and binding["prompt_code"] == "log-analysis")
    check("能力绑定默认参数可读", binding is not None and binding["config"].get("errorType") == "timeout")
    check("能力绑定列表可读", len(caps.list_by_module(feature_id)) == 1)

    # 用 SAVEPOINT 隔离失败语句，避免回滚掉本函数已写入的数据
    conn.execute("SAVEPOINT uniq_test")
    try:
        conn.execute(
            "INSERT INTO biz_module_ai_capability (module_id, capability, enabled,"
            " created_at, updated_at) VALUES (?, 'log_analysis', 1, '2026-11-03', '2026-11-03')",
            (feature_id,),
        )
        conn.execute("ROLLBACK TO uniq_test")
        conn.execute("RELEASE uniq_test")
        check("能力绑定唯一约束生效", False, "重复插入成功")
    except sqlite3.IntegrityError as exc:
        conn.execute("ROLLBACK TO uniq_test")
        conn.execute("RELEASE uniq_test")
        check("能力绑定唯一约束生效", True, str(exc))

    # 被用例引用的节点不可删
    conn.execute(
        "UPDATE biz_test_case SET module_id = ? WHERE id = (SELECT MIN(id) FROM biz_test_case)",
        (feature_id,),
    )
    conn.commit()
    try:
        modules.delete_node(feature_id)
        check("被引用的节点不可删", False, "未抛异常")
    except AppError as exc:
        check("被引用的节点不可删", exc.code == 140104, f"code={exc.code}")

    # 清理：解除引用后可以删
    conn.execute("UPDATE biz_test_case SET module_id = NULL")
    conn.commit()
    check("解除引用后节点可删", modules.delete_node(feature_id) is True)
    check("删除后子节点可删", modules.delete_node(sub_id) is True)
    check("删除后父节点可删", modules.delete_node(module_id) is True)


def check_v2_knowledge_governance(conn: sqlite3.Connection) -> None:
    """待审池 → 正式库、版本化、引用保护。"""
    from aitp.errors import AppError
    from aitp.repositories import (
        KnowledgeDraftRepository,
        KnowledgeRefRepository,
        KnowledgeRepository,
        KnowledgeVersionRepository,
    )

    drafts = KnowledgeDraftRepository(conn)
    knowledge = KnowledgeRepository(conn)
    versions = KnowledgeVersionRepository(conn)
    refs = KnowledgeRefRepository(conn)

    published_before = knowledge.published_count()
    pending_before = drafts.pending_count()

    draft_id = drafts.create_draft(
        title="支付回调幂等方案草稿",
        content="支付网关重复回调必须以订单号做幂等控制，先查后写加唯一索引。",
        summary="幂等控制要点",
        category="踩坑记录",
        tags="支付,幂等",
        source_type="ai_output",
        ref_type="task",
        ref_id=1,
        producer="bug_analysis",
    )
    check("待审池新增草稿", drafts.pending_count() == pending_before + 1)
    check("待审池不影响正式库计数", knowledge.published_count() == published_before)

    result = drafts.review(draft_id, "approve", reviewer="yichengren", review_comment="内容准确")
    article_id = result["articleId"]
    check("审核通过转正式库", article_id is not None)
    check("转正后正式库 +1", knowledge.published_count() == published_before + 1)
    check("转正后待审池 -1", drafts.pending_count() == pending_before)

    hits = knowledge.search("幂等")["list"]
    check("转正后立即可被全文检索", any(h["id"] == article_id for h in hits),
          f"命中={[h['id'] for h in hits]}")

    # 重复审核被拦截
    try:
        drafts.review(draft_id, "approve", reviewer="yichengren")
        check("重复审核被拦截", False, "未抛异常")
    except AppError as exc:
        check("重复审核被拦截", exc.code == 140105, f"code={exc.code}")

    # 版本化：编辑产生 v2（pending），旧版正文与索引保持不变
    article = cast("dict[str, Any]", knowledge.get(article_id))
    v2_id = versions.create_version(
        article_id,
        title=article["title"],
        content=article["content"] + "\n补充：还需要状态机校验与幂等表。",
        summary=article["summary"],
        category=article["category"],
        tags=article["tags"],
        change_log="补充幂等表说明",
        created_by="yichengren",
    )
    check("编辑产生新版本号", versions.get_version(article_id, 2) is not None)
    check("新版本默认待审", cast("dict[str, Any]", versions.get_version(article_id, 2))["review_status"] == "pending")
    check("待审版本不改变正式正文",
          cast("dict[str, Any]", knowledge.get(article_id))["content"] == article["content"])
    old_hits = knowledge.search("幂等")["list"]
    check("旧版仍可检索", any(h["id"] == article_id for h in old_hits))
    new_hits = knowledge.search("幂等表")["list"]
    check("待审版本内容不进入索引", not any(h["id"] == article_id for h in new_hits),
          f"命中={[h['id'] for h in new_hits]}")

    diff = versions.diff(article_id, 1, 2)
    check("版本 diff 可用", diff["changed"] is True and diff["added"] >= 1, str(diff))

    # 审核通过新版本 → 写回正文
    versions.apply_version(article_id, 2, reviewer="yichengren")
    check("审核通过后正文写回", "幂等表" in cast("dict[str, Any]", knowledge.get(article_id))["content"])
    check("审核通过后当前版本前移",
          cast("dict[str, Any]", knowledge.get(article_id))["current_version"] == 2)
    new_hits = knowledge.search("幂等表")["list"]
    check("审核通过后新版可检索", any(h["id"] == article_id for h in new_hits))

    # 引用保护：被报告引用后禁止撤销
    report_id = int(conn.execute("SELECT MIN(id) FROM biz_report").fetchone()[0])
    refs.add_ref(article_id, "report", report_id, ref_label="测试报告")
    check("引用登记可读", len(refs.list_refs(article_id)) == 1)
    try:
        knowledge.revoke(article_id)
        check("被引用条目禁止撤销", False, "未抛异常")
    except AppError as exc:
        check("被引用条目禁止撤销", exc.code == 140104, f"code={exc.code}")
        check("撤销异常回传引用清单", bool((exc.data or {}).get("refs")), str(exc.data))
    try:
        knowledge.delete_article(article_id)
        check("被引用条目禁止删除", False, "未抛异常")
    except AppError as exc:
        check("被引用条目禁止删除", exc.code == 140104, f"code={exc.code}")

    # 解除引用后可撤销
    refs.remove_ref(article_id, "report", report_id)
    knowledge.revoke(article_id)
    check("解除引用后可撤销", cast("dict[str, Any]", knowledge.get(article_id))["review_status"] == "revoked")
    check("撤销后退出正式库计数", knowledge.published_count() == published_before)
    revoked_hits = knowledge.search("幂等")["list"]
    check("撤销后不再被检索", not any(h["id"] == article_id for h in revoked_hits))

    # 清理：硬删除测试数据，保证脚本可重复执行且不影响种子计数
    conn.execute("DELETE FROM biz_knowledge_draft WHERE id = ?", (draft_id,))
    conn.execute("DELETE FROM biz_knowledge_version WHERE article_id = ?", (article_id,))
    conn.execute("DELETE FROM biz_knowledge_article WHERE id = ?", (article_id,))
    conn.commit()


def _find_tree_node(nodes: list[dict[str, Any]], node_id: int) -> dict[str, Any] | None:
    for node in nodes:
        if node["id"] == node_id:
            return node
        found = _find_tree_node(node.get("children") or [], node_id)
        if found:
            return found
    return None


def check_concurrent_writes(db_path: pathlib.Path) -> None:
    """8 个线程各写 20 条，验证不会抛 'database is locked'。"""
    errors: list[str] = []
    barrier = threading.Barrier(8)
    started = time.perf_counter()

    conn = get_standalone_conn(db_path)
    try:
        session_id = int(conn.execute("SELECT MIN(id) FROM biz_ai_session").fetchone()[0])
    finally:
        conn.close()

    def worker(worker_id: int) -> None:
        conn = None
        try:
            conn = get_standalone_conn(db_path)
            barrier.wait(timeout=10)
            for i in range(20):
                conn.execute(
                    "INSERT INTO biz_ai_message (session_id, role, content, created_at, updated_at)"
                    " VALUES (?, 'user', ?, '2026-11-03 00:00:00', '2026-11-03 00:00:00')",
                    (session_id, f"并发写入测试-{worker_id}-{i}"),
                )
                conn.commit()
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{type(exc).__name__}: {exc}")
        finally:
            # 必须关闭：未关闭的写事务会一直持有写锁，让其它线程拿到 database is locked
            if conn is not None:
                conn.close()

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)

    check("并发写入无 database is locked", not errors, "; ".join(errors[:3]))

    conn = get_standalone_conn(db_path)
    total = conn.execute(
        "SELECT COUNT(*) FROM biz_ai_message WHERE content LIKE '并发写入测试-%'"
    ).fetchone()[0]
    conn.execute("DELETE FROM biz_ai_message WHERE content LIKE '并发写入测试-%'")
    conn.commit()
    conn.close()
    check("并发写入数据全部落库", total == 160, f"实际={total}")
    print(f"    （8 线程 x 20 条，耗时 {time.perf_counter() - started:.2f}s）")


def check_migration_idempotent(db_path: pathlib.Path) -> None:
    from aitp.db.migrator import applied_versions, run_migrations

    conn = get_standalone_conn(db_path)
    try:
        before = applied_versions(conn)
        run_migrations(conn)
        after = applied_versions(conn)
    finally:
        conn.close()
    check("迁移重复执行不产生新版本", before == after, f"前={before} 后={after}")


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-checkdb-"))
    db_path = workdir / "check.db"
    try:
        init_db(db_path)
        # 知识库落盘目录指向临时目录，避免污染项目默认 knowledge/
        conn = get_standalone_conn(db_path)
        SysConfigRepository(conn).set_value(
            CONFIG_PATH, str(workdir / "knowledge"), "string", "校验脚本临时目录")
        conn.commit()
        seed(conn)
        conn.commit()
        conn.close()

        conn = get_standalone_conn(db_path)
        try:
            check_pragmas(conn)
            check_foreign_keys(conn)
            check_counts(conn)
            check_soft_delete(conn)
            check_repositories(conn)
            check_v2_module_tree(conn)
            check_v2_knowledge_governance(conn)
        finally:
            conn.close()

        check_concurrent_writes(db_path)
        check_migration_idempotent(db_path)
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
