"""测试数据文件字节构造器。

把「文件格式目录」里的一种格式变成真实字节，规则与前端文案一一对应：

- 用户没填大小 → 用该格式的最小规格字节（``FileFormat.min_bytes``）；
- 能一次写出合法结构的格式（图片 / PDF / Office / 压缩包 / 音频 / 文本族）先把结构写好，
  再用该格式「剪下来也照样合法」的填充单元补到目标字节数；
- 分块格式（tar 512 字节块、SQLite 页）做不到任意精确字节，向上取整到块边界并在结果里说明；
- 其余格式只写文件头签名 + 填充，用来验证扩展名识别、大小限制和 MIME 校验。

产物全部在内存里构造，由 ``test_data_service`` 落盘；同一入参必然得到同一字节，
这样测试用例里写死的大小和校验值才可复现。
"""

from __future__ import annotations

import gzip
import io
import os
import sqlite3
import tempfile
import wave
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from aitp.services.test_data_formats import (
    BUILDER_BYTES,
    BUILDER_CODE,
    BUILDER_CSS,
    BUILDER_CSV,
    BUILDER_DOCX,
    BUILDER_GZ,
    BUILDER_HTML,
    BUILDER_INI,
    BUILDER_JS,
    BUILDER_JSON,
    BUILDER_PDF,
    BUILDER_PILLOW,
    BUILDER_PY,
    BUILDER_SQL,
    BUILDER_SQLITE,
    BUILDER_TAR,
    BUILDER_TEXT,
    BUILDER_TOML,
    BUILDER_WAV,
    BUILDER_XLSX,
    BUILDER_XML,
    BUILDER_YAML,
    BUILDER_ZIP,
    FileFormat,
)

#: 单次构造的字节上限；再大的文件交给磁盘配额，不在内存里拼
MAX_BUILD_BYTES = 64 * 1024 * 1024

#: 文本族一次最多拼多少行：更大的目标大小由填充段补齐，避免为凑行数空转几百万次
_MAX_GROW_ROWS = 500_000

#: 固定时间戳：让「同样参数 → 同样字节」成立，测试数据才能写死校验值
_STAMP = "2026-01-01 08:00:00"


class BuildError(ValueError):
    """目标大小非法或结构写不出来。"""


@dataclass(frozen=True, slots=True)
class BuiltFile:
    """一次构造的结果。"""

    data: bytes
    requested: int
    actual: int
    #: 是否精确命中请求字节数
    exact: bool
    #: 未能精确命中时的说明（前端与库内都展示）
    note: str
    #: 实际使用的构造方式说明
    mode_label: str

    def as_dict(self) -> dict[str, object]:
        return {
            "requestedBytes": self.requested,
            "bytes": self.actual,
            "exact": self.exact,
            "note": self.note,
            "modeLabel": self.mode_label,
        }


# ---------------------------------------------------------------- 填充工具

