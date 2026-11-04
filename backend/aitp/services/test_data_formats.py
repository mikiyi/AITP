"""测试文件格式目录。

「测试数据生成」的文件分支要让用户任选常见文件格式，这里给出每种格式的中文名、
分类、MIME、最小规格字节与构造方式。构造方式决定产物有多「真」：

- 结构构造：本地库能写出可被软件正常打开的文件（图片 / PDF / Office / 压缩包 / 音视频 / 文本族）；
- 签名填充：写入该格式的文件头签名，再补足到目标大小。扩展名与大小正确，
  适合验证上传限制与类型识别，不保证能被专业软件打开；
- 规格填充：没有通用文件头的格式，只按目标大小补足内容。

新增格式往 _ROWS 追加一行即可；分类在前端的展示顺序按 _ROWS 里首次出现的顺序。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# ---- 构造方式 ----
BUILDER_PILLOW = "pillow"  # pillow:<Pillow 格式名>
BUILDER_BYTES = "bytes"  # 文件签名 + 补足
BUILDER_TEXT = "text"  # 文本行
BUILDER_CODE = "code"  # 源码骨架
BUILDER_PY = "py"  # Python 脚本骨架
BUILDER_JS = "js"  # JavaScript 骨架
BUILDER_HTML = "html"
BUILDER_CSS = "css"
BUILDER_XML = "xml"
BUILDER_JSON = "json"
BUILDER_YAML = "yaml"
BUILDER_TOML = "toml"
BUILDER_INI = "ini"
BUILDER_CSV = "csv"
BUILDER_SQL = "sql"
BUILDER_PDF = "pdf"
BUILDER_DOCX = "docx"
BUILDER_XLSX = "xlsx"
BUILDER_ZIP = "zip"
BUILDER_TAR = "tar"
BUILDER_GZ = "gz"
BUILDER_WAV = "wav"
BUILDER_SQLITE = "sqlite"

#: 能写出「打开即正常」的文件的方式，其余按签名或纯填充处理
STRUCTURED_BUILDERS = frozenset({
    BUILDER_PILLOW, BUILDER_TEXT, BUILDER_CODE, BUILDER_PY, BUILDER_JS, BUILDER_HTML,
    BUILDER_CSS, BUILDER_XML, BUILDER_JSON, BUILDER_YAML, BUILDER_TOML, BUILDER_INI,
    BUILDER_CSV, BUILDER_SQL, BUILDER_PDF, BUILDER_DOCX, BUILDER_XLSX, BUILDER_ZIP,
    BUILDER_TAR, BUILDER_GZ, BUILDER_WAV, BUILDER_SQLITE,
})

MODE_STRUCTURED = "structured"
MODE_SIGNATURE = "signature"
MODE_FILLER = "filler"
MODE_LABELS = {
    MODE_STRUCTURED: "结构构造",
    MODE_SIGNATURE: "签名填充",
    MODE_FILLER: "规格填充",
}

OCTET = "application/octet-stream"

#: (扩展名, 中文名, 分类, MIME, 最小规格字节, 构造方式, 文件签名 hex)
_ROWS: tuple[tuple[str, str, str, str, int, str, str], ...] = (
    # ---------------- 图片 ----------------
    ("jpg", "JPEG 图片", "图片", "image/jpeg", 704, "pillow:JPEG", "ffd8ff"),
    ("jpeg", "JPEG 图片", "图片", "image/jpeg", 704, "pillow:JPEG", "ffd8ff"),
    ("jfif", "JFIF 图片", "图片", "image/jpeg", 704, "pillow:JPEG", "ffd8ff"),
    ("png", "PNG 图片", "图片", "image/png", 300, "pillow:PNG", "89504e470d0a1a0a"),
    ("apng", "动态 PNG", "图片", "image/apng", 300, "pillow:PNG", "89504e470d0a1a0a"),
    # GIF 头部有 87a / 89a 两种，Pillow 按有无动画选择，目录只声明两者共同的稳定前缀
    ("gif", "GIF 图片", "图片", "image/gif", 300, "pillow:GIF", "47494638"),
    ("bmp", "BMP 位图", "图片", "image/bmp", 300, "pillow:BMP", "424d"),
    ("dib", "设备无关位图", "图片", "image/bmp", 300, "pillow:BMP", "424d"),
    ("tif", "TIFF 图像", "图片", "image/tiff", 384, "pillow:TIFF", "49492a00"),
    ("tiff", "TIFF 图像", "图片", "image/tiff", 384, "pillow:TIFF", "49492a00"),
    ("webp", "WebP 图片", "图片", "image/webp", 300, "pillow:WEBP", "52494646"),
    ("ico", "网站图标", "图片", "image/vnd.microsoft.icon", 200, "pillow:ICO", "00000100"),
    ("icns", "macOS 图标", "图片", "image/icns", 512, BUILDER_BYTES, "69636e73"),
    ("ppm", "彩色 pixmap", "图片", "image/x-portable-pixmap", 256, "pillow:PPM", "5036"),
    ("pgm", "灰度 pixmap", "图片", "image/x-portable-graymap", 100, "pillow:PGM", "5035"),
    ("pbm", "黑白 pixmap", "图片", "image/x-portable-bitmap", 100, "pillow:PBM", "5034"),
    ("pnm", "通用 pixmap", "图片", "image/x-portable-anymap", 256, "pillow:PPM", "5037"),
    ("tga", "TGA 位图", "图片", OCTET, 512, BUILDER_BYTES, ""),
    ("pcx", "PCX 位图", "图片", "image/x-pcx", 512, BUILDER_BYTES, "0a050000"),
    ("psd", "Photoshop 文档", "图片", "image/vnd.adobe.photoshop", 1024, BUILDER_BYTES, "38425053"),
    ("psb", "Photoshop 大文档", "图片", OCTET, 1024, BUILDER_BYTES, "38425053"),
    ("heic", "HEIF 图片", "图片", "image/heic", 1024, BUILDER_BYTES, "68656963"),
    ("heif", "HEIF 图片", "图片", "image/heif", 1024, BUILDER_BYTES, "68656966"),
    ("avif", "AVIF 图片", "图片", "image/avif", 1024, BUILDER_BYTES, "61766966"),
    ("jxl", "JPEG XL 图片", "图片", "image/jxl", 512, BUILDER_BYTES, "ff4f"),
    ("qoi", "QOI 图片", "图片", "image/qoi", 256, BUILDER_BYTES, "716f6966"),
    ("exr", "OpenEXR 高动态图", "图片", "image/x-exr", 512, BUILDER_BYTES, "762f3101"),
    ("hdr", "Radiance 高动态图", "图片", "image/vnd.radiance", 256, BUILDER_BYTES, "233f52414449"),
    ("raw", "相机原始图", "图片", OCTET, 2048, BUILDER_BYTES, ""),
    ("cr2", "佳能 RAW", "图片", OCTET, 2048, BUILDER_BYTES, "49492a00"),
    ("cr3", "佳能 RAW（新）", "图片", OCTET, 2048, BUILDER_BYTES, ""),
    ("nef", "尼康 RAW", "图片", OCTET, 2048, BUILDER_BYTES, "4d4d002a"),
    ("arw", "索尼 RAW", "图片", OCTET, 2048, BUILDER_BYTES, "49492a00"),
    ("raf", "富士 RAW", "图片", OCTET, 2048, BUILDER_BYTES, "46554a49"),
    ("orf", "奥林巴斯 RAW", "图片", OCTET, 2048, BUILDER_BYTES, "4949524f"),
    ("rw2", "松下 RAW", "图片", OCTET, 2048, BUILDER_BYTES, ""),
    ("dng", "数字负片", "图片", OCTET, 2048, BUILDER_BYTES, "49492a00"),
    ("mng", "移动 PNG 序列", "图片", "video/x-mng", 512, BUILDER_BYTES, "6d6e6700"),
    ("wdp", "HD Photo", "图片", "image/vnd.ms-photo", 1024, BUILDER_BYTES, "4d535000"),
    ("dds", "DirectDraw 贴图", "图片", OCTET, 1024, BUILDER_BYTES, "44445320"),
    ("kra", "Krita 画稿", "图片", OCTET, 2048, BUILDER_ZIP, "504b0304"),
    ("xcf", "GIMP 工程", "图片", OCTET, 1024, BUILDER_BYTES, "7863662d56"),
    ("sketch", "Sketch 设计稿", "图片", OCTET, 4096, BUILDER_ZIP, "504b0304"),
    ("fig", "Figma 设计稿", "图片", OCTET, 4096, BUILDER_ZIP, "504b0304"),
    # ---------------- 矢量图 ----------------
    ("svg", "SVG 矢量图", "矢量图", "image/svg+xml", 300, BUILDER_XML, ""),
    ("svgz", "SVG 压缩图", "矢量图", "image/svg+xml", 300, BUILDER_GZ, "1f8b"),
    ("eps", "EPS 封装图", "矢量图", "application/postscript", 512, BUILDER_TEXT, ""),
    ("ai", "Illustrator 文档", "矢量图", "application/postscript", 2048, BUILDER_PDF, "25504446"),
    ("ps", "PostScript 文档", "矢量图", "application/postscript", 512, BUILDER_TEXT, ""),
    ("emf", "Windows 增强图元", "矢量图", "image/emf", 1024, BUILDER_BYTES, "01000000"),
    ("wmf", "Windows 图元文件", "矢量图", "image/wmf", 1024, BUILDER_BYTES, "d7cdc69a"),
    ("cdr", "CorelDRAW 文档", "矢量图", OCTET, 2048, BUILDER_BYTES, ""),
    ("cmx", "Corel 元文件", "矢量图", OCTET, 1024, BUILDER_BYTES, ""),
    ("dwg", "AutoCAD 图纸", "矢量图", OCTET, 2048, BUILDER_BYTES, "41433130"),
    ("dxf", "AutoCAD 交换格式", "矢量图", "image/vnd.dxf", 512, BUILDER_TEXT, ""),
    ("dwf", "Autodesk 发布格式", "矢量图", OCTET, 2048, BUILDER_ZIP, "504b0304"),
    ("blend", "Blender 场景", "矢量图", OCTET, 4096, BUILDER_BYTES, "424c454e444552"),
    ("skp", "SketchUp 模型", "矢量图", OCTET, 4096, BUILDER_BYTES, ""),
    # ---------------- 文档 ----------------
    ("pdf", "PDF 文档", "文档", "application/pdf", 1200, BUILDER_PDF, "255044462d"),
    ("doc", "Word 文档（旧）", "文档", "application/msword", 4096, BUILDER_BYTES, "d0cf11e0a1b11ae1"),
    ("docx", "Word 文档", "文档",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.document", 37100, BUILDER_DOCX, ""),
    ("docm", "Word 宏文档", "文档", "application/vnd.ms-word.document.macroEnabled.12",
     37100, BUILDER_DOCX, ""),
    ("dotx", "Word 模板", "文档",
     "application/vnd.openxmlformats-officedocument.wordprocessingml.template", 37100, BUILDER_DOCX, ""),
    ("rtf", "RTF 富文本", "文档", "application/rtf", 512, BUILDER_TEXT, ""),
    ("odt", "OpenDocument 文本", "文档", "application/vnd.oasis.opendocument.text", 4096, BUILDER_ZIP, ""),
    ("ott", "OpenDocument 文本模板", "文档", "application/vnd.oasis.opendocument.text-template",
     4096, BUILDER_ZIP, ""),
    ("sxw", "StarOffice 文本", "文档", OCTET, 4096, BUILDER_ZIP, ""),
    ("pages", "Pages 文档", "文档", OCTET, 8192, BUILDER_ZIP, ""),
    ("epub", "EPUB 电子书", "文档", "application/epub+zip", 8192, BUILDER_ZIP, ""),
    ("mobi", "Kindle 电子书", "文档", "application/x-mobipocket-ebook", 8192, BUILDER_BYTES, ""),
    ("azw", "Kindle 电子书（旧）", "文档", OCTET, 8192, BUILDER_BYTES, ""),
    ("azw3", "Kindle 电子书（新）", "文档", OCTET, 8192, BUILDER_ZIP, ""),
    ("fb2", "FictionBook 电子书", "文档", "application/x-fictionbook+xml", 512, BUILDER_XML, ""),
    ("djvu", "DjVu 文档", "文档", "image/vnd.djvu", 2048, BUILDER_BYTES, "41542654464f524d"),
    ("xps", "XPS 文档", "文档", "application/vnd.ms-xpsdocument", 8192, BUILDER_ZIP, ""),
    ("tex", "LaTeX 源文件", "文档", "application/x-tex", 300, BUILDER_TEXT, ""),
    ("md", "Markdown 文档", "文档", "text/markdown", 300, BUILDER_TEXT, ""),
    ("markdown", "Markdown 文档", "文档", "text/markdown", 300, BUILDER_TEXT, ""),
    ("txt", "纯文本", "文档", "text/plain", 200, BUILDER_TEXT, ""),
    ("adoc", "AsciiDoc 文档", "文档", "text/asciidoc", 300, BUILDER_TEXT, ""),
    ("org", "Emacs Org 文档", "文档", "text/org", 300, BUILDER_TEXT, ""),
    ("typ", "Typst 文档", "文档", "text/typst", 300, BUILDER_TEXT, ""),
    ("one", "OneNote 节", "文档", OCTET, 4096, BUILDER_BYTES, ""),
    ("msg", "Outlook 邮件", "文档", "application/vnd.ms-outlook", 4096, BUILDER_BYTES, "d0cf11e0a1b11ae1"),
    ("wpd", "WordPerfect 文档", "文档", OCTET, 4096, BUILDER_BYTES, ""),
    ("wps", "WPS 文字", "文档", OCTET, 4096, BUILDER_BYTES, ""),
    ("lrf", "旧版电子书", "文档", OCTET, 4096, BUILDER_BYTES, ""),
    # ---------------- 电子表格与结构化数据 ----------------
    ("xls", "Excel 工作簿（旧）", "电子表格", "application/vnd.ms-excel", 4096, BUILDER_BYTES,
     "d0cf11e0a1b11ae1"),
    ("xlsx", "Excel 工作簿", "电子表格",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", 6144, BUILDER_XLSX, ""),
    ("xlsm", "Excel 宏工作簿", "电子表格", "application/vnd.ms-excel.sheet.macroEnabled.12",
     6144, BUILDER_XLSX, ""),
    ("xltx", "Excel 模板", "电子表格",
     "application/vnd.openxmlformats-officedocument.spreadsheetml.template", 6144, BUILDER_XLSX, ""),
    ("xlt", "Excel 模板（旧）", "电子表格", OCTET, 4096, BUILDER_BYTES, "d0cf11e0a1b11ae1"),
    ("xlsb", "Excel 二进制工作簿", "电子表格", "application/vnd.ms-excel.sheet.binary.macroEnabled.12",
     6144, BUILDER_XLSX, ""),
    ("csv", "CSV 逗号表", "电子表格", "text/csv", 400, BUILDER_CSV, ""),
    ("tsv", "TSV 制表符表", "电子表格", "text/tab-separated-values", 400, BUILDER_CSV, ""),
    ("ods", "OpenDocument 表格", "电子表格", "application/vnd.oasis.opendocument.spreadsheet",
     4096, BUILDER_ZIP, ""),
    ("numbers", "Numbers 表格", "电子表格", OCTET, 8192, BUILDER_ZIP, ""),
    ("et", "WPS 表格", "电子表格", OCTET, 4096, BUILDER_BYTES, ""),
    ("dps", "WPS 演示", "电子表格", OCTET, 4096, BUILDER_BYTES, ""),
    ("parquet", "Parquet 列存", "电子表格", "application/x-parquet", 1024, BUILDER_BYTES, "50415231"),
    ("avro", "Avro 数据", "电子表格", "application/x-avro", 1024, BUILDER_BYTES, "4f626a01"),
    ("orc", "ORC 列存", "电子表格", "application/x-orc", 1024, BUILDER_BYTES, "4f5243"),
    ("arrow", "Arrow 列存", "电子表格", "application/vnd.apache.arrow.file", 1024, BUILDER_BYTES, "4152524f5731"),
    ("feather", "Feather 列存", "电子表格", "application/vnd.apache.arrow.file", 1024, BUILDER_BYTES, ""),
    ("sav", "SPSS 数据", "电子表格", OCTET, 2048, BUILDER_BYTES, ""),
    ("dta", "Stata 数据", "电子表格", OCTET, 2048, BUILDER_BYTES, ""),
    # ---------------- 演示文稿 ----------------
    ("ppt", "PowerPoint 演示（旧）", "演示文稿", "application/vnd.ms-powerpoint", 4096, BUILDER_BYTES,
     "d0cf11e0a1b11ae1"),
    ("pptx", "PowerPoint 演示", "演示文稿",
     "application/vnd.openxmlformats-officedocument.presentationml.presentation", 8192, BUILDER_ZIP, ""),
    ("pptm", "PowerPoint 宏演示", "演示文稿", "application/vnd.ms-powerpoint.presentation.macroEnabled.12",
     8192, BUILDER_ZIP, ""),
    ("potx", "PowerPoint 模板", "演示文稿",
     "application/vnd.openxmlformats-officedocument.presentationml.template", 8192, BUILDER_ZIP, ""),
    ("pps", "PowerPoint 放映", "演示文稿", OCTET, 4096, BUILDER_BYTES, "d0cf11e0a1b11ae1"),
    ("odp", "OpenDocument 演示", "演示文稿", "application/vnd.oasis.opendocument.presentation",
     4096, BUILDER_ZIP, ""),
    ("key", "Keynote 演示", "演示文稿", OCTET, 8192, BUILDER_ZIP, ""),
    ("swf", "Flash 动画", "演示文稿", "application/x-shockwave-flash", 1024, BUILDER_BYTES, "435753"),
    ("swa", "Flash 音频", "演示文稿", OCTET, 1024, BUILDER_BYTES, "465753"),
    # ---------------- 音频 ----------------
    ("mp3", "MP3 音频", "音频", "audio/mpeg", 2048, BUILDER_BYTES, "494433"),
    ("wav", "WAV 音频", "音频", "audio/x-wav", 1024, BUILDER_WAV, "52494646"),
    ("flac", "FLAC 无损音频", "音频", "audio/flac", 2048, BUILDER_BYTES, "664c6143"),
    ("ogg", "Ogg 音频", "音频", "audio/ogg", 2048, BUILDER_BYTES, "4f676753"),
    ("oga", "Ogg 音频", "音频", "audio/ogg", 2048, BUILDER_BYTES, "4f676753"),
    ("opus", "Opus 音频", "音频", "audio/opus", 2048, BUILDER_BYTES, "4f676753"),
    ("aac", "AAC 音频", "音频", "audio/aac", 1024, BUILDER_BYTES, "fff1"),
    ("m4a", "M4A 音频", "音频", "audio/mp4", 2048, BUILDER_BYTES, "4d344120"),
    ("aiff", "AIFF 音频", "音频", "audio/aiff", 2048, BUILDER_BYTES, "464f524d"),
    ("aif", "AIFF 音频", "音频", "audio/aiff", 2048, BUILDER_BYTES, "464f524d"),
    ("wma", "Windows 媒体音频", "音频", "audio/x-ms-wma", 2048, BUILDER_BYTES, "3026b2758e66cf11"),
    ("ape", "Monkey's Audio", "音频", OCTET, 2048, BUILDER_BYTES, "4d414320"),
    ("mid", "MIDI 音乐", "音频", "audio/midi", 512, BUILDER_BYTES, "4d546864"),
    ("midi", "MIDI 音乐", "音频", "audio/midi", 512, BUILDER_BYTES, "4d546864"),
    ("amr", "AMR 语音", "音频", "audio/amr", 1024, BUILDER_BYTES, "2321414d52"),
    ("mka", "Matroska 音频", "音频", "audio/x-matroska", 2048, BUILDER_BYTES, "1a45dfa3"),
    ("m4b", "有声书", "音频", "audio/mp4", 2048, BUILDER_BYTES, "4d344220"),
    ("aax", "Audible 有声书", "音频", OCTET, 2048, BUILDER_BYTES, ""),
    ("dsf", "DSD 音频", "音频", OCTET, 2048, BUILDER_BYTES, "44534446"),
    ("ra", "RealAudio", "音频", "audio/x-realaudio", 1024, BUILDER_BYTES, "2e524143"),
    ("voc", "Creative 音频", "音频", OCTET, 1024, BUILDER_BYTES, ""),
    ("au", "Sun 音频", "音频", "audio/basic", 1024, BUILDER_BYTES, "2e736e64"),
    # ---------------- 视频 ----------------
    ("mp4", "MP4 视频", "视频", "video/mp4", 4096, BUILDER_BYTES, "66747970"),
    ("m4v", "iTunes 视频", "视频", "video/x-m4v", 4096, BUILDER_BYTES, "66747970"),
    ("mov", "QuickTime 视频", "视频", "video/quicktime", 4096, BUILDER_BYTES, "66747970"),
    ("avi", "AVI 视频", "视频", "video/x-msvideo", 4096, BUILDER_BYTES, "52494646"),
    ("mkv", "Matroska 视频", "视频", "video/x-matroska", 4096, BUILDER_BYTES, "1a45dfa3"),
    ("webm", "WebM 视频", "视频", "video/webm", 4096, BUILDER_BYTES, "1a45dfa3"),
    ("flv", "Flash 视频", "视频", "video/x-flv", 4096, BUILDER_BYTES, "464c5601"),
    ("f4v", "Flash 视频（新）", "视频", "video/x-f4v", 4096, BUILDER_BYTES, ""),
    ("wmv", "Windows 媒体视频", "视频", "video/x-ms-wmv", 4096, BUILDER_BYTES, "3026b2758e66cf11"),
    ("asf", "高级流格式", "视频", "video/x-ms-asf", 4096, BUILDER_BYTES, "3026b2758e66cf11"),
    ("mpg", "MPEG 视频", "视频", "video/mpeg", 4096, BUILDER_BYTES, "000001ba"),
    ("mpeg", "MPEG 视频", "视频", "video/mpeg", 4096, BUILDER_BYTES, "000001ba"),
    ("mpe", "MPEG 视频", "视频", "video/mpeg", 4096, BUILDER_BYTES, "000001ba"),
    ("m2ts", "蓝光流", "视频", "video/mp2t", 4096, BUILDER_BYTES, "47"),
    ("mts", "AVCHD 视频", "视频", "video/mp2t", 4096, BUILDER_BYTES, "47"),
    ("vob", "DVD 视频对象", "视频", "video/mpeg", 4096, BUILDER_BYTES, "000001ba"),
    ("3gp", "3GPP 手机视频", "视频", "video/3gpp", 4096, BUILDER_BYTES, "66747970"),
    ("3g2", "3GPP2 手机视频", "视频", "video/3gpp2", 4096, BUILDER_BYTES, "66747970"),
    ("rm", "RealMedia", "视频", "application/vnd.rn-realmedia", 4096, BUILDER_BYTES, "2e524d46"),
    ("rmvb", "RealMedia 可变码率", "视频", "application/vnd.rn-realmedia-vbr", 4096, BUILDER_BYTES, "2e524d46"),
    ("ogv", "Ogg 视频", "视频", "video/ogg", 4096, BUILDER_BYTES, "4f676753"),
    ("divx", "DivX 视频", "视频", "video/divx", 4096, BUILDER_BYTES, ""),
    ("mxf", "素材交换格式", "视频", "application/mxf", 4096, BUILDER_BYTES, "060e2b34"),
    ("m3u8", "HLS 播放清单", "视频", "application/x-mpegURL", 300, BUILDER_TEXT, ""),
    ("m3u", "播放清单", "视频", "audio/x-mpegurl", 300, BUILDER_TEXT, ""),
    ("pls", "广播播放清单", "视频", "audio/x-scpls", 300, BUILDER_INI, ""),
    ("srt", "SRT 字幕", "视频", "application/x-subrip", 300, BUILDER_TEXT, ""),
    ("vtt", "WebVTT 字幕", "视频", "text/vtt", 300, BUILDER_TEXT, ""),
    ("ass", "SSA 字幕", "视频", "text/plain", 300, BUILDER_INI, ""),
    ("smi", "SMIL 字幕", "视频", "application/smil", 300, BUILDER_XML, ""),
    # ---------------- 压缩包 ----------------
    ("zip", "ZIP 压缩包", "压缩包", "application/zip", 400, BUILDER_ZIP, "504b0304"),
    ("zipx", "ZIP 扩展包", "压缩包", OCTET, 4096, BUILDER_ZIP, "504b0304"),
    ("rar", "RAR 压缩包", "压缩包", "application/vnd.rar", 2048, BUILDER_BYTES, "526172211a070100"),
    ("7z", "7-Zip 压缩包", "压缩包", "application/x-7z-compressed", 2048, BUILDER_BYTES, "377abcaf271c"),
    ("tar", "TAR 归档", "压缩包", "application/x-tar", 10240, BUILDER_TAR, ""),
    ("gz", "GZip 压缩包", "压缩包", "application/gzip", 200, BUILDER_GZ, "1f8b"),
    ("tgz", "TAR.GZ 压缩包", "压缩包", "application/gzip", 200, BUILDER_GZ, "1f8b"),
    ("bz2", "BZip2 压缩包", "压缩包", "application/x-bzip2", 2048, BUILDER_BYTES, "425a68"),
    ("tbz2", "TAR.BZ2 压缩包", "压缩包", "application/x-bzip2", 2048, BUILDER_BYTES, "425a68"),
    ("xz", "XZ 压缩包", "压缩包", "application/x-xz", 2048, BUILDER_BYTES, "fd377a585a00"),
    ("lz4", "LZ4 压缩包", "压缩包", "application/x-lz4", 1024, BUILDER_BYTES, "04224d18"),
    ("zst", "Zstandard 包", "压缩包", "application/zstd", 1024, BUILDER_BYTES, "28b52ffd"),
    ("lz", "LZip 压缩包", "压缩包", OCTET, 2048, BUILDER_BYTES, "4c5a4950"),
    ("lzh", "LHa 压缩包", "压缩包", OCTET, 2048, BUILDER_BYTES, ""),
    ("cab", "Windows 压缩包", "压缩包", "application/vnd.ms-cab-compressed", 2048, BUILDER_BYTES, "4d534346"),
    ("pak", "游戏资源包", "压缩包", OCTET, 4096, BUILDER_ZIP, ""),
    ("jar", "Java 归档", "压缩包", "application/java-archive", 4096, BUILDER_ZIP, ""),
    ("war", "Web 应用归档", "压缩包", "application/java-archive", 4096, BUILDER_ZIP, ""),
    ("ear", "Java 企业归档", "压缩包", "application/java-archive", 4096, BUILDER_ZIP, ""),
    ("apk", "Android 安装包", "压缩包", "application/vnd.android.package-archive", 8192, BUILDER_ZIP, ""),
    ("ipa", "iOS 安装包", "压缩包", "application/ipa", 8192, BUILDER_ZIP, ""),
    ("deb", "Debian 包", "压缩包", "application/vnd.debian.binary-package", 4096, BUILDER_BYTES, "213c617263683e"),
    ("rpm", "RedHat 包", "压缩包", "application/x-rpm", 4096, BUILDER_BYTES, "edabeedb"),
    ("dmg", "macOS 磁盘镜像", "压缩包", "application/x-apple-diskimage", 8192, BUILDER_BYTES, ""),
    ("iso", "光盘镜像", "压缩包", "application/x-iso9660-image", 20480, BUILDER_BYTES, ""),
    ("img", "磁盘镜像", "压缩包", OCTET, 20480, BUILDER_BYTES, ""),
    ("cbr", "压缩漫画包", "压缩包", OCTET, 4096, BUILDER_BYTES, "526172211a0700"),
    ("cbz", "压缩漫画包（ZIP）", "压缩包", OCTET, 4096, BUILDER_ZIP, ""),
    ("usdz", "通用场景描述", "压缩包", OCTET, 4096, BUILDER_ZIP, ""),
    # ---------------- 数据与配置 ----------------
    ("json", "JSON 数据", "数据文件", "application/json", 300, BUILDER_JSON, ""),
    ("jsonl", "JSON Lines", "数据文件", "application/x-ndjson", 300, BUILDER_JSON, ""),
    ("ndjson", "NDJSON 数据", "数据文件", "application/x-ndjson", 300, BUILDER_JSON, ""),
    ("json5", "JSON5 数据", "数据文件", "application/json5", 300, BUILDER_JSON, ""),
    ("xml", "XML 数据", "数据文件", "application/xml", 300, BUILDER_XML, ""),
    ("yaml", "YAML 配置", "数据文件", "application/yaml", 300, BUILDER_YAML, ""),
    ("yml", "YAML 配置", "数据文件", "application/yaml", 300, BUILDER_YAML, ""),
    ("toml", "TOML 配置", "数据文件", "application/toml", 300, BUILDER_TOML, ""),
    ("ini", "INI 配置", "数据文件", "text/plain", 300, BUILDER_INI, ""),
    ("cfg", "配置文件", "数据文件", "text/plain", 300, BUILDER_INI, ""),
    ("conf", "配置文件", "数据文件", "text/plain", 300, BUILDER_INI, ""),
    ("properties", "Java 属性", "数据文件", "text/plain", 300, BUILDER_INI, ""),
    ("env", "环境变量文件", "数据文件", "text/plain", 300, BUILDER_INI, ""),
    ("plist", "Apple 属性表", "数据文件", "application/xml", 300, BUILDER_XML, ""),
    ("edn", "EDN 数据", "数据文件", "application/edn", 300, BUILDER_TEXT, ""),
    ("proto", "Protobuf 定义", "数据文件", "text/plain", 300, BUILDER_CODE, ""),
    ("pb", "Protocol Buffer", "数据文件", OCTET, 1024, BUILDER_BYTES, ""),
    ("thrift", "Thrift 定义", "数据文件", "text/plain", 300, BUILDER_CODE, ""),
    ("avsc", "Avro Schema", "数据文件", "application/json", 300, BUILDER_JSON, ""),
    ("xsd", "XML Schema", "数据文件", "application/xml", 300, BUILDER_XML, ""),
    ("dtd", "XML 文档类型", "数据文件", "application/xml-dtd", 300, BUILDER_XML, ""),
    ("wsdl", "WSDL 服务描述", "数据文件", "application/wsdl+xml", 400, BUILDER_XML, ""),
    ("gpx", "GPS 轨迹", "数据文件", "application/gpx+xml", 400, BUILDER_XML, ""),
    ("kml", "Keyhole 标记", "数据文件", "application/vnd.google-earth.kml+xml", 400, BUILDER_XML, ""),
    ("kmz", "压缩地图", "数据文件", "application/vnd.google-earth.kmz", 4096, BUILDER_ZIP, ""),
    ("geojson", "GeoJSON 地理数据", "数据文件", "application/geo+json", 300, BUILDER_JSON, ""),
    ("topojson", "TopoJSON 地理数据", "数据文件", "application/topojson", 300, BUILDER_JSON, ""),
    ("tcx", "Garmin 轨迹", "数据文件", "application/xml", 400, BUILDER_XML, ""),
    ("fit", "Garmin FIT", "数据文件", OCTET, 1024, BUILDER_BYTES, ""),
    ("har", "HTTP 归档", "数据文件", "application/json", 300, BUILDER_JSON, ""),
    ("pcap", "抓包数据", "数据文件", "application/vnd.tcpdump.pcap", 4096, BUILDER_BYTES, "d4c3b2a1"),
    ("pcapng", "抓包数据（新版）", "数据文件", "application/vnd.tcpdump.pcapng", 4096, BUILDER_BYTES, "0a0d0d0a"),
    ("db", "SQLite 数据库", "数据文件", "application/x-sqlite3", 12288, BUILDER_SQLITE, ""),
    ("sqlite", "SQLite 数据库", "数据文件", "application/x-sqlite3", 12288, BUILDER_SQLITE, ""),
    ("sqlite3", "SQLite 数据库", "数据文件", "application/x-sqlite3", 12288, BUILDER_SQLITE, ""),
    ("mdb", "Access 数据库", "数据文件", OCTET, 4096, BUILDER_BYTES, "d0cf11e0a1b11ae1"),
    ("accdb", "Access 数据库", "数据文件", OCTET, 4096, BUILDER_BYTES, "d0cf11e0a1b11ae1"),
    ("sql", "SQL 脚本", "数据文件", "application/sql", 400, BUILDER_SQL, ""),
    ("ddl", "SQL 建表脚本", "数据文件", "application/sql", 400, BUILDER_SQL, ""),
    ("bak", "备份文件", "数据文件", OCTET, 1024, BUILDER_BYTES, ""),
    ("dat", "数据文件", "数据文件", OCTET, 1024, BUILDER_BYTES, ""),
    ("bin", "二进制文件", "数据文件", OCTET, 1024, BUILDER_BYTES, ""),
    ("npy", "NumPy 数组", "数据文件", OCTET, 2048, BUILDER_BYTES, "934e554d50590100"),
    ("npz", "NumPy 归档", "数据文件", OCTET, 4096, BUILDER_ZIP, ""),
    ("h5", "HDF5 数据", "数据文件", "application/x-hdf5", 4096, BUILDER_BYTES, "894844460d0a1a0a"),
    ("hdf5", "HDF5 数据", "数据文件", "application/x-hdf5", 4096, BUILDER_BYTES, "894844460d0a1a0a"),
    ("nc", "NetCDF 数据", "数据文件", "application/x-netcdf", 2048, BUILDER_BYTES, "434446"),
    ("mat", "MATLAB 数据", "数据文件", OCTET, 2048, BUILDER_BYTES, "4d415446494c"),
    ("ipynb", "Jupyter 笔记本", "数据文件", "application/x-ipynb+json", 400, BUILDER_JSON, ""),
    # ---------------- 日志文本 ----------------
    ("log", "运行日志", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("trace", "跟踪日志", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("out", "标准输出", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("err", "错误输出", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("diff", "差异补丁", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("patch", "补丁文件", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("reg", "Windows 注册表", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("inf", "安装信息", "日志文本", "text/plain", 400, BUILDER_INI, ""),
    ("eml", "电子邮件", "日志文本", "message/rfc822", 400, BUILDER_TEXT, ""),
    ("mbox", "邮件邮箱", "日志文本", "application/mbox", 400, BUILDER_TEXT, ""),
    ("vcf", "联系人卡片", "日志文本", "text/vcard", 300, BUILDER_TEXT, ""),
    ("ics", "日历事件", "日志文本", "text/calendar", 300, BUILDER_TEXT, ""),
    ("ifb", "日历时区", "日志文本", "text/calendar", 300, BUILDER_TEXT, ""),
    ("xbrl", "财务报告", "日志文本", "text/xml", 400, BUILDER_XML, ""),
    ("edifact", "EDI 报文", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    ("x12", "EDI 报文", "日志文本", "text/plain", 400, BUILDER_TEXT, ""),
    # ---------------- 网页资源 ----------------
    ("html", "HTML 页面", "网页资源", "text/html", 500, BUILDER_HTML, ""),
    ("htm", "HTML 页面", "网页资源", "text/html", 500, BUILDER_HTML, ""),
    ("xhtml", "XHTML 页面", "网页资源", "application/xhtml+xml", 500, BUILDER_HTML, ""),
    ("hbs", "Handlebars 模板", "网页资源", "text/html", 500, BUILDER_HTML, ""),
    ("mustache", "Mustache 模板", "网页资源", "text/html", 500, BUILDER_HTML, ""),
    ("jsp", "Java Server Page", "网页资源", "text/html", 500, BUILDER_HTML, ""),
    ("asp", "Active Server Page", "网页资源", "text/html", 500, BUILDER_HTML, ""),
    ("aspx", "ASP.NET 页面", "网页资源", "text/html", 500, BUILDER_HTML, ""),
    ("php", "PHP 页面", "网页资源", "text/x-php", 400, BUILDER_CODE, ""),
    ("css", "CSS 样式", "网页资源", "text/css", 400, BUILDER_CSS, ""),
    ("scss", "SCSS 样式", "网页资源", "text/x-scss", 400, BUILDER_CSS, ""),
    ("sass", "Sass 样式", "网页资源", "text/x-sass", 400, BUILDER_CSS, ""),
    ("less", "Less 样式", "网页资源", "text/x-less", 400, BUILDER_CSS, ""),
    ("styl", "Stylus 样式", "网页资源", "text/plain", 400, BUILDER_CSS, ""),
    ("js", "JavaScript 脚本", "网页资源", "text/javascript", 400, BUILDER_JS, ""),
    ("mjs", "ES Module 脚本", "网页资源", "text/javascript", 400, BUILDER_JS, ""),
    ("cjs", "CommonJS 脚本", "网页资源", "text/javascript", 400, BUILDER_JS, ""),
    ("jsx", "JSX 组件", "网页资源", "text/javascript", 400, BUILDER_JS, ""),
    ("vue", "Vue 组件", "网页资源", "text/plain", 500, BUILDER_HTML, ""),
    ("wasm", "WebAssembly", "网页资源", "application/wasm", 1024, BUILDER_BYTES, "0061736d"),
    ("sitemap", "站点地图", "网页资源", "application/xml", 400, BUILDER_XML, ""),
    ("rss", "RSS 订阅", "网页资源", "application/rss+xml", 400, BUILDER_XML, ""),
    ("atom", "Atom 订阅", "网页资源", "application/atom+xml", 400, BUILDER_XML, ""),
    # ---------------- 代码脚本 ----------------
    ("py", "Python 脚本", "代码脚本", "text/x-python", 400, BUILDER_PY, ""),
    ("pyc", "Python 字节码", "代码脚本", OCTET, 1024, BUILDER_BYTES, "0a0d0d0a"),
    ("pyw", "Python 窗口脚本", "代码脚本", "text/x-python", 400, BUILDER_PY, ""),
    ("java", "Java 源码", "代码脚本", "text/x-java", 400, BUILDER_CODE, ""),
    ("kt", "Kotlin 源码", "代码脚本", "text/x-kotlin", 400, BUILDER_CODE, ""),
    ("kts", "Kotlin 脚本", "代码脚本", "text/x-kotlin", 400, BUILDER_CODE, ""),
    ("scala", "Scala 源码", "代码脚本", "text/x-scala", 400, BUILDER_CODE, ""),
    ("groovy", "Groovy 源码", "代码脚本", "text/x-groovy", 400, BUILDER_CODE, ""),
    ("c", "C 源码", "代码脚本", "text/x-c", 400, BUILDER_CODE, ""),
    ("h", "C 头文件", "代码脚本", "text/x-c", 400, BUILDER_CODE, ""),
    ("cpp", "C++ 源码", "代码脚本", "text/x-c++src", 400, BUILDER_CODE, ""),
    ("hpp", "C++ 头文件", "代码脚本", "text/x-c++hdr", 400, BUILDER_CODE, ""),
    ("cs", "C# 源码", "代码脚本", "text/x-csharp", 400, BUILDER_CODE, ""),
    ("go", "Go 源码", "代码脚本", "text/x-go", 400, BUILDER_CODE, ""),
    ("rs", "Rust 源码", "代码脚本", "text/x-rust", 400, BUILDER_CODE, ""),
    ("rb", "Ruby 源码", "代码脚本", "text/x-ruby", 400, BUILDER_CODE, ""),
    ("swift", "Swift 源码", "代码脚本", "text/x-swift", 400, BUILDER_CODE, ""),
    ("dart", "Dart 源码", "代码脚本", "text/x-dart", 400, BUILDER_CODE, ""),
    ("m", "Objective-C 源码", "代码脚本", "text/x-objectivec", 400, BUILDER_CODE, ""),
    ("pl", "Perl 脚本", "代码脚本", "text/x-perl", 400, BUILDER_CODE, ""),
    ("lua", "Lua 脚本", "代码脚本", "text/x-lua", 400, BUILDER_CODE, ""),
    ("r", "R 脚本", "代码脚本", "text/x-r-source", 400, BUILDER_CODE, ""),
    ("jl", "Julia 脚本", "代码脚本", "text/x-julia", 400, BUILDER_CODE, ""),
    ("sh", "Shell 脚本", "代码脚本", "text/x-sh", 400, BUILDER_CODE, ""),
    ("bash", "Bash 脚本", "代码脚本", "text/x-sh", 400, BUILDER_CODE, ""),
    ("zsh", "Zsh 脚本", "代码脚本", "text/x-sh", 400, BUILDER_CODE, ""),
    ("bat", "Windows 批处理", "代码脚本", "text/plain", 400, BUILDER_TEXT, ""),
    ("cmd", "Windows 命令脚本", "代码脚本", "text/plain", 400, BUILDER_TEXT, ""),
    ("ps1", "PowerShell 脚本", "代码脚本", "text/plain", 400, BUILDER_CODE, ""),
    ("vbs", "VBScript 脚本", "代码脚本", "text/vbscript", 400, BUILDER_CODE, ""),
    ("clj", "Clojure 源码", "代码脚本", "text/x-clojure", 400, BUILDER_CODE, ""),
    ("hs", "Haskell 源码", "代码脚本", "text/x-haskell", 400, BUILDER_CODE, ""),
    ("erl", "Erlang 源码", "代码脚本", "text/x-erlang", 400, BUILDER_CODE, ""),
    ("nim", "Nim 源码", "代码脚本", "text/x-nim", 400, BUILDER_CODE, ""),
    ("tf", "Terraform 配置", "代码脚本", "text/plain", 400, BUILDER_CODE, ""),
    ("dockerfile", "Dockerfile 镜像定义", "代码脚本", "text/plain", 400, BUILDER_TEXT, ""),
    ("gradle", "Gradle 构建", "代码脚本", "text/plain", 400, BUILDER_CODE, ""),
    ("ts", "TypeScript 源码", "代码脚本", "text/plain", 400, BUILDER_CODE, ""),
    ("tsx", "TSX 组件", "代码脚本", "text/plain", 400, BUILDER_CODE, ""),
    ("class", "Java 字节码", "代码脚本", OCTET, 1024, BUILDER_BYTES, "cafebabe"),
    ("dex", "Android 字节码", "代码脚本", OCTET, 4096, BUILDER_BYTES, "6465780a"),
    ("aar", "Android 库归档", "代码脚本", OCTET, 4096, BUILDER_ZIP, ""),
    ("so", "Linux 共享库", "代码脚本", "application/x-sharedlib", 4096, BUILDER_BYTES, "7f454c46"),
    ("dll", "Windows 动态库", "代码脚本", OCTET, 4096, BUILDER_BYTES, "4d5a"),
    ("ocx", "Windows 控件", "代码脚本", OCTET, 4096, BUILDER_BYTES, "4d5a"),
    ("exe", "Windows 可执行文件", "代码脚本", "application/x-msdownload", 4096, BUILDER_BYTES, "4d5a"),
    ("msi", "Windows 安装包", "代码脚本", OCTET, 8192, BUILDER_BYTES, "d0cf11e0a1b11ae1"),
    ("elf", "Linux 可执行文件", "代码脚本", "application/x-executable", 4096, BUILDER_BYTES, "7f454c46"),
    ("dylib", "macOS 动态库", "代码脚本", OCTET, 4096, BUILDER_BYTES, "cffaedfe"),
    ("pkg", "安装包", "代码脚本", OCTET, 8192, BUILDER_BYTES, ""),
    # ---------------- 字体 ----------------
    ("ttf", "TrueType 字体", "字体", "font/ttf", 4096, BUILDER_BYTES, "00010000"),
    ("otf", "OpenType 字体", "字体", "font/otf", 4096, BUILDER_BYTES, "4f54544f"),
    ("woff", "Web 字体", "字体", "font/woff", 2048, BUILDER_BYTES, "774f4646"),
    ("woff2", "Web 字体（新）", "字体", "font/woff2", 2048, BUILDER_BYTES, "774f4632"),
    ("eot", "Embedded 字体", "字体", "application/vnd.ms-fontobject", 2048, BUILDER_BYTES, ""),
    ("fon", "Windows 字体", "字体", OCTET, 2048, BUILDER_BYTES, ""),
    ("fnt", "位图字体", "字体", OCTET, 1024, BUILDER_BYTES, ""),
    # ---------------- 证书与密钥 ----------------
    ("pem", "PEM 证书或私钥", "证书密钥", "application/x-pem-file", 400, BUILDER_TEXT, ""),
    ("crt", "X.509 证书", "证书密钥", "application/x-x509-ca-cert", 1024, BUILDER_BYTES, "308204"),
    ("cer", "证书", "证书密钥", "application/x-x509-ca-cert", 1024, BUILDER_BYTES, "308204"),
    ("der", "DER 编码证书", "证书密钥", "application/x-x509-ca-cert", 1024, BUILDER_BYTES, "308204"),
    ("pub", "公钥文件", "证书密钥", "text/plain", 300, BUILDER_TEXT, ""),
    ("p12", "PKCS#12 证书包", "证书密钥", "application/x-pkcs12", 2048, BUILDER_BYTES, "308204"),
    ("pfx", "PKCS#12 证书包（Windows）", "证书密钥", "application/x-pkcs12", 2048, BUILDER_BYTES, "308204"),
    ("jks", "Java 密钥库", "证书密钥", OCTET, 2048, BUILDER_BYTES, "feedfeed"),
    ("keystore", "密钥库", "证书密钥", OCTET, 2048, BUILDER_BYTES, ""),
    ("gpg", "GPG 密钥环", "证书密钥", OCTET, 1024, BUILDER_BYTES, ""),
    ("asc", "ASCII 装甲密钥", "证书密钥", "text/plain", 400, BUILDER_TEXT, ""),
    ("sig", "签名文件", "证书密钥", OCTET, 512, BUILDER_BYTES, ""),
    # ---------------- 3D 与模型 ----------------
    ("obj", "Wavefront 模型", "3D模型", "text/plain", 400, BUILDER_TEXT, ""),
    ("stl", "STL 网格", "3D模型", "text/plain", 840, BUILDER_TEXT, ""),
    ("glb", "glTF 二进制", "3D模型", "model/gltf-binary", 1024, BUILDER_BYTES, "676c7446"),
    ("gltf", "glTF 模型", "3D模型", "model/gltf+json", 400, BUILDER_JSON, ""),
    ("fbx", "FBX 模型", "3D模型", OCTET, 4096, BUILDER_BYTES, "4b617964617261"),
    ("dae", "Collada 模型", "3D模型", "application/xml", 400, BUILDER_XML, ""),
    ("ply", "PLY 点云", "3D模型", "text/plain", 400, BUILDER_TEXT, ""),
    ("3mf", "3D 制造格式", "3D模型", "model/3mf", 4096, BUILDER_ZIP, ""),
    ("gcode", "3D 打印指令", "3D模型", "text/plain", 400, BUILDER_TEXT, ""),
    ("step", "STEP 模型", "3D模型", "model/step", 400, BUILDER_TEXT, ""),
    ("stp", "STEP 模型", "3D模型", "model/step", 400, BUILDER_TEXT, ""),
    ("iges", "IGES 模型", "3D模型", "model/iges", 400, BUILDER_TEXT, ""),
    ("igs", "IGES 模型", "3D模型", "model/iges", 400, BUILDER_TEXT, ""),
    ("pcd", "点云数据", "3D模型", "text/plain", 400, BUILDER_TEXT, ""),
    ("las", "LiDAR 点云", "3D模型", OCTET, 4096, BUILDER_BYTES, ""),
    # ---------------- 地理与科学 ----------------
    ("shp", "Shapefile 几何", "地理科学", OCTET, 1024, BUILDER_BYTES, "0000270a"),
    ("shx", "Shapefile 索引", "地理科学", OCTET, 1024, BUILDER_BYTES, "0000270a"),
    ("dbf", "dBASE 表", "地理科学", OCTET, 1024, BUILDER_BYTES, ""),
    ("prj", "投影定义", "地理科学", "text/plain", 300, BUILDER_TEXT, ""),
    ("hdf", "HDF 数据", "地理科学", OCTET, 4096, BUILDER_BYTES, "0e031301"),
    ("cdf", "NetCDF 数据", "地理科学", "application/x-netcdf", 2048, BUILDER_BYTES, "434446"),
    ("fits", "FITS 天文数据", "地理科学", "application/fits", 2880, BUILDER_TEXT, ""),
    ("dcm", "DICOM 医学影像", "地理科学", "application/dicom", 4096, BUILDER_BYTES, ""),
    ("sas7bdat", "SAS 数据集", "地理科学", OCTET, 4096, BUILDER_BYTES, ""),
    ("spss", "统计数据集", "地理科学", OCTET, 4096, BUILDER_BYTES, ""),
    ("mnc", "MINC 影像", "地理科学", OCTET, 4096, BUILDER_BYTES, ""),
    ("rec", "二进制记录", "地理科学", OCTET, 2048, BUILDER_BYTES, ""),
    # ---------------- 虚拟化与镜像 ----------------
    ("vmdk", "VMware 磁盘", "虚机镜像", "application/x-vmdk", 8192, BUILDER_TEXT, ""),
    ("vdi", "VirtualBox 磁盘", "虚机镜像", OCTET, 8192, BUILDER_BYTES, "beda6f62"),
    ("vhd", "Hyper-V 磁盘", "虚机镜像", OCTET, 16384, BUILDER_BYTES, ""),
    ("vhdx", "Hyper-V 磁盘（新）", "虚机镜像", OCTET, 16384, BUILDER_BYTES, "6576696378686476"),
    ("qcow2", "QEMU 磁盘", "虚机镜像", OCTET, 8192, BUILDER_BYTES, "514649fb"),
    ("ova", "虚机导出包", "虚机镜像", OCTET, 10240, BUILDER_TAR, ""),
    ("ovf", "虚机描述", "虚机镜像", "application/xml", 400, BUILDER_XML, ""),
    ("vbox", "VirtualBox 配置", "虚机镜像", "text/plain", 400, BUILDER_TEXT, ""),
    # ---------------- 其他 ----------------
    ("torrent", "BT 种子", "其他", "application/x-bittorrent", 2048, BUILDER_BYTES, "6438616e6e"),
    ("desktop", "Linux 快捷方式", "其他", "text/plain", 300, BUILDER_INI, ""),
    ("url", "网页快捷方式", "其他", "text/plain", 300, BUILDER_INI, ""),
    ("lnk", "Windows 快捷方式", "其他", OCTET, 1024, BUILDER_BYTES, "4c000000"),
    ("tmp", "临时文件", "其他", OCTET, 512, BUILDER_BYTES, ""),
    ("swp", "编辑器交换文件", "其他", OCTET, 512, BUILDER_BYTES, ""),
    ("lock", "锁文件", "其他", "text/plain", 200, BUILDER_TEXT, ""),
    ("pid", "进程号文件", "其他", "text/plain", 200, BUILDER_TEXT, ""),
    ("license", "许可证文件", "其他", "text/plain", 300, BUILDER_TEXT, ""),
    ("manifest", "清单文件", "其他", "application/xml", 400, BUILDER_XML, ""),
    ("webmanifest", "Web 应用清单", "其他", "application/manifest+json", 300, BUILDER_JSON, ""),
    ("crx", "Chrome 扩展", "其他", OCTET, 4096, BUILDER_BYTES, "43723234"),
    ("xpi", "Firefox 扩展", "其他", OCTET, 4096, BUILDER_ZIP, ""),
    ("vsix", "VS Code 扩展", "其他", OCTET, 4096, BUILDER_ZIP, ""),
    ("nupkg", "NuGet 包", "其他", OCTET, 4096, BUILDER_ZIP, ""),
    ("gem", "RubyGem 包", "其他", OCTET, 4096, BUILDER_TAR, ""),
    ("whl", "Python Wheel", "其他", OCTET, 4096, BUILDER_ZIP, ""),
    ("egg", "Python Egg", "其他", OCTET, 4096, BUILDER_ZIP, ""),
)


@dataclass(frozen=True, slots=True)
class FileFormat:
    """一种可生成的文件格式。"""

    ext: str
    label: str
    category: str
    media_type: str
    min_bytes: int
    builder: str
    signature: bytes

    @property
    def mode(self) -> str:
        """结构构造 / 签名填充 / 规格填充。"""
        if self.builder.split(":")[0] in STRUCTURED_BUILDERS:
            return MODE_STRUCTURED
        return MODE_SIGNATURE if self.signature else MODE_FILLER

    @property
    def mode_label(self) -> str:
        return MODE_LABELS[self.mode]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ext": self.ext,
            "label": self.label,
            "category": self.category,
            "mediaType": self.media_type,
            "minBytes": self.min_bytes,
            "builder": self.builder,
            "mode": self.mode,
            "modeLabel": self.mode_label,
            "name": f".{self.ext}｜{self.label}",
        }


def _build_rows() -> tuple[FileFormat, ...]:
    formats: list[FileFormat] = []
    seen: set[str] = set()
    for ext, label, category, media_type, min_bytes, builder, signature in _ROWS:
        key = ext.lower().lstrip(".")
        # 同一扩展名出现两次会让后一条悄悄盖掉前一条，生成结果与用户选中的格式不一致
        if key in seen:
            raise ValueError(f"文件格式目录扩展名重复：.{key}")
        seen.add(key)
        formats.append(FileFormat(
            ext=key,
            label=label,
            category=category,
            media_type=media_type,
            min_bytes=int(min_bytes),
            builder=builder,
            signature=bytes.fromhex(signature) if signature else b"",
        ))
    return tuple(formats)


FORMATS: tuple[FileFormat, ...] = _build_rows()
FORMAT_BY_EXT: dict[str, FileFormat] = {item.ext: item for item in FORMATS}
#: 分类按 _ROWS 里首次出现的顺序，前端下拉按这个顺序分组
CATEGORIES: tuple[str, ...] = tuple(dict.fromkeys(item.category for item in FORMATS))
#: 每种构造方式有多少格式，前端用来标注能力边界
MODE_COUNTS: dict[str, int] = {mode: 0 for mode in MODE_LABELS}
for _item in FORMATS:
    MODE_COUNTS[_item.mode] = MODE_COUNTS.get(_item.mode, 0) + 1


def resolve_format(raw_ext: str) -> FileFormat:
    """按扩展名取格式；接受 .jpg / JPG / jpg 图片 三种写法。"""
    key = str(raw_ext or "").strip().lower().lstrip(".")
    if not key:
        raise KeyError("")
    hit = FORMAT_BY_EXT.get(key)
    if hit is None:
        raise KeyError(key)
    return hit


def formats_payload() -> dict[str, Any]:
    """给前端一次性用的目录：格式表 + 分组顺序 + 各构造方式的数量。"""
    return {
        "total": len(FORMATS),
        "formats": [item.as_dict() for item in FORMATS],
        "categories": [
            {
                "name": name,
                "count": sum(1 for item in FORMATS if item.category == name),
            }
            for name in CATEGORIES
        ],
        "modes": [
            {"mode": mode, "label": MODE_LABELS[mode], "count": MODE_COUNTS.get(mode, 0)}
            for mode in (MODE_STRUCTURED, MODE_SIGNATURE, MODE_FILLER)
        ],
    }
