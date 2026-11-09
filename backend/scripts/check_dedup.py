"""GAP-29 生成去重与增量：单元口径确定性门禁。

盯的是**逐条判定口径**本身。`check_prompt_regression.py`（GAP-31）只看 `dupRate`
这一个汇总指标有没有回退，它回答不了「这一条到底该落在哪一档、该不该被摘走、
返回体里的下标能不能信」。本门禁把这些口径钉死，判据全部来自
`aitp/services/dedup_service.py`：

  1. 归一化（全角/标点/空白/大小写）不影响相似判定，语义不同必须低于疑似阈值；
  2. 分档：`<0.85` 不判重、`[0.85,0.95)` 只算疑似、`≥0.95` 算确定重复；
     对照条目（同类型不同名）必须一条都不判，否则「判出重复」等于全都判；
  3. 比较域分桶不串味：跨用例类型不判重。两处已确认的代码口径照代码为准——
     ① `regression_generation` 的「类型」维度取 `priority` 而不是 `caseType`
     （见 `comparable_of` 的 else 分支）；
     ② 比对基线只能由 `existing_cases()` 从 `biz_test_case` 表按项目召回；
  4. 模式优先级：调用方显式 `dedupMode` > 功能点绑定 > 系统配置 `ai.dedup.mode`
     > 默认 `mark`，非法取值回落默认且不崩；
  5. `mark` 永不丢条目、只打标记；`increment` 必须真的摘走确定重复，
     且 `droppedCount` 与 `dropped` 明细一致；
  6. `DedupReport.as_dict()` 口径自洽：未摘走的命中 `items[].index` 就是产出数组下标
     （前端据此把疑似重复项默认不勾选）、`suspectCount == 未摘走的命中数`、
     超出 `MAX_ITEMS_IN_PAYLOAD` 只截明细不截计数；
  7. `existing_cases` 的召回限量 `MAX_EXISTING` 与按项目隔离（跨项目基线不得互串）；
  8. `baseline_directive` 注入提示词的文本非空、限量、且基线确实出现在指令里；
  9. 前端真的消费这套结论：`采纳为用例` 弹窗把疑似重复默认不勾选、给一键恢复，
     提交的是勾选集而不是全量（GAP-29 的「标记 ≠ 删除」只在界面上才成立）。

跑的是系统临时目录里的独立 sqlite 库（`init_db` 建表）+ service 层直调：
不起服务、不占端口、不碰 `data/aitp.db`、不调任何大模型（HTTP 链路由 GAP-31 覆盖）。

    python backend/scripts/check_dedup.py

退出码：FAIL=0 为 0，任何 FAIL 为 1。
"""

from __future__ import annotations

import json
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.db.migrator import init_db  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services import dedup_service as dedup  # noqa: E402

JsonDict = dict[str, Any]

SRC = ROOT / "frontend" / "src"
CAPABILITY_VUE = SRC / "views" / "lab" / "CapabilityView.vue"
TYPES_TS = SRC / "api" / "types.ts"
#: 「疑似重复默认不勾选」的唯一落点：勾选的是非命中行。改名或换成恒 true 都要红。
DEFAULT_UNCHECK_RE = re.compile(r"toggleRowSelection\(\s*row\s*,\s*!\s*row\.dupHit\s*\)")
#: 前端类型必须钉住的后端 dedup 契约字段（漏一个 = 界面与结论失联）
DEDUP_CONTRACT_FIELDS = ("hitCount", "suspectCount", "droppedCount", "restorable", "dropped")

#: 门禁侧写死的口径常量：服务层把阈值改了，这里必须红，而不是跟着一起改
SUSPECT_AT = 0.85
EXACT_AT = 0.95
EXISTING_CAP = 60
PAYLOAD_CAP = 60

#: 分档测试向量：标题相似度分别落在「确定重复 / 疑似重复 / 不判重」三档
#: 0.96 档（归一后不相等，只能靠阈值判 exact）
PAIR_EXACT = ("支付回调重复通知幂等校验", "支付回调重复通知幂等性校验")
#: 0.9231 档（只到疑似，绝不许摘走）
PAIR_SUSPECT = ("优惠券叠加下单金额计算正确", "优惠券叠加下单金额算得正确")
#: 标题毫不相干（综合分只由正文撑到 0.42，必须不判重）
PAIR_CONTROL = ("登录限流窗口边界", "导出报表表头顺序字段名一致")
#: 归一后完全同名（走「标题相同即确定重复」分支）
TITLE_SAME = "手机号验证码登录成功"

STAMP = "2026-11-01 00:00:00"
DEFAULT_SCOPE: JsonDict = {"projectId": 9001, "caseTypes": ["functional"]}

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        return
    _failed += 1
    print(f"[FAIL] {name}  {detail}", flush=True)


def info(message: str) -> None:
    print(f"[DATA] {message}", flush=True)


# ---------------- 构造产出条目 ----------------

def case_item(title: str, tag: str, case_type: str = "functional") -> JsonDict:
    """一条用例产出：正文只由 tag 决定，保证不同 tag 的正文相似度撑不出误判。"""
    return {
        "title": title,
        "caseType": case_type,
        "preconditions": f"前置条件{tag}",
        "steps": [{"action": f"执行步骤{tag}并观察", "expected": f"预期结果{tag}出现"}],
        "expectedResult": f"最终结果{tag}",
    }


def xmind_item(title: str, tag: str, case_type: str = "functional") -> JsonDict:
    """xmind_to_case 的产出形状与用例生成同域（comparable_of 走同一分支）。"""
    return case_item(title, tag, case_type)


def regression_item(title: str, tag: str, priority: str = "P0",
                    case_type: str | None = "functional") -> JsonDict:
    """回归点产出：可比串取 module/reason/verifyTarget，类型维度取 priority。

    `case_type=None` 模拟模型没写 caseType 的常见形态：`dedup_output` 的 SQL 类型
    过滤只看 caseType，判定分桶看的却是 priority（口径①），两者不对齐时召回域会先
    被 caseType 收窄——这个推论也要有断言盯着。
    """
    item: JsonDict = {
        "title": title,
        "priority": priority,
        "module": f"模块{tag}",
        "reason": f"变更原因{tag}",
        "verifyTarget": f"验证目标{tag}",
    }
    if case_type is not None:
        item["caseType"] = case_type
    return item


def title_of(item: Any) -> str:
    return str(cast(JsonDict, item).get("title") or "")


def run_items(capability: str, items: list[Any], existing: list[dedup.ComparableCase],
              mode: str, scope: JsonDict | None = None) -> tuple[list[Any], JsonDict]:
    kept, report = dedup.dedup_items(
        capability, items, existing, mode=mode,
        scope=scope if scope is not None else DEFAULT_SCOPE,
    )
    return kept, report.as_dict()


def hits_of(payload: JsonDict) -> list[JsonDict]:
    return [cast(JsonDict, hit) for hit in payload.get("items") or []]


