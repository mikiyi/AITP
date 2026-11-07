"""本地访问口令：口令哈希、登录会话与失败限流。

默认不启用；启用后除「状态 / 登录」外的全部 /api 接口都要带上有效会话。
口令只存 PBKDF2 哈希，文件与数据库同级（access_password.json），不写明文。
会话放在进程内存里：服务重启即失效，必须重新输入口令。
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import threading
import time
from pathlib import Path
from typing import Any

from ..common.secret_store import atomic_write_text
from ..common.time_utils import utcnow_iso

COOKIE_NAME = "aitp_access"
FILE_NAME = "access_password.json"

SESSION_TTL_SECONDS = 12 * 3600
MIN_PASSWORD_LENGTH = 6
MAX_PASSWORD_LENGTH = 128

#: 同一客户端 60 秒内错 5 次就锁 60 秒，挡一下本机脚本的暴力尝试
MAX_FAILURES = 5
FAILURE_WINDOW_SECONDS = 60.0
LOCK_SECONDS = 60.0

_ALGORITHM = "pbkdf2_sha256"
_ITERATIONS = 240_000
_SALT_BYTES = 16
_DK_BYTES = 32

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_sessions: dict[str, float] = {}
_failures: dict[str, list[float]] = {}


def password_path(data_dir: Path) -> Path:
    return Path(data_dir) / FILE_NAME


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.b64decode(text.encode("ascii"), validate=True)


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, _ITERATIONS, dklen=_DK_BYTES)
    return f"{_ALGORITHM}${_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(stored: str, password: str) -> bool:
    """校验口令；哈希格式陌生时一律判为不匹配，不抛异常。"""
    try:
        algorithm, iterations, salt_text, digest_text = str(stored).split("$")
        if algorithm != _ALGORITHM:
            return False
        rounds = int(iterations)
        salt = _unb64(salt_text)
        expected = _unb64(digest_text)
    except (ValueError, TypeError):
        return False
    if rounds <= 0 or not salt or not expected:
        return False
    actual = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=len(expected))
    return hmac.compare_digest(actual, expected)


def load_record(data_dir: Path) -> dict[str, Any] | None:
    """读取口令记录；文件不存在或损坏时返回 None（视为未启用）。"""
    path = password_path(data_dir)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except OSError as exc:
        logger.warning("access password file unreadable: %s", exc)
        return None
    try:
        record = json.loads(raw)
    except ValueError:
        logger.warning("access password file is not valid JSON: %s", path)
        return None
    if not isinstance(record, dict) or not str(record.get("hash") or ""):
        logger.warning("access password file has no hash: %s", path)
        return None
    return record


def is_enabled(data_dir: Path) -> bool:
    return load_record(data_dir) is not None


def configure(data_dir: Path, password: str) -> dict[str, Any]:
    """写入或替换口令；同时清空全部会话，旧令牌立即失效。"""
    record = {
        "version": 1,
        "algorithm": _ALGORITHM,
        "iterations": _ITERATIONS,
        "hash": hash_password(password),
        "updatedAt": utcnow_iso(),
    }
    atomic_write_text(password_path(data_dir), json.dumps(record, ensure_ascii=False, indent=2))
    revoke_all()
    return record


def remove(data_dir: Path) -> None:
    password_path(data_dir).unlink(missing_ok=True)
    revoke_all()


# ---------------- 会话 ----------------

def _purge_expired(now: float) -> None:
    for token, expires_at in list(_sessions.items()):
        if expires_at <= now:
            _sessions.pop(token, None)


def create_session() -> tuple[str, float]:
    token = secrets.token_urlsafe(32)
    expires_at = time.time() + SESSION_TTL_SECONDS
    with _lock:
        _purge_expired(time.time())
        _sessions[token] = expires_at
    return token, expires_at


def is_valid(token: str) -> bool:
    if not token:
        return False
    now = time.time()
    with _lock:
        expires_at = _sessions.get(token)
        if expires_at is None:
            return False
        if expires_at <= now:
            _sessions.pop(token, None)
            return False
        return True


def revoke(token: str) -> None:
    if not token:
        return
    with _lock:
        _sessions.pop(token, None)


def revoke_all() -> None:
    with _lock:
        _sessions.clear()


def active_sessions() -> int:
    with _lock:
        _purge_expired(time.time())
        return len(_sessions)


# ---------------- 失败限流 ----------------

def _recent_failures(key: str, now: float) -> list[float]:
    stamps = [item for item in _failures.get(key, []) if now - item < FAILURE_WINDOW_SECONDS]
    if stamps:
        _failures[key] = stamps
    else:
        _failures.pop(key, None)
    return stamps


def locked_seconds(key: str) -> int:
    """返回还需等待的秒数；没被锁时返回 0。"""
    now = time.time()
    with _lock:
        stamps = _recent_failures(key, now)
        if len(stamps) < MAX_FAILURES:
            return 0
        remaining = LOCK_SECONDS - (now - stamps[-1])
    return max(1, int(remaining + 0.999)) if remaining > 0 else 0


def record_failure(key: str) -> None:
    now = time.time()
    with _lock:
        stamps = _recent_failures(key, now)
        stamps.append(now)
        _failures[key] = stamps


def reset_failures(key: str) -> None:
    with _lock:
        _failures.pop(key, None)


def remaining_attempts(key: str) -> int:
    now = time.time()
    with _lock:
        return max(0, MAX_FAILURES - len(_recent_failures(key, now)))
