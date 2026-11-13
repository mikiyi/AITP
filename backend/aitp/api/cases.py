"""测试用例 API。"""

from __future__ import annotations

from typing import Any, Iterator

from flask import Blueprint, Response, request

from ..common.enums import CASE_PRIORITY, CASE_STATUS, CASE_TYPE
from ..common.validators import check_choice, optional_int, optional_str, require_int, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import NULL_FILTER, dumps, loads
from ..repositories.case_execution_repo import CaseExecutionRepository
from ..repositories.module_repo import ModuleRepository
from ..repositories.numbering import begin_claim
from ..repositories.project_repo import ProjectRepository
from ..repositories.requirement_repo import RequirementRepository
from ..repositories.test_case_repo import TestCaseRepository
from ..repositories.test_env_repo import TestEnvRepository
from ..services import assertion_schema, audit_service, execution_protocol, export_jobs, interface_runner
from ..services import nav_badge_service
from ..services import tabular_export, xlsx_exporter, xlsx_importer
from .helpers import (
    attach_version_codes,
    audit,
    batch_soft_delete,
    body,
    check_ref_project,
    commit,
    db,
    download_response,
    guard_project_scope,
    ids_of,
    keyword_of,
    page_args,
    page_payload,
    path_int,
    query_filters,
    reject_project_move,
    require_project,
    scope_arg,
)
from .modules import _to_public as _module_to_public

bp = Blueprint("test_cases", __name__)

FILTERS = {
    "projectId": "project_id",
    "requirementId": "requirement_id",
    "moduleId": "module_id",
    "caseType": "case_type",
    "priority": "priority",
    "status": "status",
    "source": "source",
    "versionId": "version_id",
}
WRITE_FIELDS = {
    "project_id", "requirement_id", "module_id", "title", "case_type", "priority",
    "status", "preconditions", "expected_result", "actual_result", "tags", "source", "creator",
    "version_id",
}


def _expand_module_scope(filters: dict[str, Any]) -> None:
    """功能点筛选的两种扩展写法。

    `moduleId=none` 只看未挂功能点的用例；`moduleId=1,2,3` 看多个节点，
    前端把「含下级」展开成 id 列表传进来，后端不猜层级。
    """
    raw = filters.get("module_id")
    if raw is None:
        return
    text = str(raw).strip()
    if text.lower() == "none":
        filters["module_id"] = NULL_FILTER
        return
    if "," in text:
        filters["module_id"] = [int(part) for part in text.split(",") if part.strip().isdigit()]


def _check_case_refs(
    conn: Any,
    project_id: Any,
    requirement_id: Any = None,
    module_id: Any = None,
    version_id: Any = None,
) -> tuple[int | None, int | None, int | None]:
    """用例上的三个引用必须落在同一个项目里，否则覆盖矩阵和版本统计会串项目。"""
    return (
        check_ref_project(conn, "biz_requirement", requirement_id, project_id, "需求", "requirementId"),
        check_ref_project(conn, "biz_module", module_id, project_id, "功能点", "moduleId"),
        check_ref_project(conn, "biz_project_version", version_id, project_id, "项目版本", "versionId"),
    )


def _validate_assertion_rules(extra: Any) -> None:
    """GAP-34：保存用例前先解析「断言规则」里的 schema 断言。

    坏 JSON Schema 落库后要等到真跑才炸，报错点离填写点太远；这里在写入侧就拦住，
    给出「哪一行、为什么」的中文提示。非 schema 的文本算子不强判（一期口径不变），
    ``lib:`` 引用也不在这里查存在性——那需要项目上下文，执行期已统一守门。
    """
    if not isinstance(extra, dict):
        return
    rules = extra.get("断言规则") or extra.get("assertionRules") or ""
    if not isinstance(rules, str) or not rules.strip():
        return
    for chunk in rules.replace("；", ";").replace("\r", "\n").split("\n"):
        for piece in chunk.split(";"):
            line = piece.strip()
            if not line.lower().startswith(("schema:", "schema ")):
                continue
            head, schema_text = assertion_schema.split_schema_rule(line)
            assertion_schema.parse_schema(schema_text, field=f"断言规则「{head}」的 JSON Schema")


