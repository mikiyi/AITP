"""服务器资源指标采集：本机指标用标准库直采，远程服务器读取可选监控地址。

本机（不依赖第三方包）：
  - Windows：ctypes 调 GetSystemTimes / GlobalMemoryStatusEx / GetLogicalDrives
  - Linux：/proc/stat、/proc/meminfo
  - 磁盘：shutil.disk_usage
远程（需在环境配置里填写监控地址）：
  - node_exporter 的 Prometheus /metrics 文本
  - AITP 约定的 JSON：cpuPercent、memory{percent|usedBytes,totalBytes}、
    disks[{name|mount,percent|usedBytes,totalBytes}]

所有采集都在本机发起，指标只回给当前请求，不落库、不参与大模型调用。
"""

from __future__ import annotations

import ctypes
import json
import os
import re
import shutil
import sys
import threading
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlsplit

from ..common.time_utils import utcnow_iso

#: 远程指标接口超时；探测不该被监控地址拖住
METRICS_TIMEOUT = 2.5
#: 本机快照复用窗口：同一轮探测里应用/数据库往往是同一台机器，
#: 复用同一份快照可避免两边显示不同数值（单次探测最长约 5 秒）
LOCAL_SNAPSHOT_TTL = 6.0
#: 最多展示的磁盘数量，避免多盘机器把首页撑爆
MAX_DISKS = 4
#: Prometheus 文本最大读取量，防止误配大文件
MAX_BODY_BYTES = 512 * 1024

_LOCAL_HOSTS = {"", "localhost", "127.0.0.1", "::1", "0.0.0.0", "[::1]"}
_DISK_EXCLUDE_FS = {"tmpfs", "devtmpfs", "squashfs", "proc", "sysfs", "cgroup", "cgroup2"}

_cpu_lock = threading.Lock()
_cpu_samples: dict[str, tuple[float, float]] = {}
_local_lock = threading.Lock()
_local_cache: tuple[float, dict[str, Any]] | None = None


class _FileTime(ctypes.Structure):
    _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]


class _MemoryStatusEx(ctypes.Structure):
    _fields_ = [
        ("dwLength", ctypes.c_uint32),
        ("dwMemoryLoad", ctypes.c_uint32),
        ("ullTotalPhys", ctypes.c_uint64),
        ("ullAvailPhys", ctypes.c_uint64),
        ("ullTotalPageFile", ctypes.c_uint64),
        ("ullAvailPageFile", ctypes.c_uint64),
        ("ullTotalVirtual", ctypes.c_uint64),
        ("ullAvailVirtual", ctypes.c_uint64),
        ("ullAvailExtendedVirtual", ctypes.c_uint64),
    ]


def unavailable_metrics(message: str) -> dict[str, Any]:
    """统一的「没有指标」结构，前端据此显示未接入提示。"""
    return {
        "available": False,
        "source": "",
        "cpuPercent": None,
        "memory": {"percent": None, "usedBytes": None, "totalBytes": None},
        "disks": [],
        "message": message,
    }


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _int_number(value: Any) -> int | None:
    number = _number(value)
    if number is None or number < 0:
        return None
    return int(number)


def _clamp_percent(value: Any) -> float | None:
    number = _number(value)
    if number is None:
        return None
    return round(max(0.0, min(100.0, number)), 1)


def _percent_of(used: int | None, total: int | None) -> float | None:
    if used is None or total is None or total <= 0:
        return None
    return round(max(0.0, min(100.0, used / total * 100)), 1)


def _disk_entry(name: str, total: int, free: int) -> dict[str, Any]:
    used = max(0, total - free)
    return {
        "name": name,
        "percent": _percent_of(used, total),
        "usedBytes": used,
        "totalBytes": total,
    }


# ---------------- Windows 本机 ----------------

def _read_windows_cpu() -> tuple[float, float] | None:
    windll: Any = getattr(ctypes, "windll", None)
    if windll is None:
        return None
    idle = _FileTime()
    kernel = _FileTime()
    user = _FileTime()
    ok = windll.kernel32.GetSystemTimes(
        ctypes.byref(idle), ctypes.byref(kernel), ctypes.byref(user)
    )
    if not ok:
        return None
    idle_ticks = (idle.high << 32) | idle.low
    kernel_ticks = (kernel.high << 32) | kernel.low
    user_ticks = (user.high << 32) | user.low
    # kernel 时间包含 idle，忙时占比要用 (total - idle) / total
    return float(idle_ticks), float(kernel_ticks + user_ticks)


