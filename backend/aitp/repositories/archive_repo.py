"""归档包裹与归档条目索引（三期 GAP-44）。

不解压就能检索：条目索引冗余标题/时间/摘要；
回看原文才按 ref_id 打开 zip 的 items.jsonl 逐行定位。
"""

from __future__ import annotations

from ..common.pagination import RowDict
from .base import BaseRepository


class ArchiveBundleRepository(BaseRepository):
    table = "biz_archive_bundle"
    default_order = "id"
    order_whitelist = {"id", "data_type", "created_at"}
    searchable = ["bundle_no", "data_type"]


class ArchiveItemRepository(BaseRepository):
    table = "biz_archive_item"
    default_order = "id"
    order_whitelist = {"id", "bundle_id", "data_type", "ref_id", "happened_at"}
    searchable = ["title", "summary"]

    def list_by_bundle(self, bundle_id: int) -> list[RowDict]:
        rows = self.conn.execute(
            "SELECT * FROM biz_archive_item WHERE bundle_id = ? AND is_deleted = 0 ORDER BY id ASC",
            (int(bundle_id),),
        ).fetchall()
        return [dict(row) for row in rows]


def bundle_public(row: RowDict) -> RowDict:
    return {
        "id": int(row["id"]),
        "bundleNo": str(row.get("bundle_no") or ""),
        "dataType": str(row.get("data_type") or ""),
        "action": str(row.get("action") or ""),
        "itemCount": int(row.get("item_count") or 0),
        "periodFrom": str(row.get("period_from") or ""),
        "periodTo": str(row.get("period_to") or ""),
        "filePath": str(row.get("file_path") or ""),
        "fileSize": int(row.get("file_size") or 0),
        "createdBy": str(row.get("created_by") or ""),
        "createdAt": row.get("created_at"),
    }


def item_public(row: RowDict) -> RowDict:
    return {
        "id": int(row["id"]),
        "bundleId": int(row.get("bundle_id") or 0),
        "dataType": str(row.get("data_type") or ""),
        "refId": int(row.get("ref_id") or 0),
        "title": str(row.get("title") or ""),
        "happenedAt": str(row.get("happened_at") or ""),
        "summary": str(row.get("summary") or ""),
    }
