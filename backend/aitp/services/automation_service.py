"""自动化脚本执行引擎。

两类执行器：
  - Python 解释器：跑 web / app / 接口三类自动化用例
  - JMeter 命令行：跑 .jmx 性能脚本

三条硬性约束：
  1. 只执行库里登记过的脚本正文，落盘目录由服务端决定，用户不能指定路径
  2. 子进程不用 shell（shell=False），参数走列表，命令拼接没有注入面
  3. 一定有超时，超时按 timeout 收口；输出截断，避免大结果把库撑爆
"""

from __future__ import annotations

import os
import platform
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from ..common.time_utils import utcnow_iso
from ..errors import AppError, ErrorCode
from ..repositories.automation_repo import (
    CATEGORY_PERFORMANCE,
    RUN_MODE_JMETER,
    RUN_MODE_PYTEST,
    RUN_STATUS_ERROR,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    RUN_STATUS_SUCCESS,
    RUN_STATUS_TIMEOUT,
    AutomationRunRepository,
    AutomationScriptRepository,
)
from ..repositories.sys_config_repo import SysConfigRepository

#: 配置键
CONFIG_PYTHON_INTERPRETER = "automation.python.interpreter"
CONFIG_PYTHON_TIMEOUT = "automation.python.timeout"
CONFIG_JMETER_HOME = "performance.jmeter.home"
CONFIG_JMETER_ARGS = "performance.jmeter.args"

#: 单次执行默认超时（秒）
DEFAULT_PYTHON_TIMEOUT = 300
#: JMeter 默认超时（秒）——压测比脚本用例慢
DEFAULT_JMETER_TIMEOUT = 900
#: 标准输出 / 错误输出各自的最大保留字符数
MAX_OUTPUT_CHARS = 20000
#: 附加参数最大长度，避免塞超长命令
MAX_ENTRY_ARGS = 1000


class ExecutionError(Exception):
    """执行前置条件不满足（解释器没配、JMeter 没装等）。"""


# ---------------------------------------------------------------------
# 运行时配置
# ---------------------------------------------------------------------

def _config_value(conn: sqlite3.Connection, key: str, default: str = "") -> str:
    return str(SysConfigRepository(conn).get_value(key, default) or "").strip()


def python_interpreter(conn: sqlite3.Connection) -> str:
    """当前生效的 Python 解释器；未配置时回落到本服务自己的解释器。"""
    configured = _config_value(conn, CONFIG_PYTHON_INTERPRETER)
    if configured:
        return configured
    return sys.executable


def python_timeout(conn: sqlite3.Connection) -> int:
    raw = _config_value(conn, CONFIG_PYTHON_TIMEOUT)
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_PYTHON_TIMEOUT
    return value if value > 0 else DEFAULT_PYTHON_TIMEOUT


def jmeter_command(conn: sqlite3.Connection) -> str:
    """从 JMeter 根目录推出可执行文件路径；没配返回空串。"""
    home = _config_value(conn, CONFIG_JMETER_HOME)
    if not home:
        return ""
    root = Path(home).expanduser()
    names = ("jmeter.bat", "jmeter") if platform.system() == "Windows" else ("jmeter", "jmeter.bat")
    for name in names:
        candidate = root / "bin" / name
        if candidate.is_file():
            return str(candidate)
    return ""


def jmeter_args(conn: sqlite3.Connection) -> list[str]:
    """JMeter 附加参数，按空白切分。"""
    raw = _config_value(conn, CONFIG_JMETER_ARGS)
    if not raw:
        return []
    parts = raw.split()
    return parts[:20]


def runtime_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """给设置页看的运行时体检结果：配置了什么、能不能用。"""
    interpreter = python_interpreter(conn)
    interpreter_path = Path(interpreter)
    interpreter_ok = interpreter_path.is_file()
    jmeter = jmeter_command(conn)
    jmeter_ok = bool(jmeter) and Path(jmeter).is_file()
    return {
        "pythonInterpreter": interpreter,
        "pythonInterpreterConfigured": bool(_config_value(conn, CONFIG_PYTHON_INTERPRETER)),
        "pythonInterpreterAvailable": interpreter_ok,
        "pythonTimeout": python_timeout(conn),
        "jmeterHome": _config_value(conn, CONFIG_JMETER_HOME),
        "jmeterCommand": jmeter,
        "jmeterArgs": jmeter_args(conn),
        "jmeterAvailable": jmeter_ok,
        "platform": platform.system(),
    }


