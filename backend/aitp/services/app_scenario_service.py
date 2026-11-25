"""App 场景执行引擎（V2.7.0，补齐最后一条执行路径）。

不另造移动端框架：直接驱动本机 ``adb``（真机 / 模拟器 / 自定义桩均可），
把场景步骤翻译成 ``adb shell`` 命令，UI 文本断言用 ``uiautomator dump``。

设计边界：
  - 驱动命令、设备序列号、超时都来自系统配置，支持指向任意可执行文件；
    这样 CI / E2E 可以用本地桩验证整条链路，不依赖真机。
  - 无设备、命令缺失、超时都返回可读失败原因，不阻断场景其余步骤与人工流程。
  - 每一步都产出与其他步骤一致的轨迹结构（passed/request/response/assertions）。
"""

from __future__ import annotations

import base64
import os
import re
import shlex
import subprocess
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from ..repositories.sys_config_repo import SysConfigRepository
from . import interface_runner

CONFIG_ADB_COMMAND = "app.adb_command"
CONFIG_DEVICE_SERIAL = "app.device_serial"
CONFIG_TIMEOUT = "app.command_timeout"

DEFAULT_TIMEOUT = 20
MAX_OUTPUT_CHARS = 20000
SHOT_DIR_NAME = "app-shots"

ACTIONS = ("launch", "stop", "tap", "input", "swipe", "back", "home",
           "wait", "assertText", "screenshot")

_BOUNDS_RE = re.compile(r"\[(\d+),(\d+)\]\[(\d+),(\d+)\]")


def driver_config(conn: Any) -> dict[str, Any]:
    """读取 App 执行配置；缺省值保证「装了 adb 就能跑」。"""
    repo = SysConfigRepository(conn)
    try:
        timeout = int(str(repo.get_value(CONFIG_TIMEOUT, DEFAULT_TIMEOUT) or DEFAULT_TIMEOUT))
    except (TypeError, ValueError):
        timeout = DEFAULT_TIMEOUT
    return {
        "adbCommand": str(repo.get_value(CONFIG_ADB_COMMAND, "adb") or "adb").strip() or "adb",
        "deviceSerial": str(repo.get_value(CONFIG_DEVICE_SERIAL, "") or "").strip(),
        "timeout": max(3, min(timeout, 120)),
    }


def _run_adb(config: dict[str, Any], args: list[str], text: bool = True) -> tuple[int, str | bytes]:
    """执行一次 adb 命令；返回 (退出码, 输出)。命令缺失/超时抛可读异常。"""
    parts = [item.strip('"') for item in shlex.split(
        config["adbCommand"], posix=(os.name != "nt")
    )]
    if not parts:
        raise RuntimeError("未配置 App 执行命令")
    serial = str(config.get("deviceSerial") or "").strip()
    command = [*parts]
    if serial:
        command += ["-s", serial]
    command += args
    try:
        proc = subprocess.run(
            command, capture_output=True, text=text, timeout=int(config["timeout"]),
            encoding="utf-8" if text else None, errors="replace" if text else None,
        )
    except FileNotFoundError as exc:
        raise RuntimeError(
            f"未找到 App 执行命令「{config['adbCommand']}」，请在「场景自动化 · App 执行配置」里指向 adb"
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"adb 命令超时（>{config['timeout']}s）：{' '.join(args[:2])}") from exc
    output: str | bytes = proc.stdout
    stderr = proc.stderr
    if text:
        text_out = str(output or "") + (str(stderr or "") if proc.returncode else "")
        return proc.returncode, text_out[:MAX_OUTPUT_CHARS]
    return proc.returncode, output


def probe(conn: Any, overrides: dict[str, Any] | None = None) -> dict[str, Any]:
    """探测设备：返回 adb 版本与设备列表，供设置页「测试连接」。

    overrides 允许用页面上尚未保存的 adb 命令 / 设备序列号做试测，
    避免用户必须先保存错误配置才能验证。
    """
    config = driver_config(conn)
    if overrides:
        command = str(overrides.get("adbCommand") or "").strip()
        if command:
            config["adbCommand"] = command
        if "deviceSerial" in overrides:
            config["deviceSerial"] = str(overrides.get("deviceSerial") or "").strip()
    result: dict[str, Any] = {"ok": False, "adbCommand": config["adbCommand"],
                              "deviceSerial": config["deviceSerial"], "devices": [],
                              "message": ""}
    try:
        _, version = _run_adb(config, ["version"])
        _, output = _run_adb(config, ["devices"])
    except RuntimeError as exc:
        result["message"] = str(exc)
        return result
    devices = []
    for line in str(output or "").splitlines():
        parts = line.strip().split()
        if len(parts) >= 2 and parts[1] in ("device", "offline", "unauthorized"):
            devices.append({"serial": parts[0], "state": parts[1]})
    result["devices"] = devices
    result["ok"] = any(item["state"] == "device" for item in devices)
    result["version"] = str(version or "").strip().splitlines()[0] if version else ""
    result["message"] = "" if result["ok"] else "未发现可用设备（state=device）"
    return result