def _to_public(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "requirementId": row.get("requirement_id"),
        "moduleId": row.get("module_id"),
        "versionId": row.get("version_id"),
        "caseNo": row.get("case_no"),
        "title": row.get("title"),
        "caseType": row.get("case_type"),
        "priority": row.get("priority"),
        "status": row.get("status"),
        "preconditions": row.get("preconditions"),
        "steps": row.get("steps") if "steps" in row else loads(row.get("steps_json"), []) or [],
        "expectedResult": row.get("expected_result"),
        "actualResult": row.get("actual_result"),
        "tags": row.get("tags"),
        "extra": row.get("extra") if "extra" in row else loads(row.get("extra_json"), {}) or {},
        "source": row.get("source"),
        "aiTaskId": row.get("ai_task_id"),
        "creator": row.get("creator"),
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


CASE_EXPORT_FORMATS = ("xlsx", *tabular_export.TABULAR_FORMATS)

CASE_TABULAR_COLUMNS: tuple[tuple[str, str], ...] = (
    ("caseNo", "用例编号"),
    ("title", "标题"),
    ("caseType", "用例类型"),
    ("priority", "优先级"),
    ("status", "执行状态"),
    ("modulePath", "功能点"),
    ("reqNo", "需求编号"),
    ("preconditions", "前置条件"),
    ("stepsText", "操作步骤"),
    ("expectedResult", "预期结果"),
    ("tags", "标签"),
    ("linkedBugNos", "关联缺陷"),
    ("creator", "创建人"),
    ("createdAt", "创建时间"),
)


def _flat_cases(
    rows: list[dict[str, Any]],
    module_paths: dict[int, str],
    requirement_nos: dict[int, str],
    bug_nos_by_case: dict[int, list[str]],
) -> list[dict[str, Any]]:
    """把用例摊平成一行一条，供 CSV / JSON 导出使用。"""
    flat: list[dict[str, Any]] = []
    for case in rows:
        case_id = int(case["id"])
        module_id = case.get("module_id")
        requirement_id = case.get("requirement_id")
        flat.append({
            "caseNo": case.get("case_no"),
            "title": case.get("title"),
            "caseType": case.get("case_type"),
            "priority": case.get("priority"),
            "status": case.get("status"),
            "modulePath": module_paths.get(int(module_id), "") if module_id else "",
            "reqNo": requirement_nos.get(int(requirement_id), "") if requirement_id else "",
            "preconditions": case.get("preconditions"),
            "stepsText": xlsx_exporter.steps_to_text(case.get("steps") or []),
            "expectedResult": case.get("expected_result"),
            "tags": case.get("tags"),
            "linkedBugNos": "、".join(bug_nos_by_case.get(case_id, [])),
            "creator": case.get("creator"),
            "createdAt": case.get("created_at"),
        })
    return flat


@bp.get("")
def list_cases() -> Response:
    conn = db()
    page_num, page_size = page_args()
    repo = TestCaseRepository(conn)
    filters = query_filters(FILTERS)
    _expand_module_scope(filters)
    page = repo.list_detail(filters, keyword_of() or None, page_num=page_num, page_size=page_size)
    payload = page_payload(page)
    payload["list"] = attach_version_codes(conn, [_to_public(row) for row in payload["list"]])
    return ok(payload)


@bp.get("/tree")
def case_tree() -> Response:
    """用例树：功能点树上挂各自的用例，没挂功能点的用例单独列出。

    节点数字走 SQL 汇总（一次 GROUP BY），用例正文按主键游标分批返回：
    万级用例下不再把整表读进内存数数，前端翻游标就能取全。
    """
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    version_id = scope_arg(field="versionId")
    module_repo = ModuleRepository(conn)
    tree = module_repo.tree(project_id)

    case_filters: dict[str, Any] = {"project_id": project_id}
    if version_id:
        case_filters["version_id"] = version_id
    repo = TestCaseRepository(conn)
    counts = repo.tree_counts(case_filters)
    by_module: dict[int, list[dict[str, Any]]] = {}
    orphans: list[dict[str, Any]] = []

    page_size = _tree_page_size()
    after_id = max(0, require_int(request.args.get("afterId"), "afterId", 0))
    rows = repo.page_after_id(case_filters, after_id=after_id, batch=page_size)
    for row in rows:
        item = _to_public(row)
        module_id = row.get("module_id")
        if module_id:
            by_module.setdefault(int(module_id), []).append(item)
        else:
            orphans.append(item)
    next_cursor = int(rows[-1]["id"]) if len(rows) >= page_size and rows else 0

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            node_id = int(node["id"])
            stat = counts["byModule"].get(node_id) or {}
            node["cases"] = by_module.get(node_id, [])
            node["caseCount"] = int(stat.get("total") or 0)
            node["passCount"] = int(stat.get("pass") or 0)
            node["failCount"] = int(stat.get("fail") or 0)
            walk(node.get("children") or [])

    walk(tree)
    orphan_stat = counts["orphans"]
    return ok({
        "list": [_case_node_public(node) for node in tree],
        "orphans": orphans,
        "total": counts["total"],
        "orphanCount": int(orphan_stat.get("total") or 0),
        "pageSize": page_size,
        "nextCursor": next_cursor,
        "returned": len(rows),
    })


def _case_node_public(node: dict[str, Any]) -> dict[str, Any]:
    """模块树节点 camelCase 出参 + 用例挂载字段，任意深度同一契约。"""
    pub = _module_to_public(node)
    pub["cases"] = node.get("cases", [])
    pub["caseCount"] = int(node.get("caseCount") or 0)
    pub["passCount"] = int(node.get("passCount") or 0)
    pub["failCount"] = int(node.get("failCount") or 0)
    pub["children"] = [_case_node_public(child) for child in node.get("children") or []]
    return pub


def _tree_page_size() -> int:
    """用例树每批回传的用例条数，默认 200、上限 1000，避免一次拖全表。"""
    raw = request.args.get("pageSize")
    if raw in (None, ""):
        return TREE_DEFAULT_PAGE_SIZE
    value = require_int(raw, "pageSize")
    if value <= 0:
        raise AppError(ErrorCode.PARAM_ERROR, "pageSize 必须大于 0")
    return min(value, TREE_MAX_PAGE_SIZE)


#: 用例树单批用例数：默认值覆盖中小项目一次看完，上限挡住万级全表回传
TREE_DEFAULT_PAGE_SIZE = 200
TREE_MAX_PAGE_SIZE = 1000


@bp.get("/coverage")
def coverage_matrix() -> Response:
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    return ok(TestCaseRepository(conn).coverage_matrix(project_id, scope_arg(field="versionId")))


@bp.get("/coverage/modules")
def module_coverage_matrix() -> Response:
    """按功能点统计的覆盖矩阵。"""
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    return ok(TestCaseRepository(conn).module_coverage_matrix(
        project_id, scope_arg(field="versionId")
    ))


#: 用例导出接受的筛选口径（与列表接口一致，不含 source）
CASE_EXPORT_FILTERS: dict[str, str] = {
    "projectId": "project_id",
    "caseType": "case_type",
    "status": "status",
    "priority": "priority",
    "moduleId": "module_id",
    "requirementId": "requirement_id",
    "versionId": "version_id",
}


def _case_export_filters(conn: Any) -> dict[str, Any]:
    """按白名单把导出查询串换成仓储筛选，并展开功能点子树。"""
    filters = query_filters(CASE_EXPORT_FILTERS)
    if "project_id" not in filters:
        raise AppError(ErrorCode.PARAM_ERROR, "projectId 必填：导出不允许全库口径")
    require_int(filters["project_id"], "projectId")
    _expand_module_scope(filters)
    return filters


class CaseExportSource:
    """用例导出的分批数据源。

    一次只从库里取 `SCAN_PAGE_SIZE` 条，顺带补齐功能点路径、需求编号与关联缺陷；
    CSV / JSON 每批摊平后即可写出，xlsx 要按用例类型分组所以把批次的摊平结果留着。
    这样万级用例的导出不再有一次性 `list_all(limit=10000)` 的全表载入。
    """

    def __init__(self, conn: Any, project_id: int) -> None:
        self.conn = conn
        self.project_id = int(project_id)
        self.repo = TestCaseRepository(conn)
        self.module_repo = ModuleRepository(conn)
        self.requirements = RequirementRepository(conn).list_by_project(project_id)
        self.requirement_nos = {
            int(item["id"]): str(item.get("req_no") or "") for item in self.requirements
        }
        self.module_paths: dict[int, str] = {}
        self.bug_nos_by_case: dict[int, list[str]] = {}
        #: 已扫描出的用例行数，供导出计数用，避免为了数数再扫一遍
        self.row_count = 0

    def case_batches(self, filters: dict[str, Any], keyword: str) -> Iterator[list[dict[str, Any]]]:
        """按批产出解析好 steps / extra 的用例行。"""
        for chunk in self.repo.iter_rows(filters, keyword or None):
            rows: list[dict[str, Any]] = []
            for raw in chunk:
                case = dict(raw)
                case["steps"] = loads(case.get("steps_json"), []) or []
                case["extra"] = loads(case.get("extra_json"), {}) or {}
                rows.append(case)
            self._attach_modules(rows)
            self._attach_bugs(rows)
            self.row_count += len(rows)
            yield rows

    def flat_batches(
        self, filters: dict[str, Any], keyword: str
    ) -> Iterator[list[dict[str, Any]]]:
        """CSV / JSON 用的一行一条摊平批次。"""
        for batch in self.case_batches(filters, keyword):
            yield _flat_cases(
                batch, self.module_paths, self.requirement_nos, self.bug_nos_by_case
            )

    def count(self, filters: dict[str, Any], keyword: str) -> int:
        return self.repo.count(filters, keyword or None)

    def workbook(self, filters: dict[str, Any], keyword: str) -> bytes:
        cases_by_type: dict[str, list[dict[str, Any]]] = {case_type: [] for case_type in CASE_TYPE}
        for batch in self.case_batches(filters, keyword):
            for case in batch:
                case_type = str(case.get("case_type") or "functional")
                if case_type not in cases_by_type:
                    case_type = "functional"
                cases_by_type[case_type].append(case)
        return xlsx_exporter.export_workbook(
            cases_by_type,
            module_paths=self.module_paths,
            requirement_nos=self.requirement_nos,
            bug_nos_by_case=self.bug_nos_by_case,
            requirements=self.requirements,
        )

    def _attach_modules(self, rows: list[dict[str, Any]]) -> None:
        for case in rows:
            module_id = case.get("module_id")
            if module_id and int(module_id) not in self.module_paths:
                self.module_paths[int(module_id)] = self.module_repo.path_text(
                    int(module_id), project_name=""
                )

    def _attach_bugs(self, rows: list[dict[str, Any]]) -> None:
        """关联缺陷按批查：一批最多几百个占位符，不会万级 id 一次拼进 IN。"""
        linked = self.repo.linked_bug_ids([int(case["id"]) for case in rows])
        for case_id, items in linked.items():
            self.bug_nos_by_case[case_id] = [
                str(item.get("bug_no") or "") for item in items
            ]


def _case_export_name(conn: Any, filters: dict[str, Any], fmt: str) -> str:
    project = ProjectRepository(conn).get(int(filters.get("project_id") or 0))
    project_name = str((project or {}).get("name") or "")
    return f"{project_name or 'AITP'}测试用例"


@bp.get("/export")
def export_cases() -> Response:
    """导出用例：xlsx 走 7 张表（格式对齐参考实现 generate_test_cases.py），csv/json 走单表摊平。

    数据按批扫描，不在请求里一次性载入全量；万级用例要导给后台任务跑，
    见 `POST /test-cases/export/async`。
    """
    conn = db()
    fmt = str(request.args.get("format") or "xlsx").strip().lower()
    if fmt not in CASE_EXPORT_FORMATS:
        raise AppError(ErrorCode.PARAM_ERROR, "format 只能是 xlsx / csv / json")
    filters = _case_export_filters(conn)
    project_id = int(filters["project_id"])
    keyword = keyword_of()
    source = CaseExportSource(conn, project_id)
    project_name = _case_export_name(conn, filters, fmt)
    total = source.count(filters, keyword)
    export_jobs.guard_sync_rows(total, "/api/v1/test-cases/export/async")

    if fmt in tabular_export.TABULAR_FORMATS:
        flat_rows = [row for batch in source.flat_batches(filters, keyword) for row in batch]
        content, mimetype, extension = tabular_export.render(
            fmt, CASE_TABULAR_COLUMNS, flat_rows,
        )
        audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_TEST_CASE, project_id,
              project_name, {"format": fmt, "count": len(flat_rows)})
        return download_response(
            content,
            tabular_export.safe_filename(project_name, extension),
            mimetype,
            count=len(flat_rows),
        )

    content = source.workbook(filters, keyword)
    audit(conn, audit_service.AUDIT_ACTION_EXPORT, audit_service.RESOURCE_TEST_CASE, project_id,
          project_name, {"format": fmt, "count": source.row_count})
    filename = xlsx_exporter.build_export_filename(project_name)
    return Response(
        content,
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={
            "Content-Disposition": f"attachment; filename*=UTF-8''{_url_quote(filename)}",
            "X-AITP-Export-Count": str(source.row_count),
        },
    )


