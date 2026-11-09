"""应用工厂。"""

from __future__ import annotations

import os
import threading
from pathlib import Path
from typing import Any, cast

from flask import Flask, Response, abort, current_app, send_file, send_from_directory
from flask.json.provider import DefaultJSONProvider

from .common.path_safety import is_blocked_name, normalize_relative_path, safe_join
from .config import AppConfig, load_config
from .errors import register_error_handlers
from .logging_setup import log_deploy_event, setup_logging
from .middleware import register_middleware
from .observability import register_observability

_LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}


def _register_static(app: Flask, static_dir: Path) -> None:
    """托管 Vite 构建产物，并对非 /api 路径提供 SPA fallback。

    只服务 static_dir（生产即 dist/）内部的文件；路径先规范化再校验，杜绝目录穿越。
    """

    @app.route("/", defaults={"path": ""})
    @app.route("/<path:path>")
    def spa(path: str) -> Response:
        if path.startswith("api/"):
            abort(404)

        relative = normalize_relative_path(path)
        if relative and not is_blocked_name(relative):
            candidate = safe_join(static_dir, relative)
            if candidate is not None and candidate.is_file():
                return send_from_directory(static_dir, relative)

        index_file = safe_join(static_dir, "index.html")
        if index_file is not None and index_file.is_file():
            return send_from_directory(static_dir, "index.html")
        return send_file(Path(__file__).resolve().parent / "templates" / "placeholder.html")


def _strip_cors_headers(response: Response) -> Response:
    """服务端不设置任何 CORS 头，阻断恶意网站跨域读取本地 API。"""
    response.headers.pop("Access-Control-Allow-Origin", None)
    response.headers.pop("Access-Control-Allow-Credentials", None)
    response.headers.pop("Access-Control-Allow-Headers", None)
    response.headers.pop("Access-Control-Allow-Methods", None)
    response.headers.pop("Access-Control-Expose-Headers", None)
    return response


def _has_index(static_dir: Path) -> bool:
    index_file = safe_join(static_dir, "index.html")
    return index_file is not None and index_file.is_file()


def _warm_up_http_client() -> None:
    """预热共享 httpx 客户端。

    首次创建 Client 要加载 Windows 证书库，约 700ms。放到后台线程做，
    避免用户第一次调用 AI 时白等。
    """
    def _run() -> None:
        try:
            from .ai.adapters.base import shared_client

            shared_client()
        except Exception as exc:  # pragma: no cover - 预热失败不影响启动
            app.logger.warning("http client warm-up failed: %s", exc)  # type: ignore[name-defined]

    threading.Thread(target=_run, name="aitp-http-warmup", daemon=True).start()


def _register_secret_store(cfg: AppConfig) -> None:
    """密钥库存放在数据目录旁，与数据库同级但独立成目录。"""
    from .common.secret_store import SecretStore, set_store

    store = SecretStore(cfg.db_path.parent)
    set_store(store)
    log_deploy_event("secret-store", "密钥存储后端就绪", backend=store.backend_label())


def _register_profile_loader(cfg: AppConfig) -> None:
    """把数据库里的 AI 档案接入配置解析器（DB > env > 默认）。"""
    from .ai.resolver import set_profile_loader, set_setting_loader
    from .db.connection import get_standalone_conn
    from .repositories.ai_profile_repo import AIProfileRepository
    from .repositories.sys_config_repo import SysConfigRepository

    def _load() -> list[dict[str, Any]]:
        conn = get_standalone_conn(cfg.db_path)
        try:
            return AIProfileRepository(conn).list_active()
        finally:
            conn.close()

    def _setting(key: str, default: str) -> str:
        conn = get_standalone_conn(cfg.db_path)
        try:
            return str(SysConfigRepository(conn).get_value(key, default))
        finally:
            conn.close()

    set_profile_loader(_load)
    set_setting_loader(_setting)


def _register_prompt_overrides(cfg: AppConfig) -> None:
    """把库里的提示词自定义正文接入注册表（自定义 > 磁盘默认）。"""
    from .db.connection import get_standalone_conn
    from .prompts.registry import get_registry
    from .repositories.prompt_override_repo import PromptOverrideRepository

    def _load() -> dict[str, str]:
        conn = get_standalone_conn(cfg.db_path)
        try:
            return PromptOverrideRepository(conn).list_bodies()
        finally:
            conn.close()

    get_registry().set_override_loader(_load)


