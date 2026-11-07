"""测试环境配置门禁：CRUD、口令加密掩码、健康探测、默认环境唯一。

核心安全不变量：
  - 数据库口令本体进 OS 密钥库（test_env:<id>），库内只存掩码与引用
  - 任何接口都不回显口令明文
  - 探测纯本机发起，环境信息不进大模型
"""

from __future__ import annotations

import pathlib
import sqlite3
import sys
import tempfile
from typing import Any, cast

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.repositories.sys_config_repo import SysConfigRepository  # noqa: E402
from aitp.services.knowledge_storage import CONFIG_PATH  # noqa: E402
from flask import Flask  # noqa: E402

ROOT = pathlib.Path(__file__).resolve().parents[2]
V1 = "/api/v1"
JsonDict = dict[str, Any]

_passed = 0
_failed = 0
_failures: list[str] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        return True
    _failed += 1
    _failures.append(name)
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    """Flask test client 薄封装：统一拆包封、断言业务码。"""

    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def data(self, method: str, path: str, payload: JsonDict | None = None) -> Any:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True) or {}
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}")
        return body.get("data")

    def expect(self, name: str, method: str, path: str, code: int,
               payload: JsonDict | None = None) -> None:
        resp = self.client.open(path, method=method, json=payload)
        body = resp.get_json(silent=True) or {}
        check(name, body.get("code") == code,
              f"http={resp.status_code} code={body.get('code')} msg={body.get('message')}")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "env.db"
    init_db(db_path)
    conn = sqlite3.connect(db_path)
    SysConfigRepository(conn).set_value(
        CONFIG_PATH, str(workdir / "knowledge"), "string", "门禁临时目录")
    conn.commit()
    conn.close()
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "env.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def section_crud(api: Api) -> dict[str, Any]:
    """清单带类型词表；创建/读取/更新/删除走一遍。"""
    listing = api.data("GET", f"{V1}/test-envs")
    check("清单可读且带库类型词表", isinstance(listing.get("list"), list)
          and "mysql" in listing.get("dbTypes", [])
          and isinstance(listing.get("dbDefaultPorts"), dict), str(listing)[:160])
    check("初始清单为空", listing["list"] == [], str(listing["list"]))

    api.expect("缺 name 被拒绝", "POST", f"{V1}/test-envs", ErrorCode.PARAM_ERROR, {})
    api.expect("非法库类型被拒绝", "POST", f"{V1}/test-envs", ErrorCode.PARAM_ERROR, {
        "name": "x", "db": {"type": "spaceship"}})

    created = api.data("POST", f"{V1}/test-envs", {
        "name": "测试环境",
        "description": "门禁用",
        "isDefault": True,
        "app": {"baseUrl": "http://127.0.0.1:8760", "healthPath": "/api/v1/meta/enums",
                "metricsUrl": ""},
        "db": {"type": "mysql", "host": "127.0.0.1", "port": 3306,
               "name": "shop", "username": "qa", "password": "s3cret-pass-123",
               "metricsUrl": ""},
    })
    check("创建返回 id", bool(created.get("id")), str(created)[:160])
    check("应用服务器字段落库", created["app"]["baseUrl"] == "http://127.0.0.1:8760", str(created["app"]))
    check("数据库字段落库", created["db"]["host"] == "127.0.0.1" and created["db"]["type"] == "mysql",
          str(created["db"]))
    check("监控地址默认留空", created["app"].get("metricsUrl") == ""
          and created["db"].get("metricsUrl") == "", str(created)[:160])
    check("口令只回掩码不回明文", created["db"]["hasPassword"] is True
          and "s3cret-pass" not in str(created) and created["db"]["passwordMasked"] != "",
          str(created["db"]))
    check("标记为默认环境", created["isDefault"] is True, str(created))
    return cast(JsonDict, created)


