"""Web 探索性测试服务（三期 GAP-45）。

MCP 驱动浏览器按「读快照→决策→动作→观测」循环探索，产物**不直接写缺陷库**：
疑似缺陷先进知识待审池（source_type='exploratory'），人工审核通过才转 biz_bug；
每一步异常都留统一执行证据（source_type='exploratory'），证据包含步骤/选择器、
异常时强制 3 张截图、双锚点 ±N 秒日志切片、网络快照、控制台错误与 traceId。

两个驱动：
- ``scripted``：按发起时给定的确定性计划逐步执行，门禁与无模型场景用它；
- ``model``：期望由模型决策下一步；没有可用模型时**优雅降级**为 scripted 并如实标记。

MCP 客户端走 stdio JSON-RPC，只允许调用钉住的浏览器工具清单，命令由配置给定，
不内置任何远端地址；输入值按配置脱敏，口令/令牌绝不留明文。
"""

from __future__ import annotations

import base64
import contextlib
import json
import queue
import re
import shlex
import sqlite3
import subprocess
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from ..ai.resolver import resolve_candidates
from ..common.pii import scan_sensitive
from ..common.time_utils import parse_iso, utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.exploration_repo import (
    ExplorationSessionRepository,
    ExplorationStepRepository,
    session_public,
    step_public,
)
from ..repositories.knowledge_draft_repo import KnowledgeDraftRepository
from ..repositories.sys_config_repo import SysConfigRepository
from . import dedup_service, execution_protocol

SETTINGS_KEY = "exploratory.web.settings"

DRIVER_SCRIPTED = "scripted"
DRIVER_MODEL = "model"
DRIVERS = (DRIVER_SCRIPTED, DRIVER_MODEL)
DRIVER_LABELS = {DRIVER_SCRIPTED: "确定性脚本", DRIVER_MODEL: "模型决策"}

SESSION_DRAFT = "draft"
SESSION_QUEUED = "queued"
SESSION_RUNNING = "running"
SESSION_DONE = "done"
SESSION_FAILED = "failed"
SESSION_CANCELLED = "cancelled"
SESSION_STATUSES = (SESSION_DRAFT, SESSION_QUEUED, SESSION_RUNNING,
                    SESSION_DONE, SESSION_FAILED, SESSION_CANCELLED)
SESSION_STATUS_LABELS = {
    SESSION_DRAFT: "草稿", SESSION_QUEUED: "排队中", SESSION_RUNNING: "探索中",
    SESSION_DONE: "已完成", SESSION_FAILED: "失败", SESSION_CANCELLED: "已取消",
}

#: 钉住的 MCP 浏览器工具：只允许调用这些，避免驱动到任意工具
PINNED_TOOLS = (
    "browser_navigate", "browser_click", "browser_type", "browser_snapshot",
    "browser_screenshot", "browser_console_messages", "browser_network_requests",
)

#: 异常类型
ANOMALY_PAGE_ERROR = "page_error"
ANOMALY_HTTP_ERROR = "http_error"
ANOMALY_CONSOLE_ERROR = "console_error"
ANOMALY_BUSINESS_CODE = "business_code"
ANOMALY_LABELS = {
    ANOMALY_PAGE_ERROR: "页面报错",
    ANOMALY_HTTP_ERROR: "接口 4xx/5xx",
    ANOMALY_CONSOLE_ERROR: "控制台错误",
    ANOMALY_BUSINESS_CODE: "业务码异常",
}

#: 异常时强制截图张数
ANOMALY_SCREENSHOTS = 3
#: 日志切片窗口上下限（秒）
MIN_LOG_WINDOW = 1
MAX_LOG_WINDOW = 10
#: 日志来源最多个数
MAX_LOG_SOURCES = 5

DEFAULT_SETTINGS: dict[str, Any] = {
    "maxSteps": 20,
    "maxSeconds": 180,
    "perStepTimeout": 30,
    "perStepScreenshots": 1,
    "recordSelectors": True,
    "inputMasking": True,
    "screenshotDir": "exploratory/screenshots",
    "logSliceDir": "exploratory/log-slices",
    "logSources": [],
    "logWindowSeconds": 5,
    "mcpCommand": "",
    "pinnedTools": list(PINNED_TOOLS),
}

