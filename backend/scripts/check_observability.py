"""可观测性门禁（GAP-23，二期第 C1 批）。

    python backend/scripts/check_observability.py

跑临时数据库 + test_client，不依赖已启动的服务，绝不读写正式库。
断言的是「出了问题能不能查到」：
  1) 业务日志一行内有 traceId 列，且响应头 X-Trace-Id 能在日志文件里 grep 到同一行；
  2) 只记非 2xx / 业务失败 / 慢请求，成功的快请求不刷日志；
  3) 明文密钥（含结构化字段里的）一律打码，且打码不依赖新的列格式；
  4) 无请求上下文时 traceId 列填占位符，不抛错；
  5) health 的 aiConfigured 是真实判定：无档案 false、启用档案但取不到密钥 false、
     密钥可用 true、本地档案 true；同时报出档案数、待审知识、队列积压与数据库耗时；
  6) 前端统一请求层把 X-Trace-Id 透出到错误文案（源码级断言）。
"""

from __future__ import annotations

import logging
import os
import pathlib
import re
import shutil
import sys
import tempfile
import time
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp import observability  # noqa: E402
from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.envelope import ok  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.logging_setup import NO_TRACE, log_event  # noqa: E402
from flask import Flask, Response  # noqa: E402

JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []

#: 门禁要证明「无档案即 false」，环境变量兜底会污染这条判定，跑之前先摘干净
_ENV_KEYS = (
    "AITP_AI_PROVIDER",
    "AITP_AI_API_KEY",
    "AITP_AI_BASE_URL",
    "AITP_AI_MODEL",
    "OPENAI_API_KEY",
    "DASHSCOPE_API_KEY",
    "DEEPSEEK_API_KEY",
    "SENSENOVA_API_KEY",
    "OLLAMA_HOST",
)

#: 一条业务日志：时间 | 级别 | 模块 | traceId | 消息（关键参数跟在消息里）
LINE_PATTERN = re.compile(
    r"^(?P<ts>\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2},\d{3})"
    r" \| (?P<level>[A-Z]+)[ \t]*"
    r" \| (?P<module>\S+)"
    r" \| (?P<trace>\S+)"
    r" \| (?P<body>.*)$"
)

FAKE_SECRET = "sk-OBSERVABILITYGATE1234567890"


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        _failures.append(name)
        print(f"[FAIL] {name}  {detail}")


# ---------------- 脚手架 ----------------

def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "observability.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8768,
        debug=False,
        db_path=db_path,
        log_path=workdir / "observability.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def read_log(log_path: pathlib.Path) -> str:
    """刷干 handler 再读日志文件；文件不存在时返回空串。"""
    for handler in logging.getLogger().handlers:
        try:
            handler.flush()
        except Exception:  # noqa: BLE001 - 读日志不该被 handler 状态带崩
            pass
    if not log_path.exists():
        return ""
    return log_path.read_text(encoding="utf-8", errors="replace")


def log_lines(text: str) -> list[str]:
    return [line for line in text.splitlines() if line.strip()]


def data_of(client: Any, method: str, path: str, payload: Any = None) -> JsonDict:
    resp = client.open(path, method=method, json=payload)
    body = resp.get_json(silent=True)
    if not isinstance(body, dict):
        raise AssertionError(f"{method} {path} 期望 JSON 响应")
    if body.get("code") != ErrorCode.SUCCESS:
        raise AssertionError(f"{method} {path} 失败：{body.get('code')} {body.get('message')}")
    data = body.get("data")
    return cast(JsonDict, data) if isinstance(data, dict) else {}


def call(client: Any, method: str, path: str, payload: Any = None) -> JsonDict:
    resp = client.open(path, method=method, json=payload)
    body = resp.get_json(silent=True)
    return cast(JsonDict, body) if isinstance(body, dict) else {}


# ---------------- 断言 1/2/3/4：日志里的 traceId ----------------