def _url_quote(value: str) -> str:
    from urllib.parse import quote

    return quote(value, safe="")


@bp.post("/import")
def import_cases() -> Response:
    """导入 xlsx：按表头名取值，覆盖矩阵表忽略。"""
    conn = db()
    uploaded = request.files.get("file")
    if uploaded is None or not uploaded.filename:
        raise AppError(ErrorCode.PARAM_ERROR, "请选择要导入的 xlsx 文件")
    if not uploaded.filename.lower().endswith(".xlsx"):
        raise AppError(ErrorCode.PARAM_ERROR, "只支持 .xlsx 格式")

    project_id = request.form.get("projectId") or request.args.get("projectId")
    if not project_id:
        raise AppError(ErrorCode.PARAM_ERROR, "缺少 projectId")
    project_id_int = require_int(project_id, "projectId")

    raw = uploaded.read()
    try:
        parsed = xlsx_importer.parse_workbook(raw)
    except AppError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解析失败统一转参数错误
        raise AppError(ErrorCode.IMPORT_PARSE_FAILED, f"xlsx 解析失败：{exc}") from None
    if not parsed:
        raise AppError(ErrorCode.IMPORT_PARSE_FAILED, "文件里没有识别到用例表")

    repo = TestCaseRepository(conn)
    module_repo = ModuleRepository(conn)
    requirement_repo = RequirementRepository(conn)

    modules = module_repo.list_all(project_id_int)
    module_by_text: dict[str, int] = {}
    for node in modules:
        name = str(node.get("name") or "")
        path = module_repo.path_text(int(node["id"]), project_name="")
        if name:
            module_by_text.setdefault(name, int(node["id"]))
        if path:
            module_by_text.setdefault(path, int(node["id"]))
            module_by_text.setdefault(path.split("/")[-1], int(node["id"]))

    requirements = requirement_repo.list_by_project(project_id_int)
    requirement_by_no = {str(item.get("req_no") or ""): int(item["id"]) for item in requirements}
    requirement_by_title = {str(item.get("title") or ""): int(item["id"]) for item in requirements}

    # 先开写事务再采集已用编号：否则两个并发导入读到同一份「已用号」，
    # 各自插自己的号，最后一起撞 uk_biz_test_case_no（GAP-26 编号竞态）
    begin_claim(conn)
    used_numbers = {
        text
        for text in repo.scan_column("case_no", {"project_id": project_id_int})
    }

    created = 0
    skipped = 0
    renumbered = 0
    for item in parsed:
        title = str(item.get("title") or "").strip()
        if not title:
            skipped += 1
            continue

        case_type = str(item.get("caseType") or "functional")
        if case_type not in CASE_TYPE:
            case_type = "functional"

        module_text = str(item.get("moduleText") or "").strip()
        module_id = module_by_text.get(module_text)
        requirement_text = str(item.get("requirementNo") or "").strip()
        requirement_id = requirement_by_no.get(requirement_text) or requirement_by_title.get(requirement_text)

        extra = dict(item.get("extra") or {})
        if module_id is None and module_text:
            extra["所属模块/功能点"] = module_text
        if requirement_id is None and requirement_text:
            extra["关联需求"] = requirement_text

        case_no = str(item.get("caseNo") or "").strip()
        if not case_no or case_no in used_numbers:
            # 编号冲突不丢数据，改成自动编号，但要告知用户改了哪些
            case_no = repo.next_case_no(project_id_int, case_type)
            renumbered += 1
        used_numbers.add(case_no)

        repo.insert({
            "project_id": project_id_int,
            "requirement_id": requirement_id,
            "module_id": module_id,
            "case_no": case_no,
            "title": title,
            "case_type": case_type,
            "priority": str(item.get("priority") or "P2"),
            "status": str(item.get("status") or "not_executed"),
            "preconditions": str(item.get("preconditions") or ""),
            "steps_json": dumps(item.get("steps") or []),
            "expected_result": str(item.get("expectedResult") or ""),
            "actual_result": str(item.get("actualResult") or ""),
            "tags": "",
            "extra_json": dumps(extra),
            "source": "imported",
            "creator": str(item.get("creator") or ""),
        })
        created += 1

    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE, None,
          uploaded.filename, {
              "created": created,
              "skipped": skipped,
              "renumbered": renumbered,
              "projectId": project_id_int,
          })
    return ok({
        "created": created,
        "skipped": skipped,
        "renumbered": renumbered,
        "total": len(parsed),
    })


