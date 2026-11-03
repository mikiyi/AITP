"""AI 错误分类与到业务错误码的映射。"""

from __future__ import annotations

from ..errors import AppError, ErrorCode


class AIError(AppError):
    """AI 层异常；继承 AppError 以便直接映射到业务错误码。"""

    code = ErrorCode.INTERNAL_ERROR
    retryable = False

    def __init__(self, message: str, code: int | None = None, retryable: bool | None = None):
        super().__init__(code if code is not None else self.code, message)
        if retryable is not None:
            self.retryable = retryable


class AICredentialMissing(AIError):
    code = ErrorCode.AI_CREDENTIAL_MISSING


class AIAuthFailed(AIError):
    code = ErrorCode.AI_AUTH_FAILED


class AIUnreachable(AIError):
    code = ErrorCode.AI_UNREACHABLE
    retryable = True


class AIModelNotFound(AIError):
    code = ErrorCode.AI_MODEL_NOT_FOUND


class AITimeout(AIError):
    code = ErrorCode.AI_TIMEOUT
    retryable = True


class AIEmptyResponse(AIError):
    code = ErrorCode.AI_EMPTY_RESPONSE


class AIKeyDecryptFailed(AIError):
    code = ErrorCode.AI_KEY_DECRYPT_FAILED


def classify_status(status: int, body: str = "") -> AIError:
    """按 HTTP 状态码把上游错误映射为 AI 层异常。"""
    detail = (body or "").strip()
    if len(detail) > 500:
        detail = detail[:500] + "..."

    if status in (401, 403):
        return AIAuthFailed(f"AI 鉴权失败（HTTP {status}）：密钥无效或无权限。{detail}")
    if status == 404:
        return AIModelNotFound(f"AI 接口或模型不存在（HTTP 404）。{detail}")
    if status == 429:
        return AIError(f"AI 服务限流（HTTP 429），请稍后重试。{detail}", retryable=True)
    if status in (500, 502, 503, 504):
        return AIError(f"AI 服务端异常（HTTP {status}）。{detail}", retryable=True)
    return AIError(f"AI 请求失败（HTTP {status}）。{detail}")
