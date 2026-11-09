"""首启引导状态门禁（GAP-18，二期第 0 批 T0.1）。

    python backend/scripts/check_onboarding.py

跑临时数据库 + test_client，不依赖已启动的服务，不读写正式库。
断言：
  1) 全新库迁移 045 后 onboarding.completed=false（弹引导）、reopened_at 存在；
  2) 已有项目的库执行迁移后默认 true（老用户升级不打扰）；
  3) GET /api/v1/onboarding 返回 completed/reopenedAt/showGuide；
  4) POST complete/skip → completed=true 且 showGuide=false；POST reopen → false 且记录时间；
  5) 非法 action 报 PARAM_ERROR；
  6) 迁移可重复执行（幂等），键为 is_system=1。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402

JsonDict = dict[str, Any]

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


def build_app(workdir: pathlib.Path, name: str = "onboarding.db") -> Flask:
    db_path = workdir / name
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8766,
        debug=False,
        db_path=db_path,
        log_path=workdir / "onboarding.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def _config_row(conn: sqlite3.Connection, key: str) -> JsonDict:
    row = conn.execute(
        "SELECT config_value, is_system FROM sys_config WHERE config_key = ?", (key,)
    ).fetchone()
    return dict(row) if row else {}


def _project(conn: sqlite3.Connection, code: str) -> None:
    """插一个最小项目行，模拟存量库（列随迁移演进，只填非空必需列）。"""
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (code, "存量项目", now, now),
    )
    conn.commit()


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-onboarding-"))

    # 1) 全新库：completed=false
    fresh_db = workdir / "fresh.db"
    init_db(fresh_db)
    conn = sqlite3.connect(fresh_db)
    conn.row_factory = sqlite3.Row
    row = _config_row(conn, "onboarding.completed")
    check("全新库 completed=false", row.get("config_value") == "false", f"实际 {row}")
    check("completed 是系统键", row.get("is_system") == 1, f"实际 {row}")
    check("reopened_at 键已种入", _config_row(conn, "onboarding.reopened_at") != {}, "缺 key")

    # 2) 迁移幂等：重复执行不改值
    init_db(fresh_db)
    check("迁移重复执行幂等", _config_row(conn, "onboarding.completed").get("config_value") == "false")

    # 3) 已有项目后补迁移 → true（模拟老用户升级：建库→插项目→手动应用 045）
    legacy_db = workdir / "legacy.db"
    init_db(legacy_db)
    conn2 = sqlite3.connect(legacy_db)
    conn2.row_factory = sqlite3.Row
    conn2.execute("DELETE FROM sys_config WHERE config_key LIKE 'onboarding.%'")
    conn2.commit()
    _project(conn2, "LEGACY")
    sql_045 = (ROOT / "backend" / "aitp" / "db" / "migrations" / "045_onboarding_state.sql").read_text(
        encoding="utf-8"
    )
    conn2.executescript(sql_045)
    row2 = _config_row(conn2, "onboarding.completed")
    check("存量库补迁移默认 true", row2.get("config_value") == "true", f"实际 {row2}")
    conn.close()
    conn2.close()

    # 4) API 读写闭环
    app = build_app(workdir)
    client = app.test_client()
    resp = client.get("/api/v1/onboarding")
    body = resp.get_json(silent=True) or {}
    data = body.get("data") or {}
    check("GET 返回成功", body.get("code") == ErrorCode.SUCCESS, f"code={body.get('code')}")
    check("GET 新库 showGuide=true",
          data.get("completed") is False and data.get("showGuide") is True, f"data={data}")

    resp = client.post("/api/v1/onboarding", json={"action": "complete"})
    data = (resp.get_json(silent=True) or {}).get("data") or {}
    check("complete 后不再弹引导",
          data.get("completed") is True and data.get("showGuide") is False, f"data={data}")

    resp = client.post("/api/v1/onboarding", json={"action": "reopen"})
    data = (resp.get_json(silent=True) or {}).get("data") or {}
    check("reopen 后重新弹引导并记录时间",
          data.get("showGuide") is True and bool(data.get("reopenedAt")), f"data={data}")

    resp = client.post("/api/v1/onboarding", json={"action": "skip"})
    data = (resp.get_json(silent=True) or {}).get("data") or {}
    check("skip 后不再弹引导", data.get("completed") is True, f"data={data}")

    resp = client.post("/api/v1/onboarding", json={"action": "hack"})
    body = resp.get_json(silent=True) or {}
    check("非法 action 报参数错误", body.get("code") == ErrorCode.PARAM_ERROR,
          f"code={body.get('code')}")

    # 5) 示例注入 API：幂等且不碰其他项目
    inject_db = workdir / "inject.db"
    init_db(inject_db)
    conn3 = sqlite3.connect(inject_db)
    conn3.row_factory = sqlite3.Row
    _project(conn3, "REAL-KEEP")
    conn3.close()
    cfg = AppConfig(
        host="127.0.0.1", port=8766, debug=False, db_path=inject_db,
        log_path=workdir / "inject.log", deploy_log_path=workdir / "deploy2.log",
        secret_key_path=workdir / ".secret_key2",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    client2 = create_app(cfg).test_client()
    resp = client2.post("/api/v1/onboarding/demo")
    body = resp.get_json(silent=True) or {}
    data = body.get("data") or {}
    check("注入示例项目成功", body.get("code") == ErrorCode.SUCCESS and data.get("code") == "DEMO-EC",
          f"code={body.get('code')}")
    resp = client2.post("/api/v1/onboarding/demo")
    body = resp.get_json(silent=True) or {}
    check("二次注入幂等（重建示例自身）", body.get("code") == ErrorCode.SUCCESS,
          f"code={body.get('code')}")
    conn3 = sqlite3.connect(inject_db)
    codes = sorted(r[0] for r in conn3.execute("SELECT code FROM biz_project"))
    check("既有项目不受影响", codes == ["DEMO-EC", "REAL-KEEP"], f"实际 {codes}")
    scripts = conn3.execute(
        "SELECT COUNT(*) FROM biz_automation_script WHERE script_no = 'AUTO-API-001'"
    ).fetchone()[0]
    check("示例含自动化脚本", scripts == 1, f"实际 {scripts}")
    conn3.close()

    print(f"\n首启引导：PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