_SENSITIVE_SELECTOR_RE = re.compile(
    r"(?i)(password|passwd|pwd|token|secret|credential|api[_-]?key|口令|密码|凭证)"
)
_LOG_TS_RE = re.compile(
    r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:[.,]\d{1,6})?(?:Z|[+-]\d{2}:?\d{2})?)"
)
_PAGE_ERROR_RE = re.compile(
    r"(?i)(internal server error|500|502|503|exception|traceback|错误|异常|报错|failed to|cannot read)"
)
_BUSINESS_CODE_RE = re.compile(r'"code"\s*:\s*(?!0\b)(\d+)')


# --------------------------------------------------------------------- 配置


def get_settings(conn: sqlite3.Connection) -> dict[str, Any]:
    raw = SysConfigRepository(conn).get_value(SETTINGS_KEY, "")
    merged = dict(DEFAULT_SETTINGS)
    if raw:
        try:
            stored = json.loads(str(raw))
            if isinstance(stored, dict):
                merged.update(stored)
        except (TypeError, ValueError):
            pass
    merged["pinnedTools"] = list(PINNED_TOOLS)
    return merged


def update_settings(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    current = get_settings(conn)
    candidate = dict(current)
    for key in ("maxSteps", "maxSeconds", "perStepTimeout", "perStepScreenshots", "logWindowSeconds"):
        if key in data:
            candidate[key] = _bounded_int(data[key], key)
    for key in ("recordSelectors", "inputMasking"):
        if key in data:
            candidate[key] = _as_bool(data[key])
    for key in ("screenshotDir", "logSliceDir", "mcpCommand"):
        if key in data:
            candidate[key] = str(data[key] or "").strip()
    if "logSources" in data:
        candidate["logSources"] = _validate_log_sources(data["logSources"])
    SysConfigRepository(conn).set_value(
        SETTINGS_KEY, json.dumps(candidate, ensure_ascii=False), value_type="json",
        description="Web 探索性测试配置", is_system=1,
    )
    conn.commit()
    return candidate


def _bounded_int(value: Any, key: str) -> int:
    bounds = {
        "maxSteps": (1, 200), "maxSeconds": (1, 3600), "perStepTimeout": (1, 300),
        "perStepScreenshots": (0, 5), "logWindowSeconds": (MIN_LOG_WINDOW, MAX_LOG_WINDOW),
    }
    low, high = bounds[key]
    try:
        number = int(value)
    except (TypeError, ValueError):
        raise AppError(ErrorCode.PARAM_ERROR, f"{key} 必须是整数")
    if not low <= number <= high:
        raise AppError(ErrorCode.PARAM_ERROR, f"{key} 超出范围 [{low}, {high}]")
    return number


def _as_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


def _validate_log_sources(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list):
        raise AppError(ErrorCode.PARAM_ERROR, "logSources 必须是数组")
    if len(value) > MAX_LOG_SOURCES:
        raise AppError(ErrorCode.PARAM_ERROR, f"日志来源最多 {MAX_LOG_SOURCES} 个")
    result: list[dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise AppError(ErrorCode.PARAM_ERROR, "日志来源必须是 {name,path,source} 对象")
        path = str(item.get("path") or "").strip()
        if not path:
            raise AppError(ErrorCode.PARAM_ERROR, "日志来源 path 不能为空")
        result.append({
            "name": str(item.get("name") or Path(path).name)[:100],
            "path": path,
            "source": str(item.get("source") or "file")[:20],
        })
    return result


# --------------------------------------------------------------------- MCP 客户端


class McpError(Exception):
    """MCP 调用失败（启动失败 / 超时 / 协议错误 / 工具不在钉住清单）。"""


class McpClient:
    """stdio JSON-RPC MCP 客户端：只调用钉住的浏览器工具。"""

    def __init__(self, command: str, *, timeout: float = 30.0,
                 pinned: tuple[str, ...] = PINNED_TOOLS) -> None:
        argv = shlex.split(command) if isinstance(command, str) else list(command)
        if not argv:
            raise McpError("未配置 MCP 服务命令（exploratory.web.settings.mcpCommand）")
        self.pinned = tuple(pinned)
        self.timeout = float(timeout)
        self._id = 0
        self._queue: queue.Queue[str] = queue.Queue()
        try:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, bufsize=1,
            )
        except (OSError, ValueError) as exc:
            raise McpError(f"MCP 服务启动失败：{exc}") from exc
        self._reader = threading.Thread(target=self._read_loop, daemon=True)
        self._reader.start()

    def _read_loop(self) -> None:
        assert self.proc.stdout is not None
        for line in self.proc.stdout:
            text = line.strip()
            if text:
                self._queue.put(text)

    def _request(self, method: str, params: dict[str, Any]) -> Any:
        if self.proc.poll() is not None:
            raise McpError("MCP 服务进程已退出")
        self._id += 1
        message = {"jsonrpc": "2.0", "id": self._id, "method": method, "params": params}
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(json.dumps(message, ensure_ascii=False) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as exc:
            raise McpError(f"MCP 写入失败：{exc}") from exc
        try:
            line = self._queue.get(timeout=self.timeout)
        except queue.Empty:
            raise McpError(f"MCP 响应超时（{self.timeout}s）：{method}")
        try:
            response = json.loads(line)
        except (TypeError, ValueError) as exc:
            raise McpError(f"MCP 响应不是合法 JSON：{line[:200]}") from exc
        if isinstance(response, dict) and response.get("error"):
            err = response["error"]
            raise McpError(f"MCP 错误：{err.get('message') or err}")
        return response.get("result") if isinstance(response, dict) else None

    def initialize(self) -> dict[str, Any]:
        result = self._request("initialize", {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "aitp-exploratory", "version": "1.0"},
        })
        return result if isinstance(result, dict) else {}

    def list_tools(self) -> list[str]:
        result = self._request("tools/list", {})
        tools = (result or {}).get("tools") if isinstance(result, dict) else None
        return [str(item.get("name")) for item in (tools or []) if isinstance(item, dict)]

    def ensure_pinned(self) -> list[str]:
        tools = self.list_tools()
        missing = [name for name in self.pinned if name not in tools]
        if missing:
            raise McpError(f"MCP 服务缺少钉住工具：{'、'.join(missing)}")
        return tools

    def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if name not in self.pinned:
            raise McpError(f"工具 {name} 不在钉住清单，拒绝调用")
        result = self._request("tools/call", {"name": name, "arguments": arguments})
        return _tool_payload(result)

    def close(self) -> None:
        with contextlib.suppress(OSError, ValueError):
            if self.proc.stdin:
                self.proc.stdin.close()
        try:
            self.proc.terminate()
            self.proc.wait(timeout=3)
        except Exception:  # noqa: BLE001 - 清理失败不影响结果，兜底强杀
            with contextlib.suppress(Exception):
                self.proc.kill()


def _tool_payload(result: Any) -> dict[str, Any]:
    """MCP tools/call 的 content 块 → 解析后的 JSON 字典。"""
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list) and content:
            first = content[0]
            if isinstance(first, dict) and first.get("type") == "text":
                text = str(first.get("text") or "")
                try:
                    parsed = json.loads(text)
                    if isinstance(parsed, dict):
                        return parsed
                except (TypeError, ValueError):
                    return {"text": text}
        if "content" not in result:
            return result
    return {}


