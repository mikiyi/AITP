"""回归清单服务。

清单的来源有两种：手工写，或者让 AI 按变更范围生成。
生成的条目落到明细表里逐条跟踪状态——每条验证目标对应一个执行结果，
整单状态由明细结果回推，不允许「清单写了但没人跑」。
"""

from __future__ import annotations

import sqlite3
from typing import Any, NoReturn

from ..ai.capability_codes import CAP_REGRESSION_GENERATION
from ..common.time_utils import utcnow_iso
from ..common.validators import require_int
from ..errors import AppError, ErrorCode
from ..repositories.project_repo import ProjectRepository
from ..repositories.regression_repo import (
    ITEM_RESULTS,
    REGRESSION_DRAFT,
    RISK_LEVELS,
    RegressionItemRepository,
    RegressionRepository,
    item_summary,
)
from ..repositories.test_case_repo import TestCaseRepository
from ..repositories.test_env_repo import TestEnvRepository
from . import execution_protocol, interface_runner, lab_service


def _items_from_output(parsed: Any) -> list[dict[str, Any]]:
    """从 AI 的 JSON 产出里取出回归条目。

    模型偶尔会把数组包在 {"items": [...]} 或 {"list": [...]} 里，
    两种都认；认不出来就返回空列表，不猜。
    """
    if isinstance(parsed, dict):
        for key in ("items", "list", "regressions", "regressionItems"):
            value = parsed.get(key)
            if isinstance(value, list):
                parsed = value
                break
    if not isinstance(parsed, list):
        return []
    items: list[dict[str, Any]] = []
    for raw in parsed:
        if not isinstance(raw, dict):
            continue
        title = str(raw.get("title") or "").strip()
        if not title:
            continue
        priority = str(raw.get("priority") or "P1").strip().upper()
        if priority not in ("P0", "P1", "P2"):
            priority = "P1"
        items.append({
            "title": title,
            "module": str(raw.get("module") or "").strip(),
            "priority": priority,
            "reason": str(raw.get("reason") or "").strip(),
            "verifyTarget": str(raw.get("verifyTarget") or raw.get("verify_target") or "").strip(),
        })
    return items


def _guard_scope(
    conn: sqlite3.Connection,
    *,
    project_id: Any,
    version_id: Any,
    creating: bool,
    current_project_id: Any = None,
) -> None:
    """清单必须挂在真实项目下，版本引用也不能跨项目。

    跨项目的版本会让「按版本回归」统计把别的项目的清单算进来，
    这里在写入前拦住，而不是等数据脏了再去补。
    """
    if creating:
        if conn.execute(
            "SELECT id FROM biz_project WHERE id = ? AND is_deleted = 0", (int(project_id or 0),)
        ).fetchone() is None:
            raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目不存在")
        effective = project_id
    else:
        if project_id is not None and int(project_id) != int(current_project_id or 0):
            raise AppError(ErrorCode.ILLEGAL_OPERATION, "不支持把回归清单改挂到别的项目")
        effective = project_id if project_id is not None else current_project_id
    if version_id in (None, ""):
        return
    value = require_int(version_id, "versionId")
    row = conn.execute(
        "SELECT project_id FROM biz_project_version WHERE id = ? AND is_deleted = 0", (value,)
    ).fetchone()
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "项目版本不存在")
    if effective in (None, "", 0) or int(row["project_id"] or -1) != int(effective):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "项目版本不属于该项目，不能挂到本项目下")


def create_regression(
    conn: sqlite3.Connection,
    *,
    project_id: int,
    title: str,
    change_scope: str = "",
    affected_modules: str = "",
    version: str = "",
    version_id: int | None = None,
    risk_level: str = "low",
    content: str = "",
    source: str = "manual",
) -> int:
    repo = RegressionRepository(conn)
    risk = risk_level if risk_level in RISK_LEVELS else "low"
    _guard_scope(conn, project_id=project_id, version_id=version_id, creating=True)
    return repo.insert({
        "project_id": project_id,
        "title": str(title or "回归清单"),
        "change_scope": str(change_scope or ""),
        "affected_modules": str(affected_modules or ""),
        "version": str(version or ""),
        "version_id": version_id,
        "risk_level": risk,
        "status": REGRESSION_DRAFT,
        "source": source,
        "content": str(content or ""),
    })