def _fit(payload: bytes, target: int, pad_unit: str = "") -> BuiltFile:
    """把 payload 补到正好 target 字节；pad_unit 的任意前缀都不能破坏文件。"""
    if len(payload) >= target:
        if len(payload) == target:
            return BuiltFile(payload, target, target, True, "", "")
        return BuiltFile(
            payload, target, len(payload), False,
            f"该格式合法结构最小约 {len(payload)} 字节，已按结构下限产出",
            "",
        )
    unit = pad_unit.encode("utf-8") if pad_unit else b"\0"
    if not unit:
        data = payload + b"\0" * (target - len(payload))
    else:
        missing = target - len(payload)
        data = payload + unit * (missing // len(unit)) + unit[: missing % len(unit)]
    return BuiltFile(data, target, len(data), len(data) == target, "", "")


def _grow(
    head: str,
    row: Callable[[int], str],
    tail: str,
    target: int,
    pad_unit: str,
) -> BuiltFile:
    """按「头 + N 行 + 尾」拼文本结构，行数随目标大小伸缩，再补到精确字节。

    行数逐行累加到刚好不超过目标大小，剩余空间用 ``pad_unit`` 填充——
    所有 pad_unit 的任意前缀都不会破坏对应格式。行号会改变行宽（99999 → 100000），
    所以不能按首行长度一次性估算，只能边加边判断。
    """
    head_bytes = head.encode("utf-8")
    tail_bytes = tail.encode("utf-8")
    overhead = len(head_bytes) + len(tail_bytes)
    if overhead >= target:
        return _fit(head_bytes + tail_bytes, target, pad_unit)
    rows: list[bytes] = []
    used = overhead
    while len(rows) < _MAX_GROW_ROWS:
        item = row(len(rows) + 1).encode("utf-8")
        if used + len(item) > target:
            break
        rows.append(item)
        used += len(item)
    payload = head_bytes + b"".join(rows) + tail_bytes
    return _fit(payload, target, pad_unit)


# ---------------------------------------------------------------- 文本族

_TEXT_HEADER = (
    "AITP 测试数据文件（AITestPlatform 本地生成，非业务数据）\r\n"
    f"生成基准时间：{_STAMP}\r\n"
)


def _payload_text(fmt: FileFormat, target: int) -> BuiltFile:
    head = _TEXT_HEADER + f"格式：{fmt.label}（.{fmt.ext}）\r\n"
    return _grow(
        head,
        lambda index: f"{index:05d} AITP-PAYLOAD-LINE 测试数据填充 记录{index}\r\n",
        "",
        target,
        "AITP 填充行\r\n",
    )


def _payload_code(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        f"// AITP 测试数据源码骨架：.{fmt.ext}（{fmt.label}）\n"
        "export const aitpFixture = {\n"
        "  project: 'AITestPlatform',\n"
        "  module: 'test-data-generation',\n"
    )
    return _grow(
        head,
        lambda index: f"  case{index}: '测试数据{index}',\n",
        "}\n",
        target,
        "// AITP 填充\n",
    )


def _payload_python(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        f'"""AITP 测试数据脚本骨架：.{fmt.ext}（{fmt.label}）。\n'
        "基准时间：" + _STAMP + "\n\"\"\"\n\n"
        "FIXTURE = {\n    'project': 'AITestPlatform',\n"
    )
    tail = (
        "}\n\n\ndef build(index: int) -> dict:\n"
        "    \"\"\"返回第 index 条测试数据。\"\"\"\n    return {**FIXTURE, 'index': index}\n"
    )
    return _grow(head, lambda index: f"    'case{index}': '测试数据{index}',\n", tail, target, "# AITP 填充\n")


def _payload_html(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        '<!DOCTYPE html>\n<html lang="zh-CN">\n<head>\n<meta charset="utf-8">\n'
        f"<title>AITP 测试数据 .{fmt.ext}</title>\n</head>\n"
        "<body>\n<h1>AITestPlatform 测试数据</h1>\n<ul>\n"
    )
    # HTML 末尾只能补空白，非空白会让严格解析器报错
    return _grow(head, lambda index: f"<li>测试数据 {index}</li>\n", "</ul>\n</body>\n</html>\n", target, " ")


def _payload_css(fmt: FileFormat, target: int) -> BuiltFile:
    head = f"/* AITP 测试数据样式：.{fmt.ext}（{fmt.label}） */\n"
    return _grow(
        head,
        lambda index: f".aitp-case-{index} {{\n  color: #333;\n  padding: 8px;\n}}\n",
        "",
        target,
        " ",
    )


def _payload_json(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        '{\n  "producer": "AITestPlatform",\n'
        f'  "format": "{fmt.ext}",\n  "generatedAt": "{_STAMP}",\n  "items": [\n'
    )
    tail = '    {"id": 0, "name": "AITP 收尾项", "pass": true}\n  ]\n}\n'
    # JSON 结尾补注释会破坏解析，只能用空白
    return _grow(
        head,
        lambda index: f'    {{"id": {index}, "name": "测试数据{index}", "pass": true}},\n',
        tail,
        target,
        " ",
    )


def _payload_xml(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<aitp producer="AITestPlatform" format="{fmt.ext}" label="{fmt.label}">\n'
    )
    return _grow(
        head,
        lambda index: f'  <item id="{index}">测试数据{index}</item>\n',
        "</aitp>\n",
        target,
        " ",
    )


def _payload_yaml(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        f"# AITP 测试数据 .{fmt.ext}\nproducer: AITestPlatform\n"
        f"generatedAt: '{_STAMP}'\nitems:\n"
    )
    return _grow(
        head,
        lambda index: f"  - id: {index}\n    name: 测试数据{index}\n",
        "",
        target,
        "# AITP 填充\n",
    )


def _payload_toml(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        f"# AITP 测试数据 .{fmt.ext}\n[meta]\nproducer = \"AITestPlatform\"\n"
        f"generated_at = \"{_STAMP}\"\n"
    )
    return _grow(
        head,
        lambda index: f'[[item]]\nid = {index}\nname = "测试数据{index}"\n',
        "",
        target,
        "# AITP 填充\n",
    )


def _payload_ini(fmt: FileFormat, target: int) -> BuiltFile:
    head = f"; AITP 测试数据 .{fmt.ext}\n[aitp]\nproducer = AITestPlatform\nformat = {fmt.ext}\n"
    return _grow(
        head,
        lambda index: f"[item{index}]\nname = 测试数据{index}\n\n",
        "",
        target,
        "; AITP 填充\n",
    )


def _payload_csv(fmt: FileFormat, target: int) -> BuiltFile:
    head = "id,name,priority,status,remark\n"

    def row(index: int) -> str:
        result = "通过" if index % 3 else "失败"
        return f"{index},测试用例{index},P{index % 4 + 1},{result},AITP 填充数据\n"

    # 填充段只能是空行：CSV 没有注释语法，补半行会让最后一条记录列数不齐
    return _grow(head, row, "", target, "\n")


def _payload_sql(fmt: FileFormat, target: int) -> BuiltFile:
    head = (
        f"-- AITP 测试数据 .{fmt.ext}（{fmt.label}）\n"
        "-- 基准时间：" + _STAMP + "\n"
        "CREATE TABLE IF NOT EXISTS aitp_fixture (\n  id INTEGER PRIMARY KEY,\n"
        "  name VARCHAR(64) NOT NULL,\n  status VARCHAR(16)\n);\n"
    )
    return _grow(
        head,
        lambda index: (
            f"INSERT INTO aitp_fixture (id, name, status) "
            f"VALUES ({index}, '测试数据{index}', 'ready');\n"
        ),
        "",
        target,
        "-- AITP 填充\n",
    )


# ---------------------------------------------------------------- 二进制结构

#: Pillow 写出时「模式 → 填充色」必须配套，否则会报 color must be int
_IMAGE_MODES: dict[str, tuple[str, object]] = {
    "PBM": ("1", 1),
    "PGM": ("L", 128),
    "PPM": ("RGB", (72, 120, 200)),
    "PNG": ("RGBA", (72, 120, 200, 255)),
    "GIF": ("P", 0),
    "ICO": ("RGBA", (72, 120, 200, 255)),
    "WEBP": ("RGB", (72, 120, 200)),
    "TIFF": ("RGB", (72, 120, 200)),
    "BMP": ("RGB", (72, 120, 200)),
    "JPEG": ("RGB", (72, 120, 200)),
}

#: Pillow 对这些格式只支持到某个最小边长：ICO 低于 16 时写出的是条目数为 0 的空头，
#: 文件头合法但任何解码器都打不开，所以不能沿用通用的 8 起点。
_MIN_IMAGE_SIDE: dict[str, int] = {"ICO": 16}


def _payload_image(fmt: FileFormat, target: int) -> BuiltFile:
    from PIL import Image

    pillow_format = fmt.builder.split(":", 1)[1] if ":" in fmt.builder else "PNG"
    mode, color = _IMAGE_MODES.get(pillow_format, ("RGB", (72, 120, 200)))
    # 边长只在「放大后仍明显小于请求大小」时增加：小请求保持最小结构，大请求少补些填充
    side = _MIN_IMAGE_SIDE.get(pillow_format, 8)
    while side < 512 and (side + 8) ** 2 * 4 <= target * 3 // 5:
        side += 8
    image = Image.new(mode, (side, side), color)  # type: ignore[arg-type]
    buffer = io.BytesIO()
    try:
        image.save(buffer, format=pillow_format)
    except Exception:
        # Pillow 没有该格式的写出插件，退化成签名填充，仍保证扩展名与大小可控
        built = _payload_bytes(fmt, target)
        note = built.note
        return BuiltFile(
            built.data, built.requested, built.actual, built.exact,
            (note + "；" if note else "") + "本地图像库不支持写出该格式，按文件头签名填充",
            "",
        )
    # 图片解码器在读完自身数据后即可停止，尾部字节不影响打开
    return _fit(buffer.getvalue(), target)


def _payload_pdf(fmt: FileFormat, target: int) -> BuiltFile:
    """手写最小 PDF：正文流里的注释长度可控，按差值微调即可命中目标字节数。"""

    def render(pad: int) -> bytes:
        text = "BT /F1 12 Tf 72 720 Td (AITestPlatform test fixture ." + fmt.ext + ") Tj ET"
        stream = text + ("\n%" + "A" * pad) if pad > 0 else text
        stream_bytes = stream.encode("latin-1", "replace")
        objects = [
            b"<< /Type /Catalog /Pages 2 0 R >>",
            b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
            b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] "
            b"/Resources << /Font << /F1 5 0 R >> >> /Contents 4 0 R >>",
            b"<< /Length " + str(len(stream_bytes)).encode() + b" >>\nstream\n"
            + stream_bytes + b"\nendstream",
            b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        ]
        out = bytearray(b"%PDF-1.4\n")
        offsets = []
        for number, content in enumerate(objects, start=1):
            offsets.append(len(out))
            out += f"{number} 0 obj\n".encode() + content + b"\nendobj\n"
        xref_at = len(out)
        out += f"xref\n0 {len(objects) + 1}\n".encode()
        out += b"0000000000 65535 f \n"
        for offset in offsets:
            out += f"{offset:010d} 00000 n \n".encode()
        out += (
            f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R >>\n"
            f"startxref\n{xref_at}\n%%EOF\n"
        ).encode()
        return bytes(out)

    pad = max(0, target - len(render(0)))
    payload = render(pad)
    # /Length 位数进位会让总长多 1 字节，按差值回调补齐次数即可精确命中
    for _ in range(4):
        if len(payload) == target:
            break
        pad = max(0, pad + target - len(payload))
        payload = render(pad)
    if len(payload) == target:
        return BuiltFile(payload, target, target, True, "", "")
    if len(payload) < target:
        return _fit(payload, target)
    # 结构下限已超出请求大小
    return BuiltFile(
        payload, target, len(payload), False,
        f"合法 PDF 结构最小约 {len(payload)} 字节，已按结构下限产出", "",
    )


