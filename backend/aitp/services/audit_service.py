"""关键写操作审计。

审计表只记录「谁在什么时候改了什么」，不记录请求体全文，避免把敏感内容写进审计日志。
"""

from __future__ import annotations

import json
import sqlite3
from typing import Any, cast

from ..common.time_utils import utcnow_iso

# ---- 审计动作 ----
AUDIT_ACTION_CREATE = "create"
AUDIT_ACTION_UPDATE = "update"
AUDIT_ACTION_DELETE = "delete"
AUDIT_ACTION_REVIEW = "review"
AUDIT_ACTION_REVOKE = "revoke"
AUDIT_ACTION_TRANSITION = "transition"
AUDIT_ACTION_IMPORT = "import"
AUDIT_ACTION_EXPORT = "export"
AUDIT_ACTION_GENERATE = "generate"
AUDIT_ACTION_CONFIG = "config"
AUDIT_ACTION_BACKUP = "backup"
AUDIT_ACTION_RESTORE = "restore"
AUDIT_ACTION_EXECUTE = "execute"

# ---- 资源类型 ----
RESOURCE_AI_PROFILE = "ai_profile"
RESOURCE_AI_PROMPT = "ai_prompt"
RESOURCE_AI_TASK = "ai_task"
RESOURCE_AI_RECORD = "ai_record"
RESOURCE_MODULE = "module"
RESOURCE_MODULE_CAPABILITY = "module_capability"
RESOURCE_KNOWLEDGE_ARTICLE = "knowledge_article"
RESOURCE_KNOWLEDGE_DRAFT = "knowledge_draft"
RESOURCE_KNOWLEDGE_VERSION = "knowledge_version"
RESOURCE_TEST_CASE = "test_case"
RESOURCE_BUG = "bug"
RESOURCE_REPORT = "report"
RESOURCE_PROJECT = "project"
RESOURCE_REQUIREMENT = "requirement"
RESOURCE_SYSTEM_CONFIG = "system_config"
RESOURCE_DB_BACKUP = "db_backup"
RESOURCE_WORKBENCH_ENTRY = "workbench_entry"
RESOURCE_VALIDATION_CASE = "validation_case"
RESOURCE_LAB_ATTACHMENT = "lab_attachment"
RESOURCE_REGRESSION = "regression"
RESOURCE_ENTITY_RELATION = "entity_relation"
RESOURCE_TODO = "todo"
RESOURCE_AUTOMATION_SCRIPT = "automation_script"
RESOURCE_AUTOMATION_RUN = "automation_run"
RESOURCE_DOC_TEMPLATE = "doc_template"

_MAX_DETAIL_CHARS = 2000


def _current_context() -> tuple[str, str, str]:
    """从 Flask 请求上下文取 traceId / 客户端 IP / 操作人。"""
    try:
        from flask import g, has_request_context, request

        if not has_request_context():
            return "local", "", ""
        trace_id = str(getattr(g, "trace_id", "") or "")
        client_ip = request.headers.get("X-Forwarded-For", "") or request.remote_addr or ""
        actor = request.headers.get("X-AITP-Actor", "") or "local"
        return actor, trace_id, client_ip
    except Exception:
        return "local", "", ""


def record(
    conn: sqlite3.Connection,
    action: str,
    resource_type: str,
    resource_id: int | None = None,
    resource_label: str = "",
    detail: dict[str, Any] | None = None,
    *,
    actor: str = "",
    trace_id: str = "",
    client_ip: str = "",
    result: str = "success",
) -> int:
    """写一条审计记录，返回自增 id。"""
    ctx_actor, ctx_trace, ctx_ip = _current_context()
    payload = json.dumps(detail, ensure_ascii=False) if detail else None
    if payload and len(payload) > _MAX_DETAIL_CHARS:
        payload = payload[:_MAX_DETAIL_CHARS] + "...(truncated)"

    cursor = conn.execute(
        "INSERT INTO sys_audit_log (actor, action, resource_type, resource_id,"
        " resource_label, detail_json, result, trace_id, client_ip, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            actor or ctx_actor or "local",
            action,
            resource_type,
            resource_id,
            resource_label or "",
            payload,
            result,
            trace_id or ctx_trace,
            client_ip or ctx_ip,
            utcnow_iso(),
        ),
    )
    return int(cast(int, cursor.lastrowid))
