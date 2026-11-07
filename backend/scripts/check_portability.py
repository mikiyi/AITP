"""跨平台交付门禁：启动自检、PDF 中文字体、密钥库回落、脚本与依赖的可移植性。

这些行为只在「把仓库交给别人」时才暴露——Linux/macOS 的字体目录、钥匙串、
系统自带 SQLite、CRLF 换行、没有 cp313 轮子的依赖，本机全绿不代表对方能跑。
本脚本不写正式库、不改配置，随时可跑。

    python backend/scripts/check_portability.py
"""

from __future__ import annotations

import os
import pathlib
import platform
import re
import shutil
import subprocess
import sys
import tempfile
import types
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.common import preflight, secret_store  # noqa: E402
from aitp.config import load_config  # noqa: E402
from aitp.services import report_exporter  # noqa: E402

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


# ---------------------------------------------------------------- 启动自检


def check_preflight() -> None:
    check("自检模块存在", hasattr(preflight, "check_runtime"))
    check("trigram 最低版本写成常量", preflight.MIN_SQLITE_VERSION == (3, 34),
          str(preflight.MIN_SQLITE_VERSION))
    check("版本号解析补齐三段", preflight._version_tuple("3.45.1") == (3, 45, 1))
    check("过旧版本判低", preflight._version_tuple("3.26.0") < preflight.MIN_SQLITE_VERSION)
    check("3.34 恰好达标", preflight._version_tuple("3.34.0") >= preflight.MIN_SQLITE_VERSION)
    check("非纯数字版本号不炸", preflight._version_tuple("3.40.0-rc1") == (3, 40, 0),
          str(preflight._version_tuple("3.40.0-rc1")))
    check("后缀数字不并进补丁号", preflight._version_tuple("3.33.9-2020") < preflight.MIN_SQLITE_VERSION)
    check("两段版本号补齐", preflight._version_tuple("3.26") == (3, 26, 0))
    check("异常版本号按 0 处理", preflight._version_tuple("unknown") == (0, 0, 0))

    original_sqlite3 = getattr(preflight, "sqlite3")
    try:
        # 1) 模拟 SQLite 过旧：整体换成假的 sqlite3 模块，类型不同只能走 setattr
        setattr(
            preflight,
            "sqlite3",
            types.SimpleNamespace(
                sqlite_version="3.22.0",
                connect=lambda *a, **k: None,
                Error=Exception,
            ),
        )
        reason = preflight._probe_fts()
        check("SQLite 过旧被拦下", bool(reason), reason)
        check("提示里带上实际版本与要求版本", "3.22.0" in reason and "3.34" in reason, reason)

        # 2) 模拟 SQLite 够新但没编 FTS5/trigram
        boom = type("Boom", (Exception,), {})

        def _raise(*_a: Any, **_k: Any) -> None:
            raise boom("no such module: fts5")

        setattr(
            preflight,
            "sqlite3",
            types.SimpleNamespace(
                sqlite_version="3.45.1",
                connect=_raise,
                Error=boom,
            ),
        )
        reason = preflight._probe_fts()
        check("缺 FTS5 被拦下", bool(reason), reason)
        check("缺 FTS5 的提示指向 trigram", "trigram" in reason, reason)
    finally:
        setattr(preflight, "sqlite3", original_sqlite3)

    check("本机自检通过", preflight.check_runtime(load_config()) == [])

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-checkport-"))
    try:
        cfg = load_config()
        blocker = workdir / "afile"
        blocker.write_text("x", encoding="utf-8")
        cfg.db_path = blocker / "aitp.db"
        problems = preflight.check_runtime(cfg)
        check("数据库目录不可写被报出", any("数据库目录不可写" in item for item in problems),
              str(problems))

        cfg2 = load_config()
        cfg2.db_path = workdir / "sub" / "aitp.db"
        cfg2.deploy_log_path = blocker / "deploy.log"
        problems = preflight.check_runtime(cfg2)
        check("部署日志目录不可写被报出",
              any("部署日志目录不可写" in item for item in problems), str(problems))
        check("不可写时会自动创建父目录再试", (workdir / "sub").is_dir())

        cfg3 = load_config()
        cfg3.db_path = workdir / "aitp.db"
        cfg3.prompts_dir = workdir / "nope"
        problems = preflight.check_runtime(cfg3)
        check("提示词目录缺失被报出", any("提示词目录不存在" in item for item in problems),
              str(problems))

        cfg4 = load_config()
        cfg4.db_path = blocker / "aitp.db"
        cfg4.log_path = blocker / "aitp.log"
        problems = preflight.check_runtime(cfg4)
        check("多个问题一次报全", len(problems) >= 2, str(problems))

        # 探针文件用完即删，不在数据目录留垃圾
        before = {p.name for p in workdir.iterdir()}
        preflight._probe_writable(workdir, "临时目录")
        after = {p.name for p in workdir.iterdir()}
        check("写探针不留残留文件", before == after, str(after - before))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    create_app_source = (ROOT / "backend" / "aitp" / "app.py").read_text(encoding="utf-8")
    check("启动时真的调用自检", "_run_startup_preflight(cfg)" in create_app_source)
    check("自检排在迁移之前",
          create_app_source.index("_run_startup_preflight(cfg)")
          < create_app_source.index("_apply_pending_migrations(cfg)"))
    check("自检失败抛专用异常", "raise PreflightError(" in create_app_source)
    check("专用异常可直接捕获", issubclass(preflight.PreflightError, RuntimeError))

    run_source = (ROOT / "backend" / "run.py").read_text(encoding="utf-8")
    check("入口只接住自检异常", "except PreflightError" in run_source)
    check("入口失败时退出码非 0", "raise SystemExit(1)" in run_source)
    check("入口打印可读原因", "服务未启动" in run_source)

    start_source = (ROOT / "start.sh").read_text(encoding="utf-8")
    check("脚本区分启动失败与正常退出", "服务未能启动" in start_source)
    check("脚本失败时透传退出码", 'exit "$STATUS"' in start_source)