def _windows_cpu_percent() -> float | None:
    sample = _read_windows_cpu()
    if sample is None:
        return None
    with _cpu_lock:
        previous = _cpu_samples.get("windows")
        if previous is None:
            time.sleep(0.15)
            fresh = _read_windows_cpu() or sample
            _cpu_samples["windows"] = fresh
            previous, sample = sample, fresh
        else:
            _cpu_samples["windows"] = sample
    idle_delta = sample[0] - previous[0]
    total_delta = sample[1] - previous[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def _windows_memory() -> tuple[int | None, int | None]:
    windll: Any = getattr(ctypes, "windll", None)
    if windll is None:
        return None, None
    status = _MemoryStatusEx()
    status.dwLength = ctypes.sizeof(_MemoryStatusEx)
    if not windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
        return None, None
    return int(status.ullTotalPhys), int(status.ullAvailPhys)


def _windows_disks() -> list[dict[str, Any]]:
    windll: Any = getattr(ctypes, "windll", None)
    if windll is None:
        return []
    mask = int(windll.kernel32.GetLogicalDrives())
    disks: list[dict[str, Any]] = []
    for index, letter in enumerate("ABCDEFGHIJKLMNOPQRSTUVWXYZ"):
        if not mask & (1 << index):
            continue
        root = f"{letter}:\\"
        try:
            usage = shutil.disk_usage(root)
        except OSError:
            continue
        if usage.total <= 0:
            continue
        disks.append(_disk_entry(f"{letter}:", int(usage.total), int(usage.free)))
    disks.sort(key=lambda item: str(item.get("name") or ""))
    return disks[:MAX_DISKS]


# ---------------- Linux 本机 ----------------

def _linux_cpu_percent() -> float | None:
    try:
        with open("/proc/stat", encoding="utf-8") as handle:
            fields = handle.readline().split()
        values = [float(item) for item in fields[1:]]
    except (OSError, ValueError):
        return None
    if len(values) < 4:
        return None
    idle = values[3] + (values[4] if len(values) > 4 else 0.0)
    total = sum(values)
    with _cpu_lock:
        previous = _cpu_samples.get("linux")
        _cpu_samples["linux"] = (idle, total)
    if previous is None:
        return None
    idle_delta = idle - previous[0]
    total_delta = total - previous[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def _linux_memory() -> tuple[int | None, int | None]:
    values: dict[str, int] = {}
    try:
        with open("/proc/meminfo", encoding="utf-8") as handle:
            for line in handle:
                key, _, rest = line.partition(":")
                if rest:
                    values[key] = int(rest.strip().split()[0]) * 1024
    except (OSError, ValueError):
        return None, None
    total = values.get("MemTotal")
    available = values.get("MemAvailable")
    if available is None:
        available = values.get("MemFree", 0) + values.get("Buffers", 0) + values.get("Cached", 0)
    return total, available


def _fallback_disks() -> list[dict[str, Any]]:
    try:
        usage = shutil.disk_usage(os.path.abspath(os.sep))
    except OSError:
        return []
    if usage.total <= 0:
        return []
    return [_disk_entry(os.path.abspath(os.sep), int(usage.total), int(usage.free))]


# ---------------- 本机汇总 ----------------

def local_metrics() -> dict[str, Any]:
    """当前机器的 CPU / 内存 / 磁盘快照；6 秒内重复调用复用同一份。

    首页会并发探测多套环境，采样必须整体串行：否则第二个线程会在上一个线程
    刚采完的瞬间再采一次，CPU 时间差为 0，算出的占用率是空值。
    """
    global _local_cache
    with _local_lock:
        now = time.monotonic()
        if _local_cache is not None and now - _local_cache[0] < LOCAL_SNAPSHOT_TTL:
            return _local_cache[1]

        cpu: float | None = None
        total: int | None = None
        available: int | None = None
        disks: list[dict[str, Any]] = []
        if sys.platform == "win32":
            cpu = _windows_cpu_percent()
            total, available = _windows_memory()
            disks = _windows_disks()
        elif sys.platform.startswith("linux"):
            cpu = _linux_cpu_percent()
            total, available = _linux_memory()
            disks = _fallback_disks()
        else:
            disks = _fallback_disks()

        used = max(0, total - available) if total is not None and available is not None else None
        metrics: dict[str, Any] = {
            "available": True,
            "source": "local",
            "cpuPercent": cpu,
            "memory": {
                "percent": _percent_of(used, total),
                "usedBytes": used,
                "totalBytes": total,
            },
            "disks": disks,
            "message": "本机采集",
            "sampledAt": utcnow_iso(),
        }
        _local_cache = (now, metrics)
        return metrics


# ---------------- 远程监控地址 ----------------

_SAMPLE_RE = re.compile(
    r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>[^}]*)\})?\s+(?P<value>[-+]?[0-9.eE+]+)"
)


