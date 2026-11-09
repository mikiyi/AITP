"""分页与限量门禁（GAP-25 / C2 批次）。

盯的是「万级数据下后端有没有把返回形状限住」这一件事，判据来自
docs/AITP-二期工程清单.md §5.5 的 C2 实施规格：

  1. 单页返回条目不超过 `MAX_PAGE_SIZE`（前端传超了也要收敛，不能顺着给）；
  2. 树 / 分组接口受限返回，并且带「还能再取」的口径字段（游标或总量），
     只限量不给口径等于把卡顿换成丢数据；
  3. `total` 口径与库里实际条数一致（列表说多少就是多少，C1 的矩阵统计吃过这个亏）；
  4. 深翻页 / 游标翻完不丢数据、不重号；
  5. 导出：同步导出前先过行数闸门，后台导出任务能取回与列表同口径的产物；
  6. 静态断言：api 层不得新增「无上限全量列表」（GAP-25 功能点 4）。

跑的是临时库自造的万级数据 + Flask test_client：不起服务、不占端口、
不碰 data/aitp.db、不调任何大模型。

    python backend/scripts/check_pagination.py
    python backend/scripts/check_pagination.py --rows 20000

退出码：FAIL=0 为 0，任何 FAIL 为 1。
"""

from __future__ import annotations

import argparse
import ast
import csv
import io
import pathlib
import sqlite3
import sys
import time
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.common.pagination import MAX_PAGE_SIZE  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

V1 = "/api/v1"

_passed = 0
_failed = 0

#: 已知的「同步导出仍在全量取数」遗留点（GAP-25 未收口，按文件名放行）。
#: 门禁不因此判红，但会原样打印；出现不在名单里的新站点就判 FAIL。
KNOWN_UNBOUNDED_EXPORTS = {
    "knowledge.py": "export_articles 仍走 list_all(limit=10000)",
    "requirements.py": "export_requirements 仍走 list_all(limit=10000)",
}
#: 树接口单批用例上限（api/cases.py TREE_MAX_PAGE_SIZE）
TREE_MAX_PAGE_SIZE = 1000
#: 每页取数上限的判定基准：列表类一律按 MAX_PAGE_SIZE，树类按单批上限
SCAN_LIST_LIMIT = 2000


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        return
    _failed += 1
    print(f"[FAIL] {name}  {detail}", flush=True)


def info(message: str) -> None:
    print(f"[DATA] {message}", flush=True)


def num(payload: JsonDict, key: str) -> int:
    """取数值字段：缺失或类型不对记 -1，绝不把真实的 0 当成「没有」。

    C1 的矩阵统计就是在 `x or 默认值` 上把 0 读成了默认值，这里统一走这个入口。
    """
    raw = payload.get(key)
    if isinstance(raw, bool):
        return -1
    try:
        return int(cast(Any, raw))
    except (TypeError, ValueError):
        return -1


def items(payload: JsonDict, key: str = "list") -> list[Any]:
    value = payload.get(key)
    return value if isinstance(value, list) else []


def envelope(resp: TestResponse) -> JsonDict:
    body = resp.get_json(silent=True)
    if body is None:
        return {"code": -1, "message": f"非 JSON 响应 http={resp.status_code}", "data": None}
    return cast(JsonDict, body)


class Api:
    """薄封装：只拆包封，成功以外的形态直接抛错，交给 check 记 FAIL。"""

    def __init__(self, client: Any) -> None:
        self.client = client

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             params: JsonDict | None = None) -> JsonDict:
        resp = cast(TestResponse, self.client.open(
            path, method=method, json=payload, query_string=params))
        body = envelope(resp)
        if body.get("code") != 200000:
            raise AssertionError(
                f"{method} {path} http={resp.status_code} "
                f"code={body.get('code')} msg={body.get('message')}")
        data = body.get("data")
        return cast(JsonDict, data if isinstance(data, dict) else {"value": data})

    def raw(self, method: str, path: str, payload: JsonDict | None = None,
            params: JsonDict | None = None) -> TestResponse:
        return cast(TestResponse, self.client.open(
            path, method=method, json=payload, query_string=params))


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "pagination.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "pagination.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


# ---------------- 造数 ----------------