# ---------------------------------------------------------------- PDF 中文字体


def check_fonts() -> None:
    patterns = list(report_exporter._FONT_PATTERNS)
    check("黑体排在宋体之前",
          patterns.index("simhei.ttf") < patterns.index("simsun.ttc"))
    check("候选覆盖三系统",
          {"simhei.ttf", "PingFang.ttc", "NotoSansCJK*.ttc", "wqy-zenhei*.ttc"}
          <= set(patterns))
    check("不再写死单个盘符", "C:/Windows/Fonts/simhei.ttf" not in patterns)

    dirs = report_exporter._font_dirs()
    check("字体目录全部真实存在", all(path.is_dir() for path in dirs), str(dirs))
    if platform.system() == "Windows":
        check("Windows 扫描系统字体目录",
              any("Fonts" in str(path) for path in dirs), str(dirs))

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-checkfont-"))
    original_dirs = dict(report_exporter._FONT_DIRS)
    original_result = report_exporter._font_result
    try:
        junk = workdir / "NotoSansCJK-Regular.ttc"
        junk.write_bytes(b"not-a-font")
        second = workdir / "simhei.ttf"
        second.write_bytes(b"not-a-font")
        report_exporter._FONT_DIRS[platform.system() or "Linux"] = (str(workdir),)
        report_exporter._font_result = None
        found = report_exporter._cjk_font_files()
        check("候选顺序优先黑体", [path.name for path in found][:1] == ["simhei.ttf"],
              str([path.name for path in found]))
        check("同目录不同后缀都收进候选", len(found) == 2, str(found))
        check("命中文件去重",
              len({str(path).casefold() for path in found}) == len(found))

        report_exporter._font_result = None
        name, degraded = report_exporter._register_cjk_font()
        check("坏字体不抛异常", name == "Helvetica" and degraded, f"{name} {degraded}")

        # 缓存生效的判据：把扫盘换成必抛异常的函数，再取一次仍拿到降级结果
        def _no_scan() -> list[pathlib.Path]:
            raise AssertionError("命中缓存后不应再扫盘")

        original_files_fn = report_exporter._cjk_font_files
        report_exporter._cjk_font_files = _no_scan
        try:
            cached = report_exporter._cjk_font()
        finally:
            report_exporter._cjk_font_files = original_files_fn
        check("降级结果已缓存不再扫盘", cached[0] == "Helvetica" and cached[1], str(cached))

        # 目录不存在时也不能抛
        report_exporter._FONT_DIRS[platform.system() or "Linux"] = (str(workdir / "gone"),)
        report_exporter._font_result = None
        check("字体目录缺失时返回空候选", report_exporter._cjk_font_files() == [])
    finally:
        report_exporter._FONT_DIRS.clear()
        report_exporter._FONT_DIRS.update(original_dirs)
        report_exporter._font_result = original_result
        shutil.rmtree(workdir, ignore_errors=True)

    font_name, degraded = report_exporter._register_cjk_font()
    check("本机导出 PDF 有中文字体", degraded is False or platform.system() not in
          ("Windows", "Darwin"), f"{font_name} degraded={degraded}")
    payload, pdf_degraded = report_exporter.render_pdf(
        {
            "title": "中文字体渲染检查",
            "summary": "本段用于验证 reportlab 是否拿到了可用中文字体。",
            "content": "## 结论\n\n中文正文不应出现方块。",
            "status": "draft",
            "report_no": "RPT-PORTABILITY",
        },
        [{"section": "conclusion", "title": "结论", "content": "中文正文"}],
    )
    check("PDF 正常产出", payload.startswith(b"%PDF") and len(payload) > 2000,
          f"{len(payload)} bytes")
    check("render_pdf 与注册结果一致", pdf_degraded == degraded, f"{pdf_degraded} vs {degraded}")
    if not degraded:
        # 光有字节数不够：字体注册成功但字形没嵌进去，中文照样是方块
        from io import BytesIO

        from pypdf import PdfReader

        text = "\n".join(
            page.extract_text() or "" for page in PdfReader(BytesIO(payload)).pages
        )
        check("PDF 里的中文可被抽取", "中文正文不应出现方块" in text, text[:80])

    reports_source = (ROOT / "backend" / "aitp" / "api" / "reports.py").read_text(encoding="utf-8")
    request_source = (ROOT / "frontend" / "src" / "api" / "request.ts").read_text(encoding="utf-8")
    view_source = (ROOT / "frontend" / "src" / "views" / "reports" / "ReportDetailView.vue"
                   ).read_text(encoding="utf-8")
    check("后端仍在降级时打响应头", "X-AITP-Font-Warning" in reports_source)
    check("前端读同一个响应头", "X-AITP-Font-Warning" in request_source)
    check("前端按 fontWarning 提示", "result.fontWarning" in view_source)
    check("PDF 改走 fetch 而非 window.open", "reportApi.exportBlob(report.value.id, 'pdf')"
          in view_source)
    check("提示文案给了 Linux 安装命令", "fonts-noto-cjk" in view_source)