def update_regression(conn: sqlite3.Connection, pk: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    payload: dict[str, Any] = {}
    if "title" in data:
        payload["title"] = str(data["title"] or "")
    if "changeScope" in data:
        payload["change_scope"] = str(data["changeScope"] or "")
    if "affectedModules" in data:
        payload["affected_modules"] = str(data["affectedModules"] or "")
    if "version" in data:
        payload["version"] = str(data["version"] or "")
    if "versionId" in data:
        raw_version_id = data["versionId"]
        payload["version_id"] = None if raw_version_id in (None, "") else require_int(raw_version_id, "versionId")
    if "content" in data:
        payload["content"] = str(data["content"] or "")
    if "riskLevel" in data:
        risk = str(data["riskLevel"])
        if risk not in RISK_LEVELS:
            raise AppError(ErrorCode.PARAM_ERROR, "风险等级非法")
        payload["risk_level"] = risk
    _guard_scope(
        conn,
        project_id=data.get("projectId"),
        version_id=payload.get("version_id"),
        creating=False,
        current_project_id=row.get("project_id"),
    )
    repo.update(pk, payload)
    detail = repo.get_detail(pk)
    if detail is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    return detail


def _project_name(conn: sqlite3.Connection, project_id: int | None, fallback: str) -> str:
    """取项目名：优先用项目表里的真名，取不到就用清单标题兜底。

    提示词把 projectName 列为必填，传空串会被渲染层判成缺变量，
    所以这里必须给出一个非空的名字。
    """
    if project_id:
        project = ProjectRepository(conn).get(int(project_id))
        if project:
            name = str(project.get("name") or "").strip()
            if name:
                return name
    return str(fallback or "").strip() or "未命名项目"


def generate_items(
    conn: sqlite3.Connection,
    pk: int,
    *,
    profile_id: int | None = None,
) -> dict[str, Any]:
    """用「回归清单」能力生成条目并覆盖旧明细。"""
    repo = RegressionRepository(conn)
    row = repo.get(pk)
    if row is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    change_scope = str(row.get("change_scope") or "").strip()
    if not change_scope:
        raise AppError(ErrorCode.PARAM_ERROR, "请先填写「变更范围」，再生成回归条目")
    variables = {
        "projectName": _project_name(
            conn, row.get("project_id"), str(row.get("title") or ""),
        ),
        "changeScope": change_scope,
        "affectedModules": str(row.get("affected_modules") or ""),
    }
    result = lab_service.run_capability(
        conn, CAP_REGRESSION_GENERATION, variables, profile_id=profile_id,
    )
    items = _items_from_output(result.parsed)
    item_repo = RegressionItemRepository(conn)
    created = item_repo.replace_all(pk, items)
    repo.update(pk, {
        "source": "ai",
        "status": "ready" if created else REGRESSION_DRAFT,
        "content": result.output,
    })
    return {
        "id": pk,
        "created": created,
        "items": [item_summary(row) for row in item_repo.list_by_regression(pk)],
        "output": result.output,
        "model": result.model,
        "reviewRequired": result.review_required,
    }


def add_item(conn: sqlite3.Connection, pk: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    title = str(data.get("title") or "").strip()
    if not title:
        raise AppError(ErrorCode.PARAM_ERROR, "回归点标题不能为空")
    priority = str(data.get("priority") or "P1").upper()
    if priority not in ("P0", "P1", "P2"):
        raise AppError(ErrorCode.PARAM_ERROR, "优先级只能是 P0 / P1 / P2")
    new_id = item_repo.insert({
        "regression_id": pk,
        "seq": item_repo.next_seq(pk),
        "title": title,
        "module": str(data.get("module") or ""),
        "priority": priority,
        "reason": str(data.get("reason") or ""),
        "verify_target": str(data.get("verifyTarget") or ""),
        "result": "not_executed",
        "remark": "",
        **_mode_payload(conn, data),
    })
    repo.sync_status(pk)
    return item_summary(item_repo.get(new_id) or {})


def _mode_payload(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    """GAP-33：执行方式 + 真跑定位字段；不传就保持默认手工，不猜。"""
    payload: dict[str, Any] = {}
    if "execMode" in data or "caseId" in data:
        exec_mode = str(data.get("execMode") or EXEC_MANUAL).strip().lower()
        if exec_mode not in EXEC_MODES:
            raise AppError(ErrorCode.PARAM_ERROR, "执行方式只能是 auto（可自动）或 manual（手工）")
        case_id = data.get("caseId")
        if exec_mode == EXEC_AUTO:
            if not case_id:
                raise AppError(
                    ErrorCode.PARAM_ERROR,
                    "标为自动执行的回归点必须绑定一条接口用例：请在 caseId 里选择要真跑的用例，"
                    "或把执行方式改回 manual。",
                )
            _check_interface_case(conn, int(case_id))
        payload["exec_mode"] = exec_mode
        payload["case_id"] = int(case_id) if case_id else None
    if "envId" in data:
        env_id = data.get("envId")
        if env_id:
            if TestEnvRepository(conn).get_by_id(int(env_id)) is None:
                raise AppError(ErrorCode.PARAM_ERROR, "执行环境不存在或已删除，请重新选择环境")
            payload["env_id"] = int(env_id)
        else:
            payload["env_id"] = None
    return payload


def _check_interface_case(conn: sqlite3.Connection, case_id: int) -> dict[str, Any]:
    case = TestCaseRepository(conn).get_detail(case_id)
    if case is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"回归点绑定的用例 {case_id} 不存在或已删除")
    if str(case.get("case_type") or "") != "interface":
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"用例「{str(case.get('title') or case_id)[:30]}」不是「接口测试」类型，"
            "自动执行目前只支持接口用例；请改选接口用例或把执行方式标为 manual。",
        )
    return case


def update_item(conn: sqlite3.Connection, pk: int, item_id: int, data: dict[str, Any]) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    row = item_repo.get(item_id)
    if row is None or int(row.get("regression_id") or 0) != pk:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归明细不存在")
    payload: dict[str, Any] = {}
    payload.update(_mode_payload(conn, data))
    exec_mode = str(payload.get("exec_mode") or row.get("exec_mode") or EXEC_MANUAL)
    if "title" in data:
        payload["title"] = str(data["title"] or "")
    if "module" in data:
        payload["module"] = str(data["module"] or "")
    if "reason" in data:
        payload["reason"] = str(data["reason"] or "")
    if "verifyTarget" in data:
        payload["verify_target"] = str(data["verifyTarget"] or "")
    if "priority" in data:
        priority = str(data["priority"]).upper()
        if priority not in ("P0", "P1", "P2"):
            raise AppError(ErrorCode.PARAM_ERROR, "优先级只能是 P0 / P1 / P2")
        payload["priority"] = priority
    if "remark" in data:
        payload["remark"] = str(data["remark"] or "")
    if "result" in data:
        result = str(data["result"])
        if result not in ITEM_RESULTS:
            raise AppError(ErrorCode.PARAM_ERROR, "执行结果非法")
        if exec_mode == EXEC_AUTO:
            # GAP-33：自动条目的结论只能由服务端真跑写入（POST /regressions/<id>/execute）。
            # PUT 里带 result 一律拒绝，连「顺带替它跑一次」也不做：
            # 否则前端会以为结论是自己改的，假执行后门只是换了个口子。
            raise AppError(
                ErrorCode.ILLEGAL_OPERATION,
                f"回归点「{str(row.get('title') or '')[:30]}」标成了自动执行，结论不能手工写入："
                f"请调用 POST /api/v1/regressions/{pk}/execute 由服务端真跑回写；"
                "本接口仍可改标题、优先级、绑定用例与环境。"
                "确实要人工判结论，请把执行方式改为 manual 并按要求附证据。",
            )
        evidence = _manual_evidence(data, str(payload.get("remark") or row.get("remark") or ""))
        if result == "passed" and not evidence:
            _reject_without_evidence([f"{item_id}（{str(row.get('title') or '')[:30]}）"])
        payload["result"] = result
        payload["executed_at"] = utcnow_iso()
        payload["executed_by"] = str(data.get("executedBy") or "local")
        if evidence:
            payload["evidence_id"] = execution_protocol.capture_regression_item(
                conn, item=row, regression=repo.get(pk) or {}, result=result,
                exec_mode=exec_mode,
                remark=str(payload.get("remark") or row.get("remark") or ""),
                executed_by=str(data.get("executedBy") or "local"),
                duration_ms=data.get("durationMs"), **evidence,
            )
    item_repo.update(item_id, payload)
    repo.sync_status(pk)
    return item_summary(item_repo.get(item_id) or {})


def delete_item(conn: sqlite3.Connection, pk: int, item_id: int) -> dict[str, Any]:
    repo = RegressionRepository(conn)
    if repo.get(pk) is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    row = item_repo.get(item_id)
    if row is None or int(row.get("regression_id") or 0) != pk:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归明细不存在")
    item_repo.soft_delete(item_id)
    repo.sync_status(pk)
    return {"id": item_id, "deleted": True}


def execute_items(
    conn: sqlite3.Connection,
    pk: int,
    results: list[dict[str, Any]],
    *,
    env_id: int | None = None,
    executed_by: str = "local",
) -> dict[str, Any]:
    """批量回写执行结果，并按明细回推整单状态。

    GAP-33 起分两条路：
      - ``exec_mode = auto`` 的条目由服务端**真跑**（调用接口执行器），
        客户端传来的结论一律忽略，只认这次真实跑出来的结果与证据；
      - ``exec_mode = manual`` 的条目仍允许批量回写，但**必须带证据**
        （附件 / 请求 / 响应 / 日志摘要任一），否则不允许标记为通过（决策 4）。
    """
    repo = RegressionRepository(conn)
    regression = repo.get(pk)
    if regression is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "回归清单不存在")
    item_repo = RegressionItemRepository(conn)
    updated: list[int] = []
    skipped: list[int] = []
    executed: list[dict[str, Any]] = []
    failures: list[str] = []
    default_env_id = env_id
    for item in results:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "results 元素必须是对象")
        item_id = require_int(item.get("itemId"), "results[].itemId")
        row = item_repo.get(item_id)
        if row is None or int(row.get("regression_id") or 0) != pk:
            skipped.append(item_id)
            continue
        if str(row.get("exec_mode") or "manual") == EXEC_AUTO:
            executed.append(
                _run_auto_item(conn, item_repo, regression, row, env_id=default_env_id,
                               executed_by=executed_by)
            )
            updated.append(item_id)
            continue
        result = str(item.get("result") or "")
        if result not in ITEM_RESULTS:
            skipped.append(item_id)
            continue
        remark = str(item.get("remark") or row.get("remark") or "")
        evidence = _manual_evidence(item, remark)
        if result == "passed" and not evidence:
            failures.append(f"{item_id}（{str(row.get('title') or '')[:30]}）")
            continue
        payload: dict[str, Any] = {
            "result": result,
            "remark": remark,
            "executed_by": str(item.get("executedBy") or executed_by),
            "executed_at": utcnow_iso(),
        }
        if evidence:
            payload["evidence_id"] = execution_protocol.capture_regression_item(
                conn, item=row, regression=regression, result=result,
                exec_mode=EXEC_MANUAL, remark=remark, executed_by=executed_by,
                duration_ms=item.get("durationMs"), **evidence,
            )
        item_repo.update(item_id, payload)
        updated.append(item_id)
    if failures:
        _reject_without_evidence(failures)
    status = repo.sync_status(pk)
    stats = repo.stats_of(pk)
    return {
        "id": pk,
        "status": status,
        "updated": updated,
        "skipped": skipped,
        "executed": executed,
        "stats": stats,
        "coverage": coverage(stats),
    }


