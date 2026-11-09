"""流式产出的独立承载（GAP-26）：产出跑在自己的线程池上，HTTP 侧改成短轮询。

为什么要换承载方式：
  原来 `POST /lab/<capability>/stream` 直接返回 SSE 响应体，waitress 的工作线程会被
  这条响应一路占到模型出完字。本机只有 8 个 worker（backend/run.py:37），10 路并发流式
  就能把 worker 全占住，之后任何普通接口都得排队——用户看到的不是「流式慢」，
  而是整站转圈直到超时。

换完之后：
  - 模型调用与逐帧产出在 `aitp-stream-*` 线程上跑（不占 waitress 的 worker）；
  - HTTP 侧只剩两个短请求：发起（立刻拿到 taskId）与取帧（取完当下已有的帧就返回）；
  - 帧的内容与顺序和 SSE 时代一致（同一个 SeqCounter、同一批帧类型），帧的**派发**
    逻辑确实一行未改；但**读帧**这件事改了：前端必须认 `mode:"poll"` 信封并按
    after 增量去取。这句话当初写的是「前端一行都不用改」，于是 `api/stream.ts`
    真的没跟着改，实验室流式执行整条界面哑掉且没有任何门禁报红——
    现在由 `check_stream_polling.py` 把两端契约一起钉住。

取舍说明：保留 SSE 帧格式、把传输改成轮询，而不是「SSE + 独立线程池」。
因为 SSE 只要还以长连接形态存在，响应体就得有人占着 worker 往外写；把计算挪到别的
线程也只是让 worker 空等，worker 并没有被腾出来。只有让请求短起来才是真的解决。

缓冲区是进程内的：服务重启后未取走的帧会丢，因此取帧接口在缓冲区命中不到时
回落到 `biz_ai_task` 的终态（`expired=true` + 终态帧），前端照样能拿到最终产出。
"""

from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable

#: 流式产出线程数：与 waitress 的 worker 数脱钩。AI 调用是 I/O 等待型，
#: 多线程不等于多 CPU；上限存在的意义是别把本机模型服务压垮。
STREAM_WORKERS = 8

#: 同时在途的流式任务上限。超了直接拒绝并给明确提示，
#: 而不是默默排到不知什么时候。
MAX_ACTIVE_STREAMS = 32

#: 单个任务最多留多少帧：超出后丢最早的 delta 帧（done 帧始终带完整产出，
#: 前端拿到 done 会用整段文本覆盖，所以丢旧 delta 只影响打字机手感，不影响正确性）
MAX_FRAMES_PER_TASK = 5000

#: 已结束任务的缓冲保留时间（秒）：够前端把最后几帧取完，又不会把内存长期占住
BUFFER_TTL_S = 180.0

#: 建议前端轮询间隔（毫秒）：本地既跟得上打字机速度，也不会把接口压满
POLL_INTERVAL_MS = 250


class StreamSink:
    """一个流式任务的写入端：生产者只管 emit，不关心帧怎么送出去。"""

    __slots__ = ("task_id", "_closed")

    def __init__(self, task_id: int) -> None:
        self.task_id = task_id
        self._closed = False

    def emit(self, frame: dict[str, Any]) -> None:
        """追加一帧；缓冲区已经不在了（过期/服务收尾）就当这帧没人要，静默丢弃。"""
        if self._closed:
            return
        with _LOCK:
            buffer = _BUFFERS.get(self.task_id)
            if buffer is None:
                return
            buffer.put(frame)

    def finish(self, status: str) -> None:
        """标记任务终结并记下终态，让取帧接口能马上告诉前端「不用再轮了」。"""
        self._closed = True
        with _LOCK:
            buffer = _BUFFERS.get(self.task_id)
            if buffer is None:
                return
            buffer.status = status
            buffer.finished_at = time.monotonic()