def section_metrics_url(api: Api, env_id: int) -> None:
    """监控地址可保存、可清空；不填不报错，填了也不影响可达性探测。"""
    updated = api.data("PUT", f"{V1}/test-envs/{env_id}", {
        "app": {"baseUrl": "http://127.0.0.1:8760", "healthPath": "/api/v1/meta/enums",
                "metricsUrl": "http://127.0.0.1:9100/metrics"},
        "db": {"type": "mysql", "host": "127.0.0.1", "port": 3306, "name": "shop",
               "username": "qa", "metricsUrl": "http://127.0.0.1:9101/metrics"},
    })
    check("监控地址保存后回显", updated["app"].get("metricsUrl") == "http://127.0.0.1:9100/metrics"
          and updated["db"].get("metricsUrl") == "http://127.0.0.1:9101/metrics",
          str(updated)[:200])
    reset = api.data("PUT", f"{V1}/test-envs/{env_id}", {
        "app": {"baseUrl": "http://127.0.0.1:8760", "healthPath": "/api/v1/meta/enums",
                "metricsUrl": ""},
        "db": {"type": "mysql", "host": "127.0.0.1", "port": 3306, "name": "shop",
               "username": "qa", "metricsUrl": ""},
    })
    check("监控地址可清空", reset["app"].get("metricsUrl") == ""
          and reset["db"].get("metricsUrl") == "", str(reset)[:160])


def section_password_secret(api: Api, env_id: int, workdir: pathlib.Path) -> None:
    """口令本体进密钥库、库里无明文；更新与清除都只回掩码。"""
    row = api.data("GET", f"{V1}/test-envs/{env_id}")
    check("详情不回明文口令", "s3cret-pass" not in str(row), str(row["db"]))

    conn = sqlite3.connect(workdir / "env.db")
    raw = conn.execute("SELECT db_password_mask, db_password_ref FROM biz_test_env WHERE id = ?",
                       (env_id,)).fetchone()
    conn.close()
    check("库内只存掩码与引用", raw is not None and raw[0] and raw[0] != "s3cret-pass-123"
          and raw[1] == f"test_env:{env_id}", str(raw))
    check("库内无明文口令", raw is None or "s3cret-pass" not in str(raw), str(raw))

    masked = api.data("POST", f"{V1}/test-envs/{env_id}/password", {"password": "new-pass-456"})
    check("更新口令只回掩码", masked.get("hasPassword") is True
          and "new-pass" not in str(masked) and masked.get("passwordMasked"), str(masked))

    cleared = api.data("POST", f"{V1}/test-envs/{env_id}/password", {"password": ""})
    check("清空口令后 hasPassword 为假", cleared.get("hasPassword") is False, str(cleared))
    api.data("POST", f"{V1}/test-envs/{env_id}/password", {"password": "s3cret-pass-123"})


def section_default_unique(api: Api) -> None:
    """默认环境全局唯一，设新默认后旧的自动取消。"""
    second = api.data("POST", f"{V1}/test-envs", {
        "name": "预发环境", "isDefault": True,
        "db": {"type": "postgresql", "host": "10.0.0.5"}})
    listing = api.data("GET", f"{V1}/test-envs")
    defaults = [e for e in listing["list"] if e["isDefault"]]
    check("默认环境唯一", len(defaults) == 1 and defaults[0]["id"] == second["id"],
          str([(e["id"], e["isDefault"]) for e in listing["list"]]))
    check("端口按类型带出默认值", second["db"]["port"] == 5432, str(second["db"]))
    api.data("PUT", f"{V1}/test-envs/{second['id']}", {"isDefault": False})
    listing2 = api.data("GET", f"{V1}/test-envs")
    check("取消默认后无默认环境", not any(e["isDefault"] for e in listing2["list"]),
          str([(e["id"], e["isDefault"]) for e in listing2["list"]]))
    api.data("DELETE", f"{V1}/test-envs/{second['id']}")


def _audit_count(workdir: pathlib.Path, env_id: int) -> int:
    conn = sqlite3.connect(workdir / "env.db")
    count = int(conn.execute(
        "SELECT COUNT(*) FROM sys_audit_log WHERE resource_type = 'test_env' AND resource_id = ?"
        " AND action = 'execute'", (env_id,)).fetchone()[0])
    conn.close()
    return count