def _check_bind_host(cfg: AppConfig) -> None:
    """默认仅绑定 127.0.0.1；要暴露到局域网/公网必须显式开关。"""
    if cfg.host in _LOOPBACK_HOSTS:
        log_deploy_event("bind", "仅本机访问", host=cfg.host, port=cfg.port)
        return
    if os.environ.get("AITP_ALLOW_PUBLIC_BIND", "").strip() not in {"1", "true", "yes", "on"}:
        raise RuntimeError(
            f"拒绝启动：监听地址 {cfg.host} 会把本地数据暴露给局域网/公网。"
            f"默认只允许 127.0.0.1；确需对外暴露时显式设置 AITP_ALLOW_PUBLIC_BIND=1。"
        )
    log_deploy_event(
        "bind-warn",
        "已显式允许非回环监听，请自行确认网络边界",
        host=cfg.host,
        port=cfg.port,
    )


def _run_startup_preflight(cfg: AppConfig) -> None:
    """迁移前先验 SQLite 能力与目录可写，失败时一次说清原因。

    这两项只有跨平台时才会踩到（系统自带 Python 的 SQLite 过旧、目录只读或被占用），
    放到迁移之后就是半启动状态加一屏裸 SQL 错误。
    """
    import sqlite3
    import sys

    from .common.preflight import PreflightError, check_runtime

    problems = check_runtime(cfg)
    if problems:
        for item in problems:
            log_deploy_event("preflight", item)
        raise PreflightError(
            "AITP 启动自检未通过：\n" + "\n".join(f"  - {item}" for item in problems)
        )
    log_deploy_event(
        "preflight",
        "启动自检通过",
        python=sys.version.split()[0],
        sqlite=sqlite3.sqlite_version,
        platform=sys.platform,
    )


def _apply_pending_migrations(cfg: AppConfig) -> None:
    """启动时补齐未应用的迁移，避免升级后接口因缺列报错。"""
    from .db.connection import get_standalone_conn
    from .db.migrator import MIGRATIONS_DIR, drifted_versions, run_migrations

    conn = get_standalone_conn(cfg.db_path)
    try:
        applied = run_migrations(conn)
        drift = drifted_versions(conn, MIGRATIONS_DIR)
    finally:
        conn.close()
    if applied:
        log_deploy_event("migrate", "已应用数据库迁移", versions=applied)
    if drift:
        # 已应用的迁移被改过：新库和老库结构会悄悄分叉，必须留痕
        log_deploy_event(
            "migrate-drift",
            "已应用的迁移文件内容被修改过，新装环境可能与本机不一致",
            versions=drift,
        )


def _reconcile_interrupted(cfg: AppConfig) -> None:
    """启动时把上次没跑完的任务判为中断。

    服务被 kill 时正在执行的 AI 调用/脚本跑不会有机会写终态，
    这些行会永远停在 running，任务清单和概览就一直显示「执行中」，
    用户以为还在跑，实际早没了。
    """
    from .common.time_utils import utcnow_iso
    from .db.connection import get_standalone_conn
    from .services import knowledge_assistant_service

    reason = "服务重启时任务仍在执行，已标记为中断"
    now = utcnow_iso()
    statements = (
        "UPDATE biz_ai_task SET status = 'failed', error_message = ?, finished_at = ?,"
        " updated_at = ? WHERE status IN ('pending', 'running') AND is_deleted = 0",
        "UPDATE biz_ai_step SET status = 'failed', error_message = ?, completed_at = ?,"
        " updated_at = ? WHERE status IN ('pending', 'running') AND is_deleted = 0",
        "UPDATE biz_automation_run SET status = 'error', error_message = ?, finished_at = ?,"
        " updated_at = ? WHERE status = 'running' AND is_deleted = 0",
        "UPDATE sys_task SET status = 'failed', error_message = ?, finished_at = ?,"
        " updated_at = ? WHERE status = 'running'",
    )
    conn = get_standalone_conn(cfg.db_path)
    try:
        recovered = sum(conn.execute(sql, (reason, now, now)).rowcount for sql in statements)
        # 整理任务被标记失败后，条目状态要回到「待自动整理」，否则按钮一直显示执行中
        recovered += knowledge_assistant_service.reconcile(conn)
        conn.commit()
    finally:
        conn.close()
    if recovered:
        log_deploy_event("reconcile", "已将上次未跑完的任务标记为中断", count=recovered)


