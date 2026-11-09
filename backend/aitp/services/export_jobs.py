"""导出后台任务：把「一次读完整张表」换成「按批扫描 + 落盘 + 完成后下载」。

两件事同时成立：
  1. 原有 `GET .../export` 直连下载保留（门禁与收藏的链接都在读它），但数据侧改成
     按批扫描（单批不超过 `SCAN_PAGE_SIZE` 行），不再 `list_all(limit=10000)`；
  2. 新增 `POST .../export/async` 提交 `sys_task` 队列任务，worker 里分批把 CSV / JSON
     直接写盘（内存只留一批），完成后从 `GET .../export/files/<taskId>` 取文件。
     xlsx 这类必须整体成型的格式仍走既有导出器，本批只改数据加载方式，不动格式能力。

任务体系完全复用 job_queue：排队、并发上限、进度、取消、失败落库、任务中心可见。
路由由 install_routes() 挂在各资源蓝图上，路径与同步导出保持同一前缀。
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Sequence

from flask import Blueprint, Response, current_app, request, send_file

from ..common.time_utils import utcnow_iso
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.base import NULL_FILTER, SCAN_MAX_ROWS, SCAN_PAGE_SIZE
from . import audit_service, job_queue, tabular_export, task_control

#: 队列任务类型；任务中心按这个键显示与筛选
EXPORT_KIND = "data.export"
#: 导出产物目录名（落在数据库文件同级目录下，随备份一起走）
ARTIFACT_DIR_NAME = "exports"
#: 产物保留时长（小时）：提交新任务时顺手清理过期文件，避免本机越攒越多
ARTIFACT_TTL_HOURS = 24
#: 产物最多保留个数
ARTIFACT_KEEP = 60

XLSX_MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
MIME_TYPES = {
    "csv": "text/csv",
    "json": "application/json",
    "xlsx": XLSX_MIME,
}

PageFactory = Callable[[sqlite3.Connection, dict[str, Any], str], Iterator[Sequence[dict[str, Any]]]]
CountFactory = Callable[[sqlite3.Connection, dict[str, Any], str], int]
WorkbookFactory = Callable[[sqlite3.Connection, dict[str, Any], str], bytes]
NameFactory = Callable[[sqlite3.Connection, dict[str, Any], str], str]
FullNameFactory = Callable[[sqlite3.Connection, dict[str, Any], str], str]

_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9_.-]+")


@dataclass(slots=True)
class ExportSpec:
    """一种可导出的资源：谁能筛、怎么分批取、写出来叫什么名字。"""

    resource: str
    label: str
    formats: tuple[str, ...]
    filters: dict[str, str]
    #: 公开查询参数名 → 数据库列名，与列表接口用的是同一张白名单
    pages: PageFactory
    count: CountFactory
    columns: tuple[tuple[str, str], ...] = ()
    workbook: WorkbookFactory | None = None
    #: 功能点这类需要把 `moduleId=1,2,3` 展开成列表的筛选，由资源自己补一刀
    refine: Callable[[dict[str, Any]], None] | None = None
    #: 文件名主体（不含扩展名）由资源按筛选条件决定，例如带项目名
    name_of: NameFactory = field(default=lambda conn, filters, fmt: "AITP导出")
    #: xlsx 这类自带命名规则的格式直接给全名，缺省时用 name_of + 扩展名
    full_name_of: FullNameFactory | None = None
    audit_resource: str = ""
    project_required: bool = False

    @property
    def default_format(self) -> str:
        return "csv" if "csv" in self.formats else self.formats[0]

    def normalize_format(self, raw: Any) -> str:
        fmt = str(raw or self.default_format).strip().lower()
        if fmt not in self.formats:
            raise AppError(
                ErrorCode.PARAM_ERROR,
                f"format 只能是 {' / '.join(self.formats)}",
            )
        return fmt

    def resolve_filters(self, query: dict[str, Any]) -> dict[str, Any]:
        filters: dict[str, Any] = {}
        for key, column in self.filters.items():
            value = query.get(key)
            if value in (None, ""):
                continue
            if isinstance(value, str) and value.strip().lower() == "none":
                filters[column] = NULL_FILTER
                continue
            filters[column] = value
        if self.refine is not None:
            self.refine(filters)
        if self.project_required and "project_id" not in filters:
            raise AppError(ErrorCode.PARAM_ERROR, "projectId 必填：导出不允许全库口径")
        return filters

    def filename(self, conn: sqlite3.Connection, filters: dict[str, Any], fmt: str) -> str:
        if self.full_name_of is not None:
            return self.full_name_of(conn, filters, fmt)
        return tabular_export.safe_filename(self.name_of(conn, filters, fmt), fmt)


_SPECS: dict[str, ExportSpec] = {}


def register(spec: ExportSpec) -> ExportSpec:
    _SPECS[spec.resource] = spec
    return spec


def spec_of(resource: str) -> ExportSpec:
    spec = _SPECS.get(str(resource or ""))
    if spec is None:
        raise AppError(ErrorCode.PARAM_ERROR, f"未注册的导出类型：{resource}")
    return spec


def registered_resources() -> tuple[str, ...]:
    return tuple(_SPECS)


def guard_sync_rows(total: int, submit_path: str) -> None:
    """直连导出的行数闸门：超过扫描上限就让用户改走后台任务，别把请求挂死。"""
    if total > SCAN_MAX_ROWS:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"当前筛选有 {total} 行，直连导出上限 {SCAN_MAX_ROWS} 行，"
            f"请改用后台导出（POST {submit_path}）",
        )


def row_batches(
    repo_factory: Callable[[sqlite3.Connection], Any],
    adapter: Callable[[dict[str, Any]], dict[str, Any]],
) -> PageFactory:
    """通用「按批扫描 + 逐行适配公开字段」的导出批次工厂，供各资源注册时复用。"""

    def _pages(
        conn: sqlite3.Connection, filters: dict[str, Any], keyword: str
    ) -> Iterator[list[dict[str, Any]]]:
        repo = repo_factory(conn)
        for chunk in repo.iter_rows(filters, keyword or None):
            yield [adapter(row) for row in chunk]

    return _pages


def rows_total(
    repo_factory: Callable[[sqlite3.Connection], Any],
) -> CountFactory:
    """通用导出计数工厂：先数一遍总数，用于 JSON 头部与直连行数闸门。"""

    def _count(conn: sqlite3.Connection, filters: dict[str, Any], keyword: str) -> int:
        return int(repo_factory(conn).count(filters, keyword or None))

    return _count


def scan_rows(
    conn: sqlite3.Connection,
    repo_factory: Callable[[sqlite3.Connection], Any],
    adapter: Callable[[dict[str, Any]], dict[str, Any]],
    filters: dict[str, Any],
    keyword: str,
) -> list[dict[str, Any]]:
    """直连导出用：按批扫描后拼成列表（每批仍受 SCAN_PAGE_SIZE 约束）。"""
    rows: list[dict[str, Any]] = []
    for batch in row_batches(repo_factory, adapter)(conn, filters, keyword):
        rows.extend(batch)
    return rows


# ---------------- 产物落盘 ----------------

def artifact_root() -> Path:
    """导出产物目录：与数据库同级，随 AITP_DB_PATH 一起搬到临时目录。"""
    db_path = Path(str(current_app.config["AITP_DB_PATH"]))
    return db_path.parent / ARTIFACT_DIR_NAME


def artifact_path(name: str) -> Path | None:
    """只接受安全的文件名，避免任务结果里的路径被拼成目录穿越。"""
    safe = _UNSAFE_NAME.sub("-", str(name or ""))
    if not safe or safe != str(name):
        return None
    path = (artifact_root() / safe).resolve()
    root = artifact_root().resolve()
    if root not in path.parents or not path.is_file():
        return None
    return path


def prune_artifacts(root: Path) -> int:
    """清理过期/超额产物，返回删除数量；失败不阻断导出本身。"""
    try:
        files = sorted(root.glob("export-*"), key=lambda item: item.stat().st_mtime, reverse=True)
    except OSError:
        return 0
    deadline = time.time() - ARTIFACT_TTL_HOURS * 3600
    removed = 0
    for index, path in enumerate(files):
        if index < ARTIFACT_KEEP and path.stat().st_mtime > deadline:
            continue
        try:
            path.unlink()
            removed += 1
        except OSError:
            continue
    return removed


# ---------------- 任务提交与执行 ----------------

def submit(
    conn: sqlite3.Connection,
    *,
    resource: str,
    fmt: str,
    query: dict[str, Any],
    keyword: str = "",
) -> dict[str, Any]:
    """校验筛选与格式后立即入队，返回任务快照（不等导出跑完）。"""
    spec = spec_of(resource)
    normalized = spec.normalize_format(fmt)
    filters = spec.resolve_filters(query)
    task = job_queue.submit(
        conn,
        kind=EXPORT_KIND,
        title=f"导出{spec.label}（{normalized}）",
        payload={
            "resource": spec.resource,
            "format": normalized,
            "query": {key: value for key, value in query.items() if key in spec.filters},
            "keyword": str(keyword or "").strip()[:200],
            "filters": {key: str(value) for key, value in filters.items()},
        },
        priority=200,
    )
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXPORT,
        spec.audit_resource or spec.resource,
        _int_or_none(filters.get("project_id")),
        spec.label,
        {"mode": "async", "format": normalized, "taskId": task.get("id")},
    )
    conn.commit()
    return task


def run_export(
    conn: sqlite3.Connection,
    task: dict[str, Any],
    payload: dict[str, Any],
    db_path: Path,
) -> dict[str, Any]:
    """队列 worker 里执行：分批扫描 → 分批写盘 → 回写产物名与行数。"""
    spec = spec_of(str(payload.get("resource") or ""))
    fmt = spec.normalize_format(payload.get("format"))
    keyword = str(payload.get("keyword") or "").strip()
    filters = spec.resolve_filters(dict(payload.get("query") or {}))
    total = spec.count(conn, filters, keyword)
    if total > SCAN_MAX_ROWS:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"当前筛选有 {total} 行，超过单次导出上限 {SCAN_MAX_ROWS} 行，请缩小筛选范围",
        )

    root = artifact_root()
    root.mkdir(parents=True, exist_ok=True)
    prune_artifacts(root)
    task_id = int(task["id"])
    filename = spec.filename(conn, filters, fmt)
    # 落盘名只用 ASCII 与任务号，展示名留在结果里，避免中文/路径分隔符进文件名
    stored = f"export-{task_id}-{int(time.time() * 1000)}.{fmt}"
    path = root / stored
    key = f"task:{task_id}"

    written = 0
    batches = 0
    try:
        if fmt in tabular_export.TABULAR_FORMATS:
            if not spec.columns:
                raise AppError(ErrorCode.PARAM_ERROR, f"{spec.label}不支持 {fmt} 导出")
            written, batches = _write_tabular(
                conn, key, task_id, path, fmt, spec, filters, keyword, total
            )
        else:
            if spec.workbook is None:
                raise AppError(ErrorCode.PARAM_ERROR, f"{spec.label}不支持 {fmt} 导出")
            written = total
            batches = 1
            path.write_bytes(spec.workbook(conn, filters, keyword))
    except Exception:
        # 半截文件不留，否则用户下载到的是坏文件
        path.unlink(missing_ok=True)
        raise

    _progress(conn, task_id, 100)
    audit_service.record(
        conn,
        audit_service.AUDIT_ACTION_EXPORT,
        spec.audit_resource or spec.resource,
        _int_or_none(filters.get("project_id")),
        filename,
        {"mode": "async", "format": fmt, "count": written, "taskId": task_id, "batches": batches},
    )
    conn.commit()
    return {
        "resource": spec.resource,
        "format": fmt,
        "count": written,
        "filename": filename,
        "storedName": stored,
        "sizeBytes": path.stat().st_size if path.is_file() else 0,
        "finishedAt": utcnow_iso(),
    }


def _write_tabular(
    conn: sqlite3.Connection,
    cancel_key: str,
    task_id: int,
    path: Path,
    fmt: str,
    spec: ExportSpec,
    filters: dict[str, Any],
    keyword: str,
    total: int,
) -> tuple[int, int]:
    """CSV / JSON 逐批写盘：内存里只有当前这一批行和写入缓冲。返回 (行数, 批次数)。"""
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = (
            tabular_export.CsvStreamWriter(handle, spec.columns)
            if fmt == tabular_export.CSV_FORMAT
            else tabular_export.JsonStreamWriter(handle, spec.columns, total)
        )
        done = 0
        batches = 0
        for batch in spec.pages(conn, filters, keyword):
            if task_control.is_cancelled(cancel_key):
                raise job_queue.TaskCancelled()
            writer.write(batch)
            batches += 1
            done += len(batch)
            _progress(conn, task_id, min(99, int(done * 100 / total) if total else 100))
        return writer.close(), batches


def _progress(conn: sqlite3.Connection, task_id: int, value: int) -> None:
    conn.execute(
        "UPDATE sys_task SET progress = ?, updated_at = ? WHERE id = ?",
        (int(value), utcnow_iso(), task_id),
    )
    conn.commit()


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def download(conn: sqlite3.Connection, task_id: int) -> Response:
    """任务成功且产物还在本机时下发文件；否则给可读的业务错误。"""
    task = job_queue.get_task(conn, task_id)
    if task is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "导出任务不存在")
    status = str(task.get("status") or "")
    if status in (job_queue.TASK_QUEUED, job_queue.TASK_RUNNING):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "导出还在进行，稍后再下载")
    if status != job_queue.TASK_SUCCESS:
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"导出未成功（{task.get('errorMessage') or status}）",
        )
    result = task.get("result") or {}
    path = artifact_path(str(result.get("storedName") or ""))
    if path is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "导出文件已不在本机，请重新导出")
    fmt = str(result.get("format") or "csv")
    response = send_file(
        path,
        as_attachment=True,
        download_name=str(result.get("filename") or path.name),
        mimetype=MIME_TYPES.get(fmt, "application/octet-stream"),
    )
    if fmt in tabular_export.TABULAR_FORMATS:
        # 文本类补 charset，和同步导出保持一致，Excel 双击不乱码
        response.headers["Content-Type"] = f"{MIME_TYPES.get(fmt)}; charset=utf-8"
    count = result.get("count")
    if count is not None:
        response.headers["X-AITP-Export-Count"] = str(count)
    return response


# ---------------- 路由挂载 ----------------

def install_routes(bp: Blueprint, resource: str, path_prefix: str = "") -> None:
    """在资源蓝图上挂「提交导出任务」与「下载导出文件」两个路由。

    `POST <prefix>/export/async` 收 JSON `{format, query, keyword}`，也接受直接把它们
    放在查询串上（与同步导出同一个口径），返回 sys_task 任务快照。
    `GET <prefix>/export/files/<taskId>` 下载该任务产出的文件。
    """
    suffix = re.sub(r"[^a-zA-Z0-9]+", "_", path_prefix).strip("_") or resource.replace("_", "")

    @bp.post(f"{path_prefix}/export/async", endpoint=f"submit_export_job_{suffix}")
    def submit_export_job() -> Response:
        conn = _db()
        data = request.get_json(silent=True) or {}
        if not isinstance(data, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "请求体必须是 JSON 对象")
        raw_query = data.get("query")
        query: dict[str, Any] = raw_query if isinstance(raw_query, dict) else _args_to_dict()
        keyword = str(data.get("keyword") or request.args.get("keyword") or "").strip()
        fmt = str(data.get("format") or request.args.get("format") or "")
        task = submit(conn, resource=resource, fmt=fmt, query=query, keyword=keyword)
        return ok(task)

    @bp.get(f"{path_prefix}/export/files/<int:task_id>", endpoint=f"download_export_job_{suffix}")
    def download_export_file(task_id: int) -> Response:
        return download(_db(), int(task_id))


def _db() -> sqlite3.Connection:
    from ..db.connection import get_db

    return get_db()


def _args_to_dict() -> dict[str, str]:
    return {key: request.args.get(key, "") for key in request.args}


job_queue.register(EXPORT_KIND, run_export)
