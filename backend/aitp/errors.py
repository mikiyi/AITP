"""错误码注册表与应用级异常。"""

from __future__ import annotations

from typing import Any

from flask import Flask, Response, jsonify
from werkzeug.exceptions import HTTPException

from .envelope import fail


class ErrorCode:
    SUCCESS = 200000

    PARAM_ERROR = 100100
    NOT_FOUND_ENDPOINT = 100103
    METHOD_NOT_ALLOWED = 100104
    PAGE_PARAM_INVALID = 100108

    AI_CREDENTIAL_MISSING = 110102
    AI_AUTH_FAILED = 110103
    AI_UNREACHABLE = 110105
    AI_MODEL_NOT_FOUND = 110108
    AI_TIMEOUT = 110110
    AI_EMPTY_RESPONSE = 110111
    AI_KEY_DECRYPT_FAILED = 110113

    PROMPT_NOT_FOUND = 120101
    PROMPT_VAR_MISSING = 120102
    PROMPT_PARSE_FAILED = 120103
    AI_OUTPUT_INVALID_JSON = 120104

    IMPORT_PARSE_FAILED = 130101
    EXPORT_FAILED = 130102

    RESOURCE_NOT_FOUND = 140100
    ILLEGAL_TRANSITION = 140102
    UNIQUE_CONFLICT = 140103
    RESOURCE_REFERENCED = 140104
    ILLEGAL_OPERATION = 140105

    INTERNAL_ERROR = 199000


class AppError(Exception):
    """业务异常。抛出后由 errorhandler 转成包封响应。"""

    def __init__(self, code: int, message: str, data: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.data = data


def register_error_handlers(app: Flask) -> None:
    @app.errorhandler(AppError)
    def _handle_app_error(exc: AppError) -> Response:
        return fail(exc.code, exc.message, exc.data)

    @app.errorhandler(404)
    def _handle_404(exc: HTTPException) -> Response:
        return fail(ErrorCode.NOT_FOUND_ENDPOINT, "接口不存在")

    @app.errorhandler(405)
    def _handle_405(exc: HTTPException) -> Response:
        return fail(ErrorCode.METHOD_NOT_ALLOWED, "请求方法不支持")

    @app.errorhandler(Exception)
    def _handle_unexpected(exc: Exception) -> Response:
        app.logger.exception("unhandled error: %s", exc)
        return fail(ErrorCode.INTERNAL_ERROR, f"服务器内部错误: {exc}")