def existing_case_of(title: str, tag: str, case_type: str = "functional") -> dedup.ComparableCase:
    """一条库内基线（形如 existing_cases 召回出来的 ComparableCase）。"""
    comparable = cast(
        dedup.ComparableCase,
        dedup.comparable_of("case_generation", case_item(title, tag, case_type), 0),
    )
    comparable.ref = "TC-9001"
    comparable.index = None
    return comparable


def band_of(title_a: str, title_b: str, tag_a: str, tag_b: str) -> float:
    """两个标题在真实产出形状下的综合相似度（门禁向量的自检入口）。"""
    one = cast(dedup.ComparableCase,
               dedup.comparable_of("case_generation", case_item(title_a, tag_a), 0))
    other = cast(dedup.ComparableCase,
                 dedup.comparable_of("case_generation", case_item(title_b, tag_b), 1))
    return dedup.similarity(one, other)


# ---------------- 库内种子 ----------------

def seed_project(conn: sqlite3.Connection, project_id: int, name: str) -> None:
    conn.execute(
        "INSERT INTO biz_project (id, code, name, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, 0)",
        (project_id, f"DUP{project_id}", name, STAMP, STAMP),
    )


def seed_case(conn: sqlite3.Connection, *, project_id: int, case_no: str, title: str,
              case_type: str = "functional", module_id: int | None = None,
              requirement_id: int | None = None, step_action: str = "",
              expected: str = "", deleted: bool = False,
              updated_at: str = STAMP) -> None:
    steps = [{"action": step_action, "expected": expected}] if step_action else []
    conn.execute(
        "INSERT INTO biz_test_case"
        " (project_id, requirement_id, module_id, case_no, title, case_type,"
        "  preconditions, steps_json, expected_result, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, ?, '', ?, ?, ?, ?, ?)",
        (project_id, requirement_id, module_id, case_no, title, case_type,
         json.dumps(steps, ensure_ascii=False), expected, updated_at, updated_at,
         1 if deleted else 0),
    )


def set_config(conn: sqlite3.Connection, value: str | None) -> None:
    """写 / 删系统配置里的去重档位（键名口径与 047 迁移种子一致）。"""
    key = dedup.CONFIG_MODE_KEY
    if value is None:
        conn.execute("DELETE FROM sys_config WHERE config_key = ?", (key,))
    else:
        SysConfigRepository(conn).set_value(key, value)
    conn.commit()


def align_errors(kept: list[Any], payload: JsonDict) -> list[str]:
    """未摘走的命中必须能用 index 直接在产出数组里取到同一条（默认不勾选的依据）。"""
    bad: list[str] = []
    for hit in hits_of(payload):
        if hit.get("dropped"):
            continue
        index = int(cast(Any, hit.get("index")))
        if not 0 <= index < len(kept):
            bad.append(f"index={index} 越界（产出只有 {len(kept)} 条）title={hit.get('title')}")
        elif title_of(kept[index]) != str(hit.get("title")):
            bad.append(f"index={index} 指向「{title_of(kept[index])}」而非「{hit.get('title')}」")
    return bad


def ref_errors(kept: list[Any], payload: JsonDict) -> list[str]:
    """本批次命中的 matchedRef 也要落在同一套下标里，否则界面高亮的是别的行。"""
    bad: list[str] = []
    for hit in hits_of(payload):
        if hit.get("source") != "batch" or hit.get("dropped"):
            continue
        ref = str(hit.get("matchedRef") or "")
        if not (ref.startswith("[") and ref.endswith("]")):
            bad.append(f"matchedRef={ref} 不是下标形态")
            continue
        try:
            position = int(ref[1:-1])
        except ValueError:
            bad.append(f"matchedRef={ref} 下标解析失败")
            continue
        if not 0 <= position < len(kept) or title_of(kept[position]) != str(hit.get("matchedTitle")):
            bad.append(f"matchedRef={ref} 取不到「{hit.get('matchedTitle')}」")
    return bad


# ---------------- 断言面 1：归一化 ----------------

def section_normalize() -> None:
    check("阈值与限量常量仍等于口径值",
          (dedup.SUSPECT_THRESHOLD, dedup.EXACT_THRESHOLD,
           dedup.MAX_EXISTING, dedup.MAX_ITEMS_IN_PAYLOAD)
          == (SUSPECT_AT, EXACT_AT, EXISTING_CAP, PAYLOAD_CAP),
          f"实际={dedup.SUSPECT_THRESHOLD}/{dedup.EXACT_THRESHOLD}/"
          f"{dedup.MAX_EXISTING}/{dedup.MAX_ITEMS_IN_PAYLOAD}")
    check("全角标点/全角字母数字/全角空白归一到同一串",
          dedup.normalize_text("支付（Ｖ２）　登录！") == dedup.normalize_text("支付(V2)登录"),
          repr(dedup.normalize_text("支付（Ｖ２）　登录！")))
    check("中英文标点与大小写一并抹平",
          dedup.normalize_text("Login, 超时. ！") == dedup.normalize_text("login超时")
          == "login超时")
    check("None 与空串都归一为空串",
          dedup.normalize_text(None) == "" and dedup.normalize_text("") == "")
    check("归一化不伤纯中文（不是把所有字符都删掉）",
          dedup.normalize_text("登录超时") == "登录超时")
    fullwidth_sim = band_of("支付（Ｖ２）　登录！", "支付(V2)登录", "甲", "甲")
    check("全角/半角写法差异不影响相似判定（判到 1.0）",
          fullwidth_sim == 1.0, f"sim={fullwidth_sim}")
    different_sim = band_of("登录限流窗口边界", "优惠券叠加退款金额校验", "乙", "丙")
    check("语义完全不同时相似度低于 SUSPECT_THRESHOLD",
          different_sim < SUSPECT_AT, f"sim={different_sim}")
    shared_bigram = dedup._ratio("登录超时重发", "超时重发降级")
    check("中文二字组让共享片段的文本拿到非零相似度",
          shared_bigram > 0.0, f"ratio={shared_bigram}")
    check("二字组没把中文压成人人相同的字袋（不同片段区分得开）",
          dedup._ratio("登录超时重发", "支付退款对账") < shared_bigram,
          f"{dedup._ratio('登录超时重发', '支付退款对账')} 不小于 {shared_bigram}")
    check("_ratio 对空串/单空/同串的处理",
          dedup._ratio("", "") == 0.0 and dedup._ratio("a", "") == 0.0
          and dedup._ratio("a", "a") == 1.0)


# ---------------- 断言面 2：判重分档 ----------------

