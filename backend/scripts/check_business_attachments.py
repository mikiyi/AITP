"""业务对象持久附件门禁。

覆盖：
- 缺陷 / 用例挂附件：上传、列表回读、文件下载、删除
- 幂等：同对象下相同内容重复上传直接跳过
- 越权：跨项目 projectId、未知 ownerType、空文件都被明确拒绝
- 安全：SVG 按普通附件下载，页面内不预览

    python backend/scripts/check_business_attachments.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import base64
import io
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

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQ"
    "DwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def check(name: str, condition: bool, detail: str = "") -> bool:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
        return True
    _failed += 1
    _failures.append(f"{name}: {detail}")
    print(f"[FAIL] {name}  {detail}")
    return False


class Api:
    def __init__(self, app: Flask) -> None:
        self.client = app.test_client()

    def call(
        self,
        method: str,
        path: str,
        payload: JsonDict | None = None,
        **kwargs: Any,
    ) -> tuple[TestResponse, JsonDict]:
        resp = self.client.open(path, method=method, json=payload, **kwargs)
        body = resp.get_json(silent=True)
        if body is None:
            body = {"code": -1, "message": "非 JSON 响应", "data": None}
        return resp, cast(JsonDict, body)

    def data(
        self,
        method: str,
        path: str,
        payload: JsonDict | None = None,
        **kwargs: Any,
    ) -> Any:
        _, body = self.call(method, path, payload, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(
                f"{method} {path} 期望成功，实际 code={body.get('code')} msg={body.get('message')}"
            )
        return body.get("data")


def build_app(workdir: pathlib.Path) -> Flask:
    db_path = workdir / "check_business_attachments.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8760,
        debug=False,
        db_path=db_path,
        log_path=workdir / "check_business_attachments.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def _upload(
    api: Api,
    owner_type: str,
    owner_id: int,
    project_id: int,
    files: list[tuple[bytes, str, str]],
) -> tuple[TestResponse, JsonDict]:
    payload = [(io.BytesIO(data), name, mime) for data, name, mime in files]
    return api.call(
        "POST",
        "/api/v1/attachments",
        None,
        data={"files": payload},
        content_type="multipart/form-data",
        query_string={
            "ownerType": owner_type,
            "ownerId": str(owner_id),
            "projectId": str(project_id),
        },
    )


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_biz_attach_"))
    try:
        app = build_app(workdir)
        api = Api(app)

        project = api.data("POST", "/api/v1/projects", {
            "code": f"ATT-{id(workdir)}",
            "name": "业务附件门禁项目",
        })
        project_id = int(project["id"])
        other = api.data("POST", "/api/v1/projects", {
            "code": f"ATT-OTHER-{id(workdir)}",
            "name": "另一个项目",
        })
        other_project_id = int(other["id"])
        bug = api.data("POST", "/api/v1/bugs", {
            "projectId": project_id,
            "title": "附件门禁缺陷",
        })
        bug_id = int(bug["id"])
        case = api.data("POST", "/api/v1/test-cases", {
            "projectId": project_id,
            "title": "附件门禁用例",
            "caseType": "functional",
            "priority": "P1",
            "steps": [{"action": "打开页面", "expected": "正常展示"}],
        })
        case_id = int(case["id"])

        created = _upload(api, "bug", bug_id, project_id, [
            (b"log line 1\nlog line 2", "执行日志.txt", "text/plain"),
            (PNG, "失败截图.png", "image/png"),
        ])
        body = created[1]
        check("缺陷附件上传成功", body.get("code") == ErrorCode.SUCCESS, str(body)[:200])
        items = (body.get("data") or {}).get("list") or []
        check("一次上传两个附件", len(items) == 2, str(items)[:200])
        kinds = {item["fileName"]: item["kind"] for item in items}
        check("图片识别为可预览", kinds.get("失败截图.png") == "image", str(kinds))
        check("文本识别为普通附件", kinds.get("执行日志.txt") == "file", str(kinds))
        log_item = next((item for item in items if item["fileName"] == "执行日志.txt"), None)
        check("附件返回下载地址", bool(log_item and log_item.get("url")), str(log_item)[:160])
        log_id = int((log_item or {}).get("id") or 0)
        check("附件返回编号", log_id > 0, str(log_item)[:160])

        listed = api.data(
            "GET",
            f"/api/v1/attachments?ownerType=bug&ownerId={bug_id}&projectId={project_id}",
        )
        check("缺陷附件可回读", listed.get("total") == 2, str(listed)[:200])

        file_resp = api.client.get(
            f"/api/v1/attachments/{log_id}/file?projectId={project_id}"
        )
        check("文本附件内容一致", file_resp.data == b"log line 1\nlog line 2", str(file_resp.status_code))
        check(
            "普通附件按下载返回",
            "attachment" in (file_resp.headers.get("Content-Disposition") or ""),
            file_resp.headers.get("Content-Disposition") or "",
        )
        image_item = next(item for item in items if item["fileName"] == "失败截图.png")
        image_resp = api.client.get(
            f"/api/v1/attachments/{int(image_item['id'])}/file?projectId={project_id}"
        )
        check(
            "图片可在页面内预览",
            "inline" in (image_resp.headers.get("Content-Disposition") or ""),
            image_resp.headers.get("Content-Disposition") or "",
        )

        repeated = _upload(api, "bug", bug_id, project_id, [
            (PNG, "重复截图.png", "image/png"),
        ])
        repeated_data = (repeated[1].get("data") or {})
        check("相同内容重复上传被跳过", len(repeated_data.get("skipped") or []) == 1,
              str(repeated_data)[:200])
        check("重复上传不新增记录", len(repeated_data.get("list") or []) == 0,
              str(repeated_data)[:200])

        cross = _upload(api, "bug", bug_id, other_project_id, [
            (b"x", "越权.txt", "text/plain"),
        ])
        check("跨项目上传被拒", cross[1].get("code") == ErrorCode.RESOURCE_NOT_FOUND,
              str(cross[1])[:200])

        unknown = _upload(api, "unknown_owner", bug_id, project_id, [
            (b"x", "未知.txt", "text/plain"),
        ])
        check("未知归属类型被拒", unknown[1].get("code") == ErrorCode.PARAM_ERROR,
              str(unknown[1])[:200])

        empty = api.call(
            "POST", "/api/v1/attachments", None,
            data={"files": []},
            content_type="multipart/form-data",
            query_string={
                "ownerType": "bug", "ownerId": str(bug_id), "projectId": str(project_id),
            },
        )
        check("空附件被拒", empty[1].get("code") == ErrorCode.PARAM_ERROR, str(empty[1])[:200])

        svg = _upload(api, "bug", bug_id, project_id, [
            (b"<svg xmlns='http://www.w3.org/2000/svg'><script>alert(1)</script></svg>",
             "矢量图.svg", "image/svg+xml"),
        ])
        svg_item = ((svg[1].get("data") or {}).get("list") or [{}])[0]
        check("SVG 标记为普通附件", svg_item.get("kind") == "file", str(svg_item)[:160])
        svg_resp = api.client.get(
            f"/api/v1/attachments/{int(svg_item['id'])}/file?projectId={project_id}"
        )
        check(
            "SVG 只以下载方式返回",
            "attachment" in (svg_resp.headers.get("Content-Disposition") or ""),
            svg_resp.headers.get("Content-Disposition") or "",
        )

        case_attach = _upload(api, "test_case", case_id, project_id, [
            (b"interface payload", "接口报文.json", "application/json"),
        ])
        check("用例也可挂附件", case_attach[1].get("code") == ErrorCode.SUCCESS,
              str(case_attach[1])[:200])

        missing = api.call(
            "GET",
            f"/api/v1/attachments?ownerType=bug&ownerId=999999&projectId={project_id}",
        )
        check("归属对象不存在时明确报错",
              missing[1].get("code") == ErrorCode.RESOURCE_NOT_FOUND, str(missing[1])[:200])

        removed = api.data(
            "DELETE",
            f"/api/v1/attachments/{log_id}?projectId={project_id}",
        )
        check("附件可删除", removed.get("deleted") is True, str(removed)[:160])
        after = api.data(
            "GET",
            f"/api/v1/attachments?ownerType=bug&ownerId={bug_id}&projectId={project_id}",
        )
        names = [item["fileName"] for item in after.get("list") or []]
        check("删除后列表不再返回", "执行日志.txt" not in names, str(names))
        gone = api.call(
            "GET",
            f"/api/v1/attachments/{log_id}/file?projectId={project_id}",
        )
        check("删除后文件不可取回", gone[1].get("code") == ErrorCode.RESOURCE_NOT_FOUND,
              str(gone[1])[:160])

        folder = workdir / "business-attachments" / "bug" / str(bug_id)
        check(
            "附件落在业务附件目录",
            folder.is_dir() and any(path.suffix in {".png", ".svg"} for path in folder.iterdir()),
            str(folder),
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"\n业务附件校验: PASS={_passed} FAIL={_failed}")
    for item in _failures:
        print(f"  [FAIL] {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
