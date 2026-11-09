"""GAP-22 密钥入库收口门禁：引用式存储、残留检测、迁移/回滚与双读行为。

    python backend/scripts/check_secret_residue.py

全部在临时目录里的临时 SQLite + build_app + test_client 上运行，绝不接触正式库。
断言组：
1. 新建/更新 AI 档案走正常接口后，库里 api_key_ref 有值、api_key_enc 为空，
   密钥只落在密钥库引用里，接口响应不回显密钥。
2. 残留扫描器「可红可绿」：注入一条 api_key_enc 残留后计数为 1（能红），
   迁移收口后计数为 0（能绿）——两个方向都断言，证明检查不是永远为真。
3. migrate_secrets / rollback_secrets 在副本上端到端：干跑不写、apply 收口、
   幂等重跑、回滚恢复旧形态、再次迁移回到引用形态。
4. 双读顺序：仅旧密文时 resolver 仍可解密；密文与引用并存时取引用；
   引用失效时按现有 ErrorCode（AI_CREDENTIAL_MISSING）报错。
5. resolver 兜底分支打出一次性 WARNING：含 profile id、绝不包含密钥本身。

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(ROOT / "backend" / "scripts"))

import migrate_secrets  # noqa: E402
import rollback_secrets  # noqa: E402
from aitp.ai import resolver  # noqa: E402
from aitp.ai.errors import AICredentialMissing  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.common.secret_store import get_store  # noqa: E402
from aitp.common.security import encrypt, mask  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []

NEW_KEY = "sk-residue-new-A1B2C3D4"
ROT_KEY = "sk-residue-rot-E5F6G7H8"
OLD_KEY = "sk-residue-legacy-I9J0K1L2"


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")


def build_app(workdir: pathlib.Path) -> tuple[Flask, pathlib.Path]:
    db_path = workdir / "check_secret_residue.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8765,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_secret_residue.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg), db_path


def row_snapshot(db_path: pathlib.Path, profile_id: int) -> JsonDict:
    conn = migrate_secrets.open_readonly(db_path)
    try:
        row = conn.execute(
            "SELECT api_key_ref, api_key_enc, api_key_masked FROM sys_ai_profile WHERE id = ?",
            (profile_id,),
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def residue_count(db_path: pathlib.Path) -> int:
    conn = migrate_secrets.open_readonly(db_path)
    try:
        return migrate_secrets.count_residue(migrate_secrets.fetch_profile_rows(conn))
    finally:
        conn.close()


# ---------------- 1. 正常接口写入收口 ----------------

def check_write_paths(api: Api, db_path: pathlib.Path) -> None:
    created = api.data("POST", "/api/v1/settings/ai/profiles", {
        "name": "收口门禁档案",
        "provider": "custom",
        "protocol": "openai_compat",
        "baseUrl": "https://example.invalid/v1",
        "model": "residue-check",
        "apiKey": NEW_KEY,
        "isActive": True,
        "priority": 5,
    })
    pid = int(created["id"])
    snap = row_snapshot(db_path, pid)
    check("新建档案后 api_key_ref 已回填", snap.get("api_key_ref") == f"ai_profile:{pid}", str(snap))
    check("新建档案后 api_key_enc 为空", not str(snap.get("api_key_enc") or ""), str(snap)[:80])
    check("密钥落在密钥库且值正确", get_store().get(f"ai_profile:{pid}") == NEW_KEY)
    check("库里只存掩码", snap.get("api_key_masked") == mask(NEW_KEY), str(snap.get("api_key_masked")))

    detail_resp, _ = api.call("GET", f"/api/v1/settings/ai/profiles/{pid}")
    raw = detail_resp.get_data(as_text=True)
    check("档案详情接口不回显密钥", NEW_KEY not in raw)

    api.data("PUT", f"/api/v1/settings/ai/profiles/{pid}", {"apiKey": ROT_KEY})
    snap = row_snapshot(db_path, pid)
    check("更新档案后 api_key_enc 仍为空", not str(snap.get("api_key_enc") or ""), str(snap)[:80])
    check("更新后密钥库引用值同步", get_store().get(f"ai_profile:{pid}") == ROT_KEY)

    api.data("POST", f"/api/v1/settings/ai/profiles/{pid}/api-key", {"apiKey": NEW_KEY})
    check("api-key 专用接口同样只写引用", get_store().get(f"ai_profile:{pid}") == NEW_KEY
          and not str(row_snapshot(db_path, pid).get("api_key_enc") or ""))
    check("收口后全库残留为 0", residue_count(db_path) == 0)


# ---------------- 2+3. 残留可红可绿 + 迁移/回滚端到端 ----------------

def inject_residue(db_path: pathlib.Path, profile_id: int) -> None:
    key_path = db_path.parent / ".secret_key"
    ciphertext = encrypt(OLD_KEY, key_path)
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(
            "UPDATE sys_ai_profile SET api_key_enc = ?, api_key_ref = NULL WHERE id = ?",
            (ciphertext, profile_id),
        )
        conn.commit()
    finally:
        conn.close()


def ref_row_count(db_path: pathlib.Path) -> int:
    conn = migrate_secrets.open_readonly(db_path)
    try:
        rows = migrate_secrets.fetch_profile_rows(conn)
        return sum(1 for row in rows if str(row.get("api_key_ref") or "").strip())
    finally:
        conn.close()


def check_migration_cycle(api: Api, db_path: pathlib.Path) -> None:
    created = api.data("POST", "/api/v1/settings/ai/profiles", {
        "name": "收口门禁历史档案",
        "provider": "custom",
        "protocol": "openai_compat",
        "baseUrl": "https://example.invalid/v1",
        "model": "residue-legacy",
        "isActive": True,
        "priority": 4,
    })
    pid = int(created["id"])
    key_path = db_path.parent / ".secret_key"

    clean_before = residue_count(db_path)
    inject_residue(db_path, pid)
    polluted = residue_count(db_path)
    # 一红一绿两个方向：扫描器若失效（恒 0 或恒 >0），必有一条断言变红
    check("残留扫描：干净库计数为 0（能绿）", clean_before == 0, str(clean_before))
    check("残留扫描：注入密文后计数为 1（能红）", polluted == 1, str(polluted))

    dry = migrate_secrets.migrate_db(db_path, apply=False, key_path=key_path)
    check("迁移干跑不写库", residue_count(db_path) == 1 and dry.migrated == 0)
    check("迁移干跑命中 1 行", dry.residue == 1, str(dry.residue))

    first = migrate_secrets.migrate_db(db_path, apply=True, key_path=key_path)
    snap = row_snapshot(db_path, pid)
    check("迁移 apply 后残留为 0", first.remaining == 0 and residue_count(db_path) == 0,
          str(first.remaining))
    check("迁移写入了引用与掩码", snap.get("api_key_ref") == f"ai_profile:{pid}"
          and bool(snap.get("api_key_masked")), str(snap)[:120])
    check("迁移后引用可读出原密钥", get_store().get(f"ai_profile:{pid}") == OLD_KEY)
    backup_file = pathlib.Path(first.backup_path)
    check("apply 前自动备份已落盘", backup_file.exists() and backup_file.parent.name == "backups",
          first.backup_path)

    second = migrate_secrets.migrate_db(db_path, apply=True, key_path=key_path)
    check("幂等：重复迁移命中 0 行且无失败", second.residue == 0 and second.failed == 0,
          str(second))
    check("幂等：引用与密钥库值未被重复写入改变",
          row_snapshot(db_path, pid).get("api_key_ref") == f"ai_profile:{pid}"
          and get_store().get(f"ai_profile:{pid}") == OLD_KEY)

    # 此时「收口门禁档案」（正常写入路径产物）与历史档案都持有引用：回滚面向全部引用行
    with_ref = ref_row_count(db_path)
    rb_dry = rollback_secrets.rollback_db(db_path, apply=False, key_path=key_path)
    snap = row_snapshot(db_path, pid)
    check("回滚干跑不写库", rb_dry.residue == with_ref
          and not str(snap.get("api_key_enc") or "") and bool(snap.get("api_key_ref")),
          f"residue={rb_dry.residue} with_ref={with_ref} snap={str(snap)[:120]}")
    rolled = rollback_secrets.rollback_db(db_path, apply=True, key_path=key_path)
    snap = row_snapshot(db_path, pid)
    check("回滚写回了 api_key_enc", rolled.migrated == with_ref and bool(str(snap.get("api_key_enc") or "")),
          f"migrated={rolled.migrated} with_ref={with_ref} snap={str(snap)[:120]}")
    check("回滚清空了引用（恢复迁移前形态）", not snap.get("api_key_ref") and ref_row_count(db_path) == 0)
    check("回滚后残留计数=引用行数（回到旧形态）", residue_count(db_path) == with_ref,
          f"residue={residue_count(db_path)} with_ref={with_ref}")

    rb_again = rollback_secrets.rollback_db(db_path, apply=True, key_path=key_path)
    check("幂等：重复回滚命中 0 行（引用已清空）", rb_again.residue == 0 and rb_again.failed == 0,
          str(rb_again))

    re_migrated = migrate_secrets.migrate_db(db_path, apply=True, key_path=key_path)
    check("回滚后可再次迁移收口", re_migrated.migrated == with_ref
          and residue_count(db_path) == 0
          and get_store().get(f"ai_profile:{pid}") == OLD_KEY)


# ---------------- 4. 双读顺序 ----------------

def base_profile(profile_id: int) -> JsonDict:
    return {
        "id": profile_id,
        "provider": "custom",
        "protocol": "openai_compat",
        "base_url": "https://example.invalid/v1",
        "model": "dual-read",
    }


def check_dual_read(db_path: pathlib.Path) -> None:
    key_path = db_path.parent / ".secret_key"

    legacy_only = base_profile(900001)
    legacy_only["api_key_enc"] = encrypt(OLD_KEY, key_path)
    resolved = resolver.resolve(profile=legacy_only, key_path=key_path)
    check("双读：仅旧密文时仍可解密取到密钥", resolved.api_key == OLD_KEY)

    both = base_profile(900002)
    get_store().set("ai_profile:900002", NEW_KEY)
    both["api_key_ref"] = "ai_profile:900002"
    both["api_key_enc"] = encrypt(OLD_KEY, key_path)
    resolved = resolver.resolve(profile=both, key_path=key_path)
    check("双读：引用与密文并存时取引用（引用优先）", resolved.api_key == NEW_KEY,
          resolved.api_key[:6] + "…")

    broken = base_profile(900003)
    broken["provider"] = "openai"
    broken["api_key_ref"] = "ai_profile:900003-missing"
    try:
        resolver.resolve(profile=broken, key_path=key_path, require_key=True)
        check("双读：引用失效时报凭证缺失错误", False, "未抛异常")
    except AICredentialMissing as exc:
        check("双读：引用失效时报凭证缺失错误", exc.code == ErrorCode.AI_CREDENTIAL_MISSING,
              f"code={exc.code}")


# ---------------- 5. 兜底 WARNING ----------------

class _Capture:
    def __init__(self) -> None:
        self.messages: list[str] = []

    def emit(self, record: Any) -> None:
        self.messages.append(record.getMessage())


def check_residual_warning(db_path: pathlib.Path) -> None:
    import logging

    key_path = db_path.parent / ".secret_key"
    logger = logging.getLogger("aitp.ai.resolver")
    capture = _Capture()
    handler = logging.Handler()
    handler.emit = capture.emit  # type: ignore[method-assign]
    original_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.WARNING)
    try:
        profile = base_profile(900004)
        profile["api_key_enc"] = encrypt(OLD_KEY, key_path)
        resolver.resolve(profile=profile, key_path=key_path)
        warned = any("api_key_enc" in message and "900004" in message for message in capture.messages)
        check("兜底命中时打出含 profile id 的 WARNING", warned, str(capture.messages)[:200])
        leaked = any(OLD_KEY in message for message in capture.messages)
        check("WARNING 不含密钥内容", not leaked)
        resolver.resolve(profile=profile, key_path=key_path)
        check("WARNING 按档案去重（低频，不逐次刷屏）",
              sum(1 for message in capture.messages if "900004" in message) == 1,
              str(capture.messages)[:200])
    finally:
        logger.removeHandler(handler)
        logger.setLevel(original_level)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_residue_"))
    try:
        app, db_path = build_app(workdir)
        api = Api(app)
        check_write_paths(api, db_path)
        check_migration_cycle(api, db_path)
        check_dual_read(db_path)
        check_residual_warning(db_path)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
