"""测试数据生成（文件分支）验收脚本。

全程本地、不碰大模型，验证：
- 格式目录自身完整：扩展名唯一、分类中文、构造方式与构造器对得上
- 每种格式按「最小规格」默认构建一次都不抛异常，且长度自述与实际字节一致
- 任意大小落盘：能精确命中的必须逐字节相等，命不中的要如实标记 exact=False 并给出原因
- 产物可被真实解析器读懂：图像 / zip 系 / tar / gz / wav / sqlite / json / xml / csv 等
- 同一输入两次构建字节完全相同（可复现，不掺时间戳与随机数）
- API 闭环：目录 → 生成 → 列表 → 详情 → 单文件下载 → 整批 zip → 删除
- 命名规则：前缀、后缀、自增编号、时间戳，以及数量 > 1 时防覆盖
- 安全：大小与数量上限、格式白名单、非法文件名、批次目录不越界

    python backend/scripts/check_test_data.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import csv
import gzip
import hashlib
import io
import json
import logging
import pathlib
import shutil
import sqlite3
import sys
import tarfile
import tempfile
import wave
import zipfile
import xml.etree.ElementTree as ET
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services.test_data_builder import (  # noqa: E402
    MAX_BUILD_BYTES,
    BuildError,
    build_file,
)
from aitp.services.test_data_formats import (  # noqa: E402
    CATEGORIES,
    FORMATS,
    FORMAT_BY_EXT,
    MODE_FILLER,
    MODE_SIGNATURE,
    MODE_STRUCTURED,
    resolve_format,
)
from aitp.services.test_data_service import MAX_FILES_PER_BATCH, generated_root  # noqa: E402
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
    _failures.append(name)
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

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None) -> None:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code,
              f"期望 {code}，实际 {body.get('code')} {str(body)[:160]}")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_test_data.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_test_data.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


# ---------------------------------------------------------------- 格式目录

def check_catalog() -> None:
    check("格式目录数量达到几百种", len(FORMATS) >= 300, f"实际 {len(FORMATS)}")
    check("扩展名全部小写无点号",
          all(fmt.ext == fmt.ext.lower().lstrip(".") for fmt in FORMATS))
    check("扩展名唯一", len({fmt.ext for fmt in FORMATS}) == len(FORMATS),
          f"{len({fmt.ext for fmt in FORMATS})} vs {len(FORMATS)}")
    check("格式与索引一一对应",
          all(FORMAT_BY_EXT[fmt.ext] is fmt for fmt in FORMATS))
    check("未知扩展名查询报错", _raises(KeyError, resolve_format, "no-such-ext"))
    check("带点与大写扩展名能归一", resolve_format(".PDF").ext == "pdf")
    check("分类全中文且数量合理",
          8 <= len(CATEGORIES) <= 30
          and all(any("\u4e00" <= ch <= "\u9fff" for ch in name) for name in CATEGORIES),
          str(CATEGORIES)[:200])
    check("每个格式都归入已有分类",
          all(fmt.category in CATEGORIES for fmt in FORMATS),
          str({fmt.category for fmt in FORMATS if fmt.category not in CATEGORIES}))
    check("最小规格都是正整数", all(fmt.min_bytes > 0 for fmt in FORMATS))
    check("MIME 类型带斜杠",
          all("/" in fmt.media_type for fmt in FORMATS))
    check("三种构造方式齐备",
          {fmt.mode for fmt in FORMATS} == {MODE_STRUCTURED, MODE_SIGNATURE, MODE_FILLER})
    check("签名填充都声明文件头",
          all(fmt.signature for fmt in FORMATS if fmt.mode == MODE_SIGNATURE))
    check("规格填充不声明文件头",
          all(not fmt.signature for fmt in FORMATS if fmt.mode == MODE_FILLER))


def _raises(exc: type[BaseException], func: Any, *args: Any) -> bool:
    try:
        func(*args)
    except exc:
        return True
    except BaseException:
        return False
    return False


# ---------------------------------------------------------------- 构建

def check_build_defaults() -> None:
    """每种格式按最小规格构建一次：403 种一种都不能抛。"""
    errors: list[str] = []
    loose: list[str] = []
    for fmt in FORMATS:
        try:
            built = build_file(fmt, None)
        except Exception as exc:  # noqa: BLE001
            errors.append(f".{fmt.ext}: {type(exc).__name__}: {exc}")
            continue
        if len(built.data) != built.actual:
            errors.append(f".{fmt.ext}: 自述 {built.actual} 实长 {len(built.data)}")
        if not built.exact:
            loose.append(f".{fmt.ext}={built.actual}")
    check("全部格式按最小规格构建成功", not errors, str(errors[:6])[:600])
    # 最小规格是"下限"而不是"承诺"：结构下限高于目录值时如实标注，但不允许静默偏差
    check("最小规格默认精确命中", not loose, f"{len(loose)} 项偏离：{str(loose[:8])[:400]}")


def check_size_fidelity() -> None:
    """任意字节诉求：能精确的必须相等，不能精确的必须 exact=False 且给原因。"""
    targets = [1, 7, 256, 512, 513, 1024, 4096, 5000, 65536, 100000]
    problems: list[str] = []
    for ext in ("txt", "log", "json", "xml", "html", "css", "js", "py", "sql", "csv",
                "md", "yaml", "ini", "pdf", "png", "jpg", "bmp", "gif", "zip", "gz",
                "tar", "wav", "db", "docx", "xlsx"):
        fmt = FORMAT_BY_EXT[ext]
        for target in targets:
            try:
                built = build_file(fmt, target)
            except BuildError as exc:
                problems.append(f".{ext}@{target}: {exc}")
                continue
            if len(built.data) != built.actual:
                problems.append(f".{ext}@{target}: 自述 {built.actual} 实长 {len(built.data)}")
            elif built.actual != target and not (built.note and built.exact is False):
                problems.append(f".{ext}@{target}: 偏差 {built.actual} 但未标注原因")
    check("任意大小诉求结果自洽", not problems, str(problems[:6])[:600])

    # 文本与压缩类应当逐字节命中；tar 只能落在 512 的整数倍
    for ext, size in (("txt", 4096), ("json", 5000), ("zip", 1024), ("db", 12288),
                      ("pdf", 20000), ("png", 33333)):
        built = build_file(FORMAT_BY_EXT[ext], size)
        check(f".{ext} 可精确到 {size} 字节", built.exact and built.actual == size,
              f"实际 {built.actual} exact={built.exact} {built.note}")
    tar_built = build_file(FORMAT_BY_EXT["tar"], 5000)
    check(".tar 落不到 5000 时如实标注",
          tar_built.exact is False and bool(tar_built.note) and tar_built.actual % 512 == 0,
          f"实际 {tar_built.actual} note={tar_built.note}")


def check_determinism() -> None:
    same = []
    for ext in ("png", "zip", "tar", "db", "wav", "pdf", "json", "docx", "sql", "jpg"):
        fmt = FORMAT_BY_EXT[ext]
        first = hashlib.sha256(build_file(fmt, None).data).hexdigest()
        second = hashlib.sha256(build_file(fmt, None).data).hexdigest()
        if first != second:
            same.append(ext)
    check("同一格式两次构建字节相同", not same, str(same))


def check_limits() -> None:
    fmt = FORMAT_BY_EXT["txt"]
    check("0 字节被拒绝", _raises(BuildError, build_file, fmt, 0))
    check("负数被拒绝", _raises(BuildError, build_file, fmt, -1))
    check("超过单文件上限被拒绝",
          _raises(BuildError, build_file, fmt, MAX_BUILD_BYTES + 1))
    built = build_file(fmt, MAX_BUILD_BYTES)
    check("单文件上限本身可用", built.actual == MAX_BUILD_BYTES, str(built.actual))


# ---------------------------------------------------------------- 真实解析

def check_parseable() -> None:
    results: list[str] = []

    def expect(name: str, fn: Any) -> None:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            results.append(f"{name}: {type(exc).__name__}: {exc}")

    # 图像：Pillow 能打开并解码
    try:
        from PIL import Image
    except ImportError:
        Image = None  # type: ignore[assignment]
    if Image is not None:
        for ext in ("png", "jpg", "bmp", "gif", "webp", "tiff", "ico", "ppm"):
            def open_image(ext: str = ext) -> None:
                raw = build_file(FORMAT_BY_EXT[ext], None).data
                with Image.open(io.BytesIO(raw)) as img:
                    img.load()
                    assert img.size[0] > 0 and img.size[1] > 0
            expect(f"图像 .{ext}", open_image)

    # 容器类：zip / tar / gz 能被标准库列出
    def open_zip(ext: str) -> None:
        raw = build_file(FORMAT_BY_EXT[ext], None).data
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            assert zf.testzip() is None
            assert zf.namelist()
            zf.read(zf.namelist()[0])

    for ext in ("zip", "docx", "xlsx", "pptx", "epub", "apk", "jar", "odt", "ods"):
        expect(f"压缩包 .{ext}", lambda e=ext: open_zip(e))

    def open_tar() -> None:
        raw = build_file(FORMAT_BY_EXT["tar"], None).data
        with tarfile.open(fileobj=io.BytesIO(raw)) as tf:
            members = tf.getmembers()
            assert members
            handle = tf.extractfile(members[0])
            assert handle is not None and handle.read()

    expect("归档 .tar", open_tar)

    def open_gz() -> None:
        raw = build_file(FORMAT_BY_EXT["gz"], None).data
        with gzip.GzipFile(fileobj=io.BytesIO(raw)) as gf:
            assert gf.read()

    expect("压缩 .gz", open_gz)

    def open_wav() -> None:
        raw = build_file(FORMAT_BY_EXT["wav"], None).data
        with wave.open(io.BytesIO(raw), "rb") as wf:
            assert wf.getnchannels() >= 1 and wf.getframerate() > 0
            assert wf.readframes(wf.getnframes()) is not None

    expect("音频 .wav", open_wav)

    def open_sqlite() -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = pathlib.Path(tmp) / "sample.db"
            path.write_bytes(build_file(FORMAT_BY_EXT["db"], None).data)
            conn = sqlite3.connect(str(path))
            try:
                assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
                assert conn.execute("SELECT name FROM sqlite_master").fetchall()
            finally:
                conn.close()

    expect("数据库 .db", open_sqlite)

    def open_json_doc(ext: str) -> None:
        raw = build_file(FORMAT_BY_EXT[ext], None).data
        assert isinstance(json.loads(raw.decode("utf-8")), dict)

    for ext in ("json", "geojson", "avsc", "har"):
        expect(f"数据 .{ext}", lambda e=ext: open_json_doc(e))

    def open_csv(size: int | None) -> None:
        raw = build_file(FORMAT_BY_EXT["csv"], size).data.decode("utf-8")
        # 填充段是空行，任何解析器都会跳过；有内容的行必须列数一致
        rows = [row for row in csv.reader(io.StringIO(raw)) if row]
        assert rows, "没有数据行"
        assert len({len(row) for row in rows}) == 1, rows[:3]

    for size in (None, 1024, 4096, 100000):
        expect(f"表格 .csv@{size or '最小规格'}", lambda s=size: open_csv(s))

    def open_xml(ext: str) -> None:
        raw = build_file(FORMAT_BY_EXT[ext], None).data
        assert ET.fromstring(raw) is not None

    for ext in ("xml", "svg", "gpx"):
        expect(f"标记 .{ext}", lambda e=ext: open_xml(e))

    def open_html() -> None:
        raw = build_file(FORMAT_BY_EXT["html"], None).data.decode("utf-8")
        assert "<html" in raw and "</html>" in raw

    expect("网页 .html", open_html)

    def open_pdf() -> None:
        raw = build_file(FORMAT_BY_EXT["pdf"], 30000).data
        assert raw.startswith(b"%PDF-") and b"%%EOF" in raw[-1024:]
        assert raw.count(b"obj") >= 6

    expect("文档 .pdf", open_pdf)

    def check_signature(ext: str) -> None:
        fmt = FORMAT_BY_EXT[ext]
        raw = build_file(fmt, None).data
        assert raw.startswith(fmt.signature), f"{raw[:8]!r} != {fmt.signature!r}"

    for ext in ("jpg", "png", "gif", "pdf", "zip", "gz", "wav", "mp3", "mp4",
                "exe", "dll", "class", "woff2", "pcap", "pcapng", "mdb", "doc"):
        fmt = FORMAT_BY_EXT[ext]
        if fmt.signature:
            expect(f"文件头 .{ext}", lambda e=ext: check_signature(e))

    check("产物能被真实解析器读懂", not results, str(results[:8])[:800])


# ---------------------------------------------------------------- API 闭环

def check_api(api: Api, workdir: pathlib.Path) -> None:
    catalog = api.data("GET", "/api/v1/test-data/formats")
    check("格式目录接口返回总数与分组",
          catalog.get("total") == len(FORMATS) and len(catalog.get("categories", [])) == len(CATEGORIES),
          str(catalog)[:200])
    check("格式目录带构造方式统计",
          sum(item["count"] for item in catalog["modes"]) == len(FORMATS))
    check("格式目录字段是前端友好的驼峰",
          {"ext", "label", "name", "category", "mediaType", "minBytes", "mode", "modeLabel"}
          <= set(catalog["formats"][0].keys()),
          str(list(catalog["formats"][0].keys())))

    project_id = int(api.data("POST", "/api/v1/projects",
                              {"code": "TD-MAIN", "name": "测试数据项目"})["id"])

    batch = api.data("POST", "/api/v1/test-data/files", {
        "projectId": project_id,
        "ext": "csv",
        "sizeBytes": 2048,
        "fileCount": 3,
        "namePrefix": "order",
        "nameSuffix": "raw",
        "tokenMode": "sequence",
        "remark": "分页导入用",
    })
    check("生成批次返回批次号", str(batch["batchNo"]).startswith("TD-"), str(batch)[:200])
    check("生成数量与请求一致",
          batch["createdFiles"] == 3 and len(batch["files"]) == 3, str(batch)[:200])
    check("总字节按请求累加", batch["totalBytes"] == 2048 * 3, str(batch["totalBytes"]))
    check("命名规则拼出前缀主体后缀编号",
          [f["fileName"] for f in batch["files"]]
          == [f"order_aitp_raw_{i:02d}.csv" for i in (1, 2, 3)],
          str([f["fileName"] for f in batch["files"]]))
    names = {f["fileName"] for f in batch["files"]}
    check("同批次文件名不重复", len(names) == 3)

    root = generated_root(workdir / "check_test_data.db")
    on_disk = sorted(p.name for p in (root / batch["batchNo"]).iterdir())
    check("文件确实落盘且与明细同名", on_disk == sorted(names), str(on_disk))
    check("批次目录不越界",
          (root / batch["batchNo"]).resolve().is_relative_to(root.resolve()))

    first = batch["files"][0]
    downloaded = api.client.get(f"/api/v1/test-data/files/{first['id']}/download")
    check("单文件下载状态与长度",
          downloaded.status_code == 200 and len(downloaded.get_data()) == first["sizeBytes"],
          f"{downloaded.status_code} {len(downloaded.get_data())}")
    check("下载响应声明附件与 MIME",
          "attachment" in downloaded.headers.get("Content-Disposition", "")
          and "csv" in downloaded.headers.get("Content-Type", ""),
          str(dict(downloaded.headers)))

    detail = api.data("GET", f"/api/v1/test-data/batches/{batch['id']}")
    check("批次详情带明细", len(detail["files"]) == 3 and detail["formatLabel"], str(detail)[:200])
    check("详情标注精确命中", all(f["exact"] for f in detail["files"]),
          str([f["note"] for f in detail["files"]]))

    page = api.data("GET", f"/api/v1/test-data/batches?projectId={project_id}&pageSize=20")
    check("批次列表按项目过滤", page["total"] == 1 and page["list"][0]["id"] == batch["id"],
          str(page)[:200])
    check("列表只带统计不带明细", "files" not in page["list"][0], str(page["list"][0])[:200])
    hit = api.data("GET", f"/api/v1/test-data/batches?keyword={batch['batchNo']}")
    check("批次列表支持关键字", hit["total"] == 1, str(hit)[:200])
    other = api.data("GET", "/api/v1/test-data/batches?projectId=999999")
    check("不存在项目的过滤返回空", other["total"] == 0, str(other)[:200])

    archive = api.client.get(f"/api/v1/test-data/batches/{batch['id']}/archive")
    check("整批打包下载成功", archive.status_code == 200, str(archive.status_code))
    with zipfile.ZipFile(io.BytesIO(archive.get_data())) as zf:
        check("压缩包内含全部文件",
              sorted(zf.namelist()) == sorted(names), str(zf.namelist()))
    check("打包响应回传统计条数",
          archive.headers.get("X-AITP-Export-Count") == "3",
          str(archive.headers.get("X-AITP-Export-Count")))

    stamp_batch = api.data("POST", "/api/v1/test-data/files", {
        "projectId": project_id, "ext": "png", "tokenMode": "timestamp", "fileCount": 1,
    })
    check("时间戳标记生效", "20" in stamp_batch["files"][0]["fileName"],
          stamp_batch["files"][0]["fileName"])
    check("未填大小时按最小规格产出",
          stamp_batch["sizeBytes"] is None
          and stamp_batch["files"][0]["sizeBytes"] >= FORMAT_BY_EXT["png"].min_bytes,
          str(stamp_batch)[:200])

    both = api.data("POST", "/api/v1/test-data/files", {
        "projectId": project_id, "ext": "log", "tokenMode": "timestamp_sequence", "fileCount": 2,
    })
    check("时间戳 + 自增编号可叠加",
          both["files"][0]["fileName"] != both["files"][1]["fileName"]
          and len(both["files"][0]["fileName"]) > len("aitp.log") + 14,
          str([f["fileName"] for f in both["files"]]))

    flat = api.data("POST", "/api/v1/test-data/files", {
        "projectId": project_id, "ext": "txt", "tokenMode": "none", "fileCount": 4,
    })
    check("选择「不带编号」但多文件时自动补序号防覆盖",
          len({f["fileName"] for f in flat["files"]}) == 4,
          str([f["fileName"] for f in flat["files"]]))

    deleted = api.data("DELETE", f"/api/v1/test-data/batches/{batch['id']}")
    check("删除批次返回成功", deleted["deleted"] is True)
    check("删除后批次目录一起清理",
          not (root / batch["batchNo"]).exists(), str(root / batch["batchNo"]))
    api.expect("删除后详情返回 140100", "GET",
               f"/api/v1/test-data/batches/{batch['id']}", ErrorCode.RESOURCE_NOT_FOUND)
    api.expect("删除后下载返回 140100", "GET",
               f"/api/v1/test-data/files/{first['id']}/download", ErrorCode.RESOURCE_NOT_FOUND)
    after = api.data("GET", f"/api/v1/test-data/batches?projectId={project_id}")
    check("删除后列表不再包含该批次",
          all(item["id"] != batch["id"] for item in after["list"]), str(after)[:200])


def check_api_errors(api: Api) -> None:
    project = api.data("POST", "/api/v1/projects", {"code": "TD-ERR", "name": "测试数据异常项目"})
    project_id = int(project["id"])

    def expect_error(name: str, payload: JsonDict, code: int) -> None:
        _, body = api.call("POST", "/api/v1/test-data/files", payload)
        check(name, body.get("code") == code, f"code={body.get('code')} {str(body)[:160]}")

    expect_error("缺项目返回参数错误", {"ext": "txt"}, ErrorCode.PARAM_ERROR)
    expect_error("未知格式返回参数错误",
                 {"projectId": project_id, "ext": "whatever"}, ErrorCode.PARAM_ERROR)
    expect_error("非法前缀返回参数错误",
                 {"projectId": project_id, "ext": "txt", "namePrefix": "../etc"},
                 ErrorCode.PARAM_ERROR)
    expect_error("非法后缀返回参数错误",
                 {"projectId": project_id, "ext": "txt", "nameSuffix": "a/b"},
                 ErrorCode.PARAM_ERROR)
    expect_error("未知标记返回参数错误",
                 {"projectId": project_id, "ext": "txt", "tokenMode": "uuid"},
                 ErrorCode.PARAM_ERROR)
    expect_error("0 大小返回参数错误",
                 {"projectId": project_id, "ext": "txt", "sizeBytes": 0},
                 ErrorCode.PARAM_ERROR)
    expect_error("0 数量返回参数错误",
                 {"projectId": project_id, "ext": "txt", "fileCount": 0},
                 ErrorCode.PARAM_ERROR)
    expect_error("超单文件上限返回参数错误",
                 {"projectId": project_id, "ext": "txt", "sizeBytes": MAX_BUILD_BYTES + 1},
                 ErrorCode.PARAM_ERROR)
    expect_error("超批次数量返回参数错误",
                 {"projectId": project_id, "ext": "txt", "fileCount": MAX_FILES_PER_BATCH + 1},
                 ErrorCode.PARAM_ERROR)
    expect_error("超批次总大小返回参数错误",
                 {"projectId": project_id, "ext": "txt",
                  "sizeBytes": MAX_BUILD_BYTES, "fileCount": MAX_FILES_PER_BATCH},
                 ErrorCode.PARAM_ERROR)
    _, body = api.call("POST", "/api/v1/test-data/files",
                       {"projectId": 987654, "ext": "txt"})
    check("项目不存在返回 140100", body.get("code") == ErrorCode.RESOURCE_NOT_FOUND,
          str(body)[:200])
    _, missing = api.call("DELETE", "/api/v1/test-data/batches/999999")
    check("删除不存在的批次返回 140100",
          missing.get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(missing)[:200])

    _, bad_filter = api.call("GET", "/api/v1/test-data/batches?projectId=abc")
    check("非数字项目筛选返回 100100",
          bad_filter.get("code") == ErrorCode.PARAM_ERROR, str(bad_filter)[:200])

    # 报错路径不能留下半截批次，也不能落盘
    leftover = api.data("GET", f"/api/v1/test-data/batches?projectId={project_id}")
    check("失败请求不产生批次记录", leftover["total"] == 0, str(leftover)[:200])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-check-test-data-"))
    try:
        check_catalog()
        check_build_defaults()
        check_size_fidelity()
        check_determinism()
        check_limits()
        check_parseable()
        app = build_app(workdir)
        api = Api(app)
        check_api(api, workdir)
        check_api_errors(api)
    finally:
        # 应用的日志 handler 还占着临时目录里的文件，Windows 上不先关掉就删不掉，
        # ignore_errors 又会把失败吞掉，于是每跑一次就留一个临时目录。
        logging.shutdown()
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
