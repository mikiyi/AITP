"""离线测试知识包门禁（四期专项 G）。

    python backend/scripts/check_heuristics_pack.py

覆盖五段：
  1. 四份字典可解析、分类齐备、样例非空；
  2. 关键词检索命中（「边界」命中 BVA、「注入」命中探测样例、「emoji」命中对抗串）；
  3. 无命中时给默认分类，且受 limit 约束；
  4. 能力过滤：只有声明了该包的能力才命中；
  5. 注入上下文时按档位限量（不超 per-kind 上限、计入总字数）。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import pathlib
import sys

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.capability_codes import spec_of  # noqa: E402
from aitp.services import context_assembler, heuristics_service  # noqa: E402

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")


def pack_checks() -> None:
    packs = heuristics_service.available_packs()
    names = {item["pack"] for item in packs}
    check("四份字典齐备",
          names == {"naughty_strings", "bva_templates", "sfdipot", "probing_payloads"},
          str(sorted(names)))
    check("每份字典都有分类",
          all(int(item["categoryCount"]) > 0 for item in packs), str(packs))
    check("每份字典都有中文标签",
          all(str(item["label"]).strip() for item in packs), str(packs))
    check("每份字典都声明了消费能力",
          all(item["capabilities"] for item in packs), str(packs))

    for meta, categories in heuristics_service._all_packs():
        pack = str(meta.get("pack") or "")
        for category in categories:
            cid = str(category.get("id") or "")
            samples = category.get("samples") or []
            check(f"{pack}:{cid} 有样例值", bool(samples), str(category)[:120])
            check(f"{pack}:{cid} 有关键词", bool(category.get("keywords")),
                  str(category)[:120])


def search_checks() -> None:
    bva = heuristics_service.search(["边界值", "数值"], capability="case_generation", limit=4)
    check("「边界值」命中 BVA 模板",
          any(hit.ref.startswith("bva_templates:") for hit in bva), str([h.ref for h in bva]))

    probe = heuristics_service.search(["注入", "sqli"], capability="api_test_design", limit=4)
    check("「注入」命中探测样例",
          any(hit.ref.startswith("probing_payloads:") for hit in probe),
          str([h.ref for h in probe]))

    emoji = heuristics_service.search(["emoji", "unicode"], capability="case_generation", limit=4)
    check("「emoji」命中对抗性字符串",
          any(hit.ref.startswith("naughty_strings:") for hit in emoji),
          str([h.ref for h in emoji]))

    struct = heuristics_service.search(["结构", "功能", "数据"], capability="case_generation",
                                        limit=4)
    check("多维关键词命中 SFDIPOT",
          any(hit.ref.startswith("sfdipot:") for hit in struct), str([h.ref for h in struct]))

    fallback = heuristics_service.search([], capability="case_generation", limit=2)
    check("无关键词时给默认分类且不超 limit", 0 < len(fallback) <= 2, str([h.ref for h in fallback]))
    check("limit=0 时不返回任何块",
          heuristics_service.search(["边界"], capability="case_generation", limit=0) == [])

    only = heuristics_service.search(["边界", "注入", "结构"], capability="api_test_design",
                                      limit=10)
    refs = [hit.ref for hit in only]
    check("能力过滤：接口设计不注入 SFDIPOT（未声明消费）",
          not any(ref.startswith("sfdipot:") for ref in refs), str(refs))


def limit_checks() -> None:
    spec = spec_of("case_generation")
    assert spec is not None
    check("用例生成声明了离线知识包上下文",
          context_assembler.KIND_HEURISTIC in spec.context_kinds, str(spec.context_kinds))

    hits = heuristics_service.search(["边界", "注入", "结构", "emoji", "unicode"],
                                     capability="case_generation", limit=10)
    check("检索结果受 limit 约束",
          len(heuristics_service.search(["边界", "注入", "结构", "emoji", "unicode"],
                                        capability="case_generation", limit=3)) <= 3,
          str(len(hits)))
    check("命中块正文受字数上限约束",
          all(hit.chars <= heuristics_service.MAX_HIT_CHARS + 200 for hit in hits),
          str([hit.chars for hit in hits]))

    for tier in context_assembler.CONTEXT_TIERS:
        limits = context_assembler.TIER_LIMITS[tier]
        check(f"{tier} 档为知识包留了 per-kind 上限",
              int(limits.get("heuristic", 0)) > 0, str(limits))

    plan = context_assembler.ContextPlan(
        capability="case_generation", tier="lite",
        kinds=(context_assembler.KIND_HEURISTIC,),
        blocks=[context_assembler.ContextBlock(
            context_assembler.KIND_HEURISTIC, f"x:{i}", "标题", "正文", 1) for i in range(5)],
    )
    kept = context_assembler._apply_limits(
        list(plan.blocks), plan, context_assembler.TIER_LIMITS["lite"])
    check("知识包块走统一档位裁剪（lite 只留 1 条）", len(kept) == 1, str(len(kept)))
    check("被裁掉的知识包块留痕", len(plan.skipped) == 4, str(plan.skipped))
    check("知识包块有中文种类标签",
          plan.blocks[0].as_dict()["kindLabel"] == "测试知识包",
          str(plan.blocks[0].as_dict()["kindLabel"]))

    variables = context_assembler.ContextPlan(
        capability="case_generation", tier="standard",
        kinds=(context_assembler.KIND_HEURISTIC,),
        blocks=[context_assembler.ContextBlock(
            context_assembler.KIND_HEURISTIC, "bva_templates:number", "边界值模板·数值字段",
            "按 0/1/上限取", 2)],
    ).variables()
    check("知识包注入到 contextHeuristics 变量",
          "边界值模板" in variables.get("contextHeuristics", ""), str(variables))


def main() -> int:
    pack_checks()
    search_checks()
    limit_checks()
    print(f"\nPASS={_passed} FAIL={_failed}")
    for item in _failures:
        print(f"  [FAIL] {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
