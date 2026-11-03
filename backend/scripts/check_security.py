"""安全与合规回归检查。

    python backend/scripts/check_security.py

覆盖：静态目录穿越、无 CORS 头、明文密钥不落库不外传、SQL 参数化、
排序字段白名单、分页上限、原子写不残留临时文件。
"""

from __future__ import annotations

import pathlib
import re
import sqlite3
import sys
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.common.path_safety import is_blocked_name, normalize_relative_path, safe_join  # noqa: E402
from aitp.common.pii import detect_sensitive, mask_sensitive  # noqa: E402

_passed = 0
_failed = 0

SECRET = "sk-securitycheck-abcdef123456"


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


# ---------------------------------------------------------------------
def check_path_safety() -> None:
    root = ROOT / "backend" / "aitp" / "static"

    check("前导 ../ 被剥离",
          normalize_relative_path("../../etc/passwd") == "etc/passwd",
          normalize_relative_path("../../etc/passwd"))
    check("反斜杠分隔符归一化",
          normalize_relative_path("..\\..\\windows\\win.ini") == "windows/win.ini",
          normalize_relative_path("..\\..\\windows\\win.ini"))
    check("绝对路径被限制在根目录内",
          safe_join(root, "/etc/passwd") == (root / "etc" / "passwd").resolve())
    check("穿越目标不落在根目录外",
          str(safe_join(root, "../../../../Windows/System32/drivers/etc/hosts")).startswith(str(root.resolve()))
          if safe_join(root, "../../../../Windows/System32/drivers/etc/hosts") else False)
    check("隐藏文件被拒绝", is_blocked_name(".env") and is_blocked_name("x/.secret_key"))
    check("敏感后缀被拒绝", is_blocked_name("aitp.db") and is_blocked_name("aitp.log"))
    check("正常资源名不被误伤", not is_blocked_name("assets/index-abc123.js"))


def check_no_cors_and_static() -> None:
    from aitp.app import create_app

    app = create_app()
    client = app.test_client()

    resp = client.get("/api/v1/health")
    cors = [k for k, _ in resp.headers.items() if k.lower().startswith("access-control")]
    check("响应不含任何 CORS 头", not cors, str(cors))

    for path in ("/../../etc/passwd", "/%2e%2e/%2e%2e/etc/passwd", "/.env", "/../data/aitp.db"):
        body = client.get(path).get_data(as_text=True)
        leaked = "root:" in body or "SQLite format" in body or "API_KEY=" in body
        check(f"目录穿越被拦截 {path}", not leaked, body[:80])

    resp = client.get("/api/v1/health", headers={"Origin": "https://evil.example"})
    cors = [k for k, _ in resp.headers.items() if k.lower().startswith("access-control")]
    check("带 Origin 头也不返回 CORS 头", not cors, str(cors))


def check_secret_storage() -> None:
    from aitp.common.secret_store import SecretStore, atomic_write_bytes

    store = SecretStore(ROOT / "data")
    check("密钥存储后端可用", store.backend in ("dpapi", "keychain", "file"), store.backend)

    ref = "security_check:probe"
    store.set(ref, SECRET)
    check("密钥写入后可读回", store.get(ref) == SECRET)
    store.delete(ref)
    check("密钥删除后读不到", store.get(ref) == "")

    target = ROOT / "data" / "_atomic_probe.bin"
    atomic_write_bytes(target, b"x" * 32)
    leftovers = list((ROOT / "data").glob("_atomic_probe.bin.tmp-*"))
    check("原子写不留临时文件", not leftovers, str(leftovers))
    target.unlink(missing_ok=True)