# ---------------------------------------------------------------- 密钥库回落


def check_secret_fallback() -> None:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-checksecret-"))
    original = {
        "set": secret_store._keychain_set,
        "get": secret_store._keychain_get,
        "delete": secret_store._keychain_delete,
    }
    ref = "ai_profile:9"
    try:
        store = secret_store.SecretStore(workdir)
        store._backend = secret_store.BACKEND_KEYCHAIN

        def _fail_set(_ref: str, _secret: bytes) -> None:
            raise OSError("SecKeychainAddGenericPassword 失败: -25308")

        secret_store._keychain_set = _fail_set  # type: ignore[assignment]
        secret_store._keychain_get = lambda _ref: None  # type: ignore[assignment]
        secret_store._keychain_delete = lambda _ref: None  # type: ignore[assignment]

        store.set(ref, "sk-secret-123")
        check("钥匙串写失败不抛异常", True)
        check("失败后回落读到原值", store.get(ref) == "sk-secret-123", store.get(ref))
        check("回落标签说明改用本地文件", "本地加密文件" in store.backend_label(),
              store.backend_label())

        index_path = workdir / "secrets.enc.json"
        check("回落写入确实落盘", index_path.is_file())
        check("落盘不是明文", "sk-secret-123" not in index_path.read_text(encoding="utf-8"))

        # 钥匙串恢复可用：写成功要顺手清掉文件侧的旧副本
        written: dict[str, bytes] = {}

        def _ok_set(target: str, secret: bytes) -> None:
            written[target] = secret

        secret_store._keychain_set = _ok_set  # type: ignore[assignment]
        secret_store._keychain_get = lambda target: written.get(target)  # type: ignore[assignment]
        store.set(ref, "sk-new-456")
        check("恢复后写进钥匙串", written.get(ref) == b"sk-new-456", str(written))
        check("恢复后读到钥匙串值", store.get(ref) == "sk-new-456", store.get(ref))
        check("写成功清掉文件侧副本", ref not in store._read_index(), str(store._read_index()))
        check("恢复后标签不再提示回落", "不可用" not in store.backend_label(),
              store.backend_label())

        # 写失败但钥匙串里还留着旧值时，回落副本必须优先，不能被旧值盖回去
        secret_store._keychain_set = _fail_set  # type: ignore[assignment]
        secret_store._keychain_get = lambda _ref: b"sk-stale"  # type: ignore[assignment]
        store.set(ref, "sk-fresh")
        check("回落副本优先于钥匙串旧值", store.get(ref) == "sk-fresh", store.get(ref))

        secret_store._keychain_set = _ok_set  # type: ignore[assignment]
        secret_store._keychain_get = lambda target: written.get(target)  # type: ignore[assignment]
        store.set(ref, "sk-new-456")

        # 读失败也不能 500：回落文件侧
        def _fail_get(_ref: str) -> bytes | None:
            raise OSError("SecKeychainFindGenericPassword 失败: -128")

        store._file_set(ref, "sk-old-789")
        secret_store._keychain_get = _fail_get  # type: ignore[assignment]
        check("钥匙串读失败时回落文件", store.get(ref) == "sk-old-789", store.get(ref))

        # 删除两侧都清
        secret_store._keychain_delete = lambda _ref: None  # type: ignore[assignment]
        written.pop(ref, None)
        store.delete(ref)
        check("删除后两侧都不残留", store.get(ref) == "" and ref not in store._read_index())

        def _fail_delete(_ref: str) -> None:
            raise OSError("SecKeychainItemDelete 失败: -25299")

        secret_store._keychain_delete = _fail_delete  # type: ignore[assignment]
        store._file_set(ref, "sk-x")
        store.delete(ref)
        check("钥匙串删除失败不抛且清掉文件副本", ref not in store._read_index())
    finally:
        secret_store._keychain_set = original["set"]  # type: ignore[assignment]
        secret_store._keychain_get = original["get"]  # type: ignore[assignment]
        secret_store._keychain_delete = original["delete"]  # type: ignore[assignment]
        shutil.rmtree(workdir, ignore_errors=True)

    store_dir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-checkstore-"))
    try:
        backend = secret_store.default_store(store_dir).backend
        check(
            "本机密钥后端可用",
            backend in {
                secret_store.BACKEND_DPAPI,
                secret_store.BACKEND_KEYCHAIN,
                secret_store.BACKEND_FILE,
            },
            backend,
        )
    finally:
        shutil.rmtree(store_dir, ignore_errors=True)

    file_dir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-checkfile-"))
    try:
        file_store = secret_store.SecretStore(file_dir)
        file_store._backend = secret_store.BACKEND_FILE
        file_store.set("test:1", "abc")
        check("文件后端读写正常", file_store.get("test:1") == "abc", file_store.get("test:1"))
        file_store.delete("test:1")
        check("文件后端删除正常", file_store.get("test:1") == "")
    finally:
        shutil.rmtree(file_dir, ignore_errors=True)


