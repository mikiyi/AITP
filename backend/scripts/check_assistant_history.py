"""问答助手历史按会话落库门禁：换个菜单回来，问过的问题还在。

用户报的现象：问答助手里自己问的问题，切个菜单再回来就看不到了。根因是对话只活在
组件实例里（`messages` 是组件内 ref，`<keep-alive :max="6">` 一旦把它挤掉就销毁重建，
首页卡片与问答页还各自一份），后端一条记录都没留——关浏览器、重启服务同理全丢。

新口径钉死在这里：

  1) 每次问答（含知识库直答）都落 `biz_ai_session` + 一 user 一 assistant 两条
     `biz_ai_message`，模式/引用/耗时/脱敏/外发提示随消息留档；
  2) 历史默认回放最近一条会话，指定 sessionId 就回放那条；「清空对话」= 另起会话，
     旧会话仍在库里；
  3) 回答的留档时间由服务端给出并在回放时原样返回，前端「加入知识库」用它算指纹，
     所以回放后按钮状态与真实待审池一致，不会重复入池；
  4) 给定会话不存在或不是问答助手的会话，提问直接拒绝——不白烧一次模型额度；
  5) 只写会话与消息：不产生 AI 执行记录、不进成本与预算口径（决策不变）。

    python backend/scripts/check_assistant_history.py

全程只用临时库与本机 mock 模型，绝不碰正式库、正式服务与云端额度。
"""

from __future__ import annotations

import json
import pathlib
import sqlite3
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import knowledge_assistant_service as ka  # noqa: E402

V1 = "/api/v1"
SUCCESS = 200000
MOCK_ANSWER = "排查思路：先看回调幂等键，再看重试退避。"
PROMPT_TOKENS = 1200
COMPLETION_TOKENS = 340

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


# ---------------- 本机 mock 模型（绝不打云端） ----------------

