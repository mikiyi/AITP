"""外部平台只读导入服务（三期 GAP-40）。

只读单向：从外部平台拉取需求/用例/缺陷进 AITP，绝不回写源平台。
凭据走系统密钥库；库内只存引用与掩码。映射模板可保存复用；
导入按 项目 + 平台 + 外部 id 去重，批次与审计留痕。
"""

from __future__ import annotations

import json
from typing import Any

from ..common.secret_store import get_store
from ..common.security import mask
from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.external_import_repo import (
    ExternalImportBatchRepository,
    ExternalImportConfigRepository,
    ExternalImportMappingRepository,
    batch_public,
    config_public,
    mapping_public,
)
from .external_import.base import ExternalClient
from .external_import.jira import JiraClient
from .external_import.jira import default_mapping as jira_mapping
from .external_import.mapping import apply_mapping
from .external_import.testlink import TestLinkClient
from .external_import.testlink import default_mapping as testlink_mapping
from .external_import.zentao import ZentaoClient
from .external_import.zentao import default_mapping as zentao_mapping

PLATFORMS: tuple[str, ...] = ("zentao", "jira", "testlink")
PLATFORM_LABELS: dict[str, str] = {
    "zentao": "禅道 Zentao",
    "jira": "Jira",
    "testlink": "TestLink",
}
PLATFORM_KINDS: dict[str, tuple[str, ...]] = {
    "zentao": ("requirements", "cases", "bugs"),
    "jira": ("requirements", "cases", "bugs"),
    "testlink": ("requirements", "cases"),
}
PLATFORM_CODE_PREFIX: dict[str, str] = {"zentao": "ZT", "jira": "JIRA", "testlink": "TL"}
KIND_LABELS: dict[str, str] = {
    "requirements": "需求",
    "cases": "用例",
    "bugs": "缺陷",
}
KINDS: tuple[str, ...] = tuple(KIND_LABELS.keys())

MAX_RESULTS = 1000
DEFAULT_RESULTS = 200

SECRET_PREFIX = "external_import"


def _fail(message: str, code: int = ErrorCode.PARAM_ERROR) -> AppError:
    return AppError(code, message)


def _platform(raw: Any) -> str:
    platform = str(raw or "").strip().lower()
    if platform not in PLATFORMS:
        raise _fail(f"平台只能是：{'、'.join(PLATFORMS)}")
    return platform


def _kinds(raw: Any, platform: str) -> list[str]:
    value = raw if isinstance(raw, list) else str(raw or "").split(",")
    kinds = [str(item).strip() for item in value if str(item).strip()]
    if not kinds:
        raise _fail("请至少选择一个导入内容（需求/用例/缺陷）")
    invalid = [kind for kind in kinds if kind not in KIND_LABELS]
    if invalid:
        raise _fail(f"导入内容只能是：{'、'.join(KIND_LABELS)}；收到：{'、'.join(invalid)}")
    unsupported = [kind for kind in kinds if kind not in PLATFORM_KINDS[platform]]
    if unsupported:
        names = "、".join(KIND_LABELS[kind] for kind in unsupported)
        raise _fail(f"{PLATFORM_LABELS[platform]} 不支持导入：{names}")
    return kinds


def default_mapping(platform: str = "zentao") -> dict[str, Any]:
    if platform == "jira":
        return jira_mapping()
    if platform == "testlink":
        return testlink_mapping()
    return zentao_mapping()


def _secret_ref(project_id: int, platform: str) -> str:
    return f"{SECRET_PREFIX}:{project_id}:{platform}"


def save_config(
    conn: Any, *, project_id: int, platform: str, base_url: str, username: str,
    password: str = "", server_username: str = "", server_password: str = "",
    product_id: str = "", extra: Any = None,
) -> dict[str, Any]:
    platform = _platform(platform)
    base = str(base_url or "").strip().rstrip("/")
    if not base.startswith(("http://", "https://")):
        raise _fail(f"{PLATFORM_LABELS[platform]}地址必须以 http:// 或 https:// 开头")
    repo = ExternalImportConfigRepository(conn)
    existing = repo.get_by_project_platform(project_id, platform)
    ref = existing["auth_ref"] if existing and existing.get("auth_ref") else _secret_ref(project_id, platform)
    password = str(password or "")
    server_password = str(server_password or "")
    # 口令只在非空时更新，避免编辑其他字段时误清已存凭据
    if password or server_password:
        secret = {"password": password, "server_password": server_password}
        # 若旧引用存在，先读旧值保留未提交的另一半
        old = _read_secret(conn, project_id, platform) if existing else None
        if old and not password:
            secret["password"] = str(old.get("password") or "")
        if old and not server_password:
            secret["server_password"] = str(old.get("server_password") or "")
        get_store().set(ref, json.dumps(secret, ensure_ascii=False))
    auth_ref = ref if (password or server_password or (existing and existing.get("auth_ref"))) else ""
    now = utcnow_iso()
    payload = {
        "project_id": project_id,
        "platform": platform,
        "base_url": base,
        "username": str(username or "")[:200],
        "product_id": str(product_id or "")[:100],
        "server_username": str(server_username or "")[:200],
        "extra_json": json.dumps(extra if isinstance(extra, dict) else {}, ensure_ascii=False),
        "auth_ref": auth_ref,
        "auth_mask": mask(password) if password else (existing.get("auth_mask") if existing else ""),
        "updated_at": now,
    }
    if existing:
        config_id = repo.upsert(project_id, platform, payload)
    else:
        payload["created_at"] = now
        config_id = repo.insert(payload)
    return get_config(conn, project_id, platform) or {"id": config_id}


