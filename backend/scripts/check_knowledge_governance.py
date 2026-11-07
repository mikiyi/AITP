"""P8 知识治理验收脚本：待审池转正、版本化、引用保护、Markdown 落盘与重建索引。

在临时数据库 + 临时知识库目录上跑完整闭环，校验：

    python backend/scripts/check_knowledge_governance.py

输出逐条 [PASS]/[FAIL]，结尾给出 PASS/FAIL 计数；FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import json
import os
import pathlib
import re
import shutil
import sqlite3
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.repositories.knowledge_draft_repo import assistant_title  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services import knowledge_assistant_service  # noqa: E402
from aitp.services import job_queue  # noqa: E402
from aitp.services import prompt_usage  # noqa: E402
from aitp.services.knowledge_storage import CONFIG_PATH  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

ASSISTANT_TITLE_RE = re.compile(r"^AI问答助手\+\d{8,14}｜")

JsonDict = dict[str, Any]

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


class Api:
    """薄封装：统一拆包封、断言业务码。"""

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
            raise AssertionError(f"{method} {path} 失败：{body}")
        return body.get("data")

    def code(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> int:
        _, body = self.call(method, path, payload, **kwargs)
        return int(body.get("code", -1))


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "aitp.db"
    init_db(db_path)
    # 知识库落盘目录指向临时目录，避免污染项目默认 knowledge/
    conn = sqlite3.connect(db_path)
    SysConfigRepository(conn).set_value(
        CONFIG_PATH, str(workdir / "knowledge"), "string", "验收脚本临时目录")
    conn.commit()
    conn.close()
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def check_draft_flow(api: Api, project_id: int) -> int:
    """待审池 → 正式库 → 落盘 Markdown。"""
    before = cast(JsonDict, api.data("GET", "/api/v1/knowledge/articles"))
    pending_before = int(cast(JsonDict, api.data("GET", "/api/v1/knowledge/drafts"))["stats"]["pending"])

    draft = cast(JsonDict, api.data("POST", "/api/v1/knowledge/drafts", {
        "title": "验证码时效测试要点",
        "content": "验证码 60 秒有效，连续错误 5 次锁定账号 15 分钟。",
        "category": "测试方法",
        "tags": "登录,验证码",
        "sourceType": "ai_output",
        "producer": "testpoint_generation",
        "moduleId": None,
    }))
    draft_id = int(draft["id"])
    check("新建待审条目", draft_id > 0, str(draft))

    pending_after = int(cast(JsonDict, api.data("GET", "/api/v1/knowledge/drafts"))["stats"]["pending"])
    check("待审池计数 +1", pending_after == pending_before + 1,
          f"前={pending_before} 后={pending_after}")

    after = cast(JsonDict, api.data("GET", "/api/v1/knowledge/articles"))
    check("待审条目不计入正式库", int(after["total"]) == int(before["total"]),
          f"前={before['total']} 后={after['total']}")

    approved = cast(JsonDict, api.data(
        "POST", f"/api/v1/knowledge/drafts/{draft_id}/review",
        {"action": "approve", "reviewer": "yichengren", "comment": "内容可用"},
    ))
    article_id = int(approved["articleId"])
    check("审核通过生成正式条目", article_id > 0, str(approved))

    published = cast(JsonDict, api.data("GET", "/api/v1/knowledge/articles"))
    check("正式库计数 +1", int(published["total"]) == int(before["total"]) + 1,
          f"前={before['total']} 后={published['total']}")

    detail = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{article_id}"))
    check("条目状态为已发布", detail["reviewStatus"] == "published", str(detail.get("reviewStatus")))
    check("正文可检索", "验证码" in str(detail["content"]))

    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    root = pathlib.Path(str(storage["storagePath"]))
    files = list(root.glob("*.md"))
    check("审核通过后落盘 Markdown", len(files) >= 1, f"目录={root} 文件数={len(files)}")
    if files:
        text = files[0].read_text(encoding="utf-8")
        check("Markdown 含 frontmatter", text.startswith("---") and "id:" in text, text[:60])
        check("Markdown 含正文", "验证码" in text)
        check("详情回传 fileChanged=false", detail["fileChanged"] is False, str(detail["fileChanged"]))
    return article_id


def check_external_edit(api: Api, article_id: int) -> None:
    """外部改文件后，详情应提示 fileChanged。"""
    detail = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{article_id}"))
    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    root = pathlib.Path(str(storage["storagePath"]))
    rel = str(detail.get("filePath") or "")
    target = root / rel
    if not target.exists():
        check("外部修改检测有落盘文件", False, str(target))
        return
    target.write_text(target.read_text(encoding="utf-8") + "\n\n外部补充一行。\n", encoding="utf-8")
    detail = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{article_id}"))
    check("外部改文件后提示 fileChanged", detail["fileChanged"] is True, str(detail["fileChanged"]))


def check_version_flow(api: Api, article_id: int) -> None:
    """编辑 → 待审新版本 → 审核通过 → 正文与文件同步。"""
    detail = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{article_id}"))
    old_content = str(detail["content"])

    api.data("PUT", f"/api/v1/knowledge/articles/{article_id}", {
        "title": "验证码时效测试要点（修订）",
        "content": "验证码 60 秒有效；连续错误 5 次锁定账号 15 分钟；并发点击只发一条短信。",
        "changeLog": "补充并发场景",
        "operator": "yichengren",
    })

    after_edit = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{article_id}"))
    check("编辑不立即改写当前正文", after_edit["content"] == old_content, str(after_edit["content"])[:60])
    check("生成待审版本", int(after_edit["draftVersion"]) == 2, str(after_edit.get("draftVersion")))

    versions = cast(list[Any], api.data("GET", f"/api/v1/knowledge/articles/{article_id}/versions"))
    pending = [v for v in versions if v["reviewStatus"] == "pending"]
    check("版本列表含待审版本", len(pending) == 1, f"版本数={len(versions)}")

    search = cast(JsonDict, api.data("GET", "/api/v1/knowledge/search?keyword=并发点击"))
    check("待审版本不进入检索", len(search["list"]) == 0, f"命中={len(search['list'])}")

    api.data("POST", f"/api/v1/knowledge/articles/{article_id}/versions/2/review",
             {"action": "approve", "reviewer": "yichengren", "comment": "通过"})
    after_approve = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{article_id}"))
    check("审核通过后正文更新", "并发点击" in str(after_approve["content"]))
    check("生效版本号前移", int(after_approve["currentVersion"]) == 2,
          str(after_approve.get("currentVersion")))

    search = cast(JsonDict, api.data("GET", "/api/v1/knowledge/search?keyword=并发点击"))
    check("审核通过后可检索", len(search["list"]) >= 1, f"命中={len(search['list'])}")

    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    root = pathlib.Path(str(storage["storagePath"]))
    rel = str(after_approve.get("filePath") or "")
    text = (root / rel).read_text(encoding="utf-8") if (root / rel).exists() else ""
    check("落盘文件同步新正文", "并发点击" in text, text[:80])

    diff = cast(JsonDict, api.data(
        "GET", f"/api/v1/knowledge/articles/{article_id}/diff?from=1&to=2"))
    check("版本 diff 可用", int(diff["added"]) >= 1, str(diff.get("added")))


def check_reference_protection(api: Api, project_id: int, article_id: int) -> None:
    """报告引用知识条目后，条目禁止撤销 / 删除。"""
    report = cast(JsonDict, api.data("POST", "/api/v1/reports", {
        "projectId": project_id,
        "title": "迭代测试报告",
        "reportType": "summary",
        "content": "引用了验证码时效测试要点。",
        "knowledgeRefs": [article_id],
    }))
    report_id = int(report["id"])
    check("报告创建成功", report_id > 0, str(report))

    detail = cast(JsonDict, api.data("GET", f"/api/v1/reports/{report_id}"))
    check("报告详情回传知识引用", len(detail.get("knowledgeRefs", [])) == 1,
          str(detail.get("knowledgeRefs")))

    code = api.code("POST", f"/api/v1/knowledge/articles/{article_id}/revoke")
    check("被引用的条目禁止撤销", code == 140104, f"code={code}")

    _, body = api.call("GET", f"/api/v1/knowledge/articles/{article_id}/refs")
    refs = cast(list[Any], body.get("data") or [])
    check("引用清单可读", len(refs) == 1, f"refs={len(refs)}")
    check("引用清单含类型", bool(refs) and refs[0].get("refType") == "report", str(refs[:1]))
    check("引用清单含来源标题", bool(refs) and bool(refs[0].get("label")), str(refs[:1]))

    code = api.code("DELETE", f"/api/v1/knowledge/articles/{article_id}")
    check("被引用的条目禁止删除", code == 140104, f"code={code}")

    api.data("DELETE", f"/api/v1/reports/{report_id}")
    code = api.code("POST", f"/api/v1/knowledge/articles/{article_id}/revoke")
    check("报告删除后可撤销", code == ErrorCode.SUCCESS, f"code={code}")


def check_assistant_evolution(api: Api, app: Flask, db_path: pathlib.Path,
                              manual_article_id: int) -> None:
    """问答助手自成长：溯源入池、幂等、来源列、外发预检、自动整理只产出待审版本。"""
    base: JsonDict = {
        "question": "支付回调重复推送怎么定位？",
        "answer": "先看网关重试日志，再核对幂等键是否按订单号落地。",
        "mode": "hybrid",
        "model": "sensenova-6.8-flash",
        "answeredAt": "2026-11-06T10:20:30.000Z",
        "refs": [{"marker": "K1", "articleId": manual_article_id, "title": "验证码时效测试要点"}],
        "reviewRequired": True,
    }
    draft = cast(JsonDict, api.data("POST", "/api/v1/knowledge/assistant/add", base))
    draft_id = int(draft["id"])
    check("问答回答可加入待审池", draft_id > 0, str(draft)[:120])
    check("待审标题带秒级时间前缀",
          bool(ASSISTANT_TITLE_RE.match(str(draft.get("title") or "")))
          and re.match(r"^AI问答助手\+\d{14}｜", str(draft.get("title") or "")) is not None,
          str(draft.get("title")))
    check("来源中文标签为知识库问答助手", draft.get("sourceTypeLabel") == "知识库问答助手",
          str(draft.get("sourceTypeLabel")))
    check("待审保留回答时间", draft.get("answeredAt") == base["answeredAt"], str(draft.get("answeredAt")))
    check("待审保留回答模式与模型",
          draft.get("answerMode") == "hybrid" and draft.get("answerModel") == base["model"],
          f"mode={draft.get('answerMode')} model={draft.get('answerModel')}")
    check("待审保留人工复核标记", draft.get("reviewRequired") is True, str(draft.get("reviewRequired")))
    check("待审保留引用条目", len(cast(list[Any], draft.get("refs") or [])) == 1,
          str(draft.get("refs")))
    check("正文包含提问与回答",
          "## 提问" in str(draft.get("content")) and "## 回答" in str(draft.get("content")))

    again = cast(JsonDict, api.data("POST", "/api/v1/knowledge/assistant/add", base))
    check("重复提交命中幂等", again.get("duplicated") is True and int(again["id"]) == draft_id,
          str(again)[:120])

    drafts = cast(JsonDict, api.data("GET", "/api/v1/knowledge/drafts?pageSize=100"))
    listed = [row for row in cast(list[Any], drafts["list"]) if int(row["id"]) == draft_id]
    check("待审列表回传中文来源", bool(listed) and listed[0].get("sourceTypeLabel") == "知识库问答助手",
          str(listed[:1]))

    code = api.code("POST", "/api/v1/knowledge/assistant/add", {**base, "mode": "kb"})
    check("知识库直答不允许加回知识库", code == ErrorCode.PARAM_ERROR, f"code={code}")

    second = {
        "question": "支付回调超时如何复现？",
        "answer": "把网关超时阈值调到 200ms，再让下游延迟 500ms 即可稳定复现。",
        "mode": "llm",
        "model": "sensenova-6.8-flash-lite",
        "answeredAt": "2026-11-06T11:05:00.000Z",
        "refs": [],
        "reviewRequired": False,
    }
    draft2 = cast(JsonDict, api.data("POST", "/api/v1/knowledge/assistant/add", second))
    draft2_id = int(draft2["id"])
    check("第二条问答独立入池", draft2_id != draft_id, f"{draft_id} vs {draft2_id}")

    first = cast(JsonDict, api.data(
        "POST", f"/api/v1/knowledge/drafts/{draft_id}/review",
        {"action": "approve", "reviewer": "yichengren", "comment": "可用"},
    ))
    second_article = cast(JsonDict, api.data(
        "POST", f"/api/v1/knowledge/drafts/{draft2_id}/review",
        {"action": "approve", "reviewer": "yichengren", "comment": "可用"},
    ))
    keep_id = int(first["articleId"])
    merge_id = int(second_article["articleId"])
    check("问答转正生成正式条目", keep_id > 0 and merge_id > 0, f"{keep_id}/{merge_id}")

    keep = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{keep_id}"))
    check("正式标题带日期前缀",
          re.match(r"^AI问答助手\+\d{8}｜", str(keep.get("title") or "")) is not None,
          str(keep.get("title")))
    check("正式条目来源中文标签", keep.get("sourceTypeLabel") == "知识库问答助手",
          str(keep.get("sourceTypeLabel")))
    check("正式条目初始为待自动整理", keep.get("organizeStatus") == "pending",
          str(keep.get("organizeStatus")))
    check("转正保留回答溯源", keep.get("answeredAt") == base["answeredAt"]
          and keep.get("answerMode") == "hybrid", str(keep.get("answeredAt")))

    preview = cast(JsonDict, api.data("GET", "/api/v1/knowledge/assistant/preview"))
    files = cast(list[Any], preview.get("files") or [])
    mine = [item for item in files if int(item.get("articleId") or 0) == keep_id]
    check("外发预检列出助手条目", int(preview.get("articleCount") or 0) >= 2,
          str(preview.get("articleCount")))
    check("预检带回 .md 全文与路径",
          bool(mine) and bool(mine[0].get("absolutePath")) and "## 提问" in str(mine[0].get("content")),
          str(mine[:1])[:160])

    egress = cast(JsonDict, api.data("GET", "/api/v1/knowledge/assistant/egress?mode=hybrid"))
    check("外发评估返回候选链", isinstance(egress.get("candidates"), list), str(egress)[:120])
    check("外发评估给出可能外发结论", isinstance(egress.get("externalPossible"), bool),
          str(egress.get("externalPossible")))
    check("外发评估拒绝非大模型模式",
          api.code("GET", "/api/v1/knowledge/assistant/egress?mode=kb") == ErrorCode.PARAM_ERROR)

    check("非助手来源拒绝自动整理",
          api.code("POST", f"/api/v1/knowledge/articles/{manual_article_id}/organize",
                   {"desensitized": True}) == ErrorCode.ILLEGAL_OPERATION)

    real_submit = job_queue.submit
    real_chat = getattr(knowledge_assistant_service, "chat_with_fallback")

    def fake_submit(conn: sqlite3.Connection, *, kind: str, title: str,
                    payload: JsonDict | None = None, priority: int = 100,
                    max_attempts: int = 1) -> JsonDict:
        now = utcnow_iso()
        cur = conn.execute(
            "INSERT INTO sys_task (kind, title, status, priority, payload_json, attempts,"
            " max_attempts, created_at, updated_at) VALUES (?, ?, 'queued', ?, ?, 0, ?, ?, ?)",
            (kind, title, priority, json.dumps(payload or {}, ensure_ascii=False),
             max(1, max_attempts), now, now),
        )
        conn.commit()
        return cast(JsonDict, job_queue.get_task(conn, int(cur.lastrowid or 0)))

    job_queue.submit = fake_submit
    try:
        if egress["externalPossible"]:
            code = api.code("POST", f"/api/v1/knowledge/articles/{keep_id}/organize", {})
            check("未确认脱敏时拒绝自动整理", code == ErrorCode.PARAM_ERROR, f"code={code}")

        submitted = cast(JsonDict, api.data(
            "POST", f"/api/v1/knowledge/articles/{keep_id}/organize", {"desensitized": True}))
        task_id = int(submitted["taskId"])
        check("发起自动整理写入后台任务", task_id > 0
              and str(cast(JsonDict, submitted["task"]).get("kind")) == "knowledge.organize",
              str(submitted)[:120])

        status = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{keep_id}/organize"))
        check("发起后状态为后台自动整理中", status.get("organizeStatus") == "running",
              str(status.get("organizeStatus")))
        check("整理状态接口回传任务", isinstance(status.get("task"), dict)
              and cast(JsonDict, status["task"]).get("status") == "queued", str(status.get("task")))

        conn = get_standalone_conn(db_path)
        try:
            check("任务仍在排队时不回退状态", knowledge_assistant_service.reconcile(conn) == 0)
            conn.execute("UPDATE sys_task SET status = 'failed' WHERE id = ?", (task_id,))
            conn.commit()
            check("无存活任务时回退待自动整理",
                  knowledge_assistant_service.reconcile(conn) == 2)
            conn.commit()
            pending_status = cast(JsonDict, api.data(
                "GET", f"/api/v1/knowledge/articles/{keep_id}/organize"))
            check("回退后状态为待自动整理", pending_status.get("organizeStatus") == "pending",
                  str(pending_status.get("organizeStatus")))
            task_row = job_queue.get_task(conn, task_id)
        finally:
            conn.close()
        check("失败任务可从任务中心查询", bool(task_row) and task_row is not None
              and task_row.get("status") == "failed", str(task_row)[:80])

        proposal = {
            "groups": [{"keepId": keep_id, "mergeIds": [merge_id], "reason": "同一支付回调问题"}],
            "articles": [
                {"id": keep_id, "title": "支付回调重复推送排查", "category": "踩坑记录",
                 "tags": "支付,幂等", "summary": "重试日志与幂等键排查路径"},
                {"id": merge_id, "title": "支付回调超时复现", "category": "踩坑记录",
                 "tags": "支付", "summary": "超时复现步骤"},
            ],
        }

        def fake_chat(candidates: Any, messages: Any, **kwargs: Any) -> Any:
            return SimpleNamespace(
                result=SimpleNamespace(content=json.dumps(proposal, ensure_ascii=False),
                                       model="fake-organize"),
                config=SimpleNamespace(model="fake-organize"),
            )

        setattr(knowledge_assistant_service, "chat_with_fallback", fake_chat)
        with app.app_context():
            conn = get_standalone_conn(db_path)
            try:
                task = job_queue.get_task(conn, task_id)
                assert task is not None
                summary = knowledge_assistant_service.run_organize(
                    conn, task, {"articleId": keep_id}, db_path)
                again_summary = knowledge_assistant_service.run_organize(
                    conn, task, {"articleId": keep_id}, db_path)
                # 自动整理不落 AI 执行记录，提示词调用次数要单独计入 sys_prompt_usage
                usage_counts = prompt_usage.counts(conn)
                versions = conn.execute(
                    "SELECT article_id, version, content, review_status, created_by,"
                    " change_log, merge_targets_json"
                    " FROM biz_knowledge_version WHERE article_id IN (?, ?)"
                    " AND review_status = 'pending' ORDER BY article_id", (keep_id, merge_id)
                ).fetchall()
            finally:
                conn.close()
    finally:
        job_queue.submit = real_submit
        setattr(knowledge_assistant_service, "chat_with_fallback", real_chat)

    check("自动整理只为保留项生成一条提案",
          int(summary.get("versions") or 0) == 1 and len(versions) == 1,
          f"summary={summary} versions={len(versions)}")
    check("自动整理识别出一组重复", int(summary.get("groups") or 0) == 1, str(summary))
    check("重复问答计入合并数", int(summary.get("merged") or 0) == 1, str(summary))
    check("提案以知识库问答助手名义生成",
          all(row["created_by"] == "知识库问答助手" for row in versions),
          str([row["created_by"] for row in versions]))
    keep_row = versions[0]
    merge_targets = json.loads(str(keep_row["merge_targets_json"] or "[]"))
    check("重复项不生成提案，只记录合并目标",
          int(keep_row["article_id"]) == keep_id and merge_targets == [merge_id],
          f"article={keep_row['article_id']} targets={merge_targets}")
    check("保留项提案不复制重复正文",
          "## 合并记录" not in str(keep_row["content"])
          and "支付回调超时如何复现" not in str(keep_row["content"])
          and "合并 1 条重复问答" in str(keep_row["change_log"]),
          str(keep_row["change_log"]))

    unchanged = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{keep_id}"))
    check("自动整理不改正式正文", "先看网关重试日志" in str(unchanged.get("content"))
          and int(unchanged.get("currentVersion") or 0) == 1,
          f"version={unchanged.get('currentVersion')}")
    done = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{keep_id}/organize"))
    check("整理完成后状态为已整理完", done.get("organizeStatus") == "done",
          str(done.get("organizeStatus")))
    check("已有待审提案时不重复生成",
          int(again_summary.get("versions") or 0) == 0 and int(again_summary.get("skipped") or 0) == 2,
          str(again_summary))
    check("自动整理计入提示词库调用次数",
          usage_counts.get("knowledge-organize") == 2, str(usage_counts))

    keep_version = int(keep_row["version"])
    api.data("POST", f"/api/v1/knowledge/articles/{keep_id}/versions/{keep_version}/review",
             {"action": "approve", "reviewer": "yichengren", "comment": "采纳整理提案"})
    applied = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{keep_id}"))
    check("提案审核通过后才推进正式版本",
          "## 合并记录" not in str(applied.get("content"))
          and int(applied.get("currentVersion") or 0) == keep_version,
          str(applied.get("currentVersion")))
    merged = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{merge_id}"))
    check("提案通过后被并项标为已合并并指向保留项",
          merged.get("reviewStatus") == "merged"
          and int(merged.get("mergedInto") or 0) == keep_id
          and merged.get("mergedIntoTitle") == applied.get("title"),
          f"status={merged.get('reviewStatus')} into={merged.get('mergedInto')}")
    listing = cast(JsonDict, api.data("GET", "/api/v1/knowledge/articles"))
    listing_ids = [int(item["id"]) for item in cast(list[JsonDict], listing.get("list") or [])]
    check("已合并条目退出正式库默认列表",
          merge_id not in listing_ids and keep_id in listing_ids, str(listing_ids))

    noop = {
        "groups": [],
        "articles": [{
            "id": keep_id,
            "title": applied.get("title"),
            "category": applied.get("category"),
            "tags": applied.get("tags") or "",
            "summary": "仅为测试的摘要改写，不足以让人再审一遍",
        }],
    }

    def fake_noop_chat(candidates: Any, messages: Any, **kwargs: Any) -> Any:
        return SimpleNamespace(
            result=SimpleNamespace(content=json.dumps(noop, ensure_ascii=False),
                                   model="fake-organize"),
            config=SimpleNamespace(model="fake-organize"),
        )

    setattr(knowledge_assistant_service, "chat_with_fallback", fake_noop_chat)
    try:
        with app.app_context():
            conn = get_standalone_conn(db_path)
            try:
                task = job_queue.get_task(conn, task_id)
                assert task is not None
                noop_summary = knowledge_assistant_service.run_organize(
                    conn, task, {"articleId": keep_id}, db_path)
            finally:
                conn.close()
    finally:
        setattr(knowledge_assistant_service, "chat_with_fallback", real_chat)
    check("仅摘要措辞变化不生成待审版本",
          int(noop_summary.get("versions") or 0) == 0
          and int(noop_summary.get("skipped") or 0) == 1,
          str(noop_summary))

    titled = assistant_title("支付回调重复推送怎么定位？", base["answeredAt"], with_time=True)
    check("标题前缀可重复应用",
          assistant_title(titled, base["answeredAt"], with_time=True) == titled
          and titled.count("AI问答助手+") == 1, titled)


def check_storage_switch(api: Api, workdir: pathlib.Path) -> None:
    """自定义目录 → 迁移 → 重建索引；手工丢文件后重建索引可恢复。"""
    target = workdir / "我的知识库"
    result = cast(JsonDict, api.data("POST", "/api/v1/knowledge/storage/migrate",
                                     {"targetPath": str(target)}))
    check("迁移文件成功", int(result["copied"]) >= 1, str(result))
    check("迁移后目录已切换", str(result["target"]) == str(target.resolve()), str(result.get("target")))

    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    check("存储路径已更新", pathlib.Path(str(storage["storagePath"])) == target.resolve(),
          str(storage["storagePath"]))
    check("新目录里有 Markdown 文件", len(list(target.glob("*.md"))) >= 1, str(target))

    # 手工丢一个 .md，重建索引后应重新入库
    manual = target / "手工补充.md"
    manual.write_text(
        "---\nid: 999001\ntitle: 手工补充条目\nversion: 1\ncategory: general\n"
        "tags: [手工]\nreview_status: published\nupdated_at: 2026-01-01T00:00:00Z\n---\n\n手工写入的正文内容。\n",
        encoding="utf-8",
    )
    reindex = cast(JsonDict, api.data("POST", "/api/v1/knowledge/reindex"))
    check("重建索引扫描到文件", int(reindex["scanned"]) >= 1, str(reindex))
    check("重建索引导入新条目", int(reindex["imported"]) >= 1, str(reindex))

    detail = api.data("GET", "/api/v1/knowledge/articles/999001")
    check("重建索引后可按 id 查到", isinstance(detail, dict) and "手工补充条目" in str(detail.get("title")),
          str(detail)[:120])

    search = cast(JsonDict, api.data("GET", "/api/v1/knowledge/search?keyword=手工写入"))
    check("重建索引后可检索", len(search["list"]) >= 1, f"命中={len(search['list'])}")

    # 回到临时目录，避免污染项目默认知识库目录
    api.data("POST", "/api/v1/knowledge/storage/migrate",
             {"targetPath": str(workdir / "knowledge-restore")})


def check_reindex_recovery(api: Api) -> None:
    """改名不留旧文件，软删条目的孤儿 .md 不再撞主键，同 id 多份以最新为准。"""
    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    root = pathlib.Path(str(storage["storagePath"]))

    def article_md(article_id: int, content: str) -> str:
        return (f"---\nid: {article_id}\ntitle: 重建索引条目{article_id}\nversion: 1\n"
                f"category: general\nreview_status: published\n---\n\n{content}\n")

    draft = cast(JsonDict, api.data("POST", "/api/v1/knowledge/drafts", {
        "title": "重建索引验证条目",
        "content": "重建索引前的正文。",
        "category": "测试方法",
        "sourceType": "ai_output",
        "producer": "testpoint_generation",
    }))
    approved = cast(JsonDict, api.data(
        "POST", f"/api/v1/knowledge/drafts/{int(draft['id'])}/review",
        {"action": "approve", "reviewer": "yichengren", "comment": "验证落盘"}))
    article_id = int(approved["articleId"])
    first_file = str(cast(JsonDict, api.data(
        "GET", f"/api/v1/knowledge/articles/{article_id}"))["filePath"])

    api.data("PUT", f"/api/v1/knowledge/articles/{article_id}", {
        "title": "重建索引验证条目（改名）",
        "content": "改名后的正文。",
        "changeLog": "验证改名清理旧文件",
        "operator": "yichengren"})
    versions = cast(list[Any], api.data("GET", f"/api/v1/knowledge/articles/{article_id}/versions"))
    pending = [v for v in versions if v["reviewStatus"] == "pending"]
    api.data("POST", f"/api/v1/knowledge/articles/{article_id}/versions/{int(pending[0]['version'])}/review",
             {"action": "approve", "reviewer": "yichengren", "comment": "通过"})
    new_file = str(cast(JsonDict, api.data(
        "GET", f"/api/v1/knowledge/articles/{article_id}"))["filePath"])
    leftovers = sorted(p.name for p in root.glob(f"{article_id}-*.md"))
    check("改名后落盘文件名跟随标题", bool(new_file) and new_file != first_file
          and (root / new_file).exists(), f"{first_file} → {new_file}")
    check("改名后旧文件被清理", leftovers == [new_file], f"残留={leftovers}")

    api.data("DELETE", f"/api/v1/knowledge/articles/{article_id}")
    orphan = root / f"{article_id}-删除后残留.md"
    orphan.write_text(article_md(article_id, "孤儿文件里的正文。"), encoding="utf-8")
    _, reindex_body = api.call("POST", "/api/v1/knowledge/reindex")
    check("孤儿文件不再让重建索引报内部错误", reindex_body.get("code") == ErrorCode.SUCCESS,
          str(reindex_body)[:160])
    result = cast(JsonDict, reindex_body.get("data") or {})
    check("孤儿文件找回已删除条目", int(result.get("revived") or 0) >= 1, str(result.get("revived")))
    revived = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{article_id}"))
    check("找回条目的正文以文件为准", "孤儿文件里的正文" in str(revived.get("content")),
          str(revived.get("content"))[:60])

    dup_id = 999010
    stale = root / f"{dup_id}-旧文件.md"
    fresh = root / f"{dup_id}-新文件.md"
    stale.write_text(article_md(dup_id, "同一 id 的旧内容。"), encoding="utf-8")
    fresh.write_text(article_md(dup_id, "同一 id 的新内容。"), encoding="utf-8")
    stale_mtime = time.time() - 3600.0
    os.utime(stale, (stale_mtime, stale_mtime))
    dup_result = cast(JsonDict, api.data("POST", "/api/v1/knowledge/reindex"))
    check("同 id 多份文件只取最新一份", int(dup_result["skipped"]) >= 1, str(dup_result))
    dup_article = cast(JsonDict, api.data("GET", f"/api/v1/knowledge/articles/{dup_id}"))
    check("最新文件的内容覆盖旧文件", "同一 id 的新内容" in str(dup_article.get("content")),
          str(dup_article.get("content"))[:60])


def check_db_only_mode(api: Api) -> None:
    """db_only 模式不再写文件，也不再读目录文件回填数据库。"""
    api.data("PUT", "/api/v1/knowledge/storage", {"fileMode": "db_only"})
    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    check("可切换到仅数据库模式", storage["fileMode"] == "db_only", str(storage["fileMode"]))
    result = cast(JsonDict, api.data("POST", "/api/v1/knowledge/reindex"))
    check("仅数据库模式重建索引不读目录文件",
          int(result["scanned"]) == 0 and int(result["imported"]) == 0
          and int(result["updated"]) == 0 and int(result["revived"]) == 0, str(result))
    api.data("PUT", "/api/v1/knowledge/storage", {"fileMode": "mirror"})


def check_assistant_default_mode(api: Api) -> None:
    """问答助手默认模式：出厂 hybrid，可切换，非法值被拒绝。"""
    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    check("问答助手默认模式初始为 hybrid", storage.get("assistantDefaultMode") == "hybrid",
          str(storage.get("assistantDefaultMode")))

    updated = cast(JsonDict, api.data("PUT", "/api/v1/knowledge/storage",
                                      {"assistantDefaultMode": "kb"}))
    check("默认模式可改为 kb", updated.get("assistantDefaultMode") == "kb",
          str(updated.get("assistantDefaultMode")))
    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    check("默认模式已持久化", storage.get("assistantDefaultMode") == "kb",
          str(storage.get("assistantDefaultMode")))

    code = api.code("PUT", "/api/v1/knowledge/storage", {"assistantDefaultMode": "bad"})
    check("非法默认模式被拒绝", code == ErrorCode.PARAM_ERROR, f"code={code}")

    api.data("PUT", "/api/v1/knowledge/storage", {"assistantDefaultMode": "hybrid"})


def check_path_safety(api: Api) -> None:
    """文件名里的非法字符与路径穿越都不能逃出知识库目录。"""
    from aitp.services import knowledge_storage

    name = knowledge_storage.file_name(1, "../../etc/passwd")
    check("文件名替换非法字符", "/" not in name and ".." not in name, name)
    check("文件名长度受限", len(name) <= 124, name)

    root = pathlib.Path("/tmp/aitp-knowledge-root")
    try:
        knowledge_storage.safe_join(root, "../outside.md")
        check("路径穿越被拦截", False, "未抛异常")
    except Exception:
        check("路径穿越被拦截", True)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-knowledge-"))
    try:
        app = build_app(workdir)
        api = Api(app)

        project = cast(JsonDict, api.data("POST", "/api/v1/projects", {
            "code": "KNOW-GOV",
            "name": "知识治理验证项目",
            "owner": "yichengren",
        }))
        project_id = int(project["id"])

        article_id = check_draft_flow(api, project_id)
        check_external_edit(api, article_id)
        check_version_flow(api, article_id)
        check_reference_protection(api, project_id, article_id)
        check_assistant_evolution(api, app, workdir / "aitp.db", article_id)
        check_storage_switch(api, workdir)
        check_reindex_recovery(api)
        check_db_only_mode(api)
        check_assistant_default_mode(api)
        check_path_safety(api)
    finally:
        shutil.rmtree(str(workdir), ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：" + "、".join(_failures))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