def section_bands() -> None:
    exact_sim = band_of(*PAIR_EXACT, "丁", "戊")
    suspect_sim = band_of(*PAIR_SUSPECT, "己", "庚")
    control_sim = band_of(*PAIR_CONTROL, "辛", "壬")
    info(f"向量综合相似度 exact={exact_sim:.4f} suspect={suspect_sim:.4f} "
         f"control={control_sim:.4f}（阈值 {SUSPECT_AT}/{EXACT_AT}）")
    check("三档测试向量确实落在各自的档位里（向量失效应立刻判红）",
          exact_sim >= EXACT_AT and SUSPECT_AT <= suspect_sim < EXACT_AT
          and control_sim < SUSPECT_AT,
          f"exact={exact_sim} suspect={suspect_sim} control={control_sim}")

    kept, payload = run_items(
        "case_generation", [case_item(TITLE_SAME, "癸"), case_item(TITLE_SAME, "子")],
        [], "mark")
    same_title_hits = [h for h in hits_of(payload) if int(cast(Any, h.get("index"))) == 1]
    check("同名条目判重（命中一条，来源本批次，分数达到确定档）",
          len(same_title_hits) == 1 and same_title_hits[0].get("source") == "batch"
          and float(cast(Any, same_title_hits[0].get("similarity"))) >= EXACT_AT
          and "完全相同" in str(same_title_hits[0].get("reason")),
          str(hits_of(payload)))
    check("确定重复在 mark 模式下不摘走（标记模式一条都不丢）",
          payload.get("droppedCount") == 0 and len(kept) == 2, str(payload.get("dropped")))

    kept_inc, payload_inc = run_items(
        "case_generation", [case_item(PAIR_EXACT[0], "丑"), case_item(PAIR_EXACT[1], "寅")],
        [], "increment")
    check("近同名（≥EXACT_THRESHOLD）判为确定重复并真的被摘走",
          payload_inc.get("droppedCount") == 1 and len(kept_inc) == 1
          and title_of(kept_inc[0]) == PAIR_EXACT[0],
          f"droppedCount={payload_inc.get('droppedCount')} kept={len(kept_inc)}")

    kept_sup, payload_sup = run_items(
        "case_generation", [case_item(PAIR_SUSPECT[0], "卯"), case_item(PAIR_SUSPECT[1], "辰")],
        [], "increment")
    suspect_hits = [h for h in hits_of(payload_sup)
                    if str(h.get("title")) == PAIR_SUSPECT[1]]
    check("相似度落在 [0.85,0.95) 只算 suspect、不算 exact（不得摘走）",
          len(suspect_hits) == 1 and suspect_hits[0].get("dropped") is False
          and payload_sup.get("droppedCount") == 0 and len(kept_sup) == 2,
          f"dropped={payload_sup.get('dropped')} hits={suspect_hits}")
    check("疑似条目在原因里写明「低于确定重复阈值，仅标记不摘走」",
          bool(suspect_hits) and "仅标记不摘走" in str(suspect_hits[0].get("reason")),
          str(suspect_hits))

    kept_ctl, payload_ctl = run_items(
        "case_generation", [case_item(PAIR_CONTROL[0], "巳"), case_item(PAIR_CONTROL[1], "午")],
        [], "increment")
    check("同类型不同名的对照条目一条都不判（防误杀）",
          payload_ctl.get("hitCount") == 0 and len(kept_ctl) == 2, str(hits_of(payload_ctl)))
    check("对照条目在 mark 下同样不判（判定与模式无关）",
          run_items("case_generation",
                    [case_item(PAIR_CONTROL[0], "巳"), case_item(PAIR_CONTROL[1], "午")],
                    [], "mark")[1].get("hitCount") == 0)

    same_body: list[Any] = [
        {"title": "登录限流窗口边界", "caseType": "functional", "preconditions": "同一份前置",
         "steps": [{"action": "同一份步骤", "expected": "同一份预期"}],
         "expectedResult": "同一份结果"},
        {"title": "优惠券叠加退款金额校验", "caseType": "functional",
         "preconditions": "同一份前置",
         "steps": [{"action": "同一份步骤", "expected": "同一份预期"}],
         "expectedResult": "同一份结果"},
    ]
    body_payload = run_items("case_generation", same_body, [], "increment")[1]
    check("正文完全相同但标题毫不相干不判重（综合分 0.5 < 疑似阈值）",
          body_payload.get("hitCount") == 0, str(hits_of(body_payload)))
    body_only_sim = dedup.similarity(
        cast(dedup.ComparableCase, dedup.comparable_of("case_generation", same_body[0], 0)),
        cast(dedup.ComparableCase, dedup.comparable_of("case_generation", same_body[1], 1)))
    check("综合分公式为 max(标题分, 0.5×标题分 + 0.5×正文分)",
          abs(body_only_sim - 0.5) < 1e-9, f"sim={body_only_sim}")

    kept_db, payload_db = run_items(
        "case_generation", [case_item(TITLE_SAME, "未"), case_item(PAIR_EXACT[0], "申")],
        [existing_case_of(TITLE_SAME, "酉")], "mark")
    db_hits = hits_of(payload_db)
    check("与已有用例同名判确定重复（来源 existing、引用用例编号）",
          len(db_hits) == 1 and db_hits[0].get("source") == "existing"
          and db_hits[0].get("matchedRef") == "TC-9001"
          and float(cast(Any, db_hits[0].get("similarity"))) >= EXACT_AT,
          str(db_hits))
    check("命中已有基线时不重复计入本批次内部判定",
          len(kept_db) == 2 and payload_db.get("hitCount") == 1,
          str(payload_db.get("hitCount")))


# ---------------- 断言面 3：类型/模块分桶 ----------------