def _label_value(labels: str, key: str) -> str:
    match = re.search(rf'{key}="([^"]*)"', labels)
    return match.group(1) if match else ""


def _parse_prometheus(text: str) -> dict[str, list[tuple[str, float]]]:
    samples: dict[str, list[tuple[str, float]]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        match = _SAMPLE_RE.match(line)
        if not match:
            continue
        value = _number(match.group("value"))
        if value is None:
            continue
        samples.setdefault(match.group("name"), []).append((match.group("labels") or "", value))
    return samples


def _prometheus_cpu(url: str, samples: dict[str, list[tuple[str, float]]]) -> float | None:
    rows = samples.get("node_cpu_seconds_total") or []
    if not rows:
        return None
    total = sum(value for _, value in rows)
    idle = sum(value for labels, value in rows if _label_value(labels, "mode") == "idle")
    with _cpu_lock:
        previous = _cpu_samples.get(f"prom:{url}")
        _cpu_samples[f"prom:{url}"] = (idle, total)
    if previous is None:
        return None
    idle_delta = idle - previous[0]
    total_delta = total - previous[1]
    if total_delta <= 0:
        return None
    return round(max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100)), 1)


def _prometheus_memory(samples: dict[str, list[tuple[str, float]]]) -> tuple[int | None, int | None]:
    def first(name: str) -> int | None:
        rows = samples.get(name) or []
        return int(rows[0][1]) if rows else None

    total = first("node_memory_MemTotal_bytes")
    available = first("node_memory_MemAvailable_bytes")
    if available is None:
        free = first("node_memory_MemFree_bytes")
        buffers = first("node_memory_Buffers_bytes")
        cached = first("node_memory_Cached_bytes")
        if free is not None:
            available = free + (buffers or 0) + (cached or 0)
    return total, available


def _prometheus_disks(samples: dict[str, list[tuple[str, float]]]) -> list[dict[str, Any]]:
    sizes = samples.get("node_filesystem_size_bytes") or []
    available_rows = samples.get("node_filesystem_avail_bytes") or []
    free_by_mount: dict[str, float] = {}
    for labels, value in available_rows:
        free_by_mount[_label_value(labels, "mountpoint") or _label_value(labels, "device")] = value

    disks: list[dict[str, Any]] = []
    for labels, value in sizes:
        fstype = _label_value(labels, "fstype")
        if fstype in _DISK_EXCLUDE_FS:
            continue
        mount = _label_value(labels, "mountpoint") or _label_value(labels, "device") or "disk"
        free = free_by_mount.get(mount)
        if free is None or value <= 0:
            continue
        disks.append(_disk_entry(mount, int(value), int(free)))

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        name = str(item.get("name") or "")
        preferred = 1 if name in {"/", "C:", "C:\\"} else 0
        return preferred, int(item.get("totalBytes") or 0)

    disks.sort(key=rank, reverse=True)
    return disks[:MAX_DISKS]


