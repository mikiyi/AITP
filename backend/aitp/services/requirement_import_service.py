"""需求批量拆分导入服务（四期专项 D）。

一份需求文档 → AI 拆成多条结构化需求（`requirement_import` 能力 + schema 校验）
→ 人工逐条勾选 → 批量落库为草稿需求，并记导入批次与文件指纹。

三条口径：
  - **AI 只建议、人决策**：拆分结果只回给前端勾选，落库由显式采纳接口完成；
  - **降级不阻塞**：没有可用模型时返回 `generated=False` 与提示，不抛错，
    用户仍可手工逐条新建需求；
  - **重复导入可识别**：同一项目下同一文档指纹复用同一条批次；条目级幂等
    复用外部键唯一索引（`external_platform='ai-import'`），重复采纳自动跳过。
"""

from __future__ import annotations

import hashlib
import sqlite3
from typing import Any, Callable, cast

from ..ai.capability_codes import CAP_REQUIREMENT_IMPORT
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.module_repo import ModuleRepository
from ..repositories.project_repo import ProjectRepository
from ..repositories.requirement_repo import RequirementRepository
from . import lab_service

#: 外部键平台名：与外部导入共用同一套唯一索引口径
IMPORT_PLATFORM = "ai-import"

#: 单次拆分上限（提示词与库里都按这个收敛，防止一份文档炸出几百条）
MAX_ITEMS = 50
DEFAULT_MAX_ITEMS = 30
#: 送入提示词的文档长度上限
MAX_DOC_CHARS = 200_000

#: 拆分执行器签名：注入点便于门禁替换为桩，不影响生产链路
Runner = Callable[[sqlite3.Connection, int, str, int | None, int], dict[str, Any]]

_NO_MODEL_MESSAGE = (
    "无可用 AI 模型，已跳过自动拆分。可先到「设置 → AI 配置」启用模型后重试，"
    "也可以手工逐条新建需求。"
)


def fingerprint(text: str) -> str:
    """文件/文本指纹：去首尾空白后取 sha256 前 16 位，稳定且够短。"""
    return hashlib.sha256((text or "").strip().encode("utf-8")).hexdigest()[:16]


def _require_project(conn: sqlite3.Connection, project_id: int) -> None:
    if not ProjectRepository(conn).get(project_id):
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")


def _clamp_items(value: Any) -> int:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return DEFAULT_MAX_ITEMS
    return max(1, min(MAX_ITEMS, number))


def _find_batch(
    conn: sqlite3.Connection, project_id: int, fp: str
) -> sqlite3.Row | None:
    if not fp:
        return None
    return cast("sqlite3.Row | None", conn.execute(
        "SELECT * FROM biz_requirement_import"
        " WHERE project_id = ? AND fingerprint = ? AND is_deleted = 0",
        (project_id, fp),
    ).fetchone())


def _get_batch(conn: sqlite3.Connection, project_id: int, import_id: int) -> sqlite3.Row:
    row = cast("sqlite3.Row | None", conn.execute(
        "SELECT * FROM biz_requirement_import"
        " WHERE id = ? AND project_id = ? AND is_deleted = 0",
        (import_id, project_id),
    ).fetchone())
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "导入批次不存在")
    return row


def _create_batch(
    conn: sqlite3.Connection, project_id: int, fp: str, file_name: str,
    item_count: int, chars: int,
) -> int:
    now = utcnow_iso()
    cursor = conn.execute(
        "INSERT INTO biz_requirement_import"
        " (project_id, fingerprint, file_name, item_count, adopted_count, chars,"
        " created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 0, ?, ?, ?, 0)",
        (project_id, fp, file_name[:200], item_count, chars, now, now),
    )
    return int(cast(int, cursor.lastrowid))


def _touch_batch(
    conn: sqlite3.Connection, batch_id: int, item_count: int, chars: int
) -> None:
    conn.execute(
        "UPDATE biz_requirement_import"
        " SET item_count = MAX(item_count, ?), chars = MAX(chars, ?), updated_at = ?"
        " WHERE id = ?",
        (item_count, chars, utcnow_iso(), batch_id),
    )


