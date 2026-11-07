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

import logging
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime, timezone
from json import loads
from pathlib import Path
from typing import Any, Callable
from urllib.error import URLError
from urllib.request import urlopen

from ..common.automation_frameworks import (
    normalize_platforms,
    options_for,
)
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
    RUN_STATUS_CANCELLED,
    AutomationRunRepository,
    AutomationScriptRepository,
)
from ..repositories.sys_config_repo import SysConfigRepository
from . import automation_writeback

logger = logging.getLogger(__name__)

#: 配置键
CONFIG_PYTHON_INTERPRETER = "automation.python.interpreter"
CONFIG_PYTHON_TIMEOUT = "automation.python.timeout"
CONFIG_JMETER_HOME = "performance.jmeter.home"
CONFIG_JMETER_ARGS = "performance.jmeter.args"
#: App 自动化依赖的外部命令行；留空则按 PATH 找 appium / hdc / adb
CONFIG_APPIUM_COMMAND = "automation.appium.command"
CONFIG_APPIUM_SERVER = "automation.appium.server"
CONFIG_HDC_COMMAND = "automation.hdc.command"
CONFIG_ADB_COMMAND = "automation.adb.command"

#: Appium 服务默认地址；只在用户显式执行 App 脚本时访问
DEFAULT_APPIUM_SERVER = "http://127.0.0.1:4723"

#: 解释器候选上限，避免把机器上所有 Python 都拉起来体检
MAX_INTERPRETER_CANDIDATES = 10

#: 框架命令行工具 → 配置键，用户本机装了但不在 PATH 上时用它指定
FRAMEWORK_COMMAND_KEYS = {
    "appium": CONFIG_APPIUM_COMMAND,
    "hdc": CONFIG_HDC_COMMAND,
}

#: 除了 pip 装包之外还要执行的准备命令；{python} 会替换成当前解释器
FRAMEWORK_SETUP_COMMANDS = {
    "playwright": ("{python} -m playwright install chromium",),
    "appium": ("npm install -g appium", "appium"),
    "uiautomator2": ("{python} -m uiautomator2 init",),
}

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
    appium = appium_server_status(conn)
    adb = adb_command(conn)
    hdc = hdc_command(conn)
    return {
        "pythonInterpreter": interpreter,
        "pythonInterpreterConfigured": bool(_config_value(conn, CONFIG_PYTHON_INTERPRETER)),
        "pythonInterpreterAvailable": interpreter_ok,
        "pythonTimeout": python_timeout(conn),
        "jmeterHome": _config_value(conn, CONFIG_JMETER_HOME),
        "jmeterCommand": jmeter,
        "jmeterArgs": jmeter_args(conn),
        "jmeterAvailable": jmeter_ok,
        "appiumServerUrl": appium["url"],
        "appiumServerAvailable": appium["available"],
        "appiumServerReady": appium["ready"],
        "adbCommand": adb,
        "adbAvailable": bool(adb),
        "androidDevices": adb_devices(conn),
        "hdcCommand": hdc,
        "hdcAvailable": bool(hdc),
        "hosDevices": hdc_devices(conn),
        "playwrightBrowsersAvailable": playwright_browsers_available(),
        "platform": platform.system(),
    }


# ---------------------------------------------------------------------
# 框架运行时体检
# ---------------------------------------------------------------------

#: 一次子进程同时问清解释器版本与所有框架依赖包，免得每个框架起一个进程
_PACKAGE_PROBE = (
    "import importlib.metadata as md, sys\n"
    "print('__python__=' + sys.version.split()[0])\n"
    "for name in sys.argv[1:]:\n"
    "    try:\n"
    "        print(name + '=' + md.version(name))\n"
    "    except Exception:\n"
    "        print(name + '=')\n"
)