def check_trace_in_log(client: Any, log_path: pathlib.Path) -> None:
    resp = client.get("/api/v1/projects/999999")
    header_trace = str(resp.headers.get("X-Trace-Id") or "")
    check("报错响应带 X-Trace-Id 头", bool(header_trace), str(dict(resp.headers))[:200])

    body = resp.get_json(silent=True)
    check("业务失败仍是 HTTP 200 的包封", resp.status_code == 200, str(resp.status_code))
    check("包封业务码是资源不存在",
          isinstance(body, dict) and body.get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(body)[:160])

    text = read_log(log_path)
    matched = [line for line in log_lines(text) if header_trace in line]
    check("响应头的 traceId 能在业务日志里 grep 到", bool(matched),
          f"trace={header_trace} 日志行数={len(log_lines(text))}")
    if not matched:
        return
    line = matched[-1]
    parsed = LINE_PATTERN.match(line)
    check("业务日志是「时间|级别|模块|traceId|消息」的一行格式", parsed is not None, line[:200])
    if parsed is not None:
        check("traceId 列取的就是这次请求的追踪号", parsed.group("trace") == header_trace, line[:200])
        check("失败请求记为 WARNING 级", parsed.group("level") == "WARNING", line[:200])
    check("失败请求记了 api_error 事件", "api_error" in line, line[:200])
    check("关键参数含 path/method/status/耗时",
          all(token in line for token in ("path=", "method=", "status=", "ms=")), line[:240])
    check("包封里的 traceId 与响应头一致",
          isinstance(body, dict) and str(body.get("traceId") or "") == header_trace, str(body)[:160])


def check_quiet_success(client: Any, log_path: pathlib.Path) -> None:
    client.get("/api/v1/health")  # 先热身，避免首次调用的冷启动耗时被算成慢请求
    before = set(log_lines(read_log(log_path)))
    for _ in range(5):
        client.get("/api/v1/health")
    added = [line for line in log_lines(read_log(log_path)) if line not in before]
    noisy = [line for line in added if "api_error" in line or "api_slow" in line]
    check("成功的快请求不写访问日志（防刷量）", not noisy, str(noisy[:2])[:300])


def check_slow_request(client: Any, log_path: pathlib.Path) -> None:
    """把阈值临时压低再真实跑一次慢请求，验证 api_slow 分支而不是只读常量。"""
    original = observability.SLOW_REQUEST_MS
    check("慢请求阈值是可调常量且默认 2000ms", original == 2000, str(original))
    observability.SLOW_REQUEST_MS = 30
    try:
        resp = client.get("/api/v1/__obs_slow")
        trace = str(resp.headers.get("X-Trace-Id") or "")
        text = read_log(log_path)
        slow_lines = [line for line in log_lines(text) if "api_slow" in line and trace in line]
        check("超过阈值的成功请求记为 api_slow", bool(slow_lines), f"trace={trace}")
        if slow_lines:
            check("api_slow 带耗时毫秒", "ms=" in slow_lines[-1], slow_lines[-1][:200])
    finally:
        observability.SLOW_REQUEST_MS = original


def check_slow_route(app: Flask) -> None:
    """门禁自带的慢接口：只用于验证观测钩子，不碰业务代码。"""

    def _slow() -> Response:
        time.sleep(0.06)
        return ok({"slow": True})

    app.add_url_rule("/api/v1/__obs_slow", "obs_slow", _slow)
    check("慢请求测试路由已挂上", "/api/v1/__obs_slow" in {
        str(rule.rule) for rule in app.url_map.iter_rules()
    }, "")


def check_redaction(log_path: pathlib.Path) -> None:
    plain_logger = logging.getLogger("aitp.gate.redact")
    plain_logger.warning("探针：读到密钥 %s，请打码", FAKE_SECRET)
    log_event(plain_logger, "gate_probe", token=FAKE_SECRET, path="/api/v1/x")
    text = read_log(log_path)
    check("明文密钥不落日志", FAKE_SECRET not in text, "")
    check("日志里出现打码后的 sk-***", "sk-***" in text, "")
    masked = [line for line in log_lines(text) if "sk-***" in line]
    check("结构化字段里的密钥同样被打码",
          any("gate_probe" in line and "sk-***" in line for line in masked), str(masked[-1:])[:200])