def seed(workdir: pathlib.Path, api: Api, rows: int) -> JsonDict:
    """建项目 + 功能点，然后按 SQL 直灌万级用例 / 缺陷 / 需求。

    走 SQL 而不是走 API：API 建 1 万条要 1 万次请求，门禁耗时就爆了；
    编号用固定前缀 + 序号，保证唯一索引不冲突，翻完之后还能按前缀核对总数。
    """
    db_path = workdir / "pagination.db"
    project = api.data("POST", f"{V1}/projects", {"code": "PGT", "name": "分页门禁项目"})
    project_id = int(project["id"])
    module = api.data("POST", f"{V1}/modules", {"projectId": project_id, "name": "分页门禁模块"})
    module_id = int(module["id"])

    stamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    conn = sqlite3.connect(str(db_path), timeout=30.0)
    conn.execute("PRAGMA busy_timeout = 30000")
    try:
        conn.execute(
            "INSERT INTO biz_test_case (project_id, module_id, case_no, title, case_type,"
            " priority, status, steps_json, source, created_at, updated_at, is_deleted)"
            " WITH RECURSIVE cnt(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM cnt WHERE x<?)"
            " SELECT ?, CASE WHEN x % 5 = 0 THEN NULL ELSE ? END,"
            " 'PGT-C' || printf('%06d', x), '分页门禁用例' || x, 'functional',"
            " 'P2', 'not_executed', '[]', 'manual', ?, ?, 0 FROM cnt",
            (rows, project_id, module_id, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO biz_bug (project_id, bug_no, title, severity, status, priority,"
            " bug_type, steps_json, created_at, updated_at, is_deleted)"
            " WITH RECURSIVE cnt(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM cnt WHERE x<?)"
            " SELECT ?, 'PGT-B' || printf('%06d', x), '分页门禁缺陷' || x, 'P2', 'open',"
            " 'P2', 'functional', '[]', ?, ?, 0 FROM cnt",
            (SCAN_LIST_LIMIT, project_id, stamp, stamp),
        )
        conn.execute(
            "INSERT INTO biz_requirement (project_id, req_no, title, status, priority,"
            " created_at, updated_at, is_deleted)"
            " WITH RECURSIVE cnt(x) AS (VALUES(1) UNION ALL SELECT x+1 FROM cnt WHERE x<?)"
            " SELECT ?, 'PGT-R' || printf('%06d', x), '分页门禁需求' || x, 'draft', 'P2',"
            " ?, ?, 0 FROM cnt",
            (SCAN_LIST_LIMIT, project_id, stamp, stamp),
        )
        conn.commit()
    finally:
        # 必须关掉：造数连接一旦留着未提交事务，app 侧的写会排队到 busy_timeout 才报错
        conn.close()

    counts = SqlCount(db_path)
    info(f"造数完成：项目 {project_id} 用例={counts.case_total()} 缺陷={counts.bug_total()}"
         f" 需求={counts.req_total()}（万级用例={rows}）")
    return {"projectId": project_id, "moduleId": module_id, "dbPath": str(db_path)}


class SqlCount:
    """直连库做口径核对：门禁不能只信接口自己报的 total。"""

    def __init__(self, db_path: pathlib.Path | str) -> None:
        self.db_path = str(db_path)

    def scalar(self, sql: str) -> int:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            return int(conn.execute(sql).fetchone()[0])
        finally:
            conn.close()

    def case_total(self) -> int:
        return self.scalar(
            "SELECT COUNT(*) FROM biz_test_case WHERE case_no LIKE 'PGT-C%' AND is_deleted = 0")

    def bug_total(self) -> int:
        return self.scalar(
            "SELECT COUNT(*) FROM biz_bug WHERE bug_no LIKE 'PGT-B%' AND is_deleted = 0")

    def req_total(self) -> int:
        return self.scalar(
            "SELECT COUNT(*) FROM biz_requirement WHERE req_no LIKE 'PGT-R%' AND is_deleted = 0")

    def case_ids(self) -> set[int]:
        conn = sqlite3.connect(self.db_path, timeout=30.0)
        try:
            return {int(row[0]) for row in conn.execute(
                "SELECT id FROM biz_test_case WHERE case_no LIKE 'PGT-C%' AND is_deleted = 0")}
        finally:
            conn.close()


# ---------------- 判据 1/3/4：分页列表 ----------------

def check_paged_list(api: Api, counts: SqlCount, path: str, label: str,
                     project_id: int, key: str, expected_total: int) -> None:
    """列表接口：超上限要收敛、total 要与库里一致、深翻页不丢不重。"""
    oversized = api.data("GET", path, params={
        "projectId": project_id, "pageNum": 1, "pageSize": MAX_PAGE_SIZE * 50})
    rows = items(oversized)
    check(f"{label} 单页不超过 MAX_PAGE_SIZE",
          len(rows) <= MAX_PAGE_SIZE, f"实际单页 {len(rows)} 条")
    check(f"{label} pageSize 被后端收敛",
          num(oversized, "pageSize") <= MAX_PAGE_SIZE,
          f"回显 pageSize={oversized.get('pageSize')}")
    check(f"{label} total 与库里条数一致",
          num(oversized, "total") == expected_total,
          f"接口 total={oversized.get('total')} 库里={expected_total}（口径：{key}）")

    # 深翻页：按 pages 逐页翻到底，收集主键，既验不丢也验不重
    pages = num(oversized, "pages")
    limit = num(oversized, "pageSize")
    if limit <= 0:
        limit = MAX_PAGE_SIZE
    seen: list[Any] = []
    page_num = 1
    started = time.perf_counter()
    while page_num <= max(1, pages) + 2:
        page = api.data("GET", path, params={
            "projectId": project_id, "pageNum": page_num, "pageSize": limit})
        chunk = items(page)
        if not chunk:
            break
        seen.extend(item.get(key) for item in chunk)
        page_num += 1
    elapsed = (time.perf_counter() - started) * 1000
    check(f"{label} 深翻页不丢数据", len(set(seen)) == expected_total,
          f"翻到 {len(set(seen))} 条 / 期望 {expected_total} 条，耗时 {elapsed:.0f}ms")
    check(f"{label} 深翻页不重号", len(seen) == len(set(seen)),
          f"累计 {len(seen)} 条、去重后 {len(set(seen))} 条")


# ---------------- 判据 2：树接口受限返回 ----------------

def check_case_tree(api: Api, counts: SqlCount, project_id: int) -> None:
    """用例树：一次只回一批用例 + 明确的续取口径，节点计数与总量对得上。"""
    tree = api.data("GET", f"{V1}/test-cases/tree", params={"projectId": project_id})
    returned = num(tree, "returned")
    page_size = num(tree, "pageSize")
    total = num(tree, "total")
    cases_in_payload = collect_tree_cases(tree)

    check("用例树 单批用例数受上限约束",
          returned <= TREE_MAX_PAGE_SIZE and len(cases_in_payload) <= TREE_MAX_PAGE_SIZE,
          f"returned={returned} 实收={len(cases_in_payload)} 上限={TREE_MAX_PAGE_SIZE}")
    check("用例树 不再一次吐全量",
          len(cases_in_payload) < total or total <= TREE_MAX_PAGE_SIZE,
          f"实收 {len(cases_in_payload)} 条 / total={total}，仍在拉全量")
    check("用例树 带续取口径字段",
          all(name in tree for name in ("nextCursor", "pageSize", "returned", "total")),
          f"字段={sorted(tree.keys())}")
    check("用例树 total 与库里条数一致", total == counts.case_total(),
          f"接口 total={total} 库里={counts.case_total()}")
    check("用例树 节点计数 + 孤儿 = total", node_case_counts(tree) == total,
          f"节点求和={node_case_counts(tree)} total={total}")

    # 游标翻到底：所有用例主键（含孤儿）不丢不重
    collected: set[Any] = set()
    cursor = 0
    rounds = 0
    while rounds < 400:
        batch = api.data("GET", f"{V1}/test-cases/tree", params={
            "projectId": project_id, "pageSize": page_size if page_size > 0 else 200,
            "afterId": cursor})
        found = collect_tree_cases(batch)
        if not found and num(batch, "returned") == 0:
            break
        collected.update(item for item in found if item is not None)
        cursor = num(batch, "nextCursor")
        rounds += 1
        if cursor <= 0:
            break
    check("用例树 游标翻完不丢数据", len(collected) == total,
          f"游标累计 {len(collected)} 条 / total={total}（{rounds} 批）")
    oversized = api.data("GET", f"{V1}/test-cases/tree", params={
        "projectId": project_id, "pageSize": TREE_MAX_PAGE_SIZE * 10})
    check("用例树 pageSize 超上限被收敛",
          num(oversized, "pageSize") <= TREE_MAX_PAGE_SIZE,
          f"回显 pageSize={oversized.get('pageSize')}")


def collect_tree_cases(payload: JsonDict) -> list[Any]:
    """树里的用例分两处：功能点节点的 cases、没挂功能点的 orphans。"""
    found: list[Any] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            no = node.get("caseNo")
            if no is not None:
                found.append(no)
            for value in node.values():
                if isinstance(value, (list, dict)):
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    walk({"root": payload.get("list") or [], "orphans": payload.get("orphans") or []})
    return found


def node_case_counts(payload: JsonDict) -> int:
    """节点上声明的 caseCount 之和 + orphanCount，应与 total 同口径。"""
    total = 0

    def walk(nodes: Any) -> None:
        nonlocal total
        if isinstance(nodes, list):
            for node in nodes:
                if isinstance(node, dict):
                    total += int(node.get("caseCount") or 0)
                    walk(node.get("children") or [])

    walk(payload.get("list") or [])
    return total + int(payload.get("orphanCount") or 0)


# ---------------- 判据 5：导出 ----------------

def csv_rows(content: bytes) -> int:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.reader(io.StringIO(text))
    return max(0, len([row for row in reader if any(cell.strip() for cell in row)]) - 1)


def check_exports(api: Api, project_id: int, expected_total: int) -> None:
    """同步导出行数完整；后台导出任务能取回同口径产物（GAP-25 功能点 3）。"""
    started = time.perf_counter()
    resp = api.raw("GET", f"{V1}/test-cases/export", params={
        "projectId": project_id, "format": "csv"})
    if resp.status_code != 200:
        check("用例同步导出成功", False, f"http={resp.status_code}")
        return
    direct = csv_rows(resp.data)
    ms = (time.perf_counter() - started) * 1000
    check("用例同步导出行数与库里一致", direct == expected_total,
          f"导出 {direct} 行 / 库里 {expected_total} 条，耗时 {ms:.0f}ms")

    task = api.data("POST", f"{V1}/test-cases/export/async",
                    payload={"format": "csv", "query": {"projectId": project_id}})
    task_id = int(task.get("id") or 0)
    check("用例后台导出返回 taskId", task_id > 0, f"快照={str(task)[:160]}")
    status = ""
    for _ in range(120):
        snap = api.data("GET", f"{V1}/tasks/{task_id}")
        status = str(snap.get("status") or "")
        if status in ("success", "failed", "cancelled"):
            break
        time.sleep(0.5)
    check("用例后台导出任务跑完", status == "success",
          f"taskId={task_id} status={status} 明细={str(snap)[:200]}")
    if status != "success":
        return
    file_resp = api.raw("GET", f"{V1}/test-cases/export/files/{task_id}")
    check("用例后台导出产物可下载", file_resp.status_code == 200,
          f"http={file_resp.status_code}")
    artifact = csv_rows(file_resp.data)
    check("用例后台导出产物行数与库里一致", artifact == expected_total,
          f"产物 {artifact} 行 / 库里 {expected_total} 条")


# ---------------- 判据 6：静态断言 ----------------

def list_all_limits(path: pathlib.Path) -> list[tuple[int, int]]:
    """AST 里真实存在的 `xxx.list_all(..., limit=N)` 调用点（行号, N）。

    用 AST 而不是逐行找字符串：docstring 里「不再一次 list_all(limit=10000)」
    这种说明文字会被文本扫描误判成违规点，门禁就会假红。
    """
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[tuple[int, int]] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute) or func.attr != "list_all":
            continue
        for keyword in node.keywords:
            if keyword.arg != "limit":
                continue
            value = keyword.value
            if isinstance(value, ast.Constant) and isinstance(value.value, int):
                found.append((node.lineno, int(value.value)))
    return found


def check_static_no_unbounded_lists(api_files: list[pathlib.Path]) -> None:
    """禁止新增「无上限全量列表」：api 层超过 SCAN_LIST_LIMIT 的 list_all 只允许已知遗留点。"""
    offenders: list[str] = []
    for path in api_files:
        for lineno, limit in list_all_limits(path):
            if limit <= SCAN_LIST_LIMIT or path.name in KNOWN_UNBOUNDED_EXPORTS:
                continue
            offenders.append(f"{path.name}:{lineno} limit={limit}")
    check("api 层没有新增无上限全量列表", not offenders, "; ".join(offenders))
    leftovers = [f"{name}（{why}）" for name, why in sorted(KNOWN_UNBOUNDED_EXPORTS.items())]
    info(f"扫描 api 文件 {len(api_files)} 个；GAP-25 遗留未收口的同步全量导出："
         + "、".join(leftovers))


def check_sync_export_guard() -> None:
    """同步导出必须先过行数闸门（export_jobs.guard_sync_rows），否则大表会把请求挂死。"""
    missing: list[str] = []
    for name in ("bugs.py", "cases.py"):
        text = (ROOT / "backend" / "aitp" / "api" / name).read_text(encoding="utf-8")
        if "guard_sync_rows" not in text:
            missing.append(name)
    check("同步导出接了行数闸门", not missing, f"缺 guard_sync_rows：{missing}")


# ---------------- 主流程 ----------------

def main() -> int:
    parser = argparse.ArgumentParser(description="GAP-25 分页与限量门禁")
    parser.add_argument("--rows", type=int, default=10000, help="用例造数行数，默认 10000")
    args = parser.parse_args()

    workdir = pathlib.Path(__file__).resolve().parents[2] / "logs" / "gate_pagination"
    workdir.mkdir(parents=True, exist_ok=True)
    for stale in workdir.glob("pagination.db*"):
        stale.unlink(missing_ok=True)

    app = build_app(workdir)
    with app.app_context():
        api = Api(app.test_client())
        seeded = seed(workdir, api, args.rows)
        project_id = int(seeded["projectId"])
        counts = SqlCount(seeded["dbPath"])
        expected = counts.case_total()
        if expected < 5000:
            print(f"[FAIL] 造数不足万级（实际 {expected} 条），门禁判据不成立", flush=True)
            return 1

        check_paged_list(api, counts, f"{V1}/test-cases", "用例列表", project_id,
                         "id", expected)
        check_paged_list(api, counts, f"{V1}/bugs", "缺陷列表", project_id,
                         "id", counts.bug_total())
        check_paged_list(api, counts, f"{V1}/requirements", "需求列表", project_id,
                         "id", counts.req_total())
        check_case_tree(api, counts, project_id)

        articles = api.data("GET", f"{V1}/knowledge/articles", params={
            "projectId": project_id, "pageSize": MAX_PAGE_SIZE * 10})
        check("知识清单 单页不超过 MAX_PAGE_SIZE",
              len(items(articles)) <= MAX_PAGE_SIZE, f"实际 {len(items(articles))} 条")

        check_exports(api, project_id, expected)
        api_files = sorted((ROOT / "backend" / "aitp" / "api").glob("*.py"))
        check_static_no_unbounded_lists(api_files)
        check_sync_export_guard()

        # 筛选后的 total 必须等于该筛选下实际返回的行数（C1 矩阵统计的口径教训）
        filtered = api.data("GET", f"{V1}/test-cases", params={
            "projectId": project_id, "keyword": "PGT-C000123", "pageSize": MAX_PAGE_SIZE})
        filtered_rows = items(filtered)
        check("筛选后 total 与实际条数口径一致",
              len(filtered_rows) >= 1 and num(filtered, "total") == len(filtered_rows),
              f"total={filtered.get('total')} 实返={len(filtered_rows)} 条")
        check("筛选后单页仍受 MAX_PAGE_SIZE 约束",
              len(filtered_rows) <= MAX_PAGE_SIZE, f"实返 {len(filtered_rows)} 条")

    print(f"\n分页门禁判据：万级用例={expected} 条，"
          f"列表/树/导出/静态四类检查已跑完", flush=True)
    print(f"PASS={_passed} FAIL={_failed}", flush=True)
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