class _MockHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    hits = 0
    #: 每次 /chat/completions 收到的 messages，用来断言多轮上下文真的进了模型请求
    last_messages: list[dict[str, Any]] = []

    def log_message(self, fmt: str, *args: object) -> None:  # noqa: D102 - 屏蔽 stderr 噪声
        pass

    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler 约定
        _MockHandler.hits += 1
        length = int(self.headers.get("Content-Length") or 0)
        try:
            body = cast("dict[str, Any]", json.loads(self.rfile.read(length).decode("utf-8")))
        except Exception:
            body = {}
        messages = body.get("messages")
        if isinstance(messages, list):
            _MockHandler.last_messages = cast("list[dict[str, Any]]", messages)
        payload = json.dumps({
            "id": "chatcmpl-history",
            "object": "chat.completion",
            "model": body.get("model") or "mock-local",
            "choices": [{
                "index": 0,
                "message": {"role": "assistant", "content": MOCK_ANSWER},
                "finish_reason": "stop",
            }],
            "usage": {
                "prompt_tokens": PROMPT_TOKENS,
                "completion_tokens": COMPLETION_TOKENS,
                "total_tokens": PROMPT_TOKENS + COMPLETION_TOKENS,
            },
        }, ensure_ascii=False).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_mock() -> ThreadingHTTPServer:
    server = ThreadingHTTPServer(("127.0.0.1", 0), _MockHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


# ---------------- 请求脚手架 ----------------

def raw(client: Any, method: str, path: str,
        payload: dict[str, Any] | None = None) -> tuple[int, dict[str, Any]]:
    if method == "GET":
        resp = client.get(path)
    else:
        resp = client.post(path, json=payload or {})
    return int(resp.status_code), cast("dict[str, Any]", resp.get_json() or {})


def data_of(client: Any, method: str, path: str,
            payload: dict[str, Any] | None = None) -> dict[str, Any]:
    status, resp = raw(client, method, path, payload)
    if status != 200 or int(resp.get("code") or 0) != SUCCESS:
        raise AssertionError(f"{method} {path} 失败：status={status} body={str(resp)[:300]}")
    got = resp.get("data")
    return cast("dict[str, Any]", got) if isinstance(got, dict) else {"value": got}


def ask(client: Any, question: str, mode: str = "kb",
        session_id: int | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {"question": question, "mode": mode}
    if session_id is not None:
        payload["sessionId"] = session_id
    return data_of(client, "POST", f"{V1}/knowledge/ask", payload)


def history(client: Any, session_id: int | None = None) -> dict[str, Any]:
    suffix = f"?sessionId={session_id}" if session_id is not None else ""
    return data_of(client, "GET", f"{V1}/knowledge/assistant/history{suffix}")


# ---------------- 库里直接取证 ----------------

def query(db_path: pathlib.Path, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(row) for row in conn.execute(sql, params).fetchall()]
    finally:
        conn.close()


def session_rows(db_path: pathlib.Path) -> list[dict[str, Any]]:
    return query(
        db_path,
        "SELECT * FROM biz_ai_session WHERE module = ? AND is_deleted = 0 ORDER BY id",
        (ka.SESSION_MODULE,),
    )


def message_rows(db_path: pathlib.Path, session_id: int) -> list[dict[str, Any]]:
    return query(
        db_path,
        "SELECT * FROM biz_ai_message WHERE session_id = ? AND is_deleted = 0 ORDER BY id",
        (session_id,),
    )


def at(rows: list[Any], index: int) -> Any:
    """越界当成空值返回：让断言报 FAIL，而不是把门禁跑崩在索引上。"""
    return rows[index] if 0 <= index < len(rows) else {}


def dig(value: Any, *keys: str) -> Any:
    for key in keys:
        if not isinstance(value, dict):
            return None
        value = value.get(key)
    return value


def _meta_json(raw: Any) -> dict[str, Any]:
    if not raw:
        return {}
    parsed = json.loads(str(raw))
    return cast("dict[str, Any]", parsed) if isinstance(parsed, dict) else {}


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-history-"))
    db_path = workdir / "history.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "history.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    client = create_app(cfg).test_client()
    server = start_mock()
    base_url = f"http://127.0.0.1:{int(server.server_address[1])}/v1"
    try:
        data_of(client, "POST", f"{V1}/settings/ai/profiles", {
            "name": "历史门禁本地档案", "provider": "ollama", "protocol": "openai_compat",
            "baseUrl": base_url, "model": "mock-local", "isLocal": True,
            "isActive": True, "isDefault": True, "priority": 300, "timeout": 10,
        })

        # ---------- ① 知识库直答也留痕：不调模型，问题与结论照样进会话 ----------
        first = ask(client, "支付回调重复推送怎么排查？", "kb")
        sid = int(first.get("sessionId") or 0)
        check("提问返回 sessionId（前端据此续用同一条会话）", sid > 0, str(first)[:200])
        check("回答给出服务端留档时间 answeredAt", bool(first.get("answeredAt")), str(first)[:200])
        sessions = session_rows(db_path)
        first_session = at(sessions, 0)
        check("会话落库一条，模块标记为问答助手",
              len(sessions) == 1 and first_session.get("module") == ka.SESSION_MODULE, str(sessions))
        check("会话标题取首个问题",
              first_session.get("title") == "支付回调重复推送怎么排查？", str(first_session.get("title")))
        rows = message_rows(db_path, sid)
        check("一问一答两条消息按顺序入库",
              len(rows) == 2 and [r["role"] for r in rows] == ["user", "assistant"], str(rows))
        check("问题原文留在 user 消息里", at(rows, 0).get("content") == "支付回调重复推送怎么排查？",
              str(at(rows, 0).get("content")))
        meta = _meta_json(at(rows, 1).get("message_meta"))
        check("知识库直答的模式与引用随消息留档",
              meta.get("mode") == "kb" and isinstance(meta.get("refs"), list), str(meta)[:200])
        check("知识库直答不调模型：prompt_code 与 model 都不写",
              at(rows, 1).get("prompt_code") is None and not at(rows, 1).get("model"),
              str(at(rows, 1))[:200])

        replay = history(client)
        replay_messages = cast("list[dict[str, Any]]", replay.get("messages") or [])
        check("历史默认回放最近一条会话", int(replay.get("sessionId") or 0) == sid, str(replay)[:200])
        check("回放能拿回问过的问题（这就是用户看不到的那段历史）",
              [m.get("content") for m in replay_messages] == [r["content"] for r in rows],
              str(replay_messages)[:300])
        check("回放消息带回答模式标签",
              dig(at(replay_messages, 1), "meta", "modeLabel") == "知识库回答",
              str(at(replay_messages, 1)))
        check("回放未超上限时不报截断", replay.get("truncated") is False, str(replay.get("truncated")))

        # ---------- ② 同一条会话续问：换个菜单回来还是这条线 ----------
        second = ask(client, "那幂等键放在哪一层合适？", "kb", sid)
        check("续用会话时 sessionId 不变", int(second.get("sessionId") or 0) == sid, str(second)[:200])
        check("同一条会话累计四条消息，没有另开会话",
              len(session_rows(db_path)) == 1 and len(message_rows(db_path, sid)) == 4,
              str(session_rows(db_path)))
        check("回放按时间正序给出两轮问答",
              len(history(client).get("messages") or []) == 4, str(history(client))[:200])

        # ---------- ③ 「清空对话」= 另起会话，旧的仍然可查 ----------
        third = ask(client, "新线程的第一个问题", "kb")
        new_sid = int(third.get("sessionId") or 0)
        check("不带 sessionId 的提问新开一条会话", new_sid != sid and new_sid > 0,
              f"old={sid} new={new_sid}")
        latest = history(client)
        check("默认回放只看最近这条线程",
              int(latest.get("sessionId") or 0) == new_sid and len(latest.get("messages") or []) == 2,
              str(latest)[:200])
        old_replay = history(client, sid)
        check("旧会话按 id 仍可回放（清空不等于丢数据）",
              int(old_replay.get("sessionId") or 0) == sid and len(old_replay.get("messages") or []) == 4,
              str(old_replay)[:200])

        # ---------- ④ 调模型的回答同样留痕，token 与模型落在消息上 ----------
        hits_before = _MockHandler.hits
        called = ask(client, "重复推送的排查清单", "llm")
        called_sid = int(called.get("sessionId") or 0)
        called_rows = message_rows(db_path, called_sid)
        check("大模型回答同样落进会话", _MockHandler.hits == hits_before + 1
              and len(called_rows) == 2, str(called)[:200])
        check("回答正文与模型名如实入库",
              at(called_rows, 1).get("content") == MOCK_ANSWER and bool(at(called_rows, 1).get("model")),
              str(at(called_rows, 1))[:250])
        check("token 用量随消息入库",
              at(called_rows, 1).get("tokens_prompt") == PROMPT_TOKENS
              and at(called_rows, 1).get("tokens_completion") == COMPLETION_TOKENS,
              str(at(called_rows, 1))[:250])
        check("调模型的消息记录提示词编码",
              at(called_rows, 1).get("prompt_code") == "knowledge-assistant",
              str(at(called_rows, 1).get("prompt_code")))
        check("会话上的模型列被更新",
              bool(at(query(db_path, "SELECT model FROM biz_ai_session WHERE id = ?", (called_sid,)), 0)
                  .get("model")),
              str(called_sid))

        # ---------- ④b 多轮记忆：同会话前几轮以角色数组直传模型 ----------
        multi_first = ask(client, "帮我列一下回归测试的准入标准", "llm")
        multi_sid = int(multi_first.get("sessionId") or 0)
        _MockHandler.last_messages = []
        _ = ask(client, "我刚才问了哪些问题？", "llm", multi_sid)
        sent = [str(m.get("content") or "") for m in _MockHandler.last_messages]
        roles = [str(m.get("role") or "") for m in _MockHandler.last_messages]
        check("多轮记忆：第一条提问的原文进了模型请求",
              any("回归测试的准入标准" in s for s in sent),
              str(sent)[:400])
        check("多轮记忆：追问的原文也在最后一条 user 消息里",
              bool(sent) and "我刚才问了哪些问题" in sent[-1],
              str(sent[-1:]) if sent else "(no messages)")
        check("多轮记忆：历史以独立角色数组直传（不是拼成一段文本）",
              roles[-3:-1] == ["user", "assistant"]
              and "回归测试的准入标准" in sent[-3],
              str(list(zip(roles, sent)))[:400])
        # 单轮冷启动：无历史，只有 system + 当前问题
        _MockHandler.last_messages = []
        _ = ask(client, "单轮冷启动", "llm")
        first_roles = [str(m.get("role") or "") for m in _MockHandler.last_messages]
        check("单轮冷启动时只有 system 与当前提问（没有历史消息）",
              first_roles == ["system", "user"], str(first_roles))

        # ---------- ⑤ 会话校验：不存在 / 不是问答助手，都不该白烧额度 ----------
        hits_before = _MockHandler.hits
        status, resp = raw(client, "POST", f"{V1}/knowledge/ask",
                           {"question": "挂在不存在会话上", "mode": "llm", "sessionId": 999999})
        check("不存在的会话提问被拒绝", int(resp.get("code") or 0) == ErrorCode.RESOURCE_NOT_FOUND,
              f"status={status} body={str(resp)[:200]}")
        check("会话校验发生在调模型之前（不白烧额度）", _MockHandler.hits == hits_before,
              f"before={hits_before} after={_MockHandler.hits}")

        other_sid = int(at(query(
            db_path,
            "INSERT INTO biz_ai_session (title, module, created_at, updated_at, is_deleted)"
            " VALUES ('别的会话', 'lab', '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0)"
            " RETURNING id",
        ), 0).get("id") or 0)
        status, resp = raw(client, "GET", f"{V1}/knowledge/assistant/history?sessionId={other_sid}")
        check("别的模块的会话不能当问答历史回放（正控：校验不是放行任意会话）",
              int(resp.get("code") or 0) == ErrorCode.RESOURCE_NOT_FOUND, str(resp)[:200])
        status, resp = raw(client, "POST", f"{V1}/knowledge/ask",
                           {"question": "非法 sessionId 类型", "mode": "kb", "sessionId": "abc"})
        check("sessionId 类型非法被参数校验拦住",
              int(resp.get("code") or 0) == ErrorCode.PARAM_ERROR, str(resp)[:200])

        # ---------- ⑥ 留档时间闭环：回放后的「加入知识库」不会重复入池 ----------
        added = data_of(client, "POST", f"{V1}/knowledge/assistant/add", {
            "question": "重复推送的排查清单", "answer": MOCK_ANSWER, "mode": "llm",
            "model": called.get("model"), "answeredAt": called.get("answeredAt"),
            "refs": [], "reviewRequired": False,
        })
        check("按服务端的留档时间能入待审池", bool(added.get("id")), str(added)[:200])
        replayed = cast("list[dict[str, Any]]", history(client, called_sid).get("messages") or [])
        replay_item = at(replayed, 1)
        check("回放如实标出「已提交待审」（同一答不会被再入一次池）",
              replay_item.get("submitState") == "submitted"
              and replay_item.get("answeredAt") == called.get("answeredAt"), str(replay_item)[:300])
        again = data_of(client, "POST", f"{V1}/knowledge/assistant/add", {
            "question": "重复推送的排查清单", "answer": MOCK_ANSWER, "mode": "llm",
            "model": called.get("model"), "answeredAt": replay_item.get("answeredAt"),
            "refs": [], "reviewRequired": False,
        })
        check("用回放出来的留档时间再点一次判定为重复", again.get("duplicated") is True,
              str(again)[:200])
        check("回放的问答对里带着对应问题（加入知识库要用）",
              replay_item.get("question") == "重复推送的排查清单", str(replay_item)[:200])

        # ---------- ⑦ 上限截断：长期不清空也只回放最近这些 ----------
        conn = sqlite3.connect(db_path)
        try:
            conn.executemany(
                "INSERT INTO biz_ai_message (session_id, role, content, message_meta,"
                " created_at, updated_at, is_deleted) VALUES (?, 'user', ?, '{}',"
                " '2026-01-01T00:00:00Z', '2026-01-01T00:00:00Z', 0)",
                [(sid, f"填充问题 {i}") for i in range(ka.HISTORY_LIMIT)],
            )
            conn.commit()
        finally:
            conn.close()
        stuffed = history(client, sid)
        stuffed_messages = cast("list[dict[str, Any]]", stuffed.get("messages") or [])
        check("消息超过上限时按最近 HISTORY_LIMIT 条回放",
              len(stuffed_messages) == ka.HISTORY_LIMIT, str(len(stuffed_messages)))
        check("超上限时如实标出被截断", stuffed.get("truncated") is True, str(stuffed.get("truncated")))
        check("截断后留下的是最近的消息，最早的问答被挤出回放窗口",
              stuffed_messages[-1].get("content") == f"填充问题 {ka.HISTORY_LIMIT - 1}"
              and all(m.get("content") != "支付回调重复推送怎么排查？" for m in stuffed_messages),
              str(at(stuffed_messages, -1))[:150])

        # ---------- ⑧ 会话记录页能看到，且不混进项目视图 ----------
        listed = data_of(client, "GET",
                         f"{V1}/lab/sessions?module={ka.SESSION_MODULE}&pageSize=50")
        check("问答会话出现在「会话记录」列表里",
              int(listed.get("total") or 0) >= 3, str(listed)[:200])
        lab_listed = data_of(client, "GET", f"{V1}/lab/sessions?module=lab&pageSize=50")
        check("按其它能力筛选不会带出问答会话（正控：筛选真起作用）",
              int(lab_listed.get("total") or 0) == 0, str(lab_listed)[:200])

        # ---------- ⑨ 既有决策不变：不落 AI 执行记录 ----------
        records = query(db_path, "SELECT COUNT(*) AS n FROM biz_ai_record")
        check("问答历史不产生 AI 执行记录（不进成本与预算口径）",
              int(at(records, 0).get("n") or 0) == 0, str(records))
    finally:
        server.shutdown()
        server.server_close()

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