@bp.get("/<pk>")
def get_case(pk: str) -> Response:
    conn = db()
    row = TestCaseRepository(conn).get_detail(path_int(pk))
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    guard_project_scope(row, scope_arg(), "测试用例")
    return ok(_to_public(row))


@bp.post("")
def create_case() -> Response:
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    title = require_str(data.get("title"), "title", 500)
    require_project(conn, project_id)
    requirement_id, module_id, version_id = _check_case_refs(
        conn, project_id, data.get("requirementId"), data.get("moduleId"), data.get("versionId")
    )
    _validate_assertion_rules(data.get("extra"))
    case_type = check_choice(data.get("caseType"), CASE_TYPE, "caseType", "functional") or "functional"
    priority = check_choice(data.get("priority"), CASE_PRIORITY, "priority", "P2") or "P2"
    status = check_choice(data.get("status"), CASE_STATUS, "status", "not_executed") or "not_executed"
    repo = TestCaseRepository(conn)
    steps = data.get("steps")
    new_id = repo.insert({
        "project_id": project_id,
        "requirement_id": requirement_id,
        "module_id": module_id,
        "case_no": repo.next_case_no(project_id, case_type),
        "version_id": version_id,
        "title": title,
        "case_type": case_type,
        "priority": priority,
        "status": status,
        "preconditions": optional_str(data.get("preconditions"), "preconditions", 5000),
        "steps_json": dumps(steps if isinstance(steps, list) else []),
        "expected_result": optional_str(data.get("expectedResult"), "expectedResult", 5000),
        "actual_result": optional_str(data.get("actualResult"), "actualResult", 5000),
        "tags": optional_str(data.get("tags"), "tags", 500),
        "extra_json": dumps(data.get("extra") or {}),
        "source": optional_str(data.get("source"), "source", 40) or "manual",
        "creator": optional_str(data.get("creator"), "creator", 64),
    })
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE, new_id, title,
          {"caseType": case_type})
    return ok(_to_public(TestCaseRepository(conn).get_detail(new_id) or {}))


