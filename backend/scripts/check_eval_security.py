"""Eval 安全与合规门禁（设计 §八 check_eval_security，6 项断言）。

① 评测输入外发走脱敏：判定/落库前的样本统一过公共 PII 规则；
② 样本中的密钥/PII 不落库、不打日志：跑完扫描整库文件与执行期日志；
③ 结果保留期策略生效：默认 90 天归档，清正文留判定与指标，钉住跑批豁免；
④ /evals 全路由在 v1 前缀且鉴权一致：访问口令开启后匿名一律被拒；
⑤ 越权访问他项目 suite 被拒：跨项目配置/判定器/用例挂载全部拒绝；
⑥ 导出无越权：holdout 导出拒绝 160107，普通导出不含明文 PII。

全程临时库 / 临时目录，零 token，不碰正式服务与正式库。
用法：python backend/scripts/check_eval_security.py
"""

from __future__ import annotations

import json
import logging
import pathlib
import sqlite3
import sys
import tempfile
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from flask import Flask

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp import middleware  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import AppError, ErrorCode  # noqa: E402
from aitp.repositories.eval_suite_repo import EvalSuiteRepository  # noqa: E402
from aitp.services import eval_runner, eval_service, retention_service  # noqa: E402

RESULTS: list[tuple[str, bool, str]] = []

PII_SAMPLES: dict[str, str] = {
    "api_key": "sk-securitycheck-abcdef123456",
    "aws_key": "AKIAABCDEFGHIJKLMNOP",
    "phone": "13800138000",
    "id_card": "110101199003071234",
    "email": "alice@example.com",
    "bank_card": "6222021234567890123",
}
SECRET_LINE = "答案如下：" + " ".join(PII_SAMPLES.values())


def check(name: str, func: Callable[[], None]) -> None:
    try:
        func()
    except Exception as exc:  # noqa: BLE001 - 门禁脚本需要把任何异常都记成失败
        RESULTS.append((name, False, f"{type(exc).__name__}: {exc}"))
    else:
        RESULTS.append((name, True, ""))


class _ListHandler(logging.Handler):
    def __init__(self, bucket: list[str]) -> None:
        super().__init__(level=logging.DEBUG)
        self.bucket = bucket

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.bucket.append(record.getMessage())
        except Exception:  # noqa: BLE001 - 日志格式化失败不影响被测流程
            pass


# ---------------------------------------------------------------- 夹具


def _new_conn() -> sqlite3.Connection:
    directory = pathlib.Path(tempfile.mkdtemp(prefix="aitp_eval_security_"))
    db_path = directory / "eval.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    now = utcnow_iso()
    conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        ("EVAL-SEC", "评测安全自检项目", now, now),
    )
    conn.commit()
    return conn


def _db_path(conn: sqlite3.Connection) -> str:
    return str(conn.execute("PRAGMA database_list").fetchone()[2])


def _project_id(conn: sqlite3.Connection) -> int:
    return int(conn.execute("SELECT id FROM biz_project LIMIT 1").fetchone()["id"])


def _new_project(conn: sqlite3.Connection, code: str, name: str) -> int:
    now = utcnow_iso()
    cursor = conn.execute(
        "INSERT INTO biz_project (code, name, created_at, updated_at) VALUES (?, ?, ?, ?)",
        (code, name, now, now),
    )
    conn.commit()
    assert cursor.lastrowid is not None
    return int(cursor.lastrowid)


def _suite(conn: sqlite3.Connection, project_id: int, name: str, visibility: str = "private") -> int:
    return eval_service.create_suite(
        conn,
        {"projectId": project_id, "name": name, "visibility": visibility, "targetType": "llm"},
    )


def _case(conn: sqlite3.Connection, project_id: int, suite_id: int, title: str) -> int:
    return eval_service.add_case(
        conn,
        {
            "suiteId": suite_id,
            "projectId": project_id,
            "title": title,
            "severity": "S1",
            "assertions": {"max_chars": 4000},
        },
    )