def _normalize_json(payload: dict[str, Any]) -> dict[str, Any]:
    cpu_raw: Any = payload.get("cpuPercent")
    if cpu_raw is None and isinstance(payload.get("cpu"), dict):
        cpu_raw = payload["cpu"].get("percent")

    raw_memory = payload.get("memory")
    memory: dict[str, Any] = raw_memory if isinstance(raw_memory, dict) else {}
    total = _int_number(memory.get("totalBytes") or memory.get("total"))
    used = _int_number(memory.get("usedBytes") or memory.get("used"))
    percent = _clamp_percent(memory.get("percent") or memory.get("usedPercent"))
    if percent is None:
        percent = _percent_of(used, total)
    if used is None and percent is not None and total is not None:
        used = int(total * percent / 100)

    disks: list[dict[str, Any]] = []
    raw_disks = payload.get("disks")
    if isinstance(raw_disks, list):
        for item in raw_disks:
            if not isinstance(item, dict):
                continue
            disk_total = _int_number(item.get("totalBytes") or item.get("total"))
            disk_used = _int_number(item.get("usedBytes") or item.get("used"))
            disk_percent = _clamp_percent(item.get("percent") or item.get("usedPercent"))
            if disk_percent is None:
                disk_percent = _percent_of(disk_used, disk_total)
            if disk_used is None and disk_percent is not None and disk_total is not None:
                disk_used = int(disk_total * disk_percent / 100)
            name = str(item.get("name") or item.get("mount") or f"磁盘 {len(disks) + 1}")
            disks.append({
                "name": name,
                "percent": disk_percent,
                "usedBytes": disk_used,
                "totalBytes": disk_total,
            })
            if len(disks) >= MAX_DISKS:
                break

    return {
        "available": True,
        "source": "json",
        "cpuPercent": _clamp_percent(cpu_raw),
        "memory": {"percent": percent, "usedBytes": used, "totalBytes": total},
        "disks": disks,
        "message": "远程指标（JSON）",
        "sampledAt": utcnow_iso(),
    }


def _remote_metrics(url: str) -> dict[str, Any]:
    request = urllib.request.Request(
        url.strip(),
        headers={"Accept": "text/plain, application/json"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=METRICS_TIMEOUT) as response:
        raw = response.read(MAX_BODY_BYTES)
        charset = response.headers.get_content_charset() or "utf-8"
    text = raw.decode(charset, errors="replace")

    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        samples = _parse_prometheus(text)
        if not samples:
            raise ValueError("返回内容既不是 JSON 也不是 Prometheus 指标")
        total, available = _prometheus_memory(samples)
        used = max(0, total - available) if total is not None and available is not None else None
        return {
            "available": True,
            "source": "prometheus",
            "cpuPercent": _prometheus_cpu(url.strip(), samples),
            "memory": {
                "percent": _percent_of(used, total),
                "usedBytes": used,
                "totalBytes": total,
            },
            "disks": _prometheus_disks(samples),
            "message": "远程指标（Prometheus）",
            "sampledAt": utcnow_iso(),
        }

    if not isinstance(payload, dict):
        raise ValueError("JSON 指标必须是对象")
    return _normalize_json(payload)


def _fetch_remote(url: str) -> dict[str, Any]:
    try:
        return _remote_metrics(url)
    except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
        reason = getattr(exc, "reason", exc)
        return unavailable_metrics(f"监控地址不可用：{reason}")


def host_of_url(url: str) -> str:
    try:
        return urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""


def is_local_host(host: str) -> bool:
    value = host.strip().lower()
    if value in _LOCAL_HOSTS:
        return value != ""
    return value.startswith("127.")


def collect_metrics(*, host: str, metrics_url: str = "") -> dict[str, Any]:
    """按目标主机挑采集方式：配了监控地址优先远程，本机目标直接本机采集。"""
    if metrics_url.strip():
        remote = _fetch_remote(metrics_url.strip())
        if remote.get("available") or not is_local_host(host):
            return remote
        local = dict(local_metrics())
        local["message"] = "监控地址不可用，已改用本机指标"
        return local
    if is_local_host(host):
        return local_metrics()
    if not host.strip():
        return unavailable_metrics("未配置服务器地址")
    return unavailable_metrics("远程服务器未配置监控地址（支持 node_exporter /metrics 或 JSON 指标接口）")
