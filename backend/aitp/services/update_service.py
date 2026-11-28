"""检查更新、断点续传下载、暂存与「下次启动应用」的完整链路。

设计边界（改这里之前先想清楚，写坏了会把本机应用改残）：
  - **只在启动时替换文件**：真正拷贝发生在 backend/update_bootstrap.py，它在 run.py
    导入应用之前执行，因此新代码在同一次启动里生效；服务运行中绝不覆盖自身代码。
  - **只覆盖仓库里的源码**：排除 data/、.env、.venv、node_modules、.git、logs/，
    用户数据与密钥不会被更新包碰到。
  - **动手前先备份**：被替换的每个文件先在 data/updates/backup-<时间>/ 留一份。
  - **每一步都落日志**：check/download/verify/stage/apply 写 logs/deploy.log。

来源固定为 Gitee 的 master 分支（用户 2026-11-28 拍板）。版本号从
`/raw/master/backend/aitp/__init__.py` 读取，下载用 `/repository/archive/master.zip`。
测试时可用 AITP_UPDATE_BASE_URL 指向本地桩服务器。
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import threading
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx

REPO = "mikiyi/AITP"
BRANCH = "master"
VERSION_FILE = "backend/aitp/__init__.py"
ARCHIVE_PATH = f"/repository/archive/{BRANCH}.zip"

#: 更新包不得覆盖的目录/文件（相对仓库根）
EXCLUDED_TOP = {".git", ".venv", "data", "logs", "node_modules", ".idea", ".vscode"}
EXCLUDED_NAMES = {".env", ".env.local", ".secret_key"}

STATE_LOCK = threading.Lock()
_state: dict[str, Any] = {
    "phase": "idle",
    "message": "",
    "version": "",
    "downloaded": 0,
    "total": 0,
    "resumable": False,
    "updatedAt": "",
}
_worker: threading.Thread | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def base_url() -> str:
    return (os.environ.get("AITP_UPDATE_BASE_URL") or f"https://gitee.com/{REPO}").rstrip("/")


def updates_root(data_dir: Path) -> Path:
    return data_dir / "updates"


def package_dir(data_dir: Path, version: str) -> Path:
    return updates_root(data_dir) / version


def pending_path(data_dir: Path) -> Path:
    return updates_root(data_dir) / "pending-update.json"


def parse_version(text: str) -> tuple[int, ...]:
    """'v2.8.1' / '2.8' -> (2, 8, 1) / (2, 8)；无法解析返回空元组。"""
    digits = re.findall(r"\d+", str(text or "").lstrip("vV"))
    return tuple(int(part) for part in digits[:4]) if digits else ()


def local_version() -> str:
    from .. import __version__

    return str(__version__)


def _client(timeout: float = 8.0) -> httpx.Client:
    # trust_env=False：本机 NO_PROXY 里可能有 httpx 解析不了的 IPv6 字面量（见 zentao.py）
    return httpx.Client(timeout=timeout, follow_redirects=True, trust_env=False)


def _version_from_text(text: str) -> str:
    match = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", text)
    return match.group(1) if match else ""


def _fetch_remote_version() -> tuple[str, str, str]:
    """返回 (最新版本, 命中的地址, 错误信息)。

    优先走 Gitee API contents（base64，不经过 raw.giteeusercontent.com 签名 CDN——
    本机对那个 CDN 直接拒连）；API 不可用时回退到 /raw/<branch>/<file> 纯文本。
    """
    base = base_url()
    repo_suffix = f"/{REPO}"
    api_base = base[: -len(repo_suffix)] if base.endswith(repo_suffix) else base
    api_url = f"{api_base}/api/v5/repos/{REPO}/contents/{VERSION_FILE}?ref={BRANCH}"
    raw_url = f"{base}/raw/{BRANCH}/{VERSION_FILE}"
    errors: list[str] = []
    with _client(8.0) as client:
        try:
            resp = client.get(api_url)
            resp.raise_for_status()
            payload = resp.json()
            content = payload.get("content") or ""
            latest = _version_from_text(base64.b64decode(content).decode("utf-8", "replace"))
            if latest:
                return latest, api_url, ""
            errors.append("API 返回里没有 __version__")
        except Exception as exc:
            errors.append(f"API {type(exc).__name__}: {exc}")
        try:
            resp = client.get(raw_url)
            resp.raise_for_status()
            latest = _version_from_text(resp.text)
            if latest:
                return latest, raw_url, ""
            errors.append("raw 文件里没有 __version__")
        except Exception as exc:
            errors.append(f"raw {type(exc).__name__}: {exc}")
    return "", "", "读取远程版本失败：" + "；".join(errors)


def check_update() -> dict[str, Any]:
    """读取 Gitee master 上的版本号并与本机比较。网络失败返回 error，不抛异常。"""
    local = local_version()
    latest, url, error = _fetch_remote_version()
    if error or not latest:
        return {"local": local, "latest": "", "hasUpdate": False,
                "error": error or "远程版本为空", "checkedAt": _now()}
    return {
        "local": local,
        "latest": latest,
        "hasUpdate": parse_version(latest) > parse_version(local),
        "url": url,
        "checkedAt": _now(),
    }


def status() -> dict[str, Any]:
    with STATE_LOCK:
        return dict(_state)


def _set(**fields: Any) -> None:
    with STATE_LOCK:
        _state.update(fields)
        _state["updatedAt"] = _now()


def _write_progress(meta_path: Path, payload: dict[str, Any]) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 256), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _download_blocking(data_dir: Path, version: str) -> None:
    root = package_dir(data_dir, version)
    root.mkdir(parents=True, exist_ok=True)
    part = root / "pkg.zip.part"
    final = root / "pkg.zip"
    meta_path = root / "pkg.json"
    url = f"{base_url()}{ARCHIVE_PATH}"

    existing = part.stat().st_size if part.exists() else 0
    headers = {"Range": f"bytes={existing}-"} if existing else {}
    _set(phase="downloading", version=version, downloaded=existing, total=0,
         resumable=bool(existing), message="开始下载")

    with _client(30.0) as client:
        with client.stream("GET", url, headers=headers) as resp:
            if resp.status_code == 416:  # 续传位置已越界，按已完成处理
                if part.exists():
                    shutil.move(str(part), str(final))
                raise RuntimeError("range-not-satisfiable")
            resp.raise_for_status()
            if existing and resp.status_code == 200:
                # 服务端不支持 Range：只能从头下
                existing = 0
                part.unlink(missing_ok=True)
                _set(resumable=False)
            total = 0
            content_range = resp.headers.get("content-range", "")
            match = re.search(r"/(\d+)$", content_range)
            if match:
                total = int(match.group(1))
            elif resp.headers.get("content-length"):
                total = existing + int(resp.headers["content-length"])
            _set(total=total, downloaded=existing)

            mode = "ab" if existing else "wb"
            written = existing
            last_flush = 0.0
            with part.open(mode) as handle:
                for chunk in resp.iter_bytes(64 * 1024):
                    handle.write(chunk)
                    written += len(chunk)
                    now = time.time()
                    if now - last_flush > 0.4:
                        last_flush = now
                        _set(downloaded=written, total=total or written)
                        _write_progress(meta_path, {
                            "version": version, "downloaded": written, "total": total or written,
                            "etag": resp.headers.get("etag", ""), "resumable": resp.status_code == 206 or bool(headers),
                            "updatedAt": _now(),
                        })
    if final.exists():
        final.unlink()
    shutil.move(str(part), str(final))
    size = final.stat().st_size
    _set(phase="verifying", downloaded=size, total=size or size, message="校验文件")
    digest = _sha256(final)
    _write_progress(meta_path, {"version": version, "downloaded": size, "total": size,
                                "sha256": digest, "updatedAt": _now()})
    _set(phase="ready", message="下载完成", downloaded=size, total=size)
    _log("update-download", "更新包已下载并校验", version=version, bytes=size, sha256=digest[:16])


def start_download(data_dir: Path, version: str) -> dict[str, Any]:
    """后台线程下载，重复点击不会开第二个。返回当前状态。"""
    global _worker
    with STATE_LOCK:
        if _worker is not None and _worker.is_alive():
            return dict(_state)
        _state.update(phase="downloading", version=version, downloaded=0, total=0,
                      message="开始下载", updatedAt=_now())
        thread = threading.Thread(target=_run_download, args=(data_dir, version), daemon=True)
        _worker = thread
    thread.start()
    return status()


def _run_download(data_dir: Path, version: str) -> None:
    try:
        _log("update-download-start", "开始下载更新包", version=version)
        _download_blocking(data_dir, version)
        prepare_pending(data_dir, version)
    except Exception as exc:
        _set(phase="error", message=f"下载失败：{type(exc).__name__}: {exc}")
        _log("update-download", "下载失败", version=version, error=f"{type(exc).__name__}: {exc}")


def _log(event: str, message: str, **fields: Any) -> None:
    try:
        from ..logging_setup import log_deploy_event

        log_deploy_event(event, message, **fields)
    except Exception:  # 日志失败不能影响主流程
        pass


def _safe_members(zf: zipfile.ZipFile, prefix: str) -> list[zipfile.ZipInfo]:
    members: list[zipfile.ZipInfo] = []
    for info in zf.infolist():
        name = info.filename
        if not name.startswith(prefix):
            continue
        rel = name[len(prefix):]
        if not rel or rel.endswith("/"):
            continue
        if ".." in Path(rel).parts:
            continue
        top = Path(rel).parts[0]
        if top in EXCLUDED_TOP or Path(rel).name in EXCLUDED_NAMES:
            continue
        members.append(info)
    return members


def stage(data_dir: Path, version: str) -> dict[str, Any]:
    """把 pkg.zip 解到 staging/，返回文件清单。已解过的直接复用。"""
    root = package_dir(data_dir, version)
    pkg = root / "pkg.zip"
    if not pkg.exists():
        raise FileNotFoundError("更新包不存在，请先下载")
    staging = root / "staging"
    marker = root / "staged.json"
    if marker.exists() and staging.exists():
        cached: dict[str, Any] = json.loads(marker.read_text(encoding="utf-8"))
        if cached.get("version") == version:
            _set(phase="ready", message="已暂存，重启后生效", version=version)
            return cached
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    files: list[str] = []
    with zipfile.ZipFile(pkg) as zf:
        names = zf.namelist()
        prefix = names[0].split("/")[0] + "/" if names else ""
        for info in _safe_members(zf, prefix):
            rel = info.filename[len(prefix):]
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, target.open("wb") as dst:
                shutil.copyfileobj(src, dst)
            files.append(rel)
    payload = {"version": version, "stagedAt": _now(), "files": files,
               "sha256": _sha256(pkg)}
    marker.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _set(phase="ready", message="已暂存，重启后生效", version=version)
    _log("update-stage", "更新包已解压暂存", version=version, files=len(files))
    return payload


def prepare_pending(data_dir: Path, version: str) -> Path:
    """写 pending-update.json；run.py 启动时由 update_bootstrap 读取并应用。"""
    staged = stage(data_dir, version)
    path = pending_path(data_dir)
    payload = {
        "version": version,
        "packageDir": str(package_dir(data_dir, version)),
        "stagingDir": str(package_dir(data_dir, version) / "staging"),
        "fileCount": len(staged.get("files", [])),
        "sha256": staged.get("sha256", ""),
        "createdAt": _now(),
        "state": "pending",
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    _log("update-pending", "更新已就绪，等待下次启动应用", version=version,
         files=payload["fileCount"])
    return path


def pending_info(data_dir: Path) -> dict[str, Any] | None:
    path = pending_path(data_dir)
    if not path.exists():
        return None
    try:
        info: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return info
    except Exception:
        return None
