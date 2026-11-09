"""GAP-22 AI 档案密钥入库收口：把历史 api_key_enc 密文迁移到引用式密钥库。

    python backend/scripts/migrate_secrets.py [--db PATH] [--dry-run | --apply]
        [--key-path PATH] [--allow-production]

行为约定：
- 默认干跑（不传 --apply 即为干跑）：只扫描并打印「会改哪些行、执行什么动作」，不写库。
- --apply 前自动整库快照（VACUUM INTO 单文件）到 <库所在目录>/backups/，
  复用 backup_service 的命名约定（pre-restore 安全快照前缀 + 时间戳，冲突追加 -NN）。
- 幂等：重复执行不产生重复引用；库里已有可用 api_key_ref 的行只清残留密文，
  不会覆盖密钥库里已有的值。
- 只处理 sys_ai_profile 表中 api_key_enc 非空的行（含软删档案——备份文件同样泄露它们）。
- 拒绝贸然写：目标是正式库（data/aitp.db 或 AITP_DB_PATH 指向的库）且未传
  --allow-production 时中止；正式库还额外探测服务端口，监听中即中止；
  任何目标 --apply 前都探测库写锁（BEGIN IMMEDIATE），被占用即中止。

删除条件（与 resolver 双读兜底配套）：正式库与全部开发副本上
``migrate_secrets.py --dry-run`` 残留为 0，且一个版本内 resolver 不再打出
「检测到残留 api_key_enc」WARNING，则下一版本删除 resolver 的旧密文兜底分支。
"""

from __future__ import annotations

import argparse
import socket
import sqlite3
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, cast

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.errors import AIKeyDecryptFailed  # noqa: E402
from aitp.common.secret_store import SecretStore  # noqa: E402
from aitp.common.security import decrypt, mask  # noqa: E402
from aitp.config import PROJECT_ROOT, load_config  # noqa: E402
from aitp.repositories.ai_profile_repo import AIProfileRepository  # noqa: E402

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

#: 与 backup_service 相同的备份目录与安全快照命名约定（不另起一套）
BACKUP_SUBDIR = "backups"
SAFETY_PREFIX = "pre-restore"

RowDict = dict[str, Any]

ACTION_MIGRATE = "migrate"
ACTION_CLEAR = "clear"
ACTION_FAIL = "fail"


@dataclass(slots=True)
class RowPlan:
    """单行迁移计划：action 为 migrate / clear / fail 之一。"""

    profile_id: int
    name: str
    action: str
    ref: str
    note: str


@dataclass(slots=True)
class MigrationSummary:
    """迁移结果计数，供 CLI 打印与门禁脚本断言。"""

    scanned: int = 0
    residue: int = 0
    migrated: int = 0
    cleared: int = 0
    failed: int = 0
    remaining: int = 0
    backup_path: str = ""
    plans: list[RowPlan] | None = None


def key_ref(profile_id: int) -> str:
    """与 AIProfileRepository 同一引用格式（单一事实来源，直接复用）。"""
    return AIProfileRepository.key_ref(profile_id)


