"""测试环境配置仓储。

一行 = 一套命名环境（开发 / 测试 / 预发…），带应用服务器与数据库服务器
两组目标信息。数据库口令本体进 OS 密钥库（ref=test_env:<id>），库内只存
掩码与引用。整套配置只服务本机的环境健康监测，绝不参与大模型调用。
"""

from __future__ import annotations

from typing import Any

from ..common.secret_store import get_store
from ..common.security import mask
from ..common.pagination import RowDict, RowList
from .base import BaseRepository

#: 支持的数据库类型；健康监测按类型决定默认端口与探测方式
DB_TYPES: tuple[str, ...] = ("mysql", "postgresql", "sqlserver", "oracle", "sqlite", "other")

#: 环境类型：生产环境执行前需要二次确认
ENV_KINDS: tuple[str, ...] = ("dev", "test", "staging", "prod")
ENV_KIND_LABELS: dict[str, str] = {
    "dev": "开发",
    "test": "测试",
    "staging": "预发",
    "prod": "生产",
}

#: 各数据库类型的默认端口，前端选类型时自动带出
DB_DEFAULT_PORTS: dict[str, int] = {
    "mysql": 3306,
    "postgresql": 5432,
    "sqlserver": 1433,
    "oracle": 1521,
}


class TestEnvRepository(BaseRepository):
    table = "biz_test_env"
    soft_delete_enabled = True
    default_order = "sort_order, id"
    default_order_dir = "asc"
    order_whitelist = {"id", "name", "db_type", "is_default", "sort_order", "created_at", "updated_at"}
    searchable = ["name", "description", "app_base_url", "db_host"]

    def get_by_id(self, pk: int) -> RowDict | None:
        row = self.conn.execute(
            "SELECT * FROM biz_test_env WHERE id = ? AND is_deleted = 0", (pk,)
        ).fetchone()
        return dict(row) if row else None

    def get_default(self) -> RowDict | None:
        """首页健康监测默认监测的环境；没设默认就取排序第一个。"""
        row = self.conn.execute(
            """
            SELECT * FROM biz_test_env
            WHERE is_deleted = 0
            ORDER BY is_default DESC, sort_order ASC, id ASC
            LIMIT 1
            """
        ).fetchone()
        return dict(row) if row else None

    def clear_other_defaults(self, keep_id: int) -> None:
        """保证「默认环境」全局唯一，否则首页不知道该监测哪套。"""
        self.conn.execute(
            "UPDATE biz_test_env SET is_default = 0 WHERE is_default = 1 AND id <> ?",
            (keep_id,),
        )

    # ---- 口令：本体进 OS 密钥库，库里只留引用与掩码 ----
    @staticmethod
    def password_ref(env_id: int) -> str:
        return f"test_env:{env_id}"

    def save_password(self, env_id: int, password: str) -> str:
        """写入口令到系统密钥库，返回掩码。数据库不保存明文。"""
        ref = self.password_ref(env_id)
        store = get_store()
        if password:
            store.set(ref, password)
        else:
            store.delete(ref)
        masked = mask(password) if password else ""
        self.update(env_id, {"db_password_ref": ref, "db_password_mask": masked})
        return masked

    def load_password(self, env_id: int) -> str:
        """只在服务端探测时取用，任何接口都不回显明文。"""
        try:
            return get_store().get(self.password_ref(env_id))
        except Exception:
            return ""

    @staticmethod
    def to_public(row: RowDict) -> dict[str, Any]:
        """对外输出，绝不暴露口令明文。"""
        return {
            "id": row.get("id"),
            "name": row.get("name"),
            "kind": row.get("env_kind") or "test",
            "kindLabel": ENV_KIND_LABELS.get(str(row.get("env_kind") or "test"), ""),
            "description": row.get("description"),
            "app": {
                "baseUrl": row.get("app_base_url"),
                "healthPath": row.get("app_health_path"),
                "metricsUrl": row.get("app_metrics_url") or "",
            },
            "db": {
                "type": row.get("db_type"),
                "host": row.get("db_host"),
                "port": row.get("db_port"),
                "name": row.get("db_name"),
                "username": row.get("db_username"),
                "passwordMasked": row.get("db_password_mask") or "",
                "hasPassword": bool(row.get("db_password_ref")),
                "metricsUrl": row.get("db_metrics_url") or "",
            },
            "isDefault": bool(row.get("is_default")),
            "sortOrder": row.get("sort_order"),
            "createdAt": row.get("created_at"),
            "updatedAt": row.get("updated_at"),
        }

    def list_public(self) -> RowList:
        return [self.to_public(row) for row in self.list_all()]