def section_buckets() -> None:
    cross = run_items(
        "case_generation",
        [case_item(TITLE_SAME, "戌", "functional"), case_item(TITLE_SAME, "戌", "boundary")],
        [], "mark")
    check("不同 caseType 的同名条目不得判 exact（跨类型判重复就是误报）",
          cross[1].get("hitCount") == 0, str(hits_of(cross[1])))
    cross_inc = run_items(
        "case_generation",
        [case_item(PAIR_EXACT[0], "亥", "functional"), case_item(PAIR_EXACT[1], "亥", "boundary")],
        [], "increment")
    check("跨类型即使达到确定档分数也不得摘走",
          cross_inc[1].get("droppedCount") == 0 and len(cross_inc[0]) == 2,
          str(hits_of(cross_inc[1])))
    same_type = run_items(
        "case_generation",
        [case_item(TITLE_SAME, "戌", "boundary"), case_item(TITLE_SAME, "戌", "boundary")],
        [], "mark")
    check("同 caseType 的同名条目仍然判重（分桶没把判定一起关掉）",
          same_type[1].get("hitCount") == 1, str(hits_of(same_type[1])))
    unknown_type = run_items(
        "case_generation",
        [case_item(TITLE_SAME, "戌", ""), case_item(TITLE_SAME, "戌", "functional")],
        [], "mark")
    check("类型缺失（空串）时不豁免比对（拿不准就保守判重）",
          unknown_type[1].get("hitCount") == 1, str(hits_of(unknown_type[1])))

    reg_by_priority = run_items(
        "regression_generation",
        [regression_item(TITLE_SAME, "戌", "P0", "functional"),
         regression_item(TITLE_SAME, "戌", "P0", "boundary")],
        [], "mark")
    check("回归点：同 priority 即判重，caseType 不同不影响（依据 comparable_of 取 priority）",
          reg_by_priority[1].get("hitCount") == 1, str(hits_of(reg_by_priority[1])))
    reg_by_case_type = run_items(
        "regression_generation",
        [regression_item(TITLE_SAME, "戌", "P0", "functional"),
         regression_item(TITLE_SAME, "戌", "P1", "functional")],
        [], "mark")
    check("回归点：priority 不同即不比对（caseType 相同也不比对）",
          reg_by_case_type[1].get("hitCount") == 0, str(hits_of(reg_by_case_type[1])))
    xmind_cross = run_items(
        "xmind_to_case",
        [xmind_item(TITLE_SAME, "戌", "functional"), xmind_item(TITLE_SAME, "戌", "exception")],
        [], "mark")
    check("xmind_to_case 的类型分桶与 case_generation 同口径",
          xmind_cross[1].get("hitCount") == 0, str(hits_of(xmind_cross[1])))
    regression_comparable = dedup.comparable_of(
        "regression_generation", regression_item(TITLE_SAME, "戌", "P2", "functional"), 3)
    check("回归点可比串取 module/reason/verifyTarget，类型取 priority",
          regression_comparable is not None
          and regression_comparable.case_type == "P2"
          and "模块戌" in regression_comparable.body
          and "验证目标戌" in regression_comparable.body,
          str(regression_comparable))
    case_comparable = dedup.comparable_of(
        "case_generation", case_item(TITLE_SAME, "戌", "functional"), 0)
    check("用例可比串取前置/步骤/预期，类型取 caseType",
          case_comparable is not None and case_comparable.case_type == "functional"
          and "执行步骤戌" in case_comparable.body
          and "最终结果戌" in case_comparable.body,
          str(case_comparable))
    _, no_title = run_items(
        "case_generation", [{"caseType": "functional"}, case_item(TITLE_SAME, "戌")],
        [], "increment")
    check("条目缺标题时不参与比对但仍计入 compared（不丢条目）",
          no_title.get("compared") == 2 and no_title.get("hitCount") == 0, str(no_title))


# ---------------- 断言面 4：模式解析优先级 ----------------

def mode_of_broken_conn() -> str:
    """没有 sys_config 表的裸连接：读库异常必须被吞掉并回落合法值。"""
    bare = sqlite3.connect(":memory:")
    try:
        return dedup.resolve_mode(
            bare, "case_generation", binding_config={dedup.BINDING_MODE_KEY: "increment"})
    finally:
        bare.close()


def mode_of_plain_conn(db_path: pathlib.Path) -> str:
    """同一份库用默认 row_factory 打开：读配置抛 ValueError，必须回落不崩。"""
    plain = sqlite3.connect(db_path)
    try:
        return dedup.resolve_mode(plain, "case_generation")
    finally:
        plain.close()


def section_mode(conn: sqlite3.Connection, db_path: pathlib.Path) -> None:
    check("配置键名口径（系统配置 / 功能点绑定）",
          dedup.CONFIG_MODE_KEY == "ai.dedup.mode" and dedup.BINDING_MODE_KEY == "dedupMode",
          f"{dedup.CONFIG_MODE_KEY}/{dedup.BINDING_MODE_KEY}")
    seeded = SysConfigRepository(conn).get_value(dedup.CONFIG_MODE_KEY, "")
    check("建库即带默认档位 mark（047 迁移的 sys_config 种子在位）",
          seeded == "mark", repr(seeded))

    set_config(conn, "mark")
    check("系统配置 mark 时解析为 mark",
          dedup.resolve_mode(conn, "case_generation") == "mark")
    set_config(conn, dedup.MODE_INCREMENT)
    check("系统配置 increment 时解析为 increment",
          dedup.resolve_mode(conn, "case_generation") == dedup.MODE_INCREMENT)
    check("功能点绑定覆盖系统配置",
          dedup.resolve_mode(conn, "case_generation",
                             binding_config={dedup.BINDING_MODE_KEY: "mark"}) == "mark")
    check("调用方显式 dedupMode 覆盖功能点绑定与系统配置",
          dedup.resolve_mode(conn, "case_generation", requested="increment",
                             binding_config={dedup.BINDING_MODE_KEY: "mark"})
          == dedup.MODE_INCREMENT)
    check("调用方显式 mark 压住绑定与系统配置的 increment",
          dedup.resolve_mode(conn, "case_generation", requested="mark",
                             binding_config={dedup.BINDING_MODE_KEY: dedup.MODE_INCREMENT})
          == "mark")
    check("绑定层在没有显式请求时覆盖系统配置",
          dedup.resolve_mode(conn, "case_generation",
                             binding_config={dedup.BINDING_MODE_KEY: dedup.MODE_INCREMENT})
          == dedup.MODE_INCREMENT)
    check("非法显式取值不短路下一级（回落到绑定层）",
          dedup.resolve_mode(conn, "case_generation", requested="incremental",
                             binding_config={dedup.BINDING_MODE_KEY: "mark"}) == "mark")
    set_config(conn, "错误的档位")
    check("非法绑定值继续下探系统配置层",
          dedup.resolve_mode(conn, "case_generation",
                             binding_config={dedup.BINDING_MODE_KEY: "INCR"}) == "mark")
    check("逐层都非法时回落默认 mark 且不崩",
          dedup.resolve_mode(conn, "case_generation", requested="去重",
                             binding_config={dedup.BINDING_MODE_KEY: "incremental"}) == "mark")
    check("系统配置非法值回落默认 mark",
          dedup.resolve_mode(conn, "case_generation") == "mark")
    set_config(conn, None)
    check("系统配置缺键时回落默认 mark",
          dedup.resolve_mode(conn, "case_generation") == "mark")
    set_config(conn, " Increment ")
    check("大小写与首尾空白容错",
          dedup.resolve_mode(conn, "case_generation") == dedup.MODE_INCREMENT)
    check("无连接时不读配置、只用请求/绑定/默认",
          dedup.resolve_mode(None, "case_generation") == "mark"
          and dedup.resolve_mode(None, "case_generation", requested="increment")
          == dedup.MODE_INCREMENT)
    check("未知能力也照优先级解析（解析与能力无关）",
          dedup.resolve_mode(conn, "interface_test_design") == dedup.MODE_INCREMENT)
    set_config(conn, "mark")
    check("normalize_mode/mode_or_default 对非法值回落 mark",
          dedup.normalize_mode("bogus") == "mark" and dedup.mode_or_default(None) == "mark"
          and dedup.normalize_mode(dedup.MODE_INCREMENT) == dedup.MODE_INCREMENT)
    check("读库异常（缺表）不崩，结果仍是合法档位",
          mode_of_broken_conn() in dedup.MODES)
    check("非 Row 工厂的连接读配置不崩（异常被吞并回落）",
          mode_of_plain_conn(db_path) in dedup.MODES)


