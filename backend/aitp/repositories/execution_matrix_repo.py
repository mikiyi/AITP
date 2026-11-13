"""执行矩阵仓储（三期 GAP-43）。

矩阵、格子、格子结果三张表都只做留痕与对比，不承载执行逻辑；
真正的请求/脚本执行仍由 interface_runner / automation_service 完成。
"""

from __future__ import annotations

from ..common.pagination import RowDict
from ..common.time_utils import utcnow_iso
from .base import BaseRepository


class ExecutionMatrixRepository(BaseRepository):
    table = "biz_execution_matrix"
    default_order = "id"
    order_whitelist = {"id", "name", "status", "created_at", "updated_at"}
    searchable = ["matrix_no", "name"]


class ExecutionMatrixCellRepository(BaseRepository):
    table = "biz_execution_matrix_cell"
    default_order = "id"
    order_whitelist = {"id", "env_id", "target", "status", "created_at"}

    def list_by_matrix(self, matrix_id: int) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM biz_execution_matrix_cell"
            " WHERE matrix_id = ? AND is_deleted = 0 ORDER BY id ASC",
            (int(matrix_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def get_of_matrix(self, matrix_id: int, cell_id: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_execution_matrix_cell"
            " WHERE id = ? AND matrix_id = ? AND is_deleted = 0",
            (int(cell_id), int(matrix_id)),
        ).fetchone()
        return dict(row) if row else None

    def reset_for_run(self, matrix_id: int) -> None:
        """重跑前把格子与旧结果一起作废，避免上一轮结论混进本轮对比。"""
        self.conn.execute(
            "UPDATE biz_execution_matrix_result SET is_deleted = 1"
            " WHERE matrix_id = ? AND is_deleted = 0",
            (int(matrix_id),),
        )
        self.conn.execute(
            "UPDATE biz_execution_matrix_cell SET status = 'pending', task_id = NULL,"
            " total = 0, passed = 0, failed = 0, error_message = '',"
            " started_at = NULL, finished_at = NULL"
            " WHERE matrix_id = ? AND is_deleted = 0",
            (int(matrix_id),),
        )


class ExecutionMatrixResultRepository(BaseRepository):
    table = "biz_execution_matrix_result"
    default_order = "id"
    order_whitelist = {"id", "case_id", "result", "created_at"}

    def list_by_matrix(self, matrix_id: int) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM biz_execution_matrix_result"
            " WHERE matrix_id = ? AND is_deleted = 0 ORDER BY id ASC",
            (int(matrix_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def list_by_cell(self, cell_id: int) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM biz_execution_matrix_result"
            " WHERE cell_id = ? AND is_deleted = 0 ORDER BY id ASC",
            (int(cell_id),),
        ).fetchall()
        return [dict(row) for row in rows]

    def mark_bug(self, matrix_id: int, case_no: str, bug_id: int) -> int:
        cursor = self.conn.execute(
            "UPDATE biz_execution_matrix_result SET bug_id = ?, updated_at = ?"
            " WHERE matrix_id = ? AND case_no = ? AND is_deleted = 0",
            (int(bug_id), utcnow_iso(), int(matrix_id), str(case_no)),
        )
        return int(cursor.rowcount or 0)
