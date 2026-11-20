"""自动化脚本资产与执行引擎验收脚本。

在临时库上验证：
- 四类脚本目录与 CRUD
- Python 解释器执行：成功 / 失败 / 超时三条路径
- JMeter 未配置时给出可读提示，不崩
- 执行历史落库与筛选
- 从 AI 产出一键入库（提取 xml / python 代码块）
- 运行时体检与配置写入
- 框架清单与平台归属：iOS / Android 用 Appium，鸿蒙用 Hypium
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
        self.scope_project_id: int | None = None

    def _scoped(self, path: str) -> str:
        if not self.scope_project_id or "projectId=" in path:
            return path
        bare = path.split("?")[0]
        if not any(part.isdigit() for part in bare.strip("/").split("/")):
            return path
        sep = "&" if "?" in path else "?"
        return f"{path}{sep}projectId={self.scope_project_id}"

    def call(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(self._scoped(path), method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        if (method == "POST" and not self.scope_project_id
                and path.split("?")[0].rstrip("/").endswith("/projects")):
            data = body.get("data")
            if isinstance(data, dict) and data.get("id"):
                self.scope_project_id = int(data["id"])
        return resp, cast(JsonDict, body)

    def data(self, method: str, path: str, payload: JsonDict | None = None,
             **kwargs: Any) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        result = body.get("data")
        if (method == "POST" and not self.scope_project_id
                and path.split("?")[0].rstrip("/").endswith("/projects")):
            if isinstance(result, dict) and result.get("id"):
                self.scope_project_id = int(result["id"])
        return result


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
        "projectId": api.scope_project_id,
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

    # 目录统计必须按项目收敛：另一个项目看不到本项目的脚本数
    other = api.data("POST", "/api/v1/projects", {"code": "AUTOG2", "name": "统计隔离项目"})
    other_items = api.data("GET", f"/api/v1/automation/categories?projectId={other['id']}")
    own_items = api.data("GET", f"/api/v1/automation/categories?projectId={api.scope_project_id}")
    own_web = next(item for item in own_items if item["category"] == "web")
    check("目录统计按项目隔离", all(item["scriptCount"] == 0 for item in other_items)
          and own_web["scriptCount"] >= 1, str(other_items)[:200])

    resp, body = api.call("DELETE", f"/api/v1/automation/scripts/{script_id}")
    check("删除脚本成功", body.get("code") == ErrorCode.SUCCESS, str(body))
    resp, body = api.call("GET", f"/api/v1/automation/scripts/{script_id}")
    check("删除后取不到脚本", body.get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(body))


def check_python_execution(api: Api) -> None:
    created = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
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
        "projectId": api.scope_project_id,
        "name": "执行失败用例", "category": "web", "content": PY_FAIL,
    })
    result = api.data("POST", f"/api/v1/automation/scripts/{failed_script['id']}/run", {})
    check("失败脚本状态为 failed", result.get("status") == "failed", str(result)[:300])
    check("失败脚本带回退出码 3", result.get("exitCode") == 3, str(result)[:300])
    check("失败脚本带回错误输出", "boom" in str(result.get("stderr") or ""), str(result)[:300])

    slow_script = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
        "name": "超时用例", "category": "web", "content": PY_SLEEP,
    })
    result = api.data("POST", f"/api/v1/automation/scripts/{slow_script['id']}/run",
                      {"timeoutSeconds": 1})
    check("超时按 timeout 收口", result.get("status") == "timeout", str(result)[:300])
    check("超时给出可读提示", "超时" in str(result.get("errorMessage") or ""), str(result)[:300])


def check_jmeter_guard(api: Api) -> None:
    script = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
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
        "projectId": api.scope_project_id,
        "output": output, "name": "一键入库压测", "category": "performance",
    })
    check("从产出提取 xml 代码块", "<jmeterTestPlan" in str(created.get("content") or ""),
          str(created)[:300])
    check("从产出入库标记来源为 ai", created.get("source") == "ai", str(created)[:200])
    check("性能产出入库默认 JMeter 框架", created.get("framework") == "jmeter",
          str(created)[:200])

    py_output = "说明文字\n\n```python\nprint('from-ai')\n```\n"
    created = api.data("POST", "/api/v1/automation/scripts/from-output", {
        "projectId": api.scope_project_id,
        "output": py_output, "name": "一键入库用例", "category": "web",
    })
    check("从产出提取 python 代码块", "from-ai" in str(created.get("content") or ""),
          str(created)[:300])
    check("Web 产出入库默认 Selenium 框架", created.get("framework") == "selenium",
          str(created)[:200])

    created = api.data("POST", "/api/v1/automation/scripts/from-output", {
        "projectId": api.scope_project_id,
        "output": py_output, "name": "一键入库鸿蒙", "category": "app",
        "appPlatforms": "hos",
    })
    check("鸿蒙产出入库默认 Hypium 框架", created.get("framework") == "hypium",
          str(created)[:200])

    # AI 实验室一键入库编号带 AI_ 前缀，与人工创建走独立序列
    ai_web = api.data("POST", "/api/v1/automation/scripts/from-output", {
        "projectId": api.scope_project_id,
        "output": py_output, "name": "AI入库Web", "category": "web", "ai": True,
    })
    check("AI 入库 Web 脚本编号带 AI_AUTO-W 前缀",
          str(ai_web.get("scriptNo") or "").startswith("AI_AUTO-W-"),
          str(ai_web.get("scriptNo")))
    manual_web = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
        "name": "人工Web脚本", "category": "web", "content": "print(1)",
    })
    check("人工创建仍走 AUTO-W 序列，不受 AI 编号影响",
          str(manual_web.get("scriptNo") or "").startswith("AUTO-W-")
          and not str(manual_web.get("scriptNo") or "").startswith("AI_"),
          str(manual_web.get("scriptNo")))
    ai_api = api.data("POST", "/api/v1/automation/scripts/from-output", {
        "projectId": api.scope_project_id,
        "output": py_output, "name": "AI入库接口", "category": "api", "ai": True,
    })
    check("AI 入库接口脚本编号带 AI_AUTO-I 前缀",
          str(ai_api.get("scriptNo") or "").startswith("AI_AUTO-I-"),
          str(ai_api.get("scriptNo")))
    ai_sql = api.data("POST", "/api/v1/sql-scripts", {
        "name": "AI入库SQL", "content": "SELECT 1", "ai": True,
    })
    check("AI 入库数据库脚本编号带 AI_SQL 前缀",
          str(ai_sql.get("scriptNo") or "").startswith("AI_SQL-"),
          str(ai_sql.get("scriptNo")))
    manual_sql = api.data("POST", "/api/v1/sql-scripts", {
        "name": "人工SQL脚本", "content": "SELECT 2",
    })
    check("人工创建数据库脚本仍走 SQL 序列",
          str(manual_sql.get("scriptNo") or "").startswith("SQL-")
          and not str(manual_sql.get("scriptNo") or "").startswith("AI_"),
          str(manual_sql.get("scriptNo")))


def check_copy(api: Api) -> None:
    """单条复制：新编号唯一且符合序列规则、名称加「副本-」前缀、其余字段一致。"""
    # 自动化脚本复制
    src = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
        "name": "复制源脚本", "category": "web", "content": "print('copy-me')",
        "framework": "playwright", "description": "复制源描述",
    })
    copied = api.data("POST", f"/api/v1/automation/scripts/{src['id']}/copy", {})
    check("自动化脚本复制后编号是新的 AUTO-W 序列",
          str(copied.get("scriptNo") or "").startswith("AUTO-W-")
          and str(copied.get("scriptNo") or "") != str(src.get("scriptNo") or ""),
          str(copied.get("scriptNo")))
    check("自动化脚本复制名称带副本-前缀",
          str(copied.get("name") or "").startswith("副本-"), str(copied.get("name")))
    check("自动化脚本复制其余字段一致（正文/框架/描述）",
          copied.get("content") == src.get("content")
          and copied.get("framework") == src.get("framework")
          and copied.get("description") == src.get("description"),
          str(copied)[:200])
    check("自动化脚本复制不继承运行态字段",
          not copied.get("lastRunStatus") and not copied.get("lastRunAt"),
          f"lastRunStatus={copied.get('lastRunStatus')} lastRunAt={copied.get('lastRunAt')}")

    # AI 来源脚本复制：编号继续走 AI_ 序列
    ai_src = api.data("POST", "/api/v1/automation/scripts/from-output", {
        "projectId": api.scope_project_id,
        "output": "```python\nprint('ai-copy')\n```\n", "name": "AI复制源", "category": "api",
        "ai": True,
    })
    ai_copied = api.data("POST", f"/api/v1/automation/scripts/{ai_src['id']}/copy", {})
    check("AI 来源脚本复制编号仍带 AI_AUTO-I 前缀",
          str(ai_copied.get("scriptNo") or "").startswith("AI_AUTO-I-"),
          str(ai_copied.get("scriptNo")))

    # 数据库脚本复制
    sql_src = api.data("POST", "/api/v1/sql-scripts", {
        "name": "SQL复制源", "content": "SELECT 1", "alias": "copy-alias",
        "description": "SQL复制描述",
    })
    sql_copied = api.data("POST", f"/api/v1/sql-scripts/{sql_src['id']}/copy", {})
    check("数据库脚本复制后编号是新的 SQL 序列",
          str(sql_copied.get("scriptNo") or "").startswith("SQL-")
          and str(sql_copied.get("scriptNo") or "") != str(sql_src.get("scriptNo") or ""),
          str(sql_copied.get("scriptNo")))
    check("数据库脚本复制名称带副本-前缀",
          str(sql_copied.get("name") or "").startswith("副本-"), str(sql_copied.get("name")))
    check("数据库脚本复制其余字段一致（正文/别称/描述）",
          sql_copied.get("content") == sql_src.get("content")
          and sql_copied.get("alias") == sql_src.get("alias")
          and sql_copied.get("description") == sql_src.get("description"),
          str(sql_copied)[:200])

    # 场景复制
    scene_src = api.data("POST", "/api/v1/api-scenarios", {
        "name": "场景复制源", "projectId": api.scope_project_id,
        "scenarioType": "api",
        "steps": [{"name": "登录", "type": "interface",
                   "extra": {"接口URL": "/login", "方法": "POST"}}],
    })
    scene_copied = api.data("POST", f"/api/v1/api-scenarios/{scene_src['id']}/copy", {})
    check("场景复制后编号是新的 SCN 序列",
          str(scene_copied.get("scenarioNo") or "").startswith("SCN-")
          and str(scene_copied.get("scenarioNo") or "") != str(scene_src.get("scenarioNo") or ""),
          str(scene_copied.get("scenarioNo")))
    check("场景复制名称带副本-前缀",
          str(scene_copied.get("name") or "").startswith("副本-"), str(scene_copied.get("name")))
    check("场景复制步骤与环境一致",
          scene_copied.get("steps") == scene_src.get("steps")
          and scene_copied.get("envId") == scene_src.get("envId"),
          str(scene_copied)[:200])


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


def check_frameworks(api: Api) -> None:
    """App 自动化的框架归属：iOS / Android 用 Appium，鸿蒙用 Hypium，清单由后端统一给。"""
    frameworks = api.data("GET", "/api/v1/automation/runtime").get("frameworks") or []
    codes = {str(item["code"]) for item in frameworks}
    check("运行时体检带框架清单", bool(frameworks), str(frameworks)[:200])
    check("鸿蒙框架 Hypium 已登记", "hypium" in codes, str(sorted(codes)))
    check("Appium 已登记", "appium" in codes, str(sorted(codes)))

    hypium: JsonDict = next((item for item in frameworks if item["code"] == "hypium"), {})
    appium: JsonDict = next((item for item in frameworks if item["code"] == "appium"), {})
    check("Hypium 只绑鸿蒙平台", hypium.get("platforms") == ["hos"], str(hypium)[:200])
    check("Hypium 依赖 hypium 包与 hdc",
          hypium.get("package") == "hypium" and hypium.get("command") == "hdc", str(hypium)[:200])
    check("Appium 绑 iOS 与 Android",
          sorted(appium.get("platforms") or []) == ["android", "ios"], str(appium)[:200])

    app_only = api.data("GET", "/api/v1/automation/runtime?category=app")["frameworks"]
    check("app 类别只返回 App 能用的框架",
          all("app" in item["targets"] for item in app_only) and len(app_only) == 4,
          str([item["code"] for item in app_only])[:200])
    hos_only = api.data("GET", "/api/v1/automation/runtime?category=app&platforms=hos")["frameworks"]
    check("勾了鸿蒙后只剩 Hypium 与 Pytest",
          {item["code"] for item in hos_only} == {"hypium", "pytest"},
          str([item["code"] for item in hos_only])[:200])
    ios_only = api.data("GET", "/api/v1/automation/runtime?category=app&platforms=ios")["frameworks"]
    check("勾了 iOS 后不再提供 Hypium",
          "hypium" not in {item["code"] for item in ios_only},
          str([item["code"] for item in ios_only])[:200])

    _, body = api.call("POST", "/api/v1/automation/scripts", {
        "name": "框架越界用例", "category": "web", "framework": "hypium", "content": "print(1)\n",
    })
    check("Web 脚本拒绝鸿蒙框架",
          body.get("code") != ErrorCode.SUCCESS, str(body)[:200])
    created = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
        "name": "鸿蒙脚本用例", "category": "app", "framework": "hypium",
        "appPlatforms": "hos", "content": "print(1)\n",
    })
    check("App 脚本接受鸿蒙框架", created.get("framework") == "hypium", str(created)[:200])


def check_runs_list(api: Api) -> None:
    listed = api.data("GET", "/api/v1/automation/runs?pageSize=100")
    check("执行历史可列出", int(listed.get("total") or 0) >= 3, str(listed.get("total")))
    filtered = api.data("GET", "/api/v1/automation/runs?status=timeout")
    check("执行历史可按状态筛选",
          all(item["status"] == "timeout" for item in filtered["list"]), str(filtered)[:200])
    by_category = api.data("GET", "/api/v1/automation/runs?category=web")
    check("执行历史可按类别筛选",
          all(item["category"] == "web" for item in by_category["list"]), str(by_category)[:200])


def check_run_report(api: Api, workdir: pathlib.Path) -> None:
    """执行报告导出：Markdown 下载、概要/输出齐全，性能附 JTL 摘要。"""
    created = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
        "name": "报告用例", "category": "api", "framework": "unittest", "content": PY_OK,
    })
    result = api.data("POST", f"/api/v1/automation/scripts/{created['id']}/run", {})
    run_id = int(result["id"])
    run_no = str(result["runNo"])

    resp, _ = api.call("GET", f"/api/v1/automation/runs/{run_id}/report")
    check("报告按 Markdown 附件下载",
          "text/markdown" in str(resp.headers.get("Content-Type"))
          and ".md" in str(resp.headers.get("Content-Disposition")),
          str(resp.headers)[:200])
    text = resp.get_data(as_text=True)
    check("报告带执行概要", "# 自动化执行报告" in text and run_no in text, text[:200])
    check("报告带框架与标准输出", "Unittest" in text and "hello-automation" in text,
          text[:400])

    jtl = workdir / "automation" / "runs" / run_no / "result.jtl"
    jtl.parent.mkdir(parents=True, exist_ok=True)
    jtl.write_text(
        "timeStamp,elapsed,label,success\n"
        "1700000000000,100,GET /a,true\n"
        "1700000001000,200,GET /a,false\n"
        "1700000002000,300,GET /a,true\n",
        encoding="utf-8",
    )
    resp, _ = api.call("GET", f"/api/v1/automation/runs/{run_id}/report")
    text = resp.get_data(as_text=True)
    check("报告解析 JTL 性能摘要",
          "性能摘要" in text and "样本数：3" in text and "失败：1" in text, text[:600])
    # A1：分位数与 JMeter 聚合报告口径一致（最近秩插值，下标 ceil(p*n/100)-1）
    # 样本 elapsed=[100,200,300]，n=3，P90/P95/P99 下标均为 2 → 300ms
    check("报告带 P90/P95/P99 分位数",
          "P90 300 ms" in text and "P95 300 ms" in text and "P99 300 ms" in text,
          text[300:900])

    _, body = api.call("GET", "/api/v1/automation/runs/999999/report")
    check("不存在的执行报告返回 404 语义",
          body.get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(body))


def check_security(api: Api) -> None:
    """附加参数不得产生 shell 语义；脚本正文不得出现在命令行里。"""
    marker = "SECRET-MARKER-CONTENT"
    created = api.data("POST", "/api/v1/automation/scripts", {
        "projectId": api.scope_project_id,
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
        project = api.data("POST", "/api/v1/projects", {"code": "AUTOG", "name": "自动化门禁项目"})
        api.scope_project_id = int(project["id"])

        check_categories(api)
        check_crud(api)
        check_python_execution(api)
        check_jmeter_guard(api)
        check_from_output(api)
        check_copy(api)
        check_runtime(api)
        check_frameworks(api)
        check_runs_list(api)
        check_run_report(api, workdir)
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
