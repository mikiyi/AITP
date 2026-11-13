"""统一执行协议与证据快照（GAP-32）。

一次执行到底留下了什么，过去有三套字段：接口执行看
``biz_interface_execution``、自动化看 ``biz_automation_run``、手工看
``biz_case_execution``（只有结论文本，零原始证据）。本模块把它们拉到同一个口径：

  - ``ExecutionResult``：唯一的执行结果结构（来源 / 用例 / 轮次 / 环境 / 结论 /
    耗时 / 断言明细 / 请求响应摘要 / 日志截图 / traceId / 错误 / 执行者 / 时间）；
    蛇形→驼峰只在本文件的 ``ExecutionResult.as_dict`` 一处转换；
  - adapters：``result_from_*`` 把三张既有表**只读**映射成该结构，
    不改一期任何写入链路（一期写回链路已稳定，回归面控制在读取与聚合侧）；
  - ``capture``：一次执行往 ``biz_execution_evidence`` 落一份快照
    （同来源同行 id 重复写入时覆盖，保持「一次执行一份」）；
    大字段只存摘要并靠 ``source_ref_json`` 指回原表，避免整库膨胀；
  - ``evidence_of`` / ``latest_evidence_for_*``：轮次详情、缺陷详情、报告章节
    三处按同一条目取同一份证据（同一个 evidence id）；
  - 保留策略：随轮次保留，全局 + 项目级保留期可配，过期只软删并留审计。

安全边界：响应体/日志只保留摘要（上限见 ``MAX_SUMMARY_CHARS``），
请求头里的敏感字段沿用 interface_runner 的脱敏结果，不在这里二次解密。
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, cast

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.base import BaseRepository
from . import attachment_service


class EvidenceRepository(BaseRepository):
    """``biz_execution_evidence`` 的读写口。

    证据只在执行链路里写（capture），不提供通用 CRUD；这里只承担 insert / update
    与「按来源取当前证据」的定位查询，其余聚合仍走本模块的只读函数。
    """

    table = "biz_execution_evidence"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "source_type", "case_id", "run_id", "run_case_id", "created_at"}
    searchable: list[str] = ["title", "error_message", "trace_id"]

#: 统一执行来源；门禁与前端都按这套值判定
SOURCE_INTERFACE = "interface"
SOURCE_AUTOMATION = "automation"
SOURCE_MANUAL = "manual"
SOURCE_REGRESSION = "regression"
SOURCE_EXPLORATORY = "exploratory"

SOURCE_TYPES = (SOURCE_INTERFACE, SOURCE_AUTOMATION, SOURCE_MANUAL, SOURCE_REGRESSION, SOURCE_EXPLORATORY)

SOURCE_LABELS = {
    SOURCE_INTERFACE: "接口执行",
    SOURCE_AUTOMATION: "自动化执行",
    SOURCE_MANUAL: "手工执行",
    SOURCE_REGRESSION: "回归执行",
    SOURCE_EXPLORATORY: "探索性测试",
}

RESULT_LABELS = {
    "passed": "通过",
    "failed": "失败",
    "blocked": "阻塞",
    "skipped": "跳过",
    "not_executed": "未执行",
}

#: 证据里的单段摘要上限（请求 / 响应 / 日志各一段）
MAX_SUMMARY_CHARS = 4000
#: 附件引用最多带几条，避免一次执行挂上百张截图
MAX_ATTACHMENTS = 20
#: 保留期上下限（天）：0=随轮次永久保留
RETENTION_CONFIG_KEY = "execution.evidence_retention_days"
DEFAULT_RETENTION_DAYS = 180
MAX_RETENTION_DAYS = 3650


@dataclass(slots=True)
class ExecutionResult:
    """一次执行的统一结果结构（四类来源共用）。"""

    source_type: str
    source_row_id: int
    project_id: int | None = None
    case_id: int | None = None
    run_id: int | None = None
    run_case_id: int | None = None
    regression_id: int | None = None
    regression_item_id: int | None = None
    bug_id: int | None = None
    env_id: int | None = None
    env_name: str = ""
    title: str = ""
    result: str = "not_executed"
    duration_ms: int = 0
    assertions: list[dict[str, Any]] = field(default_factory=list)
    request_summary: str = ""
    response_summary: str = ""
    log_summary: str = ""
    attachments: list[dict[str, Any]] = field(default_factory=list)
    source_ref: dict[str, Any] = field(default_factory=dict)
    trace_id: str = ""
    error_message: str = ""
    executed_by: str = ""
    executed_at: str = ""
    evidence_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """唯一的蛇形→驼峰出口：接口、门禁、前端都只看这里。"""
        return {
            "sourceType": self.source_type,
            "sourceTypeLabel": SOURCE_LABELS.get(self.source_type, self.source_type),
            "sourceRowId": self.source_row_id,
            "evidenceId": self.evidence_id,
            "projectId": self.project_id,
            "caseId": self.case_id,
            "runId": self.run_id,
            "runCaseId": self.run_case_id,
            "regressionId": self.regression_id,
            "regressionItemId": self.regression_item_id,
            "bugId": self.bug_id,
            "envId": self.env_id,
            "envName": self.env_name,
            "title": self.title,
            "result": self.result,
            "resultLabel": RESULT_LABELS.get(self.result, self.result),
            "durationMs": self.duration_ms,
            "assertions": list(self.assertions),
            "assertionSummary": assertion_summary(self.assertions),
            "requestSummary": self.request_summary,
            "responseSummary": self.response_summary,
            "logSummary": self.log_summary,
            "attachments": list(self.attachments),
            "sourceRef": dict(self.source_ref),
            "traceId": self.trace_id,
            "errorMessage": self.error_message,
            "executedBy": self.executed_by,
            "executedAt": self.executed_at,
        }

    def failed_assertions(self) -> list[dict[str, Any]]:
        return [item for item in self.assertions if not item.get("passed")]


# --------------------------------------------------------------------- 工具


def summarize(value: Any, limit: int = MAX_SUMMARY_CHARS) -> str:
    """任意值压成一段有界摘要；超大内容截断并标注，绝不整段长期存库。"""
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        text = json.dumps(value, ensure_ascii=False, default=str)
    else:
        text = str(value)
    text = text.strip()
    if len(text) <= limit:
        return text
    return text[:limit] + f"…（已截断，原文 {len(text)} 字）"


def assertion_summary(assertions: list[dict[str, Any]]) -> dict[str, int]:
    total = len(assertions)
    passed = sum(1 for item in assertions if item.get("passed"))
    return {"total": total, "passed": passed, "failed": total - passed}


def request_summary_of(detail: dict[str, Any]) -> str:
    """请求摘要的唯一口径（GAP-32）：接口与回归自动跑共用一份渲染。"""
    return summarize(_dict_of(detail.get("request")))


def response_summary_of(detail: dict[str, Any]) -> str:
    """响应摘要的唯一口径：状态码优先取顶层，回落 response.status。"""
    response = _dict_of(detail.get("response"))
    status = detail.get("statusCode")
    if status is None:
        status = response.get("status")
    return summarize({
        "statusCode": status,
        "headers": _dict_of(response.get("headers")),
        "body": response.get("body", ""),
        "bodyTruncated": response.get("bodyTruncated", False),
    })


def attachments_root(conn: sqlite3.Connection) -> Path:
    """附件目录与库文件同层（与 lab 附件接口一致：``<库所在目录>/attachments``）。"""
    row = conn.execute("SELECT file FROM pragma_database_list WHERE name = 'main'").fetchone()
    db_file = str(row[0] or "") if row else ""
    if not db_file:
        return Path("data") / "attachments"
    return Path(db_file).parent / "attachments"


def resolve_attachments(
    conn: sqlite3.Connection, attachment_ids: list[str], *, label: str = "证据附件"
) -> list[dict[str, Any]]:
    """把附件编号换成可长期引用的证据附件条目；无效编号直接给中文可执行提示。"""
    root = attachments_root(conn)
    resolved: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw in attachment_ids[:MAX_ATTACHMENTS]:
        attachment_id = str(raw or "").strip()
        if not attachment_id:
            continue
        meta = attachment_service.load_meta(root, attachment_id)
        if not meta:
            missing.append(attachment_id)
            continue
        resolved.append({
            "attachmentId": attachment_id,
            "fileName": str(meta.get("fileName") or ""),
            "kind": str(meta.get("kind") or ""),
            "mediaType": str(meta.get("mediaType") or ""),
            "suffix": str(meta.get("suffix") or ""),
            "previewUrl": f"/api/v1/lab/attachments/{attachment_id}",
            "note": str(meta.get("note") or ""),
        })
    if missing:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"{label}不存在或已过期：{', '.join(missing[:5])}；请重新上传后再提交",
        )
    return resolved


# --------------------------------------------------------------------- 保留策略


def _int_config(conn: sqlite3.Connection, key: str) -> int | None:
    row = conn.execute(
        "SELECT config_value FROM sys_config WHERE config_key = ?", (key,)
    ).fetchone()
    if row is None:
        return None
    text = str(row[0] or "").strip()
    if not text:
        return None
    try:
        return max(0, min(MAX_RETENTION_DAYS, int(text)))
    except ValueError:
        return None


def retention_days(conn: sqlite3.Connection, project_id: int | None = None) -> int:
    """项目级覆盖优先，其次全局，最后内置默认；0=永久保留。"""
    if project_id:
        scoped = _int_config(conn, f"{RETENTION_CONFIG_KEY}:{int(project_id)}")
        if scoped is not None:
            return scoped
    default = _int_config(conn, RETENTION_CONFIG_KEY)
    return default if default is not None else DEFAULT_RETENTION_DAYS


def set_retention_days(
    conn: sqlite3.Connection, days: int, project_id: int | None = None
) -> int:
    from ..repositories.sys_config_repo import SysConfigRepository

    value = max(0, min(MAX_RETENTION_DAYS, int(days)))
    repo = SysConfigRepository(conn)
    key = RETENTION_CONFIG_KEY if not project_id else f"{RETENTION_CONFIG_KEY}:{int(project_id)}"
    description = (
        "执行证据快照默认保留天数；0=随轮次永久保留。项目级覆盖键 "
        "execution.evidence_retention_days:<项目id>。"
        if not project_id
        else f"项目 {int(project_id)} 的执行证据保留天数；0=永久保留。"
    )
    repo.set_value(key, str(value), "int", "global", description, 1)
    return value


def expires_at(days: int, started: str | None = None) -> str:
    """保留到期时间（ISO8601）；days<=0 表示永久，返回空串。"""
    if days <= 0:
        return ""
    base = _parse_iso(started) if started else None
    if base is None:
        base = datetime.now(timezone.utc)
    return (base + timedelta(days=int(days))).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse_iso(value: str) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _is_expired(row: sqlite3.Row) -> bool:
    moment = _parse_iso(str(row["expires_at"] or ""))
    return moment is not None and moment <= _now()


# --------------------------------------------------------------------- 落库


def capture(
    conn: sqlite3.Connection,
    result: ExecutionResult,
    *,
    capture_id: int | None = None,
    executed_at: str | None = None,
) -> int:
    """落一份证据快照，返回 evidence id。

    同 ``source_type + source_row_id`` 视为同一次执行：重跑覆盖旧快照，
    保证「一条回归点 / 一条轮次用例」任何时候只有一份当前证据。
    """
    if result.source_type not in SOURCE_TYPES:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"未知执行来源：{result.source_type}，只支持 {'/'.join(SOURCE_TYPES)}",
        )
    executed = executed_at or result.executed_at or utcnow_iso()
    days = retention_days(conn, result.project_id)
    expire = expires_at(days, executed)
    payload = {
        "source_type": result.source_type,
        "source_row_id": int(result.source_row_id or 0),
        "project_id": result.project_id,
        "case_id": result.case_id,
        "run_id": result.run_id,
        "run_case_id": result.run_case_id,
        "regression_id": result.regression_id,
        "regression_item_id": result.regression_item_id,
        "bug_id": result.bug_id,
        "env_id": result.env_id,
        "env_name": (result.env_name or "")[:200],
        "title": (result.title or "")[:500],
        "result": result.result or "not_executed",
        "duration_ms": int(result.duration_ms or 0),
        "assertions_json": _dump(result.assertions),
        "request_summary": summarize(result.request_summary),
        "response_summary": summarize(result.response_summary),
        "log_summary": summarize(result.log_summary),
        "attachments_json": _dump(result.attachments),
        "source_ref_json": _dump(result.source_ref),
        "trace_id": (result.trace_id or "")[:64],
        "error_message": (result.error_message or "")[:2000],
        "executed_by": (result.executed_by or "")[:64],
        "executed_at": executed,
        "expires_at": expire,
        "purge_reason": "",
    }
    repo = EvidenceRepository(conn)
    existing: int | None = capture_id
    if existing is None:
        row = conn.execute(
            "SELECT id FROM biz_execution_evidence"
            " WHERE source_type = ? AND source_row_id = ? AND is_deleted = 0"
            " ORDER BY id DESC LIMIT 1",
            (payload["source_type"], payload["source_row_id"]),
        ).fetchone()
        existing = int(row["id"]) if row else None
    if existing:
        repo.update(existing, payload)
        result.evidence_id = int(existing)
        return int(existing)
    new_id = repo.insert(payload)
    result.evidence_id = int(new_id)
    return int(new_id)


def _dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def trace_id_now() -> str:
    """当前请求的 traceId；脚本、后台线程这些没有 Flask 上下文的地方返回空串。"""
    try:
        from flask import g

        return str(getattr(g, "trace_id", "") or "")[:64]
    except Exception:  # noqa: BLE001 - 上下文外读 g 会抛 RuntimeError
        return ""


# --------------------------------------------------------------------- 读取


def _row_to_result(row: sqlite3.Row) -> ExecutionResult:
    def _json(column: str, default: Any) -> Any:
        raw = row[column]
        if not raw:
            return default
        try:
            return json.loads(str(raw))
        except (TypeError, ValueError):
            return default

    return ExecutionResult(
        source_type=str(row["source_type"]),
        source_row_id=int(row["source_row_id"] or 0),
        project_id=_opt_int(row["project_id"]),
        case_id=_opt_int(row["case_id"]),
        run_id=_opt_int(row["run_id"]),
        run_case_id=_opt_int(row["run_case_id"]),
        regression_id=_opt_int(row["regression_id"]),
        regression_item_id=_opt_int(row["regression_item_id"]),
        bug_id=_opt_int(row["bug_id"]),
        env_id=_opt_int(row["env_id"]),
        env_name=str(row["env_name"] or ""),
        title=str(row["title"] or ""),
        result=str(row["result"] or "not_executed"),
        duration_ms=int(row["duration_ms"] or 0),
        assertions=_list_of_dicts(_json("assertions_json", [])),
        request_summary=str(row["request_summary"] or ""),
        response_summary=str(row["response_summary"] or ""),
        log_summary=str(row["log_summary"] or ""),
        attachments=_list_of_dicts(_json("attachments_json", [])),
        source_ref=_dict_of(_json("source_ref_json", {})),
        trace_id=str(row["trace_id"] or ""),
        error_message=str(row["error_message"] or ""),
        executed_by=str(row["executed_by"] or ""),
        executed_at=str(row["executed_at"] or ""),
        evidence_id=int(row["id"]),
    )


def _opt_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _list_of_dicts(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _dict_of(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _visible(row: sqlite3.Row) -> bool:
    """过期即不可见：保留到期后证据不再返回（清理任务再软删落审计）。"""
    return row["is_deleted"] == 0 and not _is_expired(row)


def evidence_row(conn: sqlite3.Connection, evidence_id: int) -> sqlite3.Row | None:
    # fetchone() 在 typeshed 里是 Any，这里显式收窄成 Row | None，不靠 type: ignore
    return cast("sqlite3.Row | None", conn.execute(
        "SELECT * FROM biz_execution_evidence WHERE id = ?", (int(evidence_id),)
    ).fetchone())


def get_evidence(conn: sqlite3.Connection, evidence_id: int) -> dict[str, Any] | None:
    """按证据 id 取统一结果结构；已过期或已软删时返回 None。"""
    row = evidence_row(conn, evidence_id)
    if row is None or not _visible(row):
        return None
    return _row_to_result(row).as_dict()


def latest_evidence_for_run_case(
    conn: sqlite3.Connection, run_case_id: int
) -> dict[str, Any] | None:
    """轮次用例的当前证据：先取轮次级快照，没有再回落到该用例最近一次执行的快照。

    手工执行只留用例级快照（run_case_id 为空），报告失败样本、转缺陷、下钻三处必须
    按同一条口径判断「有没有证据」，否则报告说有证据、详情却查不到。
    """
    row = conn.execute(
        "SELECT * FROM biz_execution_evidence"
        " WHERE run_case_id = ? AND is_deleted = 0 AND (expires_at = '' OR expires_at > ?)"
        " ORDER BY id DESC LIMIT 1",
        (int(run_case_id), _now_text()),
    ).fetchone()
    if row:
        return _row_to_result(row).as_dict()
    case_row = conn.execute(
        "SELECT case_id FROM biz_test_run_case WHERE id = ? AND is_deleted = 0",
        (int(run_case_id),),
    ).fetchone()
    if not case_row or case_row["case_id"] in (None, ""):
        return None
    return latest_evidence_for_case(conn, int(case_row["case_id"]))


def latest_evidence_for_case(conn: sqlite3.Connection, case_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM biz_execution_evidence"
        " WHERE case_id = ? AND is_deleted = 0 AND (expires_at = '' OR expires_at > ?)"
        " ORDER BY id DESC LIMIT 1",
        (int(case_id), _now_text()),
    ).fetchone()
    return _row_to_result(row).as_dict() if row else None


def latest_evidence_for_item(
    conn: sqlite3.Connection, regression_item_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM biz_execution_evidence"
        " WHERE regression_item_id = ? AND is_deleted = 0"
        " AND (expires_at = '' OR expires_at > ?) ORDER BY id DESC LIMIT 1",
        (int(regression_item_id), _now_text()),
    ).fetchone()
    return _row_to_result(row).as_dict() if row else None


def evidence_for_bug(conn: sqlite3.Connection, bug_id: int) -> list[dict[str, Any]]:
    """缺陷可见的证据：直接挂了 bug_id 的，加上该缺陷来源轮次用例的证据。"""
    rows = conn.execute(
        "SELECT * FROM biz_execution_evidence WHERE ("
        " bug_id = ? OR run_case_id IN ("
        "   SELECT id FROM biz_test_run_case WHERE bug_id = ? AND is_deleted = 0)"
        " ) AND is_deleted = 0 AND (expires_at = '' OR expires_at > ?)"
        " ORDER BY id DESC LIMIT 20",
        (int(bug_id), int(bug_id), _now_text()),
    ).fetchall()
    if rows:
        return [_row_to_result(row).as_dict() for row in rows]
    # 来源用例只有用例级快照（手工执行）时缺陷侧也要查得到，口径与轮次用例下钻一致
    case_row = conn.execute(
        "SELECT case_id FROM biz_test_run_case"
        " WHERE bug_id = ? AND is_deleted = 0 ORDER BY id DESC LIMIT 1",
        (int(bug_id),),
    ).fetchone()
    if not case_row or case_row["case_id"] in (None, ""):
        return []
    evidence = latest_evidence_for_case(conn, int(case_row["case_id"]))
    return [evidence] if evidence else []


def evidence_for_run(conn: sqlite3.Connection, run_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT * FROM biz_execution_evidence WHERE run_id = ? AND is_deleted = 0"
        " AND (expires_at = '' OR expires_at > ?) ORDER BY id DESC LIMIT 200",
        (int(run_id), _now_text()),
    ).fetchall()
    return [_row_to_result(row).as_dict() for row in rows]


def link_evidence_to_bug(
    conn: sqlite3.Connection,
    bug_id: int,
    *,
    run_case_id: int | None = None,
    case_id: int | None = None,
    run_id: int | None = None,
) -> list[int]:
    """GAP-35：失败转缺陷时把本次执行的证据挂到缺陷上（只补 bug_id，不改结论）。

    同一条证据已经挂着别的缺陷 id 时不抢：一次失败只对应第一次转出的缺陷。
    """
    if run_case_id is None and case_id is None:
        return []
    clauses: list[str] = []
    params: list[Any] = []
    if run_case_id is not None:
        clauses.append("run_case_id = ?")
        params.append(int(run_case_id))
    if case_id is not None and run_id is not None:
        clauses.append("(case_id = ? AND (run_id = ? OR run_id IS NULL))")
        params.extend([int(case_id), int(run_id)])
    elif case_id is not None:
        clauses.append("(case_id = ? AND run_id IS NULL)")
        params.append(int(case_id))
    placeholders = " OR ".join(clauses)
    rows = conn.execute(
        f"SELECT id FROM biz_execution_evidence WHERE ({placeholders})"
        " AND is_deleted = 0 AND (bug_id IS NULL OR bug_id = ?) ORDER BY id DESC LIMIT 20",
        [*params, int(bug_id)],
    ).fetchall()
    ids = [int(row["id"]) for row in rows]
    if ids:
        marks = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE biz_execution_evidence SET bug_id = ?, updated_at = ? WHERE id IN ({marks})",
            [int(bug_id), utcnow_iso(), *ids],
        )
    return ids


def describe_evidence(result: dict[str, Any], *, limit: int = 4000) -> str:
    """把一份证据压成中文摘要块，缺陷描述与报告章节共用同一段口径。"""
    assertions = result.get("assertions") or []
    failed = [item for item in assertions if isinstance(item, dict) and not item.get("passed")]
    lines = [
        "——— 执行证据（自动生成，可复盘） ———",
        f"证据编号：EV-{result.get('evidenceId') or 0}"
        f"（{result.get('sourceTypeLabel') or result.get('sourceType')} /"
        f" 结论 {result.get('resultLabel') or result.get('result')} /"
        f" 耗时 {result.get('durationMs') or 0}ms）",
    ]
    if result.get("traceId"):
        lines.append(f"traceId：{result['traceId']}")
    if result.get("requestSummary"):
        lines.append(f"请求：{summarize(result['requestSummary'], 800)}")
    if result.get("responseSummary"):
        lines.append(f"响应：{summarize(result['responseSummary'], 800)}")
    if result.get("logSummary"):
        lines.append(f"日志：{summarize(result['logSummary'], 800)}")
    summary = result.get("assertionSummary") or {}
    if assertions:
        lines.append(
            f"断言：{summary.get('total') or len(assertions)} 项，"
            f"通过 {summary.get('passed') or 0} 项，失败 {summary.get('failed') or 0} 项"
        )
        for item in failed[:5]:
            lines.append(
                f"- 失败断言 {item.get('name')}：期望 {item.get('expected')}"
                f"，实际 {item.get('actual')}；{item.get('message')}"
            )
    for item in (result.get("attachments") or [])[:5]:
        if isinstance(item, dict):
            lines.append(
                f"附件：{item.get('fileName') or item.get('attachmentId')}"
                f" → {item.get('previewUrl') or ''}"
            )
    if result.get("errorMessage"):
        lines.append(f"错误信息：{str(result['errorMessage'])[:500]}")
    lines.append(
        "下钻：GET /api/v1/test-runs/evidence/"
        f"{result.get('sourceType')}/{result.get('sourceRowId')}"
    )
    return summarize("\n".join(lines), limit)


def _now_text() -> str:
    return _now().strftime("%Y-%m-%dT%H:%M:%SZ")


# --------------------------------------------------------------------- 四类来源定位


def by_source(conn: sqlite3.Connection, source_type: str, source_row_id: int) -> dict[str, Any] | None:
    """按「来源类型 + 原表行 id」取当前证据；没有快照时回退到只读 adapter。"""
    if source_type not in SOURCE_TYPES:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"未知执行来源：{source_type}，只支持 {'/'.join(SOURCE_TYPES)}",
        )
    row = conn.execute(
        "SELECT * FROM biz_execution_evidence WHERE source_type = ? AND source_row_id = ?"
        " AND is_deleted = 0 ORDER BY id DESC LIMIT 1",
        (source_type, int(source_row_id)),
    ).fetchone()
    if row is not None:
        if not _visible(row):
            return None
        return _row_to_result(row).as_dict()
    adapter = _ADAPTERS[source_type]
    result = adapter(conn, int(source_row_id))
    return result.as_dict() if result else None


# --------------------------------------------------------------------- 只读 adapter


def result_from_interface_execution(
    conn: sqlite3.Connection, execution_id: int
) -> ExecutionResult | None:
    row = conn.execute(
        "SELECT * FROM biz_interface_execution WHERE id = ?", (int(execution_id),)
    ).fetchone()
    if row is None:
        return None
    def _json(column: str) -> Any:
        try:
            return json.loads(str(row[column] or "") or "{}")
        except (TypeError, ValueError):
            return {}

    request = _json("request_json")
    response = _json("response_json")
    assertions = _json("assertions_json")
    case_no = ""
    project_id: int | None = None
    case_row = conn.execute(
        "SELECT case_no, title, project_id FROM biz_test_case WHERE id = ?",
        (row["case_id"],),
    ).fetchone()
    if case_row is not None:
        case_no = str(case_row["case_no"] or "")
        project_id = _opt_int(case_row["project_id"])
    env_name = ""
    env_row = conn.execute(
        "SELECT name FROM biz_test_env WHERE id = ?", (row["env_id"],)
    ).fetchone()
    if env_row is not None:
        env_name = str(env_row["name"] or "")
    return ExecutionResult(
        source_type=SOURCE_INTERFACE,
        source_row_id=int(row["id"]),
        project_id=project_id,
        case_id=_opt_int(row["case_id"]),
        run_id=_opt_int(row["run_id"]),
        run_case_id=_opt_int(row["run_case_id"]),
        env_id=_opt_int(row["env_id"]),
        env_name=env_name,
        title=f"{case_no} {str(case_row['title'] or '') if case_row is not None else ''}".strip(),
        result=str(row["result"] or "not_executed"),
        duration_ms=int(row["duration_ms"] or 0),
        assertions=_list_of_dicts(assertions),
        request_summary=summarize(request),
        response_summary=summarize({
            "statusCode": row["status_code"],
            "body": _dict_of(response).get("body", ""),
            "bodyTruncated": _dict_of(response).get("bodyTruncated", False),
        }),
        log_summary="",
        source_ref={"table": "biz_interface_execution", "id": int(row["id"]),
                    "method": str(row["method"] or ""), "url": str(row["url"] or "")},
        error_message=str(row["error_message"] or ""),
        executed_by=str(row["executed_by"] or ""),
        executed_at=str(row["created_at"] or ""),
    )


def result_from_automation_run(conn: sqlite3.Connection, run_id: int) -> ExecutionResult | None:
    row = conn.execute("SELECT * FROM biz_automation_run WHERE id = ?", (int(run_id),)).fetchone()
    if row is None:
        return None
    status = str(row["status"] or "")
    result = "passed" if status == "success" else (
        "not_executed" if status == "running" else "failed"
    )
    return ExecutionResult(
        source_type=SOURCE_AUTOMATION,
        source_row_id=int(row["id"]),
        run_id=_opt_int(row["test_run_id"]),
        env_id=_opt_int(row["env_id"]),
        title=f"{str(row['script_no'] or '')} {str(row['script_name'] or '')}".strip(),
        result=result,
        duration_ms=int(row["duration_ms"] or 0),
        assertions=[],
        request_summary=summarize({"commandLine": str(row["command_line"] or "")}),
        response_summary="",
        log_summary=summarize(
            f"exitCode={row['exit_code']}\n--- stdout ---\n{row['stdout'] or ''}"
            f"\n--- stderr ---\n{row['stderr'] or ''}"
        ),
        source_ref={
            "table": "biz_automation_run",
            "id": int(row["id"]),
            "runNo": str(row["run_no"] or ""),
            "scriptId": _opt_int(row["script_id"]),
            "status": status,
            "writebackStatus": str(row["writeback_status"] or ""),
        },
        error_message=str(row["error_message"] or ""),
        executed_at=str(row["started_at"] or ""),
    )


def result_from_case_execution(
    conn: sqlite3.Connection, execution_id: int
) -> ExecutionResult | None:
    row = conn.execute("SELECT * FROM biz_case_execution WHERE id = ?", (int(execution_id),)).fetchone()
    if row is None:
        return None
    case_no = ""
    project_id: int | None = None
    case_row = conn.execute(
        "SELECT case_no, title, project_id FROM biz_test_case WHERE id = ?",
        (row["case_id"],),
    ).fetchone()
    if case_row is not None:
        case_no = str(case_row["case_no"] or "")
        project_id = _opt_int(case_row["project_id"])
    return ExecutionResult(
        source_type=SOURCE_MANUAL,
        source_row_id=int(row["id"]),
        project_id=project_id,
        case_id=_opt_int(row["case_id"]),
        run_id=_opt_int(row["run_id"]),
        run_case_id=_opt_int(row["run_case_id"]),
        bug_id=_opt_int(row["bug_id"]),
        env_name=str(row["environment"] or ""),
        title=f"{case_no} {str(case_row['title'] or '') if case_row is not None else ''}".strip(),
        result=_normalize_result(str(row["result"] or "not_executed")),
        duration_ms=int(row["duration_ms"] or 0),
        log_summary=summarize(str(row["remark"] or "")),
        source_ref={"table": "biz_case_execution", "id": int(row["id"]),
                    "version": str(row["version"] or "")},
        executed_by=str(row["executed_by"] or ""),
        executed_at=str(row["executed_at"] or ""),
    )


def result_from_regression_item(
    conn: sqlite3.Connection, item_id: int
) -> ExecutionResult | None:
    row = conn.execute(
        """
        SELECT i.*, r.project_id AS reg_project_id, r.title AS reg_title, r.id AS reg_id
        FROM biz_regression_item i
        JOIN biz_regression r ON r.id = i.regression_id
        WHERE i.id = ?
        """,
        (int(item_id),),
    ).fetchone()
    if row is None:
        return None
    return ExecutionResult(
        source_type=SOURCE_REGRESSION,
        source_row_id=int(row["id"]),
        project_id=_opt_int(row["reg_project_id"]),
        regression_id=_opt_int(row["reg_id"]),
        regression_item_id=int(row["id"]),
        case_id=_opt_int(row["case_id"]),
        env_id=_opt_int(row["env_id"]),
        title=f"{str(row['reg_title'] or '')} / {str(row['title'] or '')}".strip(" /"),
        result=str(row["result"] or "not_executed"),
        log_summary=summarize(str(row["remark"] or "")),
        source_ref={
            "table": "biz_regression_item",
            "id": int(row["id"]),
            "execMode": str(row["exec_mode"] or "manual"),
            "verifyTarget": str(row["verify_target"] or ""),
        },
        executed_by=str(row["executed_by"] or ""),
        executed_at=str(row["executed_at"] or ""),
    )


def result_from_exploration_step(
    conn: sqlite3.Connection, step_id: int
) -> ExecutionResult | None:
    """探索性测试异常步的只读兜底：正常路径证据已由 capture 落快照。"""
    row = conn.execute(
        """
        SELECT s.*, sess.project_id AS sess_project_id, sess.name AS sess_name,
               sess.target_url AS sess_target_url
        FROM biz_exploration_step s
        JOIN biz_exploration_session sess ON sess.id = s.session_id
        WHERE s.id = ?
        """,
        (int(step_id),),
    ).fetchone()
    if row is None:
        return None
    try:
        detail = _dict_of(json.loads(str(row["detail_json"] or "{}")))
    except (TypeError, ValueError):
        detail = {}
    attachments = detail.get("screenshots") or []
    return ExecutionResult(
        source_type=SOURCE_EXPLORATORY,
        source_row_id=int(row["id"]),
        project_id=_opt_int(row["sess_project_id"]),
        title=f"{str(row['sess_name'] or '')} / 第{int(row['step_no'] or 0)}步".strip(" /"),
        result="failed" if int(row["is_anomaly"] or 0) else "passed",
        duration_ms=int(row["duration_ms"] or 0),
        log_summary=summarize(str(row["observation"] or "")),
        attachments=_list_of_dicts(attachments),
        source_ref={
            "table": "biz_exploration_step",
            "id": int(row["id"]),
            "sessionId": int(row["session_id"]),
            "targetUrl": str(row["sess_target_url"] or ""),
            "anomalyType": str(row["anomaly_type"] or ""),
        },
        trace_id=str(row["trace_id"] or ""),
        executed_at=str(row["detected_at"] or row["created_at"] or ""),
    )


_ADAPTERS: dict[str, Callable[[sqlite3.Connection, int], ExecutionResult | None]] = {
    SOURCE_INTERFACE: result_from_interface_execution,
    SOURCE_AUTOMATION: result_from_automation_run,
    SOURCE_MANUAL: result_from_case_execution,
    SOURCE_REGRESSION: result_from_regression_item,
    SOURCE_EXPLORATORY: result_from_exploration_step,
}


def _normalize_result(value: str) -> str:
    """执行流水的用例状态（pass/fail/blocked/not_executed）→ 统一结论口径。"""
    mapping = {
        "pass": "passed",
        "fail": "failed",
        "passed": "passed",
        "failed": "failed",
        "blocked": "blocked",
        "skipped": "skipped",
        "not_executed": "not_executed",
    }
    return mapping.get(value, "not_executed")


# --------------------------------------------------------------------- 执行链路落证据
# 只在做「一次执行」的收口处调用：接口执行 record_detail、自动化 execute_script、
# 手工录入 execute_case、回归真跑。三条既有写回链路本身不改。


def _case_brief(conn: sqlite3.Connection, case_id: int | None) -> tuple[str, int | None]:
    """（用例标题, 项目 id）；查得到就带，查不到不影响落证据。"""
    if not case_id:
        return "", None
    row = conn.execute(
        "SELECT case_no, title, project_id FROM biz_test_case WHERE id = ?", (int(case_id),)
    ).fetchone()
    if row is None:
        return "", None
    return f"{str(row['case_no'] or '')} {str(row['title'] or '')}".strip(), _opt_int(row["project_id"])


def _script_project_id(conn: sqlite3.Connection, script_id: int) -> int | None:
    """自动化脚本的项目归属：查不到返回 None，绝不因为补口径而挡住落证据。"""
    try:
        row = cast("sqlite3.Row | None", conn.execute(
            "SELECT project_id FROM biz_automation_script WHERE id = ?", (int(script_id),)
        ).fetchone())
    except sqlite3.Error:
        return None
    return _opt_int(row["project_id"]) if row is not None else None


def capture_interface_execution(
    conn: sqlite3.Connection,
    *,
    execution_id: int,
    case_id: int | None,
    run_id: int | None,
    run_case_id: int | None,
    env_id: int | None,
    detail: dict[str, Any],
    executed_by: str = "",
) -> int:
    """接口执行收口：用内存里已有的 Detail 直接落证据，不回查原表、不发请求。"""
    request = _dict_of(detail.get("request"))
    title, project_id = _case_brief(conn, case_id)
    env_name = ""
    env_row = conn.execute(
        "SELECT name FROM biz_test_env WHERE id = ?", (int(env_id),)
    ).fetchone() if env_id else None
    if env_row is not None:
        env_name = str(env_row["name"] or "")
    message = str(detail.get("message") or "")
    return capture(conn, ExecutionResult(
        source_type=SOURCE_INTERFACE,
        source_row_id=int(execution_id),
        project_id=project_id,
        case_id=_opt_int(case_id),
        run_id=_opt_int(run_id),
        run_case_id=_opt_int(run_case_id),
        env_id=_opt_int(env_id),
        env_name=env_name,
        title=title or message[:200],
        result=_normalize_result(str(detail.get("result") or "")),
        duration_ms=int(detail.get("durationMs") or 0),
        assertions=_list_of_dicts(detail.get("assertions")),
        request_summary=request_summary_of(detail),
        response_summary=response_summary_of(detail),
        source_ref={
            "table": "biz_interface_execution", "id": int(execution_id),
            "method": str(request.get("method") or ""), "url": str(request.get("url") or ""),
        },
        trace_id=trace_id_now(),
        error_message=message if not passed_ok(str(detail.get("result") or "")) else "",
        executed_by=executed_by,
    ))


def passed_ok(result: str) -> bool:
    return _normalize_result(result) == "passed"


def capture_automation_run(
    conn: sqlite3.Connection,
    *,
    run_row: dict[str, Any],
    case_id: int | None = None,
    executed_by: str = "",
) -> int:
    """自动化执行收口：stdout/stderr/退出码压成一段日志摘要，指回 biz_automation_run。"""
    status = str(run_row.get("status") or "")
    title = f"{str(run_row.get('script_no') or '')} {str(run_row.get('script_name') or '')}".strip()
    project_id: int | None = None
    if case_id:
        title_or_none, project_id = _case_brief(conn, int(case_id))
        title = title or title_or_none
    if project_id is None and run_row.get("script_id"):
        # 脚本本身的项目归属：证据不挂项目的话，按项目筛证据 / 按项目设保留期都会漏掉这一类
        project_id = _script_project_id(conn, int(run_row["script_id"]))
    log_text = (
        f"exitCode={run_row.get('exit_code')}\n"
        f"--- stdout ---\n{run_row.get('stdout') or ''}\n"
        f"--- stderr ---\n{run_row.get('stderr') or ''}"
    )
    return capture(conn, ExecutionResult(
        source_type=SOURCE_AUTOMATION,
        source_row_id=int(run_row.get("id") or 0),
        project_id=project_id,
        case_id=_opt_int(case_id),
        run_id=_opt_int(run_row.get("test_run_id")),
        env_id=_opt_int(run_row.get("env_id")),
        title=title,
        result="passed" if status == "success" else (
            "not_executed" if status == "running" else "failed"
        ),
        duration_ms=int(run_row.get("duration_ms") or 0),
        request_summary=summarize({"commandLine": str(run_row.get("command_line") or "")}),
        log_summary=summarize(log_text),
        source_ref={
            "table": "biz_automation_run", "id": int(run_row.get("id") or 0),
            "runNo": str(run_row.get("run_no") or ""),
            "scriptId": _opt_int(run_row.get("script_id")),
            "status": status,
            "writebackStatus": str(run_row.get("writeback_status") or ""),
        },
        trace_id=trace_id_now(),
        error_message=str(run_row.get("error_message") or ""),
        executed_by=executed_by,
        executed_at=str(run_row.get("started_at") or ""),
    ))


# --------------------------------------------------------------------- 回归条目证据（GAP-33）


def capture_regression_item(
    conn: sqlite3.Connection,
    *,
    item: dict[str, Any],
    regression: dict[str, Any],
    result: str,
    exec_mode: str = "manual",
    remark: str = "",
    attachment_ids: list[str] | None = None,
    request_summary: str = "",
    response_summary: str = "",
    log_summary: str = "",
    duration_ms: int | None = None,
    executed_by: str = "",
    source_execution: dict[str, Any] | None = None,
) -> int:
    """回归点执行留痕：手工条目靠填进去的证据，自动条目把真实执行摘要带上。

    ``source_execution`` 是 interface_runner 的 Detail（自动跑时传入），
    断言明细 / 请求响应摘要直接取它，避免同一个结果两处口径不一致。
    """
    detail = source_execution or {}
    attachments = resolve_attachments(conn, list(attachment_ids or []), label="回归证据附件")
    title = f"{str(regression.get('title') or '')} / {str(item.get('title') or '')}".strip(" /")
    assertions = _list_of_dicts(detail.get("assertions"))
    if not assertions and remark and exec_mode == "manual":
        assertions = [{
            "name": "人工核对", "passed": _normalize_result(result) == "passed",
            "expected": "按验证目标核对通过", "actual": remark[:500], "message": "",
        }]
    env_name = ""
    env_id = _opt_int(item.get("env_id")) or _opt_int(detail.get("envId"))
    if env_id:
        env_row = conn.execute("SELECT name FROM biz_test_env WHERE id = ?", (env_id,)).fetchone()
        if env_row is not None:
            env_name = str(env_row["name"] or "")
    case_id = _opt_int(item.get("case_id"))
    case_title, case_project = _case_brief(conn, case_id)
    result_row = ExecutionResult(
        source_type=SOURCE_REGRESSION,
        source_row_id=int(item.get("id") or 0),
        project_id=_opt_int(regression.get("project_id")) or case_project,
        case_id=case_id,
        run_id=_opt_int(detail.get("runId")),
        run_case_id=_opt_int(detail.get("runCaseId")),
        regression_id=_opt_int(regression.get("id")),
        regression_item_id=_opt_int(item.get("id")),
        env_id=env_id,
        env_name=env_name or str(detail.get("envName") or ""),
        title=title or case_title,
        result=_normalize_result(result),
        duration_ms=int(duration_ms if duration_ms is not None else detail.get("durationMs") or 0),
        assertions=assertions,
        request_summary=(
            request_summary_of(detail) if detail else summarize(request_summary)
        ),
        response_summary=(
            response_summary_of(detail) if detail else summarize(response_summary)
        ),
        log_summary=summarize(log_summary or remark or detail.get("message") or ""),
        attachments=attachments,
        source_ref={
            "table": "biz_regression_item", "id": int(item.get("id") or 0),
            "execMode": exec_mode,
            "verifyTarget": str(item.get("verify_target") or ""),
            "interfaceExecutionId": _opt_int(detail.get("executionId")),
        },
        trace_id=trace_id_now(),
        error_message=str(detail.get("message") or "") if _normalize_result(result) != "passed" else "",
        executed_by=executed_by,
    )
    return capture(conn, result_row)


# --------------------------------------------------------------------- 手工证据（GAP-32 功能点 3）


def record_manual_evidence(
    conn: sqlite3.Connection,
    *,
    execution_id: int,
    case_id: int,
    project_id: int | None,
    run_id: int | None = None,
    run_case_id: int | None = None,
    title: str,
    result: str,
    duration_ms: int | None,
    environment: str = "",
    executed_by: str = "",
    remark: str = "",
    attachment_ids: list[str] | None = None,
    request_summary: str = "",
    response_summary: str = "",
    log_summary: str = "",
    trace_id: str = "",
) -> int | None:
    """手工录入的证据快照；没有任何原始留痕（附件/请求/响应/日志）时不落证据。"""
    attachments = resolve_attachments(conn, list(attachment_ids or []), label="证据附件")
    if not attachments and not any([request_summary, response_summary, log_summary, remark]):
        return None
    result_row = ExecutionResult(
        source_type=SOURCE_MANUAL,
        source_row_id=int(execution_id),
        project_id=project_id,
        case_id=int(case_id),
        run_id=run_id,
        run_case_id=run_case_id,
        env_name=environment,
        title=title,
        result=_normalize_result(str(result)),
        duration_ms=int(duration_ms or 0),
        request_summary=summarize(request_summary),
        response_summary=summarize(response_summary),
        log_summary=summarize(log_summary or remark),
        attachments=attachments,
        source_ref={"table": "biz_case_execution", "id": int(execution_id)},
        trace_id=trace_id,
        executed_by=executed_by,
    )
    return capture(conn, result_row)


# --------------------------------------------------------------------- 清理与审计


def purge_expired(
    conn: sqlite3.Connection, *, dry_run: bool = False
) -> dict[str, Any]:
    """保留到期的证据只软删并留审计，绝不物理删（一期审计规范）。"""
    rows = conn.execute(
        "SELECT id, source_type, source_row_id, expires_at FROM biz_execution_evidence"
        " WHERE is_deleted = 0 AND expires_at <> '' AND expires_at <= ?",
        (_now_text(),),
    ).fetchall()
    ids = [int(row["id"]) for row in rows]
    if not dry_run and ids:
        placeholders = ",".join("?" for _ in ids)
        conn.execute(
            f"UPDATE biz_execution_evidence SET is_deleted = 1, updated_at = ?,"
            f" purge_reason = '保留期到期自动清理' WHERE id IN ({placeholders})",
            [utcnow_iso(), *ids],
        )
    return {
        "matched": len(ids),
        "purged": 0 if dry_run else len(ids),
        "dryRun": dry_run,
        "ids": ids[:50],
    }


def evidence_counts(conn: sqlite3.Connection, project_id: int | None = None) -> dict[str, int]:
    sql = (
        "SELECT source_type, COUNT(*) AS c FROM biz_execution_evidence"
        " WHERE is_deleted = 0 AND (expires_at = '' OR expires_at > ?)"
    )
    params: list[Any] = [_now_text()]
    if project_id:
        sql += " AND project_id = ?"
        params.append(int(project_id))
    sql += " GROUP BY source_type"
    counts = {key: 0 for key in SOURCE_TYPES}
    for row in conn.execute(sql, params).fetchall():
        counts[str(row["source_type"])] = int(row["c"])
    counts["total"] = sum(counts[key] for key in SOURCE_TYPES)
    return counts


# --------------------------------------------------------------------- 证据字段矩阵（§8 量化口径）


#: 各执行来源的「必填证据字段」：手工条目没有原始报文，靠附件或摘要补位；
#: 回归自动跑留的是请求/响应而不是日志，日志只对纯手工条目要求（原始留痕由 EITHER 兜底）
REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    SOURCE_INTERFACE: ("request_summary", "response_summary", "assertions"),
    SOURCE_AUTOMATION: ("request_summary", "log_summary"),
    SOURCE_MANUAL: ("log_summary",),
    SOURCE_REGRESSION: ("assertions",),
    SOURCE_EXPLORATORY: ("log_summary", "attachments"),
}

#: 「附件或摘要」二选一的来源：手工/回归都可能只留一份说明
EITHER_FIELDS: dict[str, tuple[str, ...]] = {
    SOURCE_MANUAL: ("attachments", "request_summary", "response_summary", "log_summary"),
    SOURCE_REGRESSION: ("attachments", "request_summary", "response_summary", "log_summary"),
}


#: 逻辑字段名 → 证据表列名：明细与附件存 JSON 列，其余同名
FIELD_COLUMNS: dict[str, str] = {
    "assertions": "assertions_json",
    "attachments": "attachments_json",
    "source_ref": "source_ref_json",
}


def has_evidence_field(row: sqlite3.Row, name: str) -> bool:
    """字段到底有没有值：JSON 列看解析后的条目数，普通列看去空白后的文本。"""
    column = FIELD_COLUMNS.get(name, name)
    if column not in row.keys():
        return False
    if column.endswith("_json"):
        parsed = _json_field(row, column)
        if isinstance(parsed, (list, dict)):
            return len(parsed) > 0
        return bool(str(parsed or "").strip())
    return bool(str(row[column] or "").strip())


#: 证据字段的中文字典名：给矩阵与前端空态直接展示，不再露出 snake_case
FIELD_LABELS: dict[str, str] = {
    "request_summary": "请求摘要",
    "response_summary": "响应摘要",
    "log_summary": "日志摘要",
    "attachments": "附件",
    "assertions": "断言明细",
}


def missing_fields(row: sqlite3.Row) -> list[str]:
    """一条证据缺哪些必填字段；口径见 ``REQUIRED_FIELDS`` / ``EITHER_FIELDS``。"""
    source_type = str(row["source_type"] or "")
    missing: list[str] = []
    for field_name in REQUIRED_FIELDS.get(source_type, ()):
        if not has_evidence_field(row, field_name):
            missing.append(FIELD_LABELS.get(field_name, field_name))
    either = EITHER_FIELDS.get(source_type)
    if either and not any(has_evidence_field(row, name) for name in either):
        missing.append("原始留痕（附件 / 请求 / 响应 / 日志任一）")
    return missing


def _json_field(row: sqlite3.Row, column: str) -> Any:
    try:
        return json.loads(str(row[column] or "") or "[]")
    except (TypeError, ValueError):
        return []


def field_matrix(conn: sqlite3.Connection, project_id: int | None = None) -> dict[str, Any]:
    """执行类型 × 必填证据字段达标率：给 §8 的量化口径与设置页展示用。"""
    sql = (
        "SELECT * FROM biz_execution_evidence WHERE is_deleted = 0"
        " AND (expires_at = '' OR expires_at > ?)"
    )
    params: list[Any] = [_now_text()]
    if project_id:
        sql += " AND project_id = ?"
        params.append(int(project_id))
    rows = conn.execute(sql, params).fetchall()
    per_source: dict[str, dict[str, Any]] = {}
    for source_type in SOURCE_TYPES:
        per_source[source_type] = {
            "sourceType": source_type,
            "sourceTypeLabel": SOURCE_LABELS[source_type],
            "requiredFields": list(REQUIRED_FIELDS[source_type]),
            "total": 0,
            "compliant": 0,
            "missingFields": {},
        }
    for row in rows:
        bucket = per_source.get(str(row["source_type"]))
        if bucket is None:
            continue
        bucket["total"] += 1
        missing = missing_fields(row)
        if not missing:
            bucket["compliant"] += 1
        else:
            for name in missing:
                bucket["missingFields"][name] = int(bucket["missingFields"].get(name, 0)) + 1
    total = sum(int(bucket["total"]) for bucket in per_source.values())
    compliant = sum(int(bucket["compliant"]) for bucket in per_source.values())
    for bucket in per_source.values():
        bucket["rate"] = round(bucket["compliant"] * 100.0 / bucket["total"], 2) if bucket["total"] else 0.0
    return {
        "total": total,
        "compliant": compliant,
        "rate": round(compliant * 100.0 / total, 2) if total else 0.0,
        "bySource": list(per_source.values()),
    }
