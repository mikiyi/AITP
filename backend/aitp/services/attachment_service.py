"""附件处理：文档抽取文本 + 图片去水印。

AI 实验室里贴进来的东西都要先过这里：
- 文档（Word / Excel / Markdown / PDF / HTML / JSON / TXT）→ 抽成纯文本再进提示词
- 图片（JPG / PNG / GIF / SVG / WebP / BMP / TIFF / RAW / PSD / AI / HEIF / HEIC /
  PDF / APNG / AVIF / JPEG-XL）→ 先去水印，处理后的图再进后续流程

原则：
- 只在本机处理，文件不出机器；
- 解码不了的格式不抛异常拦死流程，退回原图并说明原因；
- 所有落盘都是「临时文件 + 原子替换」，不留半截文件。
"""

from __future__ import annotations

import base64
import binascii
import io
import json
import os
import re
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..common.secret_store import atomic_write_bytes

#: 可作为文档导入的扩展名
DOCUMENT_EXTENSIONS: dict[str, str] = {
    ".docx": "word",
    ".doc": "word",
    ".xlsx": "excel",
    ".xlsm": "excel",
    ".csv": "text",
    ".md": "markdown",
    ".markdown": "markdown",
    ".txt": "text",
    ".log": "text",
    ".json": "json",
    ".html": "html",
    ".htm": "html",
    ".pdf": "pdf",
}

#: 可作为图片贴入的扩展名（PDF 既能当文档也能当图片，走图片分支）
IMAGE_EXTENSIONS: dict[str, str] = {
    ".jpg": "jpeg",
    ".jpeg": "jpeg",
    ".png": "png",
    ".apng": "png",
    ".gif": "gif",
    ".svg": "svg",
    ".webp": "webp",
    ".bmp": "bmp",
    ".tif": "tiff",
    ".tiff": "tiff",
    ".raw": "raw",
    ".cr2": "raw",
    ".nef": "raw",
    ".arw": "raw",
    ".dng": "raw",
    ".psd": "psd",
    ".ai": "ai",
    ".heif": "heif",
    ".heic": "heic",
    ".avif": "avif",
    ".jxl": "jxl",
    ".pdf": "pdf",
}

#: 文档抽取出的文本上限，防止把整本大文件塞进提示词
MAX_DOCUMENT_CHARS = 20000

#: 图片边长上限，超过先等比缩小，避免超大图撑爆内存与请求体
MAX_IMAGE_SIDE = 2048

#: 单个附件的字节上限
MAX_ATTACHMENT_BYTES = 32 * 1024 * 1024

SVG_WATERMARK_HINTS = ("watermark", "water-mark", "wm", "水印", "logo")


@dataclass(slots=True)
class ExtractedDocument:
    """文档抽取结果。"""

    file_name: str
    kind: str
    text: str
    truncated: bool = False
    chars: int = 0
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "kind": self.kind,
            "text": self.text,
            "truncated": self.truncated,
            "chars": self.chars,
            "note": self.note,
        }


@dataclass(slots=True)
class ProcessedImage:
    """图片去水印结果。"""

    file_name: str
    media_type: str
    data: bytes
    width: int = 0
    height: int = 0
    watermark_applied: bool = False
    regions: list[dict[str, Any]] = field(default_factory=list)
    note: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "fileName": self.file_name,
            "mediaType": self.media_type,
            "width": self.width,
            "height": self.height,
            "size": len(self.data),
            "watermarkApplied": self.watermark_applied,
            "regions": self.regions,
            "note": self.note,
        }


class AttachmentError(Exception):
    """附件无法处理；message 直接展示给用户。"""


# ---------------------------------------------------------------------
# 分类
# ---------------------------------------------------------------------
def extension_of(file_name: str) -> str:
    return Path(file_name or "").suffix.lower()


def classify(file_name: str) -> str:
    """返回 document / image / 空串（不支持）。"""
    ext = extension_of(file_name)
    if ext in IMAGE_EXTENSIONS:
        return "image"
    if ext in DOCUMENT_EXTENSIONS:
        return "document"
    return ""


def is_image(file_name: str) -> bool:
    return extension_of(file_name) in IMAGE_EXTENSIONS


def is_document(file_name: str) -> bool:
    ext = extension_of(file_name)
    return ext in DOCUMENT_EXTENSIONS and ext not in IMAGE_EXTENSIONS


