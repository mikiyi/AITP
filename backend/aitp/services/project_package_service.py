"""项目级资产包（三期 GAP-41）。

一个项目连同模块树、版本、需求、用例、缺陷、知识、自动化脚本、断言规则
打成一个 zip（结构包），可在另一台 AITP 上预览并导入。

边界（首版）：
  - 只导出结构资产，不含执行历史/证据附件/密钥/附件文件；
  - 导入默认拒绝同编码项目，可选「改名导入」（CODE-IMPORT-N）或跳过；
  - 所有 id 重新分配，模块父子、用例→需求/模块/版本等外键同步重映射；
  - 包内不含任何 password / secret / token / api_key 字段。
"""

from __future__ import annotations

import io
import json
import re
import sqlite3
import zipfile
from pathlib import Path
from typing import Any

from ..errors import AppError, ErrorCode
from ..repositories.sys_config_repo import SysConfigRepository

PACKAGE_FORMAT = "aitp-project-package"
PACKAGE_VERSION = 1
MAX_ENTRIES = 5000
MAX_UNCOMPRESSED = 100 * 1024 * 1024

CONFIG_INCLUDE_BUG_SHOTS = "backup.include_bug_screenshots"

_SECRET_COLUMN_RE = re.compile(r"(password|passwd|secret|token|api[_-]?key|credential)", re.I)
_DROP_COLUMNS = {"ai_task_id", "session_id", "file_path", "ref_id", "merged_into", "content_checksum"}

#: 表与需要重映射的外键；None 表示该外键在导入时清空（如环境绑定）
_CORE_TABLES: tuple[tuple[str, dict[str, str | None]], ...] = (
    ("biz_project_version", {}),
    ("biz_module", {"parent_id": "biz_module"}),
    ("biz_requirement", {"module_id": "biz_module", "version_id": "biz_project_version"}),
    ("biz_test_case", {
        "requirement_id": "biz_requirement", "module_id": "biz_module",
        "version_id": "biz_project_version",
    }),
    ("biz_bug", {"module_id": "biz_module", "version_id": "biz_project_version"}),
    ("biz_knowledge_article", {"module_id": "biz_module"}),
    ("biz_automation_script", {
        "module_id": "biz_module", "version_id": "biz_project_version", "env_id": None,
    }),
    ("biz_assertion_rule", {"module_id": "biz_module"}),
)


def _include_bug_screenshots(conn: sqlite3.Connection) -> bool:
    raw = str(SysConfigRepository(conn).get_value(CONFIG_INCLUDE_BUG_SHOTS, "0") or "").strip().lower()
    return raw not in ("", "0", "false", "off", "no")


def _data_dir(conn: sqlite3.Connection) -> Path:
    row = conn.execute("PRAGMA database_list").fetchone()
    path = str((row[2] if row else "") or "")
    return Path(path).parent if path else Path("data")


def _columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({table})")]


def _clean_row(row: dict[str, Any], columns: list[str]) -> dict[str, Any]:
    # 保留旧 id 供导入时重映射外键；插入时会被排除，不会写进新库
    result: dict[str, Any] = {"id": row.get("id")}
    for column in columns:
        if column in ("id", "project_id", "is_deleted") or column in _DROP_COLUMNS:
            continue
        if _SECRET_COLUMN_RE.search(column):
            continue
        result[column] = row.get(column)
    return result


