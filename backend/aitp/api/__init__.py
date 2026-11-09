"""API 蓝图注册。所有业务路由统一挂在 /api/v1 下，每个资源一个前缀，避免路由冲突。"""

from __future__ import annotations

from flask import Flask


def register_blueprints(app: Flask) -> None:
    from .ai import bp as ai_bp
    from .ai_records import bp as ai_records_bp
    from .auth import bp as auth_bp
    from .automation import bp as automation_bp
    from .badges import bp as badges_bp
    from .bugs import bp as bugs_bp
    from .cases import bp as cases_bp
    from .ci import bp as ci_bp
    from .doc_templates import bp as doc_templates_bp
    from .knowledge import bp as knowledge_bp
    from .lab import bp as lab_bp
    from .meta import bp as meta_bp
    from .modules import bp as modules_bp
    from .onboarding import bp as onboarding_bp
    from .projects import bp as projects_bp
    from .project_versions import bp as project_versions_bp
    from .relations import bp as relations_bp
    from .regressions import bp as regressions_bp
    from .release_gates import bp as release_gates_bp
    from .reports import bp as reports_bp
    from .requirements import bp as requirements_bp
    from .search import bp as search_bp
    from .sql_analyses import bp as sql_analyses_bp
    from .settings import bp as settings_bp
    from .tasks import bp as tasks_bp
    from .test_data import bp as test_data_bp
    from .test_envs import bp as test_envs_bp
    from .test_plans import bp as test_plans_bp
    from .test_runs import bp as test_runs_bp
    from .todos import bp as todos_bp
    from .traceability import bp as traceability_bp
    from .validation import bp as validation_bp
    from .workbench import bp as workbench_bp

    for bp, prefix in (
        (auth_bp, "/api/v1/auth"),
        (workbench_bp, "/api/v1/workbench"),
        (badges_bp, "/api/v1/badges"),
        (lab_bp, "/api/v1/lab"),
        (ai_bp, "/api/v1/ai"),
        (ai_records_bp, "/api/v1/ai-records"),
        (search_bp, "/api/v1/search"),
        (sql_analyses_bp, "/api/v1/sql-analyses"),
        (projects_bp, "/api/v1/projects"),
        (project_versions_bp, "/api/v1/project-versions"),
        (requirements_bp, "/api/v1/requirements"),
        (modules_bp, "/api/v1/modules"),
        (cases_bp, "/api/v1/test-cases"),
        (doc_templates_bp, "/api/v1/doc-templates"),
        (bugs_bp, "/api/v1/bugs"),
        (knowledge_bp, "/api/v1/knowledge"),
        (reports_bp, "/api/v1/reports"),
        (regressions_bp, "/api/v1/regressions"),
        (test_plans_bp, "/api/v1/test-plans"),
        (test_runs_bp, "/api/v1/test-runs"),
        (ci_bp, "/api/v1/ci"),
        (traceability_bp, "/api/v1/traceability"),
        (release_gates_bp, "/api/v1/release-gates"),
        (relations_bp, "/api/v1/relations"),
        (todos_bp, "/api/v1/todos"),
        (test_data_bp, "/api/v1/test-data"),
        (test_envs_bp, "/api/v1/test-envs"),
        (tasks_bp, "/api/v1/tasks"),
        (automation_bp, "/api/v1/automation"),
        (validation_bp, "/api/v1/validation"),
        (settings_bp, "/api/v1/settings"),
        (meta_bp, "/api/v1/meta"),
        (onboarding_bp, "/api/v1/onboarding"),
    ):
        app.register_blueprint(bp, url_prefix=prefix)