def _default_runner(
    conn: sqlite3.Connection, project_id: int, document: str,
    profile_id: int | None, max_items: int,
) -> dict[str, Any]:
    """生产链路：走实验室同一套「渲染 → 调用 → schema 校验 → 修正重试」。"""
    project = ProjectRepository(conn).get(project_id) or {}
    variables = {
        "documentText": document[:MAX_DOC_CHARS],
        "projectName": str(project.get("name") or ""),
        "maxItems": str(max_items),
    }
    try:
        plan = lab_service.prepare_run(
            conn, CAP_REQUIREMENT_IMPORT, variables,
            profile_id=profile_id, project_id=project_id,
        )
        run = lab_service.execute_run(plan, conn=conn)
    except AppError as exc:
        if exc.code == ErrorCode.AI_CREDENTIAL_MISSING:
            return {
                "generated": False, "items": [], "schema": {},
                "message": _NO_MODEL_MESSAGE, "degraded": True,
            }
        raise
    items = run.parsed if isinstance(run.parsed, list) else []
    schema = dict(run.schema or {})
    generated = bool(items)
    message = "" if generated else str(
        schema.get("message") or "模型没有返回可用的需求条目，请调整文档内容后重试"
    )
    return {
        "generated": generated, "items": items, "schema": schema,
        "message": message, "degraded": False,
    }


def split(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    text: str,
    file_name: str = "",
    profile_id: int | None = None,
    max_items: Any = DEFAULT_MAX_ITEMS,
    runner: Runner | None = None,
) -> dict[str, Any]:
    """拆分需求文档：返回勾选用的条目、批次与指纹，不落需求。"""
    _require_project(conn, project_id)
    document = (text or "").strip()
    if not document:
        raise AppError(ErrorCode.PARAM_ERROR, "请粘贴或导入需求文档内容")
    limit = _clamp_items(max_items)
    fp = fingerprint(document)
    batch = _find_batch(conn, project_id, fp)

    result = (runner or _default_runner)(conn, project_id, document, profile_id, limit)
    items = [
        item for item in (result.get("items") or [])
        if isinstance(item, dict) and str(item.get("title") or "").strip()
    ][:MAX_ITEMS]
    generated = bool(result.get("generated")) and bool(items)

    import_id = int(batch["id"]) if batch else None
    duplicated = batch is not None
    if generated and batch is None:
        import_id = _create_batch(
            conn, project_id, fp, file_name, len(items), len(document)
        )
    elif generated and batch is not None:
        _touch_batch(conn, int(batch["id"]), len(items), len(document))

    message = str(result.get("message") or "")
    if generated and batch is not None:
        adopted = int(batch["adopted_count"] or 0)
        message = f"该文档已导入过（已采纳 {adopted} 条），重复条目在采纳时会自动跳过"

    return {
        "generated": generated,
        "degraded": bool(result.get("degraded")),
        "duplicated": duplicated,
        "fingerprint": fp,
        "importId": import_id,
        "fileName": file_name,
        "chars": len(document),
        "items": items if generated else [],
        "schema": result.get("schema") or {},
        "message": message,
    }


def _resolve_module(conn: sqlite3.Connection, project_id: int, module_path: str) -> int | None:
    """按「父/子/功能」路径或末级名称匹配功能点；匹配不到返回 None（不猜）。"""
    wanted = module_path.strip().strip("/")
    if not wanted:
        return None
    rows = ModuleRepository(conn).list_all(project_id)
    by_id = {int(row["id"]): row for row in rows}
    names: dict[int, str] = {}

    def name_of(node_id: int) -> str:
        if node_id not in names:
            parent_id = by_id.get(node_id, {}).get("parent_id")
            own = str(by_id.get(node_id, {}).get("name") or "")
            names[node_id] = f"{name_of(int(parent_id))}/{own}" if parent_id else own
        return names[node_id]

    target = wanted.replace(" ", "").lower()
    leaf_hit: int | None = None
    for node_id in by_id:
        full = name_of(node_id).replace(" ", "").lower()
        if full == target or full.endswith(f"/{target}"):
            return node_id
        if full.rsplit("/", 1)[-1] == target and leaf_hit is None:
            leaf_hit = node_id
    return leaf_hit


def _external_exists(conn: sqlite3.Connection, project_id: int, external_id: str) -> bool:
    row = conn.execute(
        "SELECT id FROM biz_requirement"
        " WHERE project_id = ? AND external_platform = ? AND external_id = ? AND is_deleted = 0",
        (project_id, IMPORT_PLATFORM, external_id),
    ).fetchone()
    return row is not None


def _content_of(item: dict[str, Any]) -> str:
    description = str(item.get("description") or "").strip()
    acceptance = str(item.get("acceptance") or "").strip()
    if not acceptance:
        return description
    block = f"验收要点：\n{acceptance}"
    return f"{description}\n\n{block}" if description else block