def export_package(conn: sqlite3.Connection, project_id: int) -> bytes:
    project = conn.execute(
        "SELECT code, name, description, owner, status FROM biz_project"
        " WHERE id = ? AND is_deleted = 0", (project_id,)
    ).fetchone()
    if not project:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
    project_info = dict(project)
    data: dict[str, list[dict[str, Any]]] = {}
    counts: dict[str, int] = {}
    for table, _ in _CORE_TABLES:
        columns = _columns(conn, table)
        rows = conn.execute(
            f"SELECT * FROM {table} WHERE project_id = ? AND is_deleted = 0 ORDER BY id",
            (project_id,),
        ).fetchall()
        cleaned = [_clean_row(dict(row), columns) for row in rows]
        data[table] = cleaned
        counts[table] = len(cleaned)

    include_shots = _include_bug_screenshots(conn)
    shot_root = _data_dir(conn) / "bug-screenshots"
    shot_files: list[tuple[str, str, bytes]] = []
    if include_shots:
        rows = conn.execute(
            "SELECT s.bug_id, s.file_name, b.bug_no FROM biz_bug_screenshot s"
            " JOIN biz_bug b ON b.id = s.bug_id"
            " WHERE s.is_deleted = 0 AND b.is_deleted = 0 AND b.project_id = ?",
            (project_id,),
        ).fetchall()
        for row in rows:
            bug_no = str(row["bug_no"] or "")
            file_name = str(row["file_name"] or "")
            path = shot_root / bug_no / file_name
            if path.is_file():
                shot_files.append((bug_no, file_name, path.read_bytes()))
    manifest: dict[str, Any] = {
        "format": PACKAGE_FORMAT,
        "version": PACKAGE_VERSION,
        "project": project_info,
        "scope": {"history": False, "evidence": False, "attachments": False,
                   "screenshots": include_shots},
        "counts": counts,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2))
        for table, rows in data.items():
            archive.writestr(
                f"data/{table}.json",
                json.dumps(rows, ensure_ascii=False, indent=2),
            )
        for bug_no, file_name, raw_file in shot_files:
            archive.writestr(f"screenshots/{bug_no}/{file_name}", raw_file)
        if shot_files:
            manifest["counts"]["bug_screenshots"] = len(shot_files)
    return buffer.getvalue()


