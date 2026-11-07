"""GAP-11 AI 外发脱敏验收脚本。

验证云端调用前的统一脱敏层：
- 手机号 / 身份证 / 邮箱 / 银行卡 / API Key / 账号口令的识别、替换与计数
- 系统提示词与用户输入都经过同一层，图片原样保留，原始消息不被修改
- 云端默认脱敏、本地模型不脱敏、设置关闭后不脱敏
- 同步 / 流式调用都会把脱敏结果回传，并写入 AI 执行记录（只留类型与数量）
- 站内检查接口只回类型与数量，不回显原文

    python backend/scripts/check_ai_masking.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import json
import os
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.ai.client import ADAPTERS, chat_with_fallback, stream_with_fallback  # noqa: E402
from aitp.ai.masking import (  # noqa: E402
    MASK_CONFIG_KEY,
    MaskInfo,
    cloud_mask_enabled,
    mask_messages,
    preview_mask,
)
from aitp.ai.types import (  # noqa: E402
    ChatResult,
    Message,
    ResolvedAIConfig,
    StreamEvent,
    Usage,
)
from aitp.app import create_app  # noqa: E402
from aitp.common.pii import (  # noqa: E402
    TYPE_LABELS,
    detect_sensitive_counts,
    mask_sensitive,
    scan_sensitive,
)
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []

PHONE = "13800138000"
PHONE2 = "13900139000"
ID_CARD = "110101199003071234"
EMAIL = "qa@example.com"
BANK_CARD = "6222021234567890123"
API_KEY = "sk-abcdefgh12345678"
PASSWORD = "password=Passw0rd!"

SAMPLE_TEXT = (
    f"联系手机 {PHONE}，备用 {PHONE2}，身份证 {ID_CARD}，邮箱 {EMAIL}，"
    f"卡号 {BANK_CARD}，密钥 {API_KEY}，口令 {PASSWORD}"
)


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
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")


class SpyAdapter:
    """记录真正发给适配器的消息，用于确认脱敏发生在出站之前。"""

    protocol = "openai_compat"

    def __init__(self, reply: str = "已确认需求") -> None:
        self.sent: list[list[Message]] = []
        self.reply = reply

    def chat(self, config: ResolvedAIConfig, messages: list[Message], **opts: Any) -> ChatResult:
        self.sent.append(list(messages))
        return ChatResult(content=self.reply, model=config.model, latency_ms=1)

    def chat_stream(self, config: ResolvedAIConfig, messages: list[Message],
                    **opts: Any) -> Any:
        self.sent.append(list(messages))
        yield StreamEvent(type="delta", content=self.reply)
        yield StreamEvent(
            type="done",
            usage=Usage(prompt_tokens=1, completion_tokens=1, total_tokens=2),
            model=config.model,
        )

    def list_models(self, config: ResolvedAIConfig) -> list[Any]:
        return []


def cloud_config() -> ResolvedAIConfig:
    return ResolvedAIConfig(
        provider="custom",
        protocol="openai_compat",
        base_url="http://127.0.0.1:9/v1",
        model="cloud-mask-check",
        api_key="sk-test",
        is_local=False,
    )


def local_config() -> ResolvedAIConfig:
    return ResolvedAIConfig(
        provider="ollama",
        protocol="openai_compat",
        base_url="http://127.0.0.1:11434",
        model="local-mask-check",
        is_local=True,
    )


def message_texts(msgs: list[Message]) -> str:
    return "\n".join(item.content for item in msgs)


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_mask.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_mask.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def sse_frames(resp: TestResponse) -> list[JsonDict]:
    text = resp.get_data(as_text=True)
    frames: list[JsonDict] = []
    for line in text.splitlines():
        if line.startswith("data:"):
            payload = line[5:].strip()
            if payload:
                frames.append(cast(JsonDict, json.loads(payload)))
    return frames


# ---------------- 识别与替换 ----------------

def check_pii() -> None:
    masked, counts = scan_sensitive(SAMPLE_TEXT)
    check("识别手机号数量", counts.get("phone") == 2, str(counts))
    check("识别身份证数量", counts.get("id_card") == 1, str(counts))
    check("识别邮箱数量", counts.get("email") == 1, str(counts))
    check("识别银行卡数量", counts.get("bank_card") == 1, str(counts))
    check("识别 API Key 数量", counts.get("api_key") == 1, str(counts))
    check("识别账号口令数量", counts.get("password") == 1, str(counts))
    check("命中类型都有中文名",
          all(code in TYPE_LABELS for code in counts), str(list(counts)))
    for secret in (PHONE, PHONE2, ID_CARD, EMAIL, BANK_CARD, API_KEY):
        check(f"脱敏后不含原文 {secret[:6]}…", secret not in masked)
    check("脱敏后仍保留可读上下文", "联系手机" in masked and "口令" in masked)
    check("占位文本可辨认", "[手机号已脱敏]" in masked and "[邮箱已脱敏]" in masked)
    check("脱敏不影响原文（纯函数）", PHONE in SAMPLE_TEXT)
    check("mask_sensitive 与 scan_sensitive 结果一致", mask_sensitive(SAMPLE_TEXT) == masked)
    check("计数接口与扫描一致", detect_sensitive_counts(SAMPLE_TEXT) == counts)
    check("空文本不产生命中", scan_sensitive("") == ("", {}))
    check("普通文本不误伤", detect_sensitive_counts("登录接口返回 200") == {})


def check_messages() -> None:
    system = Message(role="system", content=f"你是测试助手，操作人手机 {PHONE}")
    user = Message(role="user", content=f"给 {EMAIL} 发验证码", images=["data:image/png;base64,AAA"])
    masked, info = mask_messages([system, user])
    check("系统提示词也经过脱敏", PHONE not in masked[0].content and "[手机号已脱敏]" in masked[0].content)
    check("用户输入经过脱敏", EMAIL not in masked[1].content and "[邮箱已脱敏]" in masked[1].content)
    check("多模态图片原样保留", masked[1].images == ["data:image/png;base64,AAA"])
    check("原始消息不被改写", PHONE in system.content and EMAIL in user.content and system.images == [])
    check("脱敏结果标记已生效", info.applied and info.enabled and info.count == 2)
    check("类型计数按类型汇总",
          {item["code"]: item["count"] for item in info.types} == {"phone": 1, "email": 1},
          str(info.types))

    _, clean = mask_messages([Message(role="user", content="登录接口返回 200")])
    check("无敏感内容时不标记已脱敏", clean.applied is False and clean.count == 0)

    preview = preview_mask(SAMPLE_TEXT)
    check("预览只统计不替换", preview.count >= 6 and preview.applied is False, str(preview.as_dict()))
    check("预览包含中文类型名",
          {item["label"] for item in preview.types} >= {"手机号", "身份证号", "邮箱"},
          str(preview.types))


# ---------------- 调用链 ----------------

def check_client(workdir: pathlib.Path) -> None:
    original = ADAPTERS.get("openai_compat")
    spy = SpyAdapter()
    ADAPTERS["openai_compat"] = spy  # type: ignore[assignment]
    try:
        messages = [
            Message(role="system", content="你是测试助手"),
            Message(role="user", content=f"手机 {PHONE}，邮箱 {EMAIL}"),
        ]
        outcome = chat_with_fallback([cloud_config()], messages)
        sent = message_texts(spy.sent[-1])
        check("云同步调用出站内容已脱敏",
              PHONE not in sent and EMAIL not in sent and "[手机号已脱敏]" in sent, sent[:200])
        check("云同步调用回传脱敏结果",
              outcome.mask.applied and outcome.mask.count == 2
              and {item["code"] for item in outcome.mask.types} == {"phone", "email"},
              str(outcome.mask.as_dict()))
        check("调用链不改写调用方消息", PHONE in messages[1].content)

        spy.sent.clear()
        local_outcome = chat_with_fallback([local_config()], messages)
        sent = message_texts(spy.sent[-1])
        check("本地模型不脱敏", PHONE in sent and EMAIL in sent)
        check("本地模型脱敏结果标记未生效", local_outcome.mask.applied is False)

        spy.sent.clear()
        off = chat_with_fallback([cloud_config()], messages, mask_enabled=False)
        sent = message_texts(spy.sent[-1])
        check("关闭开关后云端也原样发送", PHONE in sent and off.mask.enabled is False and off.mask.applied is False)

        spy.sent.clear()
        events = list(stream_with_fallback([cloud_config()], messages))
        sent = message_texts(spy.sent[-1])
        types = [event.type for event in events]
        check("云流式调用出站内容已脱敏", PHONE not in sent and "[手机号已脱敏]" in sent, sent[:200])
        check("流式先发 mask 再出汗", types[:2] == ["mask", "delta"], str(types))
        mask_event = events[0].extra
        check("流式 mask 事件带类型与数量",
              bool(mask_event.get("applied")) and int(mask_event.get("count") or 0) == 2,
              str(mask_event))

        spy.sent.clear()
        local_events = list(stream_with_fallback([local_config()], messages))
        check("本地流式不发 mask 事件",
              "mask" not in [event.type for event in local_events]
              and PHONE in message_texts(spy.sent[-1]))
    finally:
        if original is not None:
            ADAPTERS["openai_compat"] = original


def check_switch(api: Api, conn: sqlite3.Connection) -> None:
    configs = api.data("GET", "/api/v1/settings/config")
    item = next((row for row in configs if row.get("key") == MASK_CONFIG_KEY), None)
    check("设置里有外发脱敏开关", item is not None, str(configs)[:200])
    check("外发脱敏默认开启", str((item or {}).get("value")).lower() == "true", str(item))
    check("后端默认策略为开启", cloud_mask_enabled(conn) is True)

    api.data("PUT", "/api/v1/settings/config", {
        "items": [{"key": MASK_CONFIG_KEY, "value": "false", "valueType": "bool"}],
    })
    check("关闭后后端策略同步为关闭", cloud_mask_enabled(conn) is False)
    disabled = preview_mask(f"手机 {PHONE}", enabled=cloud_mask_enabled(conn))
    check("关闭后预览仍统计但标记未启用",
          disabled.count == 1 and disabled.enabled is False, str(disabled.as_dict()))

    api.data("PUT", "/api/v1/settings/config", {
        "items": [{"key": MASK_CONFIG_KEY, "value": "true", "valueType": "bool"}],
    })
    check("重新开启后策略恢复", cloud_mask_enabled(conn) is True)


def check_preview_api(api: Api) -> None:
    text = f"联系人 {PHONE}、{PHONE2}，邮箱 {EMAIL}"
    resp = api.client.post("/api/v1/ai/mask/preview", json={"text": text})
    check("脱敏预览接口返回 200", resp.status_code == 200, str(resp.status_code))
    raw = resp.get_data(as_text=True)
    data = cast(JsonDict, (resp.get_json() or {}).get("data") or {})
    check("预览接口统计命中数量", int(data.get("count") or 0) == 3, str(data))
    check("预览接口返回中文类型名",
          {item.get("label") for item in data.get("types") or []} >= {"手机号", "邮箱"},
          str(data.get("types")))
    check("预览接口不回显原文", PHONE not in raw and EMAIL not in raw)


def check_run_api(api: Api, db_path: pathlib.Path, spy: SpyAdapter) -> None:
    spy.sent.clear()
    variables = {
        "requirement": f"手机号 {PHONE} 加验证码登录，异常时联系 {EMAIL}",
        "projectName": "脱敏门禁项目",
    }
    data = api.data("POST", "/api/v1/lab/requirement_confirm/run", {"variables": variables})
    sent = message_texts(spy.sent[-1])
    check("执行接口出站内容已脱敏",
          PHONE not in sent and EMAIL not in sent and "[手机号已脱敏]" in sent, sent[:200])
    mask = data.get("mask") or {}
    check("执行接口返回脱敏结果",
          bool(mask.get("applied")) and int(mask.get("count") or 0) == 2, str(mask))

    record_id = int(data.get("recordId") or 0)
    check("执行结果落库了记录 id", record_id > 0, str(data)[:200])
    detail = api.data("GET", f"/api/v1/ai-records/{record_id}")
    record_mask = detail.get("mask") or {}
    check("AI 记录展示脱敏是否生效",
          bool(record_mask.get("applied")) and int(record_mask.get("count") or 0) == 2,
          str(record_mask))
    check("AI 记录只存类型不存原文",
          PHONE not in json.dumps(record_mask, ensure_ascii=False)
          and EMAIL not in json.dumps(record_mask, ensure_ascii=False),
          str(record_mask))

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            "SELECT mask_json FROM biz_ai_record WHERE id = ?", (record_id,)
        ).fetchone()
    finally:
        conn.close()
    mask_json = str(row["mask_json"] if row else "")
    check("mask_json 落库且不含原文", bool(mask_json) and PHONE not in mask_json and EMAIL not in mask_json,
          mask_json[:200])
    check("mask_json 记录手机号与邮箱类型",
          "phone" in mask_json and "email" in mask_json, mask_json[:200])

    # 流式：start → mask → delta → done，mask 帧与 done 帧都带脱敏结果
    spy.sent.clear()
    resp = api.client.post("/api/v1/lab/requirement_confirm/stream", json={"variables": variables})
    frames = sse_frames(resp)
    types = [str(frame.get("type")) for frame in frames]
    check("流式帧包含 mask 类型", "mask" in types, str(types))
    if "mask" in types:
        check("mask 帧在 delta 之前", types.index("mask") < types.index("delta"), str(types))
        mask_frame = frames[types.index("mask")]
        check("mask 帧带类型与数量",
              bool(mask_frame.get("applied")) and int(mask_frame.get("count") or 0) == 2,
              str(mask_frame))
    done = frames[-1] if frames else {}
    check("done 帧带脱敏结果",
          isinstance(done.get("mask"), dict) and bool(cast(JsonDict, done["mask"]).get("applied")),
          str(done)[:200])
    sent = message_texts(spy.sent[-1])
    check("流式出站内容已脱敏",
          PHONE not in sent and EMAIL not in sent and "[手机号已脱敏]" in sent, sent[:200])


def check_off_local(api: Api, db_path: pathlib.Path) -> None:
    """本地模型不出本机：即使开关打开也不脱敏。"""
    original = ADAPTERS.get("openai_compat")
    spy = SpyAdapter(reply="本地模型回答")
    ADAPTERS["openai_compat"] = spy  # type: ignore[assignment]
    try:
        profile = api.data("POST", "/api/v1/settings/ai/profiles", {
            "name": "脱敏门禁本地模型",
            "provider": "ollama",
            "protocol": "openai_compat",
            "baseUrl": "http://127.0.0.1:11434",
            "model": "local-mask",
            "isLocal": True,
            "isDefault": True,
            "isActive": True,
            "priority": 200,
        })
        data = api.data("POST", "/api/v1/lab/requirement_confirm/run", {
            "variables": {"requirement": f"手机号 {PHONE} 登录", "projectName": "本地模型"},
            "profileId": profile["id"],
        })
        sent = message_texts(spy.sent[-1])
        check("本地模型收到的是原文", PHONE in sent, sent[:200])
        mask = data.get("mask") or {}
        check("本地模型脱敏结果未生效", mask.get("applied") is False, str(mask))
    finally:
        if original is not None:
            ADAPTERS["openai_compat"] = original


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_mask_"))
    original_key = os.environ.get("AITP_AI_API_KEY")
    os.environ["AITP_AI_API_KEY"] = "sk-test"
    try:
        check_pii()
        check_client(workdir)

        app = build_app(workdir)
        api = Api(app)
        db_path = workdir / "check_mask.db"

        api.data("POST", "/api/v1/settings/ai/profiles", {
            "name": "脱敏门禁云模型",
            "provider": "custom",
            "protocol": "openai_compat",
            "baseUrl": "http://127.0.0.1:9/v1",
            "model": "cloud-mask-check",
            "isDefault": True,
            "isActive": True,
            "priority": 100,
        })

        spy = SpyAdapter()
        original_adapter = ADAPTERS.get("openai_compat")
        ADAPTERS["openai_compat"] = spy  # type: ignore[assignment]
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        try:
            check_switch(api, conn)
            check_preview_api(api)
            check_run_api(api, db_path, spy)
            check_off_local(api, db_path)
        finally:
            conn.close()
            if original_adapter is not None:
                ADAPTERS["openai_compat"] = original_adapter
    finally:
        if original_key is None:
            os.environ.pop("AITP_AI_API_KEY", None)
        else:
            os.environ["AITP_AI_API_KEY"] = original_key
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\nPASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