# ---------------- 断言面 5：mark 与 increment 的行为差异 ----------------

def section_modes_behaviour() -> None:
    items: list[Any] = [
        case_item(TITLE_SAME, "a1"),
        case_item(f"{TITLE_SAME}（副本）", "a2"),
        case_item(PAIR_SUSPECT[0], "a3"),
        case_item(PAIR_SUSPECT[1], "a4"),
        case_item(PAIR_CONTROL[0], "a5"),
        case_item(f"{TITLE_SAME}（副本）", "a6"),
    ]
    existing = [existing_case_of(f"{TITLE_SAME}（副本）", "z9")]
    kept_mark, pm = run_items("case_generation", items, existing, "mark")
    kept_inc, pi = run_items("case_generation", items, existing, "increment")
    check("mark 模式一条都不丢（条目仍在产出里、dropped 为空）",
          len(kept_mark) == len(items) and pm.get("dropped") == []
          and pm.get("droppedCount") == 0,
          f"kept={len(kept_mark)}/{len(items)} dropped={pm.get('dropped')}")
    check("mark 模式命中项全部可恢复（restorable == 未摘走）",
          bool(hits_of(pm)) and all(h.get("restorable") is True for h in hits_of(pm)),
          str(hits_of(pm)))
    check("mark 模式的中文说明讲清「仍保留、可一键恢复」",
          any("标记模式" in str(note) and "一键恢复" in str(note) for note in pm["notes"])
          and not any("摘走" in str(note) for note in pm["notes"]),
          str(pm["notes"]))
    dropped_expect = int(cast(Any, pi.get("droppedCount") or 0))
    check("increment 真的摘走确定重复（kept 变短、droppedCount 与明细一致）",
          dropped_expect == 2 and len(kept_inc) == len(items) - dropped_expect
          and len(pi.get("dropped") or []) == dropped_expect,
          f"kept={len(kept_inc)} droppedCount={dropped_expect} "
          f"dropped={len(pi.get('dropped') or [])}")
    dropped_rows = [cast(JsonDict, row) for row in pi.get("dropped") or []]
    first_drop = dropped_rows[0] if dropped_rows else {}
    check("dropped 明细留原文（raw 能反解、标题与相似度齐）",
          str(first_drop.get("title")) == f"{TITLE_SAME}（副本）"
          and json.loads(str(first_drop.get("raw") or "{}")).get("title")
          == f"{TITLE_SAME}（副本）"
          and float(cast(Any, first_drop.get("similarity"))) >= EXACT_AT,
          str(first_drop))
    check("increment 下疑似条目仍留在产出里（只摘确定重复）",
          any(h.get("dropped") is False for h in hits_of(pi))
          and PAIR_SUSPECT[1] in [title_of(item) for item in kept_inc],
          f"kept={[title_of(item) for item in kept_inc]} hits={hits_of(pi)}")
    check("suspectCount == 未摘走的命中数（两种模式都成立）",
          all(int(cast(Any, p.get("suspectCount")))
              == sum(1 for h in hits_of(p) if h.get("dropped") is False)
              for p in (pm, pi)),
          f"mark={pm.get('suspectCount')} inc={pi.get('suspectCount')}")
    check("两模式的判定分数逐条一致，只有处置不同",
          pm.get("hitCount") == pi.get("hitCount")
          and [h.get("similarity") for h in hits_of(pm)]
          == [h.get("similarity") for h in hits_of(pi)]
          and int(cast(Any, pm.get("hitCount"))) > 0,
          f"mark={[h.get('similarity') for h in hits_of(pm)]} "
          f"inc={[h.get('similarity') for h in hits_of(pi)]}")
    check("increment 的中文说明同时报摘走数与标记数",
          any("增量模式" in str(note) and "摘走" in str(note) for note in pi["notes"])
          and not any("标记模式" in str(note) for note in pi["notes"]),
          str(pi["notes"]))
    check("modeLabel 取 MODE_LABELS（mark/increment 都有中文名）",
          pm.get("modeLabel") == dedup.MODE_LABELS["mark"]
          and pi.get("modeLabel") == dedup.MODE_LABELS[dedup.MODE_INCREMENT],
          f"{pm.get('modeLabel')}/{pi.get('modeLabel')}")
    _, bogus = run_items("case_generation", items, existing, "bogus-mode")
    check("dedup_items 收到非法模式时回落 mark（保守：不丢数据）",
          bogus.get("mode") == "mark" and bogus.get("droppedCount") == 0,
          str(bogus.get("mode")))
    check("MODES/MODE_LABELS 只含两档且互为同源",
          dedup.MODES == ("mark", dedup.MODE_INCREMENT)
          and set(dedup.MODE_LABELS) == set(dedup.MODES),
          f"{dedup.MODES}/{sorted(dedup.MODE_LABELS)}")


# ---------------- 断言面 6：as_dict 口径自洽 ----------------