def check_no_context(log_path: pathlib.Path, deploy_path: pathlib.Path) -> None:
    """无请求上下文（启动、脚本、后台线程）时 traceId 列填占位符，绝不抛错。"""
    before = set(log_lines(read_log(log_path)))
    logging.getLogger("aitp.gate.nocontext").info("上下文外的一行")
    added = [line for line in log_lines(read_log(log_path)) if line not in before]
    placeholder = [line for line in added if f"| {NO_TRACE} |" in line]
    check("无请求上下文时 traceId 列为占位符", bool(placeholder), str(added[:2])[:200])
    check("普通消息不因新增列而丢字段",
          any("上下文外的一行" in line for line in placeholder), str(added[:2])[:200])

    deploy_lines = log_lines(deploy_path.read_text(encoding="utf-8", errors="replace"))
    with_trace_column = [
        line for line in deploy_lines if f" | DEPLOY | {NO_TRACE} | " in line
    ]
    check("部署日志同样带 traceId 列", bool(with_trace_column), str(deploy_lines[:1])[:200])


# ---------------- 断言 5：health 真实化 ----------------

def health_of(client: Any) -> JsonDict:
    return data_of(client, "GET", "/api/v1/health")


def check_health(client: Any) -> None:
    fresh = health_of(client)
    check("无档案时 aiConfigured 为 false", fresh.get("aiConfigured") is False, str(fresh)[:200])
    # 环境变量能拼出一个可用兜底档案，但用户没配过档案 → 健康检查仍报未配置（判定口径以档案为准）
    os.environ["AITP_AI_PROVIDER"] = "custom"
    os.environ["AITP_AI_API_KEY"] = "sk-ENVFALLBACK1234567890"
    os.environ["AITP_AI_BASE_URL"] = "http://127.0.0.1:9/v1"
    os.environ["AITP_AI_MODEL"] = "env-model"
    try:
        env_only = health_of(client)
        check("仅环境变量兜底（无档案）仍为 false", env_only.get("aiConfigured") is False,
              str(env_only)[:200])
        check("环境变量兜底不计入可用候选", env_only.get("aiCandidateCount") == 0,
              str(env_only.get("aiCandidateCount")))
    finally:
        for key in ("AITP_AI_PROVIDER", "AITP_AI_API_KEY", "AITP_AI_BASE_URL", "AITP_AI_MODEL"):
            os.environ.pop(key, None)
    check("health 报出启用档案数", fresh.get("aiProfileCount") == 0, str(fresh.get("aiProfileCount")))
    check("health 向后兼容保留原字段",
          all(key in fresh for key in ("status", "appVersion", "aiConfigured", "serverTime",
                                       "database")), str(sorted(fresh.keys())))
    database = cast(JsonDict, fresh.get("database") or {})
    check("health 报数据库耗时（database.latencyMs）",
          isinstance(database.get("latencyMs"), (int, float)), str(database)[:160])
    check("health 报待审知识积压", isinstance(fresh.get("knowledgePendingDrafts"), int),
          str(fresh.get("knowledgePendingDrafts")))
    queue = cast(JsonDict, fresh.get("taskQueue") or {})
    check("health 报队列积压（排队/执行中）",
          isinstance(queue.get("queued"), int) and isinstance(queue.get("running"), int),
          str(queue))

    created = data_of(client, "POST", "/api/v1/settings/ai/profiles", {
        "name": "可观测性-云端无密钥",
        "provider": "custom",
        "protocol": "openai_compat",
        "baseUrl": "http://127.0.0.1:9/v1",
        "model": "gate-model",
        "isActive": True,
    })
    profile_id = int(created.get("id") or 0)
    check("档案创建接口可用", profile_id > 0, str(created)[:160])
    no_key = health_of(client)
    check("启用档案但取不到密钥仍为 false", no_key.get("aiConfigured") is False, str(no_key)[:200])
    check("档案数计入 aiProfileCount", no_key.get("aiProfileCount") == 1,
          str(no_key.get("aiProfileCount")))

    data_of(client, "POST", f"/api/v1/settings/ai/profiles/{profile_id}/api-key",
             {"apiKey": FAKE_SECRET})
    with_key = health_of(client)
    check("启用档案且密钥可用时 aiConfigured 为 true",
          with_key.get("aiConfigured") is True, str(with_key)[:200])
    check("可用候选数同步为 1", with_key.get("aiCandidateCount") == 1,
          str(with_key.get("aiCandidateCount")))

    data_of(client, "PUT", f"/api/v1/settings/ai/profiles/{profile_id}", {"isActive": False})
    off = health_of(client)
    check("档案停用后 aiConfigured 回到 false", off.get("aiConfigured") is False, str(off)[:200])

    data_of(client, "POST", "/api/v1/settings/ai/profiles", {
        "name": "可观测性-本地模型",
        "provider": "custom",
        "protocol": "openai_compat",
        "baseUrl": "http://127.0.0.1:11434/v1",
        "model": "gate-local",
        "isLocal": True,
        "isActive": True,
    })
    local = health_of(client)
    check("本地档案无需密钥即算已配置", local.get("aiConfigured") is True, str(local)[:200])

    # 队列积压项要能被真实观测到：投一个不会执行的任务再读健康检查
    conn = _open_db(client)
    if conn is not None:
        try:
            conn.execute(
                "INSERT INTO sys_task (kind, title, status, priority, created_at, updated_at)"
                " VALUES ('gate', '可观测性-排队占位', 'queued', 5,"
                " strftime('%Y-%m-%dT%H:%M:%SZ','now'), strftime('%Y-%m-%dT%H:%M:%SZ','now'))"
            )
            conn.commit()
            queued = health_of(client)
            queue_after = cast(JsonDict, queued.get("taskQueue") or {})
            check("排队的任务会计入队列积压", int(queue_after.get("queued") or 0) >= 1,
                  str(queue_after))
        finally:
            conn.close()


