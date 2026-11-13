"""TestLink 只读导入适配器（三期 GAP-40）。

只读单向：只用 XML-RPC 查询接口（tl.getRequirements / tl.getTestCasesForTestPlan），
不做任何写操作。认证用 TestLink 账号里的 API devKey（填在口令位）。
TestLink 自身不管理缺陷（缺陷在 Jira/Mantis 等外部跟踪器），因此不提供缺陷导入。
"""

from __future__ import annotations

import xmlrpc.client
from typing import Any

import httpx

from ...errors import AppError, ErrorCode

DEFAULT_PATHS: dict[str, str] = {"xmlrpc": "/lib/api/xmlrpc.php"}


def _fail(message: str) -> AppError:
    return AppError(ErrorCode.PARAM_ERROR, message)


class TestLinkClient:
    """只读 TestLink 客户端；退出时记得 close()。"""

    SUPPORTS = ("requirements", "cases")

    def __init__(
        self, *, base_url: str, account: str, password: str,
        server_user: str = "", server_password: str = "",
        paths: dict[str, str] | None = None, verify_tls: bool = True,
        extra: dict[str, Any] | None = None, timeout: float = 20.0,
    ) -> None:
        base = base_url.strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise _fail("TestLink 地址必须以 http:// 或 https:// 开头")
        if not password:
            raise _fail("请填写 TestLink API devKey（在账号 API 接口里生成）")
        cfg = extra or {}
        self.base_url = base
        self._paths = {**DEFAULT_PATHS, **(paths or {})}
        self._dev_key = password
        self._user = account
        self._plan_id = str(cfg.get("testPlanId") or "").strip()
        self._project_id = ""
        self._client = httpx.Client(
            base_url=base, timeout=timeout, trust_env=False, verify=verify_tls,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "TestLinkClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        payload = xmlrpc.client.dumps(
            ({**params, "devKey": self._dev_key},), methodname=method, encoding="utf-8",
        )
        response = self._client.post(
            self._paths["xmlrpc"], content=payload.encode("utf-8"),
            headers={"Content-Type": "text/xml; charset=utf-8"},
        )
        if response.status_code == 401:
            raise _fail("TestLink 认证失败（401）：请核对地址与 devKey")
        if response.status_code >= 400:
            raise _fail(f"TestLink 接口失败（HTTP {response.status_code}）")
        try:
            values, _method = xmlrpc.client.loads(response.content)
        except xmlrpc.client.Fault as exc:
            raise _fail(f"TestLink 调用 {method} 失败：{exc.faultString}") from None
        except (ValueError, xmlrpc.client.ResponseError):
            raise _fail("TestLink 返回不是合法 XML-RPC 响应") from None
        return values[0] if values else None

    def _require_project(self) -> int:
        if not self._project_id:
            raise _fail("请配置 TestLink 测试项目 ID")
        try:
            return int(self._project_id)
        except ValueError:
            raise _fail("TestLink 测试项目 ID 必须是数字") from None

    def fetch_requirements(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        self._project_id = product_id
        project = self._require_project()
        data = self._call("tl.getRequirements", {"testprojectid": project})
        rows: list[dict[str, Any]] = []
        self._flatten_requirements(data, rows)
        return rows[:limit]

    def _flatten_requirements(self, node: Any, out: list[dict[str, Any]]) -> None:
        if isinstance(node, list):
            for item in node:
                self._flatten_requirements(item, out)
            return
        if not isinstance(node, dict):
            return
        if node.get("id"):
            out.append(node)
        children = node.get("children")
        if children:
            self._flatten_requirements(children, out)

    def fetch_cases(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        self._project_id = product_id
        self._require_project()
        if not self._plan_id:
            raise _fail("请配置 TestLink 测试计划 ID（用例挂在测试计划下）")
        try:
            plan_id = int(self._plan_id)
        except ValueError:
            raise _fail("TestLink 测试计划 ID 必须是数字") from None
        data = self._call("tl.getTestCasesForTestPlan", {"testplanid": plan_id})
        rows = self._case_rows(data)
        for row in rows[:limit]:
            self._enrich_case(row)
        return rows[:limit]

    @staticmethod
    def _case_rows(data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if not isinstance(data, dict):
            return []
        rows: list[dict[str, Any]] = []
        for key, item in data.items():
            entries = item if isinstance(item, list) else [item]
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                row = dict(entry)
                row.setdefault("testcase_id", str(key))
                rows.append(row)
        return rows

    def _enrich_case(self, row: dict[str, Any]) -> None:
        case_id = str(row.get("testcase_id") or row.get("id") or "")
        if not case_id:
            return
        try:
            data = self._call("tl.getTestCase", {"testcaseid": int(case_id)})
        except AppError:
            return
        versions = data if isinstance(data, list) else [data]
        latest = next((item for item in reversed(versions) if isinstance(item, dict)), None)
        if not latest:
            return
        steps = latest.get("steps")
        actions: list[str] = []
        expects: list[str] = []
        if isinstance(steps, list):
            for step in steps:
                if not isinstance(step, dict):
                    continue
                action = str(step.get("actions") or "").strip()
                expect = str(step.get("expected_results") or "").strip()
                if action:
                    actions.append(action)
                if expect:
                    expects.append(expect)
        row.update({
            "name": latest.get("name") or row.get("name") or row.get("tcase_name"),
            "summary": latest.get("summary") or row.get("summary"),
            "importance": latest.get("importance") or row.get("importance"),
            "stepsText": "\n".join(actions),
            "expectText": "\n".join(expects),
        })

    def fetch_bugs(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        return []

    def external_id(self, raw: dict[str, Any]) -> str:
        return str(raw.get("testcase_id") or raw.get("id") or "")

    def url_for(self, kind: str, external_id: str) -> str:
        if kind == "requirement":
            return f"{self.base_url}/lib/requirements/reqView.php?requirement_id={external_id}"
        return (
            f"{self.base_url}/linkto.php?tprojectId={self._project_id}"
            f"&testcaseId={external_id}"
        )


def default_mapping() -> dict[str, Any]:
    return {
        "requirement": {
            "title": "title",
            "content": "scope",
            "status": "status",
        },
        "case": {
            "title": "name",
            "preconditions": "summary",
            "steps": "stepsText",
            "expectedResult": "expectText",
            "priority": "importance",
            "status": "status",
        },
        "bug": {
            "title": "title",
            "description": "description",
            "priority": "priority",
        },
    }