# --------------------------------------------------------------------- 会话 CRUD


def create_session(conn: sqlite3.Connection, data: dict[str, Any]) -> dict[str, Any]:
    project_id = int(data.get("projectId") or 0)
    if not project_id:
        raise AppError(ErrorCode.PARAM_ERROR, "projectId 必填")
    target_url = str(data.get("targetUrl") or "").strip()
    if not target_url:
        raise AppError(ErrorCode.PARAM_ERROR, "targetUrl 必填")
    driver = str(data.get("driver") or DRIVER_SCRIPTED)
    if driver not in DRIVERS:
        raise AppError(ErrorCode.PARAM_ERROR, f"driver 只能是 {'/'.join(DRIVERS)}")
    settings = get_settings(conn)
    max_steps = int(data.get("maxSteps") or settings["maxSteps"])
    max_seconds = int(data.get("maxSeconds") or settings["maxSeconds"])
    plan = data.get("plan") or []
    if not isinstance(plan, list):
        raise AppError(ErrorCode.PARAM_ERROR, "plan 必须是数组")
    repo = ExplorationSessionRepository(conn)
    session_id = repo.insert({
        "session_no": repo.next_session_no(),
        "project_id": project_id,
        "name": str(data.get("name") or f"探索 {target_url}")[:200],
        "target_url": target_url[:1000],
        "driver": driver,
        "status": SESSION_DRAFT,
        "max_steps": max(1, min(200, max_steps)),
        "max_seconds": max(1, min(3600, max_seconds)),
        "settings_json": json.dumps(settings, ensure_ascii=False),
        "plan_json": json.dumps(plan, ensure_ascii=False),
        "created_by": str(data.get("createdBy") or "local")[:64],
    })
    conn.commit()
    return get_session(conn, session_id)