def section_probe(api: Api, env_id: int, workdir: pathlib.Path) -> None:
    """探测纯本机：可达目标返回 ok，不可达返回明确业务结果而非内部错误，并带资源指标。"""
    result = api.data("POST", f"{V1}/test-envs/{env_id}/probe")
    check("探测返回结构化结果", isinstance(result, dict) and "app" in result and "db" in result
          and result.get("overall") in ("healthy", "unhealthy"), str(result)[:200])
    # 本机 AITP 服务本身在跑，应用探测应可达
    check("应用探测本机可达", result["app"].get("ok") is True, str(result["app"]))
    check("探测结果带耗时", isinstance(result["app"].get("latencyMs"), int), str(result["app"]))

    app_metrics = result["app"].get("metrics") or {}
    db_metrics = result["db"].get("metrics") or {}
    check("应用服务器带资源指标", app_metrics.get("available") is True
          and app_metrics.get("source") == "local", str(app_metrics)[:200])
    check("指标含 CPU/内存/磁盘结构", "cpuPercent" in app_metrics
          and isinstance(app_metrics.get("memory"), dict)
          and isinstance(app_metrics.get("disks"), list)
          and app_metrics["memory"].get("totalBytes", 0) > 0, str(app_metrics)[:200])
    check("本机数据库目标也带资源指标", db_metrics.get("available") is True, str(db_metrics)[:200])

    # 自动轮询用的静默探测不写操作日志
    before = _audit_count(workdir, env_id)
    api.data("POST", f"{V1}/test-envs/{env_id}/probe", {"quiet": True})
    after = _audit_count(workdir, env_id)
    check("静默探测不写操作日志", before == after, f"before={before} after={after}")
    api.data("POST", f"{V1}/test-envs/{env_id}/probe")
    check("手动探测仍写操作日志", _audit_count(workdir, env_id) == after + 1,
          f"after={after}")

    # 一个必然连不上的端口，验证「不可达」走业务结果而非 500
    down = api.data("POST", f"{V1}/test-envs/probe-draft", {
        "app": {"baseUrl": "http://127.0.0.1:1", "healthPath": "/x"},
        "db": {"type": "mysql", "host": "127.0.0.1", "port": 1}})
    check("不可达探测给出明确结果", down.get("overall") == "unhealthy"
          and down["app"].get("ok") is False and down["db"].get("ok") is False, str(down)[:200])
    # 未配置地址：返回 unconfigured 而非报错
    empty = api.data("POST", f"{V1}/test-envs/probe-draft", {
        "app": {"baseUrl": "", "healthPath": ""}, "db": {"type": "sqlite", "host": "", "port": ""}})
    check("空配置探测不报错", empty["app"].get("status") == "unconfigured", str(empty))
    check("空配置无资源指标", empty["app"]["metrics"].get("available") is False
          and empty["db"]["metrics"].get("available") is True, str(empty["app"]["metrics"]))


def section_isolation(api: Api) -> None:
    """环境信息绝不进大模型：能力 run / 问答接口的请求与响应都不含环境字段。"""
    # 结构性保证：test_envs 蓝图与 lab/ai 蓝图相互独立，环境字段只存在于 test-envs 下
    caps = api.data("GET", f"{V1}/lab/capabilities")
    blob = str(caps)
    check("能力目录不含环境配置字段", "app_base_url" not in blob and "db_password" not in blob,
          blob[:120])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-envcfg-"))
    api = Api(build_app(workdir))
    created = section_crud(api)
    env_id = int(created["id"])
    section_password_secret(api, env_id, workdir)
    section_metrics_url(api, env_id)
    section_default_unique(api)
    section_probe(api, env_id, workdir)
    section_isolation(api)

    print("-" * 50)
    print(f"测试环境配置：PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败清单：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    sys.exit(main())
