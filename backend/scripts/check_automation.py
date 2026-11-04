"""自动化脚本资产与执行引擎验收脚本。

在临时库上验证：
- 四类脚本目录与 CRUD
- Python 解释器执行：成功 / 失败 / 超时三条路径
- JMeter 未配置时给出可读提示，不崩
- 执行历史落库与筛选
- 从 AI 产出一键入库（提取 xml / python 代码块）
- 运行时体检与配置写入
- 安全：脚本内容不进命令行；附加参数不走 shell

    python backend/scripts/check_automation.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
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

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
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


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_automation.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_automation.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


PY_OK = "print('hello-automation')\n"
PY_FAIL = "import sys\nsys.stderr.write('boom')\nsys.exit(3)\n"
PY_SLEEP = "import time\ntime.sleep(30)\n"


def check_categories(api: Api) -> None:
    items = api.data("GET", "/api/v1/automation/categories")
    check("四个自动化目录齐全", len(items) == 4, str(items)[:200])
    labels = [item["label"] for item in items]
    check("目录名称正确", labels == ["Web 自动化", "App 自动化", "接口自动化", "性能测试"],
          str(labels))
    runners = {item["category"]: item["runner"] for item in items}
    check("前三类走 Python，性能走 JMeter",
          runners.get("web") == "python" and runners.get("performance") == "jmeter",
          str(runners))


def check_crud(api: Api) -> None:
    created = api.data("POST", "/api/v1/automation/scripts", {
        "name": "登录冒烟",
        "category": "web",
        "framework": "selenium",
        "language": "python",
        "content": PY_OK,
    })
    check("创建脚本返回编号", str(created.get("scriptNo") or "").startswith("AUTO-W-"),
          str(created)[:200])
    check("创建脚本默认 ready", created.get("status") == "ready", str(created)[:200])
    check("创建脚本带文件名", created.get("fileName") == "登录冒烟.py", str(created)[:200])

    script_id = int(created["id"])
    fetched = api.data("GET", f"/api/v1/automation/scripts/{script_id}")
    check("读取脚本带回正文", fetched.get("content") == PY_OK, str(fetched)[:200])

    updated = api.data("PUT", f"/api/v1/automation/scripts/{script_id}",
                        {"description": "登录主流程", "framework": "playwright"})
    check("更新脚本生效", updated.get("framework") == "playwright", str(updated)[:200])

    listed = api.data("GET", "/api/v1/automation/scripts?category=web")
    check("按类别筛选脚本", all(item["category"] == "web" for item in listed["list"]),
          str(listed)[:200])
    check("类别统计回填", listed["categoryCounts"]["web"] >= 1, str(listed.get("categoryCounts")))

    resp, body = api.call("DELETE", f"/api/v1/automation/scripts/{script_id}")
    check("删除脚本成功", body.get("code") == ErrorCode.SUCCESS, str(body))
    resp, body = api.call("GET", f"/api/v1/automation/scripts/{script_id}")
    check("删除后取不到脚本", body.get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(body))


def check_python_execution(api: Api) -> None:
    created = api.data("POST", "/api/v1/automation/scripts", {
        "name": "执行成功用例", "category": "api", "content": PY_OK,
    })
    script_id = int(created["id"])
    result = api.data("POST", f"/api/v1/automation/scripts/{script_id}/run", {})
    check("Python 脚本执行成功", result.get("status") == "success", str(result)[:300])
    check("执行返回标准输出", "hello-automation" in str(result.get("stdout") or ""),
          str(result)[:300])
    check("执行返回退出码 0", result.get("exitCode") == 0, str(result)[:300])
    check("执行记录了耗时", int(result.get("durationMs") or 0) >= 0, str(result)[:200])

    detail = api.data("GET", f"/api/v1/automation/scripts/{script_id}")
    check("脚本详情带执行历史", len(detail.get("runs") or []) >= 1, str(detail)[:200])
    check("脚本回写最近一次执行状态",
          detail.get("lastRunStatus") == "success", str(detail)[:200])

    failed_script = api.data("POST", "/api/v1/automation/scripts", {
        "name": "执行失败用例", "category": "web", "content": PY_FAIL,
    })
    result = api.data("POST", f"/api/v1/automation/scripts/{failed_script['id']}/run", {})
    check("失败脚本状态为 failed", result.get("status") == "failed", str(result)[:300])
    check("失败脚本带回退出码 3", result.get("exitCode") == 3, str(result)[:300])
    check("失败脚本带回错误输出", "boom" in str(result.get("stderr") or ""), str(result)[:300])

    slow_script = api.data("POST", "/api/v1/automation/scripts", {
        "name": "超时用例", "category": "web", "content": PY_SLEEP,
    })
    result = api.data("POST", f"/api/v1/automation/scripts/{slow_script['id']}/run",
                      {"timeoutSeconds": 1})
    check("超时按 timeout 收口", result.get("status") == "timeout", str(result)[:300])
    check("超时给出可读提示", "超时" in str(result.get("errorMessage") or ""), str(result)[:300])


def check_jmeter_guard(api: Api) -> None:
    script = api.data("POST", "/api/v1/automation/scripts", {
        "name": "登录压测", "category": "performance",
        "content": "<jmeterTestPlan/>",
    })
    check("性能脚本默认 jmeter 运行方式", script.get("runMode") == "jmeter", str(script)[:200])
    check("性能脚本文件名为 .jmx", script.get("fileName") == "登录压测.jmx", str(script)[:200])

    resp, body = api.call("POST", f"/api/v1/automation/scripts/{script['id']}/run", {})
    check("未配置 JMeter 时返回可读错误", body.get("code") == ErrorCode.ILLEGAL_OPERATION,
          str(body))
    check("错误提示指向性能测试设置", "JMeter" in str(body.get("message") or ""), str(body))


def check_from_output(api: Api) -> None:
    output = """下面是性能脚本说明。