def _start_task_queue(app: Flask, cfg: AppConfig) -> None:
    """恢复上次排队的后台任务；没有排队任务就不常驻轮询。"""
    from .services import automation_jobs  # noqa: F401 - 导入即注册处理器
    from .services import backup_schedule  # noqa: F401 - 导入即注册处理器
    from .services import interface_jobs  # noqa: F401 - 导入即注册处理器
    from .services import knowledge_assistant_service  # noqa: F401 - 导入即注册处理器
    from .services import notification_service  # noqa: F401 - 导入即注册处理器
    from .services import job_queue

    job_queue.set_app(app)
    job_queue.start_workers_if_pending(cfg.db_path)
    backup_schedule.start_scheduler(cfg.db_path)


def _start_env_health_sampler(cfg: AppConfig) -> None:
    """后台持续采集测试环境健康曲线，首页进来直接有数据。"""
    from .services import env_health_history

    env_health_history.start_sampler(cfg.db_path)


def _auto_reindex_knowledge(cfg: AppConfig) -> None:
    """按配置在启动时扫描知识库目录重建索引。"""
    from .db.connection import get_standalone_conn
    from .services import knowledge_storage

    conn = get_standalone_conn(cfg.db_path)
    try:
        if not knowledge_storage.auto_reindex_enabled(conn):
            return
        result = knowledge_storage.reindex(conn)
        conn.commit()
    except Exception as exc:  # 索引失败不阻断启动
        log_deploy_event("reindex", "知识库索引重建失败", error=str(exc))
        return
    finally:
        conn.close()
    log_deploy_event("reindex", "知识库索引已重建", scanned=result.get("scanned"))


def create_app(config: AppConfig | None = None) -> Flask:
    cfg = config or load_config()

    app = Flask(__name__, static_folder=None)
    app.config.update(
        AITP_HOST=cfg.host,
        AITP_PORT=cfg.port,
        AITP_DEBUG=cfg.debug,
        AITP_DB_PATH=str(cfg.db_path),
        AITP_LOG_PATH=str(cfg.log_path),
        AITP_DEPLOY_LOG_PATH=str(cfg.deploy_log_path),
        AITP_SECRET_KEY_PATH=str(cfg.secret_key_path),
        AITP_PROMPTS_DIR=str(cfg.prompts_dir),
        AITP_STATIC_DIR=str(cfg.static_dir),
        AITP_ROOT=str(cfg.root),
        JSON_AS_ASCII=False,
        JSONIFY_PRETTYPRINT_REGULAR=False,
    )
    cast(DefaultJSONProvider, app.json).ensure_ascii = False

    setup_logging(cfg.log_path, deploy_log_path=cfg.deploy_log_path)
    log_deploy_event(
        "start",
        "AITP 启动中",
        host=cfg.host,
        port=cfg.port,
        db=str(cfg.db_path),
        static=str(cfg.static_dir),
    )
    _check_bind_host(cfg)
    _run_startup_preflight(cfg)

    _apply_pending_migrations(cfg)
    _reconcile_interrupted(cfg)
    _start_task_queue(app, cfg)
    _start_env_health_sampler(cfg)
    _auto_reindex_knowledge(cfg)
    _register_secret_store(cfg)
    _register_profile_loader(cfg)
    _register_prompt_overrides(cfg)

    register_error_handlers(app)
    register_middleware(app)
    register_observability(app)
    app.after_request(_strip_cors_headers)

    from .db.connection import close_db
    app.teardown_appcontext(close_db)

    from .api import register_blueprints
    from .modules.common.routes import bp as common_bp

    app.register_blueprint(common_bp, url_prefix="/api/v1")
    register_blueprints(app)

    _register_static(app, cfg.static_dir)
    _warm_up_http_client()

    app.logger.info("AITP app created, static=%s", cfg.static_dir)
    return app