def _batch_for_adopt(
    conn: sqlite3.Connection, project_id: int, import_id: int | None,
    fp: str, file_name: str, item_count: int,
) -> sqlite3.Row:
    if import_id:
        return _get_batch(conn, project_id, import_id)
    if fp:
        existing = _find_batch(conn, project_id, fp)
        if existing:
            return existing
        _create_batch(conn, project_id, fp, file_name, item_count, 0)
        created = _find_batch(conn, project_id, fp)
        if created is None:  # pragma: no cover - 插入后立刻查不到属于数据库异常
            raise AppError(ErrorCode.INTERNAL_ERROR, "导入批次创建失败")
        return created
    # 没有指纹（手工勾选直接采纳）时补一条空指纹批次，不参与唯一约束
    batch_id = _create_batch(conn, project_id, "", file_name, item_count, 0)
    return _get_batch(conn, project_id, batch_id)


def adopt(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    items: list[Any],
    import_id: int | None = None,
    fingerprint_value: str = "",
    file_name: str = "",
) -> dict[str, Any]:
    """把勾选的需求批量落库为草稿；重复条目按外部键跳过。"""
    _require_project(conn, project_id)
    if not items:
        raise AppError(ErrorCode.PARAM_ERROR, "请至少勾选一条需求")
    if len(items) > MAX_ITEMS:
        raise AppError(ErrorCode.PARAM_ERROR, f"单次最多采纳 {MAX_ITEMS} 条需求")

    batch = _batch_for_adopt(
        conn, project_id, import_id, fingerprint_value.strip(), file_name, len(items)
    )
    batch_fp = str(batch["fingerprint"] or "")
    repo = RequirementRepository(conn)
    created: list[int] = []
    skipped: list[str] = []
    for index, raw in enumerate(items):
        if not isinstance(raw, dict):
            skipped.append("第 %d 条不是对象" % (index + 1))
            continue
        title = str(raw.get("title") or "").strip()[:200]
        if not title:
            skipped.append("第 %d 条缺少标题" % (index + 1))
            continue
        external_id = f"{batch_fp}#{index + 1}" if batch_fp else ""
        if external_id and _external_exists(conn, project_id, external_id):
            skipped.append(title)
            continue
        new_id = repo.insert({
            "project_id": project_id,
            "req_no": repo.next_req_no(project_id),
            "title": title,
            "content": _content_of(raw),
            "source": "ai",
            "version": "",
            "status": "draft",
            "priority": str(raw.get("priority") or "P2")[:20],
            "tags": str(raw.get("modulePath") or "").strip()[:500],
            "version_id": None,
            "module_id": _resolve_module(conn, project_id, str(raw.get("modulePath") or "")),
            "external_platform": IMPORT_PLATFORM,
            "external_id": external_id,
            "external_url": "",
            "import_id": int(batch["id"]),
        })
        created.append(new_id)

    if created:
        conn.execute(
            "UPDATE biz_requirement_import"
            " SET adopted_count = adopted_count + ?, item_count = MAX(item_count, ?),"
            " updated_at = ? WHERE id = ?",
            (len(created), len(items), utcnow_iso(), int(batch["id"])),
        )
    return {
        "importId": int(batch["id"]),
        "fingerprint": batch_fp,
        "created": len(created),
        "skipped": len(skipped),
        "ids": created,
        "skippedTitles": skipped[:20],
        "message": (
            f"已采纳 {len(created)} 条需求" + (f"，跳过重复 {len(skipped)} 条" if skipped else "")
        ),
    }


def list_imports(
    conn: sqlite3.Connection, project_id: int, limit: int = 20
) -> list[dict[str, Any]]:
    """最近导入批次：供界面展示「这份文档导入过、采纳了多少条」。"""
    _require_project(conn, project_id)
    rows = conn.execute(
        "SELECT * FROM biz_requirement_import"
        " WHERE project_id = ? AND is_deleted = 0"
        " ORDER BY created_at DESC, id DESC LIMIT ?",
        (project_id, max(1, min(100, limit))),
    ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "projectId": int(row["project_id"]),
            "fingerprint": str(row["fingerprint"] or ""),
            "fileName": str(row["file_name"] or ""),
            "itemCount": int(row["item_count"] or 0),
            "adoptedCount": int(row["adopted_count"] or 0),
            "chars": int(row["chars"] or 0),
            "createdAt": row["created_at"],
            "updatedAt": row["updated_at"],
        }
        for row in rows
    ]
