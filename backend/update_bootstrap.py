"""启动时应用「待生效」更新（只依赖标准库，必须在导入 aitp 之前执行）。

run.py 的调用顺序决定了新代码能否在同一次启动生效：
    1) 读 data/updates/pending-update.json；
    2) 被覆盖的文件先备份到 data/updates/backup-<时间>/；
    3) 从 staging 拷贝到仓库根（排除 data/.env/.venv/node_modules/...）；
    4) 删除 pending 标记，改用 applied-<版本>.json 留档；
    5) 前端源码变了就尝试 npm run build，失败只记日志（可用 start.bat --build 补救）。
任何一步失败都保留 pending 标记，下次启动会重试，不会留下半更新状态外的静默失败。
"""

from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime
from pathlib import Path

EXCLUDED_TOP = {".git", ".venv", "data", "logs", "node_modules", ".idea", ".vscode"}
EXCLUDED_NAMES = {".env", ".env.local", ".secret_key"}


def _now() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S,%f")[:-3]


def _log(repo_root: Path, event: str, message: str, **fields: object) -> None:
    try:
        log_dir = repo_root / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        extra = " | " + " ".join(f"{key}={value}" for key, value in fields.items()) if fields else ""
        line = f"{_now()} | INFO    | DEPLOY | - | [{event}] {message}{extra}\n"
        with (log_dir / "deploy.log").open("a", encoding="utf-8") as handle:
            handle.write(line)
    except Exception:  # 日志失败不能阻断启动
        pass


def _build_frontend(repo_root: Path) -> None:
    frontend = repo_root / "frontend"
    if not (frontend / "package.json").exists():
        return
    npm = shutil.which("npm") or shutil.which("npm.cmd")
    if not npm or not (frontend / "node_modules").exists():
        _log(repo_root, "update-apply", "前端依赖未就绪，跳过自动构建；请用 start.bat --build 重启以重建前端")
        return
    try:
        subprocess.run([npm, "run", "build"], cwd=str(frontend), capture_output=True, timeout=900, check=True)
        _log(repo_root, "update-apply", "前端已随更新重建")
    except Exception as exc:
        _log(repo_root, "update-apply", "前端构建失败，请手动 start.bat --build", error=type(exc).__name__)


def apply_pending_update(repo_root: Path) -> None:
    data_dir = repo_root / "data"
    pending = data_dir / "updates" / "pending-update.json"
    if not pending.exists():
        return
    try:
        payload = json.loads(pending.read_text(encoding="utf-8"))
    except Exception as exc:
        _log(repo_root, "update-apply-failed", "待生效更新文件损坏，已跳过", error=type(exc).__name__)
        return
    version = str(payload.get("version") or "")
    if payload.get("state") != "pending":
        pending.unlink(missing_ok=True)
        return
    staging = Path(str(payload.get("stagingDir") or ""))
    if not version or not staging.exists():
        _log(repo_root, "update-apply-failed", "暂存目录不存在，保留待生效标记", version=version)
        return

    backup = staging.parent / f"backup-{datetime.now():%Y%m%d-%H%M%S}"
    applied = 0
    try:
        for src in sorted(staging.rglob("*")):
            if not src.is_file():
                continue
            rel = src.relative_to(staging)
            if rel.parts[0] in EXCLUDED_TOP or rel.name in EXCLUDED_NAMES:
                continue
            dst = repo_root / rel
            if dst.exists():
                bak = backup / rel
                bak.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(dst, bak)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            applied += 1
    except Exception as exc:
        _log(repo_root, "update-apply-failed", "应用更新失败，已保留待生效标记以便重试",
             version=version, applied=applied, error=f"{type(exc).__name__}: {exc}")
        return

    payload.update(state="applied", appliedAt=_now(), fileCount=applied,
                   backupDir=backup.name)
    try:
        (data_dir / "updates" / f"applied-{version}.json").write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        pending.unlink(missing_ok=True)
    except Exception as exc:
        _log(repo_root, "update-apply-failed", "更新已覆盖但标记清理失败", error=type(exc).__name__)
    _log(repo_root, "update-apply", "更新已应用，新代码本次启动生效",
         version=version, files=applied, backup=backup.name)
    _build_frontend(repo_root)
