"""基于 git 的版本检查与更新。

应用本身从 git 仓库运行，更新不需要另造下载器：
  - **检查**：`git fetch origin master` 后读 `origin/master:backend/aitp/__init__.py` 的版本；
  - **更新**：`git merge --ff-only origin/master`，本地有未提交改动时明确拒绝而不是覆盖；
  - **断点续传 / 增量 / 回滚**都由 git 提供：拉取是增量的，历史里就有旧版本，
    回滚用 `git reset --hard <旧提交>` 即可，不需要另存备份目录；
  - 更新成功后前端源码有变化会在后台重建，并提示用户重启应用生效。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

REPO = "mikiyi/AITP"
BRANCH = "master"
VERSION_FILE = "backend/aitp/__init__.py"

STATE_LOCK = threading.Lock()
_state: dict[str, Any] = {"phase": "idle", "message": "", "version": "", "updatedAt": ""}
_builder: threading.Thread | None = None


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def repo_root() -> Path:
    # backend/aitp/services/update_service.py -> 仓库根
    return Path(__file__).resolve().parents[3]


NO_GIT_HINT = (
    "本机未安装 git，无法在线更新；可安装 git 后重试，"
    "或从 Gitee/GitHub 手动下载新版本覆盖（更新后执行 start.bat --build）。其余功能不受影响。"
)


def git_executable() -> str:
    """git 可执行文件路径；AITP_GIT_BIN 用于指定路径或测试时模拟缺失。"""
    override = os.environ.get("AITP_GIT_BIN")
    if override:
        return override if Path(override).exists() else ""
    return shutil.which("git") or ""


def _git(root: Path, *args: str, timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    exe = git_executable()
    if not exe:
        return subprocess.CompletedProcess(["git"], 127, "", NO_GIT_HINT)
    try:
        return subprocess.run(
            [exe, "-C", str(root), *args],
            capture_output=True, text=True, timeout=timeout,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return subprocess.CompletedProcess([exe], 127, "", f"git 执行失败：{type(exc).__name__}: {exc}")


def _version_of(text: str) -> str:
    match = re.search(r"""__version__\s*=\s*["']([^"']+)["']""", text)
    return match.group(1) if match else ""


def parse_version(text: str) -> tuple[int, ...]:
    digits = re.findall(r"\d+", str(text or "").lstrip("vV"))
    return tuple(int(part) for part in digits[:4]) if digits else ()


def local_version(root: Path | None = None) -> str:
    path = (root or repo_root()) / VERSION_FILE
    try:
        return _version_of(path.read_text(encoding="utf-8")) or "unknown"
    except OSError:
        from .. import __version__

        return str(__version__)


def _dirty_files(root: Path) -> list[str]:
    result = _git(root, "status", "--porcelain")
    if result.returncode != 0:
        return []
    return [line[3:].strip() for line in result.stdout.splitlines() if line.strip()]


def _branch(root: Path) -> str:
    result = _git(root, "rev-parse", "--abbrev-ref", "HEAD")
    return result.stdout.strip() if result.returncode == 0 else ""


def check(root: Path | None = None) -> dict[str, Any]:
    """fetch 后比较 origin/master 与本机版本。任何异常都返回 error，不抛。"""
    repo = root or repo_root()
    local = local_version(repo)
    info: dict[str, Any] = {
        "local": local, "latest": "", "hasUpdate": False,
        "branch": "", "dirty": [], "behind": 0, "gitAvailable": bool(git_executable()),
        "checkedAt": _now(),
    }
    if not info["gitAvailable"]:
        info["error"] = NO_GIT_HINT
        return info
    inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        info["error"] = ((inside.stderr or "").strip() or NO_GIT_HINT) if inside.returncode == 127 else (
            "当前目录不是 git 仓库，无法在线更新；请用 git clone 重新获取，或手动下载新版本覆盖。")
        return info
    info["branch"] = _branch(repo)
    fetch = _git(repo, "fetch", "origin", BRANCH, "--quiet", timeout=30)
    if fetch.returncode != 0:
        info["error"] = f"拉取远程失败：{(fetch.stderr or fetch.stdout).strip()[:300]}"
        return info
    show = _git(repo, "show", f"origin/{BRANCH}:{VERSION_FILE}")
    if show.returncode != 0:
        info["error"] = f"读取远程版本失败：{(show.stderr or show.stdout).strip()[:300]}"
        return info
    latest = _version_of(show.stdout)
    count = _git(repo, "rev-list", "--count", f"HEAD..origin/{BRANCH}")
    info.update(
        latest=latest or "",
        hasUpdate=bool(latest) and parse_version(latest) > parse_version(local),
        behind=int(count.stdout.strip() or 0) if count.returncode == 0 else 0,
        dirty=_dirty_files(repo),
    )
    if not latest:
        info["error"] = "远程版本文件里没有 __version__"
    return info


def status() -> dict[str, Any]:
    with STATE_LOCK:
        return dict(_state)


def _set(**fields: Any) -> None:
    with STATE_LOCK:
        _state.update(fields)
        _state["updatedAt"] = _now()


def _log(event: str, message: str, **fields: Any) -> None:
    try:
        from ..logging_setup import log_deploy_event

        log_deploy_event(event, message, **fields)
    except Exception:
        pass


def apply(root: Path | None = None) -> dict[str, Any]:
    """快进合并到 origin/master。工作区脏时拒绝，返回需要用户处理的文件清单。"""
    repo = root or repo_root()
    if not git_executable():
        return {"applied": False, "needsRestart": False, "dirty": [], "error": NO_GIT_HINT}
    inside = _git(repo, "rev-parse", "--is-inside-work-tree")
    if inside.returncode != 0 or inside.stdout.strip() != "true":
        return {"applied": False, "needsRestart": False, "dirty": [],
                "error": ((inside.stderr or "").strip() or NO_GIT_HINT) if inside.returncode == 127 else (
                    "当前目录不是 git 仓库，无法在线更新；请用 git clone 重新获取，或手动下载新版本覆盖。")}
    dirty = _dirty_files(repo)
    if dirty:
        return {"applied": False, "needsRestart": False, "dirty": dirty,
                "error": f"本地有 {len(dirty)} 个未提交改动，先提交或暂存后再更新"}

    fetch = _git(repo, "fetch", "origin", BRANCH, "--quiet", timeout=30)
    if fetch.returncode != 0:
        return {"applied": False, "needsRestart": False, "dirty": [],
                "error": f"拉取远程失败：{(fetch.stderr or fetch.stdout).strip()[:300]}"}

    before = _git(repo, "rev-parse", "HEAD").stdout.strip()
    merge = _git(repo, "merge", "--ff-only", f"origin/{BRANCH}", timeout=120)
    if merge.returncode != 0:
        return {"applied": False, "needsRestart": False, "dirty": [],
                "error": f"无法快进更新（本地分支与 {BRANCH} 已分叉）：{(merge.stderr or merge.stdout).strip()[:300]}"}

    after = _git(repo, "rev-parse", "HEAD").stdout.strip()
    changed = _git(repo, "diff", "--name-only", before, after).stdout.splitlines() if before != after else []
    version = local_version(repo)
    _set(phase="rebuilding", message="代码已更新，正在重建前端", version=version)
    _log("update-apply", "已快进到最新版本", version=version,
         fromRev=before[:7], toRev=after[:7], changed=len(changed))
    _start_rebuild(repo, changed)
    return {"applied": True, "needsRestart": True, "version": version,
            "changed": len(changed), "from": before[:7], "to": after[:7]}


def _start_rebuild(repo: Path, changed: list[str]) -> None:
    global _builder
    with STATE_LOCK:
        if _builder is not None and _builder.is_alive():
            return
        thread = threading.Thread(target=_rebuild, args=(repo, changed), daemon=True)
        _builder = thread
    thread.start()


def _rebuild(repo: Path, changed: list[str]) -> None:
    """前端源码变了就重建；依赖清单变了就给重启前的提示（不在请求里跑 pip）。"""
    if os.environ.get("AITP_UPDATE_SKIP_BUILD") == "1":
        _set(phase="ready", message="已更新，重启应用后生效（门禁模式跳过前端重建）")
        return
    needs_frontend = any(path.startswith("frontend/") for path in changed) or not changed
    try:
        if needs_frontend:
            frontend = repo / "frontend"
            npm = "npm.cmd" if os.name == "nt" else "npm"
            if not (frontend / "node_modules").exists():
                _set(phase="ready", message="代码已更新，前端依赖未安装；请用 start.bat --build 重启")
                return
            _set(phase="rebuilding", message="正在重建前端（约 30–60 秒）")
            result = subprocess.run([npm, "run", "build"], cwd=str(frontend),
                                    capture_output=True, text=True, timeout=900)
            if result.returncode != 0:
                _set(phase="ready", message="代码已更新，但前端重建失败；请用 start.bat --build 重启")
                _log("update-apply", "前端重建失败", error=(result.stderr or "")[-300:])
                return
        if "requirements.txt" in changed:
            _set(phase="ready", message="代码与依赖清单已更新，重启前请执行 pip install -r requirements.txt")
            return
        _set(phase="ready", message="已更新，重启应用后生效")
        _log("update-apply", "更新完成，等待重启生效")
    except Exception as exc:
        _set(phase="ready", message=f"代码已更新，但重建失败：{type(exc).__name__}；请用 start.bat --build 重启")
        _log("update-apply", "重建异常", error=f"{type(exc).__name__}: {exc}")
