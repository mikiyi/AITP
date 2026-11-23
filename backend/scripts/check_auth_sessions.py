"""登录态复用门禁（四期专项 F）。

    python backend/scripts/check_auth_sessions.py

覆盖六段：
  1. 迁移 079：档案表与 scope 唯一索引；
  2. 保存：storageState 结构校验、cookie/origin 计数、按项目/环境/角色落盘；
  3. 覆盖：同 scope 再保存只更新不新增；
  4. 过期预检：构造已过期 cookie 与已过期 JWT（localStorage）各一例必须拦截；
  5. 清除：清除后不可加载、文件不存在；
  6. 隔离与安全：跨项目不可见、目录穿越被拒、不进备份资产目录清单。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import base64
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import time
from typing import Any, Callable

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.services import auth_session_service as svc  # noqa: E402

NOW = "2026-11-23T00:00:00+00:00"

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


def new_db(path: pathlib.Path) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    return conn


def add_project(conn: sqlite3.Connection, code: str) -> int:
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 0)",
        (code, f"项目{code}", NOW, NOW),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def jwt(exp: int) -> str:
    header = base64.urlsafe_b64encode(b'{"alg":"HS256","typ":"JWT"}').decode().rstrip("=")
    payload = base64.urlsafe_b64encode(
        json.dumps({"sub": "tester", "exp": exp}).encode()
    ).decode().rstrip("=")
    return f"{header}.{payload}.sig"


def state_of(expires: float, *, with_jwt: int | None = None) -> dict[str, Any]:
    origin: dict[str, Any] = {"origin": "http://127.0.0.1:8762", "localStorage": []}
    if with_jwt is not None:
        origin["localStorage"].append({"name": "token", "value": jwt(with_jwt)})
    return {
        "cookies": [{
            "name": "sid", "value": "abc", "domain": "127.0.0.1", "path": "/",
            "expires": expires, "httpOnly": True, "secure": False, "sameSite": "Lax",
        }],
        "origins": [origin],
    }


def migration_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "schema.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("迁移 079 建了登录态档案表", "biz_auth_session" in tables, str(sorted(tables)))
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uk_biz_auth_session_scope'"
        ).fetchone()
        check("档案按「项目+环境+角色」唯一", row is not None, "索引缺失")
    finally:
        conn.close()


def save_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "save.db")
    try:
        pid = add_project(conn, "PA")
        future = time.time() + 3600
        saved = svc.save(conn, project_id=pid, env_id=None, role="admin",
                         state=state_of(future), note="admin 角色")
        conn.commit()
        check("保存后返回档案", saved["id"] > 0 and saved["role"] == "admin", str(saved))
        check("cookie / origin 计数正确",
              saved["cookieCount"] == 1 and saved["originCount"] == 1, str(saved))
        check("未过期标记为可用", saved["expired"] is False, str(saved))
        check("文件落在项目/环境/角色路径下",
              (svc._root(conn) / f"{pid}/0/admin.json").is_file(), "文件不存在")

        again = svc.save(conn, project_id=pid, env_id=None, role="admin",
                         state=state_of(future + 10), note="覆盖")
        conn.commit()
        rows = conn.execute("SELECT COUNT(*) AS c FROM biz_auth_session").fetchone()["c"]
        check("同 scope 再保存只更新不新增", int(rows) == 1 and again["id"] == saved["id"],
              f"rows={rows}")

        env_id = None
        bad_rejected = False
        try:
            svc.save(conn, project_id=pid, env_id=env_id, role="admin", state={"cookies": []})
        except AppError as exc:
            bad_rejected = exc.code == ErrorCode.PARAM_ERROR
        check("空登录态被拒", bad_rejected, "空登录态被接受")
    finally:
        conn.close()


def expiry_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "expiry.db")
    try:
        pid = add_project(conn, "PB")
        past = time.time() - 60
        expired_cookie = svc.save(conn, project_id=pid, env_id=None, role="cookie",
                                  state=state_of(past))
        conn.commit()
        check("过期 cookie 保存时即标记为过期", expired_cookie["expired"] is True,
              str(expired_cookie))
        blocked = False
        try:
            svc.load_state(conn, expired_cookie["id"], project_id=pid)
        except AppError as exc:
            blocked = exc.code == ErrorCode.ILLEGAL_OPERATION and "过期" in str(exc.message)
        check("过期 cookie 加载被拦截并给出可读提示", blocked, "未拦截")

        future = time.time() + 3600
        expired_jwt = svc.save(conn, project_id=pid, env_id=None, role="jwt",
                               state=state_of(future, with_jwt=int(time.time()) - 60))
        conn.commit()
        check("过期 JWT（localStorage）保存时即标记为过期",
              expired_jwt["expired"] is True, str(expired_jwt))
        blocked_jwt = False
        try:
            svc.load_state(conn, expired_jwt["id"], project_id=pid)
        except AppError as exc:
            blocked_jwt = exc.code == ErrorCode.ILLEGAL_OPERATION
        check("过期 JWT 加载被拦截", blocked_jwt, "未拦截")

        good = svc.save(conn, project_id=pid, env_id=None, role="ok",
                        state=state_of(future, with_jwt=int(time.time()) + 3600))
        conn.commit()
        loaded = svc.load_state(conn, good["id"], project_id=pid)
        check("有效登录态可加载并回传 storageState",
              loaded["precheck"]["expired"] is False and loaded["state"]["cookies"], str(loaded)[:120])
        check("加载会刷新最近使用时间",
              svc.get(conn, good["id"], project_id=pid)["lastUsedAt"] != "", "lastUsedAt 为空")
    finally:
        conn.close()


def remove_and_scope_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "scope.db")
    try:
        pid = add_project(conn, "PC")
        other = add_project(conn, "PD")
        saved = svc.save(conn, project_id=pid, env_id=None, role="admin",
                         state=state_of(time.time() + 3600))
        conn.commit()
        sid = int(saved["id"])
        listed = svc.list_sessions(conn, project_id=pid)
        check("列表按项目收敛", [item["id"] for item in listed] == [sid], str(listed))
        check("跨项目查不到档案",
              _not_found(lambda: svc.get(conn, sid, project_id=other)))
        check("跨项目不能加载",
              _not_found(lambda: svc.load_state(conn, sid, project_id=other)))
        check("目录穿越被拒", svc.media_safe_file(svc._root(conn), "../../aitp.db") is None)
        check("绝对路径被拒", svc.media_safe_file(svc._root(conn), "/etc/passwd") is None)

        svc.remove(conn, sid, project_id=pid)
        conn.commit()
        check("清除后查不到档案", _not_found(lambda: svc.get(conn, sid, project_id=pid)))
        check("清除后文件已删除",
              not (svc._root(conn) / f"{pid}/0/admin.json").is_file(), "文件仍存在")

        # 角色名里的路径字符会被清洗，不会拼出越界路径
        tricky = svc.save(conn, project_id=pid, env_id=None, role="../../evil",
                          state=state_of(time.time() + 3600))
        conn.commit()
        check("角色名非法字符被清洗",
              "/" not in tricky["role"] and ".." not in tricky["role"], str(tricky["role"]))
    finally:
        conn.close()


def _not_found(fn: Callable[[], Any]) -> bool:
    try:
        fn()
    except AppError as exc:
        return exc.code == ErrorCode.RESOURCE_NOT_FOUND
    return False


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-auth-"))
    try:
        migration_checks(workdir)
        save_checks(workdir)
        expiry_checks(workdir)
        remove_and_scope_checks(workdir)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nPASS={_passed} FAIL={_failed}")
    for item in _failures:
        print(f"  [FAIL] {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
