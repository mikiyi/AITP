"""跨平台密钥存储。

后端优先级：
  1. Windows —— DPAPI（CryptProtectData，CurrentUser 作用域，非管理员亦可解密）
  2. macOS   —— 钥匙串（Security.framework 通用密码项，登录钥匙串）
  3. 其它平台 —— 本地加密文件（Fernet）兜底

三条硬性约束：
  - 数据库只存掩码与引用标识，密钥本体不进数据库
  - 所有落盘写入都是「临时文件 + os.replace」原子替换，不留半截文件
  - 密钥本体永远不出现在接口响应里
"""

from __future__ import annotations

import ctypes
import ctypes.util
import json
import os
import platform
import sys
import uuid
from pathlib import Path

from .security import decrypt, encrypt, get_or_create_secret_key

SERVICE_NAME = "com.aitestpilot.secrets"

BACKEND_DPAPI = "dpapi"
BACKEND_KEYCHAIN = "keychain"
BACKEND_FILE = "file"


def atomic_write_bytes(path: Path, data: bytes, mode: int = 0o600) -> None:
    """原子写文件：先写临时文件再 os.replace，中断不会留下半截内容。"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}")
    try:
        fd = os.open(str(tmp), os.O_CREAT | os.O_EXCL | os.O_WRONLY, mode)
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8", mode: int = 0o600) -> None:
    atomic_write_bytes(path, text.encode(encoding), mode)


# ---------------------------------------------------------------------
# Windows DPAPI
# ---------------------------------------------------------------------
class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.c_uint32),
        ("pbData", ctypes.POINTER(ctypes.c_char)),
    ]


def _dpapi_available() -> bool:
    return sys.platform == "win32"


def _dpapi_protect(data: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    buffer = ctypes.create_string_buffer(data, len(data))
    in_blob = _DataBlob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()

    # CRYPTPROTECT_UI_FORBIDDEN=0x1：不弹 UI；不设 CRYPTPROTECT_LOCAL_MACHINE → CurrentUser 作用域
    if not crypt32.CryptProtectData(
        ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
    ):
        raise OSError(ctypes.get_last_error(), "CryptProtectData 失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _dpapi_unprotect(blob: bytes) -> bytes:
    crypt32 = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    buffer = ctypes.create_string_buffer(blob, len(blob))
    in_blob = _DataBlob(len(blob), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_char)))
    out_blob = _DataBlob()

    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob), None, None, None, None, 0x1, ctypes.byref(out_blob)
    ):
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData 失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


# ---------------------------------------------------------------------
# macOS 钥匙串
# ---------------------------------------------------------------------
_ERRSEC_DUPLICATE_ITEM = -25299
_ERRSEC_ITEM_NOT_FOUND = -25300


def _keychain_available() -> bool:
    return sys.platform == "darwin" and bool(ctypes.util.find_library("Security"))


def _security_lib() -> ctypes.CDLL:
    path = ctypes.util.find_library("Security")
    if not path:
        raise OSError("未找到 Security.framework")
    lib = ctypes.cdll.LoadLibrary(path)
    lib.SecKeychainFindGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainFindGenericPassword.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
        ctypes.c_uint32, ctypes.c_char_p,
        ctypes.POINTER(ctypes.c_uint32), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.SecKeychainAddGenericPassword.restype = ctypes.c_int32
    lib.SecKeychainAddGenericPassword.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p,
        ctypes.c_uint32, ctypes.c_char_p,
        ctypes.c_uint32, ctypes.c_char_p, ctypes.POINTER(ctypes.c_void_p),
    ]
    lib.SecKeychainItemModifyAttributesAndData.restype = ctypes.c_int32
    lib.SecKeychainItemModifyAttributesAndData.argtypes = [
        ctypes.c_void_p, ctypes.c_uint32, ctypes.c_char_p
    ]
    lib.SecKeychainItemDelete.restype = ctypes.c_int32
    lib.SecKeychainItemDelete.argtypes = [ctypes.c_void_p]
    return lib


def _keychain_set(ref: str, secret: bytes) -> None:
    lib = _security_lib()
    service = SERVICE_NAME.encode("utf-8")
    account = ref.encode("utf-8")
    item = ctypes.c_void_p()
    status = lib.SecKeychainAddGenericPassword(
        None, len(service), service, len(account), account, len(secret), secret, ctypes.byref(item)
    )
    if status == 0:
        return
    if status != _ERRSEC_DUPLICATE_ITEM:
        raise OSError(f"SecKeychainAddGenericPassword 失败: {status}")

    found = ctypes.c_void_p()
    status = lib.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account, None, None, ctypes.byref(found)
    )
    if status != 0:
        raise OSError(f"SecKeychainFindGenericPassword 失败: {status}")
    # 通用密码项的数据长度固定：先清空再写入
    lib.SecKeychainItemModifyAttributesAndData(found, 0, None)
    status = lib.SecKeychainItemModifyAttributesAndData(found, len(secret), secret)
    if status != 0:
        raise OSError(f"SecKeychainItemModifyAttributesAndData 失败: {status}")


def _keychain_get(ref: str) -> bytes | None:
    lib = _security_lib()
    service = SERVICE_NAME.encode("utf-8")
    account = ref.encode("utf-8")
    length = ctypes.c_uint32(0)
    data = ctypes.c_void_p()
    item = ctypes.c_void_p()
    status = lib.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account,
        ctypes.byref(length), ctypes.byref(data), ctypes.byref(item),
    )
    if status == _ERRSEC_ITEM_NOT_FOUND:
        return None
    if status != 0:
        raise OSError(f"SecKeychainFindGenericPassword 失败: {status}")
    try:
        return ctypes.string_at(data, length.value)
    finally:
        if data:
            # SecKeychainFindGenericPassword 会分配密码缓冲区，需要自己释放
            ctypes.CDLL(ctypes.util.find_library("c") or "libc.dylib").free(data)


def _keychain_delete(ref: str) -> None:
    lib = _security_lib()
    service = SERVICE_NAME.encode("utf-8")
    account = ref.encode("utf-8")
    item = ctypes.c_void_p()
    status = lib.SecKeychainFindGenericPassword(
        None, len(service), service, len(account), account, None, None, ctypes.byref(item)
    )
    if status == _ERRSEC_ITEM_NOT_FOUND:
        return
    if status != 0:
        raise OSError(f"SecKeychainFindGenericPassword 失败: {status}")
    lib.SecKeychainItemDelete(item)


# ---------------------------------------------------------------------
# 统一入口
# ---------------------------------------------------------------------
class SecretStore:
    """按引用存取密钥。ref 形如 ai_profile:3。"""

    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.blob_dir = base_dir / "secrets"
        self.blob_dir.mkdir(parents=True, exist_ok=True)
        self._backend = self._detect_backend()

    def _detect_backend(self) -> str:
        if _dpapi_available():
            return BACKEND_DPAPI
        if _keychain_available():
            return BACKEND_KEYCHAIN
        return BACKEND_FILE

    @property
    def backend(self) -> str:
        return self._backend

    def backend_label(self) -> str:
        return {
            BACKEND_DPAPI: "Windows DPAPI（当前用户）",
            BACKEND_KEYCHAIN: "macOS 钥匙串",
            BACKEND_FILE: "本地加密文件",
        }.get(self._backend, self._backend)

    # ---- 引用 -> 落盘位置 ----
    def _blob_path(self, ref: str) -> Path:
        import hashlib

        digest = hashlib.sha256(ref.encode("utf-8")).hexdigest()[:32]
        return self.blob_dir / f"{digest}.blob"

    def _file_index_path(self) -> Path:
        return self.base_dir / "secrets.enc.json"

    # ---- 读 ----
    def get(self, ref: str) -> str:
        if self._backend == BACKEND_DPAPI:
            path = self._blob_path(ref)
            if not path.exists():
                return ""
            return _dpapi_unprotect(path.read_bytes()).decode("utf-8")
        if self._backend == BACKEND_KEYCHAIN:
            data = _keychain_get(ref)
            return data.decode("utf-8") if data else ""
        index = self._read_index()
        ciphertext = index.get(ref)
        if not ciphertext:
            return ""
        return decrypt(ciphertext, self.base_dir / ".secret_key")

    # ---- 写 ----
    def set(self, ref: str, secret: str) -> None:
        if self._backend == BACKEND_DPAPI:
            atomic_write_bytes(self._blob_path(ref), _dpapi_protect(secret.encode("utf-8")))
            return
        if self._backend == BACKEND_KEYCHAIN:
            _keychain_set(ref, secret.encode("utf-8"))
            return
        index = self._read_index()
        index[ref] = encrypt(secret, self.base_dir / ".secret_key")
        atomic_write_text(
            self._file_index_path(), json.dumps(index, ensure_ascii=False, indent=2)
        )

    def delete(self, ref: str) -> None:
        if self._backend == BACKEND_DPAPI:
            path = self._blob_path(ref)
            if path.exists():
                path.unlink()
            return
        if self._backend == BACKEND_KEYCHAIN:
            _keychain_delete(ref)
            return
        index = self._read_index()
        if ref in index:
            index.pop(ref)
            atomic_write_text(
                self._file_index_path(), json.dumps(index, ensure_ascii=False, indent=2)
            )

    # ---- 内部 ----
    def _read_index(self) -> dict[str, str]:
        path = self._file_index_path()
        if not path.exists():
            return {}
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
        return data if isinstance(data, dict) else {}


def default_store(base_dir: Path | None = None) -> SecretStore:
    if base_dir is None:
        from ..config import PROJECT_ROOT

        base_dir = PROJECT_ROOT / "data"
    base_dir.mkdir(parents=True, exist_ok=True)
    # 兜底后端需要本地密钥文件，提前生成好
    if platform.system() not in ("Windows", "Darwin"):
        get_or_create_secret_key(base_dir / ".secret_key")
    return SecretStore(base_dir)


_STORE: SecretStore | None = None


def get_store() -> SecretStore:
    global _STORE
    if _STORE is None:
        _STORE = default_store()
    return _STORE


def set_store(store: SecretStore) -> None:
    global _STORE
    _STORE = store
