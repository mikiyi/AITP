"""Jira 只读导入适配器（三期 GAP-40）。

只读单向：只用 GET /rest/api/* 拉取 issue，绝不写回。
认证默认 Jira Cloud 的 Basic(email + API Token)；Jira Server 可用
extra.authMode="bearer"（把 Personal Access Token 填在口令位）。
issue 类型（需求/用例/缺陷）在 extra.requirementTypes/caseTypes/bugTypes 配置，
Jira 原生没有测试用例，需配合 Xray/Zephyr 等插件的 issue 类型。
"""

from __future__ import annotations

from typing import Any

import httpx

from ...errors import AppError, ErrorCode

DEFAULT_PATHS: dict[str, str] = {
    "search": "/rest/api/3/search",
}

FIELDS = "summary,description,priority,status,issuetype,labels,created,updated"


def _fail(message: str) -> AppError:
    return AppError(ErrorCode.PARAM_ERROR, message)


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _adf_text(node: Any) -> str:
    """Jira Cloud 描述是 ADF 文档；递归取 text 节点，块级节点换行。"""
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        return "\n".join(part for part in (_adf_text(item) for item in node) if part)
    if not isinstance(node, dict):
        return ""
    if isinstance(node.get("text"), str):
        return str(node["text"])
    text = _adf_text(node.get("content"))
    if text and node.get("type") in ("paragraph", "heading", "listItem", "codeBlock", "blockquote"):
        return text + "\n"
    return text


def _type_names(raw: Any, default: list[str]) -> list[str]:
    if isinstance(raw, list):
        values = [str(item).strip() for item in raw]
    else:
        values = [item.strip() for item in str(raw or "").split(",")]
    values = [item for item in values if item]
    return values or default


class JiraClient:
    """只读 Jira 客户端；退出时记得 close()。"""

    SUPPORTS = ("requirements", "cases", "bugs")

    def __init__(
        self, *, base_url: str, account: str, password: str,
        server_user: str = "", server_password: str = "",
        paths: dict[str, str] | None = None, verify_tls: bool = True,
        extra: dict[str, Any] | None = None, timeout: float = 20.0,
    ) -> None:
        base = base_url.strip().rstrip("/")
        if not base.startswith(("http://", "https://")):
            raise _fail("Jira 地址必须以 http:// 或 https:// 开头")
        if not account and not password:
            raise _fail("请配置 Jira 邮箱与 API Token")
        cfg = extra or {}
        self.base_url = base
        self._paths = {**DEFAULT_PATHS, **(paths or {})}
        self._bearer = str(cfg.get("authMode") or "").lower() == "bearer"
        self._types = {
            "requirements": _type_names(cfg.get("requirementTypes"), ["Story", "Requirement"]),
            "cases": _type_names(cfg.get("caseTypes"), []),
            "bugs": _type_names(cfg.get("bugTypes"), ["Bug"]),
        }
        headers = {"Authorization": f"Bearer {password}"} if self._bearer else {}
        auth = None if self._bearer else httpx.BasicAuth(account, password)
        self._client = httpx.Client(
            base_url=base, timeout=timeout, trust_env=False, auth=auth,
            verify=verify_tls, headers=headers,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "JiraClient":
        return self

    def __exit__(self, *_args: Any) -> None:
        self.close()

    def _search(self, project_key: str, kinds: str, limit: int) -> list[dict[str, Any]]:
        if not project_key:
            raise _fail("请配置 Jira 项目 Key")
        types = self._types[kinds]
        if not types:
            return []
        quoted = ", ".join(f'"{name}"' for name in types)
        jql = f"project = \"{project_key}\" AND issuetype in ({quoted}) ORDER BY key ASC"
        items: list[dict[str, Any]] = []
        start = 0
        page = min(100, limit)
        while len(items) < limit:
            response = self._client.get(
                self._paths["search"],
                params={"jql": jql, "startAt": start, "maxResults": page, "fields": FIELDS},
            )
            if response.status_code == 401:
                raise _fail("Jira 认证失败（401）：请核对邮箱与 API Token 是否匹配")
            if response.status_code >= 400:
                snippet = response.text[:200].replace("\n", " ")
                raise _fail(f"Jira 查询失败（HTTP {response.status_code}）：{snippet}")
            try:
                data = response.json()
            except ValueError:
                raise _fail("Jira 返回不是 JSON") from None
            batch = [item for item in (data.get("issues") or []) if isinstance(item, dict)]
            items.extend(self._normalize(item) for item in batch)
            if not batch or len(batch) < page:
                break
            start += len(batch)
        return items[:limit]

    def _normalize(self, issue: dict[str, Any]) -> dict[str, Any]:
        raw_fields = issue.get("fields")
        fields: dict[str, Any] = raw_fields if isinstance(raw_fields, dict) else {}
        priority = _as_dict(fields.get("priority"))
        status = _as_dict(fields.get("status"))
        issuetype = _as_dict(fields.get("issuetype"))
        raw = dict(issue)
        raw.update({
            "key": issue.get("key"),
            "summary": fields.get("summary"),
            "descriptionText": _adf_text(fields.get("description")).strip(),
            "priorityName": priority.get("name"),
            "statusName": status.get("name"),
            "issueTypeName": issuetype.get("name"),
        })
        return raw

    def fetch_requirements(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        return self._search(product_id, "requirements", limit)

    def fetch_cases(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        return self._search(product_id, "cases", limit)

    def fetch_bugs(self, product_id: str, limit: int) -> list[dict[str, Any]]:
        return self._search(product_id, "bugs", limit)

    def external_id(self, raw: dict[str, Any]) -> str:
        return str(raw.get("key") or raw.get("id") or "")

    def url_for(self, kind: str, external_id: str) -> str:
        return f"{self.base_url}/browse/{external_id}"


def default_mapping() -> dict[str, Any]:
    return {
        "requirement": {
            "title": "summary",
            "content": "descriptionText",
            "priority": "priorityName",
            "status": "statusName",
        },
        "case": {
            "title": "summary",
            "preconditions": "descriptionText",
            "steps": "descriptionText",
            "priority": "priorityName",
            "status": "statusName",
        },
        "bug": {
            "title": "summary",
            "description": "descriptionText",
            "severity": "priorityName",
            "priority": "priorityName",
            "status": "statusName",
        },
    }