def _dump_ui(config: dict[str, Any]) -> str:
    """导出当前界面 XML：先 dump 到设备，再 cat 回本机（避免 adb pull 的设备路径限制）。"""
    _, out = _run_adb(config, ["shell", "uiautomator", "dump", "/sdcard/aitp_window.xml"])
    dumped = str(out or "")
    if "ERROR" in dumped.upper() and "dumped" not in dumped.lower():
        raise RuntimeError(dumped.strip()[:300] or "界面结构导出失败")
    code, xml = _run_adb(config, ["exec-out", "cat", "/sdcard/aitp_window.xml"])
    if code != 0 or not xml:
        raise RuntimeError("读取界面结构失败，请确认设备已解锁且允许 uiautomator")
    return str(xml)


def _find_node_center(xml: str, target: str) -> tuple[int, int] | None:
    """在界面结构里按文本/描述找节点，返回可点击区域中心；找不到返回 None。"""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    needle = target.strip()
    for node in root.iter("node"):
        text = (node.get("text") or "").strip()
        desc = (node.get("content-desc") or "").strip()
        if needle and needle not in text and needle not in desc:
            continue
        matched = _BOUNDS_RE.search(node.get("bounds") or "")
        if not matched:
            continue
        x1, y1, x2, y2 = (int(v) for v in matched.groups())
        return (x1 + x2) // 2, (y1 + y2) // 2
    return None


def _ok(action: str, target: str, output: str, t0: float,
        assertions: list[dict[str, Any]] | None = None,
        passed: bool = True, message: str = "") -> dict[str, Any]:
    return {
        "passed": passed, "statusCode": None,
        "durationMs": int((time.monotonic() - t0) * 1000),
        "request": {"method": "APP", "url": f"{action} {target}".strip(), "body": ""},
        "response": {"body": output[:interface_runner.MAX_BODY_CHARS],
                     "bodyTruncated": len(output) > interface_runner.MAX_BODY_CHARS},
        "assertions": assertions or [],
        "message": message,
    }


def _fail(action: str, target: str, message: str, t0: float) -> dict[str, Any]:
    return _ok(action, target, "", t0, assertions=[
        interface_runner._make_assertion("App 步骤", False, action, message),
    ], passed=False, message=message)