def section_payload(db_path: pathlib.Path) -> None:
    items: list[Any] = [
        case_item(PAIR_CONTROL[0], "b1"),
        case_item(PAIR_SUSPECT[0], "b2"),
        case_item(PAIR_SUSPECT[1], "b3"),
        case_item(PAIR_EXACT[0], "b4"),
        case_item(TITLE_SAME, "b5"),
        case_item(PAIR_EXACT[1], "b6"),
        case_item(f"{TITLE_SAME}（副本）", "b7"),
    ]
    existing = [existing_case_of(TITLE_SAME, "b0")]
    kept_mark, pm = run_items("case_generation", items, existing, "mark")
    check("mark 模式 items[].index 与产出数组下标逐条对齐",
          not align_errors(kept_mark, pm), "; ".join(align_errors(kept_mark, pm)))
    kept_inc, pi = run_items("case_generation", items, existing, "increment")
    align_bad = align_errors(kept_inc, pi)
    check("increment 摘走条目后 items[].index 仍与产出数组下标对齐",
          len(kept_inc) < len(items) and not align_bad,
          f"kept={len(kept_inc)}；" + "; ".join(align_bad))
    check("本批次命中的 matchedRef 也落在产出下标里（高亮不会串行）",
          any(h.get("source") == "batch" and h.get("dropped") is False for h in hits_of(pi))
          and not ref_errors(kept_inc, pi),
          "; ".join(ref_errors(kept_inc, pi)))
    dropped_hits = [int(cast(Any, h.get("index"))) for h in hits_of(pi) if h.get("dropped")]
    check("摘走条目的 index 与 dropped 明细逐条对应（留痕用原始下标）",
          bool(dropped_hits)
          and set(dropped_hits) == {int(cast(Any, row.get("index")))
                                    for row in pi.get("dropped") or []}
          and all(index < len(items) for index in dropped_hits),
          f"hits={dropped_hits} dropped={[d.get('index') for d in pi.get('dropped') or []]}")

    check("compared 是送进来的产出条数、existingTotal 是召回基线条数",
          pm.get("compared") == len(items) and pm.get("existingTotal") == 1,
          f"{pm.get('compared')}/{pm.get('existingTotal')}")
    check("thresholds 原样回传 0.85/0.95 两档",
          pm.get("thresholds") == {"suspect": SUSPECT_AT, "exact": EXACT_AT},
          str(pm.get("thresholds")))
    check("scope 原样回传比较域（界面能说清和谁比的）",
          pm.get("scope") == DEFAULT_SCOPE, str(pm.get("scope")))
    check("unit 取能力表（用例 vs 回归点）",
          pm.get("unit") == "用例"
          and run_items("regression_generation",
                        [regression_item(TITLE_SAME, "r1")], [], "mark")[1].get("unit") == "回归点",
          str(pm.get("unit")))
    check("hitCount == suspectCount + droppedCount（三个口径自洽）",
          int(cast(Any, pi.get("hitCount")))
          == int(cast(Any, pi.get("suspectCount"))) + int(cast(Any, pi.get("droppedCount"))),
          f"hit={pi.get('hitCount')} suspect={pi.get('suspectCount')} "
          f"dropped={pi.get('droppedCount')}")

    many: list[Any] = [case_item(TITLE_SAME, f"c{index}")
                       for index in range(EXISTING_CAP + 10)]
    baseline = [existing_case_of(TITLE_SAME, "d0")]
    _, payload_many = run_items("case_generation", many, baseline, "mark")
    check("命中数超过 MAX_ITEMS_IN_PAYLOAD 时只截明细、不截计数",
          int(cast(Any, payload_many.get("hitCount"))) == len(many)
          and len(hits_of(payload_many)) == PAYLOAD_CAP
          and int(cast(Any, payload_many.get("suspectCount"))) == len(many),
          f"hitCount={payload_many.get('hitCount')} items={len(hits_of(payload_many))} "
          f"suspectCount={payload_many.get('suspectCount')}")
    check("items 是 hits 的前 60 条且下标单调递增",
          [int(cast(Any, h.get("index"))) for h in hits_of(payload_many)]
          == list(range(PAYLOAD_CAP)),
          str([h.get("index") for h in hits_of(payload_many)][:6]))
    kept_many, payload_drop = run_items("case_generation", many, baseline, "increment")
    check("increment 全量摘走时产出为空、dropped 明细同样截到上限",
          kept_many == [] and payload_drop.get("droppedCount") == len(many)
          and len(payload_drop.get("dropped") or []) == PAYLOAD_CAP
          and payload_drop.get("suspectCount") == 0,
          f"kept={len(kept_many)} droppedCount={payload_drop.get('droppedCount')} "
          f"dropped={len(payload_drop.get('dropped') or [])}")

    _, empty = run_items("case_generation", [], [], "mark")
    check("空产出只留中文说明、不报错",
          empty.get("hitCount") == 0 and empty.get("items") == []
          and any("没有可比对" in str(note) for note in empty["notes"]),
          str(empty["notes"]))
    manual = dedup.DedupReport(capability="case_generation", unit="用例", mode="weird")
    check("未知 mode 的 modeLabel 回落原值（不 KeyError）",
          manual.as_dict().get("modeLabel") == "weird", str(manual.as_dict()))
    hit = dedup.DuplicateHit(index=0, title="t", similarity=0.9, matched_ref="r",
                             matched_title="m", reason="x", scope="s", source="batch",
                             dropped=True)
    check("DuplicateHit 明细字段口径齐（含 dropped/restorable）",
          set(hit.as_dict()) == {"index", "title", "similarity", "matchedRef", "matchedTitle",
                                 "reason", "scope", "source", "dropped", "restorable"}
          and hit.as_dict()["restorable"] is False,
          str(sorted(hit.as_dict())))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        seed_project(conn, 9101, "去重门禁项目")
        seed_case(conn, project_id=9101, case_no="DUP-A01", title=TITLE_SAME,
                  step_action="输入正确验证码", expected="登录成功")
        conn.commit()
        kept_live, report_live = dedup.dedup_output(
            conn, "case_generation", [case_item(TITLE_SAME, "e1")],
            project_id=9101, mode=dedup.MODE_INCREMENT)
        payload_live = report_live.as_dict()
        check("dedup_output 端到端召回库内基线并摘走（matchedRef 用用例编号）",
              payload_live.get("existingTotal") == 1 and kept_live == []
              and payload_live.get("droppedCount") == 1
              and hits_of(payload_live)[0].get("matchedRef") == "DUP-A01",
              str(payload_live))
        check("比较域文本把项目与类型讲清楚（scope 进命中原因）",
              "projectId=9101" in str(hits_of(payload_live)[0].get("scope")),
              str(hits_of(payload_live)[0]))
    finally:
        conn.close()


# ---------------- 断言面 7：existing_cases 限量与项目隔离 ----------------