# ---------------------------------------------------------------------
# 工作目录
# ---------------------------------------------------------------------

def automation_root(db_path: Path) -> Path:
    """自动化脚本的工作根目录：与数据库同级，便于整体备份与清理。"""
    root = Path(db_path).parent / "automation"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _script_dir(db_path: Path, script_no: str) -> Path:
    path = automation_root(db_path) / "scripts" / script_no
    path.mkdir(parents=True, exist_ok=True)
    return path


def _run_dir(db_path: Path, run_no: str) -> Path:
    path = automation_root(db_path) / "runs" / run_no
    path.mkdir(parents=True, exist_ok=True)
    return path


# ---------------------------------------------------------------------
# 执行
# ---------------------------------------------------------------------

def _truncate(text: str) -> str:
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return text[:MAX_OUTPUT_CHARS] + f"\n...（输出过长已截断，共 {len(text)} 字符）"


def _entry_args(script: dict[str, Any]) -> list[str]:
    """附加参数：切分成列表，禁止任何 shell 语义。"""
    raw = str(script.get("entry_command") or "").strip()
    if not raw:
        return []
    if len(raw) > MAX_ENTRY_ARGS:
        raise ExecutionError("附加参数过长")
    return raw.split()[:20]


def _python_command(conn: sqlite3.Connection, script: dict[str, Any], script_path: Path) -> list[str]:
    interpreter = python_interpreter(conn)
    if not Path(interpreter).is_file():
        raise ExecutionError(
            f"Python 解释器不可用：{interpreter}。请到「设置 → 自动化管理设置」里重新指定解释器路径。"
        )
    mode = str(script.get("run_mode") or "script")
    if mode == RUN_MODE_PYTEST:
        return [interpreter, "-m", "pytest", str(script_path)]
    return [interpreter, str(script_path)]


def _jmeter_command(conn: sqlite3.Connection, script: dict[str, Any],
                    script_path: Path, run_dir: Path) -> list[str]:
    command = jmeter_command(conn)
    if not command or not Path(command).is_file():
        raise ExecutionError(
            "未找到本地 JMeter。请到「设置 → 性能测试管理设置」里填写 JMeter 安装根目录。"
        )
    result_file = run_dir / "result.jtl"
    log_file = run_dir / "jmeter.log"
    return [
        command, "-n", "-t", str(script_path),
        "-l", str(result_file),
        "-j", str(log_file),
        *jmeter_args(conn),
    ]


def _write_script_file(script: dict[str, Any], target_dir: Path) -> Path:
    """把脚本正文落到工作目录；文件名由服务端定，不接受用户路径。"""
    file_name = str(script.get("file_name") or "script.py")
    suffix = Path(file_name).suffix or ".py"
    safe_stem = Path(file_name).stem.replace("/", "_").replace("\\", "_")
    target = target_dir / f"{safe_stem}{suffix}"
    target.write_text(str(script.get("content") or ""), encoding="utf-8")
    return target


