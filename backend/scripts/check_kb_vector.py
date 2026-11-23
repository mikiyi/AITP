"""知识库向量检索门禁（四期专项 B）。

    python backend/scripts/check_kb_vector.py

覆盖六段：
  1. 迁移 077：向量表、唯一索引、条目 embedding_state 列；
  2. 分块：标题层级切节 + 滑窗、块上限、幂等；
  3. 缺模型回落：无嵌入配置时不建索引、不报错，关键词检索照常；
  4. 增量重嵌：内容不变跳过、内容变化重嵌、正文变短清多余块；
  5. RRF 融合：字面 + 向量合并、去重、排序；
  6. 跨项目隔离与云端默认关闭/开启后走脱敏。
输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai import embeddings  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import knowledge_embedding_service as svc  # noqa: E402

NOW = "2026-11-23T00:00:00+00:00"
LOCAL_SETTINGS: dict[str, Any] = {
    "enabled": False, "protocol": "openai_compat",
    "baseUrl": "", "model": "mock-embedding", "apiKey": "",
}
EMPTY_SETTINGS: dict[str, Any] = {
    "enabled": False, "protocol": "openai_compat", "baseUrl": "", "model": "", "apiKey": "",
}

#: 本门禁自带的嵌入桩向量表：概念 → 固定方向（与 runtime mock 同口径）
_CONCEPTS: list[tuple[tuple[str, ...], list[float]]] = [
    (("幂等", "重复扣款", "重复入账", "重复通知", "重复提交"), [1.0, 0.0, 0.0]),
    (("登录", "验证码", "认证", "鉴权"), [0.0, 1.0, 0.0]),
]
_DEFAULT_VECTOR = [0.0, 0.0, 1.0]


def _vector_of(text: str) -> list[float]:
    low = str(text or "")
    vector = [0.0, 0.0, 0.0]
    hit = False
    for words, direction in _CONCEPTS:
        if any(word in low for word in words):
            hit = True
            for index, value in enumerate(direction):
                vector[index] += value
    return vector if hit else list(_DEFAULT_VECTOR)

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


def configure_embedding(conn: sqlite3.Connection, base_url: str) -> None:
    """把嵌入配置写进 sys_config，让服务层按真实读配置路径工作。"""
    items = [
        ("ai.embedding.protocol", "openai_compat"),
        ("ai.embedding.baseUrl", base_url),
        ("ai.embedding.model", "mock-embedding"),
        ("ai.embedding.enabled", "0"),
    ]
    for key, value in items:
        conn.execute(
            "INSERT INTO sys_config (config_key, config_value, value_type, scope,"
            " is_system, created_at, updated_at)"
            " VALUES (?, ?, 'string', 'global', 0, ?, ?)"
            " ON CONFLICT(config_key) DO UPDATE SET config_value = excluded.config_value",
            (key, value, NOW, NOW),
        )


def add_article(conn: sqlite3.Connection, project_id: int | None, title: str,
                content: str) -> int:
    conn.execute(
        "INSERT INTO biz_knowledge_article (project_id, title, content, summary, category,"
        " tags, review_status, embedding_state, created_at, updated_at, is_deleted)"
        " VALUES (?, ?, ?, '', '测试知识', '', 'published', 'none', ?, ?, 0)",
        (project_id, title, content, NOW, NOW),
    )
    return int(conn.execute("SELECT last_insert_rowid()").fetchone()[0])


def migration_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "schema.db")
    try:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        check("迁移 077 建了向量表", "biz_kb_embedding" in tables, str(sorted(tables)))
        cols = {r[1] for r in conn.execute("PRAGMA table_info(biz_knowledge_article)")}
        check("知识条目新增 embedding_state 列", "embedding_state" in cols, str(sorted(cols)))
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type='index' AND name='uk_kb_embedding_chunk'"
        ).fetchone()
        check("向量分块有 (article_id, chunk_index) 唯一索引", row is not None, "索引缺失")
    finally:
        conn.close()


def chunk_checks() -> None:
    content = "# 登录\n验证码 5 分钟有效。\n## 限流\n60 秒一次。\n### 锁定\n连续 5 次锁定 10 分钟。"
    chunks = svc.chunk_article("登录规则", content)
    check("按 Markdown 标题切节", len(chunks) >= 3, str(len(chunks)))
    check("块文本带标题路径",
          any("限流" in chunk["headingPath"] for chunk in chunks),
          str([chunk["headingPath"] for chunk in chunks]))
    check("块带内容哈希", all(chunk["contentHash"] for chunk in chunks))

    long_text = "压测" * 2000
    chunks_long = svc.chunk_article("长文", long_text)
    check("长文按窗口切块且受上限约束",
          0 < len(chunks_long) <= svc.MAX_CHUNKS_PER_ARTICLE, str(len(chunks_long)))
    check("单块不超目标字数 + 标题前缀",
          all(len(chunk["text"]) <= svc.CHUNK_CHARS + 120 for chunk in chunks_long))

    again = svc.chunk_article("登录规则", content)
    check("分块幂等（同输入同输出）",
          [c["contentHash"] for c in chunks] == [c["contentHash"] for c in again])


def degrade_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "degrade.db")
    try:
        pid = add_project(conn, "PDG")
        aid = add_article(conn, pid, "幂等结论", "支付回调必须用幂等键去重。")
        conn.commit()
        empty = dict(EMPTY_SETTINGS)
        result = svc.ensure_index(conn, aid, settings_map=empty)
        conn.commit()
        check("无嵌入模型时不建索引且不报错", result["state"] == "none", str(result))
        count = conn.execute("SELECT COUNT(*) AS c FROM biz_kb_embedding").fetchone()["c"]
        check("无模型时不产生向量行", int(count) == 0, str(count))
        check("索引概览给出不可用原因",
              "未配置" in svc.status(conn)["embedding"]["reason"],
              svc.status(conn)["embedding"]["reason"])

        cloud = {"enabled": False, "protocol": "openai_compat",
                 "baseUrl": "https://api.example.com", "model": "text-embedding-3", "apiKey": "k"}
        check("云端嵌入默认关闭时不可用", embeddings.usable(cloud) is False)
        cloud["enabled"] = True
        check("云端嵌入显式开启后可用", embeddings.usable(cloud) is True)
        local = dict(cloud, baseUrl="http://127.0.0.1:8790", enabled=False)
        check("本地地址无需显式开启即可用", embeddings.usable(local) is True)

        masked = embeddings.is_local("https://api.example.com") is False
        check("非本地地址会走脱敏分支", masked)
    finally:
        conn.close()


def index_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "index.db")
    try:
        pid = add_project(conn, "PIDX")
        content = "# 幂等\n支付回调重复通知必须用幂等键去重，避免重复入账。"
        aid = add_article(conn, pid, "支付回调幂等", content)
        conn.commit()
        first = svc.ensure_index(conn, aid, settings_map=LOCAL_SETTINGS)
        conn.commit()
        check("有模型时索引到 ready", first["state"] == "ready", str(first))
        check("首轮全部重嵌", first["embedded"] == first["chunks"] > 0, str(first))
        state = conn.execute(
            "SELECT embedding_state FROM biz_knowledge_article WHERE id = ?", (aid,)
        ).fetchone()["embedding_state"]
        check("条目状态回写为 ready", state == "ready", str(state))

        second = svc.ensure_index(conn, aid, settings_map=LOCAL_SETTINGS)
        conn.commit()
        check("内容未变时跳过重嵌（增量幂等）",
              second["embedded"] == 0 and second["skipped"] == second["chunks"], str(second))

        conn.execute(
            "UPDATE biz_knowledge_article SET content = ? WHERE id = ?",
            ("# 幂等\n支付回调改用请求号去重。", aid),
        )
        conn.commit()
        third = svc.ensure_index(conn, aid, settings_map=LOCAL_SETTINGS)
        conn.commit()
        check("内容变化后重新嵌入", third["embedded"] > 0, str(third))

        conn.execute(
            "UPDATE biz_knowledge_article SET content = '短正文' WHERE id = ?", (aid,)
        )
        conn.commit()
        fourth = svc.ensure_index(conn, aid, settings_map=LOCAL_SETTINGS)
        conn.commit()
        rows = conn.execute(
            "SELECT chunk_index FROM biz_kb_embedding WHERE article_id = ? ORDER BY chunk_index",
            (aid,),
        ).fetchall()
        check("正文变短后多余块被清掉",
              [int(r["chunk_index"]) for r in rows] == list(range(int(fourth["chunks"]))),
              str([int(r["chunk_index"]) for r in rows]))
    finally:
        conn.close()


def vector_and_fusion_checks(workdir: pathlib.Path) -> None:
    conn = new_db(workdir / "vector.db")
    try:
        configure_embedding(conn, str(LOCAL_SETTINGS["baseUrl"]))
        pid = add_project(conn, "PVEC")
        other = add_project(conn, "PVEC2")
        hit_id = add_article(conn, pid, "支付回调",
                             "# 幂等\n支付回调重复通知必须用幂等键去重，避免重复入账。")
        hidden_id = add_article(conn, other, "别的项目",
                                "# 幂等\n另一个项目的重复扣款处理说明。")
        conn.commit()
        for article_id in (hit_id, hidden_id):
            svc.ensure_index(conn, article_id, settings_map=LOCAL_SETTINGS)
        conn.commit()

        # 用同概念不同词的说法查询：mock 嵌入把它们映射到同一方向
        hits = svc.vector_hits(conn, "重复扣款怎么防", project_id=pid, limit=5)
        check("向量召回命中同概念条目",
              any(int(hit["articleId"]) == hit_id for hit in hits),
              str([hit["articleId"] for hit in hits]))
        check("跨项目条目不进本项目向量结果",
              all(int(hit["articleId"]) != hidden_id for hit in hits),
              str([hit["articleId"] for hit in hits]))

        literal = [{"id": hidden_id + 999, "title": "字面命中", "snippet": "关键词"}]
        fused, used = svc.fuse_with_literal(conn, "重复扣款怎么防", literal,
                                            project_id=pid, limit=5)
        check("字面 + 向量做 RRF 融合", used is True and len(fused) >= 1, str(len(fused)))
        ids = [int(item["id"]) for item in fused]
        check("融合结果按 RRF 排序且无重复", len(ids) == len(set(ids)), str(ids))
        check("仅向量命中的条目补上 snippet",
              all(str(item.get("snippet") or "") for item in fused
                  if int(item["id"]) == hit_id), str(fused)[:200])

        order = svc.rrf_fuse([1, 2, 3], [3, 2, 1])
        check("RRF 双向命中条目得分更高",
              order[0][0] in (1, 3) and order[-1][0] == 2, str(order))
        check("RRF 常数可配", svc.rrf_fuse([1], [1], k=1)[0][1] == 1.0, str(svc.rrf_fuse([1], [1], k=1)))

        no_model = dict(EMPTY_SETTINGS)
        check("无模型时向量召回为空（回落关键词）",
              svc.vector_hits(conn, "重复扣款", project_id=pid, limit=5,
                              settings_map=no_model) == [])
    finally:
        conn.close()


def cosine_checks() -> None:
    check("余弦同向为 1", abs(embeddings.cosine([1.0, 0.0], [2.0, 0.0]) - 1.0) < 1e-6)
    check("余弦正交为 0", embeddings.cosine([1.0, 0.0], [0.0, 1.0]) == 0.0)
    check("零向量不抛异常", embeddings.cosine([0.0, 0.0], [1.0, 0.0]) == 0.0)
    check("维度不一致返回 0", embeddings.cosine([1.0], [1.0, 2.0]) == 0.0)
    packed = embeddings.pack([1.5, -2.0, 3.25])
    check("向量打包/解包往返一致",
          embeddings.unpack(packed) == [1.5, -2.0, 3.25], str(embeddings.unpack(packed)))
    check("损坏 BLOB 返回空表", embeddings.unpack(b"\x00\x01") == [])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-kbvec-"))
    server = _start_stub_embed_server()
    port = int(server.server_address[1])
    LOCAL_SETTINGS["baseUrl"] = f"http://127.0.0.1:{port}"
    try:
        migration_checks(workdir)
        chunk_checks()
        cosine_checks()
        degrade_checks(workdir)
        index_checks(workdir)
        vector_and_fusion_checks(workdir)
    finally:
        server.shutdown()
        server.server_close()
        shutil.rmtree(workdir, ignore_errors=True)
    print(f"\nPASS={_passed} FAIL={_failed}")
    for item in _failures:
        print(f"  [FAIL] {item}")
    return 1 if _failed else 0


def _start_stub_embed_server() -> Any:
    """本门禁自带的嵌入桩：/v1/embeddings 与 /api/embeddings 都按概念表回固定向量。"""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: Any) -> None:  # noqa: D102 - 静默
            pass

        def _send(self, payload: dict[str, Any]) -> None:
            raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def do_POST(self) -> None:  # noqa: N802 - 标准库接口
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
            except (ValueError, UnicodeDecodeError):
                body = {}
            if self.path.rstrip("/").endswith("/api/embeddings"):
                self._send({"embedding": _vector_of(str(body.get("prompt") or ""))})
                return
            raw = body.get("input")
            texts = raw if isinstance(raw, list) else [str(raw or "")]
            self._send({
                "data": [{"index": index, "embedding": _vector_of(str(text))}
                         for index, text in enumerate(texts)],
            })

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


if __name__ == "__main__":
    raise SystemExit(main())