# ---------------------------------------------------------------------
# 文档抽取
# ---------------------------------------------------------------------
def _decode_text(data: bytes) -> str:
    """按常见编码逐个尝试，Windows 上 GBK 文本也能读出来。"""
    for encoding in ("utf-8-sig", "utf-8", "gb18030", "latin-1"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _extract_word(data: bytes) -> str:
    try:
        from docx import Document
    except ImportError as exc:  # pragma: no cover - 依赖缺失时的兜底
        raise AttachmentError("本机缺少 python-docx，无法解析 Word 文档") from exc
    document = Document(io.BytesIO(data))
    lines: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if text:
            lines.append(text)
    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip() for cell in row.cells]
            if any(cells):
                lines.append(" | ".join(cells))
    return "\n".join(lines)


def _extract_excel(data: bytes) -> str:
    try:
        from openpyxl import load_workbook
    except ImportError as exc:  # pragma: no cover
        raise AttachmentError("本机缺少 openpyxl，无法解析 Excel 文件") from exc
    workbook = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    lines: list[str] = []
    for sheet in workbook.worksheets:
        lines.append(f"# 工作表：{sheet.title}")
        for row in sheet.iter_rows(values_only=True):
            cells = ["" if cell is None else str(cell) for cell in row]
            if any(item.strip() for item in cells):
                lines.append(" | ".join(cells))
    workbook.close()
    return "\n".join(lines)


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise AttachmentError("本机缺少 pypdf，无法解析 PDF 文档") from exc
    reader = PdfReader(io.BytesIO(data))
    lines: list[str] = []
    for index, page in enumerate(reader.pages, start=1):
        text = (page.extract_text() or "").strip()
        if text:
            lines.append(f"# 第 {index} 页\n{text}")
    return "\n".join(lines)


_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)


def _extract_html(data: bytes) -> str:
    raw = _decode_text(data)
    raw = _SCRIPT_RE.sub(" ", raw)
    text = _TAG_RE.sub("\n", raw)
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def _extract_json(data: bytes) -> str:
    raw = _decode_text(data)
    try:
        return json.dumps(json.loads(raw), ensure_ascii=False, indent=2)
    except json.JSONDecodeError:
        return raw


def extract_document(file_name: str, data: bytes) -> ExtractedDocument:
    """把文档转成纯文本；超出上限时截断并标注。"""
    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("附件超过 32MB，请拆分后再导入")
    kind = DOCUMENT_EXTENSIONS.get(extension_of(file_name), "text")
    note = ""
    try:
        if kind == "word":
            if extension_of(file_name) == ".doc":
                note = "旧版 .doc 二进制格式无法直接解析，已按纯文本读取"
                text = _decode_text(data)
            else:
                text = _extract_word(data)
        elif kind == "excel":
            text = _extract_excel(data)
        elif kind == "pdf":
            text = _extract_pdf(data)
        elif kind == "html":
            text = _extract_html(data)
        elif kind == "json":
            text = _extract_json(data)
        else:
            text = _decode_text(data)
    except AttachmentError:
        raise
    except Exception as exc:  # noqa: BLE001 - 解析失败退回原文，不拦死主流程
        note = f"结构化解析失败（{exc.__class__.__name__}），已按纯文本读取"
        text = _decode_text(data)

    truncated = len(text) > MAX_DOCUMENT_CHARS
    if truncated:
        text = text[:MAX_DOCUMENT_CHARS]
    return ExtractedDocument(
        file_name=file_name,
        kind=kind,
        text=text,
        truncated=truncated,
        chars=len(text),
        note=note,
    )


# ---------------------------------------------------------------------
# 图片去水印
# ---------------------------------------------------------------------
def _register_heif() -> bool:
    """注册 HEIF/HEIC 解码器（pillow-heif 缺失时返回 False）。"""
    try:
        import pillow_heif
    except ImportError:
        return False
    try:
        pillow_heif.register_heif_opener()
    except Exception:  # noqa: BLE001 - 重复注册会抛错，忽略即可
        pass
    return True