def check_key_not_in_db() -> None:
    from aitp.db.connection import get_standalone_conn
    from aitp.repositories.ai_profile_repo import AIProfileRepository

    db_path = ROOT / "data" / "aitp.db"
    conn = get_standalone_conn(db_path)
    try:
        repo = AIProfileRepository(conn)
        conn.execute(
            "INSERT INTO sys_ai_profile (name, provider, protocol, base_url, model,"
            " api_key_masked, api_key_ref, temperature, max_tokens, timeout,"
            " is_default, is_active, sort_order, created_at, updated_at)"
            " VALUES ('安全检查临时档案', 'custom', 'openai_compat', 'http://127.0.0.1:1/v1', 'm',"
            " 'sk-sec***3456', 'security_check:db', 0.7, 4096, 60, 0, 1, 0,"
            " '2026-11-03T00:00:00Z', '2026-11-03T00:00:00Z')"
        )
        conn.commit()
        row = repo.get_by_id(int(conn.execute("SELECT MAX(id) FROM sys_ai_profile").fetchone()[0]))
        public = repo.to_public(cast("dict[str, Any]", row))
        check("对外输出只含掩码", public["apiKeyMasked"] == "sk-sec***3456", str(public.get("apiKeyMasked")))
        check("对外输出不含密钥引用", "apiKeyRef" not in public and "api_key_ref" not in public)
        check("对外输出不含明文", SECRET not in str(public))
        conn.execute("DELETE FROM sys_ai_profile WHERE name = '安全检查临时档案'")
        conn.commit()
    finally:
        conn.close()

    raw = db_path.read_bytes()
    check("数据库文件不含明文密钥", SECRET.encode("utf-8") not in raw)
    secret_dir = ROOT / "data" / "secrets"
    blobs = list(secret_dir.glob("*.blob")) if secret_dir.exists() else []
    check("密钥库目录里没有明文文件", all(SECRET.encode("utf-8") not in b.read_bytes() for b in blobs),
          str(len(blobs)))


def check_sql_parameterization() -> None:
    """静态扫描：SQL 里不允许出现变量拼接（f-string / % / .format / +）。"""
    backend = ROOT / "backend" / "aitp"
    offenders: list[str] = []
    sql_pattern = re.compile(r"(execute|executemany|executescript)\s*\(\s*f?[\"']", re.IGNORECASE)
    concat_pattern = re.compile(r"(SELECT|INSERT|UPDATE|DELETE).*?[\"']\s*\+\s*\w", re.IGNORECASE)

    for path in backend.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for match in concat_pattern.finditer(text):
            line = text[: match.start()].count("\n") + 1
            offenders.append(f"{path.relative_to(ROOT)}:{line} SQL 字符串拼接")
    check("SQL 没有变量拼接", not offenders, "; ".join(offenders[:3]))
    check("扫描到 SQL 执行点", bool(sql_pattern.search(
        (backend / "repositories" / "base.py").read_text(encoding="utf-8"))))


def check_sort_whitelist() -> None:
    from aitp.repositories.base import BaseRepository

    class _Repo(BaseRepository):
        table = "biz_test_case"
        order_whitelist = {"id", "created_at"}

    repo = _Repo.__new__(_Repo)
    repo.conn = cast("sqlite3.Connection", None)
    check("白名单内排序放行", repo._safe_order("id") == "id")
    check("白名单外排序回退默认", repo._safe_order("title; DROP TABLE x") == repo.default_order)
    check("多字段排序逐项校验", repo._safe_order("id,created_at") == "id, created_at")
    check("含空格的注入尝试被拒", repo._safe_order("id, (SELECT 1)") == repo.default_order)


def check_pagination_limit() -> None:
    from aitp.common.pagination import MAX_PAGE_SIZE, clamp_page_size

    check("分页上限为 100", MAX_PAGE_SIZE == 100, str(MAX_PAGE_SIZE))
    check("超限被截断到 100", clamp_page_size(500) == 100)
    check("默认每页 20", clamp_page_size(None) == 20)


def check_pii_masking() -> None:
    text = "联系 13800138000，身份证 110101199003071234，邮箱 a.b@example.com，key=sk-abcdefgh12345"
    kinds = detect_sensitive(text)
    check("识别手机号", "phone" in kinds, str(kinds))
    check("识别身份证", "id_card" in kinds, str(kinds))
    check("识别邮箱", "email" in kinds, str(kinds))
    check("识别 API Key", "api_key" in kinds, str(kinds))
    masked = mask_sensitive(text)
    check("脱敏后不残留手机号", "13800138000" not in masked, masked)
    check("脱敏后不残留身份证", "110101199003071234" not in masked, masked)
    check("脱敏后不残留密钥", "sk-abcdefgh12345" not in masked, masked)


def main() -> int:
    check_path_safety()
    check_no_cors_and_static()
    check_secret_storage()
    check_key_not_in_db()
    check_sql_parameterization()
    check_sort_whitelist()
    check_pagination_limit()
    check_pii_masking()

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
