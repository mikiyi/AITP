"""API Key 落盘加密：Fernet + 本地密钥文件。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from ..ai.errors import AIKeyDecryptFailed

KEY_FILENAME = ".secret_key"


def get_or_create_secret_key(path: Path) -> bytes:
    """读取密钥文件，不存在则原子创建。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        key = path.read_bytes().strip()
        if key:
            return key
    key = Fernet.generate_key()
    try:
        fd = os.open(str(path), os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(key)
    except FileExistsError:
        return path.read_bytes().strip()
    return key


def _fernet(key_path: Path) -> Fernet:
    return Fernet(get_or_create_secret_key(key_path))


def encrypt(plaintext: str, key_path: Path) -> str:
    if not plaintext:
        return ""
    return _fernet(key_path).encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt(ciphertext: str, key_path: Path) -> str:
    if not ciphertext:
        return ""
    try:
        return _fernet(key_path).decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError, TypeError) as exc:
        raise AIKeyDecryptFailed(
            "API Key 无法解密（密钥文件 data/.secret_key 已变更或损坏），请重新填写该配置的密钥。"
        ) from exc


def mask(plaintext: str) -> str:
    """展示用掩码：只暴露首尾少量字符。"""
    if not plaintext:
        return ""
    if len(plaintext) <= 12:
        return "***"
    return f"{plaintext[:6]}***{plaintext[-4:]}"


def content_checksum(text: str) -> str:
    """正文指纹：sha256 前 16 位。用于判断知识库文件是否被外部改过。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()[:16]