def section_existing(db_path: pathlib.Path) -> None:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        seed_project(conn, 9201, "限量项目")
        seed_project(conn, 9202, "对照项目")
        total = EXISTING_CAP + 20
        for index in range(total):
            # updated_at 随 index 严格递增（字典序也递增），限量截断才能按预期判
            seed_case(conn, project_id=9201, case_no=f"DUP-{index:03d}",
                      title=f"批量回归标题{index}",
                      case_type="functional" if index % 2 == 0 else "boundary",
                      module_id=500 + index % 3,
                      requirement_id=700 if index % 5 == 0 else None,
                      step_action=f"执行步骤{index}", expected=f"预期结果{index}",
                      updated_at=f"2026-01-01 {index // 60:02d}:{index % 60:02d}:00")
        seed_case(conn, project_id=9201, case_no="DUP-DEL", title="已删除的用例标题",
                  deleted=True)
        conn.commit()
        expected_boundary = len([i for i in range(total) if i % 2 == 1])
        expected_module = len([i for i in range(total) if i % 3 == 1])
        expected_requirement = len([i for i in range(total) if i % 5 == 0])

        cases, scope = dedup.existing_cases(conn, project_id=9201)
        check("召回限量默认不超过 MAX_EXISTING",
              len(cases) == EXISTING_CAP, f"len={len(cases)}")
        check("限量截掉最旧的、留下最近的（updated_at DESC 口径）",
              {c.ref for c in cases}
              == {f"DUP-{i:03d}" for i in range(total - EXISTING_CAP, total)},
              f"首条={cases[0].ref} 末条={cases[-1].ref}")
        check("limit 超上限时被夹到 MAX_EXISTING",
              len(dedup.existing_cases(conn, project_id=9201, limit=5000)[0]) == EXISTING_CAP)
        check("显式 limit 生效",
              len(dedup.existing_cases(conn, project_id=9201, limit=5)[0]) == 5)
        check("limit=0 回落默认限量（不能变成不限量）",
              len(dedup.existing_cases(conn, project_id=9201, limit=0)[0]) == EXISTING_CAP)
        check("负数 limit 收敛到至少 1 条（不得变成无限制或报错）",
              len(dedup.existing_cases(conn, project_id=9201, limit=-5)[0]) == 1)
        check("limit=1 取到的是最新那条",
              [c.title for c in dedup.existing_cases(conn, project_id=9201, limit=1)[0]]
              == [f"批量回归标题{total - 1}"],
              str([c.title for c in dedup.existing_cases(conn, project_id=9201, limit=1)[0]]))
        check("逻辑删除的基线不召回",
              all("已删除" not in c.title for c in cases))
        check("比较域 scope 字段齐（项目/模块/需求/类型）",
              set(scope) == {"projectId", "moduleId", "requirementId", "caseTypes"}
              and scope["projectId"] == 9201, str(scope))
        boundary = dedup.existing_cases(conn, project_id=9201, case_types=["boundary"])[0]
        check("按用例类型过滤生效",
              len(boundary) == expected_boundary,
              f"len={len(boundary)} 期望={expected_boundary}")
        check("类型过滤后的条目类型确实都是 boundary",
              bool(boundary) and all(c.case_type == "boundary" for c in boundary),
              str(sorted({c.case_type for c in boundary})))
        check("跨类型不召回（functional 过滤取不到 boundary 条目）",
              all(c.case_type == "functional" for c in
                  dedup.existing_cases(conn, project_id=9201, case_types=["functional"])[0]))
        in_module = dedup.existing_cases(conn, project_id=9201, module_id=501)[0]
        check("按模块过滤生效（跨模块不召回）",
              len(in_module) == expected_module
              and len(dedup.existing_cases(conn, project_id=9201, module_id=999)[0]) == 0,
              f"len={len(in_module)} 期望={expected_module}")
        in_requirement = dedup.existing_cases(conn, project_id=9201, requirement_id=700)[0]
        check("按需求过滤生效",
              len(in_requirement) == expected_requirement,
              f"len={len(in_requirement)} 期望={expected_requirement}")
        empty_cases, empty_scope = dedup.existing_cases(conn, project_id=None)
        check("没有项目上下文时一条都不召回（不做跨项目比对）",
              empty_cases == [] and empty_scope["projectId"] is None, str(empty_scope))
        other_cases, _ = dedup.existing_cases(conn, project_id=9202)
        check("项目隔离：空项目召回 0 条（别的项目的基线不串味）",
              other_cases == [], f"len={len(other_cases)}")

        items: list[Any] = [case_item(f"批量回归标题{total - 2}", "f1")]
        hit_a = run_items("case_generation", items,
                          dedup.existing_cases(conn, project_id=9201,
                                               case_types=["functional"])[0], "mark")[1]
        _, report_b = dedup.dedup_output(conn, "case_generation", items,
                                         project_id=9202, mode=dedup.MODE_MARK)
        payload_b = report_b.as_dict()
        check("同项目基线确实判出重复（限量/过滤没把判定做没）",
              hit_a.get("hitCount") == 1 and hits_of(hit_a)[0].get("source") == "existing",
              str(hits_of(hit_a)))
        check("跨项目基线不得互相污染（另一项目生成同名条目判不出重复）",
              payload_b.get("hitCount") == 0 and payload_b.get("existingTotal") == 0,
              str(payload_b))

        one_case = dedup.existing_cases(conn, project_id=9201, limit=1)[0][0]
        check("库内条目可比串带步骤与预期（正文不是空的）",
              one_case.ref.startswith("DUP-") and "执行步骤" in one_case.body
              and one_case.norm_body != "",
              str(one_case))
        broken_case = dedup.comparable_from_row({
            "case_no": "DUP-X", "title": "坏步骤", "case_type": "functional",
            "preconditions": "", "steps": "非列表步骤文本", "expected_result": "预期"})
        check("步骤形态异常时 comparable_from_row 不崩（字符串步骤照旧可比）",
              "非列表步骤文本" in broken_case.body and broken_case.ref == "DUP-X",
              str(broken_case))
        regression_row = dedup.comparable_from_row({
            "title": "回归行", "priority": "P1", "module": "模块",
            "change_scope": "范围", "remark": "备注", "no": "RG-1"})
        check("回归行（无 case_no/case_type）按 priority 分桶、可比串取 change_scope",
              regression_row.case_type == "P1" and "范围" in regression_row.body
              and regression_row.ref == "RG-1",
              str(regression_row))
        steps_none = dedup.comparable_from_row({"case_no": "DUP-Y", "title": "标题",
                                               "case_type": "functional", "steps": None})
        check("steps 为 None 时可比串仍拼得出（不抛异常）",
              isinstance(steps_none.body, str) and steps_none.norm_title == "标题",
              str(steps_none))
    finally:
        conn.close()


# ---------------- 断言面 8：baseline_directive ----------------

def section_directive() -> None:
    check("没有基线时不注入任何指令（空串而不是空话）",
          dedup.baseline_directive("case_generation", []) == "")
    many = [cast(dedup.ComparableCase,
                 dedup.comparable_of("case_generation",
                                     case_item(f"基线标题{index}", f"g{index}"), index))
            for index in range(30)]
    text = dedup.baseline_directive("case_generation", many)
    check("指令文本非空且明确要求只写新场景",
          bool(text) and "增量" in text and "只写" in text and "不得与其重复" in text,
          text[:80])
    listed = text.split("基线：", 1)
    check("去重基线确实出现在指令里（列出前 20 条标题）",
          len(listed) == 2 and listed[1].startswith("基线标题0")
          and "基线标题19" in listed[1],
          listed[-1][:60])
    check("指令按 limit 限量，第 21 条标题不得出现",
          "基线标题20" not in text and "基线标题29" not in text
          and "共 20 条列出" in text,
          text[-60:])
    case_head = dedup.baseline_directive("case_generation", many[:2]).split("基线：", 1)[0]
    reg_head = dedup.baseline_directive("regression_generation", many[:2]).split("基线：", 1)[0]
    check("注入的条目量词随能力切换（用例 vs 回归点）",
          "用例" in case_head and "回归点" in reg_head and "用例" not in reg_head,
          f"{case_head} / {reg_head}")
    short = dedup.baseline_directive("case_generation", many[:3], limit=2)
    check("limit 参数可覆盖默认 20 条",
          "基线标题1" in short and "基线标题2" not in short and "共 2 条列出" in short,
          short[-60:])
    single = [cast(dedup.ComparableCase,
                   dedup.comparable_of("case_generation", case_item("唯一一条基线标题", "h1"), 0))]
    single_text = dedup.baseline_directive("case_generation", single)
    check("单条基线也给出完整指令（条数写实际值）",
          "共 1 条列出" in single_text and "唯一一条基线标题" in single_text,
          single_text)
    check("指令长度可控（不给提示词体积埋雷）",
          len(text) < 4000, f"len={len(text)}")


# ---------------- 覆盖面：能力口径表与 dedup_output 边界 ----------------

