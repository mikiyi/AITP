"""任务类型标签门禁：已注册的任务 kind 必须有中文标签，且不许靠导入副作用登记。

GAP-37 的收口口径：任务中心「所有 kind 均有中文标签」。历史上 `backup_schedule.py`
在模块顶层往 `job_queue.KIND_LABELS` 塞标签，只有该模块被导入过才有中文，
定时备份未启用的进程里 `data.export` 这类 kind 就直接对外吐英文原值。

    python backend/scripts/check_task_labels.py
"""

from __future__ import annotations

import importlib
import pathlib
import re
import sqlite3
import sys
import tempfile
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import job_queue  # noqa: E402

AITP = ROOT / "backend" / "aitp"
HELP_TS = ROOT / "frontend" / "src" / "views" / "settings" / "productHelpContent.ts"
V1 = "/api/v1"
CJK = re.compile(r"[\u4e00-\u9fff]")
#: 帮助页写「当前有 N 类任务——」，N 与实际 kind 数不符就是过期文案
KIND_COUNT_RE = re.compile(r"当前有([一二三四五六七八九十\d]+)类任务")
CN_NUM = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9, "十": 10}

#: 模块顶层直接给 KIND_LABELS 赋值 = 靠导入副作用登记标签，正是要禁止的写法
SIDE_EFFECT_RE = re.compile(r"^\s*[A-Za-z_][\w.]*KIND_LABELS\[[^\]]+\]\s*[^=]", re.M)
#: 注册站扫描：哪些模块调用了 job_queue.register
REGISTER_RE = re.compile(r"\bjob_queue\.register\(")

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


def sources() -> dict[pathlib.Path, str]:
    return {path: path.read_text(encoding="utf-8") for path in sorted(AITP.rglob("*.py"))}


def import_register_sites(texts: dict[pathlib.Path, str]) -> set[str]:
    """导入所有调用 job_queue.register 的模块，返回模块名列表。

    不写死清单：新增注册模块会被静态扫描自动带进来，避免门禁和实现脱节。
    """
    modules: set[str] = set()
    for path, text in texts.items():
        if not REGISTER_RE.search(text):
            continue
        parts = list(path.relative_to(ROOT / "backend").with_suffix("").parts)
        modules.add(".".join(parts))
    for name in sorted(modules):
        importlib.import_module(name)
    return modules


def labels_are_chinese(kinds: list[str]) -> tuple[list[str], list[str]]:
    missing = [kind for kind in kinds if not job_queue.KIND_LABELS.get(kind)]
    untranslated = [
        kind for kind in kinds
        if job_queue.KIND_LABELS.get(kind) and not CJK.search(
            str(job_queue.KIND_LABELS[kind])
        )
    ]
    return missing, untranslated


def cn_count(raw: str) -> int:
    """「五」/「10」都能变成数字，认不出来就返回 -1 让门禁红。"""
    if raw.isdigit():
        return int(raw)
    if raw in CN_NUM:
        return CN_NUM[raw]
    return -1


def main() -> int:
    texts = sources()
    module_names = import_register_sites(texts)

    registered = set(job_queue._HANDLERS)
    labeled = set(job_queue.KIND_LABELS)
    kinds = sorted(registered)

    check("扫描到任务注册模块", len(module_names) >= 3, f"modules={sorted(module_names)}")
    check(
        "已注册 kind 全部有标签",
        not (registered - labeled),
        f"缺标签={sorted(registered - labeled)}",
    )
    check(
        "标签表没有孤儿 kind",
        not (labeled - registered),
        f"多登记={sorted(labeled - registered)}",
    )
    missing, untranslated = labels_are_chinese(kinds)
    check("标签均为中文且非空", not missing and not untranslated,
          f"空={missing} 未译={untranslated}")
    same_as_kind = [kind for kind in kinds if job_queue.KIND_LABELS.get(kind) == kind]
    check("标签不与 kind 原值相同", not same_as_kind, str(same_as_kind))

    # 正向对照：证明「禁止导入副作用」这条扫描不是空转——真实仓库里已经没有，
    # 但同一段规则必须能命中故意写出来的违规样例。
    violating = [
        str(path.relative_to(ROOT))
        for path, text in texts.items()
        if SIDE_EFFECT_RE.search(text)
    ]
    check("无模块用导入副作用写标签", not violating, str(violating[:5]))
    check(
        "扫描规则能命中违规写法",
        bool(SIDE_EFFECT_RE.search('job_queue.KIND_LABELS[KIND] = "定时整包备份"')),
    )

    # 帮助页是用户唯一的口径来源：任务类型数量与每种标签都要对得上。
    help_text = HELP_TS.read_text(encoding="utf-8")
    unlisted = [str(label) for label in job_queue.KIND_LABELS.values() if str(label) not in help_text]
    check("帮助页列出每一种任务类型", not unlisted, str(unlisted))
    matched = KIND_COUNT_RE.search(help_text)
    declared = cn_count(matched.group(1)) if matched else -1
    check("帮助页的任务类型数量与注册表一致", declared == len(labeled),
          f"文案={matched and matched.group(0)} 实际={len(labeled)}")
    control = KIND_COUNT_RE.search("当前有五类任务——自动化脚本执行")
    control_ok = control is not None and cn_count(control.group(1)) == 5
    check("数量解析规则能命中过期文案", control_ok, str(control))

    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-tasklabels-"))
    db_path = workdir / "tasks.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "tasks.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    client = create_app(cfg).test_client()

    # 直接落库而不走 submit：submit 会拉起 worker 线程，任务会被立刻消费掉，
    # 门禁要的是「每种 kind 的静态任务行读回来是什么标签」。
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        ids: dict[str, int] = {}
        for kind in kinds:
            cur = conn.execute(
                "INSERT INTO sys_task (kind, title, status, priority, payload_json,"
                " attempts, max_attempts, created_at, updated_at)"
                " VALUES (?, ?, 'success', 100, '{}', 1, 1,"
                " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z')",
                (kind, f"门禁样本 {kind}"),
            )
            ids[kind] = int(cur.lastrowid or 0)
        conn.commit()

        listing = client.get(f"{V1}/tasks").get_json()["data"]
        options = {str(item["value"]): str(item["label"]) for item in listing["kinds"]}
        check("下拉覆盖全部已注册 kind", set(kinds) <= set(options),
              f"缺={sorted(set(kinds) - set(options))}")
        check("下拉值与注册表同源", options == {k: job_queue.KIND_LABELS[k] for k in kinds},
              str(options))
        check("下拉全是中文", all(CJK.search(label) for label in options.values()), str(options))

        bad = [
            kind for kind in kinds
            if not CJK.search(
                str(client.get(f"{V1}/tasks/{ids[kind]}").get_json()["data"]["kindLabel"])
            )
        ]
        check("任务详情 kindLabel 均为中文", not bad, str(bad))
    finally:
        conn.close()

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