def get_config(conn: Any, project_id: int, platform: str) -> dict[str, Any] | None:
    row = ExternalImportConfigRepository(conn).get_by_project_platform(project_id, platform)
    if not row:
        return None
    return config_public(row, has_password=bool(row.get("auth_ref")))


def _read_secret(conn: Any, project_id: int, platform: str) -> dict[str, Any] | None:
    row = ExternalImportConfigRepository(conn).get_by_project_platform(project_id, platform)
    if not row or not row.get("auth_ref"):
        return None
    raw = get_store().get(str(row["auth_ref"]))
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def delete_config(conn: Any, project_id: int, platform: str) -> None:
    repo = ExternalImportConfigRepository(conn)
    row = repo.get_by_project_platform(project_id, platform)
    if not row:
        raise _fail("未配置该平台", ErrorCode.RESOURCE_NOT_FOUND)
    if row.get("auth_ref"):
        get_store().delete(str(row["auth_ref"]))
    repo.soft_delete(int(row["id"]))


def list_mappings(conn: Any, project_id: int, platform: str) -> list[dict[str, Any]]:
    return ExternalImportMappingRepository(conn).list_for(project_id, platform)


def save_mapping(
    conn: Any, *, project_id: int, platform: str, name: str, mapping: Any,
    is_default: bool = False, mapping_id: int | None = None,
) -> dict[str, Any]:
    platform = _platform(platform)
    if not isinstance(mapping, dict):
        raise _fail("映射必须是对象")
    clean = str(name or "").strip()[:100]
    if not clean:
        raise _fail("映射模板名称不能为空")
    repo = ExternalImportMappingRepository(conn)
    if repo.name_exists(project_id, platform, clean, exclude=mapping_id):
        raise _fail(f"映射模板「{clean}」已存在")
    now = utcnow_iso()
    text = json.dumps(mapping, ensure_ascii=False)
    if mapping_id is None:
        mapping_id = repo.insert({
            "project_id": project_id, "platform": platform, "name": clean,
            "mapping_json": text, "is_default": 1 if is_default else 0,
            "created_at": now, "updated_at": now,
        })
    else:
        repo.update(mapping_id, {
            "name": clean, "mapping_json": text,
            "is_default": 1 if is_default else 0, "updated_at": now,
        })
    if is_default:
        repo.clear_defaults(project_id, platform, mapping_id)
    return mapping_public(repo.get_by_id(mapping_id) or {})


def delete_mapping(conn: Any, mapping_id: int, *, project_id: int) -> None:
    repo = ExternalImportMappingRepository(conn)
    row = repo.get_by_id(mapping_id)
    if not row or int(row.get("project_id") or 0) != project_id:
        raise _fail("映射模板不存在", ErrorCode.RESOURCE_NOT_FOUND)
    repo.soft_delete(mapping_id)


def _client_of(conn: Any, project_id: int, platform: str) -> ExternalClient:
    row = ExternalImportConfigRepository(conn).get_by_project_platform(project_id, platform)
    if not row:
        raise _fail(f"未配置{PLATFORM_LABELS[platform]}连接；请先保存连接配置")
    secret = _read_secret(conn, project_id, platform) or {}
    extra = _loads(row.get("extra_json"), {})
    common: dict[str, Any] = {
        "base_url": str(row.get("base_url") or ""),
        "account": str(row.get("username") or ""),
        "password": str(secret.get("password") or ""),
        "server_user": str(row.get("server_username") or ""),
        "server_password": str(secret.get("server_password") or ""),
        "paths": extra.get("paths") if isinstance(extra.get("paths"), dict) else None,
        "verify_tls": not bool(extra.get("insecureTls")),
        "extra": extra,
    }
    if platform == "jira":
        return JiraClient(**common)
    if platform == "testlink":
        return TestLinkClient(**common)
    return ZentaoClient(**common)


