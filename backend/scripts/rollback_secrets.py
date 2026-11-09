"""GAP-22 回滚：把引用式密钥库里的值写回 sys_ai_profile.api_key_enc，恢复迁移前形态。

    python backend/scripts/rollback_secrets.py [--db PATH] [--dry-run | --apply]
        [--key-path PATH] [--allow-production]

与 migrate_secrets.py 对称：
- 默认干跑，显式 --apply 才写库；--apply 前自动整库快照（VACUUM INTO，
  <库目录>/backups/pre-restore-<时间戳>.db，复用 backup_service 命名约定）。
- 处理对象：api_key_ref 非空的行。动作：secret = 密钥库.get(ref)，
  api_key_enc = encrypt(secret, 密钥文件)，api_key_ref 置空——即迁移前的形态。
- 密钥库读不到值的行不改动，计入失败并提示。
- 幂等：回滚后的行 api_key_ref 为空、密文非空，重复执行命中 0 行、不报错。
- 正式库/服务占用检测与 migrate_secrets 一致，中止条件相同。

注意：回滚会重新引入「备份/导出可解密」的风险窗口，仅作为迁移出错时的逃生门。
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))  # 便携版 Python 不会自动加脚本目录

from aitp.common.secret_store import SecretStore  # noqa: E402
from aitp.common.security import encrypt, get_or_create_secret_key, mask  # noqa: E402
from aitp.config import load_config  # noqa: E402

from migrate_secrets import (  # noqa: E402
    ACTION_FAIL,
    ACTION_MIGRATE,
    MigrationSummary,
    RowPlan,
    acquire_write_lock,
    backup_db,
    count_residue,
    fetch_profile_rows,
    is_official_db,
    open_readonly,
    open_rw,
    service_port_listening,
    store_get_silent,
)
ACTION_RESTORE = ACTION_MIGRATE  # 复用计数字段：summary.migrated 表示「已写回密文」的行


def plan_rollback_rows(rows: list[dict[str, Any]], store: SecretStore) -> list[RowPlan]:
    """为每条持有引用的行生成回滚计划；读不到密钥库值的行标记失败。"""
    plans: list[RowPlan] = []
    for row in rows:
        ref = str(row.get("api_key_ref") or "").strip()
        if not ref:
            continue
        profile_id = int(str(row.get("id") or 0))
        name = str(row.get("name") or "")
        secret = store_get_silent(store, ref)
        if not secret:
            plans.append(RowPlan(profile_id, name, ACTION_FAIL, ref, "密钥库读不到该引用，跳过不改动"))
            continue
        plans.append(RowPlan(profile_id, name, ACTION_RESTORE, ref, "引用值加密写回 api_key_enc，清空 api_key_ref"))
    return plans


def rollback_db(
    db_path: Path,
    *,
    apply: bool,
    key_path: Path | None = None,
    backup: bool = True,
) -> MigrationSummary:
    """核心回滚：apply=False 干跑，True 时先备份再写库。供 CLI 与门禁共用。"""
    resolved_key = key_path or (db_path.parent / ".secret_key")
    conn = open_rw(db_path) if apply else open_readonly(db_path)
    try:
        rows = fetch_profile_rows(conn)
        store = SecretStore(db_path.parent)
        plans = plan_rollback_rows(rows, store)
        summary = MigrationSummary(
            scanned=len(rows),
            residue=len(plans),
            remaining=count_residue(rows),
            plans=plans,
        )
        if not apply:
            return summary
        get_or_create_secret_key(resolved_key)
        backup_path = backup_db(conn, db_path)
        summary.backup_path = str(backup_path)
        for plan in plans:
            if plan.action == ACTION_FAIL:
                summary.failed += 1
                continue
            secret = store_get_silent(store, plan.ref)
            ciphertext = encrypt(secret, resolved_key)
            row = next(item for item in rows if int(str(item.get("id") or 0)) == plan.profile_id)
            new_masked = str(row.get("api_key_masked") or "") or mask(secret)
            conn.execute(
                "UPDATE sys_ai_profile SET api_key_enc = ?, api_key_ref = NULL, api_key_masked = ?"
                " WHERE id = ?",
                (ciphertext, new_masked, plan.profile_id),
            )
            summary.migrated += 1
        conn.commit()
        after_rows = fetch_profile_rows(conn)
        summary.remaining = count_residue(after_rows)
        summary.cleared = sum(
            1 for row in after_rows if str(row.get("api_key_enc") or "") and not str(row.get("api_key_ref") or "")
        )
        return summary
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GAP-22 密钥迁移回滚（默认干跑，--apply 才写）")
    parser.add_argument("--db", default=str(load_config().db_path), help="目标 SQLite 库文件路径")
    parser.add_argument("--dry-run", action="store_true", help="只扫描打印，不写库（默认行为）")
    parser.add_argument("--apply", action="store_true", help="真正写库（先自动整库备份）")
    parser.add_argument("--key-path", default="", help="写回密文用的 Fernet 密钥文件（默认 <库目录>/.secret_key）")
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
    print("操作：回滚——把密钥库引用值写回 api_key_enc，并清空 api_key_ref")

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
        summary = rollback_db(db_path, apply=apply, key_path=key_path)
    except sqlite3.Error as exc:
        print(f"[中止] 打开/读取库失败：{exc}")
        return 2

    print(f"\n扫描档案行数：{summary.scanned}")
    print(f"命中（api_key_ref 非空）行数：{summary.residue}")
    for plan in summary.plans or []:
        state = "将执行" if not apply else "已执行"
        print(f"  - id={plan.profile_id} name={plan.name} action={plan.action} ref={plan.ref}"
              f" [{state}] 说明={plan.note}")
    if not apply:
        print("\n干跑结束：以上为将要执行的变更；确认无误后追加 --apply。")
        print(f"当前库内残留 api_key_enc 行数：{summary.remaining}")
        return 0 if summary.failed == 0 else 1
    if summary.backup_path:
        print(f"\n备份文件：{summary.backup_path}")
    print(f"写回密文行数：{summary.migrated}")
    print(f"回滚后处于旧形态（有密文、无引用）的行数：{summary.cleared}")
    print(f"失败行数（引用失效）：{summary.failed}")
    print(f"执行后 api_key_enc 非空行数：{summary.remaining}")
    if summary.failed:
        print("[提示] 存在密钥库读不到值的引用：这些行保持引用形态未动，请重新录入对应档案的 API Key。")
    return 0 if summary.failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
