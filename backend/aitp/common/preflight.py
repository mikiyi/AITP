"""启动前置自检。

全新机器上首启失败通常是两类：
  - 系统自带 Python 里的 SQLite 太旧或没编 FTS5/trigram，迁移 002 建知识全文索引时抛裸 SQL 错误
  - 数据 / 日志目录不可写（只读挂载、权限、被杀毒软件占用），服务半启动后每个接口都 500

两者都在迁移之前一次判完，报错写清「哪一项、为什么、怎么修」。
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from ..config import AppConfig

#: 知识库全文索引用到 FTS5 的 trigram 分词器，SQLite 3.34.0 起才支持
MIN_SQLITE_VERSION = (3, 34)


class PreflightError(RuntimeError):
    """启动前置自检未通过。

    单独一类异常，入口才能只接住「环境不合格」并打印可读提示，
    不至于把数据库损坏、迁移失败之类的真实堆栈也一并吞成一行。
    """


def _version_tuple(raw: str) -> tuple[int, int, int]:
    """取每段开头的数字；3.40.0-rc1 的第 3 段是 0，不能把后缀里的数字并进来。"""
    parts: list[int] = []
    for chunk in raw.split(".")[:3]:
        digits = ""
        for char in chunk:
            if not char.isdigit():
                break
            digits += char
        parts.append(int(digits) if digits else 0)
    while len(parts) < 3:
        parts.append(0)
    return parts[0], parts[1], parts[2]


def _probe_fts() -> str:
    """探测 FTS5 + trigram；返回空串表示可用，否则返回可读原因。"""
    version = sqlite3.sqlite_version
    if _version_tuple(version) < MIN_SQLITE_VERSION:
        return (
            f"本机 Python 自带的 SQLite 版本为 {version}，低于知识库全文索引要求的 "
            f"{'.'.join(str(part) for part in MIN_SQLITE_VERSION)}（trigram 分词器）"
        )
    try:
        conn = sqlite3.connect(":memory:")
        try:
            conn.execute("CREATE VIRTUAL TABLE probe USING fts5(body, tokenize='trigram')")
        finally:
            conn.close()
    except sqlite3.Error as exc:
        return f"本机 SQLite {version} 不支持 FTS5 trigram 虚拟表：{exc}"
    return ""


def _probe_writable(path: Path, label: str) -> str:
    """实际写一个探针文件；os.access 在 Windows 目录上会误报，不能只用它。"""
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / f".aitp-write-probe-{os.getpid()}"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as exc:
        return f"{label}不可写：{path}（{exc.strerror or exc}）"
    return ""


def check_runtime(config: AppConfig) -> list[str]:
    """返回问题清单，空列表代表可以继续启动。"""
    problems: list[str] = []
    reason = _probe_fts()
    if reason:
        problems.append(
            f"{reason}。请换用 SQLite 足够新的 Python：macOS 用 `brew install python@3.12`，"
            f"Linux 用发行版自带的 python3（过旧时改用 pyenv / conda 编译的 Python），"
            f"Windows 用 python.org 安装包"
        )
    for directory, label in (
        (config.db_path.parent, "数据库目录"),
        (config.log_path.parent, "运行日志目录"),
        (config.deploy_log_path.parent, "部署日志目录"),
        (config.secret_key_path.parent, "密钥文件目录"),
    ):
        message = _probe_writable(directory, label)
        if message:
            problems.append(f"{message}。请检查该目录权限、是否只读挂载或被安全软件锁定")
    if not config.prompts_dir.is_dir():
        problems.append(
            f"提示词目录不存在：{config.prompts_dir}。请确认仓库是否完整（backend/prompts）"
        )
    return problems