def _loads(raw: Any, default: Any) -> Any:
    try:
        value = json.loads(str(raw or ""))
    except (json.JSONDecodeError, TypeError):
        return default
    return value if isinstance(value, type(default)) else default


def _mapping_of(conn: Any, project_id: int, platform: str, mapping_id: int | None) -> dict[str, Any]:
    if mapping_id is not None:
        row = ExternalImportMappingRepository(conn).get_by_id(mapping_id)
        if not row or int(row.get("project_id") or 0) != project_id:
            raise _fail("映射模板不存在", ErrorCode.RESOURCE_NOT_FOUND)
        mapping = _loads(row.get("mapping_json"), {})
        return mapping if isinstance(mapping, dict) else {}
    repo = ExternalImportMappingRepository(conn)
    for item in repo.list_for(project_id, platform):
        if item.get("isDefault"):
            return item.get("mapping") or {}
    return default_mapping(platform)


def _fetch_all(
    client: ZentaoClient, product_id: str, limit: int, kinds: tuple[str, ...] | list[str] = KINDS,
) -> dict[str, list[dict[str, Any]]]:
    fetchers = {
        "requirements": client.fetch_requirements,
        "cases": client.fetch_cases,
        "bugs": client.fetch_bugs,
    }
    return {kind: fetchers[kind](product_id, limit) for kind in kinds}


def preview(
    conn: Any, *, project_id: int, platform: str, mapping_id: int | None,
    max_results: Any = None, kinds: Any = None,
) -> dict[str, Any]:
    platform = _platform(platform)
    limit = _limit(max_results, 10)
    selected = _kinds(kinds, platform) if kinds else list(PLATFORM_KINDS[platform])
    config = get_config(conn, project_id, platform)
    if not config:
        raise _fail(f"未配置{PLATFORM_LABELS[platform]}连接；请先保存连接配置")
    mapping = _mapping_of(conn, project_id, platform, mapping_id)
    errors: list[str] = []
    counts: dict[str, int] = {}
    samples: dict[str, list[str]] = {}
    with _client_of(conn, project_id, platform) as client:
        fetched = _fetch_all(client, str(config["productId"] or ""), limit, selected)
    for kind, items in fetched.items():
        counts[kind] = len(items)
        mapped = [apply_mapping(item, _singular(kind), mapping) for item in items]
        samples[kind] = [str(item.get("title") or "") for item in mapped[:5]]
        for item in mapped:
            if not str(item.get("title") or "").strip():
                errors.append(f"{KIND_LABELS[kind]} 缺少标题映射")
                break
    return {"counts": counts, "samples": samples, "errors": errors}


def _limit(raw: Any, default: int) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return default
    return max(1, min(value, MAX_RESULTS))


def _singular(kind: str) -> str:
    return {"requirements": "requirement", "cases": "case", "bugs": "bug"}[kind]


def import_items(
    conn: Any, *, project_id: int, platform: str, kinds: list[str],
    mapping_id: int | None = None, max_results: Any = None,
) -> dict[str, Any]:
    platform = _platform(platform)
    kinds = _kinds(kinds, platform)
    limit = _limit(max_results, DEFAULT_RESULTS)
    config = get_config(conn, project_id, platform)
    if not config:
        raise _fail(f"未配置{PLATFORM_LABELS[platform]}连接；请先保存连接配置")
    mapping = _mapping_of(conn, project_id, platform, mapping_id)
    repo_batch = ExternalImportBatchRepository(conn)
    now = utcnow_iso()
    batch_id = repo_batch.insert({
        "project_id": project_id, "platform": platform,
        "config_id": config["id"], "mapping_id": mapping_id,
        "kinds": ",".join(kinds), "status": "running",
        "fetched_json": "{}", "imported_json": "{}", "skipped_json": "{}",
        "errors_json": "{}", "created_at": now, "updated_at": now,
    })
    fetched_counts: dict[str, int] = {}
    imported_counts: dict[str, int] = {}
    skipped_counts: dict[str, int] = {}
    errors: dict[str, list[str]] = {}
    status = "success"
    try:
        with _client_of(conn, project_id, platform) as client:
            fetched = _fetch_all(client, str(config["productId"] or ""), limit, kinds)
            for kind in kinds:
                items = fetched.get(kind, [])
                fetched_counts[kind] = len(items)
                imported, skipped, kind_errors = _import_kind(
                    conn, project_id, platform, kind, items, mapping, client)
                imported_counts[kind] = imported
                skipped_counts[kind] = skipped
                if kind_errors:
                    errors[kind] = kind_errors
        if errors:
            status = "partial"
    except AppError:
        repo_batch.update(batch_id, {"status": "error"})
        raise
    repo_batch.update(batch_id, {
        "status": status,
        "fetched_json": json.dumps(fetched_counts, ensure_ascii=False),
        "imported_json": json.dumps(imported_counts, ensure_ascii=False),
        "skipped_json": json.dumps(skipped_counts, ensure_ascii=False),
        "errors_json": json.dumps(errors, ensure_ascii=False),
    })
    return {
        "batchId": batch_id,
        "status": status,
        "fetched": fetched_counts,
        "imported": imported_counts,
        "skipped": skipped_counts,
        "errors": errors,
    }


