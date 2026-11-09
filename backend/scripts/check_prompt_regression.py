"""GAP-31 提示词效果回归集：离线样例集 + 一键跑批 + 结构化对比报告。

要解决的问题：改提示词（`backend/prompts/*.md`）或改装配 / 解析 / schema / 去重链路，
过去只能人工试跑，没有任何回归保护。本门禁把「提示词渲染 → 上下文装配 → 模型调用 →
解析 → schema 校验 → 去重」这条真实链路跑一遍，但**模型换成本机 mock 的确定性回放**
（fixture 是录制下来的产出），因此不联网、不消耗任何真实额度。

指标口径（写进基线文件，逐项比对）：
- passRate      schema 通过率：passed / total
- retention     条目留存率：(total - rejected) / total
- completeness  字段完整率：1 - (默认补齐 + 丢弃) / total
- dupRate       重复率：去重命中 / 比对条数
- issueCount    schema 问题条数
- promptChars   送达模型的提示词字符数（提示词体积变化的直接信号）

用法：
    python backend/scripts/check_prompt_regression.py                 # 跑批并与基线比对
    python backend/scripts/check_prompt_regression.py --show          # 附带打印对比报告
    python backend/scripts/check_prompt_regression.py --update-baseline  # 重新记录基线

样例集可扩展：往 `backend/prompts/regression/samples.json` 的 samples 里加一条
（id 唯一 + capability 真实存在 + fixture 是「录制的产出」），再跑 `--update-baseline`。

去重指标要能落地必须让 GAP-29 有基线可比，所以工装库按样例建独立项目并预置库内用例
（见 `DEDUP_SEEDS`）：同名条目必须判重、同类型的对照条目必须不判重，两头都锁死。
"""

from __future__ import annotations

import argparse
import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai import output_schema  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import dedup_service  # noqa: E402

SAMPLES_PATH = ROOT / "backend" / "prompts" / "regression" / "samples.json"
BASELINE_PATH = ROOT / "backend" / "prompts" / "regression" / "baseline.json"
V1 = "/api/v1"

#: 越大越好的指标：回退超过容差即红
HIGHER_BETTER = ("passRate", "retention", "completeness")
#: 越小越好的指标：上升超过容差即红
LOWER_BETTER = ("dupRate", "issueCount")
RATE_TOL = 0.02
COUNT_TOL = 2
#: 提示词体积是「有意改动」最常见的结果，容差放宽到 ±15%，只拦数量级跑偏
PROMPT_DRIFT = 0.15

#: 每个样例都必须让模型「看得到」的输入关键词：证明提示词真的被渲染送达，而不是空跑
MUST_APPEAR = {
    "case_generation": "验证码",
    "xmind_to_case": "优惠券叠加",
    "testpoint_generation": "锁定",
    "regression_generation": "优惠券叠加",
    "requirement_confirm": "验证码",
}

