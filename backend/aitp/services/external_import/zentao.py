"""禅道（Zentao）只读导入适配器（三期 GAP-40）。

只读单向：登录取 token 后只发 GET 拉取数据，绝不 PUT/POST/DELETE 业务数据。
禅道前面常有一层服务器级 HTTP Basic（与本机登录口令不同源），配置分开存：
  - serverUser / serverPassword：HTTP Basic（如 runner/反向代理防护层）
  - account / password：禅道应用账号（POST /api.php/v1/tokens 换 token）

端点路径允许在 extra.paths 里覆盖，兼容不同版本/反向代理。
"""

from __future__ import annotations

from typing import Any

import httpx

from ...errors import AppError, ErrorCode
from .mapping import apply_mapping  # noqa: F401  （服务层按平台复用）

#: 禅道 REST v1 默认路径；extra.paths 可按实例版本覆盖
DEFAULT_PATHS: dict[str, str] = {
    "tokens": "/api.php/v1/tokens",
    "stories": "/api.php/v1/products/{product_id}/stories",
    "testcases": "/api.php/v1/products/{product_id}/testcases",
    "bugs": "/api.php/v1/products/{product_id}/bugs",
}


def _fail(message: str) -> AppError:
    return AppError(ErrorCode.PARAM_ERROR, message)


class ZentaoClient:
    """禅道只读客户端；退出时记得 close()。"""

    SUPPORTS = ("requirements", "cases", "bugs")

    def __init__(
        self, *, base_url: str, account: str, password: str,
        server_user: str = "", server_password: str = "",
        paths: dict[str, str] | None = None, verify_tls: bool = True,
        extra: dict[str, Any] | None = None, timeout: float = 15.0,
    ) -> None:
        base = base_url.strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise _fail("禅道地址必须以 http:// 或 https:// 开头")
        auth = None
        if server_user or server_password:
            auth = httpx.BasicAuth(server_user, server_password)
        self.base_url = base
        self._paths = paths or {}
        # trust_env=False：本机 NO_PROXY 里可能有 httpx 解析不了的 IPv6 字面量
        # verify_tls 默认开启；内网自签名实例需用户显式勾选才跳过证书校验
        self._client = httpx.Client(
            base_url=base, timeout=timeout, trust_env=False, auth=auth, verify=verify_tls,
        )
        self._account = account
        self._password = password
        self._token = ""

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "ZentaoClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _path(self, key: str, **kwargs: Any) -> str:
        template = self._paths.get(key) or DEFAULT_PATHS[key]
        return template.format(**kwargs)

    def login(self) -> str:
        if not self._account or not self._password:
            raise _fail("请配置禅道账号与密码")
        response = self._client.post(
            self._path("tokens"),
            json={"account": self._account, "password": self._password},
        )
        if response.status_code == 401:
            raise _fail(
                "认证失败（401）：请核对禅道账号密码；"
                "若站点前还有服务器级 HTTP Basic，请填「服务器认证」一栏",
            )
        if response.status_code >= 400:
            raise _fail(f"禅道登录失败（HTTP {response.status_code}）")
        try:
            data = response.json()
        except ValueError:
            raise _fail("禅道登录响应不是 JSON") from None
        token = (
            data.get("token")
            or (data.get("data") or {}).get("token")
            or data.get("sessionID")
        )
        if not token:
            raise _fail("禅道登录响应里没有 token")
        self._token = str(token)
        return self._token

    def _headers(self) -> dict[str, str]:
        return {"Token": self._token, "Authorization": f"Token {self._token}"}

    def _get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        if not self._token:
            self.login()
        response = self._client.get(path, params=params, headers=self._headers())
        if response.status_code == 401:
            self._token = ""
            self.login()
            response = self._client.get(path, params=params, headers=self._headers())
        if response.status_code == 401:
            raise _fail("读取失败（401）：token 失效或权限不足")
        if response.status_code >= 400:
            raise _fail(f"读取失败（HTTP {response.status_code}）：{path}")
        try:
            return response.json()
        except ValueError:
            raise _fail(f"禅道返回不是 JSON：{path}") from None

    def _items(self, data: Any) -> list[dict[str, Any]]:
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("items", "stories", "testcases", "bugs", "list", "data"):
                items = data.get(key)
                if isinstance(items, list):
                    return [item for item in items if isinstance(item, dict)]
        return []

    def fetch_requirements(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        return self._fetch("stories", product_id, limit)

    def fetch_cases(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        return self._fetch("testcases", product_id, limit)

    def fetch_bugs(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        return self._fetch("bugs", product_id, limit)

    def _fetch(self, key: str, product_id: str, limit: int) -> list[dict[str, Any]]:
        if not product_id:
            raise _fail("请配置禅道产品 ID")
        data = self._get(self._path(key, product_id=product_id), params={"limit": limit})
        return self._items(data)[:limit]

    def url_for(self, kind: str, external_id: str) -> str:
        """源平台详情页链接（只读引用，不回写）。kind 为单数 requirement/case/bug。"""
        view = {"requirement": "story", "case": "testcase", "bug": "bug"}[kind]
        return f"{self.base_url}/{view}/view/{external_id}.html"

    def external_id(self, raw: dict[str, Any]) -> str:
        return str(raw.get("id") or "")


def default_mapping() -> dict[str, Any]:
    """默认映射：AITP 字段 → 禅道字段路径（点号 / [] / |）。"""
    return {
        "requirement": {
            "title": "title",
            "content": "story|description|spec",
            "priority": "pri",
            "status": "status",
        },
        "case": {
            "title": "title",
            "preconditions": "precondition|preconditions",
            "steps": "steps[].content",
            "expectedResult": "steps[].expect",
            "priority": "pri",
            "status": "status",
        },
        "bug": {
            "title": "title",
            "description": "steps|description",
            "severity": "severity",
            "priority": "pri",
            "status": "status",
        },
    }