def _open_db(client: Any) -> Any:
    """用应用配置里的库路径开一条独立连接，只为插一条排队任务。"""
    app = cast(Flask, client.application)
    db_path = str(app.config.get("AITP_DB_PATH", ""))
    if not db_path:
        return None
    import sqlite3

    return sqlite3.connect(db_path, timeout=5.0)


# ---------------- 断言 6：前端透出 traceId ----------------

def check_frontend() -> None:
    api_dir = ROOT / "frontend" / "src" / "api"
    request_text = (api_dir / "request.ts").read_text(encoding="utf-8")
    index_text = (api_dir / "index.ts").read_text(encoding="utf-8")
    types_text = (api_dir / "types.ts").read_text(encoding="utf-8")

    check("前端请求层读取 X-Trace-Id 响应头", 'X-Trace-Id' in request_text, "")
    check("前端把追踪号拼进错误文案", "withTrace(" in request_text and "追踪号" in request_text, "")
    check("前端 API 入口透出追踪号常量", "TRACE_ID_HEADER" in index_text, "")
    check("HealthStatus 类型补了新健康项",
          all(key in types_text for key in ("aiProfileCount", "aiCandidateCount",
                                            "knowledgePendingDrafts", "taskQueue")), "")


# ---------------- 入口 ----------------

def main() -> int:
    stashed = {key: os.environ.pop(key) for key in _ENV_KEYS if key in os.environ}
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-observability-"))
    try:
        app = build_app(workdir)
        log_path = workdir / "observability.log"
        client = app.test_client()

        check_slow_route(app)
        check_trace_in_log(client, log_path)
        check_quiet_success(client, log_path)
        check_slow_request(client, log_path)
        check_no_context(log_path, workdir / "deploy.log")
        check_health(client)
        # 打码检查放在最后：扫的是包括档案建密钥在内的全部落盘内容
        check_redaction(log_path)
        check_frontend()
    finally:
        os.environ.update(stashed)
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 60)
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：" + "、".join(_failures))
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
