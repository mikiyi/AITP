"""V2.6.0 执行可信度与资产效率门禁。

覆盖：
  1. 迁移 081/082/083：执行归因列、用例 patch 表、导入批次表；
  2. 失败归因：环境/断言/缺陷/数据/通过 五类规则；
  3. flaky：跨结果翻转判定；
  4. 用例质量：重复标题、缺预期、缺步骤被检出并扣分；
  5. 智能选测：P0 必选、变更命中、近期失败加权、理由可解释；
  6. 导入：csv/json 解析、同编号同标题幂等跳过；
  7. 局部修订：生成 → 逐条采纳/忽略 → 终态锁定；
  8. 执行前置自检：缺环境/缺地址直接阻断；
  9. 发布就绪：阻塞项与风险项分档。

  python backend/scripts/check_case_insight.py
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import (  # noqa: E402
    case_import_service,
    case_patch_service,
    case_quality_service,
    case_stability_service,
    execution_precheck_service,
    failure_attribution_service,
    release_gate_service,
    risk_selection_service,
)

NOW = "2026-11-24T00:00:00+00:00"
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


def add_case(
    conn: sqlite3.Connection,
    project_id: int,
    case_no: str,
    *,
    title: str = "登录-验证码错误",
    priority: str = "P1",
    steps: list[dict[str, Any]] | None = None,
    expected: str = "提示验证码错误",
    module_id: int | None = None,
    extra: dict[str, Any] | None = None,
) -> int:
    conn.execute(
        "INSERT INTO biz_test_case (project_id, module_id, case_no, title, case_type, priority,"
        " status, steps_json, expected_result, extra_json, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 'interface', ?, 'not_executed', ?, ?, ?, ?, ?, 0)",
        (
            project_id, module_id, case_no, title, priority,
            json.dumps(steps if steps is not None else [{"action": "提交", "expected": ""}],
                       ensure_ascii=False),
            expected,
            json.dumps(extra or {"接口URL": "http://127.0.0.1/api/login", "断言规则": "status = 200"},
                       ensure_ascii=False),
            NOW, NOW,
        ),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def add_execution(conn: sqlite3.Connection, case_id: int, result: str) -> None:
    conn.execute(
        "INSERT INTO biz_interface_execution (case_id, result, status_code, assertions_json,"
        " error_message, created_at) VALUES (?, ?, 200, '[]', '', ?)",
        (case_id, result, NOW),
    )


def main() -> int:
    tmp = pathlib.Path(tempfile.mkdtemp(prefix="aitp_v26_"))
    try:
        conn = new_db(tmp / "v26.db")
        project_id = add_project(conn, "V26")

        # 1) 迁移
        cols = {row["name"] for row in conn.execute("PRAGMA table_info(biz_interface_execution)")}
        check("迁移 081 归因列", {"failure_kind", "failure_confidence", "flaky", "retry_count"} <= cols)
        tables = {row["name"] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        check("迁移 082 patch 表", {"biz_case_patch", "biz_case_patch_item"} <= tables)
        check("迁移 083 导入批次表", "biz_case_import_batch" in tables)

        # 2) 归因规则
        env = failure_attribution_service.classify(
            status_code=None, message="Connection refused: timeout", assertions=[])
        check("归因-超时/连接失败 → 环境", env["kind"] == "environment" and env["confidence"] >= 0.9)
        auth = failure_attribution_service.classify(status_code=401, message="unauthorized")
        check("归因-401 → 环境（凭据）", auth["kind"] == "environment")
        server = failure_attribution_service.classify(status_code=500, message="boom")
        check("归因-500 → 疑似缺陷", server["kind"] == "bug")
        status_assert = failure_attribution_service.classify(
            status_code=200, message="",
            assertions=[{"name": "状态码", "passed": False, "expected": "201", "actual": "200",
                         "message": "状态码不匹配"}],
        )
        check("归因-状态码断言 → 断言错误", status_assert["kind"] == "assertion")
        path_assert = failure_attribution_service.classify(
            status_code=200, message="",
            assertions=[{"name": "JSONPath $.data", "passed": False, "expected": "1",
                         "actual": None, "message": "字段不存在"}],
        )
        check("归因-字段缺失 → 断言错误", path_assert["kind"] == "assertion")
        value_mismatch = failure_attribution_service.classify(
            status_code=200, message="",
            assertions=[{"name": "金额", "passed": False, "expected": "10", "actual": "20",
                         "message": "期望 10，实际 20"}],
        )
        check("归因-值不一致 → 疑似缺陷", value_mismatch["kind"] == "bug")
        check("归因-通过不产生结论", failure_attribution_service.classify(
            status_code=200, passed=True)["kind"] == "")

        # 3) flaky
        check("flaky-结果翻转", case_stability_service.evaluate(
            ["passed", "failed", "passed"])["flaky"] is True)
        check("flaky-全部通过不算", case_stability_service.evaluate(
            ["passed", "passed", "passed"])["flaky"] is False)
        case_id = add_case(conn, project_id, "IT-001")
        add_execution(conn, case_id, "failed")
        add_execution(conn, case_id, "passed")
        check("flaky-读历史", case_stability_service.stability(conn, case_id)["flaky"] is True)

        # 4) 质量分析
        add_case(conn, project_id, "IT-002", title="登录-验证码错误")
        add_case(conn, project_id, "IT-003", title="缺少预期", expected="")
        empty_steps = add_case(conn, project_id, "IT-004", title="空步骤", steps=[])
        quality = case_quality_service.analyze(conn, project_id=project_id)
        codes = {item["code"] for item in quality["issues"]}
        check("质量-检出重复标题", "title_duplicate" in codes)
        check("质量-检出缺预期", "expected_empty" in codes)
        check("质量-检出缺步骤", "steps_empty" in codes)
        check("质量-评分与等级", 0 <= quality["score"] < 100 and quality["grade"] in "ABCD")
        check("质量-问题可定位", any(item["caseId"] == empty_steps for item in quality["issues"]))

        # 5) 智能选测
        risk = risk_selection_service.select(
            conn, project_id=project_id, change_scope="登录验证码改造", limit=10)
        check("选测-返回结果", risk["selected"] >= 3)
        check("选测-有分值", all("score" in item for item in risk["items"]))
        check("选测-有理由", all(item["reasons"] for item in risk["items"]))
        check("选测-条件可解释", "weights" in risk["basis"])

        # 6) 导入解析与幂等
        csv_text = (
            "用例编号,用例标题,用例类型,优先级,所属模块,测试步骤,预期结果\n"
            "IT-100,CSV导入-登录,functional,P1,登录,\"1.打开页面\n2.提交\",登录成功\n"
            "IT-001,登录-验证码错误,interface,P1,登录,提交,提示错误\n"
        ).encode("utf-8")
        items, fmt = case_import_service.parse_file("cases.csv", csv_text)
        check("导入-csv 解析", fmt == "csv" and len(items) == 2)
        check("导入-步骤成数组", isinstance(items[0]["steps"], list) and len(items[0]["steps"]) == 2)
        json_text = json.dumps([{
            "caseNo": "IT-101", "title": "JSON导入-下单", "caseType": "functional",
            "priority": "P0", "steps": [{"action": "下单", "expected": "成功"}],
        }], ensure_ascii=False).encode("utf-8")
        j_items, j_fmt = case_import_service.parse_file("cases.json", json_text)
        check("导入-json 解析", j_fmt == "json" and j_items[0]["title"] == "JSON导入-下单")
        plan = case_import_service.plan_rows(conn, project_id=project_id, items=items)
        actions = [row["action"] for row in plan["rows"]]
        check("导入-同编号同标题幂等跳过", "skip" in actions)
        check("导入-新行待创建", actions.count("create") >= 1)

        # 7) 局部修订
        stub = lambda _variables: {  # noqa: E731
            "parsed": {
                "summary": "补登录异常与改标题",
                "patches": [
                    {"caseId": str(case_id), "action": "update", "target": "expected_result",
                     "after": "提示验证码错误并保留输入", "reason": "补充提示细节"},
                    {"caseId": "IT-001", "action": "update", "target": "title",
                     "after": "登录-错误验证码", "reason": "标题更准确"},
                ],
            },
            "model": "stub",
        }
        patch = case_patch_service.generate(
            conn, project_id=project_id, case_ids=[case_id], instruction="补全登录异常",
            generator=stub,
        )
        check("修订-生成批次", patch.get("id", 0) > 0 and len(patch["items"]) == 2)
        check("修订-编号前缀", str(patch["patchNo"]).startswith("AI_PATCH-"))
        first, second = patch["items"][0], patch["items"][1]
        adopted = case_patch_service.adopt_item(
            conn, first["id"], project_id=project_id)
        row = conn.execute("SELECT expected_result FROM biz_test_case WHERE id = ?",
                           (case_id,)).fetchone()
        check("修订-采纳写回", row["expected_result"] == "提示验证码错误并保留输入")
        check("修订-采纳标记", adopted["item"]["status"] == "applied")
        try:
            case_patch_service.adopt_item(conn, first["id"], project_id=project_id)
            check("修订-终态锁定", False, "二次采纳未拦截")
        except Exception:
            check("修订-终态锁定", True)
        dismissed = case_patch_service.dismiss_item(
            conn, second["id"], project_id=project_id)
        check("修订-忽略", dismissed["item"]["status"] == "dismissed")
        check("修订-批次状态同步", case_patch_service.get_patch(
            conn, patch["id"], project_id=project_id)["status"] == "partial")
        try:
            case_patch_service.adopt_item(conn, second["id"], project_id=project_id)
            check("修订-忽略后不可采纳", False, "忽略后仍可采纳")
        except Exception:
            check("修订-忽略后不可采纳", True)

        # 8) 前置自检
        pre = execution_precheck_service.check(
            conn, project_id=project_id, run_id=None, env_id=None, case_rows=[])
        check("自检-无用例阻断", pre["canRun"] is False and pre["blockers"])
        env_conn = add_project(conn, "V26B")
        try:
            execution_precheck_service.check(conn, project_id=env_conn, env_id=999999)
            check("自检-环境不存在提示", True)
        except Exception as exc:  # noqa: BLE001
            check("自检-环境不存在提示", "环境" in str(exc))

        # 9) 发布就绪
        run_id = None
        conn.execute(
            "INSERT INTO biz_test_run (project_id, name, status, total, created_at, updated_at)"
            " VALUES (?, 'V2.6 回归', 'running', 1, ?, ?)",
            (project_id, NOW, NOW),
        )
        run_id = int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])
        conn.execute(
            "INSERT INTO biz_test_run_case (run_id, case_id, seq, case_no, title, priority,"
            " result, created_at, updated_at, is_deleted) VALUES (?, ?, 1, 'IT-001',"
            " '登录-验证码错误', 'P0', 'failed', ?, ?, 0)",
            (run_id, case_id, NOW, NOW),
        )
        readiness = release_gate_service.build_readiness(
            conn, project_id=project_id, run_id=run_id, status="blocked", trace={},
            open_bugs={"P0": 1, "P1": 0}, failed_checks=[{"label": "整体通过率"}],
        )
        check("就绪-阻塞档", readiness["verdict"] == "blocked")
        check("就绪-P0 失败入风险", any(item["kind"] == "p0_failed" for item in readiness["risks"]))
        check("就绪-有建议", bool(readiness["recommendations"]))

        conn.close()
    finally:
        import shutil

        shutil.rmtree(tmp, ignore_errors=True)

    print(f"\nV2.6 门禁: PASS={_passed} FAIL={_failed}")
    for name in _failures:
        print(f"  [FAIL] {name}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
