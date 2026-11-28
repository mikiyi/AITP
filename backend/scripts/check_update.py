"""检查更新门禁（基于 git，全程离线）：本地裸仓库充当 origin。

    python backend/scripts/check_update.py

覆盖：
  1. 同版本 → hasUpdate=False、behind=0；
  2. 远端版本升高 → hasUpdate=True、behind=1；
  3. 工作区脏 → check 报出文件、apply 拒绝且不改动 HEAD；
  4. apply → 快进到远端版本、文件内容更新、status=ready；
  5. 本地与远端分叉 → apply 拒绝（非 fast-forward）；
  6. origin 不存在 → check 返回 error 而不是抛异常。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True)


def commit(cwd: Path, message: str) -> None:
    git(cwd, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "add", "-A")
    result = git(cwd, "-c", "user.name=gate", "-c", "user.email=gate@example.com", "commit", "-m", message)
    assert result.returncode == 0, result.stderr


def write_version(repo: Path, version: str) -> None:
    target = repo / "backend" / "aitp" / "__init__.py"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(f'__version__ = "{version}"\n', encoding="utf-8")


def main() -> int:
    from aitp.services import update_service

    os.environ["AITP_UPDATE_SKIP_BUILD"] = "1"  # 门禁不跑 npm
    tmp = Path(tempfile.mkdtemp(prefix="aitp_update_gate_"))
    try:
        bare = tmp / "remote.git"
        git(tmp, "init", "--bare", "-q", str(bare))
        seed = tmp / "seed"
        git(tmp, "clone", "-q", str(bare), str(seed))
        write_version(seed, "2.8.0")
        (seed / "README.md").write_text("v1\n", encoding="utf-8")
        commit(seed, "v2.8.0")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/master")
        git(seed, "branch", "-M", "master")

        work = tmp / "work"
        git(tmp, "clone", "-q", str(bare), str(work))

        same = update_service.check(work)
        check(same.get("latest") == "2.8.0" and same.get("hasUpdate") is False
              and same.get("behind") == 0,
              "同版本 → hasUpdate=False / behind=0", str(same))

        (work / "scratch.txt").write_text("local change\n", encoding="utf-8")
        dirty = update_service.check(work)
        check(any("scratch.txt" in item for item in dirty.get("dirty", [])),
              "工作区脏 → check 报出文件", str(dirty.get("dirty")))
        head_before = git(work, "rev-parse", "HEAD").stdout.strip()
        refused = update_service.apply(work)
        check(bool(refused.get("applied") is False and refused.get("dirty")),
              "工作区脏 → apply 拒绝", str(refused))
        check(git(work, "rev-parse", "HEAD").stdout.strip() == head_before,
              "拒绝后 HEAD 未移动")
        (work / "scratch.txt").unlink()

        write_version(seed, "2.8.1")
        (seed / "backend" / "aitp" / "new_feature.py").write_text("X = 1\n", encoding="utf-8")
        commit(seed, "v2.8.1")
        git(seed, "push", "-q", "origin", "master")

        newer = update_service.check(work)
        check(newer.get("latest") == "2.8.1" and newer.get("hasUpdate") is True
              and newer.get("behind") == 1,
              "远端更高 → hasUpdate=True / behind=1", str(newer))

        applied = update_service.apply(work)
        local_text = (work / "backend" / "aitp" / "__init__.py").read_text(encoding="utf-8")
        check(applied.get("applied") is True and applied.get("version") == "2.8.1",
              "apply 成功并返回新版本", str(applied))
        check('__version__ = "2.8.1"' in local_text, "本地文件已快进到新版本")
        check((work / "backend" / "aitp" / "new_feature.py").exists(), "远端新增文件已落盘")
        check(update_service.status().get("phase") == "ready",
              "status 进入 ready（门禁跳过构建）", str(update_service.status()))

        # 分叉：本地提交一条与远端无关的历史
        (work / "local.txt").write_text("x\n", encoding="utf-8")
        commit(work, "local divergence")
        write_version(seed, "2.8.2")
        commit(seed, "v2.8.2")
        git(seed, "push", "-q", "origin", "master")
        diverged = update_service.apply(work)
        check(diverged.get("applied") is False and "分叉" in str(diverged.get("error", "")),
              "本地分叉 → apply 拒绝并说明原因", str(diverged))

        git(work, "remote", "set-url", "origin", str(tmp / "missing.git"))
        broken = update_service.check(work)
        check(bool(broken.get("error")) and broken.get("hasUpdate") is False,
              "origin 不可达 → error 不抛异常", str(broken))
        os.environ["AITP_GIT_BIN"] = str(tmp / "no-such-git")
        missing = update_service.check(work)
        check(missing.get("gitAvailable") is False and "未安装 git" in str(missing.get("error"))
              and missing.get("hasUpdate") is False,
              "未安装 git → 明确提示且不抛异常", str(missing))
        missing_apply = update_service.apply(work)
        check(missing_apply.get("applied") is False and "未安装 git" in str(missing_apply.get("error")),
              "未安装 git → apply 拒绝并给手动方案", str(missing_apply))
        os.environ.pop("AITP_GIT_BIN", None)

        plain = tmp / "plain"
        plain.mkdir()
        not_repo = update_service.check(plain)
        check("不是 git 仓库" in str(not_repo.get("error")), "非 git 仓库 → 明确提示", str(not_repo))

    finally:
        os.environ.pop("AITP_UPDATE_SKIP_BUILD", None)
        os.environ.pop("AITP_GIT_BIN", None)
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [label for ok, label in RESULTS if not ok]
    print(f"\nPASS={len(RESULTS) - len(fails)} FAIL={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