```xml
<jmeterTestPlan version="1.2">
  <hashTree/>
</jmeterTestPlan>
```
"""
    created = api.data("POST", "/api/v1/automation/scripts/from-output", {
        "output": output, "name": "一键入库压测", "category": "performance",
    })
    check("从产出提取 xml 代码块", "<jmeterTestPlan" in str(created.get("content") or ""),
          str(created)[:300])
    check("从产出入库标记来源为 ai", created.get("source") == "ai", str(created)[:200])

    py_output = "说明文字\n\n```python\nprint('from-ai')\n```\n"
    created = api.data("POST", "/api/v1/automation/scripts/from-output", {
        "output": py_output, "name": "一键入库用例", "category": "web",
    })
    check("从产出提取 python 代码块", "from-ai" in str(created.get("content") or ""),
          str(created)[:300])


def check_runtime(api: Api) -> None:
    status = api.data("GET", "/api/v1/automation/runtime")
    check("运行时体检返回解释器", bool(status.get("pythonInterpreter")), str(status)[:200])
    check("运行时体检返回平台", bool(status.get("platform")), str(status)[:200])
    check("默认回落到服务自身解释器",
          str(status.get("pythonInterpreter") or "").endswith(".exe")
          or "/" in str(status.get("pythonInterpreter") or ""), str(status)[:200])

    check("解释器版本可探测", bool(status.get("pythonVersion")), str(status)[:200])
    check("未配置 JMeter 时标记不可用", status.get("jmeterAvailable") is False, str(status)[:200])

    api.data("PUT", "/api/v1/settings/config", {
        "items": [
            {"key": "performance.jmeter.home", "value": "C:/opt/jmeter", "valueType": "string"},
        ]
    })
    status = api.data("GET", "/api/v1/automation/runtime")
    check("JMeter 根目录写入后生效", status.get("jmeterHome") == "C:/opt/jmeter", str(status)[:200])


def check_runs_list(api: Api) -> None:
    listed = api.data("GET", "/api/v1/automation/runs?pageSize=100")
    check("执行历史可列出", int(listed.get("total") or 0) >= 3, str(listed.get("total")))
    filtered = api.data("GET", "/api/v1/automation/runs?status=timeout")
    check("执行历史可按状态筛选",
          all(item["status"] == "timeout" for item in filtered["list"]), str(filtered)[:200])
    by_category = api.data("GET", "/api/v1/automation/runs?category=web")
    check("执行历史可按类别筛选",
          all(item["category"] == "web" for item in by_category["list"]), str(by_category)[:200])


def check_security(api: Api) -> None:
    """附加参数不得产生 shell 语义；脚本正文不得出现在命令行里。"""
    marker = "SECRET-MARKER-CONTENT"
    created = api.data("POST", "/api/v1/automation/scripts", {
        "name": "安全用例", "category": "web",
        "content": f"print('{marker}')\n",
        "entryCommand": "&& echo injected",
    })
    result = api.data("POST", f"/api/v1/automation/scripts/{created['id']}/run", {})
    check("附加参数被当作普通参数传递", result.get("status") == "success", str(result)[:300])
    check("附加参数未触发 shell 执行",
          "injected" not in str(result.get("stdout") or ""), str(result)[:300])
    check("脚本正文不进入命令行",
          marker not in str(result.get("commandLine") or ""), str(result)[:300])
    check("命令行只含解释器与脚本路径",
          str(result.get("commandLine") or "").count(" ") >= 1, str(result)[:300])


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_automation_"))
    try:
        app = build_app(workdir)
        api = Api(app)

        check_categories(api)
        check_crud(api)
        check_python_execution(api)
        check_jmeter_guard(api)
        check_from_output(api)
        check_runtime(api)
        check_runs_list(api)
        check_security(api)
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
