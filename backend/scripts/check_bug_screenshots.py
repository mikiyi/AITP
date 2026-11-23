"""缺陷编号与截图门禁。

    python backend/scripts/check_bug_screenshots.py

覆盖四段：
  1. 迁移 074：来源列、缺陷号全局唯一索引、截图登记表与索引；
  2. 取号口径：三类前缀、跨项目全局唯一、软删不复用、取号进写事务；
  3. 截图：命名 pic_<编号>_001、单张 10MB / 单号 30 张、非法内容拒绝、
     删除后序号不回退；
  4. 开关：默认不进整包 / 项目资产包 / 留存，打开后三处都带上。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import MIGRATIONS_DIR, init_db, run_migrations  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.repositories.bug_repo import BugRepository  # noqa: E402
from aitp.repositories.numbering import next_no_monotonic  # noqa: E402
from aitp.services import bug_screenshot_service, retention_service  # noqa: E402

V1 = "/api/v1"
NOW = "2026-11-23T00:00:00+00:00"
PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
    b"\x08\x06\x00\x00\x00\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\xcf"
    b"\xc0\x00\x00\x03\x01\x01\x00\x18\xdd\x8d\xb0\x00\x00\x00\x00IEND\xaeB`\x82"
)

_passed = 0
_failed = 0
_failures: list[str] = []


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


def new_db(path: pathlib.Path) -> sqlite3.Connection:
    init_db(path)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=OFF")
    return conn


def add_project(conn: sqlite3.Connection, code: str) -> int:
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, 0)",
        (code, f"项目{code}", NOW, NOW),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def insert_bug(conn: sqlite3.Connection, project_id: int, bug_no: str,
               source: str = "manual") -> int:
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(biz_bug)")}
    if "source" in columns:
        conn.execute(
            "INSERT INTO biz_bug (project_id, bug_no, source, title, created_at, updated_at,"
            " is_deleted) VALUES (?, ?, ?, ?, ?, ?, 0)",
            (project_id, bug_no, source, f"缺陷{bug_no}", NOW, NOW),
        )
    else:
        conn.execute(
            "INSERT INTO biz_bug (project_id, bug_no, title, created_at, updated_at,"
            " is_deleted) VALUES (?, ?, ?, ?, ?, 0)",
            (project_id, bug_no, f"缺陷{bug_no}", NOW, NOW),
        )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def build_app(workdir: pathlib.Path) -> Any:
    db_path = workdir / "shots.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "shots.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def schema_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "schema.db")
    try:
        columns = {row["name"] for row in conn.execute("PRAGMA table_info(biz_bug)")}
        check("迁移 074 给缺陷补 source 列", "source" in columns, str(sorted(columns)))
        check("迁移 074 给缺陷补 source_ref 列", "source_ref" in columns, str(sorted(columns)))
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uk_biz_bug_no_global'"
        ).fetchone()
        check("缺陷号建了全局唯一索引", row is not None, "索引缺失")
        check(
            "唯一索引只约束未删除行（软删不占索引）",
            row is not None and "is_deleted = 0" in str(row["sql"]),
            str(row["sql"] if row else ""),
        )
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("截图登记表存在", "biz_bug_screenshot" in tables, str(sorted(tables)))
        indexes = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        check(
            "截图表有单号序号与文件名唯一索引",
            {"uk_bug_screenshot_seq", "uk_bug_screenshot_file"} <= indexes,
            str(sorted(indexes)),
        )
    finally:
        conn.close()


def numbering_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "numbering.db")
    try:
        repo = BugRepository(conn)
        pa = add_project(conn, "PA")
        pb = add_project(conn, "PB")
        manual = repo.next_bug_no("manual")
        ai = repo.next_bug_no("ai")
        auto = repo.next_bug_no("automation")
        check("手工缺陷取 BUG-001", manual == "BUG-001", manual)
        check("AI 缺陷取 AI_BUG-001", ai == "AI_BUG-001", ai)
        check("自动化缺陷取 Auto_BUG-001", auto == "Auto_BUG-001", auto)

        insert_bug(conn, pa, manual, "manual")
        insert_bug(conn, pb, ai, "ai")
        insert_bug(conn, pb, auto, "automation")
        conn.commit()

        blocked = False
        try:
            insert_bug(conn, pb, "BUG-001", "manual")
            conn.commit()
        except sqlite3.IntegrityError:
            conn.rollback()
            blocked = True
        check("同一缺陷号跨项目也被拦下（全局唯一）", blocked, "跨项目插入了同名缺陷号")

        conn.execute("UPDATE biz_bug SET is_deleted = 1 WHERE project_id = ?", (pa,))
        conn.commit()
        after_delete = repo.next_bug_no("manual")
        check("软删后编号不复用（顺延到 BUG-002）", after_delete == "BUG-002", after_delete)

        # 取号进写事务：第二个连接在第一个持锁期间拿不到号
        race = workdir / "race.db"
        conn_a = new_db(race)
        conn_b = sqlite3.connect(race)
        conn_b.row_factory = sqlite3.Row
        conn_a.execute("PRAGMA busy_timeout=1000")
        conn_b.execute("PRAGMA busy_timeout=250")
        first = BugRepository(conn_a).next_bug_no("manual")
        waited = False
        try:
            BugRepository(conn_b).next_bug_no("manual")
        except sqlite3.OperationalError:
            waited = True
        check("取号期间第二个连接被写锁挡住", waited, "第二个连接立刻取到号，取号仍在事务外")
        conn_a.rollback()
        again = BugRepository(conn_b).next_bug_no("manual")
        check("释放后仍取到同一空号", again == first, f"{first} vs {again}")
        conn_a.close()
        conn_b.close()

        nxt = next_no_monotonic(conn, "biz_bug", "bug_no", "AI_BUG")
        check("AI_BUG 前缀独立计数", nxt == "AI_BUG-002", nxt)

        # 并发上传同一缺陷：序号读-改-写必须与取号一样串行，后到的连接先被挡再顺延
        shots_db = workdir / "race-shots" / "shots.db"
        shots_db.parent.mkdir()
        conn_a = new_db(shots_db)
        conn_b = sqlite3.connect(shots_db)
        conn_b.row_factory = sqlite3.Row
        conn_a.execute("PRAGMA busy_timeout=1000")
        conn_b.execute("PRAGMA busy_timeout=250")
        pc = add_project(conn_a, "PC")
        race_bug_id = insert_bug(conn_a, pc, "BUG-001")
        conn_a.commit()
        race_bug = {"id": race_bug_id, "bug_no": "BUG-001"}
        shot_first = bug_screenshot_service.save_screenshots(conn_a, race_bug, [("a.png", PNG)])
        waited = False
        try:
            bug_screenshot_service.save_screenshots(conn_b, race_bug, [("b.png", PNG)])
        except sqlite3.OperationalError:
            waited = True
        check("并发上传截图时第二个连接被写锁挡住", waited, "两个连接同时算出了同一个 seq")
        conn_a.commit()
        shot_second = bug_screenshot_service.save_screenshots(conn_b, race_bug, [("b.png", PNG)])
        check(
            "释放后截图序号顺延到 002",
            shot_first[0]["seq"] == 1 and shot_second[0]["seq"] == 2,
            f"{shot_first[0]['fileName']} / {shot_second[0]['fileName']}",
        )
        conn_a.close()
        conn_b.close()
    finally:
        conn.close()


def legacy_upgrade_checks(workdir: pathlib.Path) -> None:
    """老库（073 及以前）跨项目撞号，覆盖安装 074 能自动消歧。"""
    old_dir = workdir / "migrations_old"
    old_dir.mkdir()
    for path in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if int(path.name.split("_", 1)[0]) < 74:
            shutil.copy(path, old_dir / path.name)
    db = workdir / "legacy.db"
    init_db(db, migrations_dir=old_dir)
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    try:
        pa = add_project(conn, "PA")
        pb = add_project(conn, "PB")
        insert_bug(conn, pa, "BUG-001")
        insert_bug(conn, pa, "BUG-002")
        insert_bug(conn, pb, "BUG-001")
        insert_bug(conn, pb, "BUG-002")
        conn.commit()
        applied = run_migrations(conn)
        check("覆盖安装会补跑 074/075", 74 in applied and 75 in applied, str(sorted(applied)))
        rows = [r["bug_no"] for r in conn.execute(
            "SELECT bug_no FROM biz_bug WHERE is_deleted = 0 ORDER BY id")]
        check("消歧后缺陷号全局无重复", len(rows) == len(set(rows)), str(rows))
        check("撞号行保留 id 最小的一条", rows[0] == "BUG-001" and rows[1] == "BUG-002", str(rows))
        check("被重排的行顺延到最大号之后",
              all(value not in ("BUG-001", "BUG-002") for value in rows[2:]), str(rows))
    finally:
        conn.close()


def api_checks(workdir: pathlib.Path) -> tuple[Any, int, int, str]:
    app = build_app(workdir)
    client = app.test_client()
    project = client.post(f"{V1}/projects", json={"code": "SHOT", "name": "截图项目"}).get_json()
    project_id = int(project["data"]["id"])
    bug = client.post(f"{V1}/bugs", json={
        "projectId": project_id, "title": "截图能力验证",
    }).get_json()
    bug_id = int(bug["data"]["id"])
    bug_no = str(bug["data"]["bugNo"])
    check("手工建缺陷编号为 BUG- 前缀", bug_no.startswith("BUG-"), bug_no)

    uploaded = client.post(
        f"{V1}/bugs/{bug_id}/screenshots?projectId={project_id}",
        data={"files": [
            (io.BytesIO(PNG), "first.png"),
            (io.BytesIO(PNG + b"x"), "second.png"),
        ]},
        content_type="multipart/form-data",
    ).get_json()
    shots = uploaded.get("data", {}).get("list") or []
    names = [item["fileName"] for item in shots]
    check("一次性上传两张截图", len(shots) == 2, str(uploaded)[:200])
    check(
        "文件名为 pic_<编号>_001/002",
        names == [f"pic_{bug_no}_001.png", f"pic_{bug_no}_002.png"],
        str(names),
    )
    folder = workdir / "bug-screenshots" / bug_no
    check("截图落盘到 data/bug-screenshots/<编号>/",
          (folder / names[0]).is_file() and (folder / names[1]).is_file(),
          str(sorted(p.name for p in folder.glob("*"))) if folder.is_dir() else "目录不存在")

    listed = client.get(f"{V1}/bugs/{bug_id}/screenshots?projectId={project_id}").get_json()
    check("截图列表可读", len(listed["data"]["list"]) == 2, str(listed)[:200])
    raw = client.get(f"{V1}/bugs/{bug_id}/screenshots/{shots[0]['id']}/file?projectId={project_id}")
    check("截图文件以图片内联返回",
          raw.status_code == 200 and raw.data == PNG
          and str(raw.headers.get("Content-Type", "")).startswith("image/png"),
          f"{raw.status_code} {raw.headers.get('Content-Type')}")

    invalid = client.post(
        f"{V1}/bugs/{bug_id}/screenshots?projectId={project_id}",
        data={"files": [(io.BytesIO(b"<html><script>alert(1)</script></html>"), "evil.png")]},
        content_type="multipart/form-data",
    ).get_json()
    check("伪装成 png 的 HTML 被拒绝", invalid["code"] == ErrorCode.PARAM_ERROR, str(invalid)[:200])

    huge = client.post(
        f"{V1}/bugs/{bug_id}/screenshots?projectId={project_id}",
        data={"files": [(io.BytesIO(PNG + b"0" * (10 * 1024 * 1024)), "huge.png")]},
        content_type="multipart/form-data",
    ).get_json()
    check("超过 10MB 的截图被拒绝", huge["code"] == ErrorCode.PARAM_ERROR, str(huge)[:200])

    deleted = client.delete(
        f"{V1}/bugs/{bug_id}/screenshots/{shots[0]['id']}?projectId={project_id}"
    ).get_json()
    check("删除截图登记成功", deleted["code"] == ErrorCode.SUCCESS, str(deleted)[:200])
    check("删除后磁盘文件同步移除", not (folder / names[0]).exists(), names[0])
    again = client.post(
        f"{V1}/bugs/{bug_id}/screenshots?projectId={project_id}",
        data={"files": [(io.BytesIO(PNG), "third.png")]},
        content_type="multipart/form-data",
    ).get_json()
    third = [item["fileName"] for item in again["data"]["list"]]
    check("删除后序号不回退（下一张为 003）",
          third == [f"pic_{bug_no}_003.png"], str(third))

    # 单号 30 张上限：再补 28 条登记，凑满 30 后上传被拒
    conn = sqlite3.connect(workdir / "shots.db")
    try:
        for seq in range(4, 32):
            conn.execute(
                "INSERT INTO biz_bug_screenshot (bug_id, seq, file_name, original_name,"
                " mime, size_bytes, sha256, created_at, is_deleted)"
                " VALUES (?, ?, ?, 'seed', 'image/png', 1, '', ?, 0)",
                (bug_id, seq, f"pic_{bug_no}_{seq:03d}.png", NOW),
            )
        conn.commit()
    finally:
        conn.close()
    full = client.post(
        f"{V1}/bugs/{bug_id}/screenshots?projectId={project_id}",
        data={"files": [(io.BytesIO(PNG), "overflow.png")]},
        content_type="multipart/form-data",
    ).get_json()
    check("超过 30 张的截图被拒绝", full["code"] == ErrorCode.PARAM_ERROR, str(full)[:200])
    return app, project_id, bug_id, bug_no


def switch_checks(workdir: pathlib.Path, app: Any, project_id: int, bug_id: int,
                  bug_no: str) -> None:
    client = app.test_client()

    def set_switch(flag: bool) -> None:
        client.put(f"{V1}/settings/config", json={"items": [{
            "key": "backup.include_bug_screenshots",
            "value": "true" if flag else "false",
            "valueType": "boolean",
        }]})

    def bundle_names() -> set[str]:
        info = client.post(f"{V1}/settings/backups/bundle").get_json()
        name = str(info["data"]["name"])
        with zipfile.ZipFile(workdir / "backups" / name) as archive:
            return set(archive.namelist())

    set_switch(False)
    names_off = bundle_names()
    check("开关关闭时整包不含缺陷截图",
          not any(name.startswith("bug-screenshots/") for name in names_off),
          str(sorted(n for n in names_off if n.startswith("bug-screenshots/"))))
    set_switch(True)
    names_on = bundle_names()
    check("开关打开时整包带上缺陷截图",
          any(name.startswith(f"bug-screenshots/{bug_no}/") for name in names_on),
          str(sorted(n for n in names_on if n.startswith("bug-screenshots/"))))

    exported = client.post(f"{V1}/project-packages/export", json={"projectId": project_id})
    package_on = zipfile.ZipFile(io.BytesIO(client.post(
        f"{V1}/project-packages/export", json={"projectId": project_id}).data))
    manifest_on = json.loads(package_on.read("manifest.json").decode("utf-8"))
    check("开关打开时项目资产包声明包含截图", manifest_on["scope"]["screenshots"] is True,
          str(manifest_on["scope"]))
    check("资产包内截图按编号分目录",
          any(name.startswith(f"screenshots/{bug_no}/") for name in package_on.namelist()),
          str(package_on.namelist())[:300])
    set_switch(False)
    package_off = zipfile.ZipFile(io.BytesIO(client.post(
        f"{V1}/project-packages/export", json={"projectId": project_id}).data))
    manifest_off = json.loads(package_off.read("manifest.json").decode("utf-8"))
    check("开关关闭时项目资产包不含截图",
          manifest_off["scope"]["screenshots"] is False
          and not any(n.startswith("screenshots/") for n in package_off.namelist()),
          str(manifest_off["scope"]))
    check("导出接口始终可用", exported.status_code == 200 and exported.data[:2] == b"PK",
          f"{exported.status_code} {exported.data[:8]!r}")

    # 留存：关闭时到期量为 0；打开后归档会把图片本体一起打进 zip
    conn = sqlite3.connect(workdir / "shots.db")
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("UPDATE biz_bug_screenshot SET created_at = '2020-01-01T00:00:00Z'")
        conn.commit()
        retention_service.update_settings(conn, {"items": [{
            "dataType": "bug_screenshot", "days": 30, "action": "archive",
        }]})
        conn.commit()
        preview_off = retention_service.preview(conn)
        off_item = next(i for i in preview_off["items"] if i["dataType"] == "bug_screenshot")
        check("开关关闭时截图没有到期项", off_item["dueCount"] == 0, str(off_item["dueCount"]))

        set_switch(True)
        conn.commit()
        preview_on = retention_service.preview(conn)
        on_item = next(i for i in preview_on["items"] if i["dataType"] == "bug_screenshot")
        check("开关打开后截图纳入到期清单", on_item["dueCount"] > 0, str(on_item["dueCount"]))

        summary = retention_service.run_cleanup(
            conn, workdir / "shots.db", data_types=["bug_screenshot"], created_by="gate")
        archived = summary["types"][0]["archived"]
        check("截图按动作归档", archived > 0, str(summary["types"][0]))
        bundle = summary["types"][0]["bundles"][0]
        with zipfile.ZipFile(workdir / bundle["filePath"]) as archive:
            check("归档 zip 里保留图片本体",
                  any(name.startswith(f"files/{bug_no}/") for name in archive.namelist()),
                  str(archive.namelist())[:300])
        left = conn.execute(
            "SELECT COUNT(*) FROM biz_bug_screenshot WHERE bug_id = ? AND is_deleted = 0",
            (bug_id,)).fetchone()[0]
        check("归档后截图登记被软删", int(left) == 0, str(left))
    finally:
        conn.close()


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-check-shots-"))
    try:
        schema_checks(workdir)
        numbering_checks(workdir)
        legacy_upgrade_checks(workdir)
        app, project_id, bug_id, bug_no = api_checks(workdir)
        switch_checks(workdir, app, project_id, bug_id, bug_no)
        return 1 if _failed else 0
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    code = main()
    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    raise SystemExit(code)