@bp.put("/<pk>")
def update_case(pk: str) -> Response:
    conn = db()
    repo = TestCaseRepository(conn)
    target = path_int(pk)
    row = repo.get_detail(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    data = body()
    payload = {k: v for k, v in data.items() if k in WRITE_FIELDS}
    reject_project_move(data, row.get("project_id"), "用例")
    payload.pop("project_id", None)
    requirement_id, module_id, version_id = _check_case_refs(
        conn, row.get("project_id"), data.get("requirementId"), data.get("moduleId"),
        data.get("versionId"),
    )
    if "requirementId" in data:
        payload["requirement_id"] = requirement_id
    if "moduleId" in data:
        payload["module_id"] = module_id
    if "versionId" in data:
        payload["version_id"] = version_id
    if "caseType" in data:
        payload["case_type"] = data["caseType"]
    if "expectedResult" in data:
        payload["expected_result"] = data["expectedResult"]
    if "actualResult" in data:
        payload["actual_result"] = data["actualResult"]
    if "case_type" in payload:
        payload["case_type"] = check_choice(payload["case_type"], CASE_TYPE, "case_type", row["case_type"])
    if "priority" in payload:
        payload["priority"] = check_choice(payload["priority"], CASE_PRIORITY, "priority", row["priority"])
    if "status" in payload:
        payload["status"] = check_choice(payload["status"], CASE_STATUS, "status", row["status"])
    if "steps" in data:
        payload["steps_json"] = dumps(data.get("steps") or [])
    if "extra" in data:
        _validate_assertion_rules(data.get("extra"))
        payload["extra_json"] = dumps(data.get("extra") or {})
    repo.update(target, payload)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_CASE, target,
          str(payload.get("title", row.get("title", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(_to_public(repo.get_detail(target) or {}))


@bp.delete("/<pk>")
def delete_case(pk: str) -> Response:
    conn = db()
    repo = TestCaseRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_CASE, target,
          row.get("title", ""))
    return ok({"id": target, "deleted": True})


@bp.post("/batch-delete")
def batch_delete_cases() -> Response:
    """批量删除用例；已删除或不存在的 id 归入 skipped。"""
    conn = db()
    ids = ids_of(body())
    return ok(batch_soft_delete(conn, TestCaseRepository(conn), ids, audit_service.RESOURCE_TEST_CASE))


@bp.post("/adopt")
def adopt_cases() -> Response:
    """采纳 AI 生成的用例：批量转成正式用例。"""
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    require_project(conn, project_id)
    default_requirement, default_module, version_id = _check_case_refs(
        conn, project_id, data.get("requirementId"), data.get("moduleId"), data.get("versionId")
    )
    items = data.get("cases")
    if not isinstance(items, list) or not items:
        raise AppError(ErrorCode.PARAM_ERROR, "cases 不能为空")

    repo = TestCaseRepository(conn)
    created: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        case_type = str(item.get("caseType") or "functional")
        if case_type not in CASE_TYPE:
            case_type = "functional"
        priority = str(item.get("priority") or "P2")
        if priority not in CASE_PRIORITY:
            priority = "P2"
        steps = item.get("steps")
        if not isinstance(steps, list):
            steps = []
        case_no = repo.next_case_no(project_id, case_type)
        item_requirement, item_module, _ = _check_case_refs(
            conn, project_id, item.get("requirementId"), item.get("moduleId")
        )
        new_id = repo.insert({
            "project_id": project_id,
            "requirement_id": item_requirement or default_requirement,
            "module_id": item_module or default_module,
            "version_id": version_id,
            "case_no": case_no,
            "title": title,
            "case_type": case_type,
            "priority": priority,
            "status": "not_executed",
            "preconditions": str(item.get("preconditions") or ""),
            "steps_json": dumps(steps),
            "expected_result": str(item.get("expectedResult") or ""),
            "actual_result": "",
            "tags": str(item.get("tags") or ""),
            "extra_json": dumps(item.get("extra") or {}),
            "source": "ai_generated",
            "creator": str(item.get("creator") or "ai"),
        })
        created.append({"id": new_id, "caseNo": case_no})

    nav_badge_service.increase_for(conn, "cases", len(created))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE, None,
          f"采纳 AI 用例 {len(created)} 条", {"count": len(created), "projectId": project_id})
    return ok({"created": created, "count": len(created)})


@bp.post("/<pk>/execute")
def execute_case(pk: str) -> Response:
    conn = db()
    repo = TestCaseRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    data = body()
    result = check_choice(data.get("result"), CASE_STATUS, "result", "not_executed") or "not_executed"
    exec_id = CaseExecutionRepository(conn).record(
        target,
        result,
        duration_ms=data.get("durationMs"),
        environment=optional_str(data.get("environment"), "environment", 100),
        version=optional_str(data.get("version"), "version", 100),
        executed_by=optional_str(data.get("executedBy"), "executedBy", 64),
        remark=optional_str(data.get("remark"), "remark", 2000),
    )
    repo.update(target, {"status": result})
    evidence_id = execution_protocol.record_manual_evidence(
        conn,
        execution_id=exec_id,
        case_id=target,
        project_id=optional_int(row.get("project_id"), "projectId"),
        title=str(row.get("title") or ""),
        result=result,
        duration_ms=data.get("durationMs"),
        environment=str(data.get("environment") or ""),
        executed_by=str(data.get("executedBy") or ""),
        remark=str(data.get("remark") or ""),
        attachment_ids=list(data.get("attachmentIds") or [])
        if isinstance(data.get("attachmentIds"), list) else None,
        request_summary=str(data.get("requestSummary") or ""),
        response_summary=str(data.get("responseSummary") or ""),
        log_summary=str(data.get("logSummary") or ""),
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_CASE, target,
          row.get("title", ""), {"result": result})
    return ok({"id": target, "status": result, "executionId": exec_id,
               "evidenceId": evidence_id})


@bp.post("/<pk>/interface-run")
def run_interface_case(pk: str) -> Response:
    """按接口用例的扩展字段发一次真实 HTTP 请求，并回写执行结果。"""
    conn = db()
    target = path_int(pk)
    row = TestCaseRepository(conn).get(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    data = body()
    env_id = optional_int(data.get("envId"), "envId")
    if env_id is None:
        default_env = TestEnvRepository(conn).get_default()
        env_id = int(default_env["id"]) if default_env else None
    payload = interface_runner.run_case(
        conn,
        target,
        env_id,
        timeout=optional_int(data.get("timeoutSeconds"), "timeoutSeconds")
        or interface_runner.DEFAULT_TIMEOUT,
        executed_by=optional_str(data.get("executedBy"), "executedBy", 64) or "local",
    )
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_TEST_CASE, target,
          str(row.get("title") or ""),
          {"envId": payload.get("envId"), "statusCode": payload.get("statusCode"),
           "result": payload.get("result")})
    return ok(payload)


def _case_export_filename(conn: Any, filters: dict[str, Any], fmt: str) -> str:
    """导出文件名：xlsx 沿用自己那套带时间戳的名字，csv / json 走通用安全文件名。"""
    stem = _case_export_name(conn, filters, fmt)
    if fmt == "xlsx":
        return xlsx_exporter.build_export_filename(stem)
    return tabular_export.safe_filename(stem, fmt)


def _case_export_pages(conn: Any, filters: dict[str, Any], keyword: str) -> Iterator[list[dict[str, Any]]]:
    source = CaseExportSource(conn, int(filters.get("project_id") or 0))
    return source.flat_batches(filters, keyword)


def _case_export_workbook(conn: Any, filters: dict[str, Any], keyword: str) -> bytes:
    source = CaseExportSource(conn, int(filters.get("project_id") or 0))
    return source.workbook(filters, keyword)


export_jobs.register(export_jobs.ExportSpec(
    resource="test_case",
    label="测试用例",
    formats=CASE_EXPORT_FORMATS,
    filters=CASE_EXPORT_FILTERS,
    columns=CASE_TABULAR_COLUMNS,
    pages=_case_export_pages,
    count=lambda conn, filters, keyword: TestCaseRepository(conn).count(filters, keyword or None),
    workbook=_case_export_workbook,
    refine=_expand_module_scope,
    name_of=_case_export_name,
    full_name_of=_case_export_filename,
    audit_resource=audit_service.RESOURCE_TEST_CASE,
    project_required=True,
))
export_jobs.install_routes(bp, "test_case")


# --------------------------------------------------------------------- 断言库（GAP-34）


def _rule_public(row: dict[str, Any]) -> dict[str, Any]:
    """断言库条目的统一出参：``reference`` 就是用例里能直接写的引用串。"""
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "ruleNo": row.get("rule_no"),
        "name": row.get("name"),
        "rule": row.get("rule"),
        "description": row.get("description") or "",
        "enabled": bool(int(row.get("enabled") or 0)),
        "reference": f"lib:{row.get('rule_no')}",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


@bp.get("/assertion-rules")
def list_assertion_rules() -> Response:
    """断言库清单：项目级（未挂功能点）+ 命中功能点及其祖先的条目都可见。

    可见范围要先算祖先链再按编号排序，所以分页在取完可见集之后做，
    返回口径与全站列表接口一致（``list/total/pageNum/pageSize/pages``），前端表格组件直接复用。
    """
    conn = db()
    project_id = require_int(request.args.get("projectId"), "projectId")
    module_id = optional_int(request.args.get("moduleId"), "moduleId")
    if module_id is not None:
        check_ref_project(conn, "biz_module", module_id, project_id, "功能点", "moduleId")
    repo = assertion_schema.AssertionRuleRepository(conn)
    rows = repo.list_visible(project_id, assertion_schema.module_scope_ids(conn, module_id))
    keyword = (keyword_of() or "").strip()
    if keyword:
        lowered = keyword.lower()
        rows = [
            row for row in rows
            if any(lowered in str(row.get(field) or "").lower()
                   for field in ("rule_no", "name", "rule", "description"))
        ]
    page_num, page_size = page_args()
    total = len(rows)
    start = (page_num - 1) * page_size
    window = rows[start:start + page_size]
    return ok({
        "list": [_rule_public(row) for row in window],
        "total": total,
        "pageNum": page_num,
        "pageSize": page_size,
        "pages": (total + page_size - 1) // page_size if total else 0,
        "count": total,
    })


@bp.post("/assertion-rules")
def create_assertion_rule() -> Response:
    """新增断言库条目；规则语法与用例「断言规则」完全一致，落库前先解析一遍。"""
    conn = db()
    data = body()
    project_id = require_int(data.get("projectId"), "projectId")
    require_project(conn, project_id)
    module_id = check_ref_project(
        conn, "biz_module", data.get("moduleId"), project_id, "功能点", "moduleId"
    )
    name = require_str(data.get("name"), "name", 200)
    rule = assertion_schema.validate_rule_text(data.get("rule"))
    repo = assertion_schema.AssertionRuleRepository(conn)
    new_id = repo.insert({
        "project_id": project_id,
        "module_id": module_id,
        "rule_no": assertion_schema.next_rule_no(conn, project_id),
        "name": name,
        "rule": rule,
        "description": optional_str(data.get("description"), "description", 500) or "",
        "enabled": 0 if data.get("enabled") is False else 1,
    })
    commit(conn)
    created = repo.get(new_id) or {}
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_CASE,
          new_id, name, {"ruleNo": created.get("rule_no")})
    return ok(_rule_public(created))


@bp.put("/assertion-rules/<pk>")
def update_assertion_rule(pk: str) -> Response:
    conn = db()
    repo = assertion_schema.AssertionRuleRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "断言规则不存在或已删除")
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 200)
    if "rule" in data:
        payload["rule"] = assertion_schema.validate_rule_text(data["rule"])
    if "description" in data:
        payload["description"] = optional_str(data["description"], "description", 500) or ""
    if "enabled" in data:
        payload["enabled"] = 1 if data["enabled"] else 0
    if "moduleId" in data:
        payload["module_id"] = check_ref_project(
            conn, "biz_module", data["moduleId"], row.get("project_id"), "功能点", "moduleId"
        )
    if payload:
        repo.update(target, payload)
        commit(conn)
        audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_CASE,
              target, str(payload.get("name") or row.get("name") or ""),
              {"fields": sorted(payload.keys())})
    return ok(_rule_public(repo.get(target) or {}))


