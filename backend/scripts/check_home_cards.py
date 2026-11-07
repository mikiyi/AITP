"""首页卡片定制门禁：默认布局、脏配置清洗、显隐排序闭环与首页数据字段。

覆盖「设置 → 首页卡片定制」四条底线：
  1) 没配置时默认布局可用，固定区始终排最前且不参与定制；
  2) 配置被改坏（未知 key、重复、漏配、非 JSON）不抛错，自动洗成合法布局；
  3) 保存只落可定制区，接口重读的顺序与显隐完全一致；
  4) 首页候选卡依赖的 overview 字段（需求状态/缺陷级别/能力使用/最新报告）确实返回。

    python backend/scripts/check_home_cards.py

全程跑在临时库与临时目录上，不碰正式库、不重启 8760。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import socket
import sys
import tempfile
import time
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.common.enums import BUG_SEVERITY  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import home_card_service as cards  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: object = "") -> bool:
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

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def wait_port(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return True
        except OSError:
            time.sleep(0.2)
    return False


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_home_cards.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_home_cards.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def keys_of(layout: list[dict[str, Any]]) -> list[str]:
    return [str(item["key"]) for item in layout]


def custom_keys_of(layout: list[dict[str, Any]]) -> list[str]:
    return keys_of(layout)[len(cards.LOCKED_CARDS):]


# ---------------- 纯函数：布局清洗 ----------------
def check_layout_rules() -> None:
    default = cards.default_layout()
    check("默认布局 = 3 块固定区 + 9 张可定制卡", len(default) == 12, f"实际 {len(default)}")
    check(
        "固定区始终排在最前且锁定显示",
        all(item["locked"] and item["visible"] for item in default[:3]),
        str(default[:3]),
    )
    check(
        "可定制区按默认顺序紧跟固定区",
        custom_keys_of(default) == [card.key for card in cards.CUSTOMIZABLE_CARDS],
        custom_keys_of(default),
    )
    check(
        "order 连续无空洞",
        [int(item["order"]) for item in default] == list(range(len(default))),
        str([item["order"] for item in default]),
    )
    visible = {str(item["key"]): bool(item["visible"]) for item in default}
    check(
        "原有卡默认显示、新增候选卡默认隐藏",
        visible["recentTasks"] and visible["projectProgress"]
        and not visible["aiUsage"] and not visible["recentReports"],
        str(visible),
    )

    dirty = [
        {"key": "no_such_card", "visible": True},
        {"key": "assistant", "visible": False},
        {"key": "envHealth", "visible": False},
        {"key": "envHealth", "visible": True},
        {"key": "bugSeverity", "visible": True},
        "不是对象",
        {"visible": True},
    ]
    cleaned = cards.normalize(dirty)
    custom = custom_keys_of(cleaned)
    check("未知 key 与固定区 key 被丢弃", "no_such_card" not in custom and "assistant" not in custom, custom)
    check("重复卡片只保留第一次", custom.count("envHealth") == 1, custom)
    check(
        "漏配的卡片按默认顺序补齐",
        set(custom) == {card.key for card in cards.CUSTOMIZABLE_CARDS},
        custom,
    )
    check("隐藏状态取第一次出现的值", not visible_of(cleaned)["envHealth"], str(cleaned))
    check("非列表输入不抛错", custom_keys_of(cards.normalize("oops")) == custom_keys_of(default))
    check(
        "补齐后 order 仍连续",
        [int(item["order"]) for item in cleaned] == list(range(len(cleaned))),
        [item["order"] for item in cleaned],
    )


def visible_of(layout: list[dict[str, Any]]) -> dict[str, bool]:
    return {str(item["key"]): bool(item["visible"]) for item in layout}


# ---------------- 接口：读写闭环 ----------------
def check_api(app: Flask, workdir: pathlib.Path) -> None:
    api = Api(app)

    listed = api.data("GET", "/api/v1/settings/home-cards")
    layout = cast(list[dict[str, Any]], listed["list"])
    check("未配置时 GET 返回默认布局", custom_keys_of(layout) == custom_keys_of(cards.default_layout()), layout)

    order = ["envHealth", "recentReports", "bugSeverity", "aiUsage",
             "recentTasks", "caseStatus", "bugStatus", "projectProgress", "moduleTree"]
    payload = {"cards": [{"key": key, "visible": key not in ("aiUsage", "moduleTree")} for key in order]}
    saved = api.data("PUT", "/api/v1/settings/home-cards", payload)
    saved_layout = cast(list[dict[str, Any]], saved["list"])
    check("PUT 后顺序按提交顺序回填", custom_keys_of(saved_layout) == order, custom_keys_of(saved_layout))
    expected_visible = {key: key not in ("aiUsage", "moduleTree") for key in order}
    actual_visible = {
        str(item["key"]): bool(item["visible"]) for item in saved_layout if not item["locked"]
    }
    check("PUT 后显隐按提交值回填", actual_visible == expected_visible, str(actual_visible))

    reread = cast(list[dict[str, Any]], api.data("GET", "/api/v1/settings/home-cards")["list"])
    check("重读与保存结果一致", custom_keys_of(reread) == order, custom_keys_of(reread))

    stored = _stored_config(workdir)
    check("配置里不含固定区", "assistant" not in stored and "overview" not in stored, stored)
    check("配置是紧凑 JSON 数组", isinstance(json.loads(stored), list), stored)

    _, body = api.call("PUT", "/api/v1/settings/home-cards", {"cards": []})
    check("空数组被拒绝", body.get("code") == ErrorCode.PARAM_ERROR, str(body))
    _, body = api.call("PUT", "/api/v1/settings/home-cards", {"cards": "不是数组"})
    check("非数组被拒绝", body.get("code") == ErrorCode.PARAM_ERROR, str(body))
    _, body = api.call("PUT", "/api/v1/settings/home-cards", {})
    check("缺 cards 字段被拒绝", body.get("code") == ErrorCode.PARAM_ERROR, str(body))

    defaults = cast(list[dict[str, Any]], api.data("GET", "/api/v1/settings/home-cards?default=1")["list"])
    check("?default=1 忽略已保存配置返回默认布局",
          custom_keys_of(defaults) == custom_keys_of(cards.default_layout()), custom_keys_of(defaults))

    overview = cast(JsonDict, api.data("GET", "/api/v1/workbench/overview"))
    missing = [
        field for field in ("requirementStatus", "bugSeverity", "aiUsage", "recentReports")
        if field not in overview
    ]
    check("首页候选卡所需字段全部返回", not missing, f"缺少 {missing}")
    check("缺陷级别覆盖 P0–P3",
          set(cast(dict[str, Any], overview.get("bugSeverity", {}))) == set(BUG_SEVERITY),
          str(overview.get("bugSeverity")))


def _stored_config(workdir: pathlib.Path) -> str:
    """直连临时库读 home.cards 原文，确认落库内容不含固定区。"""
    import sqlite3

    db_path = workdir / "check_home_cards.db"
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT config_value FROM sys_config WHERE config_key = ?", (cards.HOME_CARDS_KEY,)
        ).fetchone()
    return str(row[0]) if row else ""


def main() -> int:
    # 日志句柄在 Windows 上不会随 app 释放，用 mkdtemp + ignore_errors 避免删不掉临时目录
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_home_cards_"))
    try:
        check_layout_rules()
        check_api(build_app(workdir), workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"\n合计：通过 {_passed}，失败 {_failed}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
