"""测试数据生成批次仓储。

批次存生成规则（格式 / 大小 / 命名 / 数量），明细存每个落盘文件的实际字节数，
文件本体留在 data/generated/<dir_name>/ 下，库里只记相对路径。
"""

from __future__ import annotations

from typing import Any

from ..common.pagination import RowDict
from .base import BaseRepository
from .numbering import next_no

#: 文件名序号位：与自增数字 / 时间戳组合成文件名
TOKEN_MODES = ("none", "sequence", "timestamp", "timestamp_sequence")
TOKEN_LABELS = {
    "none": "不带编号",
    "sequence": "自增数字",
    "timestamp": "时间戳",
    "timestamp_sequence": "时间戳 + 自增数字",
}


def decorate_batch(row: RowDict) -> dict[str, Any]:
    token = str(row.get("token_mode") or "none")
    return {
        "id": row.get("id"),
        "projectId": row.get("project_id"),
        "moduleId": row.get("module_id"),
        "versionId": row.get("version_id"),
        "batchNo": row.get("batch_no"),
        "ext": row.get("ext"),
        "formatLabel": row.get("format_label") or "",
        "buildMode": row.get("build_mode") or "",
        "sizeBytes": row.get("size_bytes"),
        "fileCount": row.get("file_count"),
        "createdFiles": row.get("created_files"),
        "totalBytes": row.get("total_bytes"),
        "namePrefix": row.get("name_prefix") or "",
        "nameSuffix": row.get("name_suffix") or "",
        "tokenMode": token,
        "tokenLabel": TOKEN_LABELS.get(token, token),
        "dirName": row.get("dir_name") or "",
        "remark": row.get("remark") or "",
        "createdAt": row.get("created_at"),
        "updatedAt": row.get("updated_at"),
    }


def decorate_file(row: RowDict) -> dict[str, Any]:
    return {
        "id": row.get("id"),
        "batchId": row.get("batch_id"),
        "fileName": row.get("file_name"),
        "ext": row.get("ext"),
        "sizeBytes": row.get("size_bytes"),
        "exact": bool(row.get("exact")),
        "note": row.get("note") or "",
        "relPath": row.get("rel_path"),
        "createdAt": row.get("created_at"),
    }


class TestDataBatchRepository(BaseRepository):
    table = "biz_test_data_batch"
    default_order = "created_at"
    order_whitelist = {"id", "batch_no", "ext", "file_count", "total_bytes", "created_at"}
    searchable = ["batch_no", "ext", "format_label", "remark"]

    def next_batch_no(self) -> str:
        # 批次号在库里是全局唯一，生成目录也是全局的（generated/<batch_no>），
        # 不能按项目分段计数，否则不同项目会撞号触发唯一索引冲突。
        return next_no(self.conn, self.table, "batch_no", "TD")


class TestDataFileRepository(BaseRepository):
    table = "biz_test_data_file"
    default_order = "id"
    default_order_dir = "asc"
    order_whitelist = {"id", "file_name", "size_bytes", "created_at"}
    searchable = ["file_name", "ext"]

    def list_by_batch(self, batch_id: int) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM biz_test_data_file WHERE batch_id = ? AND is_deleted = 0 ORDER BY id",
            (batch_id,),
        ).fetchall()
        return [dict(row) for row in rows]

    def delete_by_batch(self, batch_id: int) -> int:
        cursor = self.conn.execute(
            "DELETE FROM biz_test_data_file WHERE batch_id = ?", (batch_id,)
        )
        return int(cursor.rowcount)
