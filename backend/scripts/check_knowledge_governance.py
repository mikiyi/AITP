"""P8 知识治理验收脚本：待审池转正、版本化、引用保护、Markdown 落盘与重建索引。

在临时数据库 + 临时知识库目录上跑完整闭环，校验：

    python backend/scripts/check_knowledge_governance.py

输出逐条 [PASS]/[FAIL]，结尾给出 PASS/FAIL 计数；FAIL>0 时退出码为 1。
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

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services.knowledge_storage import CONFIG_PATH  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

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
        "category": "test_method",
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


def check_db_only_mode(api: Api) -> None:
    """db_only 模式不再写文件。"""
    api.data("PUT", "/api/v1/knowledge/storage", {"fileMode": "db_only"})
    storage = cast(JsonDict, api.data("GET", "/api/v1/knowledge/storage"))
    check("可切换到仅数据库模式", storage["fileMode"] == "db_only", str(storage["fileMode"]))
    api.data("PUT", "/api/v1/knowledge/storage", {"fileMode": "mirror"})


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
        check_storage_switch(api, workdir)
        check_db_only_mode(api)
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
