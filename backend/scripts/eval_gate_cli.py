"""Eval 门禁 CLI（设计 §七 CI 集成）。

    python backend/scripts/eval_gate_cli.py --gate 1 --run latest --json logs/eval-gate.json

退出码：fail → 1；warn → 0 并输出 ::warning::；pass / insufficient → 0。
数据库默认取 AITP_DB_PATH，未设置时用仓库 data/aitp.db。
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import sqlite3
import sys
from typing import Any, cast

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.errors import AppError  # noqa: E402
from aitp.services import eval_service  # noqa: E402

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")
cast(Any, sys.stderr).reconfigure(encoding="utf-8", errors="replace")


def _connect(db_path: str) -> sqlite3.Connection:
    path = pathlib.Path(db_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.is_file():
        raise AppError(140100, f"数据库不存在：{path}")
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AITP 评测门禁 CLI")
    parser.add_argument("--gate", type=int, required=True, help="门禁 id")
    parser.add_argument("--run", default="latest", help="跑批 id 或 latest")
    parser.add_argument("--db", default=os.environ.get("AITP_DB_PATH", "data/aitp.db"))
    parser.add_argument("--json", dest="json_path", default="", help="把门禁结果写到 JSON 文件")
    args = parser.parse_args(argv)

    run_id: int | None = None
    if str(args.run).strip().lower() != "latest":
        try:
            run_id = int(args.run)
        except ValueError:
            parser.error("--run 只能是 latest 或跑批 id")

    try:
        conn = _connect(str(args.db))
        result = eval_service.evaluate_gate_by_id(conn, int(args.gate), run_id)
    except AppError as exc:
        print(f"[eval-gate] 求值失败：{exc.message}", file=sys.stderr)
        return 1
    except sqlite3.Error as exc:
        print(f"[eval-gate] 数据库错误：{exc}", file=sys.stderr)
        return 1

    conclusion = str(result.get("conclusion") or "")
    summary = (
        f"[eval-gate] gate={args.gate} sample={result.get('sampleSize')} "
        f"conclusion={conclusion}"
    )
    print(summary)
    for note in result.get("notes") or []:
        print(f"  - {note}")
    for item in result.get("failures") or []:
        print(f"  [未达标] {item.get('key')}: {item.get('judged')} vs {item.get('limit')}")

    if conclusion == "fail":
        print(f"::error::{summary}")
    elif conclusion == "warn":
        print(f"::warning::{summary}")
    else:
        print(f"::notice::{summary}")

    if args.json_path:
        target = pathlib.Path(args.json_path)
        if not target.is_absolute():
            target = ROOT / target
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            json.dumps(
                {"gate": int(args.gate), "runId": result.get("runId"), "result": result},
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        print(f"[eval-gate] 结果已写入 {target}")

    return 1 if conclusion == "fail" else 0


if __name__ == "__main__":
    raise SystemExit(main())