EXEC_AUTO = "auto"
EXEC_MANUAL = "manual"
EXEC_MODES = (EXEC_AUTO, EXEC_MANUAL)


def _reject_without_evidence(descriptors: list[str]) -> NoReturn:
    """决策 4：无证据不得标记通过——报错要点名是哪几条、该补什么。"""
    raise AppError(
        ErrorCode.PARAM_ERROR,
        f"手工回归点标记「通过」必须附证据：{ '、'.join(descriptors[:5]) } 没有附件也没有"
        "请求/响应/日志摘要。请在提交里带 attachmentIds，或填写 logSummary / requestSummary /"
        " responseSummary 之一；确实还没验证的条目请改标为 not_executed 或 blocked。",
    )


def _manual_evidence(item: dict[str, Any], remark: str) -> dict[str, Any]:
    """把前端传来的证据字段整理成 capture 的入参；什么都没带时返回空 dict。

    备注本身不算证据（一期就是只有一句「失败」才复盘不了），所以这里只看
    附件与请求 / 响应 / 日志摘要。
    """
    raw_attachments = item.get("attachmentIds")
    attachment_ids = [str(x) for x in raw_attachments if str(x or "").strip()] \
        if isinstance(raw_attachments, list) else []
    summaries = {
        "request_summary": str(item.get("requestSummary") or ""),
        "response_summary": str(item.get("responseSummary") or ""),
        "log_summary": str(item.get("logSummary") or ""),
    }
    if not attachment_ids and not any(summaries.values()):
        return {}
    return {"attachment_ids": attachment_ids, **summaries}


