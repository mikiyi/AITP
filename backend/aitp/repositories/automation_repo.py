"""自动化脚本资产仓储。

两张表：
  biz_automation_script —— 脚本资产（web / app / api / performance 四类）
  biz_automation_run   —— 执行留痕

脚本编号前缀按类别区分：AUTO-W / AUTO-A / AUTO-I / PERF，
这样看一眼编号就知道是哪种脚本。
"""

from __future__ import annotations

import sqlite3
from typing import Any

from ..common.pagination import Page, RowDict
from .base import BaseRepository

#: 四类脚本
CATEGORY_WEB = "web"
CATEGORY_APP = "app"
CATEGORY_API = "api"
CATEGORY_PERFORMANCE = "performance"

SCRIPT_CATEGORIES = (CATEGORY_WEB, CATEGORY_APP, CATEGORY_API, CATEGORY_PERFORMANCE)

#: 类别 → 编号前缀
CATEGORY_PREFIX = {
    CATEGORY_WEB: "AUTO-W",
    CATEGORY_APP: "AUTO-A",
    CATEGORY_API: "AUTO-I",
    CATEGORY_PERFORMANCE: "PERF",
}

#: 类别 → 默认文件名后缀
CATEGORY_SUFFIX = {
    CATEGORY_WEB: ".py",
    CATEGORY_APP: ".py",
    CATEGORY_API: ".py",
    CATEGORY_PERFORMANCE: ".jmx",
}

#: 类别中文名
CATEGORY_LABEL = {
    CATEGORY_WEB: "Web 自动化",
    CATEGORY_APP: "App 自动化",
    CATEGORY_API: "接口自动化",
    CATEGORY_PERFORMANCE: "性能测试",
}

#: 执行方式
RUN_MODE_SCRIPT = "script"      # 直接 python 执行脚本
RUN_MODE_PYTEST = "pytest"      # pytest 执行
RUN_MODE_JMETER = "jmeter"      # JMeter 命令行执行 .jmx

RUN_MODES = (RUN_MODE_SCRIPT, RUN_MODE_PYTEST, RUN_MODE_JMETER)

#: 执行状态
RUN_STATUS_RUNNING = "running"
RUN_STATUS_SUCCESS = "success"
RUN_STATUS_FAILED = "failed"
RUN_STATUS_TIMEOUT = "timeout"
RUN_STATUS_ERROR = "error"

RUN_STATUSES = (
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    RUN_STATUS_FAILED,
    RUN_STATUS_TIMEOUT,
    RUN_STATUS_ERROR,
)

#: 脚本状态
SCRIPT_STATUS_READY = "ready"
SCRIPT_STATUS_DISABLED = "disabled"

SCRIPT_STATUSES = (SCRIPT_STATUS_READY, SCRIPT_STATUS_DISABLED)


class AutomationScriptRepository(BaseRepository):
    table = "biz_automation_script"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {
        "id", "script_no", "name", "category", "framework", "language",
        "status", "last_run_status", "last_run_at", "created_at", "updated_at",
    }
    searchable = ["name", "script_no", "description", "framework", "file_name"]

    def next_script_no(self, category: str) -> str:
        """按类别前缀取下一个可用编号；软删的编号可以被复用。"""
        prefix = CATEGORY_PREFIX.get(category, "AUTO")
        rows = self.conn.execute(
            "SELECT script_no FROM biz_automation_script WHERE is_deleted = 0"
        ).fetchall()
        used: set[int] = set()
        for row in rows:
            value = str(row[0] or "")
            head, _, seq = value.rpartition("-")
            if head.upper() != prefix.upper() or not seq.isdigit():
                continue
            used.add(int(seq))
        index = 1
        while index in used:
            index += 1
        return f"{prefix}-{index:03d}"

    def count_by_category(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT category, COUNT(*) AS c FROM biz_automation_script"
            " WHERE is_deleted = 0 GROUP BY category"
        ).fetchall()
        counts = {category: 0 for category in SCRIPT_CATEGORIES}
        for row in rows:
            key = str(row["category"] or "")
            if key in counts:
                counts[key] = int(row["c"])
        return counts


