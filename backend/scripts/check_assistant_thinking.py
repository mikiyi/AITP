"""问答助手思考过程（P0/P1/P2）验收门禁。

在临时库上验证：
- Usage.reasoning_tokens 进 as_dict（reasoningTokens 计费口径）
- ask-stream 为 SSE；无可用 AI 配置时以 error 事件收尾（不崩、不挂起）
- 思考默认不落库：record_qa 后 reasoning 为空
- opt-in save-thinking 才写 reasoning；history 回放带 thinkingText / id
- 未保存时 history 的 thinkingText 为空（前端据此提示「仅当次会话可见」）

    python backend/scripts/check_assistant_thinking.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码 1。
"""

from __future__ import annotations

import pathlib
import shutil
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.types import Usage  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services import knowledge_assistant_service as kas  # noqa: E402
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
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, payload: JsonDict | None = None) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON", "data": None}
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        _, body = self.call(method, path, payload)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} code={body.get('code')} msg={body.get('message')}")
        return body.get("data")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_thinking.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "check.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_thinking_"))
    try:
        app = build_app(workdir)
        api = Api(app)

        u = Usage(prompt_tokens=10, completion_tokens=20, total_tokens=30, reasoning_tokens=7)
        check("usage 带 reasoningTokens", u.as_dict().get("reasoningTokens") == 7, str(u.as_dict()))

        # 无 AI 配置：ask-stream 应以 SSE error 事件收尾，不崩不挂起
        resp = api.client.open("/api/v1/knowledge/assistant/ask-stream", method="POST",
                               json={"question": "你好", "mode": "llm"})
        check("ask-stream 返回 SSE", resp.mimetype == "text/event-stream", str(resp.mimetype))
        text = resp.get_data(as_text=True)
        check("无配置时以 error 事件收尾", "event: error" in text, text[:200])

        # 落库默认不带思考
        import sqlite3
        conn = sqlite3.connect(workdir / "check_thinking.db")
        conn.row_factory = sqlite3.Row
        sid = kas.ensure_session(conn, None, "问题一")
        stamp = kas.record_qa(conn, session_id=sid, question="问题一", mode="llm",
                              answer="回答一", refs=[], model="m", latency_ms=1)
        conn.commit()
        row = conn.execute(
            "SELECT reasoning FROM biz_ai_message WHERE session_id=? AND role='assistant'", (sid,)
        ).fetchone()
        check("思考默认不落库", row["reasoning"] is None, str(row["reasoning"]))

        # opt-in 保存思考
        saved = api.data("POST", "/api/v1/knowledge/assistant/save-thinking",
                         {"sessionId": sid, "answeredAt": stamp, "text": "思考内容"})
        check("save-thinking 返回 saved", saved.get("saved") is True, str(saved))
        hist = api.data("GET", f"/api/v1/knowledge/assistant/history?sessionId={sid}")
        msgs = [m for m in hist["messages"] if m["role"] == "assistant"]
        check("history 回放带已保存思考", bool(msgs) and msgs[-1].get("thinkingText") == "思考内容",
              str(msgs[-1].get("thinkingText")) if msgs else "no msg")
        check("history 消息带 id", bool(msgs) and msgs[-1].get("id") is not None, str(msgs[-1].get("id")) if msgs else "")

        # 另一条未保存的思考应为空
        stamp2 = kas.record_qa(conn, session_id=sid, question="问题二", mode="llm",
                               answer="回答二", refs=[], model="m", latency_ms=1)
        conn.commit()
        hist2 = api.data("GET", f"/api/v1/knowledge/assistant/history?sessionId={sid}")
        last = [m for m in hist2["messages"] if m["role"] == "assistant"][-1]
        check("未保存思考 history 为空", last.get("thinkingText") == "", str(last.get("thinkingText")))
        conn.close()
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
