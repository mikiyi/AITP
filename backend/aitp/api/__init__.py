"""API 蓝图注册。所有业务路由统一挂在 /api/v1 下，每个资源一个前缀，避免路由冲突。"""

from __future__ import annotations

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from .ai import bp as ai_bp
    from .bugs import bp as bugs_bp
    from .cases import bp as cases_bp
    from .knowledge import bp as knowledge_bp
    from .lab import bp as lab_bp
    from .meta import bp as meta_bp
    from .modules import bp as modules_bp
    from .projects import bp as projects_bp
    from .reports import bp as reports_bp
    from .requirements import bp as requirements_bp
    from .settings import bp as settings_bp
    from .workbench import bp as workbench_bp

    for bp, prefix in (
        (workbench_bp, "/api/v1/workbench"),
        (lab_bp, "/api/v1/lab"),
        (ai_bp, "/api/v1/ai"),
        (projects_bp, "/api/v1/projects"),
        (requirements_bp, "/api/v1/requirements"),
        (modules_bp, "/api/v1/modules"),
        (cases_bp, "/api/v1/test-cases"),
        (bugs_bp, "/api/v1/bugs"),
        (knowledge_bp, "/api/v1/knowledge"),
        (reports_bp, "/api/v1/reports"),
        (settings_bp, "/api/v1/settings"),
        (meta_bp, "/api/v1/meta"),
    ):
        app.register_blueprint(bp, url_prefix=prefix)