class AutomationRunRepository(BaseRepository):
    table = "biz_automation_run"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {
        "id", "run_no", "script_id", "category", "status",
        "duration_ms", "started_at", "finished_at", "created_at",
    }
    searchable = ["run_no", "script_no", "script_name"]

    def next_run_no(self) -> str:
        rows = self.conn.execute(
            "SELECT run_no FROM biz_automation_run WHERE is_deleted = 0"
        ).fetchall()
        used: set[int] = set()
        for row in rows:
            value = str(row[0] or "")
            head, _, seq = value.rpartition("-")
            if head.upper() != "RUN" or not seq.isdigit():
                continue
            used.add(int(seq))
        index = 1
        while index in used:
            index += 1
        return f"RUN-{index:03d}"

    def list_for_script(self, script_id: int, page_num: int = 1, page_size: int = 20) -> Page:
        """某个脚本的执行历史。"""
        return self.list({"script_id": script_id}, None, page_num=page_num, page_size=page_size)

    def latest_for_script(self, script_id: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_automation_run WHERE script_id = ? AND is_deleted = 0"
            " ORDER BY id DESC LIMIT 1",
            (script_id,),
        ).fetchone()
        return dict(row) if row else None

    def stats_by_category(self) -> dict[str, dict[str, int]]:
        """四类脚本各自的执行统计：总数 / 成功 / 失败。"""
        rows = self.conn.execute(
            "SELECT category, COUNT(*) AS total,"
            " SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) AS success,"
            " SUM(CASE WHEN status IN ('failed','error','timeout') THEN 1 ELSE 0 END) AS failed"
            " FROM biz_automation_run WHERE is_deleted = 0 GROUP BY category"
        ).fetchall()
        stats = {
            category: {"total": 0, "success": 0, "failed": 0}
            for category in SCRIPT_CATEGORIES
        }
        for row in rows:
            key = str(row["category"] or "")
            if key not in stats:
                continue
            stats[key] = {
                "total": int(row["total"] or 0),
                "success": int(row["success"] or 0),
                "failed": int(row["failed"] or 0),
            }
        return stats


def script_summary(row: dict[str, Any]) -> dict[str, Any]:
    """接口出参统一驼峰。"""
    category = str(row.get("category") or CATEGORY_WEB)
    return {
        "id": row.get("id"),
        "scriptNo": row.get("script_no"),
        "name": row.get("name"),
        "category": category,
        "categoryLabel": CATEGORY_LABEL.get(category, category),
        "targetType": row.get("target_type") or "",
        "framework": row.get("framework") or "",
        "language": row.get("language") or "",
        "appPlatforms": row.get("app_platforms") or "",
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "description": row.get("description") or "",
        "fileName": row.get("file_name") or "",
        "content": row.get("content") or "",
        "source": row.get("source") or "manual",
        "runMode": row.get("run_mode") or RUN_MODE_SCRIPT,
        "entryCommand": row.get("entry_command") or "",
        "timeoutSeconds": row.get("timeout_seconds"),
        "status": row.get("status") or SCRIPT_STATUS_READY,
        "lastRunStatus": row.get("last_run_status") or "",
        "lastRunAt": row.get("last_run_at") or "",
        "remark": row.get("remark") or "",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def run_summary(row: dict[str, Any]) -> dict[str, Any]:
    """执行记录出参；标准输出与错误各截断，避免超大响应。"""
    return {
        "id": row.get("id"),
        "runNo": row.get("run_no"),
        "scriptId": row.get("script_id"),
        "scriptNo": row.get("script_no") or "",
        "scriptName": row.get("script_name") or "",
        "category": row.get("category") or "",
        "categoryLabel": CATEGORY_LABEL.get(str(row.get("category") or ""), ""),
        "status": row.get("status") or "",
        "exitCode": row.get("exit_code"),
        "durationMs": row.get("duration_ms"),
        "commandLine": row.get("command_line") or "",
        "stdout": row.get("stdout") or "",
        "stderr": row.get("stderr") or "",
        "errorMessage": row.get("error_message") or "",
        "startedAt": row.get("started_at"),
        "finishedAt": row.get("finished_at"),
        "createdAt": row.get("created_at"),
    }
