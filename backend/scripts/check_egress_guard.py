"""数据外发确认只看「实际会用的首选模型」门禁。

用户报的问题很具体：用本地大模型问答，也弹「数据外发确认」。原来的口径是
「降级链里只要有一个云端候选，就算可能外发」——那是把还没发生的降级当成了事实，
于是本地模型永远被云端的事情打扰。新口径：

- 确认框只看首选模型：本地首选不打扰，云端首选才弹；
- 真降级到云端时，后端仍会自动脱敏，并由响应里的 egress/egressWarning 如实说明
  这次确实外发过，界面在结果处提示，不靠事前弹窗兜底；
- 候选链照旧完整返回，界面仍能看到「万一挂了会降到谁」。

    python backend/scripts/check_egress_guard.py

纯离线：用假候选替换 `lab_service.available_candidates`，不发 HTTP、不碰正式库与服务。
"""

from __future__ import annotations

import pathlib
import sys
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.types import ResolvedAIConfig  # noqa: E402
from aitp.common.constants import EGRESS_NONE, EGRESS_THIRD_PARTY  # noqa: E402
from aitp.services import knowledge_assistant_service, lab_service  # noqa: E402

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


def candidate(*, local: bool, model: str) -> ResolvedAIConfig:
    return ResolvedAIConfig(
        provider="ollama" if local else "custom",
        protocol="openai_compat",
        base_url="http://127.0.0.1:11434/v1",
        model=model,
        is_local=local,
    )


def main() -> int:
    # 候选口径本身：本地不出本机，云端才算外发
    check("本地候选 egress=NONE", candidate(local=True, model="qwen3:8b").egress == EGRESS_NONE,
          candidate(local=True, model="qwen3:8b").egress)
    check("云端候选 egress=THIRD_PARTY（正控）",
          candidate(local=False, model="cloud-max").egress == EGRESS_THIRD_PARTY,
          candidate(local=False, model="cloud-max").egress)

    original = lab_service.available_candidates
    try:
        # ① 用户踩到的那条路：本地首选 + 云端兜底
        lab_service.available_candidates = lambda profile_id: [
            candidate(local=True, model="qwen3:8b"),
            candidate(local=False, model="cloud-max"),
        ]
        local_first = knowledge_assistant_service.egress_plan("hybrid")
        check("本地首选 + 云端兜底：不判定为外发（不再打扰本地问答）",
              local_first["externalPossible"] is False, str(local_first))
        check("本地首选时 egress 结论为本机", local_first["egress"] == EGRESS_NONE,
              str(local_first["egress"]))
        check("候选链仍然完整返回兜底模型（界面可如实说明降级去向）",
              len(local_first["candidates"]) == 2
              and local_first["candidates"][1]["isLocal"] is False, str(local_first["candidates"]))

        # ② 反过来：云端首选，本地兜底，必须弹
        lab_service.available_candidates = lambda profile_id: [
            candidate(local=False, model="cloud-max"),
            candidate(local=True, model="qwen3:8b"),
        ]
        cloud_first = knowledge_assistant_service.egress_plan("llm")
        check("云端首选判定为外发（正控：口径不是永远 False）",
              cloud_first["externalPossible"] is True, str(cloud_first))
        check("云端首选时 egress 结论为第三方",
              cloud_first["egress"] == EGRESS_THIRD_PARTY, str(cloud_first["egress"]))

        # ③ 只看首选，不是「任一候选」——两条链只差顺序，结论必须相反
        check("同一批候选换顺序结论翻转（证明判定落在首选而不是 any）",
              local_first["externalPossible"] is not cloud_first["externalPossible"],
              f"local={local_first['externalPossible']} cloud={cloud_first['externalPossible']}")

        # ④ 纯知识库模式不调模型；没有候选（没配模型）也不弹
        kb = knowledge_assistant_service.egress_plan("kb")
        check("纯知识库回答不外发、无候选", kb["externalPossible"] is False and kb["candidates"] == [],
              str(kb))
        lab_service.available_candidates = lambda profile_id: []
        empty = knowledge_assistant_service.egress_plan("hybrid")
        check("没有可用模型时不误报外发", empty["externalPossible"] is False, str(empty))

        # ⑤ 全本地链路（没有任何云端候选）保持不外发
        lab_service.available_candidates = lambda profile_id: [
            candidate(local=True, model="qwen3:8b"),
            candidate(local=True, model="llama3"),
        ]
        all_local = knowledge_assistant_service.egress_plan("hybrid")
        check("全本地候选链不外发", all_local["externalPossible"] is False
              and all_local["egress"] == EGRESS_NONE, str(all_local))
    finally:
        lab_service.available_candidates = original

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
