"""附件能力验收：文档导入 + 图片去水印。

覆盖：
- 文档抽取：Word / Excel / Markdown / PDF / HTML / JSON / TXT
- 图片去水印：PNG/JPG/GIF/BMP/TIFF/WEBP/AVIF/HEIC 走位图处理，SVG 按图元处理
- 解码不了的格式（RAW / AI / JPEG-XL）不拦死流程，退回原图并说明原因
- 接口：上传文档、上传/粘贴图片、取回预览与 data URL、删除附件

    python backend/scripts/check_attachments.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import base64
import io
import json
import os
import pathlib
import shutil
import socket
import subprocess
import sys
import tempfile
import time
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import attachment_service  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    _failures.append(f"{name}: {detail}")
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_attachments.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_attachments.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


# ---------------- 造样例文件 ----------------
def make_docx() -> bytes:
    from docx import Document

    document = Document()
    document.add_heading("登录需求", level=1)
    document.add_paragraph("手机号加验证码登录，验证码 5 分钟有效")
    table = document.add_table(rows=2, cols=2)
    table.cell(0, 0).text = "字段"
    table.cell(0, 1).text = "说明"
    table.cell(1, 0).text = "code"
    table.cell(1, 1).text = "验证码"
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def make_xlsx() -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    sheet.title = "用例"
    sheet.append(["用例标题", "预期结果"])
    sheet.append(["正常登录", "跳转首页"])
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def make_pdf() -> bytes:
    """手写一个最小可解析 PDF，文本内容为 Hello PDF。"""
    content = b"BT /F1 18 Tf 20 100 Td (Hello PDF) Tj ET"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 300 200]/Contents 4 0 R"
        b"/Resources<</Font<</F1 5 0 R>>>>>>",
        b"<</Length " + str(len(content)).encode() + b">>stream\n" + content + b"\nendstream",
        b"<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, payload in enumerate(objects, start=1):
        offsets.append(len(out))
        out += f"{index} 0 obj\n".encode() + payload + b"\nendobj\n"
    xref_at = len(out)
    out += f"xref\n0 {len(objects) + 1}\n".encode()
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += f"{offset:010d} 00000 n \n".encode()
    out += (
        f"trailer\n<</Size {len(objects) + 1}/Root 1 0 R>>\nstartxref\n{xref_at}\n%%EOF\n"
    ).encode()
    return bytes(out)


def make_watermarked_png() -> bytes:
    """左上角压一块亮色覆盖层，模拟常见的水印。"""
    from PIL import Image

    image = Image.new("RGB", (400, 300), (120, 120, 120))
    for x in range(140):
        for y in range(50):
            image.putpixel((x, y), (245, 245, 245))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def make_svg_with_watermark() -> bytes:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="200" height="100">'
        '<rect width="200" height="100" fill="#eee"/>'
        '<text id="watermark-text" x="10" y="50">水印</text>'
        '<text id="title" x="10" y="80">正文</text>'
        '</svg>'
    ).encode("utf-8")


# ---------------- 服务级检查 ----------------
def check_document_extraction() -> None:
    cases: list[tuple[str, bytes, str]] = [
        ("Word", make_docx(), "验证码"),
        ("Excel", make_xlsx(), "正常登录"),
        ("Markdown", "# 标题\n\n正文内容".encode("utf-8"), "正文内容"),
        ("TXT", "纯文本内容".encode("utf-8"), "纯文本内容"),
        ("JSON", json.dumps({"a": 1}, ensure_ascii=False).encode("utf-8"), '"a"'),
        ("HTML", "<html><body><p>页面文字</p></body></html>".encode("utf-8"), "页面文字"),
        ("PDF", make_pdf(), "Hello PDF"),
    ]
    for label, data, keyword in cases:
        ext = {
            "Word": ".docx", "Excel": ".xlsx", "Markdown": ".md", "TXT": ".txt",
            "JSON": ".json", "HTML": ".html", "PDF": ".pdf",
        }[label]
        try:
            result = attachment_service.extract_document(f"样例{ext}", data)
        except Exception as exc:  # noqa: BLE001
            check(f"文档 {label} 可抽取文本", False, f"{exc.__class__.__name__}: {exc}")
            continue
        check(f"文档 {label} 可抽取文本", keyword in result.text, result.text[:120])


def check_image_dewatermark() -> None:
    from PIL import Image

    data = make_watermarked_png()
    result = attachment_service.process_image("样例.png", data)
    check("位图识别出水印覆盖层", result.watermark_applied, result.note)
    cleaned = Image.open(io.BytesIO(result.data)).convert("RGB")
    pixel = cleaned.getpixel((10, 10))
    pixel_rgb = pixel if isinstance(pixel, tuple) else (pixel,)
    red = float(pixel_rgb[0]) if pixel_rgb[0] is not None else -1.0
    check("水印区域已被覆盖回背景色", abs(red - 120) < 12, f"像素={pixel}")

    plain = Image.new("RGB", (400, 300), (120, 120, 120))
    buffer = io.BytesIO()
    plain.save(buffer, format="PNG")
    plain_result = attachment_service.process_image("干净.png", buffer.getvalue())
    check("干净图片不会被误改", not plain_result.watermark_applied, plain_result.note)

    svg = attachment_service.process_image("样例.svg", make_svg_with_watermark())
    check("SVG 按图元去水印", svg.watermark_applied, svg.note)
    check("SVG 正文保留", b"title" in svg.data, svg.data[:120].decode("utf-8", "replace"))

    unsupported = attachment_service.process_image("样例.jxl", b"\x00\x01not-a-real-file")
    check("解码不了的格式不拦死流程", not unsupported.watermark_applied, unsupported.note)
    check("解码不了的格式给出说明", "解码器" in unsupported.note, unsupported.note)
    check("解码不了的格式保留原图", unsupported.data == b"\x00\x01not-a-real-file", "")


def check_formats_decodable() -> None:
    """逐个格式确认能解码（位图格式都要能走完去水印流程）。"""
    from PIL import Image

    formats = ["PNG", "JPEG", "GIF", "BMP", "TIFF", "WEBP", "AVIF"]
    for fmt in formats:
        buffer = io.BytesIO()
        Image.new("RGB", (200, 160), (90, 140, 200)).save(buffer, format=fmt)
        ext = {"PNG": ".png", "JPEG": ".jpg", "GIF": ".gif", "BMP": ".bmp",
               "TIFF": ".tiff", "WEBP": ".webp", "AVIF": ".avif"}[fmt]
        try:
            result = attachment_service.process_image(f"样例{ext}", buffer.getvalue())
            check(f"{fmt} 可解码并处理", result.width > 0 and result.height > 0, result.note)
        except Exception as exc:  # noqa: BLE001
            check(f"{fmt} 可解码并处理", False, f"{exc.__class__.__name__}: {exc}")


# ---------------- 接口级检查 ----------------
def check_api(api: Api, workdir: pathlib.Path) -> None:
    attachments_dir = workdir / "attachments"

    doc = api.data("POST", "/api/v1/lab/attachments/document", None, data={
        "file": (io.BytesIO("# 需求\n手机号登录".encode("utf-8")), "需求.md"),
        "content_type": "multipart/form-data",
    })
    check("文档导入返回正文", "手机号登录" in str(doc.get("text") or ""), str(doc)[:200])
    check("文档导入返回附件编号", bool(doc.get("attachmentId")), str(doc)[:120])

    png = api.data("POST", "/api/v1/lab/attachments/image", None, data={
        "file": (io.BytesIO(make_watermarked_png()), "截图.png"),
        "content_type": "multipart/form-data",
    })
    check("图片上传返回附件编号", bool(png.get("attachmentId")), str(png)[:200])
    check("图片上传标记已去水印", png.get("watermarkApplied") is True, str(png)[:200])
    image_id = str(png.get("attachmentId") or "")

    resp = api.client.get(f"/api/v1/lab/attachments/{image_id}")
    check("图片预览可取回", resp.status_code == 200 and len(resp.data) > 0, str(resp.status_code))

    payload = api.data("GET", f"/api/v1/lab/attachments/{image_id}/payload")
    check("图片可取 data URL", str(payload.get("dataUrl") or "").startswith("data:image/"),
          str(payload)[:120])

    pasted = api.data("POST", "/api/v1/lab/attachments/image", {
        "dataUrl": "data:image/png;base64," + base64.b64encode(make_watermarked_png()).decode()
    })
    check("粘贴图片可处理", bool(pasted.get("attachmentId")), str(pasted)[:200])

    bad = api.call("POST", "/api/v1/lab/attachments/document", None, data={
        "file": (io.BytesIO(b"x"), "样例.exe"),
        "content_type": "multipart/form-data",
    })
    check("不支持的文档格式被拒", bad[1].get("code") == ErrorCode.PARAM_ERROR, str(bad[1]))

    removed = api.data("DELETE", f"/api/v1/lab/attachments/{image_id}")
    check("附件可删除", removed.get("deleted") is True, str(removed))
    after = api.call("GET", f"/api/v1/lab/attachments/{image_id}")
    check("删除后取不到内容", after[1].get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(after[1]))

    # 附件目录确实落在数据目录里
    check("附件落在数据目录", attachments_dir.is_dir() and any(attachments_dir.iterdir()),
          str(attachments_dir))


def check_run_with_image(api: Api) -> None:
    """带图片执行一次能力，确认请求链路通。"""
    png = api.data("POST", "/api/v1/lab/attachments/image", None, data={
        "file": (io.BytesIO(make_watermarked_png()), "截图.png"),
        "content_type": "multipart/form-data",
    })
    image_id = str(png.get("attachmentId") or "")
    data = api.data("POST", "/api/v1/lab/log_analysis/run", {
        "variables": {"logContent": "NullPointerException at OrderService:88", "errorType": "NPE"},
        "attachmentIds": [image_id],
    })
    check("带图片执行能力可跑通", bool(str(data.get("output") or "").strip()), str(data)[:200])


def main() -> int:
    check_document_extraction()
    check_image_dewatermark()
    check_formats_decodable()

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_attach_"))
    port = free_port()
    mock = subprocess.Popen(
        [sys.executable, str(ROOT / "backend" / "scripts" / "mock_openai_server.py"),
         "--port", str(port), "--ollama-port", str(free_port())],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env={**os.environ, "PYTHONUTF8": "1"},
    )
    try:
        if not wait_port(port):
            print("[FAIL] mock 模型服务启动失败")
            return 1
        app = build_app(workdir)
        api = Api(app)
        api.data("POST", "/api/v1/settings/ai/profiles", {
            "name": "本地 mock",
            "provider": "custom",
            "protocol": "openai_compat",
            "baseUrl": f"http://127.0.0.1:{port}/v1",
            "model": "mock-model",
            "apiKey": "sk-test",
            "isDefault": True,
            "isActive": True,
            "priority": 100,
        })
        check_api(api, workdir)
        check_run_with_image(api)
    finally:
        mock.terminate()
        try:
            mock.wait(timeout=5)
        except subprocess.TimeoutExpired:
            mock.kill()
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n附件校验: PASS={_passed} FAIL={_failed}")
    for item in _failures:
        print(f"  [FAIL] {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
