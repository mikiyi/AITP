"""全局搜索能力边界门禁。

覆盖六组边界：
1. 参数：q 缺失/空白/200 字边界、types 白名单与去重、limit 上下限、projectId 非法值
2. 隔离：项目过滤对四类实体生效，跨项目数据不串
3. 过滤：软删记录、知识待审池不进结果
4. 匹配：子串语义、大小写、%/_ 字面量、注入串、Unicode、首尾空白
5. 排序截断：updated_at 倒序、limit 按类型截断、上限 50
6. 契约：keyword 回显、total 与分组一致、type/typeLabel/route/no/snippet 字段

    python backend/scripts/check_global_search.py

输出逐条 [PASS]/[FAIL]，结尾给出 PASS/FAIL 计数；FAIL>0 时退出码为 1。
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
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import search_service  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]
T0 = "2026-01-01T00:00:00Z"

_passed = 0
_failed = 0
_failures: list[str] = []
_notes: list[str] = []


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


def note(text: str) -> None:
    _notes.append(text)
    print(f"[NOTE] {text}")


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, path: str, **params: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method="GET", query_string=params)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "search.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "search.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def seed(conn: sqlite3.Connection) -> None:
    """插入专用标记数据；所有标记以 ZQX 开头，避免与演示数据互相干扰。"""
    conn.executemany(
        "INSERT INTO biz_project (id, code, name, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, ?)",
        [
            (901, "P901", "搜索测试项目A", T0, T0, 0),
            (902, "P902", "搜索测试项目B", T0, T0, 0),
            (903, "P903", "搜索测试项目-已删除", T0, T0, 1),
        ],
    )
    conn.executemany(
        "INSERT INTO biz_requirement"
        " (project_id, req_no, title, content, tags, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (901, "REQ-ZQX-1", "ZQXREQT 标题命中", "普通正文", "", T0, "2026-02-01T00:00:00Z", 0),
            (901, "REQ-ZQX-2", "普通需求正文", "ZQXREQB 正文命中", "", T0, "2026-02-02T00:00:00Z", 0),
            (901, "REQ-ZQX-3", "普通需求编号", "普通正文", "", T0, "2026-02-03T00:00:00Z", 0),
            (901, "普通编号", "普通需求标签", "普通正文", "ZQXREQTG", T0, "2026-02-04T00:00:00Z", 0),
            (901, "REQ-DEL", "ZQXDREQ 已删除需求", "已删除", "", T0, "2026-02-05T00:00:00Z", 1),
            (902, "REQ-P2", "ZQXP2REQ 另一项目", "另一项目正文", "", T0, "2026-02-06T00:00:00Z", 0),
            (901, "REQ-ORD-T", "ZQXORDERTITLE 旧标题命中", "普通正文", "", T0, "2020-01-01T00:00:00Z", 0),
            (901, "REQ-ORD-B", "普通标题", "ZQXORDERBODY 新正文命中", "", T0, "2026-02-07T00:00:00Z", 0),
            (901, "REQ-ALL", "ZQXALL 全类型命中", "正文", "", T0, "2026-02-08T00:00:00Z", 0),
            (901, "REQ-SPECIAL", "ZQX特殊字符", "命中率 100% 与 a_b 下划线", "", T0, "2026-02-09T00:00:00Z", 0),
            (901, "REQ-CASE", "普通标题", "ZQXCASEsensitive 混合大小写", "", T0, "2026-02-10T00:00:00Z", 0),
            (903, "REQ-DROJ", "ZQXDROJ 已删除项目的需求", "普通正文", "", T0, "2026-02-11T00:00:00Z", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO biz_test_case"
        " (project_id, case_no, title, expected_result, actual_result, tags, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (901, "CASE-ZQX-1", "ZQXCASET 用例标题", "普通预期", "", "", T0, "2026-03-01T00:00:00Z", 0),
            (901, "CASE-ZQX-2", "普通用例", "ZQXCASEE 预期结果命中", "", "", T0, "2026-03-02T00:00:00Z", 0),
            (901, "CASE-ZQX-3", "普通用例", "", "ZQXCASEA 实际结果命中", "", T0, "2026-03-03T00:00:00Z", 0),
            (901, "CASE-ZQX-4", "普通用例", "普通预期", "", "ZQXCASETG", T0, "2026-03-04T00:00:00Z", 0),
            (901, "CASE-ZQX-5", "普通用例", "普通预期", "", "", T0, "2026-03-05T00:00:00Z", 0),
            (901, "CASE-DEL", "ZQXDCASE 已删除用例", "普通预期", "", "", T0, "2026-03-06T00:00:00Z", 1),
            (902, "CASE-P2", "ZQXP2CASE 另一项目", "普通预期", "", "", T0, "2026-03-07T00:00:00Z", 0),
            (901, "CASE-ALL", "ZQXALL 全类型命中", "普通预期", "", "", T0, "2026-03-08T00:00:00Z", 0),
            (903, "CASE-DROJ", "ZQXDROJ 已删除项目的用例", "普通预期", "", "", T0, "2026-03-09T00:00:00Z", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO biz_bug"
        " (project_id, bug_no, title, description, actual_result, module, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            (901, "BUG-ZQX-1", "ZQXBUGT 缺陷标题", "普通描述", "", "", T0, "2026-04-01T00:00:00Z", 0),
            (901, "BUG-ZQX-2", "普通缺陷", "ZQXBUGD 描述命中", "", "", T0, "2026-04-02T00:00:00Z", 0),
            (901, "BUG-ZQX-3", "普通缺陷", "", "ZQXBUGA 实际结果命中", "", T0, "2026-04-03T00:00:00Z", 0),
            (901, "BUG-ZQX-4", "普通缺陷", "普通描述", "", "ZQXBUGM", T0, "2026-04-04T00:00:00Z", 0),
            (901, "BUG-ZQX-5", "普通缺陷", "普通描述", "", "", T0, "2026-04-05T00:00:00Z", 0),
            (901, "BUG-DEL", "ZQXDBUG 已删除缺陷", "普通描述", "", "", T0, "2026-04-06T00:00:00Z", 1),
            (902, "BUG-P2", "ZQXP2BUG 另一项目", "普通描述", "", "", T0, "2026-04-07T00:00:00Z", 0),
            (901, "BUG-ALL", "ZQXALL 全类型命中", "普通描述", "", "", T0, "2026-04-08T00:00:00Z", 0),
            (903, "BUG-DROJ", "ZQXDROJ 已删除项目的缺陷", "普通描述", "", "", T0, "2026-04-09T00:00:00Z", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO biz_knowledge_article"
        " (title, content, summary, category, tags, project_id, status, review_status,"
        "  created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        [
            ("ZQXKBT 知识标题", "普通正文", "普通摘要", "general", "", 901, "published", "published", T0, "2026-05-01T00:00:00Z", 0),
            ("普通知识", "ZQXKBC 知识正文命中", "普通摘要", "general", "", 901, "published", "published", T0, "2026-05-02T00:00:00Z", 0),
            ("普通知识", "普通正文", "ZQXKBS 知识摘要命中", "general", "", 901, "published", "published", T0, "2026-05-03T00:00:00Z", 0),
            ("普通知识", "普通正文", "普通摘要", "general", "ZQXKBTG", 901, "published", "published", T0, "2026-05-04T00:00:00Z", 0),
            ("ZQXDRAFT 待审知识", "普通正文", "普通摘要", "general", "", 901, "draft", "draft", T0, "2026-05-05T00:00:00Z", 0),
            ("ZQXDKB 已删除知识", "普通正文", "普通摘要", "general", "", 901, "published", "published", T0, "2026-05-06T00:00:00Z", 1),
            ("ZQXREV 已撤回知识", "普通正文", "普通摘要", "general", "", 901, "revoked", "revoked", T0, "2026-05-09T00:00:00Z", 0),
            ("ZQXP2KB 另一项目知识", "普通正文", "普通摘要", "general", "", 902, "published", "published", T0, "2026-05-07T00:00:00Z", 0),
            ("ZQXALL 全类型命中", "普通正文", "普通摘要", "general", "", 901, "published", "published", T0, "2026-05-08T00:00:00Z", 0),
            ("ZQXDROJ 已删除项目的知识", "普通正文", "普通摘要", "general", "", 903, "published", "published", T0, "2026-05-10T00:00:00Z", 0),
            ("ZQXGLOBALK 全局知识", "普通正文", "普通摘要", "general", "", None, "published", "published", T0, "2026-05-11T00:00:00Z", 0),
        ],
    )
    conn.executemany(
        "INSERT INTO biz_requirement"
        " (project_id, req_no, title, content, tags, created_at, updated_at, is_deleted)"
        " VALUES (901, ?, ?, '批量数据', '', ?, ?, 0)",
        [(f"BULK-{i}", f"ZQXBULK 批量需求 {i}", T0, f"2026-06-01T00:{i // 60:02d}:{i % 60:02d}Z") for i in range(55)],
    )
    conn.commit()


def groups_of(data: JsonDict) -> dict[str, list[JsonDict]]:
    return {group["type"]: group["list"] for group in data["groups"]}


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-search-"))
    app = build_app(workdir)
    api = Api(app)

    conn = sqlite3.connect(workdir / "search.db")
    try:
        seed(conn)

        # ---------- 1. 参数边界 ----------
        _, body = api.call("/api/v1/search")
        check("缺 q 返回参数错误", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))

        _, body = api.call("/api/v1/search", q="   ")
        check("纯空白 q 返回参数错误", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))

        _, body = api.call("/api/v1/search", q=" ZQXREQT ")
        data = body.get("data") or {}
        check("首尾空白被裁剪", data.get("keyword") == "ZQXREQT", f"keyword={data.get('keyword')!r}")

        _, body = api.call("/api/v1/search", q="登" * 200)
        check("q 恰好 200 字放行", body.get("code") == ErrorCode.SUCCESS, str(body.get("message")))

        _, body = api.call("/api/v1/search", q="登" * 201)
        check("q 超过 200 字拒绝", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))

        _, body = api.call("/api/v1/search", keyword="ZQXREQT")
        check("keyword 是 q 的别名", body.get("code") == ErrorCode.SUCCESS and body["data"]["total"] >= 1)

        _, body = api.call("/api/v1/search", q="ZQXREQT", types="requirement")
        check("types 单选只返回该组", set(groups_of(body["data"])) == {"requirement"}, str(groups_of(body["data"])))

        _, body = api.call("/api/v1/search", q="ZQXALL", types="requirement,case")
        check("types 多选只返回所选组", set(groups_of(body["data"])) == {"requirement", "case"}, str(groups_of(body["data"])))

        _, body = api.call("/api/v1/search", q="ZQXALL", types="requirement,requirement")
        check("types 重复项去重", list(groups_of(body["data"])) == ["requirement"], str(groups_of(body["data"])))

        _, body = api.call("/api/v1/search", q="ZQXALL", types=",,,")
        check("types 全为空项时回落全类型", set(groups_of(body["data"])) == set(search_service.SEARCH_TYPES))

        _, body = api.call("/api/v1/search", q="ZQXALL", types="todo")
        check("未知 types 报参数错误", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))

        # ---------- 2. limit 边界 ----------
        _, body = api.call("/api/v1/search", q="ZQXBULK", limit=1)
        check("limit=1 每类最多 1 条", all(len(v) == 1 for v in groups_of(body["data"]).values()), str(body["data"]["total"]))

        _, body = api.call("/api/v1/search", q="ZQXBULK", limit=0)
        check("limit=0 收敛为 1", len(groups_of(body["data"])["requirement"]) == 1, str(body["data"]["total"]))

        _, body = api.call("/api/v1/search", q="ZQXBULK", limit=-3)
        check("limit 负数收敛为 1", len(groups_of(body["data"])["requirement"]) == 1, str(body["data"]["total"]))

        _, body = api.call("/api/v1/search", q="ZQXBULK", limit=999)
        check("limit 超上限截到 50", len(groups_of(body["data"])["requirement"]) == 50, str(len(groups_of(body["data"])["requirement"])))

        _, body = api.call("/api/v1/search", q="ZQXBULK", limit="abc")
        check("limit 非整数报参数错误", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))

        _, body = api.call("/api/v1/search", q="ZQXBULK", limit="1.5")
        check("limit 小数报参数错误", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))

        _, body = api.call("/api/v1/search", q="ZQXALL", types="requirement,case", limit=1)
        check(
            "limit 按类型分别生效（每类各取 1 条）",
            body["data"]["total"] == 2,
            f"total={body['data']['total']}",
        )

        # ---------- 3. 项目隔离 ----------
        _, body = api.call("/api/v1/search", q="ZQXP2", projectId=901)
        check("projectId 过滤掉其他项目数据", body["data"]["total"] == 0, str(body["data"]["total"]))

        _, body = api.call("/api/v1/search", q="ZQXREQT 标题", projectId=901)
        hits = groups_of(body["data"]).get("requirement", [])
        check("projectId 命中本项目数据", len(hits) == 1, str(len(hits)))

        _, body = api.call("/api/v1/search", q="ZQXP2")
        check("不传 projectId 时跨项目可见", body["data"]["total"] >= 4, str(body["data"]["total"]))

        _, body = api.call("/api/v1/search", q="ZQXP2", projectId="")
        check("projectId 空串视为不过滤", body.get("code") == ErrorCode.SUCCESS and body["data"]["total"] >= 4)

        _, body = api.call("/api/v1/search", q="ZQXP2", projectId="abc")
        check("projectId 非整数报参数错误", body.get("code") == ErrorCode.PARAM_ERROR, str(body.get("message")))

        _, body = api.call("/api/v1/search", q="ZQXP2", projectId=-1)
        check("projectId 负数返回空结果", body.get("code") == ErrorCode.SUCCESS and body["data"]["total"] == 0)

        _, body = api.call("/api/v1/search", q="ZQXDROJ")
        check("已删除项目下的四类数据不参与搜索", body["data"]["total"] == 0, str(body["data"]["total"]))

        _, body = api.call("/api/v1/search", q="ZQXGLOBALK")
        hits = groups_of(body["data"]).get("knowledge", [])
        check(
            "全局知识（无项目）仍可搜索且不带项目标识",
            len(hits) == 1 and hits[0].get("projectId") is None and hits[0].get("projectName") == "",
            str(hits),
        )

        # ---------- 4. 过滤边界 ----------
        _, body = api.call("/api/v1/search", q="ZQXDREQ")
        check("软删需求不出现", body["data"]["total"] == 0)
        _, body = api.call("/api/v1/search", q="ZQXDCASE")
        check("软删用例不出现", body["data"]["total"] == 0)
        _, body = api.call("/api/v1/search", q="ZQXDBUG")
        check("软删缺陷不出现", body["data"]["total"] == 0)
        _, body = api.call("/api/v1/search", q="ZQXDKB")
        check("软删知识不出现", body["data"]["total"] == 0)

        _, body = api.call("/api/v1/search", q="ZQXDRAFT")
        check("知识待审池不出现", body["data"]["total"] == 0)

        _, body = api.call("/api/v1/search", q="ZQXREV")
        check("知识已撤回条目不出现", body["data"]["total"] == 0, f"total={body['data']['total']}")

        # ---------- 5. 匹配语义 ----------
        _, body = api.call("/api/v1/search", q="ZQXCASEE")
        hits = groups_of(body["data"]).get("case", [])
        check("用例预期结果参与检索", len(hits) == 1 and "ZQXCASEE" in hits[0]["snippet"], f"hits={len(hits)}")

        _, body = api.call("/api/v1/search", q="ZQXCASEA")
        check("用例实际结果参与检索", len(groups_of(body["data"]).get("case", [])) == 1)
        _, body = api.call("/api/v1/search", q="ZQXCASETG")
        check("用例标签参与检索", len(groups_of(body["data"]).get("case", [])) == 1)
        _, body = api.call("/api/v1/search", q="CASE-ZQX-5")
        check("用例编号参与检索", len(groups_of(body["data"]).get("case", [])) == 1)
        _, body = api.call("/api/v1/search", q="ZQXBUGM")
        check("缺陷模块参与检索", len(groups_of(body["data"]).get("bug", [])) == 1)
        _, body = api.call("/api/v1/search", q="ZQXKBTG")
        check("知识标签参与检索", len(groups_of(body["data"]).get("knowledge", [])) == 1)

        _, body = api.call("/api/v1/search", q="ZQXREQTG")
        check(
            "需求标签参与检索",
            body["data"]["total"] == 1,
            f"total={body['data']['total']}",
        )
        _, body = api.call("/api/v1/search", q="ZQXREQTG")
        hit = groups_of(body["data"])["requirement"][0]
        check("标签命中时 snippet 带标签前缀", hit["snippet"].startswith("标签："), f"snippet={hit['snippet']!r}")

        _, body = api.call("/api/v1/search", q="zqxcasesensitive")
        check("ASCII 匹配不区分大小写", body["data"]["total"] == 1, f"total={body['data']['total']}")
        _, body = api.call("/api/v1/search", q="ZQXCASEsensitive")
        check("大小写完全一致时可命中", body["data"]["total"] == 1, f"total={body['data']['total']}")
        note("匹配统一走 lower()，英文大小写不敏感；SQLite lower() 不影响中文。")

        _, body = api.call("/api/v1/search", q="ZQX特殊字符")
        check("含 % 的正文可按整串命中", body["data"]["total"] == 1, f"total={body['data']['total']}")
        # LIKE 通配符语义下 % 会命中全部行、_ 会命中任意单字符行；instr 下只应命中字面量行
        _, body = api.call("/api/v1/search", q="%")
        check("% 按字面量处理不当通配符", body["data"]["total"] == 1, f"total={body['data']['total']}")
        _, body = api.call("/api/v1/search", q="_")
        check("_ 按字面量处理不当通配符", body["data"]["total"] == 1, f"total={body['data']['total']}")

        _, body = api.call("/api/v1/search", q="' OR 1=1 --")
        check("SQL 注入串不报错且不放大结果", body.get("code") == ErrorCode.SUCCESS and body["data"]["total"] == 0)

        _, body = api.call("/api/v1/search", q='登录" ) UNION SELECT 1 --')
        check("UNION 注入串不报错", body.get("code") == ErrorCode.SUCCESS and body["data"]["total"] == 0)

        _, body = api.call("/api/v1/search", q="<img src=x onerror=alert(1)>")
        check(
            "XSS 串原样回显不报错",
            body.get("code") == ErrorCode.SUCCESS and body["data"]["keyword"] == "<img src=x onerror=alert(1)>",
        )

        _, body = api.call("/api/v1/search", q="🔍ZQX")
        check("emoji 关键词不报错", body.get("code") == ErrorCode.SUCCESS and body["data"]["total"] == 0)

        _, body = api.call("/api/v1/search", q="ZQXREQB 正文")
        check("空格多词按 AND 命中同一条", body["data"]["total"] == 1, f"total={body['data']['total']}")
        _, body = api.call("/api/v1/search", q="ZQXREQB 不存在")
        check("空格多词缺一即不命中", body["data"]["total"] == 0, f"total={body['data']['total']}")
        _, body = api.call("/api/v1/search", q="zqxall 全类型")
        check(
            "多词可跨字段命中且大小写不敏感",
            set(groups_of(body["data"])) == set(search_service.SEARCH_TYPES),
            str(set(groups_of(body["data"]))),
        )
        note("空格分隔的多个关键词按 AND 组合，每个词可落在任意检索字段里。")

        # ---------- 6. 排序与契约 ----------
        _, body = api.call("/api/v1/search", q="ZQXORDER")
        hits = groups_of(body["data"]).get("requirement", [])
        check(
            "标题命中优先，其次按 updated_at 倒序",
            len(hits) == 2 and hits[0]["title"] == "ZQXORDERTITLE 旧标题命中",
            str([h["title"] for h in hits]),
        )

        _, body = api.call("/api/v1/search", q="ZQXREQT")
        hit = groups_of(body["data"])["requirement"][0]
        check(
            "命中项字段齐全",
            hit.get("type") == "requirement"
            and hit.get("typeLabel") == "需求"
            and hit.get("route") == "/requirements"
            and hit.get("no") == "REQ-QZX-1".replace("QZX", "ZQX")
            and hit.get("projectId") == 901
            and hit.get("projectName") == "搜索测试项目A",
            str(hit),
        )
        _, body = api.call("/api/v1/search", q="ZQXREQB")
        hit = groups_of(body["data"])["requirement"][0]
        check(
            "正文命中时 snippet 含关键词",
            "ZQXREQB" in hit["snippet"],
            f"snippet={hit['snippet']!r}",
        )

        _, body = api.call("/api/v1/search", q="REQ-ZQX-2")
        hit = groups_of(body["data"])["requirement"][0]
        check(
            "仅编号命中时不重复截取正文",
            hit["snippet"] == "",
            f"snippet={hit['snippet']!r}",
        )
        note("标题/编号已在结果行展示；snippet 只在正文、实际结果、模块、标签等字段命中时截取。")

        _, body = api.call("/api/v1/search", q="ZQXCASEA")
        hit = groups_of(body["data"])["case"][0]
        check("实际结果命中时 snippet 带字段前缀", hit["snippet"].startswith("实际结果："), f"snippet={hit['snippet']!r}")

        _, body = api.call("/api/v1/search", q="ZQXALL", limit=8)
        data = body["data"]
        total = data["total"]
        summed = sum(len(group["list"]) for group in data["groups"])
        check("total 等于各分组条数之和", total == summed, f"total={total} summed={summed}")
        check("total 不超过 limit × 类型数", total <= 8 * len(data["groups"]), f"total={total}")
        check("空分组不下发", all(group["list"] for group in data["groups"]))
        check(
            "分组带真实命中总数",
            all(group["total"] >= len(group["list"]) for group in data["groups"]),
            str([(g["type"], g["total"], len(g["list"])) for g in data["groups"]]),
        )
        check(
            "matched 等于各分组真实命中数之和",
            data["matched"] == sum(group["total"] for group in data["groups"]),
            f"matched={data['matched']}",
        )
        check(
            "四类实体均可被同一关键词命中",
            set(groups_of(data)) == set(search_service.SEARCH_TYPES),
            str(set(groups_of(data))),
        )

        _, body = api.call("/api/v1/search", q="ZQXBULK", limit=8)
        data = body["data"]
        group = groups_of(data)["requirement"]
        check(
            "批量命中时 total 展示真实总数、list 受 limit 约束",
            len(group) == 8 and data["matched"] == 55 and data["total"] == 8,
            f"list={len(group)} matched={data['matched']} total={data['total']}",
        )

        _, body = api.call("/api/v1/search", q="不存在的关键词-9f3c")
        check(
            "无命中时返回空分组且关键词回显",
            body["data"]["total"] == 0
            and body["data"]["groups"] == []
            and body["data"]["keyword"] == "不存在的关键词-9f3c",
        )

        # ---------- 7. service 层兜底 ----------
        direct = sqlite3.connect(workdir / "search.db")
        direct.row_factory = sqlite3.Row
        try:
            result = search_service.search_all(direct, "   ")
            check("service 层空白关键词直接返回空", result["total"] == 0 and result["groups"] == [])
            result = search_service.search_all(direct, "ZQXALL", types=("bad-type",))
            check("service 层未知类型回落全类型", len(result["groups"]) == 4, str(len(result["groups"])))
        finally:
            direct.close()

        # ---------- 8. 方法边界 ----------
        resp, body = api.call("/api/v1/search")
        check("GET 接口可用", body.get("code") in (ErrorCode.SUCCESS, ErrorCode.PARAM_ERROR), str(body.get("code")))
        post = api.client.open("/api/v1/search", method="POST", json={"q": "ZQX"})
        post_body = post.get_json(silent=True) or {}
        check("POST 不被允许", post_body.get("code") == ErrorCode.METHOD_NOT_ALLOWED, str(post_body.get("code")))
    finally:
        conn.close()
        import shutil

        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 48)
    if _notes:
        print("NOTES:")
        for item in _notes:
            print(f"  - {item}")
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