#: GAP-29 的比对基线只能从库内用例召回，工装库不种用例的话 dupRate 恒为 0，
#: 「重复率」这条指标就永远没有信息量。这里给每个可比对能力预置库内用例：
#: dupe=True 的标题刻意与 fixture 某一条完全同名（必须判重），
#: dupe=False 的同类型不同名用例是对照（必须不判重，否则「判出重复」等于全都判重）。
DEDUP_SEEDS: dict[str, list[dict[str, Any]]] = {
    "case_login_typical": [
        {"title": "正确的手机号和验证码可以登录成功", "caseType": "functional", "dupe": True},
        {"title": "连续 5 次验证码错误后账号锁定 10 分钟", "caseType": "exception", "dupe": True},
        {"title": "导出报表的表头顺序与字段名一致", "caseType": "functional", "dupe": False},
        # 疑似档基线（0.933，够标记不够摘走）：GAP-29 的「下降」必须是精准下降，
        # 只按确定重复扣条数，不然「全部拦下」也能凑出 100% 的下降比例
        {"title": "验证码超过 5 分钟后点击提交被拒绝", "caseType": "boundary",
         "dupe": True, "exact": False},
    ],
    "xmind_order_tree": [
        {"title": "两张可叠加优惠券同时生效", "caseType": "functional", "dupe": True},
        {"title": "购物车合并后备注丢失", "caseType": "functional", "dupe": False},
    ],
    # regression_generation 的类型口径是 priority（见 dedup_service.comparable_of）
    "regression_release_scope": [
        {"title": "登录限流窗口边界", "caseType": "P0", "dupe": True},
        {"title": "支付回调重放保护", "caseType": "P0", "dupe": False},
    ],
}
#: 种了几条同名用例就该判出几条重复：多了是误判，少了是链路断了
EXPECTED_DUP_HITS = {
    key: sum(1 for seed in value if seed["dupe"]) for key, value in DEDUP_SEEDS.items()
}
#: 命中里属于「确定重复」（increment 模式该摘走）与「仅疑似」（该留下）的分档数
EXPECTED_EXACT_HITS = {
    key: sum(1 for seed in value if seed["dupe"] and seed.get("exact", True))
    for key, value in DEDUP_SEEDS.items()
}
EXPECTED_SUSPECT_HITS = {
    key: sum(1 for seed in value if seed["dupe"] and not seed.get("exact", True))
    for key, value in DEDUP_SEEDS.items()
}
#: 对照用例的标题：出现在判定明细里就说明去重在误判
CONTROL_TITLES = {
    str(sample_id): [seed["title"] for seed in seeds if not seed["dupe"]]
    for sample_id, seeds in DEDUP_SEEDS.items()
}

_passed = 0
_failed = 0
#: mock 收到的原始请求体，按 model 名归档（= 装配后的提示词）
CAPTURED: dict[str, list[dict[str, Any]]] = {}
_LOCK = threading.Lock()


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


# ---------------- 本机 mock 模型（确定性回放，绝不联网） ----------------

class _MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 屏蔽 stderr 噪声
        pass

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = cast(dict[str, Any], json.loads(self.rfile.read(length).decode("utf-8")))
        except Exception:
            body = {}
        model = str(body.get("model") or "")
        with _LOCK:
            CAPTURED.setdefault(model, []).append(body)
        with _LOCK:
            replies = REPLIES
        content = replies.get(model, "")
        payload = json.dumps({
            "id": f"chatcmpl-{model}",
            "object": "chat.completion",
            "model": model,
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": content},
                "finish_reason": "stop",
            }],
            "usage": {"prompt_tokens": 800, "completion_tokens": 400, "total_tokens": 1200},
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


REPLIES: dict[str, str] = {}


def start_mock() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------- 指标抽取 ----------------

def metrics_of(data: dict[str, Any], prompt_chars: int) -> dict[str, float]:
    """把一次跑批的结论压成可对比的数值；口径全部来自 GAP-28/29 的落库结论。"""
    schema = cast(dict[str, Any], data.get("schema") or {})
    dedup = cast(dict[str, Any], data.get("dedup") or {})
    total = int(schema.get("total") or 0)
    passed = int(schema.get("passed") or 0)
    rejected = int(schema.get("rejected") or 0)
    dropped = int(schema.get("droppedCount") or 0)
    filled = len(schema.get("filledDefaults") or [])
    issues = int(schema.get("issueCount") or 0)
    compared = int(dedup.get("compared") or 0)
    hits = int(dedup.get("hitCount") or 0)
    usable = bool(schema.get("parsed")) and bool(schema.get("ok"))

    if total > 0:
        pass_rate = passed / total
        retention = (total - rejected) / total
        completeness = max(0.0, 1.0 - (filled + dropped) / total)
    else:
        # 没有条目数组的能力（如测试点树、Markdown 产出）按整体结论给 1/0
        pass_rate = 1.0 if usable else 0.0
        retention = 1.0 if usable else 0.0
        completeness = 1.0 if usable else 0.0

    dup_rate = hits / max(compared, total, 1)
    return {
        "passRate": round(pass_rate, 4),
        "retention": round(retention, 4),
        "completeness": round(completeness, 4),
        "dupRate": round(dup_rate, 4),
        "issueCount": float(issues),
        "promptChars": float(prompt_chars),
    }


