"""代码噪声门禁：调试残留、注释标记、注释掉的代码、未使用导入。

    python backend/scripts/check_noise.py

只做确定性的静态扫描，不依赖数据库与模型：
- TODO / FIXME / XXX / HACK 注释标记
- debugger / console.log / alert / pdb / breakpoint 调试残留
- 被注释掉的代码行（Python 与前端）
- Python 未使用导入（尊重 `# noqa: F401` 与 `__all__`）
- 仓库根目录的临时文件（.tmp/.bak/.orig）

退出码 0 = 全部通过。
"""

from __future__ import annotations

import ast
import io
import pathlib
import re
import sys
import tokenize
from typing import Any, cast

ROOT = pathlib.Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
FRONTEND_SRC = ROOT / "frontend" / "src"

TOKEN_RE = re.compile(r"[A-Za-z_]\w*")
MARKER_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")
PY_COMMENTED_RE = re.compile(
    r"^\s*#\s*(def |class |return |if |for |while |import |from \S+ import |try:|except )"
)
TS_COMMENTED_RE = re.compile(
    r"^\s*//\s*(const |let |var |return |if \(|function |import |export |console\.)"
)
NOISE_FILE_SUFFIXES = (".tmp", ".bak", ".orig")
PRINT_ALLOW = {"observability.py"}
DEBUG_FILE_ALLOW: set[str] = set()

passed = 0
failed = 0
failures: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    global passed, failed
    if ok:
        passed += 1
        print(f"[PASS] {name}")
        return
    failed += 1
    failures.append(name)
    print(f"[FAIL] {name}  {detail}")


def py_files(root: pathlib.Path | None = None) -> list[pathlib.Path]:
    return [
        p for p in (root or BACKEND).rglob("*.py")
        if "__pycache__" not in p.parts
    ]


def fe_files() -> list[pathlib.Path]:
    return [
        p for p in FRONTEND_SRC.rglob("*")
        if p.suffix in (".ts", ".vue")
    ]


def py_comments(path: pathlib.Path) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    src = path.read_text(encoding="utf-8", errors="replace")
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                out.append((tok.start[0], tok.string))
    except (tokenize.TokenError, IndentationError):
        pass
    return out


def unused_imports(paths: list[pathlib.Path]) -> list[str]:
    problems: list[str] = []
    for path in paths:
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError as exc:
            problems.append(f"{path.relative_to(ROOT)}:{exc.lineno} 语法错误，无法解析")
            continue
        lines = src.splitlines()
        used: set[str] = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Name):
                used.add(node.id)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                used |= set(TOKEN_RE.findall(node.value))
        exported: set[str] = set()
        if "__all__" in src:
            match = re.search(r"__all__\s*=\s*[\[(](.*?)[\])]", src, re.S)
            if match:
                exported = set(re.findall(r"['\"](\w+)['\"]", match.group(1)))
        for node in ast.walk(tree):
            if not isinstance(node, (ast.Import, ast.ImportFrom)):
                continue
            stmt = "\n".join(lines[node.lineno - 1: node.end_lineno])
            if "noqa" in stmt:
                continue
            if isinstance(node, ast.ImportFrom) and node.module == "__future__":
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue
                local = alias.asname or (
                    alias.name.split(".")[0] if isinstance(node, ast.Import) else alias.name
                )
                if local in ("annotations",) or local in exported or local in used:
                    continue
                problems.append(
                    f"{path.relative_to(ROOT)}:{node.lineno} 未使用导入 {local}"
                )
    return problems


def main() -> int:
    try:
        cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    py_all = py_files()
    py_app = py_files(BACKEND / "aitp")
    fe = fe_files()

    # 1) 注释标记
    marker_hits: list[str] = []
    for path in py_all:
        for line_no, comment in py_comments(path):
            if MARKER_RE.search(comment):
                marker_hits.append(f"{path.relative_to(ROOT)}:{line_no}")
    for path in fe:
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if re.match(r"^\s*(//|/\*|\*)", line) and MARKER_RE.search(line):
                marker_hits.append(f"{path.relative_to(ROOT)}:{line_no}")
    check("无 TODO/FIXME/XXX/HACK 注释标记", not marker_hits, "; ".join(marker_hits[:8]))

    # 2) 前端调试残留
    fe_noise: list[str] = []
    for path in fe:
        name = path.name
        if name in DEBUG_FILE_ALLOW:
            continue
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if "debugger" in line or re.search(r"(?<![\w.])alert\(", line) or "console.log" in line:
                fe_noise.append(f"{path.relative_to(ROOT)}:{line_no}")
    check("前端无 debugger/console.log/alert 残留", not fe_noise, "; ".join(fe_noise[:8]))

    # 3) 后端调试残留（AST 识别真实调用，忽略脚本文本串；脚本本身是 CLI，print 属正常输出）
    py_noise: list[str] = []
    for path in py_app:
        if path.name in PRINT_ALLOW or path.name == "check_noise.py":
            continue
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            func_name = func.id if isinstance(func, ast.Name) else (
                func.attr if isinstance(func, ast.Attribute) else ""
            )
            if func_name in ("breakpoint",):
                py_noise.append(f"{path.relative_to(ROOT)}:{node.lineno} {func_name}()")
            elif func_name == "print" and isinstance(func, ast.Name):
                py_noise.append(f"{path.relative_to(ROOT)}:{node.lineno} print()")
            elif func_name == "set_trace":
                py_noise.append(f"{path.relative_to(ROOT)}:{node.lineno} pdb.set_trace()")
    check("后端无 print/breakpoint/pdb 调试残留", not py_noise, "; ".join(py_noise[:8]))

    # 4) 被注释掉的代码
    commented: list[str] = []
    for path in py_all:
        for line_no, comment in py_comments(path):
            if PY_COMMENTED_RE.match(comment):
                commented.append(f"{path.relative_to(ROOT)}:{line_no}")
    for path in fe:
        for line_no, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            if TS_COMMENTED_RE.match(line):
                commented.append(f"{path.relative_to(ROOT)}:{line_no}")
    check("无被注释掉的代码", not commented, "; ".join(commented[:8]))

    # 5) 未使用导入
    unused = unused_imports(py_all)
    check("Python 无未使用导入", not unused, "; ".join(unused[:8]))

    # 6) 仓库临时文件
    junk: list[str] = []
    skip_dirs = {"node_modules", ".venv", ".git", "data", "logs", "build", "dist", "static"}
    for path in ROOT.rglob("*"):
        if any(part in skip_dirs for part in path.parts):
            continue
        if path.is_file() and path.name.endswith(NOISE_FILE_SUFFIXES):
            junk.append(str(path.relative_to(ROOT)))
    check("仓库无 .tmp/.bak/.orig 临时文件", not junk, "; ".join(junk[:8]))

    print(f"\n噪声门禁：PASS={passed} FAIL={failed}")
    if failures:
        print("失败项：")
        for item in failures:
            print(f"  - {item}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