# ---------------------------------------------------------------- 脚本与依赖


def check_packaging() -> None:
    lines = [
        line.strip()
        for line in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.strip().startswith("#")
    ]
    try:
        from pip._vendor.packaging.requirements import Requirement
    except ImportError:
        check("requirements 可解析", False, "缺少 packaging，无法校验标记")
        return

    def applies(version: str) -> list[str]:
        picked: list[str] = []
        for raw in lines:
            requirement = Requirement(raw)
            if not requirement.marker or requirement.marker.evaluate(
                {"python_version": version, "platform_system": "Linux", "sys_platform": "linux"}
            ):
                picked.append(raw)
        return picked

    for version in ("3.11", "3.12"):
        numpy = [raw for raw in applies(version) if raw.lower().startswith("numpy")]
        check(f"Python {version} 只命中一条 numpy 且为 1.26.4",
              len(numpy) == 1 and "1.26.4" in numpy[0], str(numpy))
    numpy_313 = [raw for raw in applies("3.13") if raw.lower().startswith("numpy")]
    check("Python 3.13 不再要求 numpy 1.26.4",
          len(numpy_313) == 1 and "1.26.4" not in numpy_313, str(numpy_313))
    check("Python 3.13 放行 numpy 2.x",
          len(numpy_313) == 1 and ">=2" in numpy_313[0], str(numpy_313))
    check("依赖条目未因标记丢失", len(applies("3.11")) == len(applies("3.13")),
          f"{len(applies('3.11'))} vs {len(applies('3.13'))}")


