"""用例测试数据解析：内联内容或本地 txt / Excel / Json / SQL 文件路径。

扩展字段「测试数据」既可以直接贴数据（默认），也可以填一个本地文件路径；
接口自动化 / Python 自动化 / JMeter 性能执行都从这里取数，格式口径统一：
  - txt / csv / tsv / log：按行读，含分隔符时首行作表头 → 记录集
  - xlsx / xls：首行表头 → 记录集
  - json：数组[对象] → 记录集；对象 → 单条记录；其余按文本
  - sql：整段脚本文本，交给执行侧自行消费
只接受本地路径与内联内容，不接受 URL（避免执行期外联）。
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

from ..errors import AppError, ErrorCode

MAX_FILE_BYTES = 8 * 1024 * 1024
MAX_ROWS = 2000
PREVIEW_ROWS = 5
PREVIEW_TEXT_CHARS = 400

TEXT_EXTS = {".txt", ".csv", ".tsv", ".log"}
EXCEL_EXTS = {".xlsx", ".xls"}
JSON_EXTS = {".json"}
SQL_EXTS = {".sql"}

EMPTY: dict[str, Any] = {
    "kind": "empty", "source": "inline", "path": "", "text": "",
    "rows": [], "records": [], "columns": [], "rowCount": 0,
}


def _text_rows(text: str) -> list[list[str]]:
    rows: list[list[str]] = []
    for line in text.splitlines():
        if not line.strip():
            continue
        if "\t" in line:
            cells = line.split("\t")
        elif "," in line:
            cells = next(csv.reader(io.StringIO(line)))
        else:
            cells = [line.strip()]
        rows.append([str(c).strip() for c in cells])
        if len(rows) >= MAX_ROWS:
            break
    return rows


def _from_rows(rows: list[list[str]], kind: str, path: str) -> dict[str, Any]:
    if not rows:
        return {**EMPTY, "kind": kind, "source": "file" if path else "inline", "path": path}
    width = max(len(r) for r in rows)
    rows = [r + [""] * (width - len(r)) for r in rows]
    # 文件默认首行表头；内联只有两行时不猜表头，避免把首条数据当表头吃掉
    header_like = (bool(path) or len(rows) > 2) and all(
        c and not c.replace(".", "").isdigit() for c in rows[0]
    )
    columns = rows[0] if header_like else []
    body = rows[1:] if header_like else rows
    records = [dict(zip(columns, r)) for r in body] if columns else []
    text = "\n".join(",".join(r) for r in rows)
    return {
        "kind": kind,
        "source": "file" if path else "inline",
        "path": path,
        "text": text,
        "rows": rows,
        "records": records,
        "columns": columns,
        "rowCount": len(records) if records else len(rows),
    }


def _from_json(text: str, kind: str, path: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (ValueError, TypeError) as exc:
        raise AppError(ErrorCode.PARAM_ERROR, f"测试数据 JSON 解析失败：{exc}") from exc
    if isinstance(data, list):
        records = [item for item in data if isinstance(item, dict)]
        if records:
            columns: list[str] = []
            for rec in records:
                for key in rec:
                    if key not in columns:
                        columns.append(str(key))
            rows = [columns] + [[str(rec.get(col, "")) for col in columns] for rec in records]
            return {
                "kind": kind, "source": "file" if path else "inline", "path": path,
                "text": text, "rows": rows[:MAX_ROWS], "records": records[:MAX_ROWS],
                "columns": columns, "rowCount": len(records),
            }
        rows = [[str(item)] for item in data][:MAX_ROWS]
        return {**_from_rows(rows, kind, path), "text": text}
    if isinstance(data, dict):
        columns = [str(k) for k in data]
        rows = [columns, [str(v) for v in data.values()]]
        return {
            "kind": kind, "source": "file" if path else "inline", "path": path,
            "text": text, "rows": rows, "records": [data], "columns": columns, "rowCount": 1,
        }
    return {**EMPTY, "kind": kind, "source": "file" if path else "inline", "path": path,
            "text": text, "rows": [[str(data)]], "rowCount": 1}


def _excel_rows(path: Path) -> list[list[str]]:
    from openpyxl import load_workbook

    workbook = load_workbook(filename=str(path), data_only=True, read_only=True)
    try:
        sheet = workbook.worksheets[0]
        rows: list[list[str]] = []
        for raw in sheet.iter_rows(values_only=True):
            cells = ["" if v is None else str(v).strip() for v in raw]
            if not any(cells):
                continue
            rows.append(cells)
            if len(rows) >= MAX_ROWS:
                break
        return rows
    finally:
        workbook.close()


def resolve(value: str) -> dict[str, Any]:
    """把「测试数据」字段值解析成统一结构；空值返回 empty，不报错。"""
    text = (value or "").strip()
    if not text:
        return dict(EMPTY)
    if text.lower().startswith(("http://", "https://")):
        raise AppError(
            ErrorCode.PARAM_ERROR,
            "测试数据只支持本地文件路径或直接填写内容，不支持 URL 链接",
        )
    path = Path(text)
    if path.is_file():
        if path.stat().st_size > MAX_FILE_BYTES:
            raise AppError(ErrorCode.PARAM_ERROR, f"测试数据文件超过 8MiB 上限：{path.name}")
        ext = path.suffix.lower()
        if ext in EXCEL_EXTS:
            return _from_rows(_excel_rows(path), "excel", str(path))
        if ext in JSON_EXTS:
            return _from_json(path.read_text(encoding="utf-8-sig", errors="replace"), "json", str(path))
        if ext in SQL_EXTS:
            sql = path.read_text(encoding="utf-8-sig", errors="replace")
            return {**EMPTY, "kind": "sql", "source": "file", "path": str(path),
                    "text": sql, "rowCount": 0}
        if ext in TEXT_EXTS or ext == "":
            return _from_rows(
                _text_rows(path.read_text(encoding="utf-8-sig", errors="replace")), "txt", str(path)
            )
        raise AppError(
            ErrorCode.PARAM_ERROR,
            f"不支持的测试数据格式：{ext or path.name}（支持 txt/csv/xlsx/xls/json/sql）",
        )
    if text.startswith(("[", "{")):
        try:
            return _from_json(text, "inline-json", "")
        except AppError:
            pass
    return _from_rows(_text_rows(text), "inline-text", "")


def to_csv(data: dict[str, Any]) -> str:
    """记录集/行集 → CSV 文本，供 JMeter CSVDataSet 与脚本消费。"""
    rows = data.get("rows") or []
    if not rows:
        return ""
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")
    for row in rows:
        writer.writerow(row)
    return buffer.getvalue()


def preview(value: str) -> dict[str, Any]:
    """编辑器「解析预览」出参：只带样本，不回传全量数据。"""
    data = resolve(value)
    return {
        "kind": data["kind"],
        "source": data["source"],
        "path": data["path"],
        "rowCount": data["rowCount"],
        "columns": data["columns"],
        "sampleRows": data["rows"][:PREVIEW_ROWS],
        "textSample": data["text"][:PREVIEW_TEXT_CHARS],
    }


def for_case(conn: Any, case_id: int | None) -> dict[str, Any]:
    """按用例 id 取扩展字段「测试数据」并解析；无用例/空值返回 empty。"""
    if not case_id:
        return dict(EMPTY)
    row = conn.execute(
        "SELECT extra_json FROM biz_test_case WHERE id = ? AND is_deleted = 0", (case_id,)
    ).fetchone()
    if row is None:
        return dict(EMPTY)
    from ..repositories.base import loads

    extra = loads(row["extra_json"], {}) or {}
    return resolve(str(extra.get("测试数据") or ""))


def for_script(conn: Any, script: dict[str, Any] | None,
               payload: dict[str, Any] | None) -> dict[str, Any]:
    """自动化/性能执行的取数链路：显式 caseId → 脚本按用例编号匹配 → 轮次首条带数据的用例。"""
    payload = payload or {}
    case_id = payload.get("caseId")
    if not case_id and script:
        match_value = str(script.get("case_match_value") or "").strip()
        if match_value and str(script.get("case_match_mode") or "case_no") == "case_no":
            row = conn.execute(
                "SELECT id FROM biz_test_case WHERE case_no = ? AND is_deleted = 0", (match_value,)
            ).fetchone()
            if row is not None:
                case_id = row["id"]
    if not case_id and payload.get("testRunId"):
        row = conn.execute(
            "SELECT c.id FROM biz_test_run_case rc JOIN biz_test_case c ON c.id = rc.case_id"
            " WHERE rc.run_id = ? AND c.is_deleted = 0"
            " AND c.extra_json LIKE '%测试数据%' ORDER BY rc.id ASC LIMIT 1",
            (payload["testRunId"],),
        ).fetchone()
        if row is not None:
            case_id = row["id"]
    return for_case(conn, int(case_id) if case_id else None)