def _strip_svg_watermark(data: bytes) -> tuple[bytes, int]:
    """SVG 是文本格式：直接删掉带水印标识的图元。"""
    text = data.decode("utf-8", errors="replace")
    removed = 0
    pattern = re.compile(
        r"<(text|image|rect|path|use)\b[^>]*?(?:id|class|aria-label)\s*=\s*[\"']([^\"']+)[\"'][^>]*>.*?</\1>",
        re.IGNORECASE | re.DOTALL,
    )

    def _replace(match: re.Match[str]) -> str:
        nonlocal removed
        marker = match.group(2).lower()
        if any(hint in marker for hint in SVG_WATERMARK_HINTS):
            removed += 1
            return ""
        return match.group(0)

    cleaned = pattern.sub(_replace, text)
    # 文本节点里直接写着「水印」字样的也一并清掉
    def _replace_text(match: re.Match[str]) -> str:
        nonlocal removed
        if any(hint in match.group(2).lower() for hint in SVG_WATERMARK_HINTS):
            removed += 1
            return ""
        return match.group(0)

    cleaned = re.sub(
        r"<(text|tspan)\b[^>]*>(.*?)</\1>",
        _replace_text,
        cleaned,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return cleaned.encode("utf-8"), removed


def _corner_blocks(width: int, height: int) -> list[tuple[int, int, int, int]]:
    """常见的水印落点：四个角 + 上下边条。"""
    cw = max(24, int(width * 0.34))
    ch = max(20, int(height * 0.16))
    sw = max(16, int(width * 0.14))
    sh = max(14, int(height * 0.08))
    return [
        (0, 0, cw, ch),
        (width - cw, 0, cw, ch),
        (0, height - ch, cw, ch),
        (width - cw, height - ch, cw, ch),
        (0, 0, width, sh),
        (0, height - sh, width, sh),
        (0, 0, sw, height),
        (width - sw, 0, sw, height),
    ]


def _strip_watermark_pixels(image: Any) -> tuple[Any, list[dict[str, Any]]]:
    """抹掉覆盖在图上的水印层。

    判据：水印一般是「一块亮度更高、颜色高度集中」的覆盖层。
    命中后用周边一圈像素的中位色回填，等价于把水印文字抹平。
    """
    import numpy as np
    from PIL import Image

    rgba = image.convert("RGBA")
    arr = np.asarray(rgba).astype(np.int16)
    height, width = arr.shape[0], arr.shape[1]
    if width < 48 or height < 48:
        return rgba, []

    rgb = arr[:, :, :3]
    alpha = arr[:, :, 3]
    luminance = rgb.mean(axis=2)
    overall = float(np.median(luminance[alpha > 16])) if (alpha > 16).any() else float(np.median(luminance))
    # 透明区域按白底参与计算，避免把透明像素当成暗色内容
    composite = rgb.astype(np.float32)
    composite[alpha < 16] = 255.0
    lum = composite.mean(axis=2)

    removed: list[dict[str, Any]] = []
    for (x0, y0, x1, y1) in _corner_blocks(width, height):
        x0, y0 = max(0, x0), max(0, y0)
        x1, y1 = min(width, x1), min(height, y1)
        if x1 - x0 < 12 or y1 - y0 < 12:
            continue
        block = composite[y0:y1, x0:x1]
        block_lum = lum[y0:y1, x0:x1]
        median_color = np.median(block.reshape(-1, 3), axis=0)
        # 块内颜色集中度：与中位色的平均偏差越小说明越像一整块覆盖层
        deviation = float(np.mean(np.abs(block - median_color)))
        if deviation > 26.0:
            continue
        if float(np.median(block_lum)) < overall + 6.0:
            # 不比整体更亮，多半是正常内容而不是水印
            continue
        # 用周边一圈像素的中位色回填（环形取样要排除块本身，否则中位色仍是水印色）
        pad = 6
        ring_x0 = max(0, x0 - pad)
        ring_y0 = max(0, y0 - pad)
        ring_x1 = min(width, x1 + pad)
        ring_y1 = min(height, y1 + pad)
        ring_parts: list[Any] = []
        if ring_y0 < y0:
            ring_parts.append(composite[ring_y0:y0, ring_x0:ring_x1].reshape(-1, 3))
        if y1 < ring_y1:
            ring_parts.append(composite[y1:ring_y1, ring_x0:ring_x1].reshape(-1, 3))
        if ring_x0 < x0:
            ring_parts.append(composite[y0:y1, ring_x0:x0].reshape(-1, 3))
        if x1 < ring_x1:
            ring_parts.append(composite[y0:y1, x1:ring_x1].reshape(-1, 3))
        if not ring_parts:
            continue
        fill = np.median(np.concatenate(ring_parts, axis=0), axis=0)
        composite[y0:y1, x0:x1] = fill
        removed.append({
            "x": int(x0),
            "y": int(y0),
            "width": int(x1 - x0),
            "height": int(y1 - y0),
        })

    if not removed:
        return rgba, []
    out = np.dstack([composite, alpha.astype(np.float32)]).astype(np.uint8)
    return Image.fromarray(out, mode="RGBA"), removed


def process_image(file_name: str, data: bytes) -> ProcessedImage:
    """对贴入的图片做去水印处理，返回处理后的图片内容。"""
    from PIL import Image

    if len(data) > MAX_ATTACHMENT_BYTES:
        raise AttachmentError("图片超过 32MB，请压缩后再上传")
    ext = extension_of(file_name)
    media_type = _media_type_of(ext)

    if ext == ".svg":
        cleaned_svg, svg_removed = _strip_svg_watermark(data)
        note = "SVG 按图元标识去水印" if svg_removed else "SVG 未发现带水印标识的图元"
        return ProcessedImage(
            file_name=file_name,
            media_type="image/svg+xml",
            data=cleaned_svg,
            watermark_applied=bool(svg_removed),
            regions=[{"type": "svg-element"} for _ in range(svg_removed)],
            note=note,
        )

    _register_heif()
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # noqa: BLE001 - 解码不了的格式按原图继续
        return ProcessedImage(
            file_name=file_name,
            media_type=media_type,
            data=data,
            watermark_applied=False,
            note=f"本机缺少 {ext or '该'} 格式的解码器（{exc.__class__.__name__}），已跳过去水印按原图继续",
        )

    try:
        cleaned, removed = _strip_watermark_pixels(image)
    except Exception as exc:  # noqa: BLE001
        return ProcessedImage(
            file_name=file_name,
            media_type=media_type,
            data=data,
            watermark_applied=False,
            note=f"去水印处理失败（{exc.__class__.__name__}），已按原图继续",
        )

    if max(cleaned.size) > MAX_IMAGE_SIDE:
        ratio = MAX_IMAGE_SIDE / max(cleaned.size)
        cleaned = cleaned.resize(
            (max(1, int(cleaned.width * ratio)), max(1, int(cleaned.height * ratio))),
            Image.Resampling.LANCZOS,
        )

    buffer = io.BytesIO()
    has_alpha = cleaned.mode in ("RGBA", "LA") or (
        cleaned.mode == "P" and "transparency" in cleaned.info
    )
    if ext in (".jpg", ".jpeg") and not has_alpha:
        cleaned.convert("RGB").save(buffer, format="JPEG", quality=92)
        out_media = "image/jpeg"
    elif ext in (".webp",) and not has_alpha:
        cleaned.convert("RGB").save(buffer, format="WEBP", quality=92)
        out_media = "image/webp"
    elif ext in (".tif", ".tiff"):
        cleaned.save(buffer, format="TIFF")
        out_media = "image/tiff"
    elif ext in (".bmp",):
        cleaned.convert("RGB").save(buffer, format="BMP")
        out_media = "image/bmp"
    elif ext in (".gif",):
        cleaned.convert("P", palette=Image.Palette.ADAPTIVE).save(buffer, format="GIF")
        out_media = "image/gif"
    else:
        cleaned.save(buffer, format="PNG")
        out_media = "image/png"

    note = f"已抹除 {len(removed)} 处水印覆盖层" if removed else "未识别到明显的覆盖式水印"
    return ProcessedImage(
        file_name=file_name,
        media_type=out_media,
        data=buffer.getvalue(),
        width=cleaned.width,
        height=cleaned.height,
        watermark_applied=bool(removed),
        regions=removed,
        note=note,
    )


def _media_type_of(ext: str) -> str:
    mapping = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".apng": "image/png",
        ".gif": "image/gif",
        ".svg": "image/svg+xml",
        ".webp": "image/webp",
        ".bmp": "image/bmp",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".raw": "application/octet-stream",
        ".cr2": "application/octet-stream",
        ".nef": "application/octet-stream",
        ".arw": "application/octet-stream",
        ".dng": "application/octet-stream",
        ".psd": "image/vnd.adobe.photoshop",
        ".ai": "application/postscript",
        ".heif": "image/heif",
        ".heic": "image/heic",
        ".avif": "image/avif",
        ".jxl": "image/jxl",
        ".pdf": "application/pdf",
    }
    return mapping.get(ext, "application/octet-stream")


