"""检查更新链路门禁：版本探测、断点续传、暂存、启动应用与排除项。

    python backend/scripts/check_update.py

全部走本地桩服务器，不访问 Gitee、不触碰正式服务与正式库：
  1. 同版本 → hasUpdate=False；远端更高 → hasUpdate=True；
  2. 预置半截 .part 后下载，断言桩服务器收到 Range 请求、文件完整、sha256 落盘；
  3. 暂存排除 data/ 与 .env，保留源码文件；
  4. update_bootstrap 应用更新：覆盖前备份、pending 转 applied、排除项不落盘；
  5. 网络失败返回 error 而不是抛异常。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

RESULTS: list[tuple[bool, str]] = []


def check(ok: bool, label: str, detail: str = "") -> None:
    RESULTS.append((bool(ok), label))
    print(f"[{'PASS' if ok else 'FAIL'}] {label}" + (f" — {detail}" if detail and not ok else ""))


def build_zip(version: str) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        prefix = "AITP-master/"
        zf.writestr(prefix + "backend/aitp/__init__.py", f'__version__ = "{version}"\n')
        zf.writestr(prefix + "backend/aitp/foo_update.py", "VALUE = 1\n")
        zf.writestr(prefix + "data/evil.txt", "should never land\n")
        zf.writestr(prefix + ".env", "SECRET=1\n")
        zf.writestr(prefix + "README.md", "# updated\n")
    return buffer.getvalue()


class StubHandler(BaseHTTPRequestHandler):
    archive = b""
    version_text = b""
    range_requests: list[str] = []
    api_broken = False

    def log_message(self, *args: object) -> None:  # 静音
        return

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/v5/repos/"):
            if StubHandler.api_broken:
                self.send_response(500)
                self.end_headers()
                return
            body = json.dumps({"content": base64.b64encode(self.version_text).decode()}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/raw/master/"):
            body = self.version_text
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/repository/archive/"):
            body = self.archive
            rng = self.headers.get("Range")
            if rng:
                StubHandler.range_requests.append(rng)
                start = int(rng.split("=")[1].split("-")[0])
                chunk = body[start:]
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {start}-{len(body) - 1}/{len(body)}")
                self.send_header("Content-Length", str(len(chunk)))
                self.end_headers()
                self.wfile.write(chunk)
                return
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        self.send_response(404)
        self.end_headers()


def main() -> int:
    from aitp.services import update_service
    import update_bootstrap

    tmp = Path(tempfile.mkdtemp(prefix="aitp_update_gate_"))
    site = tmp / "site"
    (site / "raw" / "master" / "backend" / "aitp").mkdir(parents=True)
    StubHandler.version_text = b'__version__ = "9.9.9"\n'
    StubHandler.archive = build_zip("9.9.9")
    (site / "raw" / "master" / "backend" / "aitp" / "__init__.py").write_text(
        StubHandler.version_text.decode("utf-8"), encoding="utf-8")

    server = ThreadingHTTPServer(("127.0.0.1", 0), StubHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    os.environ["AITP_UPDATE_BASE_URL"] = f"http://127.0.0.1:{server.server_address[1]}"

    data_dir = tmp / "repo" / "data"
    data_dir.mkdir(parents=True)
    repo = tmp / "repo"
    (repo / "backend" / "aitp").mkdir(parents=True)
    (repo / "backend" / "aitp" / "__init__.py").write_text('__version__ = "2.8.0"\n', encoding="utf-8")
    (repo / "data" / "evil.txt").write_text("keep me\n", encoding="utf-8")

    try:
        local = update_service.local_version()
        result = update_service.check_update()
        check(result.get("latest") == "9.9.9" and result.get("hasUpdate") is True
              and "/api/v5/" in str(result.get("url")),
              "远端更高版本 → hasUpdate=True（走 Gitee API）", json.dumps(result, ensure_ascii=False))

        StubHandler.version_text = f'__version__ = "{local}"\n'.encode()
        same = update_service.check_update()
        check(same.get("hasUpdate") is False and same.get("latest") == local,
              "同版本 → hasUpdate=False", json.dumps(same, ensure_ascii=False))
        StubHandler.version_text = b'__version__ = "9.9.9"\n'

        StubHandler.api_broken = True
        fallback = update_service.check_update()
        check(fallback.get("hasUpdate") is True and "/raw/" in str(fallback.get("url")),
              "API 不可用时回退到 /raw 探测", json.dumps(fallback, ensure_ascii=False))
        StubHandler.api_broken = False

        pkg_dir = update_service.package_dir(data_dir, "9.9.9")
        pkg_dir.mkdir(parents=True, exist_ok=True)
        (pkg_dir / "pkg.zip.part").write_bytes(StubHandler.archive[:120])
        StubHandler.range_requests.clear()

        update_service.start_download(data_dir, "9.9.9")
        deadline = time.time() + 20
        phase = ""
        while time.time() < deadline:
            phase = update_service.status().get("phase", "")
            if phase in {"ready", "error"}:
                break
            time.sleep(0.1)
        final = pkg_dir / "pkg.zip"
        check(phase == "ready", "后台下载走到 ready", json.dumps(update_service.status(), ensure_ascii=False))
        check(any(Range.startswith("bytes=120-") for Range in StubHandler.range_requests),
              "续传请求带 Range: bytes=120-", str(StubHandler.range_requests))
        check(final.exists() and final.stat().st_size == len(StubHandler.archive),
              "断点续传后文件完整", f"{final.stat().st_size if final.exists() else 0} vs {len(StubHandler.archive)}")
        meta = json.loads((pkg_dir / "pkg.json").read_text(encoding="utf-8"))
        check(meta.get("sha256") == hashlib.sha256(final.read_bytes()).hexdigest(),
              "pkg.json 记录 sha256 与文件一致")

        staging = pkg_dir / "staging"
        staged = staging / "backend" / "aitp" / "foo_update.py"
        check(staged.exists(), "暂存包含新源码文件")
        check(not (staging / "data" / "evil.txt").exists(), "暂存排除 data/")
        check(not (staging / ".env").exists(), "暂存排除 .env")

        pending = update_service.pending_path(data_dir)
        info = update_service.pending_info(data_dir) or {}
        check(pending.exists() and info.get("state") == "pending" and info.get("version") == "9.9.9",
              "下载完成后写入 pending-update.json", json.dumps(info, ensure_ascii=False))

        update_bootstrap.apply_pending_update(repo)
        updated_init = (repo / "backend" / "aitp" / "__init__.py").read_text(encoding="utf-8")
        check('__version__ = "9.9.9"' in updated_init, "启动应用后源码被替换")
        check((repo / "backend" / "aitp" / "foo_update.py").exists(), "新文件已落盘")
        check(not (repo / "data" / "evil.txt").read_text(encoding="utf-8").startswith("should"),
              "排除项没有覆盖用户数据")
        backups = list((pkg_dir).glob("backup-*"))
        backup_hit = backups and (backups[0] / "backend" / "aitp" / "__init__.py").exists()
        check(bool(backup_hit), "覆盖前已备份旧文件")
        check(not pending.exists() and (data_dir / "updates" / "applied-9.9.9.json").exists(),
              "pending 转为 applied 留档")

        os.environ["AITP_UPDATE_BASE_URL"] = "http://127.0.0.1:9/base"
        broken = update_service.check_update()
        check(bool(broken.get("error")) and broken.get("hasUpdate") is False,
              "网络失败返回 error 不抛异常", json.dumps(broken, ensure_ascii=False))
    finally:
        server.shutdown()
        os.environ.pop("AITP_UPDATE_BASE_URL", None)
        shutil.rmtree(tmp, ignore_errors=True)

    fails = [label for ok, label in RESULTS if not ok]
    print(f"\nPASS={len(RESULTS) - len(fails)} FAIL={len(fails)}")
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
