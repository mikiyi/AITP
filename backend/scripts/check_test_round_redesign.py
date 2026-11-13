"""验证测试轮次业务化一期（类型 / 计划时间窗 / 范围预设 / 4-3 轮模板）。

    python backend/scripts/check_test_round_redesign.py

全部在临时库运行，不读写正式数据。覆盖：
- 迁移 057 字段、round-meta 元数据；
- 范围预设圈选（P1 主流程 / 全部迭代流程优先 / 回归 P1+P2 / 上轮 fail+关联+异常兼容）；
- 按计划发起轮次带类型/目标/计划时间窗；
- 4/3 轮模板生成（pending + 顺序号 + 连续计划时间窗）；
- 专项轮「按上轮结果刷新范围」；已完成/执行中轮次拒绝刷新。
门禁联动与 checklist 为二期，不在本门禁。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import applied_versions, init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402

JsonDict = dict[str, Any]
V1 = "/api/v1"
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[Any, JsonDict]:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True) or {"code": -1, "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} 失败: {body.get('code')} {body.get('message')}")
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None) -> JsonDict:
        _, body = self.call(method, path, payload)
        check(name, body.get("code") == code, f"code={body.get('code')} msg={body.get('message')}")
        return body


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "round.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8773, debug=False, db_path=db_path,
        log_path=workdir / "round.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-round-"))
    app = build_app(workdir)
    api = Api(app)
    conn = sqlite3.connect(workdir / "round.db")
    conn.row_factory = sqlite3.Row

    check("迁移 057 已应用", 57 in applied_versions(conn))
    cols = {r[1] for r in conn.execute("PRAGMA table_info(biz_test_run)")}
    check("轮次新增字段齐全",
          {"round_type", "goal", "planned_start", "planned_end", "scope_preset", "seq"} <= cols,
          str(sorted(cols)))

    meta = api.data("GET", f"{V1}/test-runs/round-meta")
    check("round-meta 四类类型", [t["value"] for t in meta["types"]] == ["smoke", "full", "special", "regression"], str(meta["types"]))
    check("round-meta 含 4/3 轮模板", {t["value"] for t in meta["templates"]} == {"four", "three"}, str(meta["templates"]))
    check("模板轮数 4 与 3", {t["value"]: t["rounds"] for t in meta["templates"]} == {"four": 4, "three": 3}, str(meta["templates"]))

    project = api.data("POST", f"{V1}/projects", {"code": "ROUND", "name": "轮次改造项目"})
    pid = int(project["id"])
    version = api.data("POST", f"{V1}/project-versions", {"projectId": pid, "versionCode": "V2.0.0", "name": "2.0 迭代"})
    vid = int(version["id"])
    module = api.data("POST", f"{V1}/modules", {"projectId": pid, "name": "支付", "nodeType": "module"})
    mid = int(module["id"])

    def case(title: str, priority: str, ctype: str, tags: str = "") -> int:
        row = api.data("POST", f"{V1}/test-cases", {
            "projectId": pid, "versionId": vid, "moduleId": mid, "title": title,
            "priority": priority, "caseType": ctype, "tags": tags,
        })
        return int(row["id"])

    c_p1_flow = case("下单主流程", "P1", "functional")
    c_p1_flow2 = case("支付主流程", "P1", "functional")
    c_p2_flow = case("退款流程", "P2", "functional")
    c_p3_val = case("金额校验", "P3", "boundary")
    c_exc = case("支付超时异常", "P2", "exception")
    c_compat = case("浏览器兼容", "P2", "ui", tags="兼容")
    c_other_ver = case("旧版本用例", "P1", "functional")  # 不挂本版本
    conn.execute("UPDATE biz_test_case SET version_id=NULL WHERE id=?", (c_other_ver,))
    conn.commit()

    plan = api.data("POST", f"{V1}/test-plans", {"projectId": pid, "versionId": vid, "name": "V2.0 迭代测试计划"})
    plan_id = int(plan["id"])

    # 范围预设圈选
    smoke = api.data("POST", f"{V1}/test-plans/{plan_id}/rounds/preview-scope", {"preset": "smoke_p1"})
    check("冒烟预设仅 P1 主流程", set(smoke["caseIds"]) == {c_p1_flow, c_p1_flow2}, str(smoke["caseIds"]))
    full = api.data("POST", f"{V1}/test-plans/{plan_id}/rounds/preview-scope", {"preset": "version_all"})
    check("全覆盖预设含本版本全部且不含旧版本",
          set(full["caseIds"]) == {c_p1_flow, c_p1_flow2, c_p2_flow, c_p3_val, c_exc, c_compat},
          str(full["caseIds"]))
    check("全覆盖流程性用例排前", full["caseIds"][0] in (c_p1_flow, c_p1_flow2), str(full["caseIds"]))
    reg = api.data("POST", f"{V1}/test-plans/{plan_id}/rounds/preview-scope", {"preset": "regression_p1p2"})
    check("回归预设 P1+P2", set(reg["caseIds"]) == {c_p1_flow, c_p1_flow2, c_p2_flow, c_exc, c_compat}, str(reg["caseIds"]))

    # 按计划发起冒烟轮（带类型/目标/计划时间窗）
    run1 = api.data("POST", f"{V1}/test-plans/{plan_id}/runs", {
        "name": "第1轮 · 冒烟测试", "roundType": "smoke", "seq": 1,
        "plannedStart": "2026-11-02", "plannedEnd": "2026-11-03",
    })
    r1 = int(run1["id"])
    check("冒烟轮类型/目标落库", run1["roundType"] == "smoke" and "可不可测" in run1["goal"], str(run1.get("goal")))
    check("冒烟轮计划时间窗落库", run1["plannedStart"] == "2026-11-02" and run1["plannedEnd"] == "2026-11-03", str(run1))
    check("冒烟轮用例=预设圈选", run1["total"] == 2, str(run1["total"]))

    # 4 轮模板生成
    tpl = api.data("POST", f"{V1}/test-plans/{plan_id}/rounds/from-template", {
        "template": "four", "startDate": "2026-11-02",
    })
    runs = tpl["runs"]
    check("4 轮模板生成 4 轮", len(runs) == 4, str(len(runs)))
    check("模板轮次均为待开始", all(r["status"] == "pending" for r in runs), str([r["status"] for r in runs]))
    check("模板类型序列正确", [r["roundType"] for r in runs] == ["smoke", "full", "special", "regression"], str([r["roundType"] for r in runs]))
    check("模板顺序号递增", [r["seq"] for r in runs] == [2, 3, 4, 5], str([r["seq"] for r in runs]))
    check("模板计划时间窗连续", runs[0]["plannedStart"] == "2026-11-02" and runs[1]["plannedStart"] == "2026-11-04" and runs[3]["plannedEnd"] == "2026-11-15", str([(r["plannedStart"], r["plannedEnd"]) for r in runs]))
    special = runs[2]
    check("专项轮初始含异常/兼容", special["total"] >= 2, str(special["total"]))

    # 在全覆盖轮（模板第2轮）录一条 fail，再刷新专项轮范围
    full_run = runs[1]
    fr_id = int(full_run["id"])
    # 找全覆盖轮里的一条用例记 fail
    detail = api.data("GET", f"{V1}/test-runs/{fr_id}")
    target_case = detail["cases"][0]
    api.data("PUT", f"{V1}/test-runs/{fr_id}/cases/{target_case['id']}", {"result": "failed"})
    refreshed = api.data("POST", f"{V1}/test-runs/{special['id']}/refresh-scope")
    check("刷新范围带上上轮 fail", refreshed["count"] >= special["total"], str(refreshed))
    sp_detail = api.data("GET", f"{V1}/test-runs/{special['id']}")
    sp_case_ids = {c["caseId"] for c in sp_detail["cases"]}
    check("专项轮含上轮 fail 用例", target_case["caseId"] in sp_case_ids, str(sp_case_ids))

    # 3 轮模板
    tpl3 = api.data("POST", f"{V1}/test-plans/{plan_id}/rounds/from-template", {"template": "three", "startDate": "2026-12-01"})
    check("3 轮模板生成 3 轮", len(tpl3["runs"]) == 3, str(len(tpl3["runs"])))
    check("3 轮第二轮为合并目标", "验证已修" in tpl3["runs"][1]["goal"], str(tpl3["runs"][1]["goal"]))

    # 执行中轮次拒绝刷新范围
    api.expect("执行中轮次拒绝刷新范围", "POST", f"{V1}/test-runs/{r1}/refresh-scope", ErrorCode.ILLEGAL_OPERATION)
    # 未知预设拒绝
    api.expect("未知预设拒绝", "POST", f"{V1}/test-plans/{plan_id}/rounds/preview-scope", ErrorCode.PARAM_ERROR, {"preset": "nope"})
    # 未知模板拒绝
    api.expect("未知模板拒绝", "POST", f"{V1}/test-plans/{plan_id}/rounds/from-template", ErrorCode.PARAM_ERROR, {"template": "five", "startDate": "2026-11-02"})

    # 更新轮次类型/时间窗
    upd = api.data("PUT", f"{V1}/test-runs/{r1}", {"roundType": "regression", "plannedStart": "2026-11-10", "plannedEnd": "2026-11-13"})
    check("更新轮次类型/时间窗", upd["roundType"] == "regression" and upd["plannedEnd"] == "2026-11-13", str(upd))

    conn.close()
    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())