def _working_bash() -> str:
    """找一个真正能跑的 GNU bash。

    Windows 上 subprocess 解析裸 "bash" 会优先命中 System32 的 WSL 转发器，
    没装发行版时它直接报错；所以显式挑一个 --version 可用的路径。
    """
    candidates: list[str] = []
    first = shutil.which("bash")
    if first:
        candidates.append(first)
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if not entry:
            continue
        for name in ("bash.exe", "bash"):
            path = pathlib.Path(entry) / name
            if path.is_file() and str(path) not in candidates:
                candidates.append(str(path))
    for candidate in candidates:
        try:
            probe = subprocess.run(
                [candidate, "--version"], capture_output=True, text=True, check=False
            )
        except OSError:
            continue
        if probe.returncode == 0 and "GNU bash" in probe.stdout + probe.stderr:
            return candidate
    return ""


def check_scripts() -> None:
    bash = _working_bash()
    if not bash:
        print("[SKIP] 本机没有可用的 GNU bash，跳过 shell 语法检查")
    for name in ("start.sh", "stop.sh"):
        path = ROOT / name
        raw = path.read_bytes()
        check(f"{name} 没有 CR 字符", b"\r" not in raw, "含 \\r 的脚本在 Linux 直接 bad interpreter")
        check(f"{name} 有 bash shebang", raw.startswith(b"#!/usr/bin/env bash"))
        if bash:
            probe = subprocess.run(
                [bash, "-n", str(path)], capture_output=True, text=True, check=False
            )
            check(f"{name} 语法通过 bash -n", probe.returncode == 0, probe.stderr.strip()[:160])

    attributes = ROOT / ".gitattributes"
    check("仓库固定换行符", attributes.is_file())
    if attributes.is_file():
        text = attributes.read_text(encoding="utf-8")
        check("*.sh 强制 LF", "*.sh text eol=lf" in text)
        check("*.bat 强制 CRLF", "*.bat text eol=crlf" in text)
        check("默认按内容判定文本", "* text=auto" in text)

    listing = subprocess.run(
        ["git", "ls-files", "-s", "start.sh", "stop.sh"],
        cwd=str(ROOT), capture_output=True, text=True, check=False,
    ) if shutil.which("git") else None
    if listing is None:
        print("[SKIP] 未安装 git，跳过执行位检查")
    else:
        modes = {line.split("\t")[1].strip(): line.split()[0] for line in
                 listing.stdout.splitlines() if line.strip()}
        check("start.sh 在 git 里带执行位",
              modes.get("start.sh", "").endswith("100755"), str(modes))
        check("stop.sh 在 git 里带执行位",
              modes.get("stop.sh", "").endswith("100755"), str(modes))

    start_source = (ROOT / "start.sh").read_text(encoding="utf-8")
    check("解释器首选已验证版本",
          start_source.index("python3.12") < start_source.index("python3.13"))
    check("Python 3.13 仍作备选", "python3.13" in start_source)
    check("Linux 有 xdg-open 打开页面", "xdg-open" in start_source)
    check("Git Bash 有 rundll32 兜底", "rundll32" in start_source)
    check("虚拟环境布局按平台判断",
          'bin/python' in start_source and 'Scripts/python.exe' in start_source)
    check("macOS 依赖提示给出版本", "brew install python@3.12" in start_source)