class _Buffer:
    """一个流式任务的帧缓冲。"""

    __slots__ = ("task_id", "frames", "status", "created_at", "finished_at", "truncated", "trace_id")

    def __init__(self, task_id: int, trace_id: str) -> None:
        self.task_id = task_id
        self.frames: list[dict[str, Any]] = []
        self.status = "running"
        self.created_at = time.monotonic()
        self.finished_at: float | None = None
        self.truncated = False
        self.trace_id = trace_id

    def put(self, frame: dict[str, Any]) -> None:
        if len(self.frames) >= MAX_FRAMES_PER_TASK:
            self._drop_oldest_delta()
            self.truncated = True
        self.frames.append(frame)

    def _drop_oldest_delta(self) -> None:
        """丢掉最早的一段 delta 帧；其它帧（start/usage/degrade/error/done）一律保留。"""
        for index, frame in enumerate(self.frames):
            if str(frame.get("type") or "") == "delta":
                del self.frames[index]
                return

    def take(self, after_seq: int) -> list[dict[str, Any]]:
        return [frame for frame in self.frames if int(frame.get("seq") or 0) > after_seq]

    @property
    def last_seq(self) -> int:
        return int(self.frames[-1].get("seq") or 0) if self.frames else 0

    @property
    def expired(self) -> bool:
        return self.finished_at is not None and time.monotonic() - self.finished_at > BUFFER_TTL_S


_LOCK = threading.Lock()
_BUFFERS: dict[int, _Buffer] = {}
_POOL: ThreadPoolExecutor | None = None
_POOL_LOCK = threading.Lock()


def pool() -> ThreadPoolExecutor:
    """惰性创建的流式产出线程池；没有流式请求时一个线程都不起。"""
    global _POOL
    with _POOL_LOCK:
        if _POOL is None:
            _POOL = ThreadPoolExecutor(max_workers=STREAM_WORKERS, thread_name_prefix="aitp-stream")
        return _POOL


def open_stream(task_id: int, trace_id: str = "") -> StreamSink:
    """登记一个任务的缓冲区并返回写入端。"""
    with _LOCK:
        _evict_locked()
        _BUFFERS[task_id] = _Buffer(task_id, trace_id)
    return StreamSink(task_id)


def submit(task_id: int, target: Callable[[], None]) -> None:
    """把产出动作交给独立线程池；线程内的异常由产出方自己转成 error 帧。"""
    pool().submit(target)


def active_streams() -> int:
    """在途（未终结）的流式任务数；发起接口用它判断是否超过上限。"""
    with _LOCK:
        _evict_locked()
        return sum(1 for buffer in _BUFFERS.values() if buffer.finished_at is None)


def read_frames(task_id: int, after_seq: int) -> dict[str, Any] | None:
    """取 after_seq 之后的帧。

    返回 None 表示缓冲区没有这个任务（服务重启或已过期），调用方要回落到库里的终态。
    """
    with _LOCK:
        buffer = _BUFFERS.get(task_id)
        if buffer is None or buffer.expired:
            return None
        payload: dict[str, Any] = {
            "taskId": task_id,
            "frames": buffer.take(after_seq),
            "lastSeq": buffer.last_seq,
            "status": buffer.status,
            "finished": buffer.finished_at is not None,
            "truncated": buffer.truncated,
            "intervalMs": POLL_INTERVAL_MS,
            "expired": False,
        }
        return payload


def _evict_locked() -> None:
    """清掉超时的已结束缓冲；只在做别的缓冲操作时顺带做，不另起定时线程。"""
    for task_id in [task_id for task_id, buffer in _BUFFERS.items() if buffer.expired]:
        _BUFFERS.pop(task_id, None)


def buffered_task_ids() -> list[int]:
    """当前有缓冲的任务号（门禁断言用，也方便排查「谁还挂着」）。"""
    with _LOCK:
        return sorted(_BUFFERS)


def shutdown(wait: bool = False) -> None:
    """关闭线程池并清空缓冲：门禁脚本收尾用，避免留下还在跑模型调用的线程。"""
    global _POOL
    with _POOL_LOCK:
        current = _POOL
        _POOL = None
    if current is not None:
        current.shutdown(wait=wait, cancel_futures=True)
    with _LOCK:
        _BUFFERS.clear()


def stream_stats() -> dict[str, Any]:
    """在途/已结束的流式任务计数，给健康检查与门禁断言用。"""
    with _LOCK:
        _evict_locked()
        running = sum(1 for buffer in _BUFFERS.values() if buffer.finished_at is None)
        finished = sum(1 for buffer in _BUFFERS.values() if buffer.finished_at is not None)
        frames = sum(len(buffer.frames) for buffer in _BUFFERS.values())
    with _POOL_LOCK:
        created = _POOL is not None
    return {
        "running": running,
        "finishedBuffered": finished,
        "bufferedFrames": frames,
        "workers": STREAM_WORKERS,
        "maxActive": MAX_ACTIVE_STREAMS,
        "intervalMs": POLL_INTERVAL_MS,
        "poolCreated": created,
    }