def get_session(conn: sqlite3.Connection, session_id: int) -> dict[str, Any]:
    row = ExplorationSessionRepository(conn).get(session_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    return session_public(row)


def list_sessions(conn: sqlite3.Connection, filters: dict[str, Any],
                  page_num: int, page_size: int) -> dict[str, Any]:
    page = ExplorationSessionRepository(conn).list(filters, None, page_num=page_num, page_size=page_size)
    payload = page.as_dict()
    payload["list"] = [session_public(row) for row in payload["list"]]
    return payload


def delete_session(conn: sqlite3.Connection, session_id: int) -> None:
    repo = ExplorationSessionRepository(conn)
    row = repo.get(session_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    if str(row["status"]) in (SESSION_QUEUED, SESSION_RUNNING):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "探索进行中，不能删除")
    ExplorationStepRepository(conn).reset_for_run(session_id)
    repo.soft_delete(session_id)
    conn.commit()


def run_session(conn: sqlite3.Connection, session_id: int) -> dict[str, Any]:
    from . import job_queue

    row = ExplorationSessionRepository(conn).get(session_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    if str(row["status"]) in (SESSION_QUEUED, SESSION_RUNNING):
        raise AppError(ErrorCode.ILLEGAL_OPERATION, "探索已在排队/进行中")
    settings = json.loads(str(row["settings_json"] or "{}"))
    if not str(settings.get("mcpCommand") or "").strip():
        raise AppError(ErrorCode.ILLEGAL_OPERATION,
                       "未配置 MCP 服务命令，请先在「探索性测试设置」里填写 mcpCommand")
    ExplorationStepRepository(conn).reset_for_run(session_id)
    task = job_queue.submit(
        conn, kind="exploratory.run",
        title=f"Web 探索性测试：{row['name']}",
        payload={"sessionId": session_id}, priority=55,
    )
    ExplorationSessionRepository(conn).update(session_id, {
        "status": SESSION_QUEUED, "task_id": int(task["id"]),
        "step_done": 0, "anomaly_count": 0, "draft_count": 0,
        "error_message": "", "degraded": 0,
    })
    conn.commit()
    return get_session(conn, session_id)


def mark_cancelled(conn: sqlite3.Connection, session_id: int) -> None:
    ExplorationSessionRepository(conn).update(session_id, {
        "status": SESSION_CANCELLED, "finished_at": utcnow_iso(),
    })
    conn.commit()


# --------------------------------------------------------------------- 驱动决策


def resolve_driver(conn: sqlite3.Connection, requested: str) -> tuple[str, bool, str]:
    """model 驱动无可用模型时优雅降级为 scripted，返回（有效驱动, 是否降级, 说明）。"""
    if requested != DRIVER_MODEL:
        return DRIVER_SCRIPTED, False, ""
    try:
        candidates = resolve_candidates()
    except Exception:  # noqa: BLE001 - 解析失败按无模型处理
        candidates = []
    if candidates:
        return DRIVER_MODEL, False, ""
    return DRIVER_SCRIPTED, True, "未配置可用模型，已降级为确定性脚本驱动探索"


# --------------------------------------------------------------------- 脱敏


def mask_input(selector: str, value: str, enabled: bool) -> str:
    """输入值脱敏：选择器暗示凭证或命中敏感模式时整体打码，绝不留明文口令。"""
    text = str(value or "")
    if not enabled:
        return text
    hinted = bool(_SENSITIVE_SELECTOR_RE.search(selector or ""))
    _, counts = scan_sensitive(text)
    if hinted or counts:
        return "[输入已脱敏]"
    return text


# --------------------------------------------------------------------- 日志切片


def _parse_log_ts(line: str) -> datetime | None:
    match = _LOG_TS_RE.search(line)
    if not match:
        return None
    text = match.group(1).replace(",", ".")
    if " " in text and "T" not in text:
        text = text.replace(" ", "T", 1)
    return parse_iso(text)


def slice_log(path: str, start: datetime, end: datetime) -> dict[str, Any]:
    """按双锚点窗口切日志；路径不存在时如实标「路径失效」，不静默吞掉。"""
    file_path = Path(path)
    if not file_path.is_absolute() or not file_path.exists():
        return {"ok": False, "path": path, "note": "路径失效（日志文件不存在或非绝对路径）", "lines": []}
    kept: list[str] = []
    try:
        with file_path.open("r", encoding="utf-8", errors="replace") as handle:
            for line in handle:
                ts = _parse_log_ts(line)
                if ts is None:
                    continue
                if start <= ts <= end:
                    kept.append(line.rstrip("\n"))
    except OSError as exc:
        return {"ok": False, "path": path, "note": f"路径失效（读取失败：{exc}）", "lines": []}
    return {"ok": True, "path": path, "note": "", "lines": kept[:500]}


def build_log_slice(settings: dict[str, Any], detected_at: str, last_ok_at: str,
                    data_root: Path) -> dict[str, Any]:
    """双锚点 ±N 秒日志切片：起点取 min(detected_at, last_ok_at) - window。"""
    window = int(settings.get("logWindowSeconds") or 5)
    detected = parse_iso(detected_at) or datetime.now(timezone.utc)
    last_ok = parse_iso(last_ok_at) or detected
    anchor_start = min(detected, last_ok) - timedelta(seconds=window)
    anchor_end = max(detected, last_ok) + timedelta(seconds=window)
    sources = settings.get("logSources") or []
    slices: list[dict[str, Any]] = []
    all_lines: list[str] = []
    for source in sources:
        result = slice_log(str(source.get("path") or ""), anchor_start, anchor_end)
        slices.append({
            "name": source.get("name") or "", "path": result["path"],
            "ok": result["ok"], "note": result["note"], "lineCount": len(result["lines"]),
        })
        all_lines.extend(result["lines"])
    slice_text = "\n".join(all_lines)
    saved_path = ""
    if slice_text:
        slice_dir = data_root / str(settings.get("logSliceDir") or "exploratory/log-slices")
        slice_dir.mkdir(parents=True, exist_ok=True)
        stamp = detected.strftime("%Y%m%d%H%M%S%f")
        target = slice_dir / f"slice-{stamp}.log"
        target.write_text(slice_text, encoding="utf-8")
        saved_path = str(target)
    return {
        "windowSeconds": window,
        "anchorStart": anchor_start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "anchorEnd": anchor_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "detectedAt": detected_at,
        "lastOkAt": last_ok_at,
        "sources": slices,
        "text": slice_text[:execution_protocol.MAX_SUMMARY_CHARS],
        "filePath": saved_path,
    }


# --------------------------------------------------------------------- 异常判定


def detect_anomaly(snapshot: str, console: list[dict[str, Any]],
                   network: list[dict[str, Any]]) -> tuple[str, str]:
    """返回（异常类型, 说明）；无异常返回 ("", "")。优先级：接口>控制台>页面>业务码。"""
    bad_requests = [r for r in network if int(r.get("status") or 0) >= 400]
    if bad_requests:
        first = bad_requests[0]
        return ANOMALY_HTTP_ERROR, f"接口 {first.get('method') or 'GET'} {first.get('url')} 返回 {first.get('status')}"
    errors = [m for m in console if str(m.get("level") or "").lower() == "error"]
    if errors:
        return ANOMALY_CONSOLE_ERROR, f"控制台错误：{str(errors[0].get('text') or '')[:200]}"
    if _PAGE_ERROR_RE.search(snapshot or ""):
        return ANOMALY_PAGE_ERROR, "页面快照含报错信息"
    code_match = _BUSINESS_CODE_RE.search(snapshot or "")
    if code_match:
        return ANOMALY_BUSINESS_CODE, f"业务码异常 code={code_match.group(1)}"
    return "", ""


# --------------------------------------------------------------------- 执行


def execute_session(
    conn: sqlite3.Connection,
    session_id: int,
    db_path: Path,
    *,
    on_progress: Callable[[int, int], None] | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    session_repo = ExplorationSessionRepository(conn)
    step_repo = ExplorationStepRepository(conn)
    row = session_repo.get(session_id)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "探索会话不存在")
    settings = json.loads(str(row["settings_json"] or "{}"))
    plan = json.loads(str(row["plan_json"] or "[]"))
    project_id = int(row["project_id"])
    data_root = db_path.parent

    effective_driver, degraded, degrade_note = resolve_driver(conn, str(row["driver"]))
    session_repo.update(session_id, {
        "status": SESSION_RUNNING, "started_at": utcnow_iso(),
        "degraded": 1 if degraded else 0,
    })
    conn.commit()

    max_steps = int(row["max_steps"])
    max_seconds = int(row["max_seconds"])
    deadline = datetime.now(timezone.utc) + timedelta(seconds=max_seconds)
    steps = plan[:max_steps] if effective_driver == DRIVER_SCRIPTED else plan[:max_steps]
    total = len(steps)
    session_repo.update(session_id, {"step_total": total})
    conn.commit()

    client: McpClient | None = None
    anomaly_count = 0
    draft_count = 0
    dedup_skipped = 0
    last_ok_at = utcnow_iso()
    done = 0
    notes: list[str] = []
    if degrade_note:
        notes.append(degrade_note)

    try:
        client = McpClient(str(settings.get("mcpCommand") or ""),
                           timeout=float(settings.get("perStepTimeout") or 30))
        client.initialize()
        client.ensure_pinned()
        # 起步导航到目标地址
        client.call_tool("browser_navigate", {"url": str(row["target_url"])})

        for index, step in enumerate(steps, start=1):
            if cancel_check and cancel_check():
                from . import job_queue
                raise job_queue.TaskCancelled()
            if datetime.now(timezone.utc) > deadline:
                notes.append(f"达到最大时长 {max_seconds}s，提前收敛")
                break
            done = index
            anomaly_count, draft_count, dedup_skipped, last_ok_at = _run_step(
                conn, client, settings, row, step, index, data_root,
                last_ok_at, anomaly_count, draft_count, dedup_skipped,
            )
            session_repo.update(session_id, {
                "step_done": done, "anomaly_count": anomaly_count, "draft_count": draft_count,
            })
            conn.commit()
            if on_progress:
                on_progress(done, total)
    except Exception as exc:  # noqa: BLE001 - 收口为失败状态再抛
        conn.rollback()
        session_repo.update(session_id, {
            "status": SESSION_FAILED, "finished_at": utcnow_iso(),
            "error_message": str(exc)[:2000],
        })
        conn.commit()
        raise
    finally:
        if client is not None:
            client.close()

    summary = {
        "sessionId": session_id,
        "driver": effective_driver,
        "degraded": degraded,
        "steps": done,
        "anomalies": anomaly_count,
        "drafts": draft_count,
        "dedupSkipped": dedup_skipped,
        "notes": notes,
    }
    session_repo.update(session_id, {
        "status": SESSION_DONE, "finished_at": utcnow_iso(),
        "summary_json": json.dumps(summary, ensure_ascii=False),
    })
    conn.commit()
    return summary


def _run_step(
    conn: sqlite3.Connection,
    client: McpClient,
    settings: dict[str, Any],
    session: dict[str, Any],
    step: dict[str, Any],
    index: int,
    data_root: Path,
    last_ok_at: str,
    anomaly_count: int,
    draft_count: int,
    dedup_skipped: int,
) -> tuple[int, int, int, str]:
    action = str(step.get("action") or "snapshot")
    selector = str(step.get("selector") or "")
    record_selectors = bool(settings.get("recordSelectors", True))
    started = utcnow_iso()
    t0 = datetime.now(timezone.utc)

    if action == "navigate":
        client.call_tool("browser_navigate", {"url": str(step.get("url") or session["target_url"])})
    elif action == "click":
        client.call_tool("browser_click", {"selector": selector})
    elif action == "type":
        client.call_tool("browser_type", {"selector": selector, "value": str(step.get("value") or "")})
    # snapshot / observe / screenshot 都只读快照

    snap = client.call_tool("browser_snapshot", {})
    snapshot_text = str(snap.get("snapshot") or snap.get("text") or "")
    page_url = str(snap.get("url") or session["target_url"])
    console = _as_list(client.call_tool("browser_console_messages", {}).get("messages"))
    network = _as_list(client.call_tool("browser_network_requests", {}).get("requests"))

    anomaly_type, observation = detect_anomaly(snapshot_text, console, network)
    is_anomaly = bool(anomaly_type)
    detected_at = utcnow_iso() if is_anomaly else ""
    duration_ms = int((datetime.now(timezone.utc) - t0).total_seconds() * 1000)
    input_masked = mask_input(selector, str(step.get("value") or ""),
                              bool(settings.get("inputMasking", True))) if action == "type" else ""

    step_repo = ExplorationStepRepository(conn)
    step_id = step_repo.insert({
        "session_id": int(session["id"]),
        "step_no": index,
        "action": action,
        "selector": selector if record_selectors else "",
        "page_url": page_url[:1000],
        "input_masked": input_masked[:500],
        "snapshot_summary": execution_protocol.summarize(snapshot_text, 1000),
        "observation": observation[:2000],
        "is_anomaly": 1 if is_anomaly else 0,
        "anomaly_type": anomaly_type,
        "detected_at": detected_at,
        "last_ok_at": last_ok_at if is_anomaly else "",
        "trace_id": str(snap.get("traceId") or "")[:64],
        "duration_ms": duration_ms,
        "detail_json": "{}",
    })
    conn.commit()

    if not is_anomaly:
        return anomaly_count, draft_count, dedup_skipped, detected_at or utcnow_iso()

    anomaly_count += 1
    evidence_id, draft_id, skipped = _capture_anomaly(
        conn, client, settings, session, step_id, index, action, selector,
        snapshot_text, console, network, anomaly_type, observation,
        detected_at, last_ok_at, data_root,
    )
    detail = {
        "console": console[:50],
        "network": network[:50],
        "anomalyType": anomaly_type,
    }
    step_repo.update(step_id, {
        "evidence_id": evidence_id, "draft_id": draft_id,
        "detail_json": json.dumps(detail, ensure_ascii=False),
    })
    conn.commit()
    if skipped:
        dedup_skipped += 1
    if draft_id:
        draft_count += 1
    return anomaly_count, draft_count, dedup_skipped, last_ok_at


def _capture_anomaly(
    conn: sqlite3.Connection,
    client: McpClient,
    settings: dict[str, Any],
    session: dict[str, Any],
    step_id: int,
    index: int,
    action: str,
    selector: str,
    snapshot_text: str,
    console: list[dict[str, Any]],
    network: list[dict[str, Any]],
    anomaly_type: str,
    observation: str,
    detected_at: str,
    last_ok_at: str,
    data_root: Path,
) -> tuple[int, int | None, bool]:
    # 异常强制 3 张截图
    screenshots: list[dict[str, Any]] = []
    shot_dir = data_root / str(settings.get("screenshotDir") or "exploratory/screenshots")
    shot_dir = shot_dir / str(session["session_no"])
    shot_dir.mkdir(parents=True, exist_ok=True)
    for i in range(ANOMALY_SCREENSHOTS):
        shot = client.call_tool("browser_screenshot", {"step": index, "index": i})
        file_name = str(shot.get("fileName") or f"step{index}-{i + 1}.png")
        file_path = shot_dir / file_name
        content = shot.get("content") or shot.get("data") or ""
        try:
            file_path.write_bytes(base64.b64decode(content) if content else b"")
        except (ValueError, OSError):
            file_path.write_bytes(b"")
        screenshots.append({
            "fileName": file_name, "filePath": str(file_path), "kind": "screenshot",
        })

    log_slice = build_log_slice(settings, detected_at, last_ok_at, data_root)
    trace_id = ""
    for item in network:
        if item.get("traceId"):
            trace_id = str(item["traceId"])[:64]
            break

    log_summary = log_slice["text"] or "；".join(
        f"{s['name'] or s['path']}：{s['note']}" for s in log_slice["sources"]
    ) or "未配置日志来源，无日志切片"
    result = execution_protocol.ExecutionResult(
        source_type=execution_protocol.SOURCE_EXPLORATORY,
        source_row_id=step_id,
        project_id=int(session["project_id"]),
        title=f"{session['name']} / 第{index}步 {ANOMALY_LABELS.get(anomaly_type, anomaly_type)}",
        result="failed",
        duration_ms=0,
        log_summary=log_summary,
        request_summary=execution_protocol.summarize(network, 1500),
        response_summary=execution_protocol.summarize(console, 1500),
        attachments=screenshots,
        source_ref={
            "table": "biz_exploration_step", "id": step_id,
            "sessionId": int(session["id"]), "targetUrl": str(session["target_url"]),
            "anomalyType": anomaly_type, "detectedAt": detected_at, "lastOkAt": last_ok_at,
            "logWindowSeconds": log_slice["windowSeconds"],
            "logSliceFile": log_slice["filePath"],
        },
        trace_id=trace_id,
        error_message=observation,
        executed_by=str(session.get("created_by") or "exploratory"),
        executed_at=detected_at,
    )
    evidence_id = execution_protocol.capture(conn, result)
    conn.commit()

    draft_id, skipped = _create_product_draft(
        conn, session, step_id, index, action, selector, anomaly_type,
        observation, evidence_id, screenshots, log_slice,
    )
    return evidence_id, draft_id, skipped


def _create_product_draft(
    conn: sqlite3.Connection,
    session: dict[str, Any],
    step_id: int,
    index: int,
    action: str,
    selector: str,
    anomaly_type: str,
    observation: str,
    evidence_id: int,
    screenshots: list[dict[str, Any]],
    log_slice: dict[str, Any],
) -> tuple[int | None, bool]:
    project_id = int(session["project_id"])
    title = f"[探索性] {ANOMALY_LABELS.get(anomaly_type, anomaly_type)}：{observation[:80]}"
    body_lines = [
        f"探索会话：{session['session_no']} {session['name']}",
        f"目标地址：{session['target_url']}",
        f"异常步骤：第 {index} 步（{action}）",
        f"选择器：{selector or '（未记录）'}",
        f"异常类型：{ANOMALY_LABELS.get(anomaly_type, anomaly_type)}",
        f"观测：{observation}",
        f"证据编号：EV-{evidence_id}",
        f"日志切片窗口：{log_slice['anchorStart']} ~ {log_slice['anchorEnd']}（±{log_slice['windowSeconds']}s 双锚点）",
        f"截图：{len(screenshots)} 张",
    ]
    content = "\n".join(body_lines)

    # GAP-29 去重：同项目（+模块）标题/步骤相似度命中则跳过，除非显式覆盖
    comparable = dedup_service.ComparableCase(
        ref=f"step-{step_id}", title=title, body=content, case_type="exploratory",
    )
    if _is_duplicate(conn, project_id, comparable):
        return None, True

    draft_repo = KnowledgeDraftRepository(conn)
    draft_id = draft_repo.create_draft(
        title, content,
        summary=observation[:500],
        category="踩坑记录",
        project_id=project_id,
        source_type="exploratory",
        ref_type="exploration_step",
        ref_id=step_id,
        ref_label=str(session.get("name") or "")[:200],
        producer="exploratory_web",
        review_required=True,
    )
    conn.commit()
    return draft_id, False


def _is_duplicate(conn: sqlite3.Connection, project_id: int,
                  comparable: dedup_service.ComparableCase) -> bool:
    """与既有探索性待审产物 / 缺陷比对标题+正文，命中疑似阈值即判重。"""
    candidates: list[dedup_service.ComparableCase] = []
    draft_rows = conn.execute(
        "SELECT title, content FROM biz_knowledge_draft"
        " WHERE project_id = ? AND source_type = 'exploratory' AND is_deleted = 0"
        " ORDER BY id DESC LIMIT 60",
        (project_id,),
    ).fetchall()
    for row in draft_rows:
        candidates.append(dedup_service.ComparableCase(
            ref=f"draft-{row['title']}", title=str(row["title"] or ""),
            body=str(row["content"] or ""), case_type="exploratory",
        ))
    bug_rows = conn.execute(
        "SELECT bug_no, title, description FROM biz_bug"
        " WHERE project_id = ? AND is_deleted = 0 ORDER BY id DESC LIMIT 60",
        (project_id,),
    ).fetchall()
    for row in bug_rows:
        candidates.append(dedup_service.ComparableCase(
            ref=str(row["bug_no"] or ""), title=str(row["title"] or ""),
            body=str(row["description"] or ""), case_type="exploratory",
        ))
    for other in candidates:
        if dedup_service.similarity(comparable, other) >= dedup_service.SUSPECT_THRESHOLD:
            return True
    return False


def _as_list(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


# --------------------------------------------------------------------- 读取


def session_detail(conn: sqlite3.Connection, session_id: int) -> dict[str, Any]:
    session = get_session(conn, session_id)
    steps = [step_public(row) for row in ExplorationStepRepository(conn).list_by_session(session_id)]
    return {"session": session, "steps": steps}


def step_evidence(conn: sqlite3.Connection, step_id: int) -> dict[str, Any] | None:
    return execution_protocol.by_source(conn, execution_protocol.SOURCE_EXPLORATORY, step_id)