# ---------------------------------------------------------------------
# 落盘
# ---------------------------------------------------------------------
def attachments_dir(base_dir: Path | None = None) -> Path:
    root = Path(base_dir) if base_dir else Path("data") / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def save_attachment(
    base_dir: Path | None,
    file_name: str,
    data: bytes,
    suffix: str = "",
) -> tuple[str, Path]:
    """保存附件，返回 (附件编号, 落盘路径)。原子写入，不留半截文件。"""
    root = attachments_dir(base_dir)
    attachment_id = uuid.uuid4().hex
    safe_suffix = suffix or extension_of(file_name)
    target = root / f"{attachment_id}{safe_suffix}"
    atomic_write_bytes(target, data, mode=0o600)
    return attachment_id, target


def load_attachment(base_dir: Path | None, attachment_id: str, suffix: str) -> bytes:
    """按编号读回附件内容；编号非法时返回空字节。"""
    if not re.fullmatch(r"[0-9a-f]{32}", attachment_id or ""):
        return b""
    path = attachments_dir(base_dir) / f"{attachment_id}{suffix}"
    if not path.is_file():
        return b""
    try:
        return path.read_bytes()
    except OSError:
        return b""


def decode_data_url(data_url: str) -> tuple[str, bytes]:
    """解析 data:image/png;base64,xxxx，返回 (文件名, 字节)。"""
    match = re.match(r"^data:([^;,]+)?(;charset=[^;,]+)?(;base64)?,(.*)$", data_url or "", re.DOTALL)
    if not match:
        raise AttachmentError("不是合法的 data URL")
    media_type = match.group(1) or "image/png"
    is_base64 = bool(match.group(3))
    payload = match.group(4) or ""
    try:
        data = base64.b64decode(payload) if is_base64 else payload.encode("utf-8")
    except (binascii.Error, ValueError) as exc:
        raise AttachmentError("data URL 内容无法解码") from exc
    suffix = suffix_of_media_type(media_type)
    return f"pasted{suffix}", data