def _read_package(raw: bytes) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    if not raw:
        raise AppError(ErrorCode.PARAM_ERROR, "资产包为空")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw))
    except zipfile.BadZipFile:
        raise AppError(ErrorCode.PARAM_ERROR, "不是有效的 AITP 资产包（zip）") from None
    infos = archive.infolist()
    if len(infos) > MAX_ENTRIES:
        raise AppError(ErrorCode.PARAM_ERROR, "资产包文件条目过多")
    if sum(info.file_size for info in infos) > MAX_UNCOMPRESSED:
        raise AppError(ErrorCode.PARAM_ERROR, "资产包解压后体积超过限制")
    names = {info.filename for info in infos}
    if "manifest.json" not in names:
        raise AppError(ErrorCode.PARAM_ERROR, "资产包缺少 manifest.json")
    try:
        manifest = json.loads(archive.read("manifest.json").decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise AppError(ErrorCode.PARAM_ERROR, "manifest.json 无法解析") from None
    if manifest.get("format") != PACKAGE_FORMAT:
        raise AppError(ErrorCode.PARAM_ERROR, "资产包格式不匹配")
    data: dict[str, list[dict[str, Any]]] = {}
    for table, _ in _CORE_TABLES:
        name = f"data/{table}.json"
        if name not in names:
            data[table] = []
            continue
        try:
            rows = json.loads(archive.read(name).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            raise AppError(ErrorCode.PARAM_ERROR, f"{name} 无法解析") from None
        if not isinstance(rows, list) or not all(isinstance(row, dict) for row in rows):
            raise AppError(ErrorCode.PARAM_ERROR, f"{name} 必须是对象数组")
        data[table] = rows
    return manifest, data


def preview_package(conn: sqlite3.Connection, raw: bytes) -> dict[str, Any]:
    manifest, data = _read_package(raw)
    project = manifest.get("project") or {}
    code = str(project.get("code") or "").strip()
    existing = conn.execute(
        "SELECT id, name FROM biz_project WHERE code = ? AND is_deleted = 0", (code,)
    ).fetchone()
    counts = {table: len(rows) for table, rows in data.items()}
    return {
        "manifest": manifest,
        "counts": counts,
        "conflict": existing is not None,
        "existingProject": dict(existing) if existing else None,
    }


def _unique_code(conn: sqlite3.Connection, base: str) -> str:
    for index in range(1, 1000):
        code = f"{base}-IMPORT-{index}"[:64]
        row = conn.execute(
            "SELECT 1 FROM biz_project WHERE code = ? AND is_deleted = 0", (code,)
        ).fetchone()
        if not row:
            return code
    raise AppError(ErrorCode.PARAM_ERROR, "无法生成不重复的项目编码")


def import_package(
    conn: sqlite3.Connection, raw: bytes, *, conflict: str = "skip"
) -> dict[str, Any]:
    manifest, data = _read_package(raw)
    project = manifest.get("project") or {}
    original_code = str(project.get("code") or "").strip()
    if conflict not in ("skip", "rename"):
        raise AppError(ErrorCode.PARAM_ERROR, "conflict 只能是 skip / rename")
    if not original_code:
        raise AppError(ErrorCode.PARAM_ERROR, "资产包缺少项目编码")
    existing = conn.execute(
        "SELECT id FROM biz_project WHERE code = ? AND is_deleted = 0", (original_code,)
    ).fetchone()
    if existing and conflict == "skip":
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"项目编码「{original_code}」已存在；请选择「改名导入」或换一个目标库",
        )
    code = original_code if not existing else _unique_code(conn, original_code)
    now = _now()
    cursor = conn.execute(
        "INSERT INTO biz_project (code, name, description, owner, status, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, 0)",
        (
            code,
            str(project.get("name") or code)[:200],
            str(project.get("description") or "")[:1000],
            str(project.get("owner") or "")[:100],
            str(project.get("status") or "active"),
            now,
            now,
        ),
    )
    new_project_id = int(cursor.lastrowid or 0)
    id_maps: dict[str, dict[int, int]] = {table: {} for table, _ in _CORE_TABLES}
    counts: dict[str, int] = {}
    new_bugs_by_old_no: dict[str, dict[str, Any]] = {}

    for table, refs in _CORE_TABLES:
        columns = _columns(conn, table)
        insert_columns = [column for column in columns if column not in ("id", "is_deleted")]
        count = 0
        for row in data.get(table, []):
            payload: dict[str, Any] = {}
            for column in insert_columns:
                if column == "project_id":
                    payload[column] = new_project_id
                elif column in refs:
                    ref_table = refs[column]
                    if ref_table is None:
                        payload[column] = None
                    else:
                        old = row.get(column)
                        payload[column] = id_maps.get(ref_table, {}).get(int(old)) if old is not None else None
                elif column in _DROP_COLUMNS or _SECRET_COLUMN_RE.search(column):
                    continue
                else:
                    payload[column] = row.get(column)
            if "is_deleted" in columns:
                payload["is_deleted"] = 0
            # 自动化脚本编号是全局唯一；导入时按新项目重编号，避免与原项目撞号
            if table == "biz_automation_script":
                payload["script_no"] = f"IMP{new_project_id}-{row.get('script_no') or count + 1}"[:64]
            # 缺陷编号全局唯一且不复用；导入时按来源前缀重新取号
            if table == "biz_bug":
                from ..repositories.bug_repo import BugRepository

                payload["bug_no"] = BugRepository(conn).next_bug_no(str(row.get("source") or "manual"))
            placeholders = ", ".join("?" for _ in payload)
            names = ", ".join(payload)
            cursor = conn.execute(
                f"INSERT INTO {table} ({names}) VALUES ({placeholders})",
                list(payload.values()),
            )
            new_id = int(cursor.lastrowid or 0)
            id_maps[table][int(row.get("id") or 0)] = new_id
            if table == "biz_bug":
                old_no = str(row.get("bug_no") or "")
                new_bugs_by_old_no[old_no] = {
                    "id": new_id, "bug_no": str(payload.get("bug_no") or ""),
                }
            count += 1
        counts[table] = count

    # 模块父子关系是自引用，第一遍插成 NULL，第二遍按旧 id 映射回填
    for old_id, new_id in id_maps["biz_module"].items():
        old_parent = next(
            (row.get("parent_id") for row in data["biz_module"] if int(row.get("id") or 0) == old_id),
            None,
        )
        new_parent = id_maps["biz_module"].get(int(old_parent)) if old_parent is not None else None
        conn.execute(
            "UPDATE biz_module SET parent_id = ? WHERE id = ?", (new_parent, new_id)
        )

    # 截图文件按旧编号归档；导入后按新缺陷号重新落盘并登记
    if new_bugs_by_old_no:
        from ..services import bug_screenshot_service

        restored_shots = 0
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            for info in archive.infolist():
                parts = info.filename.split("/")
                if len(parts) != 3 or parts[0] != "screenshots":
                    continue
                bug = new_bugs_by_old_no.get(parts[1])
                if not bug:
                    continue
                name = parts[2]
                if not name or "/" in name or "\\" in name or name.startswith("."):
                    continue
                shot_bytes = archive.read(info)
                try:
                    bug_screenshot_service.save_screenshots(conn, bug, [(name, shot_bytes)])
                    restored_shots += 1
                except AppError:
                    continue
        if restored_shots:
            counts["bug_screenshots"] = restored_shots

    return {"projectId": new_project_id, "code": code, "counts": counts}


def _now() -> str:
    from ..common.time_utils import utcnow_iso

    return utcnow_iso()
