"""数据库脚本资产仓储。

一张表 biz_sql_script：SQL 脚本库。
脚本编号前缀 SQL（SQL-001 …），别称（alias）供其他页面 / 场景快速引用。
变量表（variables，JSON 数组 [{name,value}]）执行前并入调用方变量池，
再经 interface_runner._substitute 把正文里的 {{var}} 渲染成最终语句。

连接信息不在脚本里：脚本只绑定 biz_test_env（执行环境），
数据库口令本体在 OS 密钥库，复用现有检查点引擎（db_checkpoint_service）。
"""

from __future__ import annotations

from typing import Any

from .base import BaseRepository, loads
from .numbering import begin_claim

SCRIPT_NO_PREFIX = "SQL"
#: AI 实验室一键入库的 SQL 脚本用独立编号，AI_SQL-xxx 一眼可区分来源
SCRIPT_NO_PREFIX_AI = "AI_SQL"

SCRIPT_STATUSES = ("ready", "disabled")


def sql_script_summary(row: dict[str, Any]) -> dict[str, Any]:
    """对外输出口径：与 automation 的 script_summary 同构，前端列表可直接用。"""
    return {
        "id": row.get("id"),
        "scriptNo": row.get("script_no"),
        "name": row.get("name"),
        "alias": row.get("alias") or "",
        "content": row.get("content") or "",
        "variables": loads(row.get("variables"), []) or [],
        "envId": row.get("env_id"),
        "projectId": row.get("project_id"),
        "description": row.get("description") or "",
        "status": row.get("status") or "ready",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


class SqlScriptRepository(BaseRepository):
    table = "biz_sql_script"
    default_order = "id"
    default_order_dir = "desc"
    order_whitelist = {"id", "script_no", "name", "alias", "status",
                       "created_at", "updated_at"}
    searchable = ["name", "script_no", "alias", "description"]

    def next_script_no(self, ai: bool = False) -> str:
        """编号 SQL-001 递增；AI 一键入库走 AI_SQL-001 独立序列。

        软删可复用，取号与插入同处一个写事务。
        """
        prefix = SCRIPT_NO_PREFIX_AI if ai else SCRIPT_NO_PREFIX
        begin_claim(self.conn)
        rows = self.conn.execute(
            "SELECT script_no FROM biz_sql_script WHERE is_deleted = 0"
        ).fetchall()
        used: set[int] = set()
        for row in rows:
            value = str(row[0] or "")
            if value.startswith(prefix + "-"):
                tail = value[len(prefix) + 1:]
                if tail.isdigit():
                    used.add(int(tail))
        index = 1
        while index in used:
            index += 1
        return f"{prefix}-{index:03d}"

    def find_by_alias(self, alias: str, project_id: int | None = None,
                      exclude_id: int | None = None) -> dict[str, Any] | None:
        """按别称定位（供其他页面 / 场景按引用取脚本）。"""
        sql = "SELECT * FROM biz_sql_script WHERE alias = ? AND is_deleted = 0"
        params: list[Any] = [alias]
        if project_id:
            sql += " AND project_id = ?"
            params.append(int(project_id))
        if exclude_id:
            sql += " AND id <> ?"
            params.append(int(exclude_id))
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def find_by_no(self, script_no: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM biz_sql_script WHERE script_no = ? AND is_deleted = 0",
            (script_no,),
        ).fetchone()
        return dict(row) if row else None

    def count_by_project(self, project_id: int | None = None) -> int:
        sql = "SELECT COUNT(*) FROM biz_sql_script WHERE is_deleted = 0"
        params: list[Any] = []
        if project_id:
            sql += " AND project_id = ?"
            params.append(int(project_id))
        return int(self.conn.execute(sql, params).fetchone()[0])

    def scenario_refs(self, tokens: list[str]) -> list[dict[str, Any]]:
        """场景 steps_json 里按编号/别称引用到本脚本的行清单。"""
        out: list[dict[str, Any]] = []
        for token in tokens:
            rows = self.conn.execute(
                "SELECT id, name, scenario_no FROM biz_api_scenario WHERE is_deleted = 0"
                " AND steps_json LIKE ?", (f"%{token}%",)
            ).fetchall()
            for row in rows:
                if not any(item.get("id") == row[0] for item in out):
                    out.append({
                        "kind": "scenario", "id": row[0], "name": row[1],
                        "refNo": str(row[2] or ""),
                    })
        return out

    def automation_refs(self, script_no: str) -> list[dict[str, Any]]:
        """自动化脚本 pre/post/断言 三个引用字段里出现本编号的行清单。"""
        rows = self.conn.execute(
            "SELECT id, name, script_no FROM biz_automation_script WHERE is_deleted = 0"
            " AND (pre_db_refs LIKE ? OR post_db_refs LIKE ? OR db_assert_refs LIKE ?)",
            (f"%{script_no}%", f"%{script_no}%", f"%{script_no}%"),
        ).fetchall()
        return [
            {"kind": "automation", "id": row[0], "name": row[1], "refNo": str(row[2] or "")}
            for row in rows
        ]

    def refs_of(self, target_id: int) -> list[dict[str, Any]]:
        row = self.conn.execute(
            "SELECT script_no, alias, name FROM biz_sql_script WHERE id = ?",
            (target_id,),
        ).fetchone()
        if row is None:
            return []
        script_no, alias = str(row[0] or ""), str(row[1] or "")
        out = self.automation_refs(script_no)
        tokens = [t for t in {script_no, alias} if t]
        out.extend(self.scenario_refs(tokens))
        return out
