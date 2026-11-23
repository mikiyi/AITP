"""执行媒体门禁（四期专项 C）。

    python backend/scripts/check_exec_media.py

覆盖五段：
  1. 落盘：合法视频/Trace 写入并按批次归档，元数据带相对路径；
  2. 限流：视频 > 50MB / Trace > 20MB 拒绝并给出降级说明；
  3. 后缀白名单：非媒体文件拒绝；
  4. 取回安全：目录穿越、绝对路径、不存在文件一律拒绝；
  5. 留存：登记「执行媒体」类型，到期批次可被清理。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import media_service, retention_service  # noqa: E402

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")


def new_db(path: pathlib.Path) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def save_checks(conn: sqlite3.Connection) -> None:
    video = media_service.save_media(
        conn, batch="EXP-001", kind=media_service.KIND_VIDEO,
        file_name="step1.webm", data=b"\x1aE\xdf\xa3fake-webm",
    )
    check("视频落盘成功", video is not None, str(video))
    check("媒体元数据带相对路径",
          video is not None and video["relativePath"] == "EXP-001/step1.webm",
          str(video))
    trace = media_service.save_media(
        conn, batch="EXP-001", kind=media_service.KIND_TRACE,
        file_name="step1.zip", data=b"PK\x03\x04fake-zip",
    )
    check("Trace 落盘成功", trace is not None, str(trace))

    blocked = media_service.save_media(
        conn, batch="EXP-001", kind=media_service.KIND_VIDEO,
        file_name="step1.exe", data=b"MZ",
    )
    check("非媒体后缀被拒", blocked is None, str(blocked))
    empty = media_service.save_media(
        conn, batch="EXP-001", kind=media_service.KIND_VIDEO,
        file_name="step1.webm", data=b"",
    )
    check("空文件被拒", empty is None, str(empty))
    unknown = media_service.save_media(
        conn, batch="EXP-001", kind="screenshot", file_name="a.png", data=b"x",
    )
    check("未知媒体类型被拒", unknown is None, str(unknown))


def limit_checks(conn: sqlite3.Connection) -> None:
    over_video = media_service.limit_of(media_service.KIND_VIDEO) + 1
    note = media_service.degrade_note(media_service.KIND_VIDEO, over_video)
    check("视频超限给出可读降级说明", "仅截图" in note and "视频" in note, note)
    check("视频上限为 50MB",
          media_service.limit_of(media_service.KIND_VIDEO) == 50 * 1024 * 1024)
    check("Trace 上限为 20MB",
          media_service.limit_of(media_service.KIND_TRACE) == 20 * 1024 * 1024)

    # 构造一个刚好超限的字节串（不真的写 50MB 到磁盘，直接断言比较逻辑）
    huge = b"x" * (media_service.limit_of(media_service.KIND_TRACE) + 1)
    result = media_service.save_media(
        conn, batch="EXP-002", kind=media_service.KIND_TRACE,
        file_name="big.zip", data=huge,
    )
    check("超限文件不落盘", result is None, "超限仍写入")
    del huge


def fetch_checks(conn: sqlite3.Connection) -> None:
    found = media_service.media_file(conn, "EXP-001/step1.webm")
    check("正常相对路径可取回", found is not None and found.is_file(), str(found))
    check("目录穿越被拒", media_service.media_file(conn, "../../aitp.db") is None)
    check("绝对路径被拒", media_service.media_file(conn, "/etc/passwd") is None)
    check("不存在的文件被拒", media_service.media_file(conn, "EXP-001/nope.webm") is None)
    check("空路径被拒", media_service.media_file(conn, "") is None)


def retention_checks(conn: sqlite3.Connection) -> None:
    check("留存登记了「执行媒体」类型",
          retention_service.TYPE_EXEC_MEDIA in retention_service.DATA_TYPES,
          str(retention_service.DATA_TYPES))
    check("执行媒体有中文标签",
          retention_service.TYPE_LABELS.get(retention_service.TYPE_EXEC_MEDIA) == "执行媒体")
    check("执行媒体只允许直接清理（无摘要可留）",
          retention_service.TYPE_ACTIONS[retention_service.TYPE_EXEC_MEDIA] ==
          (retention_service.ACTION_DELETE,),
          str(retention_service.TYPE_ACTIONS[retention_service.TYPE_EXEC_MEDIA]))

    # 造一个 40 天前的批次，按 30 天保留应被判到期
    stale = media_service._root(conn) / "EXP-OLD"
    stale.mkdir(parents=True, exist_ok=True)
    (stale / "step1.webm").write_bytes(b"old")
    old = time.time() - 40 * 86400
    os.utime(stale, (old, old))
    due = media_service.list_expired(conn, 30)
    check("到期批次能被识别", "EXP-OLD" in due, str(due))
    removed = media_service.purge_expired(conn, 30)
    check("到期批次被清理", removed >= 1 and not stale.exists(), str(removed))
    check("未到期批次保留",
          media_service.media_file(conn, "EXP-001/step1.webm") is not None)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-media-"))
    conn = new_db(workdir / "aitp.db")
    try:
        save_checks(conn)
        limit_checks(conn)
        fetch_checks(conn)
        retention_checks(conn)
    finally:
        conn.close()
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nPASS={_passed} FAIL={_failed}")
    for item in _failures:
        print(f"  [FAIL] {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