# 交付物里不该出现作者本机路径。斜杠用字符类写法，命中面覆盖 \ 与 /。
_AUTHOR_PATH_RE = re.compile(
    rb"D:[\\/]+Qoter|C:[\\/]+Users[\\/]+52748|app_install|testSKill"
)
_SCAN_ROOTS = (
    "backend", "frontend/src", "scripts", "README.md", "README.en.md",
    "requirements.txt", "start.sh", "stop.sh", ".gitattributes",
)
_SCAN_SKIP_PARTS = frozenset({"static", "__pycache__", ".venv", "node_modules", "data"})
# 本文件为了抓这些路径必须原样写出它们，是唯一豁免；能否命中由下面的正向对照证明。
_SCAN_SELF = pathlib.Path(__file__).resolve()
_SCAN_SUFFIXES = frozenset({
    ".py", ".md", ".txt", ".vue", ".ts", ".js", ".json", ".yaml", ".yml",
    ".sh", ".bat", ".cmd", ".html", ".css", ".ini", ".cfg",
})


def _display_path(path: pathlib.Path) -> str:
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _scan_author_paths(roots: list[pathlib.Path]) -> list[str]:
    """在给定目录/文件里按字节搜作者本机路径，返回命中位置列表。"""
    hits: list[str] = []
    for root in roots:
        candidates: list[pathlib.Path] = []
        if root.is_file():
            candidates.append(root)
        elif root.is_dir():
            for path in root.rglob("*"):
                if path.suffix.lower() not in _SCAN_SUFFIXES or not path.is_file():
                    continue
                if _SCAN_SKIP_PARTS & {part.lower() for part in path.parts}:
                    continue
                candidates.append(path)
        for path in candidates:
            if path.resolve() == _SCAN_SELF:
                continue
            try:
                raw = path.read_bytes()
            except OSError:
                continue
            for lineno, line in enumerate(raw.splitlines(), start=1):
                if _AUTHOR_PATH_RE.search(line):
                    hits.append(f"{_display_path(path)}:{lineno}")
    return sorted(hits)


def check_author_paths() -> None:
    hits = _scan_author_paths([ROOT / name for name in _SCAN_ROOTS])
    check("交付物里没有作者本机路径", not hits, str(hits[:5]))

    # 正向对照：证明扫描器确实能命中，而不是正则写错导致恒为 0
    sample_dir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-checkleak-"))
    sample = sample_dir / "leak.py"
    try:
        for fragment, expected in (
            (r'"D:\\Qoter\\AITP\\aitp.db"', 1),
            (r'"C:\\Users\\52748\\x"', 1),
            ('"D:/Qoter/AITP"', 1),
            ('"app_install"', 1),
            ('"clean_value = 1"', 0),
        ):
            sample.write_text("DB = " + fragment + "\n", encoding="utf-8")
            found = _scan_author_paths([sample_dir])
            check(
                f"路径样例 {fragment[1:24]}… 命中 {expected} 处",
                len(found) == expected,
                str(found),
            )
    finally:
        shutil.rmtree(sample_dir, ignore_errors=True)


def main() -> int:
    print("== 启动前置自检 ==")
    check_preflight()
    print("== PDF 中文字体 ==")
    check_fonts()
    print("== 密钥库回落 ==")
    check_secret_fallback()
    print("== 依赖与脚本可移植性 ==")
    check_packaging()
    check_scripts()
    check_author_paths()
    print(f"\n合计 PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