def run_step(
    conn: Any,
    step: dict[str, Any],
    variables: dict[str, str],
    db_path: Path | None = None,
) -> dict[str, Any]:
    """执行一个 App 步骤；返回与其他步骤一致的轨迹结构。"""
    t0 = time.monotonic()
    config = driver_config(conn)
    serial = str(step.get("serial") or variables.get("deviceSerial") or "").strip()
    if serial:
        config = {**config, "deviceSerial": serial}
    action = str(step.get("action") or "launch").strip()
    target = interface_runner._substitute(str(step.get("target") or ""), variables)
    value = interface_runner._substitute(str(step.get("value") or ""), variables)
    package = interface_runner._substitute(str(step.get("package") or ""), variables)
    activity = interface_runner._substitute(str(step.get("activity") or ""), variables)
    label = target or value or package or action

    if action not in ACTIONS:
        return _fail(action, label, f"不支持的 App 动作：{action}", t0)

    try:
        if action == "wait":
            wait_ms = min(max(int(step.get("waitMs") or 1000), 0), 30000)
            time.sleep(wait_ms / 1000)
            return _ok(action, f"{wait_ms}ms", "", t0)

        if action == "screenshot":
            _, raw = _run_adb(config, ["exec-out", "screencap", "-p"], text=False)
            data = raw if isinstance(raw, bytes) else b""
            if not data:
                return _fail(action, label, "截图失败：设备没有返回图像数据", t0)
            shot_dir = (db_path.parent if db_path else Path.cwd() / "data") / SHOT_DIR_NAME
            shot_dir.mkdir(parents=True, exist_ok=True)
            shot = shot_dir / f"app-{int(time.time() * 1000)}.png"
            shot.write_bytes(data)
            body = f"{shot} ({len(data)} bytes)"
            return _ok(action, str(shot), body, t0,
                       assertions=[interface_runner._make_assertion("App 截图", True, "已保存", str(shot))])

        if action == "launch":
            if not package:
                return _fail(action, label, "启动 App 需要填写包名", t0)
            if activity:
                code, out = _run_adb(config, ["shell", "am", "start", "-n", f"{package}/{activity}"])
            else:
                code, out = _run_adb(config, ["shell", "monkey", "-p", package,
                                              "-c", "android.intent.category.LAUNCHER", "1"])
            if code != 0 or "Error" in str(out):
                return _fail(action, f"{package}/{activity}", str(out)[:300], t0)
            return _ok(action, f"{package}/{activity}", str(out), t0,
                       assertions=[interface_runner._make_assertion("启动 App", True, package, "已启动")])

        if action == "stop":
            if not package:
                return _fail(action, label, "停止 App 需要填写包名", t0)
            code, out = _run_adb(config, ["shell", "am", "force-stop", package])
            return _ok(action, package, str(out), t0, passed=code == 0,
                       message="" if code == 0 else str(out)[:300])

        if action == "tap":
            x_raw = str(step.get("x") or "").strip()
            y_raw = str(step.get("y") or "").strip()
            if x_raw and y_raw:
                x, y = int(float(x_raw)), int(float(y_raw))
            else:
                if not target:
                    return _fail(action, label, "点选需要填写文本/描述或坐标 x,y", t0)
                center = _find_node_center(_dump_ui(config), target)
                if center is None:
                    return _fail(action, target, f"界面上找不到「{target}」", t0)
                x, y = center
            code, out = _run_adb(config, ["shell", "input", "tap", str(x), str(y)])
            return _ok(action, target or f"{x},{y}", str(out), t0, passed=code == 0,
                       message="" if code == 0 else str(out)[:300])

        if action == "input":
            if not value:
                return _fail(action, label, "输入动作需要填写文本", t0)
            escaped = value.replace(" ", "%s")
            code, out = _run_adb(config, ["shell", "input", "text", escaped])
            return _ok(action, target or "当前焦点", str(out), t0, passed=code == 0,
                       message="" if code == 0 else str(out)[:300])

        if action == "swipe":
            coords = [_int(step.get(key), default) for key, default in (
                ("x", 0), ("y", 0), ("x2", 0), ("y2", 0),
            )]
            duration = _int(step.get("durationMs"), 300)
            code, out = _run_adb(config, ["shell", "input", "swipe",
                                          *(str(v) for v in coords), str(duration)])
            return _ok(action, "→".join(str(v) for v in coords), str(out), t0,
                       passed=code == 0, message="" if code == 0 else str(out)[:300])

        if action in ("back", "home"):
            keycode = "4" if action == "back" else "3"
            code, out = _run_adb(config, ["shell", "input", "keyevent", keycode])
            return _ok(action, keycode, str(out), t0, passed=code == 0,
                       message="" if code == 0 else str(out)[:300])

        # assertText：界面文本包含 target / value 即通过
        expected = target or value
        if not expected:
            return _fail(action, label, "文本断言需要填写期望文本", t0)
        xml = _dump_ui(config)
        visible_text = " ".join(
            (node.get("text") or "") + " " + (node.get("content-desc") or "")
            for node in ET.fromstring(xml).iter("node")
        ) if _looks_like_xml(xml) else xml
        passed = expected in visible_text
        assertion = interface_runner._make_assertion(
            "界面文本", passed, expected, "已出现" if passed else "未出现",
        )
        return _ok(action, expected, visible_text[:2000], t0, assertions=[assertion],
                   passed=passed, message="" if passed else f"界面未出现「{expected}」")
    except (RuntimeError, ValueError, ET.ParseError) as exc:
        return _fail(action, label, str(exc), t0)


def _int(raw: Any, default: int) -> int:
    try:
        return int(float(str(raw)))
    except (TypeError, ValueError):
        return default


def _looks_like_xml(text: str) -> bool:
    return text.lstrip().startswith("<")


def save_screenshot_base64(data: str) -> str:
    """调试辅助：把 base64 截图落盘，返回文件路径（当前仅桩环境使用）。"""
    raw = base64.b64decode(data)
    path = Path.cwd() / "data" / SHOT_DIR_NAME / f"app-{int(time.time() * 1000)}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return str(path)


__all__ = ["ACTIONS", "driver_config", "probe", "run_step"]