def message_chars(body: dict[str, Any]) -> int:
    return sum(len(str(m.get("content") or "")) for m in body.get("messages") or [])


def message_text(body: dict[str, Any]) -> str:
    return "\n".join(str(m.get("content") or "") for m in body.get("messages") or [])


# ---------------- 基线对比 ----------------

def compare(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    """返回回退清单；空列表即「与基线一致」。方向规则与容差都在这里。"""
    regressions: list[str] = []
    for sample_id, base_metrics in baseline.items():
        now = current.get(sample_id)
        if not isinstance(now, dict):
            regressions.append(f"{sample_id} 本轮没有跑批结果")
            continue
        for metric, base_value in cast(dict[str, float], base_metrics).items():
            value = float(now.get(metric, 0.0))
            if metric in HIGHER_BETTER and base_value - value > RATE_TOL:
                regressions.append(
                    f"{sample_id}.{metric} 下降 {base_value:.4f} → {value:.4f}")
            elif metric in LOWER_BETTER:
                tol = COUNT_TOL if metric == "issueCount" else RATE_TOL
                if value - base_value > tol:
                    regressions.append(
                        f"{sample_id}.{metric} 上升 {base_value:.4f} → {value:.4f}")
            elif metric == "promptChars" and base_value > 0:
                drift = abs(value - base_value) / base_value
                if drift > PROMPT_DRIFT:
                    regressions.append(
                        f"{sample_id}.promptChars 体积漂移 {base_value:.0f} → {value:.0f}"
                        f"（{drift:.0%} > {PROMPT_DRIFT:.0%}）")
    for sample_id in current:
        if sample_id not in baseline:
            regressions.append(f"{sample_id} 是新样例，基线里没有：请跑 --update-baseline")
    return regressions


def report_lines(baseline: dict[str, Any], current: dict[str, Any]) -> list[str]:
    lines = ["能力/样例".ljust(30) + "".join(m.ljust(15) for m in (
        "通过率", "留存率", "完整率", "重复率", "问题数", "提示词字数"))]
    for sample_id, now in current.items():
        base = cast(dict[str, float], baseline.get(sample_id) or {})
        cells = []
        for metric in ("passRate", "retention", "completeness", "dupRate",
                       "issueCount", "promptChars"):
            value = float(now.get(metric, 0.0))
            delta = value - float(base.get(metric, value))
            mark = "" if abs(delta) < 1e-9 else f"{delta:+.2f}"
            cells.append(f"{value:.3f}{mark}".ljust(15))
        lines.append(sample_id.ljust(30) + "".join(cells))
    return lines


# ---------------- 跑批 ----------------

def build_app() -> tuple[Any, pathlib.Path, ThreadingHTTPServer]:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-promptreg-"))
    db_path = workdir / "promptreg.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "promptreg.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    server = start_mock()
    return create_app(cfg).test_client(), db_path, server


def data_of(client: Any, method: str, path: str,
            payload: dict[str, Any] | None = None) -> dict[str, Any]:
    if method == "GET":
        resp = client.get(path)
    else:
        resp = client.post(path, json=payload or {})
    body = cast(dict[str, Any], resp.get_json() or {})
    if int(resp.status_code) >= 400 or body.get("code") not in (0, 200000):
        raise AssertionError(f"{method} {path} 失败：{str(body)[:300]}")
    got = body.get("data")
    return cast(dict[str, Any], got) if isinstance(got, dict) else {"value": got}


def seed_dedup_baseline(db_path: pathlib.Path, samples: list[dict[str, Any]]) -> dict[str, int]:
    """给每个样例一个独立项目，并把 GAP-29 的比对基线种进库内。

    比较域按项目隔离，样例之间不会互相串味；否则 dupRate 会随样例跑批顺序变化，
    这条指标就没法进基线。
    """
    projects: dict[str, int] = {}
    stamp = "2026-11-01 00:00:00"
    conn = sqlite3.connect(db_path)
    try:
        for index, sample in enumerate(samples):
            sample_id = str(sample["id"])
            project_id = 7000 + index
            conn.execute(
                "INSERT INTO biz_project (id, code, name, created_at, updated_at, is_deleted)"
                " VALUES (?, ?, ?, ?, ?, 0)",
                (project_id, f"PRG{index:03d}", f"提示词回归项目 {sample_id}", stamp, stamp),
            )
            for case_index, seed in enumerate(DEDUP_SEEDS.get(sample_id) or []):
                conn.execute(
                    "INSERT INTO biz_test_case"
                    " (project_id, case_no, title, case_type, preconditions, steps_json,"
                    " expected_result, created_at, updated_at, is_deleted)"
                    " VALUES (?, ?, ?, ?, '', '[]', '', ?, ?, 0)",
                    (project_id, f"PRG-C{index:02d}-{case_index}", str(seed["title"]),
                     str(seed["caseType"]), stamp, stamp),
                )
            projects[sample_id] = project_id
        conn.commit()
    finally:
        conn.close()
    return projects


def run_samples(samples: list[dict[str, Any]]) -> dict[str, dict[str, float]]:
    """每个样例一份档案（model 名 = 样例 id），mock 按 model 名回吐对应 fixture。"""
    global REPLIES
    client, db_path, server = build_app()
    projects = seed_dedup_baseline(db_path, samples)
    base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    REPLIES = {
        str(item["id"]): (
            item["fixture"]
            if isinstance(item["fixture"], str)
            else json.dumps(item["fixture"], ensure_ascii=False)
        )
        for item in samples
    }
    metrics: dict[str, dict[str, float]] = {}
    try:
        for index, sample in enumerate(samples):
            sample_id = str(sample["id"])
            capability = str(sample["capability"])
            profile = data_of(client, "POST", f"{V1}/settings/ai/profiles", {
                "name": f"提示词回归档案 {sample_id}",
                "provider": "custom",
                "protocol": "openai_compat",
                "baseUrl": base_url,
                "model": sample_id,
                "isLocal": True,
                "isActive": True,
                "priority": 200 + index,
                "timeout": 10,
            })
            run = data_of(client, "POST", f"{V1}/lab/{capability}/run", {
                "variables": sample["variables"],
                "profileId": int(profile["id"]),
                "title": str(sample.get("title") or sample_id),
                # 比较域与去重模式都显式给定：指标不能随工装库里的系统配置漂移
                "projectId": projects[sample_id],
                "dedupMode": "mark",
            })
            sent = CAPTURED.get(sample_id) or [{}]
            last_sent = sent[-1]
            chars = message_chars(last_sent)
            metrics[sample_id] = metrics_of(run, chars)
            metrics[sample_id]["_promptHasInput"] = float(
                MUST_APPEAR.get(capability, "") in message_text(last_sent)
            )
            metrics[sample_id]["_placeholderLeft"] = float("{{" in message_text(last_sent))
            dedup = cast(dict[str, Any], run.get("dedup") or {})
            metrics[sample_id]["_existingTotal"] = float(dedup.get("existingTotal") or 0)
            metrics[sample_id]["_hitCount"] = float(dedup.get("hitCount") or 0)
            controls = set(CONTROL_TITLES.get(sample_id) or [])
            metrics[sample_id]["_controlFalseHit"] = float(any(
                str(item.get("matchedTitle") or "") in controls
                for item in dedup.get("items") or []
            ))
            if sample_id in DEDUP_SEEDS:
                # GAP-29 的「同一需求重复生成两次」：产出与库内基线同源，二次生成即命中。
                # 两种处置各跑一次，比对的是同一批基线、同一套判定，只有摘不摘不同。
                body = {
                    "variables": sample["variables"],
                    "profileId": int(profile["id"]),
                    "title": str(sample.get("title") or sample_id),
                    "projectId": projects[sample_id],
                }
                inc = data_of(client, "POST", f"{V1}/lab/{capability}/run",
                              {**body, "dedupMode": dedup_service.MODE_INCREMENT})
                inc_dedup = cast(dict[str, Any], inc.get("dedup") or {})
                metrics[sample_id]["_abMarkFlow"] = float(dedup.get("hitCount") or 0)
                metrics[sample_id]["_abIncFlow"] = float(inc_dedup.get("suspectCount") or 0)
                metrics[sample_id]["_abDropped"] = float(inc_dedup.get("droppedCount") or 0)
                metrics[sample_id]["_abCompared"] = float(inc_dedup.get("compared") or 0)
                # 界面勾选的是收尾后的产出：摘走的条目必须真的从条数里掉下去
                metrics[sample_id]["_abMarkRows"] = float(len(run.get("parsed") or []))
                metrics[sample_id]["_abIncRows"] = float(len(inc.get("parsed") or []))
                # 判定必须逐条一致：下降只能来自处置，不能来自「第二次判得松」
                metrics[sample_id]["_abSameVerdict"] = float(
                    [(str(i.get("title")), i.get("similarity"))
                     for i in dedup.get("items") or []]
                    == [(str(i.get("title")), i.get("similarity"))
                        for i in inc_dedup.get("items") or []]
                )
    finally:
        server.shutdown()
    return metrics


# ---------------- GAP-29 二次生成对照 ----------------

def flow_dropped_every_pair(pairs: dict[str, tuple[float, float]]) -> bool:
    """每个样例的「流入采纳清单的重复条数」是否都真的下降了（摘走数为 0 的样例不算下降）。"""
    return all(inc < mark for mark, inc in pairs.values())


# ---------------- 主流程 ----------------

def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-baseline", action="store_true",
                        help="把本轮指标写回 baseline.json（新增样例或确认口径变化后用）")
    parser.add_argument("--show", action="store_true", help="打印对比报告表")
    args = parser.parse_args()

    payload = cast(dict[str, Any], json.loads(SAMPLES_PATH.read_text(encoding="utf-8")))
    samples = cast(list[dict[str, Any]], payload.get("samples") or [])
    ids = [str(item["id"]) for item in samples]

    check("样例集非空", len(samples) >= 4, f"samples={len(samples)}")
    check("样例 id 不重复", len(set(ids)) == len(ids), str(ids))
    covered = {str(item["capability"]) for item in samples}
    schema_caps = set(output_schema.schema_capabilities())
    check("每个有 schema 的能力都有样例", schema_caps <= covered,
          f"缺样例={sorted(schema_caps - covered)}")
    # 正控：覆盖规则必须能因「新增一个 schema 能力却没有样例」而开火
    check("覆盖规则能命中漏样例的能力",
          not (schema_caps | {"interface_test_design"}) <= covered,
          "凭空加一个能力后覆盖规则仍未报缺样例 = 规则空转")
    check("每个样例都带 fixture 与 variables",
          all(item.get("fixture") and item.get("variables") for item in samples), str(ids))

    metrics = run_samples(samples)
    plain = {
        sample_id: {k: v for k, v in data.items() if not k.startswith("_")}
        for sample_id, data in metrics.items()
    }

    check("全部样例都跑出了指标", set(plain) == set(ids),
          f"缺={sorted(set(ids) - set(plain))}")
    check("装配后的提示词真的送达了模型输入",
          all(float(m.get("_promptHasInput") or 0) == 1.0 for m in metrics.values()),
          str({k: v.get("_promptHasInput") for k, v in metrics.items()}))
    check("提示词没有留下未替换的占位符",
          all(float(m.get("_placeholderLeft") or 0) == 0.0 for m in metrics.values()),
          str({k: v.get("_placeholderLeft") for k, v in metrics.items()}))

    typical = plain.get("case_login_typical") or {}
    defective = plain.get("case_login_defective") or {}
    check("典型产出通过率高于缺陷产出（说明 schema 真在判，而不是恒过）",
          float(typical.get("passRate") or 0) > float(defective.get("passRate") or 0),
          f"typical={typical} defective={defective}")
    check("典型产出字段完整率不低于缺陷产出",
          float(typical.get("completeness") or 0) >= float(defective.get("completeness") or 0),
          f"typical={typical} defective={defective}")
    check("缺陷产出确实产生了 schema 问题",
          float(defective.get("issueCount") or 0) > 0, str(defective))

    # GAP-29 的重复率必须是有内容的数：恒 0 说明比对没做成，全 1 说明比对在乱判
    hit_counts = {sid: float(m.get("_hitCount") or 0) for sid, m in metrics.items()}
    check("去重比对确实从库内召回了基线用例",
          all(float(metrics[sid].get("_existingTotal") or 0) >= len(seeds)
              for sid, seeds in DEDUP_SEEDS.items()),
          str({sid: metrics[sid].get("_existingTotal") for sid in DEDUP_SEEDS}))
    check("只有同名条目被判重复（判重数与预置基线条数一致）",
          all(hit_counts.get(sid) == float(EXPECTED_DUP_HITS[sid])
              for sid in DEDUP_SEEDS),
          str({sid: (hit_counts.get(sid), EXPECTED_DUP_HITS[sid]) for sid in DEDUP_SEEDS}))
    check("同类型的对照用例没有被误判",
          all(float(m.get("_controlFalseHit") or 0) == 0.0 for m in metrics.values()),
          str({sid: hit_counts.get(sid) for sid in DEDUP_SEEDS}))
    check("未预置基线的样例重复率为 0（说明判定依赖比对域，不是恒真）",
          all(float(m.get("dupRate") or 0) == 0.0
              for sid, m in plain.items() if sid not in DEDUP_SEEDS),
          str({sid: m.get("dupRate") for sid, m in plain.items() if sid not in DEDUP_SEEDS}))
    check("纳入去重口径的样例重复率大于 0",
          all(float(m.get("dupRate") or 0) > RATE_TOL for sid, m in plain.items()
              if sid in DEDUP_SEEDS),
          str({sid: m.get("dupRate") for sid, m in plain.items() if sid in DEDUP_SEEDS}))

    # GAP-29 验收：「同一需求重复生成两次，重复条数显著下降」必须有绝对数，不能只看重复率
    ab = {sid: m for sid, m in metrics.items() if "_abMarkFlow" in m}
    check("二次生成对照覆盖全部预置基线样例", set(ab) == set(DEDUP_SEEDS),
          f"对照={sorted(ab)} 应有={sorted(DEDUP_SEEDS)}")
    flow_pair = {sid: (float(m["_abMarkFlow"]), float(m["_abIncFlow"])) for sid, m in ab.items()}
    check("不摘走的处置下命中的重复条数全部流入采纳清单",
          all(mark == float(EXPECTED_DUP_HITS[sid]) and mark > 0
              for sid, (mark, _inc) in flow_pair.items()),
          str(flow_pair))
    check("改成摘走后流入的重复条数每个样例都下降", flow_dropped_every_pair(flow_pair),
          str(flow_pair))
    check("下降对照规则能开火（正控：没摘走与摘更多都必须判不下降）",
          bool(flow_pair) and not flow_dropped_every_pair({"x": (3.0, 3.0)})
          and not flow_dropped_every_pair({"x": (3.0, 4.0)}),
          "把不下降当成下降 = 规则空转")
    ab_mark = sum(m["_abMarkFlow"] for m in ab.values())
    ab_inc = sum(m["_abIncFlow"] for m in ab.values())
    ab_drop = sum(m["_abDropped"] for m in ab.values())
    check("下降比例有绝对数支撑（摘走数 = 两模式流入差）",
          ab_mark > 0 and ab_drop == ab_mark - ab_inc and ab_inc < ab_mark,
          f"流入 {ab_inc:.0f} 条 / 原 {ab_mark:.0f} 条（摘走 {ab_drop:.0f} 条，"
          f"下降 {(ab_mark - ab_inc) / ab_mark:.0%}）")
    check("摘走的只有确定重复（疑似档仍流入，说明精准下降而非一刀切）",
          all(m["_abIncFlow"] == float(EXPECTED_SUSPECT_HITS[sid])
              and m["_abDropped"] == float(EXPECTED_EXACT_HITS[sid])
              for sid, m in ab.items()),
          str({sid: (m["_abDropped"], m["_abIncFlow"], EXPECTED_EXACT_HITS[sid],
                     EXPECTED_SUSPECT_HITS[sid]) for sid, m in ab.items()}))
    check("两次跑的逐条判定一致（下降来自处置，不来自判定口径变化）",
          all(m["_abSameVerdict"] == 1.0 for m in ab.values()),
          str({sid: m["_abSameVerdict"] for sid, m in ab.items()}))
    check("摘走后的产出条数与比对条数对得上（界面勾选的就是剩下的）",
          all(m["_abMarkRows"] - m["_abDropped"] == m["_abIncRows"]
              and m["_abCompared"] == m["_abMarkRows"] > 0
              for m in ab.values()),
          str({sid: (m["_abMarkRows"], m["_abDropped"], m["_abIncRows"], m["_abCompared"])
               for sid, m in ab.items()}))

    baseline_raw: dict[str, Any] = {}
    if BASELINE_PATH.is_file():
        baseline_raw = cast(dict[str, Any], json.loads(BASELINE_PATH.read_text(encoding="utf-8")))
    baseline = cast(dict[str, Any], baseline_raw.get("samples") or {})

    if args.update_baseline:
        BASELINE_PATH.write_text(json.dumps({
            "version": int(baseline_raw.get("version") or 1),
            "说明": "由 check_prompt_regression.py --update-baseline 生成；"
                  "指标含义见该脚本 docstring。改动提示词后若此处出现回退，先确认是有意为之再重录。",
            "samples": plain,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        check("基线已重新记录", True)
        check("基线覆盖全部样例", set(plain) == set(ids), str(ids))
    else:
        check("基线文件存在且覆盖全部样例",
              bool(baseline) and set(baseline) == set(ids),
              f"基线={sorted(baseline)} 样例={sorted(ids)}")
        diffs = compare(baseline, plain)
        check("本轮指标与基线一致（提示词/装配/校验/去重无回退）", not diffs, "; ".join(diffs[:6]))
        # 正控：三条方向规则都必须真能开火，否则「与基线一致」是空断言
        control_pass = {k: dict(v) for k, v in baseline.items()}
        for sample_id in list(control_pass)[:1]:
            control_pass[sample_id]["passRate"] = float(
                control_pass[sample_id].get("passRate") or 0) + 0.5
        check("通过率回退能被比出来（正控）", bool(compare(control_pass, plain)),
              "把基线通过率抬高 0.5 后仍未报回退 = 规则空转")
        control_dup = {k: dict(v) for k, v in baseline.items()}
        for sample_id in list(control_dup)[:1]:
            control_dup[sample_id]["dupRate"] = max(
                0.0, float(control_dup[sample_id].get("dupRate") or 0) - 0.5)
        check("重复率上升能被比出来（正控）", bool(compare(control_dup, plain)))
        control_chars = {k: dict(v) for k, v in baseline.items()}
        for sample_id in list(control_chars)[:1]:
            control_chars[sample_id]["promptChars"] = float(
                control_chars[sample_id].get("promptChars") or 0) * 2
        check("提示词体积漂移能被比出来（正控）", bool(compare(control_chars, plain)))
        missing = compare(baseline, {k: v for k, v in plain.items() if k not in baseline})
        check("新增样例未录基线会被拦下（正控）", bool(missing))

    if args.show or not args.update_baseline:
        print("-" * 40)
        for line in report_lines(baseline, plain):
            print(line)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