def _entry(name: str, compress_type: int = zipfile.ZIP_DEFLATED) -> zipfile.ZipInfo:
    """固定时间与属性的目录项：同样入参才能得到同样字节。"""
    info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
    info.compress_type = compress_type
    info.external_attr = 0o644 << 16
    return info


def _pad_archive(raw_base: bytes, fmt: FileFormat, target: int) -> BuiltFile:
    """把已有的 zip 结构（含 docx / xlsx）补到目标字节数。

    先把原包重写一遍，把条目时间固定、目录项归一，再用一个 STORED 填充条目补差值；
    填充长度与包总长严格 1:1，因此除结构下限外都能精确命中请求字节数。
    Office 会忽略未被关系引用的部件，测试用途下不影响打开。
    """
    base = _rewrite_zip(raw_base)
    if len(base) >= target:
        if len(base) == target:
            return BuiltFile(base, target, target, True, "", "")
        return BuiltFile(
            base, target, len(base), False,
            f"该格式合法结构最小约 {len(base)} 字节，已按结构下限产出", "",
        )
    entry_name = f"aitp_fill/{fmt.ext or 'bin'}_fill.bin"
    probe = _rewrite_zip(base, entry=(entry_name, b""))
    need = target - len(probe)
    if need < 0:
        return BuiltFile(
            probe, target, len(probe), False, f"包目录项本身已占 {len(probe)} 字节，已按下限产出", "",
        )
    data = _rewrite_zip(base, entry=(entry_name, b"\0" * need))
    if len(data) != target:
        return BuiltFile(
            data, target, len(data), False,
            f"包结构按目录块对齐，实际 {len(data)} 字节", "",
        )
    return BuiltFile(data, target, target, True, "", "")


