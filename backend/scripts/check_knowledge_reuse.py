"""知识 / 报告片段跨项目复用门禁（三期 GAP-42）。

守住的口径：
- 知识可提升为全局，project_id 置空并保留来源项目；
- 跨项目只登记引用（ref_type='project'），不复制正文；
- 被引用条目禁止删除/撤销，并回显引用方；
- 报告片段只登记使用关系，报告预览/导出实时取源片段正文，改源即改引用；
- 片段被报告使用时禁止删除；跨项目插入非全局片段被拒绝。

    python backend/scripts/check_knowledge_reuse.py

全程临时库与 Flask test_client，绝不碰正式库与已启动服务。
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402

V1 = "/api/v1"
SUCCESS = 200000
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


def build_app(workdir: pathlib.Path) -> Any:
    db_path = workdir / "reuse.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "reuse.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def seed_article(db_path: pathlib.Path, project_id: int, title: str, content: str) -> int:
    now = "2026-11-12T00:00:00Z"
    conn = sqlite3.connect(db_path)
    try:
        article_id = conn.execute(
            "INSERT INTO biz_knowledge_article (title, content, category, tags, source_type,"
            " project_id, review_status, status, current_version, created_at, updated_at, is_deleted)"
            " VALUES (?, ?, 'general', '', 'manual', ?, 'published', 'published', 1, ?, ?, 0)",
            (title, content, project_id, now, now),
        ).lastrowid
        conn.commit()
        return int(article_id or 0)
    finally:
        conn.close()


def call(client: Any, method: str, url: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    response = client.open(url, method=method, json=payload)
    return response.get_json() or {}


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-reuse-"))
    try:
        app = build_app(workdir)
        db_path = workdir / "reuse.db"
        client = app.test_client()

        project_a = call(client, "POST", f"{V1}/projects", {"code": "REUSE-A", "name": "来源项目A"})
        project_b = call(client, "POST", f"{V1}/projects", {"code": "REUSE-B", "name": "引用项目B"})
        a_id = int(project_a["data"]["id"])
        b_id = int(project_b["data"]["id"])

        article_id = seed_article(db_path, a_id, "支付回调幂等排查结论", "按订单号去重，重复回调只入账一次。")
        private_id = seed_article(db_path, a_id, "仅 A 项目内部约定", "这条不提升，B 不能直接引用。")

        # ---------- 1. 提升为全局 ----------
        body = call(client, "GET", f"{V1}/knowledge/articles/{article_id}")
        check("提升前归属 A 项目", body["data"]["projectId"] == a_id, str(body.get("data", {}).get("projectId")))
        promoted = call(client, "POST", f"{V1}/knowledge/articles/{article_id}/promote",
                        {"operator": "pm"})
        check("提升为全局成功", promoted.get("code") == SUCCESS, str(promoted.get("message")))
        data = promoted.get("data") or {}
        check("来源项目保留", data.get("sourceProjectId") == a_id)
        check("来源项目名可读", data.get("sourceProjectName") == "来源项目A", str(data))
        body = call(client, "GET", f"{V1}/knowledge/articles/{article_id}")
        detail = body["data"]
        check("全局条目 projectId 为空", detail.get("projectId") is None)
        check("全局条目 isGlobal=true", detail.get("isGlobal") is True)
        check("提升时间已落库", bool(detail.get("promotedAt")))
        check("提升人已落库", detail.get("promotedBy") == "pm")

        again = call(client, "POST", f"{V1}/knowledge/articles/{article_id}/promote", {})
        check("重复提升被拒绝", again.get("code") == ErrorCode.ILLEGAL_OPERATION, str(again.get("message")))

        body = call(client, "GET", f"{V1}/knowledge/articles?scope=global&pageSize=50")
        ids = [int(row["id"]) for row in body["data"]["list"]]
        check("全局清单包含提升条目", article_id in ids, str(ids))
        check("全局清单不含未提升条目", private_id not in ids, str(ids))
        global_row = next(row for row in body["data"]["list"] if int(row["id"]) == article_id)
        check("全局清单带来源项目名", global_row.get("sourceProjectName") == "来源项目A", str(global_row))

        body = call(client, "GET", f"{V1}/knowledge/articles?projectId={a_id}&pageSize=50")
        ids = [int(row["id"]) for row in body["data"]["list"]]
        check("项目清单不再包含已提升条目", article_id not in ids, str(ids))
        check("项目清单仍包含本项目私有条目", private_id in ids, str(ids))

        # ---------- 2. 跨项目引用 ----------
        rejected = call(client, "POST", f"{V1}/knowledge/articles/{private_id}/references",
                        {"projectId": b_id})
        check("未提升条目禁止跨项目引用", rejected.get("code") == ErrorCode.ILLEGAL_OPERATION,
              str(rejected.get("message")))

        referenced = call(client, "POST", f"{V1}/knowledge/articles/{article_id}/references",
                          {"projectId": b_id, "operator": "pm"})
        check("全局知识可登记到项目 B", referenced.get("code") == SUCCESS, str(referenced.get("message")))
        refs = referenced["data"]["refs"]
        check("引用类型为 project", refs and refs[0]["refType"] == "project", str(refs))
        check("引用记录回显项目名", refs and refs[0]["label"] == "引用项目B", str(refs))
        refer_id = int(refs[0]["refId"])
        check("引用记录指向项目 B", refer_id == b_id)

        duplicated = call(client, "POST", f"{V1}/knowledge/articles/{article_id}/references",
                          {"projectId": b_id})
        check("重复引用幂等", len(duplicated["data"]["refs"]) == 1, str(duplicated["data"]["refs"]))

        self_ref = call(client, "POST", f"{V1}/knowledge/articles/{article_id}/references",
                        {"projectId": a_id})
        check("来源项目无需重复引用", self_ref.get("code") == ErrorCode.ILLEGAL_OPERATION,
              str(self_ref.get("message")))

        body = call(client, "GET", f"{V1}/knowledge/articles/{article_id}")
        project_refs = body["data"].get("projectRefs") or []
        check("详情回显被哪些项目引用", len(project_refs) == 1 and project_refs[0]["label"] == "引用项目B",
              str(project_refs))

        blocked = call(client, "DELETE", f"{V1}/knowledge/articles/{article_id}")
        check("被引用知识禁止删除", blocked.get("code") == ErrorCode.RESOURCE_REFERENCED,
              str(blocked.get("message")))
        blocked = call(client, "POST", f"{V1}/knowledge/articles/{article_id}/revoke", {})
        check("被引用知识禁止撤销", blocked.get("code") == ErrorCode.RESOURCE_REFERENCED,
              str(blocked.get("message")))
        body = call(client, "GET", f"{V1}/knowledge/articles/{article_id}")
        check("拒绝后条目仍在", body.get("code") == SUCCESS and body["data"]["reviewStatus"] == "published")

        removed = call(client, "DELETE", f"{V1}/knowledge/articles/{article_id}/references/{b_id}")
        check("解除引用成功", removed.get("code") == SUCCESS and not removed["data"]["refs"],
              str(removed.get("data")))

        # ---------- 3. 报告片段 ----------
        report_a = call(client, "POST", f"{V1}/reports",
                        {"projectId": a_id, "title": "A 项目测试报告"})
        report_a_id = int(report_a["data"]["id"])
        report_b = call(client, "POST", f"{V1}/reports",
                        {"projectId": b_id, "title": "B 项目测试报告"})
        report_b_id = int(report_b["data"]["id"])

        fragment = call(client, "POST", f"{V1}/reports/fragments", {
            "projectId": a_id, "title": "失败证据摘要", "section": "bug",
            "content": "回调重复入账，证据快照见 EV-001。",
            "sourceReportId": report_a_id, "sourceNote": "来源 A 报告",
        })
        check("片段创建成功", fragment.get("code") == SUCCESS, str(fragment.get("message")))
        fragment_id = int(fragment["data"]["id"])
        check("片段回显来源项目", fragment["data"]["sourceProjectName"] == "来源项目A")
        check("片段回显来源报告", fragment["data"]["sourceReportTitle"] == "A 项目测试报告")

        rejected = call(client, "POST", f"{V1}/reports/{report_b_id}/fragments",
                        {"fragmentId": fragment_id})
        check("未提升片段禁止跨项目插入", rejected.get("code") == ErrorCode.ILLEGAL_OPERATION,
              str(rejected.get("message")))

        promoted_fragment = call(client, "POST", f"{V1}/reports/fragments/{fragment_id}/promote", {})
        check("片段提升为全局", promoted_fragment.get("code") == SUCCESS
              and promoted_fragment["data"]["isGlobal"] is True, str(promoted_fragment.get("data")))

        available = call(client, "GET", f"{V1}/reports/fragments?projectId={b_id}")
        check("B 项目可见全局片段", any(int(row["id"]) == fragment_id for row in available["data"]),
              str(available.get("data")))

        inserted = call(client, "POST", f"{V1}/reports/{report_b_id}/fragments",
                        {"fragmentId": fragment_id, "operator": "pm"})
        check("全局片段可插入项目 B 报告", inserted.get("code") == SUCCESS, str(inserted.get("message")))
        used = inserted["data"]["fragments"]
        check("报告回显使用中的片段", len(used) == 1 and used[0]["id"] == fragment_id, str(used))

        inserted_again = call(client, "POST", f"{V1}/reports/{report_b_id}/fragments",
                              {"fragmentId": fragment_id})
        check("重复插入幂等", len(inserted_again["data"]["fragments"]) == 1)

        blocked = call(client, "DELETE", f"{V1}/reports/fragments/{fragment_id}")
        check("被报告使用的片段禁止删除", blocked.get("code") == ErrorCode.RESOURCE_REFERENCED,
              str(blocked.get("message")))

        detail = call(client, "GET", f"{V1}/reports/{report_b_id}")
        check("报告详情带片段清单", len(detail["data"].get("fragments") or []) == 1)

        preview = call(client, "GET", f"{V1}/reports/{report_b_id}/preview?format=md")
        check("预览包含片段正文", "回调重复入账，证据快照见 EV-001。" in preview["data"]["content"])
        check("预览标注来源项目", "来源项目A" in preview["data"]["content"])

        # 改源片段 → 引用报告实时跟着变（不复制本体）
        updated = call(client, "PUT", f"{V1}/reports/fragments/{fragment_id}",
                       {"content": "回调重复入账（已修复），证据快照见 EV-002。"})
        check("片段更新成功", updated.get("code") == SUCCESS, str(updated.get("message")))
        preview = call(client, "GET", f"{V1}/reports/{report_b_id}/preview?format=md")
        check("改源后预览内容同步", "EV-002" in preview["data"]["content"]
              and "EV-001" not in preview["data"]["content"])
        html = call(client, "GET", f"{V1}/reports/{report_b_id}/preview?format=html")
        check("HTML 预览包含片段正文", "EV-002" in html["data"]["content"])

        exported = client.get(f"{V1}/reports/{report_b_id}/export?format=md")
        text = exported.data.decode("utf-8")
        check("Markdown 导出包含片段正文", "EV-002" in text and "复用片段" in text)
        exported = client.get(f"{V1}/reports/{report_b_id}/export?format=html")
        text = exported.data.decode("utf-8")
        check("HTML 导出包含片段正文", "EV-002" in text and "复用片段" in text)
        exported = client.get(f"{V1}/reports/{report_b_id}/export?format=pdf").data
        check("PDF 导出正常产出", exported[:4] == b"%PDF" and len(exported) > 2000,
              f"{len(exported)} bytes")

        body = call(client, "GET", f"{V1}/reports/fragments?projectId={a_id}&scope=mine")
        check("来源项目 mine 清单含片段", any(int(row["id"]) == fragment_id for row in body["data"]))
        body = call(client, "GET", f"{V1}/reports/fragments?scope=global")
        check("全局片段清单含片段", any(int(row["id"]) == fragment_id for row in body["data"]))

        removed = call(client, "DELETE", f"{V1}/reports/{report_b_id}/fragments/{fragment_id}")
        check("从报告移除片段成功", removed.get("code") == SUCCESS
              and not removed["data"]["fragments"], str(removed.get("data")))
        deleted = call(client, "DELETE", f"{V1}/reports/fragments/{fragment_id}")
        check("未被使用后可删除片段", deleted.get("code") == SUCCESS, str(deleted.get("message")))
        body = call(client, "GET", f"{V1}/reports/fragments?projectId={b_id}")
        check("删除后不再出现在可用清单",
              all(int(row["id"]) != fragment_id for row in body["data"]))

        # ---------- 4. 审计 ----------
        conn = sqlite3.connect(db_path)
        try:
            actions = [
                str(row[0]) for row in conn.execute(
                    "SELECT detail_json FROM sys_audit_log WHERE resource_type = 'knowledge_article'"
                    " ORDER BY id DESC LIMIT 20"
                ).fetchall()
            ]
            check("知识提升/引用有审计", any("promote_global" in item for item in actions)
                  and any("reference_project" in item for item in actions), str(actions[:3]))
            fragment_actions = [
                str(row[0]) for row in conn.execute(
                    "SELECT detail_json FROM sys_audit_log WHERE resource_type = 'report'"
                    " ORDER BY id DESC LIMIT 20"
                ).fetchall()
            ]
            check("片段创建/插入/删除有审计",
                  any("fragment_create" in item for item in fragment_actions)
                  and any("fragment_insert" in item for item in fragment_actions)
                  and any("fragment_delete" in item for item in fragment_actions),
                  str(fragment_actions[:3]))
        finally:
            conn.close()

    finally:
        import shutil
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 50)
    print(f"知识/报告片段跨项目复用：PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