@bp.delete("/assertion-rules/<pk>")
def delete_assertion_rule(pk: str) -> Response:
    conn = db()
    repo = assertion_schema.AssertionRuleRepository(conn)
    target = path_int(pk)
    row = repo.get(target)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "断言规则不存在或已删除")
    repo.soft_delete(target)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_CASE,
          target, str(row.get("name") or ""))
    return ok({"id": target, "deleted": True})


@bp.get("/<pk>/evidence")
def list_case_evidence(pk: str) -> Response:
    """用例维度的证据下钻：最近一份 + 本用例全部快照（GAP-32 验收标准 3）。"""
    conn = db()
    target = path_int(pk)
    if TestCaseRepository(conn).get(target) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试用例不存在")
    latest = execution_protocol.latest_evidence_for_case(conn, target)
    rows = conn.execute(
        "SELECT id FROM biz_execution_evidence WHERE case_id = ? AND is_deleted = 0"
        " AND (expires_at = '' OR expires_at > ?) ORDER BY id DESC LIMIT 50",
        (target, execution_protocol._now_text()),
    ).fetchall()
    items = [execution_protocol.get_evidence(conn, int(row["id"])) for row in rows]
    return ok({
        "latest": latest,
        "list": [item for item in items if item],
        "counts": execution_protocol.evidence_counts(conn, None),
    })