def _import_kind(
    conn: Any, project_id: int, platform: str, kind: str,
    items: list[dict[str, Any]], mapping: dict[str, Any], client: ExternalClient,
) -> tuple[int, int, list[str]]:
    imported = 0
    skipped = 0
    errors: list[str] = []
    singular = _singular(kind)
    for raw in items:
        mapped = apply_mapping(raw, singular, mapping)
        external_id = client.external_id(raw)
        title = str(mapped.get("title") or "").strip()
        if not external_id:
            errors.append(f"{KIND_LABELS[kind]} 缺外部 id，跳过")
            continue
        if not title:
            errors.append(f"外部 id {external_id} 缺标题，跳过")
            continue
        if _exists(conn, singular, project_id, platform, external_id):
            skipped += 1
            continue
        try:
            _insert(conn, singular, project_id, platform, external_id,
                    client.url_for(singular, external_id), mapped)
            imported += 1
        except AppError as exc:
            errors.append(f"{external_id}：{exc.message}")
    return imported, skipped, errors


def _exists(conn: Any, singular: str, project_id: int, platform: str, external_id: str) -> bool:
    table = {"requirement": "biz_requirement", "case": "biz_test_case", "bug": "biz_bug"}[singular]
    return conn.execute(
        f"SELECT 1 FROM {table} WHERE project_id=? AND external_platform=? AND external_id=?"
        " AND is_deleted=0 LIMIT 1",
        (project_id, platform, external_id),
    ).fetchone() is not None


def _insert(
    conn: Any, singular: str, project_id: int, platform: str, external_id: str,
    external_url: str, mapped: dict[str, Any],
) -> None:
    now = utcnow_iso()
    code = f"{PLATFORM_CODE_PREFIX[platform]}-{external_id}"[:64]
    if singular == "requirement":
        conn.execute(
            "INSERT INTO biz_requirement (project_id, req_no, title, content, source, status,"
            " priority, tags, external_platform, external_id, external_url,"
            " created_at, updated_at, is_deleted)"
            " VALUES (?, ?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                project_id, code, mapped["title"], str(mapped.get("content") or ""), platform,
                str(mapped.get("priority") or "P2"), f"{platform}:{external_id}",
                platform, external_id, external_url, now, now,
            ),
        )
        return
    if singular == "case":
        conn.execute(
            "INSERT INTO biz_test_case (project_id, case_no, title, case_type, priority,"
            " status, preconditions, steps_json, expected_result, tags, source,"
            " external_platform, external_id, external_url, created_at, updated_at, is_deleted)"
            " VALUES (?, ?, ?, 'functional', ?, 'draft', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)",
            (
                project_id, code, mapped["title"], str(mapped.get("priority") or "P2"),
                str(mapped.get("preconditions") or ""),
                json.dumps(mapped.get("steps") or [], ensure_ascii=False),
                str(mapped.get("expectedResult") or ""), f"{platform}:{external_id}",
                platform, platform, external_id, external_url, now, now,
            ),
        )
        return
    conn.execute(
        "INSERT INTO biz_bug (project_id, bug_no, title, severity, status, priority, bug_type,"
        " description, external_platform, external_id, external_url,"
        " created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 'open', ?, 'functional', ?, ?, ?, ?, ?, ?, 0)",
        (
            project_id, code, mapped["title"], str(mapped.get("severity") or "P2"),
            str(mapped.get("priority") or "P2"), str(mapped.get("description") or ""),
            platform, external_id, external_url, now, now,
        ),
    )


def list_batches(conn: Any, project_id: int) -> list[dict[str, Any]]:
    return ExternalImportBatchRepository(conn).list_for(project_id)


def batch_detail(conn: Any, batch_id: int, *, project_id: int) -> dict[str, Any]:
    row = ExternalImportBatchRepository(conn).get(batch_id)
    if not row or int(row.get("project_id") or 0) != project_id:
        raise _fail("导入批次不存在", ErrorCode.RESOURCE_NOT_FOUND)
    return batch_public(row)
