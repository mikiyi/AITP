"""Promptfoo ↔ AITP 自研引擎对拍。

同一批 12 条金融用例、同一批 good/bad 输出：
  - 左边：promptfoo 的判定（从 promptfoo/out.json 读）
  - 右边：AITP eval 引擎的判定（回放模式）
输出：逐条一致/不一致、一致率、以及「promptfoo 表达不了的东西」。

用法：
    python build_config.py
    cd promptfoo && npx promptfoo eval -c promptfooconfig.yaml --no-progress-bar -o out.json && cd ..
    python compare.py
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile

HERE = pathlib.Path(__file__).resolve().parent
import os
AITP_REPO = pathlib.Path(os.environ.get("AITP_REPO") or HERE.parents[1])
AITP_BACKEND = AITP_REPO / "backend"
sys.path.insert(0, str(AITP_BACKEND))

from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import eval_service  # noqa: E402

CASES = json.loads((HERE / "cases" / "finance_cases.json").read_text(encoding="utf-8"))


def _load_promptfoo() -> dict[tuple[str, str], bool]:
    path = HERE / "promptfoo" / "out.json"
    if not path.exists():
        raise SystemExit("缺少 promptfoo/out.json：先跑 build_config.py 与 promptfoo eval")
    payload = json.loads(path.read_text(encoding="utf-8"))
    results = payload["results"]["results"]
    verdicts: dict[tuple[str, str], bool] = {}
    for item in results:
        case_id = str(item["testCase"]["vars"].get("caseId") or "")
        provider = str(item.get("provider", {}).get("label") or "")
        mode = "good" if "good" in provider else "bad"
        verdicts[(case_id, mode)] = bool(item.get("success"))
    return verdicts


def _engine_verdicts() -> tuple[dict[tuple[str, str], bool], dict]:
    db_path = pathlib.Path(tempfile.mkdtemp(prefix="aitp_cmp_")) / "cmp.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("CMP", "对拍项目", now, now),
    )
    project_id = int(conn.execute("SELECT id FROM biz_project LIMIT 1").fetchone()["id"])
    suite_id = eval_service.create_suite(
        conn, {"projectId": project_id, "name": "对拍用例集", "visibility": "private", "targetType": "llm"}
    )
    for case in CASES:
        eval_service.add_case(
            conn,
            {
                "suiteId": suite_id,
                "projectId": project_id,
                "title": case["id"],
                "category": case["category"],
                "severity": case["severity"],
                "assertions": case["assertions"],
            },
        )

    verdicts: dict[tuple[str, str], bool] = {}
    metrics_bundle: dict[str, dict] = {}
    for mode in ("good", "bad"):
        run = eval_service.create_run(
            conn,
            {
                "projectId": project_id,
                "suiteId": suite_id,
                "config": {"model": "fixture"},
                "purpose": f"cmp-{mode}",
            },
        )
        outputs = [{"caseNo": case["id"], "output": case[mode], "latencyMs": 800} for case in CASES]
        detail = eval_service.execute_replay(conn, run["runId"], outputs)
        for item in detail["results"]:
            verdicts[(str(item["case_no"]), mode)] = item["verdict"] == "pass"
        metrics_bundle[mode] = detail["metrics"]
    return verdicts, metrics_bundle


def main() -> int:
    pf = _load_promptfoo()
    engine, metrics = _engine_verdicts()

    keys = sorted(set(pf) | set(engine))
    same = sum(1 for key in keys if pf.get(key) == engine.get(key))
    print("# Promptfoo ↔ AITP 引擎对拍（12 条用例 × good/bad）\n")
    print(f"一致：{same}/{len(keys)} = {same / len(keys):.0%}\n")
    print("| 用例 | 版本 | Promptfoo | AITP 引擎 | 一致 |")
    print("|---|---|---|---|---|")
    for key in keys:
        case_id, mode = key
        p, e = pf.get(key), engine.get(key)
        mark = "✓" if p == e else "✗"
        print(f"| {case_id} | {mode} | {'pass' if p else 'fail'} | {'pass' if e else 'fail'} | {mark} |")

    print("\n## 引擎多出来的东西（promptfoo 默认不提供）\n")
    for mode in ("good", "bad"):
        row = metrics[mode]
        sr = row["success_rate"]
        print(
            f"- {mode}: 成功率 {sr['value']:.2f}，95% Wilson [{sr['ci_lower']:.2f}, {sr['ci_upper']:.2f}]，"
            f"风险加权错误率 {row['severity_weighted_error']['value']:.3f}，n={sr['sample_size']}"
        )
    print("\n- 风险加权：1 条 S0 = 50 条 S2，promptfoo 的 pass 率对严重度一视同仁")
    print("- 区间：promptfoo 只报点估计；n=12 时点估计不可判成败，引擎会判 insufficient")
    print("- 判定失效：judge 输出不可解析时引擎单列 invalid 并剔除分母，promptfoo 无此语义")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