def _rewrite_zip(
    base: bytes,
    entry: tuple[str, bytes] | None = None,
) -> bytes:
    """按原有压缩方式重写包，条目时间固定；填充条目一律不压缩，长度可预测。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(base)) as source, \
            zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for info in source.infolist():
            if info.is_dir():
                continue
            archive.writestr(
                _entry(info.filename, info.compress_type), source.read(info.filename)
            )
        if entry is not None:
            archive.writestr(_entry(entry[0], zipfile.ZIP_STORED), entry[1])
    return buffer.getvalue()


def _payload_zip(fmt: FileFormat, target: int) -> BuiltFile:
    readme = (
        "AITestPlatform 测试数据包\n"
        f"格式：.{fmt.ext}（{fmt.label}）\n"
        f"基准时间：{_STAMP}\n"
    ).encode()
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(_entry("README.txt"), readme)
    return _pad_archive(buffer.getvalue(), fmt, target)


def _payload_docx(fmt: FileFormat, target: int) -> BuiltFile:
    from docx import Document

    doc = Document()
    doc.add_heading("AITestPlatform 测试数据", level=1)
    doc.add_paragraph(f"格式：.{fmt.ext}（{fmt.label}）；基准时间：{_STAMP}")
    table = doc.add_table(rows=2, cols=3)
    table.rows[0].cells[0].text = "编号"
    table.rows[0].cells[1].text = "名称"
    table.rows[0].cells[2].text = "结果"
    table.rows[1].cells[0].text = "1"
    table.rows[1].cells[1].text = "测试数据"
    table.rows[1].cells[2].text = "通过"
    buffer = io.BytesIO()
    doc.save(buffer)
    return _pad_archive(buffer.getvalue(), fmt, target)


def _payload_xlsx(fmt: FileFormat, target: int) -> BuiltFile:
    from openpyxl import Workbook

    book = Workbook()
    sheet = book.active
    sheet.title = "测试数据"
    sheet.append(["编号", "名称", "优先级", "结果"])
    for index in range(1, 21):
        sheet.append([index, f"测试用例{index}", f"P{index % 4 + 1}", "通过" if index % 3 else "失败"])
    buffer = io.BytesIO()
    book.save(buffer)
    return _pad_archive(buffer.getvalue(), fmt, target)


def _tar_entry(name: str, data: bytes) -> bytes:
    """手写 ustar 目录块：头 512 字节 + 数据按 512 对齐，长度完全可预测。

    标准库 ``tarfile`` 会把整包补齐到 10240 字节的记录边界，那样没法按字节控大小，
    所以这里按布局表逐字段写入。
    """
    header = bytearray(b"\0" * 512)

    def put(offset: int, width: int, value: bytes) -> None:
        chunk = value[:width]
        header[offset:offset + len(chunk)] = chunk

    def octal(offset: int, width: int, value: int) -> None:
        put(offset, width, (f"{value:0{width - 1}o}\0").encode("ascii"))

    name_bytes = name.encode("utf-8")[:100]
    put(0, 100, name_bytes)
    octal(100, 8, 0o644)          # mode
    octal(108, 8, 0)              # uid
    octal(116, 8, 0)              # gid
    octal(124, 12, len(data))     # size
    octal(136, 12, 0)             # mtime
    put(156, 1, b"0")             # typeflag: 普通文件
    put(257, 6, b"ustar\0")       # magic
    put(263, 2, b"00")            # version
    put(265, 32, b"aitp")         # uname
    put(297, 32, b"aitp")         # gname
    # 校验和：checksum 字段按 8 个空格参与求和，再回写六位八进制 + NUL + 空格
    checksum = sum(header[:148]) + sum(header[156:]) + 8 * 0x20
    put(148, 8, (f"{checksum:06o}\0 ").encode("ascii"))
    return bytes(header) + data + b"\0" * (-len(data) % 512)


def _tar_bytes(files: list[tuple[str, bytes]]) -> bytes:
    return b"".join(_tar_entry(name, data) for name, data in files) + b"\0" * 1024


def _payload_tar(fmt: FileFormat, target: int) -> BuiltFile:
    """tar 以 512 字节块存储：填充条目按字节换算，能命中任意 512 的整数倍。"""
    readme = (
        f"AITestPlatform 测试数据 .{fmt.ext}（{fmt.label}）基准时间 {_STAMP}\n"
    ).encode("utf-8")

    def build(pad_bytes: int) -> bytes:
        return _tar_bytes([
            ("README.txt", readme),
            ("fill/pad.bin", b"\0" * max(0, pad_bytes)),
        ])

    base = build(0)
    if target <= len(base):
        return BuiltFile(
            base, target, len(base), target == len(base),
            "" if target == len(base) else f"tar 结构下限约 {len(base)} 字节，已按下限产出", "",
        )
    data = build(target - len(base))
    exact = len(data) == target
    note = "" if exact else f"tar 按 512 字节块存储，实际 {len(data)} 字节"
    return BuiltFile(data, target, len(data), exact, note, "")


def _payload_gz(fmt: FileFormat, target: int) -> BuiltFile:
    raw = (
        f"AITestPlatform 测试数据 .{fmt.ext}（{fmt.label}）\n基准时间 {_STAMP}\n"
    ).encode("utf-8") + b"AITP-FILL\n" * 32
    payload = gzip.compress(raw, compresslevel=1)
    # gzip 解码在读完自身流后停止，尾部字节被忽略
    return _fit(payload, target)


def _payload_wav(fmt: FileFormat, target: int) -> BuiltFile:
    buffer = io.BytesIO()
    frames = max(1, (target - 44) // 2)
    with wave.open(buffer, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(8000)
        handle.writeframes(b"\0\0" * frames)
    payload = buffer.getvalue()
    if target % 2:
        return BuiltFile(
            payload, target, len(payload), len(payload) == target,
            "" if len(payload) == target else "WAV 采样按 2 字节对齐，已就近取偶数字节",
            "",
        )
    return _fit(payload, target)


def _payload_sqlite(fmt: FileFormat, target: int) -> BuiltFile:
    """用标准库真建一个 SQLite 库。

    库文件必须是整数页，页大小只能在 512～65536 的 2 的幂里挑：这里优先挑能整除目标
    大小的最小页，页越小越容易正好命中请求字节数。填充写成真实 BLOB 行，这样产出的库
    可以被 sqlite 正常打开并做 integrity_check，而不是尾部补零的伪库。
    """
    page_size = next(
        (size for size in (512, 1024, 2048, 4096) if target % size == 0), 4096
    )
    row_count = max(1, min(400, target // 1024))
    blob_len = max(0, target - row_count * 64 - page_size)
    payload = b""
    for _ in range(4):
        payload = _write_sqlite_db(page_size, blob_len, row_count)
        if len(payload) == target:
            return BuiltFile(payload, target, target, True, "", "")
        if len(payload) > target:
            if blob_len == 0:
                break
            blob_len = max(0, blob_len - (len(payload) - target) - page_size)
        else:
            blob_len += target - len(payload)
    exact = len(payload) == target
    note = ""
    if not exact:
        note = (
            f"SQLite 按 {page_size} 字节整页存储，产出实际 {len(payload)} 字节"
            f"{'（不小于请求大小）' if len(payload) > target else '（结构下限）'}"
        )
    return BuiltFile(payload, target, len(payload), exact, note, "")


def _write_sqlite_db(page_size: int, blob_len: int, row_count: int) -> bytes:
    handle, temp_name = tempfile.mkstemp(prefix="aitp-fixture-", suffix=".db")
    os.close(handle)
    path = Path(temp_name)
    path.unlink(missing_ok=True)
    try:
        conn = sqlite3.connect(path)
        try:
            conn.execute(f"PRAGMA page_size = {page_size}")
            conn.execute("PRAGMA journal_mode = DELETE")
            conn.execute(
                "CREATE TABLE aitp_fixture ("
                "id INTEGER PRIMARY KEY, name TEXT NOT NULL, status TEXT, blob BLOB)"
            )
            conn.execute("CREATE INDEX idx_aitp_status ON aitp_fixture(status)")
            rows = [
                (index, f"测试数据{index}", "ready", None) for index in range(1, row_count + 1)
            ]
            conn.executemany(
                "INSERT INTO aitp_fixture (id, name, status, blob) VALUES (?, ?, ?, ?)", rows
            )
            if blob_len > 0:
                conn.execute(
                    "INSERT INTO aitp_fixture (id, name, status, blob) VALUES (?, ?, ?, ?)",
                    (999999, "AITP 填充", "fill", b"\0" * blob_len),
                )
            conn.commit()
            conn.execute("VACUUM")
        finally:
            conn.close()
        return path.read_bytes()
    finally:
        for extra in (path, Path(str(path) + "-wal"), Path(str(path) + "-shm"),
                      Path(str(path) + "-journal")):
            extra.unlink(missing_ok=True)


# ---------------------------------------------------------------- 分发

_BUILDERS: dict[str, Callable[[FileFormat, int], BuiltFile]] = {
    BUILDER_TEXT: _payload_text,
    BUILDER_CODE: _payload_code,
    BUILDER_PY: _payload_python,
    BUILDER_JS: _payload_code,
    BUILDER_HTML: _payload_html,
    BUILDER_CSS: _payload_css,
    BUILDER_XML: _payload_xml,
    BUILDER_JSON: _payload_json,
    BUILDER_YAML: _payload_yaml,
    BUILDER_TOML: _payload_toml,
    BUILDER_INI: _payload_ini,
    BUILDER_CSV: _payload_csv,
    BUILDER_SQL: _payload_sql,
    BUILDER_PDF: _payload_pdf,
    BUILDER_DOCX: _payload_docx,
    BUILDER_XLSX: _payload_xlsx,
    BUILDER_ZIP: _payload_zip,
    BUILDER_TAR: _payload_tar,
    BUILDER_GZ: _payload_gz,
    BUILDER_WAV: _payload_wav,
    BUILDER_SQLITE: _payload_sqlite,
}


def _payload_bytes(fmt: FileFormat, target: int) -> BuiltFile:
    """签名填充 / 规格填充：文件头签名 + 零字节补足到目标大小。"""
    head = fmt.signature
    if not head:
        head = f"AITP-FILL .{fmt.ext} {fmt.label}\n".encode("utf-8", "replace")
    return _fit(head, target)


def build_file(fmt: FileFormat, size_bytes: int | None) -> BuiltFile:
    """按格式与请求大小产出文件字节；size_bytes 为空时用该格式的最小规格。"""
    if size_bytes is None:
        default_size = True
        target = fmt.min_bytes
    else:
        default_size = False
        target = int(size_bytes)
    if target <= 0:
        raise BuildError("文件大小必须是正整数字节")
    if target > MAX_BUILD_BYTES:
        raise BuildError(f"单个文件不能超过 {MAX_BUILD_BYTES // 1024 // 1024} MB")

    base_name = fmt.builder.split(":", 1)[0]
    if base_name == BUILDER_PILLOW:
        built = _payload_image(fmt, target)
    elif base_name == BUILDER_BYTES:
        built = _payload_bytes(fmt, target)
    else:
        builder = _BUILDERS.get(base_name)
        if builder is None:
            raise BuildError(f"未知的文件构造方式：{fmt.builder}")
        built = builder(fmt, target)

    actual = len(built.data)
    if default_size:
        # 没填大小时产物就是「该格式最小规格」，结构下限高于标称值也不算偏差
        note = "" if actual == target else f"该格式结构下限 {actual} 字节，已按最小规格产出"
        return BuiltFile(built.data, target, actual, True, note, fmt.mode_label)
    note = built.note
    if actual != target and not note:
        # 只要没逐字节命中就必须说明原因，不能让前端拿到一个「不精确但没说为什么」的结果
        note = (
            f"{fmt.label}至少需要 {actual} 字节才是完整文件，已按下限产出"
            if actual > target
            else f"受{fmt.mode_label}限制无法逐字节命中，实际 {actual} 字节"
        )
    return BuiltFile(built.data, target, actual, actual == target, note, fmt.mode_label)
