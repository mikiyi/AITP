"""测试环境配置 API。

管理多套命名环境（应用服务器 + 数据库服务器），供首页环境健康监测使用。
口令走系统密钥库，接口只回掩码；探测纯本机发起，环境信息绝不进大模型。
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, Response

from ..common.validators import check_choice, optional_int, optional_str, require_str
from ..envelope import ok
from ..errors import AppError, ErrorCode
from ..repositories.test_env_repo import (
    DB_DEFAULT_PORTS,
    DB_TYPES,
    ENV_KIND_LABELS,
    ENV_KINDS,
    TestEnvRepository,
)
from ..services import audit_service, env_health_history, env_probe_service
from .helpers import audit, body, commit, db, path_int

bp = Blueprint("test_envs", __name__)


def _repo(conn: Any) -> TestEnvRepository:
    return TestEnvRepository(conn)


def _require_env(repo: TestEnvRepository, target: int) -> dict[str, Any]:
    row = repo.get_by_id(target)
    if not row:
        raise AppError(ErrorCode.RESOURCE_NOT_FOUND, "测试环境不存在")
    return row


@bp.get("")
def list_envs() -> Response:
    conn = db()
    return ok({
        "list": _repo(conn).list_public(),
        "dbTypes": list(DB_TYPES),
        "dbDefaultPorts": DB_DEFAULT_PORTS,
        "kinds": [{"value": key, "label": ENV_KIND_LABELS[key]} for key in ENV_KINDS],
    })


@bp.get("/health-history")
def health_history() -> Response:
    """服务端持续采样的健康历史；进首页直接出曲线，不用等客户端重新探测。"""
    conn = db()
    env_ids = [int(row["id"]) for row in _repo(conn).list_all() if row.get("id") is not None]
    return ok(env_health_history.snapshot(env_ids))


@bp.get("/<pk>")
def get_env(pk: str) -> Response:
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = _require_env(repo, target)
    return ok(repo.to_public(row))


@bp.post("")
def create_env() -> Response:
    conn = db()
    repo = _repo(conn)
    data = body()
    name = require_str(data.get("name"), "name", 100)
    app = _app_of(data)
    dbinfo = _db_of(data)
    if dbinfo["db_type"] not in DB_TYPES:
        raise AppError(ErrorCode.PARAM_ERROR, f"数据库类型只能是：{'、'.join(DB_TYPES)}")
    new_id = repo.insert({
        "name": name,
        "env_kind": _kind_of(data),
        "description": optional_str(data.get("description"), "description", 500),
        "app_base_url": app["app_base_url"],
        "app_health_path": app["app_health_path"],
        "app_metrics_url": app["app_metrics_url"],
        "db_type": dbinfo["db_type"],
        "db_host": dbinfo["db_host"],
        "db_port": dbinfo["db_port"],
        "db_name": dbinfo["db_name"],
        "db_username": dbinfo["db_username"],
        "db_metrics_url": dbinfo["db_metrics_url"],
        "db_password_ref": "",
        "db_password_mask": "",
        "is_default": 1 if data.get("isDefault") else 0,
        "sort_order": optional_int(data.get("sortOrder"), "sortOrder") or 0,
    })
    if data.get("isDefault"):
        repo.clear_other_defaults(new_id)
    # 口令单独走密钥库，不入业务表；兼容嵌套 db.password 与顶层 dbPassword
    password = _password_of(data)
    if password:
        repo.save_password(new_id, password)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CREATE, audit_service.RESOURCE_TEST_ENV, new_id, name,
          {"dbType": dbinfo["db_type"], "hasPassword": bool(_password_of(data))})
    return ok(repo.to_public(repo.get_by_id(new_id) or {}))


@bp.put("/<pk>")
def update_env(pk: str) -> Response:
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = _require_env(repo, target)
    data = body()
    payload: dict[str, Any] = {}
    if "name" in data:
        payload["name"] = require_str(data["name"], "name", 100)
    if "kind" in data:
        payload["env_kind"] = _kind_of(data)
    if "description" in data:
        payload["description"] = optional_str(data["description"], "description", 500)
    if "app" in data and isinstance(data["app"], dict):
        app = _app_of(data["app"])
        payload["app_base_url"] = app["app_base_url"]
        payload["app_health_path"] = app["app_health_path"]
        payload["app_metrics_url"] = app["app_metrics_url"]
    if "db" in data and isinstance(data["db"], dict):
        dbinfo = _db_of(data["db"])
        if dbinfo["db_type"] not in DB_TYPES:
            raise AppError(ErrorCode.PARAM_ERROR, f"数据库类型只能是：{'、'.join(DB_TYPES)}")
        for snake in ("db_type", "db_host", "db_name", "db_username"):
            payload[snake] = dbinfo[snake]
        payload["db_port"] = dbinfo["db_port"]
        payload["db_metrics_url"] = dbinfo["db_metrics_url"]
    if "isDefault" in data:
        payload["is_default"] = 1 if data["isDefault"] else 0
    if "sortOrder" in data:
        payload["sort_order"] = optional_int(data["sortOrder"], "sortOrder") or 0
    if payload:
        repo.update(target, payload)
    if "isDefault" in data and data["isDefault"]:
        repo.clear_other_defaults(target)
    if "dbPassword" in data or (isinstance(data.get("db"), dict) and "password" in data["db"]):
        # 留空表示不改口令，避免编辑其他字段时误清已存口令
        if _password_of(data):
            repo.save_password(target, _password_of(data))
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_UPDATE, audit_service.RESOURCE_TEST_ENV, target,
          str(payload.get("name", row.get("name", "")) or ""), {"fields": sorted(payload.keys())})
    return ok(repo.to_public(repo.get_by_id(target) or {}))


@bp.delete("/<pk>")
def delete_env(pk: str) -> Response:
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = _require_env(repo, target)
    _require_not_referenced(conn, target)
    repo.soft_delete(target)
    # 删掉密钥库里的口令本体，不留孤儿密文
    if row.get("db_password_ref"):
        repo.save_password(target, "")
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_DELETE, audit_service.RESOURCE_TEST_ENV, target,
          str(row.get("name") or ""))
    return ok({"id": target, "deleted": True})


@bp.post("/<pk>/password")
def set_env_password(pk: str) -> Response:
    """单独更新数据库口令；接口只回掩码，明文不落库。"""
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = _require_env(repo, target)
    data = body()
    password = optional_str(data.get("password"), "password", 200)
    masked = repo.save_password(target, password)
    commit(conn)
    audit(conn, audit_service.AUDIT_ACTION_CONFIG, audit_service.RESOURCE_TEST_ENV, target,
          str(row.get("name") or ""), {"passwordUpdated": bool(password)})
    return ok({"id": target, "passwordMasked": masked, "hasPassword": bool(password)})


@bp.post("/<pk>/probe")
def probe_env(pk: str) -> Response:
    """对一套环境做健康探测：应用 HTTP + 数据库 TCP + 资源指标，纯本机、不进大模型。

    quiet=true 表示首页轮询用的静默探测：不写操作日志、不 commit，
    避免每 15 秒自动刷新把操作日志刷满。
    """
    conn = db()
    repo = _repo(conn)
    target = path_int(pk)
    row = _require_env(repo, target)
    data = body()
    quiet = bool(data.get("quiet"))
    result = env_probe_service.probe_env(
        app_base_url=str(row.get("app_base_url") or ""),
        app_health_path=str(row.get("app_health_path") or ""),
        db_type=str(row.get("db_type") or ""),
        db_host=str(row.get("db_host") or ""),
        db_port=row.get("db_port"),
        app_metrics_url=str(row.get("app_metrics_url") or ""),
        db_metrics_url=str(row.get("db_metrics_url") or ""),
    )
    # 手动探测也进历史，曲线里能看到这一次的采样点
    env_health_history.record(target, result)
    if not quiet:
        audit(conn, audit_service.AUDIT_ACTION_EXECUTE, audit_service.RESOURCE_TEST_ENV, target,
              str(row.get("name") or ""), {"overall": result.get("overall")})
        commit(conn)
    return ok(result)


@bp.post("/probe-draft")
def probe_draft() -> Response:
    """对「未保存」的草稿配置探测：保存前先试连，避免存一堆连不上的目标。"""
    data = body()
    raw_app = data.get("app")
    app: dict[str, Any] = raw_app if isinstance(raw_app, dict) else {}
    raw_db = data.get("db")
    dbinfo: dict[str, Any] = raw_db if isinstance(raw_db, dict) else {}
    result = env_probe_service.probe_env(
        app_base_url=str(app.get("baseUrl") or ""),
        app_health_path=str(app.get("healthPath") or ""),
        db_type=str(dbinfo.get("type") or "mysql"),
        db_host=str(dbinfo.get("host") or ""),
        db_port=dbinfo.get("port"),
        app_metrics_url=str(app.get("metricsUrl") or ""),
        db_metrics_url=str(dbinfo.get("metricsUrl") or ""),
    )
    return ok(result)


# ---------------- 入参整理 ----------------

def _kind_of(data: dict[str, Any]) -> str:
    raw = data.get("kind")
    if raw in (None, ""):
        return "test"
    return check_choice(str(raw), ENV_KINDS, "kind") or "test"


def _require_not_referenced(conn: Any, env_id: int) -> None:
    """被计划/轮次/脚本引用的环境不能删，否则历史执行记录会指向空环境。"""
    refs: list[str] = []
    for table, column, label in (
        ("biz_test_plan", "default_env_id", "测试计划"),
        ("biz_test_run", "env_id", "测试轮次"),
        ("biz_automation_script", "env_id", "自动化脚本"),
    ):
        count = int(conn.execute(
            f"SELECT COUNT(*) FROM {table} WHERE {column} = ? AND is_deleted = 0",
            (env_id,),
        ).fetchone()[0])
        if count:
            refs.append(f"{count} 个{label}")
    if refs:
        raise AppError(
            ErrorCode.RESOURCE_REFERENCED,
            f"该环境仍被 {'、'.join(refs)} 引用，请先解除引用再删除",
        )


def _app_of(data: dict[str, Any]) -> dict[str, str]:
    raw = data.get("app")
    app: dict[str, Any] = raw if isinstance(raw, dict) else data
    return {
        "app_base_url": optional_str(app.get("baseUrl") or app.get("app_base_url"), "app.baseUrl", 300),
        "app_health_path": optional_str(app.get("healthPath") or app.get("app_health_path"), "app.healthPath", 200),
        "app_metrics_url": optional_str(
            app.get("metricsUrl") or app.get("app_metrics_url"), "app.metricsUrl", 500),
    }


def _db_of(data: dict[str, Any]) -> dict[str, Any]:
    raw = data.get("db")
    dbinfo: dict[str, Any] = raw if isinstance(raw, dict) else data
    db_type = check_choice(
        dbinfo.get("type") or dbinfo.get("db_type"), DB_TYPES, "db.type", "mysql") or "mysql"
    # 端口留空时按库类型补默认值，省得用户手查
    port_raw = dbinfo.get("port") if dbinfo.get("port") not in ("", None) else dbinfo.get("db_port")
    port = optional_int(port_raw, "db.port")
    if port is None:
        port = DB_DEFAULT_PORTS.get(db_type)
    return {
        "db_type": db_type,
        "db_host": optional_str(dbinfo.get("host") or dbinfo.get("db_host"), "db.host", 200),
        "db_port": port,
        "db_name": optional_str(dbinfo.get("name") or dbinfo.get("db_name"), "db.name", 200),
        "db_username": optional_str(dbinfo.get("username") or dbinfo.get("db_username"), "db.username", 120),
        "db_metrics_url": optional_str(
            dbinfo.get("metricsUrl") or dbinfo.get("db_metrics_url"), "db.metricsUrl", 500),
    }


def _password_of(data: dict[str, Any]) -> str:
    """口令兼容嵌套 db.password 与顶层 dbPassword 两种写法。"""
    raw = data.get("db")
    dbinfo: dict[str, Any] = raw if isinstance(raw, dict) else {}
    nested = dbinfo.get("password")
    return str(nested if nested not in (None, "") else data.get("dbPassword") or "")