def suffix_of_media_type(media_type: str) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
        "image/avif": ".avif",
        "image/heic": ".heic",
        "image/heif": ".heif",
        "image/svg+xml": ".svg",
    }
    return mapping.get(media_type.lower(), ".png")


def to_data_url(media_type: str, data: bytes) -> str:
    """把图片字节转成 data URL，供多模态模型直接读取。"""
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{media_type or 'image/png'};base64,{encoded}"


def safe_file_name(file_name: str) -> str:
    """去掉路径成分与危险字符，只保留文件名本身。"""
    base = os.path.basename(file_name or "").strip()
    base = re.sub(r"[\r\n\t]", "", base)
    base = base.replace("..", "_")
    return base[:120] or "attachment"


# ---------------------------------------------------------------------
# 附件元数据
# ---------------------------------------------------------------------
def _meta_path(base_dir: Path | None, attachment_id: str) -> Path:
    return attachments_dir(base_dir) / f"{attachment_id}.json"


def save_meta(base_dir: Path | None, attachment_id: str, meta: dict[str, Any]) -> None:
    from ..common.time_utils import utcnow_iso

    payload = dict(meta)
    payload["createdAt"] = utcnow_iso()
    atomic_write_bytes(
        _meta_path(base_dir, attachment_id),
        json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        mode=0o600,
    )


def load_meta(base_dir: Path | None, attachment_id: str) -> dict[str, Any]:
    """读回附件元数据；不存在时返回空字典。"""
    if not re.fullmatch(r"[0-9a-f]{32}", attachment_id or ""):
        return {}
    path = _meta_path(base_dir, attachment_id)
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def delete_attachment(base_dir: Path | None, attachment_id: str) -> bool:
    """删除附件本体与元数据。"""
    if not re.fullmatch(r"[0-9a-f]{32}", attachment_id or ""):
        return False
    meta = load_meta(base_dir, attachment_id)
    suffix = str(meta.get("suffix") or "")
    removed = False
    if suffix:
        target = attachments_dir(base_dir) / f"{attachment_id}{suffix}"
        if target.is_file():
            try:
                target.unlink()
                removed = True
            except OSError:
                removed = False
    meta_file = _meta_path(base_dir, attachment_id)
    if meta_file.is_file():
        try:
            meta_file.unlink()
            removed = True
        except OSError:
            pass
    return removed