def _probe_interpreter(interpreter: str, packages: list[str]) -> tuple[str, dict[str, str]]:
    """返回 (Python 版本, {包名: 版本})；解释器不可用时版本为空。"""
    if not interpreter:
        return "", {}
    try:
        completed = subprocess.run(  # noqa: S603 - 参数走列表
            [interpreter, "-c", _PACKAGE_PROBE, *packages],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return "", {}
    version = ""
    result: dict[str, str] = {}
    for line in (completed.stdout or "").splitlines():
        name, _, value = line.partition("=")
        name = name.strip()
        if name == "__python__":
            version = value.strip()
        elif name:
            result[name] = value.strip()
    return version, result


def _probe_packages(interpreter: str, packages: list[str]) -> dict[str, str]:
    return _probe_interpreter(interpreter, packages)[1]


def _py_launcher_paths() -> list[str]:
    """Windows 的 py 启动器登记了本机所有 Python，用它补齐候选清单。"""
    if platform.system() != "Windows":
        return []
    launcher = shutil.which("py")
    if not launcher:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 - 参数走列表
            [launcher, "-0p"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    paths: list[str] = []
    for line in (completed.stdout or "").splitlines():
        match = re.search(r"([A-Za-z]:\\.+?\.exe)\s*$", line)
        if match:
            paths.append(match.group(1))
    return paths


def _python_candidates(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """本机可尝试的 Python 解释器：已配置 → 服务自身 → PATH → py 启动器。"""
    seen: set[str] = set()
    items: list[dict[str, str]] = []

    def add(raw: str | None, source: str) -> None:
        text = str(raw or "").strip()
        if not text:
            return
        path = Path(text).expanduser()
        if not path.is_file():
            return
        try:
            key = str(path.resolve()).lower()
        except OSError:
            key = str(path).lower()
        if key in seen:
            return
        seen.add(key)
        items.append({"path": str(path), "source": source})

    add(_config_value(conn, CONFIG_PYTHON_INTERPRETER), "已指定")
    add(sys.executable, "AITP 服务解释器")
    add(shutil.which("python"), "PATH")
    add(shutil.which("python3"), "PATH")
    for path in _py_launcher_paths():
        add(path, "Python Launcher")
    return items[:MAX_INTERPRETER_CANDIDATES]


def interpreter_candidates(conn: sqlite3.Connection,
                           target: str | None = None) -> list[dict[str, Any]]:
    """本机解释器体检：每个解释器装了哪些框架依赖包。

    这是「我明明装了却提示没装」的根因所在：服务只认自己配置的解释器，
    用户 pip 装到了另一个 Python 里。把候选解释器与包版本都摊开，页面才能
    直接给出「换用哪个解释器」的选择。
    """
    packages = sorted({item["package"] for item in options_for(target) if item["package"]})
    current = str(Path(python_interpreter(conn)).resolve()).lower()
    result: list[dict[str, Any]] = []
    for item in _python_candidates(conn):
        version, installed = _probe_interpreter(item["path"], packages)
        if not version:
            continue
        resolved = str(Path(item["path"]).resolve()).lower()
        result.append({
            "path": item["path"],
            "source": item["source"],
            "version": version,
            "isCurrent": resolved == current,
            "packages": {name: installed.get(name, "") for name in packages},
            "installedCount": sum(1 for name in packages if installed.get(name)),
            "totalPackages": len(packages),
        })
    result.sort(key=lambda row: (
        not row["isCurrent"], -int(row["installedCount"]), str(row["path"]).lower()
    ))
    return result


def set_python_interpreter(conn: sqlite3.Connection, path: str) -> str:
    """切换执行用的 Python 解释器；路径必须先证明自己是能跑的 Python。"""
    text = str(path or "").strip()
    if not text:
        raise AppError(ErrorCode.PARAM_ERROR, "请提供解释器路径")
    candidate = Path(text).expanduser()
    if not candidate.is_file():
        raise AppError(ErrorCode.PARAM_ERROR, "解释器路径不存在，请检查后重试")
    version, _ = _probe_interpreter(str(candidate), [])
    if not version:
        raise AppError(ErrorCode.PARAM_ERROR, "该路径不是可用的 Python 解释器")
    SysConfigRepository(conn).set_value(
        CONFIG_PYTHON_INTERPRETER, str(candidate), "string",
        description="Web / App / 接口自动化使用的 Python 解释器",
    )
    conn.commit()
    return str(candidate)


def appium_server_status(conn: sqlite3.Connection) -> dict[str, Any]:
    """Appium Server 是否已启动；没启动时执行 App 脚本必然连接失败。"""
    url = (_config_value(conn, CONFIG_APPIUM_SERVER) or DEFAULT_APPIUM_SERVER).rstrip("/")
    available = False
    ready = False
    try:
        with urlopen(f"{url}/status", timeout=2) as response:  # noqa: S310 - 地址来自本机配置
            payload = loads(response.read().decode("utf-8", errors="replace") or "{}")
        available = True
        value = payload.get("value") if isinstance(payload, dict) else None
        ready = bool(value.get("ready")) if isinstance(value, dict) else True
    except (OSError, URLError, ValueError):
        pass
    return {"url": url, "available": available, "ready": ready}


def adb_command(conn: sqlite3.Connection) -> str:
    configured = _config_value(conn, CONFIG_ADB_COMMAND)
    if configured:
        expanded = Path(configured).expanduser()
        return str(expanded) if expanded.is_file() else ""
    return shutil.which("adb") or ""


def adb_devices(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """已连接的 Android 设备；接不上 adb 时返回空列表，由页面提示。"""
    command = adb_command(conn)
    if not command:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 - 参数走列表
            [command, "devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    devices: list[dict[str, str]] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) >= 2:
            devices.append({"serial": parts[0], "state": parts[1]})
    return devices


def hdc_command(conn: sqlite3.Connection) -> str:
    """鸿蒙 hdc 命令行工具：先认设置里的绝对路径，再按 PATH 找。"""
    configured = _config_value(conn, CONFIG_HDC_COMMAND)
    if configured:
        expanded = Path(configured).expanduser()
        return str(expanded) if expanded.is_file() else ""
    return shutil.which("hdc") or ""


def hdc_devices(conn: sqlite3.Connection) -> list[dict[str, str]]:
    """已连接的鸿蒙设备；hdc 不在时返回空列表，由页面提示。"""
    command = hdc_command(conn)
    if not command:
        return []
    try:
        completed = subprocess.run(  # noqa: S603 - 参数走列表
            [command, "list", "targets"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=15,
            shell=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return []
    devices: list[dict[str, str]] = []
    for line in (completed.stdout or "").splitlines():
        parts = line.split()
        if not parts or parts[0] in {"Empty", "[Empty]"}:
            continue
        devices.append({
            "serial": parts[0],
            "state": parts[1] if len(parts) > 1 else "Connected",
        })
    return devices


def playwright_browsers_available() -> bool:
    """Playwright 浏览器内核是否下过；只查目录，不启动浏览器。"""
    env_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").strip()
    if env_path and env_path != "0":
        root = Path(env_path)
    elif platform.system() == "Windows":
        root = Path(os.environ.get("LOCALAPPDATA", "")) / "ms-playwright"
    else:
        root = Path.home() / ".cache" / "ms-playwright"
    try:
        return root.is_dir() and any(
            child.is_dir() and child.name.lower().startswith("chromium")
            for child in root.iterdir()
        )
    except OSError:
        return False


def _install_command(interpreter: str, package: str) -> str:
    if not package:
        return ""
    return f'"{interpreter}" -m pip install {package}'


def framework_command_path(conn: sqlite3.Connection, command: str) -> str:
    """框架依赖的命令行工具：先认设置里填的绝对路径，没填就按 PATH 找。"""
    if not command:
        return ""
    key = FRAMEWORK_COMMAND_KEYS.get(command)
    configured = _config_value(conn, key) if key else ""
    if configured:
        expanded = Path(configured).expanduser()
        return str(expanded) if expanded.is_file() else ""
    return shutil.which(command) or ""


def framework_runtimes(conn: sqlite3.Connection, target: str | None = None,
                       platforms: list[str] | str | None = None) -> list[dict[str, Any]]:
    """框架清单加本机可用性：App 自动化页与设置页用它显示「装没装、缺什么」。"""
    options = options_for(target, normalize_platforms(platforms))
    interpreter = python_interpreter(conn)
    packages = sorted({item["package"] for item in options if item["package"]})
    installed = _probe_packages(interpreter, packages)
    browsers = playwright_browsers_available()
    appium = appium_server_status(conn) if any(item["code"] == "appium" for item in options) else None
    jmeter = jmeter_command(conn) if any(item["code"] == "jmeter" for item in options) else None
    for item in options:
        package = str(item["package"])
        command = str(item["command"])
        version = installed.get(package, "") if package else ""
        path = framework_command_path(conn, command) if command else ""
        item["packageVersion"] = version
        item["packageInstalled"] = bool(version) if package else True
        item["commandPath"] = path
        item["commandAvailable"] = bool(path) if command else True
        item["interpreter"] = interpreter
        item["installCommand"] = _install_command(interpreter, package)
        item["setupCommands"] = [
            command.replace("{python}", f'"{interpreter}"')
            for command in FRAMEWORK_SETUP_COMMANDS.get(item["code"], ())
        ]
        missing: list[str] = []
        if not item["packageInstalled"]:
            missing.append(f"Python 包 {package}")
        if not item["commandAvailable"]:
            missing.append(f"命令行工具 {command}")
        if item["code"] == "playwright":
            item["browserAvailable"] = browsers
            if not browsers:
                missing.append("浏览器内核")
        if item["code"] == "appium" and appium is not None:
            item["serverUrl"] = appium["url"]
            item["serverAvailable"] = appium["available"]
            if not appium["available"]:
                missing.append(f"Appium Server（{appium['url']}）")
        # JMeter 不走包与命令行名，靠设置里的安装根目录推出 jmeter.bat；
        # 这里必须和 runtime 的 jmeterAvailable 同口径，否则页面一处说就绪、一处说没装。
        if item["code"] == "jmeter" and jmeter is not None:
            item["commandPath"] = jmeter
            item["commandAvailable"] = bool(jmeter)
            if not jmeter:
                missing.append("JMeter 安装根目录")
        item["missing"] = missing
        item["ready"] = not missing
    return options


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


def list_run_artifacts(db_path: Path, run_no: str, limit: int = 200) -> list[dict[str, Any]]:
    """执行目录里的产物（截图、日志、附件），用于失败复盘。"""
    root = automation_root(db_path) / "runs" / run_no
    if not root.is_dir():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.name == "junit.xml":
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        items.append({
            "path": path.relative_to(root).as_posix(),
            "name": path.name,
            "size": int(stat.st_size),
            "modifiedAt": datetime.fromtimestamp(
                stat.st_mtime, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z",
            "mtime": stat.st_mtime,
        })
        if len(items) >= limit:
            break
    items.sort(key=lambda item: (-float(item["mtime"]), str(item["path"])))
    for item in items:
        item.pop("mtime", None)
    return items


def artifact_file(db_path: Path, run_no: str, relative: str) -> Path | None:
    """把请求里的相对路径钉死在本次执行目录内，越界一律拒绝。"""
    root = (automation_root(db_path) / "runs" / run_no).resolve()
    candidate = (root / relative).resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


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


def _kill_process_tree(proc: subprocess.Popen[str]) -> None:
    """强杀子进程及其派生的浏览器/驱动进程，避免取消后残留。"""
    if proc.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(  # noqa: S603 - 固定参数，杀指定 PID 的进程树
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True,
            check=False,
        )
    try:
        proc.kill()
    except OSError:
        pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


def _run_child(
    command: list[str],
    *,
    cwd: str,
    env: dict[str, str],
    limit: int,
    cancel_check: Callable[[], bool] | None,
) -> tuple[int, str, str, bool]:
    """跑一个子进程；有取消回调时按秒轮询，返回 (退出码, stdout, stderr, 是否取消)。"""
    if cancel_check is None:
        completed = subprocess.run(  # noqa: S603 - 参数走列表，不用 shell
            command,
            cwd=cwd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=limit,
            shell=False,
            env=env,
        )
        return int(completed.returncode), completed.stdout or "", completed.stderr or "", False

    creationflags = (
        getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) if os.name == "nt" else 0
    )
    proc = subprocess.Popen(  # noqa: S603 - 参数走列表，不用 shell
        command,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        shell=False,
        env=env,
        creationflags=creationflags,
    )
    deadline = time.monotonic() + limit
    while True:
        try:
            stdout, stderr = proc.communicate(timeout=1)
            return int(proc.returncode or 0), stdout or "", stderr or "", False
        except subprocess.TimeoutExpired:
            if cancel_check():
                _kill_process_tree(proc)
                stdout, stderr = proc.communicate()
                return int(proc.returncode or -1), stdout or "", stderr or "", True
            if time.monotonic() >= deadline:
                _kill_process_tree(proc)
                stdout, stderr = proc.communicate()
                raise subprocess.TimeoutExpired(command, limit, output=stdout, stderr=stderr)


def execute_script(
    conn: sqlite3.Connection,
    script: dict[str, Any],
    db_path: Path,
    timeout_seconds: int | None = None,
    test_run_id: int | None = None,
    cancel_check: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """执行一个脚本，返回执行记录。失败也返回记录，不抛业务异常。

    配置了覆盖用例（或指定了轮次）时，执行结束会把结果回写到用例 / 轮次。
    """
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
        "test_run_id": test_run_id,
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
            if str(script.get("run_mode") or "") == RUN_MODE_PYTEST and not any(
                str(item).startswith("--junitxml") for item in command
            ):
                command.append(f"--junitxml={run_dir / 'junit.xml'}")
            limit = timeout_seconds or python_timeout(conn)

        command_line = " ".join(command)
        exit_code, stdout, stderr, cancelled = _run_child(
            command,
            cwd=str(run_dir),
            env=_child_env(
                run_dir=run_dir, script_dir=script_dir,
                script=script, test_run_id=test_run_id,
            ),
            limit=limit,
            cancel_check=cancel_check,
        )
        stdout = _truncate(stdout)
        stderr = _truncate(stderr)
        if cancelled:
            status = RUN_STATUS_CANCELLED
            error_message = "用户取消"
        elif exit_code == 0:
            status = RUN_STATUS_SUCCESS
        else:
            status = RUN_STATUS_FAILED
            if not stderr.strip():
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

    if test_run_id or str(script.get("case_match_value") or "").strip():
        _writeback(
            conn, script, run_id, db_path, test_run_id=test_run_id,
            exit_code=exit_code, status=status, stdout=stdout, stderr=stderr,
            error_message=error_message,
        )

    return AutomationRunRepository(conn).get(run_id) or {}


def _writeback(
    conn: sqlite3.Connection,
    script: dict[str, Any],
    run_id: int,
    db_path: Path,
    *,
    test_run_id: int | None,
    exit_code: int | None,
    status: str,
    stdout: str,
    stderr: str,
    error_message: str,
) -> None:
    """回写失败不能把执行留痕一起弄丢，因此单独收口并记下失败原因。"""
    run_row = AutomationRunRepository(conn).get(run_id) or {}
    try:
        automation_writeback.writeback(
            conn,
            script,
            run_row,
            db_path,
            test_run_id=test_run_id,
            exit_code=exit_code,
            run_status=status,
            stdout=stdout,
            stderr=stderr,
            error_message=error_message,
        )
        conn.commit()
    except Exception as exc:  # noqa: BLE001 - 回写异常要留在执行记录里
        logger.exception("自动化结果回写失败：run=%s", run_id)
        conn.rollback()
        conn.execute(
            "UPDATE biz_automation_run SET test_run_id = ?, writeback_status = 'failed',"
            " writeback_summary = ? WHERE id = ?",
            (test_run_id, f"回写失败：{exc}", run_id),
        )
        conn.commit()


def _as_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _child_env(*, run_dir: Path | None = None, script_dir: Path | None = None,
               script: dict[str, Any] | None = None,
               test_run_id: int | None = None) -> dict[str, str]:
    """子进程环境：继承系统环境，但去掉可能带凭据的 AI 变量。

    自动化脚本是用户自己的代码，但没必要让它顺手拿到 AI 密钥。
    运行目录通过 AITP_RUN_DIR 暴露，截图、日志等证据直接落在本次执行目录里。
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
    if run_dir is not None:
        env["AITP_RUN_DIR"] = str(run_dir)
    if script_dir is not None:
        env["AITP_SCRIPT_DIR"] = str(script_dir)
    if script is not None:
        env["AITP_SCRIPT_NO"] = str(script.get("script_no") or "")
        env["AITP_CATEGORY"] = str(script.get("category") or "")
    if test_run_id:
        env["AITP_TEST_RUN_ID"] = str(test_run_id)
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


def require_executable(conn: sqlite3.Connection, category: str, framework: str = "") -> None:
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
    # 三类 Python 脚本都要体检：声明了框架就先把依赖问清楚，
    # 免得 Web / 接口脚本跑到一半才抛 ModuleNotFoundError。
    _require_framework_ready(conn, category, framework)


def _require_framework_ready(conn: sqlite3.Connection, category: str, framework: str) -> None:
    """脚本声明了框架就先把依赖问清楚，免得跑到一半才报 ModuleNotFoundError。"""
    code = str(framework or "").strip()
    if not code:
        return
    item = next((f for f in framework_runtimes(conn, category) if f["code"] == code), None)
    if item is None or item["ready"]:
        return
    missing = item.get("missing") or []
    detail = "、".join(missing) if missing else "依赖"
    raise AppError(
        ErrorCode.ILLEGAL_OPERATION,
        f"框架「{item['label']}」还没就绪：缺 {detail}。"
        f"{item['setupHint']}（当前解释器：{item.get('interpreter') or python_interpreter(conn)}）",
    )