def section_coverage(db_path: pathlib.Path) -> None:
    check("纳入去重口径的能力就是三档产出型能力",
          set(dedup.DEDUP_CAPABILITIES) == {"case_generation", "xmind_to_case",
                                            "regression_generation"}
          and all(dedup.is_dedup_capability(code) for code in dedup.DEDUP_CAPABILITIES),
          str(sorted(dedup.DEDUP_CAPABILITIES)))
    check("非去重能力不判重（is_dedup_capability 与口径表同源）",
          not dedup.is_dedup_capability("testpoint_generation")
          and not dedup.is_dedup_capability(""))
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        seed_project(conn, 9301, "口径外能力项目")
        seed_project(conn, 9302, "回归基线项目")
        seed_case(conn, project_id=9301, case_no="DUP-OUT", title=TITLE_SAME)
        seed_case(conn, project_id=9302, case_no="RG-OUT", title=TITLE_SAME, case_type="P0")
        conn.commit()
        kept, report = dedup.dedup_output(conn, "testpoint_generation",
                                          [case_item(TITLE_SAME, "i1")],
                                          project_id=9301, mode=dedup.MODE_INCREMENT)
        payload = report.as_dict()
        check("去重口径外的能力连比对都不做（产出原样返回）",
              bool(kept) and payload.get("hitCount") == 0 and payload.get("existingTotal") == 0
              and any("未纳入去重口径" in str(note) for note in payload["notes"]),
              str(payload["notes"]))
        check("口径外能力也照样回传合法模式（非法值先收敛）",
              payload.get("mode") == dedup.MODE_INCREMENT
              and dedup.dedup_output(conn, "x", [], project_id=None,
                                     mode="bogus")[1].mode == "mark")

        _, reg_payload = dedup.dedup_output(
            conn, "regression_generation",
            [regression_item(TITLE_SAME, "j1", priority="P0")],
            project_id=9301, requirement_id=999, mode=dedup.MODE_MARK)
        check("回归点不看需求维度（dedup_output 显式把 requirement_id 置空）",
              reg_payload.scope["requirementId"] is None, str(reg_payload.scope))
        check("回归点对 case_type=functional 的库内条目不比对（口径①：分桶取 priority）",
              reg_payload.as_dict().get("existingTotal") == 1
              and reg_payload.as_dict().get("hitCount") == 0,
              str(reg_payload.as_dict()))
        _, reg_hit = dedup.dedup_output(
            conn, "regression_generation",
            [regression_item(TITLE_SAME, "j2", priority="P0", case_type=None)],
            project_id=9302, mode=dedup.MODE_MARK)
        payload_reg = reg_hit.as_dict()
        check("库内 case_type 写成优先级时回归点判出重复（口径②：基线只从 biz_test_case 召回）",
              payload_reg.get("hitCount") == 1
              and hits_of(payload_reg)[0].get("matchedRef") == "RG-OUT",
              str(hits_of(payload_reg)))
        _, reg_low = dedup.dedup_output(
            conn, "regression_generation",
            [regression_item(TITLE_SAME, "j3", priority="P1", case_type=None)],
            project_id=9302, mode=dedup.MODE_INCREMENT)
        payload_low = reg_low.as_dict()
        check("回归点优先级不同则不摘走（P1 产出对 P0 基线）",
              payload_low.get("existingTotal") == 1 and payload_low.get("droppedCount") == 0,
              str(payload_low))
    finally:
        conn.close()


# ---------------- 断言面 9：前端消费（默认不勾选 + 一键恢复） ----------------

def section_frontend() -> None:
    if not CAPABILITY_VUE.is_file():
        check("采纳弹窗所在的视图文件存在", False, f"未找到 {CAPABILITY_VUE}")
        return
    view = CAPABILITY_VUE.read_text(encoding="utf-8")

    check("done 帧的去重结论被接住（不是只在服务端自说自话）",
          re.search(r"dedupInfo\.value\s*=\s*readDedupConclusion\(\s*frame\.dedup\s*\)", view)
          is not None, view[:200])
    check("换能力或重跑时去重结论跟着重置（旧判定不得勾新产出）",
          re.search(r"dedupInfo\.value\s*=\s*null", view) is not None)
    check("疑似重复默认不勾选（勾的是非命中行）",
          DEFAULT_UNCHECK_RE.search(view) is not None, "没找到 toggleRowSelection(row, !row.dupHit)")
    # 正控：这条规则必须能因「全部默认勾上」而开火，否则「默认不勾选」是空断言
    check("默认不勾选规则能开火（正控）",
          DEFAULT_UNCHECK_RE.search("table.toggleRowSelection(row, true)") is None
          and DEFAULT_UNCHECK_RE.search("adoptSuspectRows.value.filter(r => r.dupHit)") is None)
    check("一键恢复与反向取消都在同一个开关上（恢复可逆）",
          "function toggleAdoptSuspects" in view
          and "const adoptSuspectsRestored" in view
          and "@click=\"toggleAdoptSuspects\"" in view)
    check("提交采纳的是勾选集而不是全量",
          re.search(r"cases:\s*adoptSelection\.value", view) is not None
          and re.search(r"cases:\s*adoptRows\.value", view) is None)
    check("勾选条数即时反映在按钮上（用户看得见少了哪几条）",
          re.search(r"采纳 \{\{\s*adoptSelection\.length\s*\}\} 条", view) is not None
          and re.search(r":disabled=\"!\s*adoptSelection\.length\"", view) is not None)
    check("界面向用户讲清「默认未勾选、可恢复」",
          "疑似重复" in view and "一键恢复" in view and "默认不勾选" in view)
    check("命中原因逐条给到用户（撞了谁、像到多少）",
          "dupHitTip" in view and "hit.matchedTitle" in view and "hit.similarity" in view)

    if TYPES_TS.is_file():
        types = TYPES_TS.read_text(encoding="utf-8")
        missing = [name for name in DEDUP_CONTRACT_FIELDS
                   if not re.search(rf"\b{name}[?]?:", types)]
        check("前端类型把后端 dedup 契约字段一次钉全（字段漂移要在这里红）",
              not missing, f"类型缺字段={missing}")
    else:
        check("前端类型文件存在", False, f"未找到 {TYPES_TS}")

    owners = [p.name for p in (SRC / "views").rglob("*.vue")
              if DEFAULT_UNCHECK_RE.search(
                  p.read_text(encoding="utf-8", errors="ignore"))]
    check("默认不勾选的落点只有采纳弹窗（别的表格不受本次口径影响）",
          owners == ["CapabilityView.vue"], str(owners))


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-dedup-"))
    db_path = workdir / "dedup.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        section_normalize()
        section_bands()
        section_buckets()
        section_mode(conn, db_path)
        section_modes_behaviour()
        section_payload(db_path)
        section_existing(db_path)
        section_directive()
        section_coverage(db_path)
        section_frontend()
    finally:
        conn.close()
        shutil.rmtree(workdir, ignore_errors=True)
    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