def _outputs(conn: sqlite3.Connection, suite_id: int, output: str) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        "SELECT case_no FROM biz_eval_case WHERE suite_id = ? AND is_deleted = 0", (suite_id,)
    ).fetchall()
    return {str(row["case_no"]): {"output": output} for row in rows}


def _run(conn: sqlite3.Connection, project_id: int, suite_id: int, purpose: str, **extra: Any) -> int:
    payload: dict[str, Any] = {
        "projectId": project_id,
        "suiteId": suite_id,
        "purpose": purpose,
    }
    payload.update(extra)
    payload.setdefault("config", {"temperature": 0})
    return int(eval_service.create_run(conn, payload)["runId"])


def _expect_error(code: int, func: Callable[[], object]) -> None:
    try:
        func()
    except AppError as exc:
        assert exc.code == code, f"期望错误码 {code}，实际 {exc.code}: {exc.message}"
        return
    raise AssertionError(f"应当抛出错误码 {code}，实际没有异常")


def _payload(response: Any) -> dict[str, Any]:
    data = response.get_json(silent=True)
    return data if isinstance(data, dict) else {}


def _code(response: Any) -> int:
    return int(_payload(response).get("code") or 0)


# ---------------------------------------------------------------- 断言


def test_outbound_redaction() -> None:
    redacted = eval_runner.redact_sensitive(SECRET_LINE)
    for name, value in PII_SAMPLES.items():
        assert value not in redacted, f"{name} 未脱敏: {redacted[:200]}"
    assert "[手机号已脱敏]" in redacted and "[API Key 已脱敏]" in redacted, redacted[:200]

    conn = _new_conn()
    project_id = _project_id(conn)
    suite_id = _suite(conn, project_id, "脱敏集")
    _case(conn, project_id, suite_id, "敏感样本")
    case = dict(
        conn.execute("SELECT * FROM biz_eval_case WHERE suite_id = ?", (suite_id,)).fetchone()
    )
    judged = eval_runner.judge_case(case, {"output": SECRET_LINE})
    serialized = json.dumps(judged, ensure_ascii=False)
    for name, value in PII_SAMPLES.items():
        assert value not in serialized, f"{name} 明文进入了判定结果（会随结果落库/展示）"
    assert judged["verdict"] == "pass" and "答案如下" in str(judged["output_text"])