def open_readonly(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def open_rw(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def fetch_profile_rows(conn: sqlite3.Connection) -> list[RowDict]:
    rows = conn.execute(
        "SELECT id, name, provider, api_key_ref, api_key_enc, api_key_masked, is_deleted"
        " FROM sys_ai_profile"
    ).fetchall()
    return [dict(row) for row in rows]


def count_residue(rows: list[RowDict]) -> int:
    """统计 api_key_enc 仍非空（可解密形态滞留库里）的行数。"""
    return sum(1 for row in rows if str(row.get("api_key_enc") or ""))


def plan_rows(rows: list[RowDict], store: SecretStore, key_path: Path) -> list[RowPlan]:
    """为每条残留密文行生成迁移计划；不落库，供干跑与 --apply 共用。"""
    plans: list[RowPlan] = []
    for row in rows:
        enc = str(row.get("api_key_enc") or "")
        if not enc:
            continue
        profile_id = int(row["id"])
        name = str(row.get("name") or "")
        ref = key_ref(profile_id)
        current_ref = str(row.get("api_key_ref") or "").strip()
        if current_ref and store_get_silent(store, current_ref):
            # 引用已生效（新路径写入或此前已迁移）：只清残留密文，绝不覆盖密钥库现值
            plans.append(RowPlan(profile_id, name, ACTION_CLEAR, current_ref, "已有可用引用，仅置空 api_key_enc"))
            continue
        try:
            secret = decrypt(enc, key_path)
        except AIKeyDecryptFailed as exc:
            plans.append(RowPlan(profile_id, name, ACTION_FAIL, ref, f"解密失败：{exc}"))
            continue
        if not secret:
            plans.append(RowPlan(profile_id, name, ACTION_CLEAR, current_ref, "密文解密为空，仅置空 api_key_enc"))
            continue
        plans.append(RowPlan(profile_id, name, ACTION_MIGRATE, ref, "解密成功后写入密钥库并回填引用"))
    return plans


def store_get_silent(store: SecretStore, ref: str) -> str:
    try:
        return store.get(ref)
    except Exception:  # noqa: BLE001 - 引用读取失败按「不可用」处理，走密文兜底
        return ""


def store_set_silent(store: SecretStore, ref: str, secret: str) -> None:
    store.set(ref, secret)


def backup_db(conn: sqlite3.Connection, db_path: Path) -> Path:
    """VACUUM INTO 整库快照，命名与 backup_service 一致（pre-restore + 时间戳）。"""
    root = db_path.parent / BACKUP_SUBDIR
    root.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    target = root / f"{SAFETY_PREFIX}-{stamp}.db"
    index = 1
    while target.exists():
        target = root / f"{SAFETY_PREFIX}-{stamp}-{index:02d}.db"
        index += 1
    conn.commit()
    conn.execute("VACUUM INTO ?", (str(target),))
    return target


def migrate_db(
    db_path: Path,
    *,
    apply: bool,
    key_path: Path | None = None,
    backup: bool = True,
) -> MigrationSummary:
    """核心迁移：apply=False 干跑，True 时先备份再写库。供 CLI 与门禁共用。"""
    resolved_key = key_path or (db_path.parent / ".secret_key")
    conn = open_rw(db_path) if apply else open_readonly(db_path)
    try:
        rows = fetch_profile_rows(conn)
        store = SecretStore(db_path.parent)
        plans = plan_rows(rows, store, resolved_key)
        summary = MigrationSummary(
            scanned=len(rows),
            residue=len(plans),
            remaining=count_residue(rows),
            plans=plans,
        )
        if not apply:
            return summary
        backup_path = backup_db(conn, db_path)
        summary.backup_path = str(backup_path) if backup_path else ""
        for plan in plans:
            if plan.action == ACTION_FAIL:
                summary.failed += 1
                continue
            if plan.action == ACTION_MIGRATE:
                row = next(item for item in rows if int(item["id"]) == plan.profile_id)
                secret = decrypt(str(row.get("api_key_enc") or ""), resolved_key)
                store_set_silent(store, plan.ref, secret)
                new_masked = str(row.get("api_key_masked") or "") or mask(secret)
                conn.execute(
                    "UPDATE sys_ai_profile SET api_key_ref = ?, api_key_masked = ?, api_key_enc = NULL"
                    " WHERE id = ?",
                    (plan.ref, new_masked, plan.profile_id),
                )
                summary.migrated += 1
            else:  # ACTION_CLEAR
                conn.execute(
                    "UPDATE sys_ai_profile SET api_key_enc = NULL WHERE id = ?",
                    (plan.profile_id,),
                )
                summary.cleared += 1
        conn.commit()
        summary.remaining = count_residue(fetch_profile_rows(conn))
        return summary
    finally:
        conn.close()


def official_db_paths() -> list[Path]:
    """默认正式库路径 + 当前环境变量指向的库路径，两者都算「正式库」。"""
    paths = [(PROJECT_ROOT / "data" / "aitp.db").resolve()]
    configured = load_config().db_path
    if configured.exists():
        paths.append(Path(configured).resolve())
    return paths


def is_official_db(db_path: Path) -> bool:
    resolved = Path(db_path).resolve()
    return any(resolved == path for path in official_db_paths())


def service_port_listening(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.settimeout(0.3)
        return probe.connect_ex(("127.0.0.1", port)) == 0


def acquire_write_lock(conn: sqlite3.Connection) -> str:
    """BEGIN IMMEDIATE 探测是否有其他进程持写锁/服务正在写。返回错误信息或空串。"""
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("ROLLBACK")
    except sqlite3.OperationalError as exc:
        return str(exc)
    return ""


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="AI 档案历史密钥入库收口迁移（默认干跑，--apply 才写）")
    parser.add_argument("--db", default=str(load_config().db_path), help="目标 SQLite 库文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只扫描打印，不写库（默认行为）")
    parser.add_argument("--apply", action="store_true", help="真正写库（先自动整库备份）")
    parser.add_argument("--key-path", default="", help="旧密文解密用的密钥文件（默认 <库目录>/.secret_key）")
    parser.add_argument(
        "--allow-production",
        action="store_true",
        help="显式确认允许对正式库执行 --apply（停服后操作）",
    )
    args = parser.parse_args(argv)

    if args.dry_run and args.apply:
        parser.error("--dry-run 与 --apply 互斥；不传参默认干跑")
    apply = bool(args.apply)
    db_path = Path(args.db)
    key_path = Path(args.key_path) if args.key_path else None

    print(f"目标库：{db_path}")
    print(f"模式：{'APPLY（写库）' if apply else 'DRY-RUN（干跑，不写库）'}")
    print("操作：迁移——把 api_key_enc 密文解密后迁入密钥库，回填 api_key_ref 并置空密文")

    if not db_path.exists():
        print(f"[中止] 库文件不存在：{db_path}")
        return 2

    if apply:
        official = is_official_db(db_path)
        if official and not args.allow_production:
            print(
                "[中止] 目标是正式库（data/aitp.db）。请确认：\n"
                "  1) 已 stop.bat / 停止服务；\n"
                "  2) 已在临时副本上演练过本脚本；\n"
                "  3) 确需在正式库执行时追加 --allow-production。"
            )
            return 2
        if official and service_port_listening(load_config().port):
            print(
                f"[中止] 目标是正式库且本机服务端口 {load_config().port} 仍在监听，"
                "服务可能正在使用库。请先停服再 --apply。"
            )
            return 2
        # 任何目标都探测写锁：被其他进程持有即中止
        probe = open_rw(db_path)
        try:
            locked = acquire_write_lock(probe)
        finally:
            probe.close()
        if locked:
            print(f"[中止] 目标库正被其他进程占用（{locked}）。请先停服再 --apply。")
            return 2
        if official:
            print("[提示] 目标为正式库且已获 --allow-production 确认，继续执行。")

    try:
        summary = migrate_db(db_path, apply=apply, key_path=key_path)
    except sqlite3.Error as exc:
        print(f"[中止] 打开/读取库失败：{exc}")
        return 2
    except FileNotFoundError as exc:
        print(f"[中止] 旧密文密钥文件不可用：{exc}（可用 --key-path 指定）")
        return 2

    print(f"\n扫描档案行数：{summary.scanned}")
    print(f"命中（api_key_enc 非空）行数：{summary.residue}")
    for plan in summary.plans or []:
        state = "将执行" if not apply else "已执行"
        print(f"  - id={plan.profile_id} name={plan.name} action={plan.action} ref={plan.ref} "
              f"[{state}] 说明={plan.note}")
    if not apply:
        print("\n干跑结束：以上为将要执行的变更；确认无误后追加 --apply。")
        print(f"当前库内残留 api_key_enc 行数：{summary.remaining}")
        return 0 if summary.failed == 0 else 1
    if summary.backup_path:
        print(f"\n备份文件：{summary.backup_path}")
    print(f"迁移行数（写入引用并置空密文）：{summary.migrated}")
    print(f"仅清理行数（引用已可用）：{summary.cleared}")
    print(f"失败行数：{summary.failed}")
    print(f"执行后残留 api_key_enc 行数：{summary.remaining}")
    if summary.failed:
        print("[提示] 存在解密失败行：密钥文件可能已更换，请重新在设置页录入对应档案的 API Key。")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
