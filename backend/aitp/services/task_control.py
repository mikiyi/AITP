"""进程内任务控制：把「取消」送到真正在跑的调用里。

数据库里的 status 是结果不是通道。生成器和子进程跑在别的线程上，
只能靠共享的 Event 叫停，否则取消只改库、模型那边还在继续吐字和计费。
"""

from __future__ import annotations

import threading

_lock = threading.Lock()
_events: dict[str, threading.Event] = {}


def register(key: str) -> threading.Event:
    event = threading.Event()
    with _lock:
        _events[key] = event
    return event


def release(key: str) -> None:
    with _lock:
        _events.pop(key, None)


def cancel(key: str) -> bool:
    """唤醒对应任务；返回是否找到正在跑的任务。"""
    with _lock:
        event = _events.get(key)
    if event is None:
        return False
    event.set()
    return True


def is_cancelled(key: str) -> bool:
    with _lock:
        event = _events.get(key)
    return event is not None and event.is_set()