def coverage(stats: dict[str, int]) -> dict[str, Any]:
    """本次执行覆盖率：已执行 / 总条目，外加自动占比与证据覆盖率。"""
    def rate(part: int, whole: int) -> float:
        return round(part * 100.0 / whole, 2) if whole else 0.0

    total = int(stats.get("total") or 0)
    executed = int(stats.get("executed") or 0)
    auto_total = int(stats.get("autoTotal") or 0)
    auto_ran = int(stats.get("autoExecuted") or 0)
    evidenced = int(stats.get("evidenced") or 0)
    return {
        "total": total,
        "executed": executed,
        "executedRate": rate(executed, total),
        "autoTotal": auto_total,
        "autoExecuted": auto_ran,
        "autoRate": rate(auto_ran, auto_total),
        "autoShare": rate(auto_total, total),
        "evidenced": evidenced,
        "evidenceRate": rate(evidenced, executed),
    }


def _run_auto_item(
    conn: sqlite3.Connection,
    item_repo: RegressionItemRepository,
    regression: dict[str, Any],
    row: dict[str, Any],
    *,
    env_id: int | None,
    executed_by: str,
) -> dict[str, Any]:
    """真跑一条自动回归点：调用接口执行器，结果与证据一起回写。"""
    case_id = row.get("case_id")
    if not case_id:
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"回归点「{str(row.get('title') or '')[:30]}」标成了自动执行但没绑定用例："
            "请在回归条目里选择要真跑的接口用例，或把执行方式改回手工。",
        )
    case = TestCaseRepository(conn).get_detail(int(case_id))
    if case is None:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, f"回归点绑定的用例 {case_id} 不存在或已删除")
    if str(case.get("case_type") or "") != "interface":
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            f"回归点「{str(row.get('title') or '')[:30]}」绑的用例不是「接口测试」类型，"
            "自动执行目前只支持接口用例，请改标为手工执行。",
        )
    target_env = env_id if env_id is not None else row.get("env_id")
    if target_env is None:
        default_env = TestEnvRepository(conn).get_default()
        target_env = int(default_env["id"]) if default_env else None
    env = interface_runner.load_env(conn, target_env)
    detail = interface_runner.execute(
        case.get("extra") or {}, env,
        conn=conn, project_id=case.get("project_id"), module_id=case.get("module_id"),
    )
    execution_id = interface_runner.record_detail(
        conn,
        case_id=int(case_id),
        run_id=None,
        run_case_id=None,
        env_id=int(env["id"]),
        detail=detail,
        executed_by=executed_by,
    )
    detail["executionId"] = execution_id
    detail["envId"] = int(env["id"])
    detail["envName"] = str(env.get("name") or "")
    item_result = "passed" if detail.get("result") == "passed" else "failed"
    evidence_id = execution_protocol.capture_regression_item(
        conn, item=row, regression=regression, result=item_result,
        exec_mode=EXEC_AUTO, remark=str(detail.get("message") or ""),
        executed_by=executed_by, source_execution=detail,
    )
    item_repo.update(int(row["id"]), {
        "result": item_result,
        "remark": str(detail.get("message") or "")[:2000],
        "executed_by": executed_by,
        "executed_at": utcnow_iso(),
        "evidence_id": evidence_id,
    })
    return {
        "itemId": int(row["id"]),
        "caseId": int(case_id),
        "result": item_result,
        "evidenceId": evidence_id,
        "executionId": execution_id,
        "durationMs": int(detail.get("durationMs") or 0),
        "assertions": list(detail.get("assertions") or []),
    }