def execute_script(
    conn: sqlite3.Connection,
    script: dict[str, Any],
    db_path: Path,
    timeout_seconds: int | None = None,
) -> dict[str, Any]:
    """执行一个脚本，返回执行记录。失败也返回记录，不抛业务异常。"""
    script_id = int(script.get("id") or 0)
    script_no = str(script.get("script_no") or "")
    category = str(script.get("category") or "web")
    run_repo = AutomationRunRepository(conn)
    run_no = run_repo.next_run_no()
    started_at = utcnow_iso()
    start = time.monotonic()

    run_id = run_repo.insert({
        "run_no": run_no,
        "script_id": script_id,
        "script_no": script_no,
        "script_name": str(script.get("name") or ""),
        "category": category,
        "status": RUN_STATUS_RUNNING,
        "started_at": started_at,
    })
    conn.commit()

    status = RUN_STATUS_ERROR
    exit_code: int | None = None
    stdout = ""
    stderr = ""
    error_message = ""
    command_line = ""

    try:
        script_dir = _script_dir(db_path, script_no or f"script-{script_id}")
        run_dir = _run_dir(db_path, run_no)
        script_path = _write_script_file(script, script_dir)

        if category == CATEGORY_PERFORMANCE or script.get("run_mode") == RUN_MODE_JMETER:
            command = _jmeter_command(conn, script, script_path, run_dir)
            limit = timeout_seconds or DEFAULT_JMETER_TIMEOUT
        else:
            command = _python_command(conn, script, script_path)
            command.extend(_entry_args(script))
            limit = timeout_seconds or python_timeout(conn)

        command_line = " ".join(command)
        completed = subprocess.run(  # noqa: S603 - 参数走列表，不用 shell
            command,
            cwd=str(script_dir),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            shell=False,
            env=_child_env(),
        )
        exit_code = int(completed.returncode)
        stdout = _truncate(completed.stdout or "")
        stderr = _truncate(completed.stderr or "")
        status = RUN_STATUS_SUCCESS if exit_code == 0 else RUN_STATUS_FAILED
        if exit_code != 0 and not stderr.strip():
            error_message = f"执行退出码 {exit_code}"
    except subprocess.TimeoutExpired as exc:
        status = RUN_STATUS_TIMEOUT
        error_message = f"执行超时（超过 {limit} 秒）"
        stdout = _truncate(_as_text(exc.stdout))
        stderr = _truncate(_as_text(exc.stderr))
    except ExecutionError as exc:
        status = RUN_STATUS_ERROR
        error_message = str(exc)
    except OSError as exc:
        status = RUN_STATUS_ERROR
        error_message = f"无法启动执行进程：{exc}"

    duration_ms = int((time.monotonic() - start) * 1000)
    run_repo.update(run_id, {
        "status": status,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "command_line": command_line,
        "stdout": stdout,
        "stderr": stderr,
        "error_message": error_message,
        "finished_at": utcnow_iso(),
    })
    conn.commit()

    # 回写脚本上的最近一次执行结果，列表页不用再联表
    AutomationScriptRepository(conn).update(script_id, {
        "last_run_status": status,
        "last_run_at": utcnow_iso(),
    })
    conn.commit()

    return AutomationRunRepository(conn).get(run_id) or {}


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _child_env() -> dict[str, str]:
    """子进程环境：继承系统环境，但去掉可能带凭据的 AI 变量。

    自动化脚本是用户自己的代码，但没必要让它顺手拿到 AI 密钥。
    """
    drop = (
        "AITP_AI_API_KEY",
        "OPENAI_API_KEY",
        "DASHSCOPE_API_KEY",
        "DEEPSEEK_API_KEY",
        "ANTHROPIC_API_KEY",
    )
    env = {k: v for k, v in os.environ.items() if k not in drop}
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    return env


def check_runtime(conn: sqlite3.Connection) -> dict[str, Any]:
    """执行前的体检：解释器与 JMeter 是否就绪。"""
    status = runtime_status(conn)
    status["pythonVersion"] = ""
    if status["pythonInterpreterAvailable"]:
        try:
            completed = subprocess.run(  # noqa: S603 - 参数走列表
                [status["pythonInterpreter"], "--version"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=15,
                shell=False,
            )
            status["pythonVersion"] = (completed.stdout or completed.stderr or "").strip()
        except (OSError, subprocess.TimeoutExpired):
            status["pythonInterpreterAvailable"] = False
    return status


def interpreter_exists(conn: sqlite3.Connection) -> bool:
    return Path(python_interpreter(conn)).is_file()


def jmeter_exists(conn: sqlite3.Connection) -> bool:
    command = jmeter_command(conn)
    return bool(command) and Path(command).is_file()


def clear_run_artifacts(db_path: Path, keep: int = 50) -> int:
    """清理历史执行目录，只保留最近 keep 个；返回删除数量。"""
    runs_dir = automation_root(db_path) / "runs"
    entries = sorted(
        (p for p in runs_dir.iterdir() if p.is_dir()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    removed = 0
    for old in entries[keep:]:
        shutil.rmtree(old, ignore_errors=True)
        removed += 1
    return removed


def require_executable(conn: sqlite3.Connection, category: str) -> None:
    """执行前的前置检查，不满足直接给可读提示。"""
    if category == CATEGORY_PERFORMANCE:
        if not jmeter_exists(conn):
            raise AppError(
                ErrorCode.ILLEGAL_OPERATION,
                "未找到本地 JMeter，请先到「设置 → 性能测试管理设置」填写 JMeter 安装根目录",
            )
        return
    if not interpreter_exists(conn):
        raise AppError(
            ErrorCode.ILLEGAL_OPERATION,
            "Python 解释器不可用，请先到「设置 → 自动化管理设置」指定解释器路径",
        )