def test_not_stored_or_logged() -> None:
    conn = _new_conn()
    db_path = pathlib.Path(_db_path(conn))
    project_id = _project_id(conn)
    suite_id = _suite(conn, project_id, "落库脱敏集")
    _case(conn, project_id, suite_id, "落库样本")
    run_id = _run(conn, project_id, suite_id, "security-log")

    emitted: list[str] = []
    handler = _ListHandler(emitted)
    root = logging.getLogger()
    previous_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        eval_service.execute_replay(conn, run_id, _outputs(conn, suite_id, SECRET_LINE))
    finally:
        root.removeHandler(handler)
        root.setLevel(previous_level)

    row = conn.execute(
        "SELECT output_text FROM biz_eval_result WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row is not None and "[API Key 已脱敏]" in str(row["output_text"])

    blob = db_path.read_bytes()
    wal_path = pathlib.Path(f"{db_path}-wal")
    if wal_path.exists():
        blob += wal_path.read_bytes()
    for name, value in PII_SAMPLES.items():
        assert value.encode("utf-8") not in blob, f"{name} 明文写进了数据库文件"
    log_text = "\n".join(emitted)
    for name, value in PII_SAMPLES.items():
        assert value not in log_text, f"{name} 明文进了日志"


def test_retention_policy() -> None:
    conn = _new_conn()
    db_path = pathlib.Path(_db_path(conn))
    project_id = _project_id(conn)
    suite_id = _suite(conn, project_id, "留存集")
    _case(conn, project_id, suite_id, "留存样本")
    normal_run = _run(conn, project_id, suite_id, "retention-normal")
    pinned_run = _run(conn, project_id, suite_id, "retention-pinned")
    eval_service.execute_replay(conn, normal_run, _outputs(conn, suite_id, "普通跑批结果"))
    eval_service.execute_replay(conn, pinned_run, _outputs(conn, suite_id, "钉住跑批结果"))
    conn.execute("UPDATE biz_eval_run SET is_pinned = 1 WHERE id = ?", (pinned_run,))
    old = (datetime.now(timezone.utc) - timedelta(days=100)).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"
    conn.execute(
        "UPDATE biz_eval_result SET created_at = ? WHERE run_id IN (?, ?)", (old, normal_run, pinned_run)
    )
    conn.commit()

    settings = retention_service.get_settings(conn)
    item = next(entry for entry in settings["items"] if entry["dataType"] == "eval_result")
    assert item["days"] == 90 and item["action"] == "archive", item

    dry = retention_service.run_cleanup(
        conn, db_path, data_types=["eval_result"], dry_run=True
    )
    dry_item = next(entry for entry in dry["types"] if entry["dataType"] == "eval_result")
    assert dry_item["due"] == 1 and dry_item["skipped"] == 1, dry_item
    untouched = conn.execute(
        "SELECT output_text FROM biz_eval_result WHERE run_id = ?", (normal_run,)
    ).fetchone()
    assert str(untouched["output_text"]) == "普通跑批结果", "dryRun 不得改库"

    real = retention_service.run_cleanup(conn, db_path, data_types=["eval_result"])
    real_item = next(entry for entry in real["types"] if entry["dataType"] == "eval_result")
    assert real_item["due"] == 1 and real_item["archived"] == 1 and real_item["skipped"] == 1, real_item
    assert real_item["bundles"], "归档动作必须落归档包"

    cleared = conn.execute(
        "SELECT output_text, tool_trace, judge_json, verdict FROM biz_eval_result WHERE run_id = ?",
        (normal_run,),
    ).fetchone()
    assert str(cleared["output_text"]) == "" and str(cleared["tool_trace"]) == "[]", dict(cleared)
    assert str(cleared["judge_json"]) == "{}" and str(cleared["verdict"]) == "pass", dict(cleared)
    metric_count = conn.execute(
        "SELECT COUNT(*) FROM biz_eval_metric WHERE run_id = ?", (normal_run,)
    ).fetchone()[0]
    assert int(metric_count) == 18, f"归档后指标必须保留，实际 {metric_count}"

    kept = conn.execute(
        "SELECT output_text FROM biz_eval_result WHERE run_id = ?", (pinned_run,)
    ).fetchone()
    assert str(kept["output_text"]) == "钉住跑批结果", "钉住跑批的明细不得被清理"


def test_routes_and_auth() -> None:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_eval_sec_app_"))
    db_path = workdir / "app.db"
    init_db(db_path)
    from aitp.app import create_app

    app: Flask = create_app(
        AppConfig(
            host="127.0.0.1",
            port=8799,
            debug=False,
            db_path=db_path,
            log_path=workdir / "app.log",
            deploy_log_path=workdir / "deploy.log",
            secret_key_path=workdir / ".secret_key",
            prompts_dir=ROOT / "backend" / "prompts",
            static_dir=ROOT / "backend" / "aitp" / "static",
        )
    )
    paths = sorted(
        str(rule.rule) for rule in app.url_map.iter_rules()
        if str(rule.rule).startswith("/api/v1/evals")
    )
    assert len(paths) == 34, f"设计要 34 条 eval 路由，实际 {len(paths)}: {paths}"
    assert not (set(paths) & set(middleware._AUTH_OPEN_PATHS)), "eval 路由不得进免登录白名单"
    for path in paths:
        assert path.startswith("/api/v1/evals/"), f"eval 路由必须在 /api/v1 前缀下: {path}"

    anon = app.test_client()
    owner = app.test_client()
    password = "eval-security-pass"
    enabled = owner.put(
        "/api/v1/auth/password", json={"password": password, "confirmPassword": password}
    )
    assert _code(enabled) == ErrorCode.SUCCESS, _payload(enabled)
    try:
        assert _code(anon.get("/api/v1/evals/meta")) == ErrorCode.AUTH_REQUIRED
        assert _code(anon.post("/api/v1/evals/suites", json={})) == ErrorCode.AUTH_REQUIRED
        login = anon.post("/api/v1/auth/login", json={"password": password})
        assert _code(login) == ErrorCode.SUCCESS, _payload(login)
        assert _code(anon.get("/api/v1/evals/meta")) == ErrorCode.SUCCESS
    finally:
        removed = owner.delete("/api/v1/auth/password", json={"password": password})
        assert _code(removed) == ErrorCode.SUCCESS, _payload(removed)


def test_cross_project_denied() -> None:
    conn = _new_conn()
    project_a = _project_id(conn)
    project_b = _new_project(conn, "EVAL-SEC-B", "越权测试项目B")
    suite_a = _suite(conn, project_a, "项目A评测集")
    suite_b = _suite(conn, project_b, "项目B评测集")
    _case(conn, project_a, suite_a, "A用例")
    _case(conn, project_b, suite_b, "B用例")
    config_a = eval_service.create_config(
        conn, {"projectId": project_a, "name": "A配置", "temperature": 0.1}
    )
    judge_a = eval_service.create_judge(
        conn, {"projectId": project_a, "name": "A判定器", "strategy": "single", "temperature": 0}
    )

    _expect_error(
        ErrorCode.ILLEGAL_OPERATION,
        lambda: _run(conn, project_b, suite_b, "cross-config", configId=config_a),
    )
    _expect_error(
        ErrorCode.ILLEGAL_OPERATION,
        lambda: _run(conn, project_b, suite_b, "cross-judge", judgeId=judge_a),
    )
    _expect_error(
        ErrorCode.PARAM_ERROR,
        lambda: eval_service.add_case(
            conn,
            {
                "suiteId": suite_b,
                "projectId": project_a,
                "title": "越权用例",
                "assertions": {"max_chars": 100},
            },
        ),
    )
    scoped = EvalSuiteRepository(conn).list({"project_id": project_b}, None, page_size=100)
    assert {int(row["id"]) for row in scoped.list} == {suite_b}, scoped.list


def test_export_no_escalation() -> None:
    conn = _new_conn()
    project_id = _project_id(conn)
    suite_id = _suite(conn, project_id, "导出集")
    _case(conn, project_id, suite_id, "导出样本")
    run_id = _run(conn, project_id, suite_id, "security-export")
    eval_service.execute_replay(conn, run_id, _outputs(conn, suite_id, SECRET_LINE))
    exported = eval_service.export_run(conn, run_id, "md")
    text = bytes(exported["content"]).decode("utf-8")
    assert exported["format"] == "md" and int(exported["count"]) == 1, exported["filename"]
    for name, value in PII_SAMPLES.items():
        assert value not in text, f"导出内容泄露了 {name} 明文"

    holdout_suite = _suite(conn, project_id, "holdout导出集", visibility="holdout")
    _case(conn, project_id, holdout_suite, "holdout样本")
    holdout_run = _run(conn, project_id, holdout_suite, "security-holdout")
    eval_service.execute_replay(conn, holdout_run, _outputs(conn, holdout_suite, "答案"))
    _expect_error(
        ErrorCode.EVAL_HOLDOUT_EXPORT_DENIED,
        lambda: eval_service.export_run(conn, holdout_run, "md"),
    )
    _expect_error(
        ErrorCode.EVAL_HOLDOUT_EXPORT_DENIED,
        lambda: eval_service.export_run(conn, holdout_run, "xlsx"),
    )


def main() -> int:
    check("评测输入外发走脱敏", test_outbound_redaction)
    check("密钥/PII 不落库、不打日志", test_not_stored_or_logged)
    check("结果保留期策略生效（90 天 / 留指标 / 钉住豁免）", test_retention_policy)
    check("/evals 全路由 v1 前缀且鉴权一致", test_routes_and_auth)
    check("越权访问他项目 suite 被拒", test_cross_project_denied)
    check("导出无越权（holdout 拒绝 + 无明文 PII）", test_export_no_escalation)

    failed = [item for item in RESULTS if not item[1]]
    for name, passed, message in RESULTS:
        print(f"[{'PASS' if passed else 'FAIL'}] {name}" + (f" —— {message}" if message else ""))
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} 项通过")
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())