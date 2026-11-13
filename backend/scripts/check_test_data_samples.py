"""测试数据样本池与脱敏门禁（三期 GAP-39）。

断言四件事：
  - 敏感列必须配规则，漏配直接拒绝导入；
  - 落盘的是脱敏副本，目录里没有原始样本，下载结果不含原始 PII；
  - 方案可保存、复用、版本化，被样本引用时不能删；
  - 保留期到期后清理副本与记录。

    python backend/scripts/check_test_data_samples.py
"""

from __future__ import annotations

import io
import json
import pathlib
import shutil
import sqlite3
import sys
import tempfile
from typing import Any

sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402

V1 = "/api/v1"
_passed = 0
_failed = 0


def check(name: str, condition: bool, detail: str = "") -> None:
    global _passed, _failed
    if condition:
        _passed += 1
        print(f"[PASS] {name}")
    else:
        _failed += 1
        print(f"[FAIL] {name}  {detail}")


class Api:
    def __init__(self, app: Any) -> None:
        self.client = app.test_client()

    def call(self, method: str, path: str, **kwargs: Any) -> tuple[int, dict[str, Any]]:
        resp = self.client.open(path, method=method, **kwargs)
        return resp.status_code, resp.get_json(silent=True) or {}

    def ok(self, method: str, path: str, **kwargs: Any) -> Any:
        _, body = self.call(method, path, **kwargs)
        if body.get("code") != ErrorCode.SUCCESS:
            raise AssertionError(f"{method} {path} -> {body.get('code')} {body.get('message')}")
        return body.get("data")

    def upload(self, path: str, data: dict[str, str], filename: str,
               content: bytes) -> tuple[int, dict[str, Any]]:
        return self.call("POST", path, data={**data, "file": (io.BytesIO(content), filename)},
                         content_type="multipart/form-data")


def build_app(workdir: pathlib.Path) -> Any:
    db_path = workdir / "sample.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1", port=8760, debug=False, db_path=db_path,
        log_path=workdir / "sample.log", deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key", prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp-samples-"))
    try:
        api = Api(build_app(workdir))
        project = api.ok("POST", f"{V1}/projects", json={"code": "MASK", "name": "脱敏项目"})
        project_id = int(project["id"])
        base = f"{V1}/test-data"

        scheme = api.ok("POST", f"{base}/mask-schemes", json={
            "projectId": project_id,
            "name": "标准脱敏",
            "isDefault": True,
            "rules": {
                "phone": {"type": "mask", "keepPrefix": 3, "keepSuffix": 4},
                "email": {"type": "hash", "length": 12},
                "password": {"type": "redact", "replacement": "[已脱敏]"},
            },
        })
        scheme_id = int(scheme["id"])
        check("方案可保存并带版本", scheme["version"] == 1 and scheme["isDefault"] is True)
        listing = api.ok("GET", f"{base}/mask-schemes?projectId={project_id}")
        check("方案列表可读", len(listing["list"]) == 1)

        updated = api.ok("PUT", f"{base}/mask-schemes/{scheme_id}", json={
            "projectId": project_id,
            "rules": {
                "phone": {"type": "hash", "length": 16},
                "email": {"type": "hash", "length": 12},
                "password": {"type": "redact", "replacement": "[已脱敏]"},
            },
        })
        check("方案修改后版本递增", updated["version"] == 2)

        csv = ("phone,email,password,amount\n"
               "13800138000,a@b.com,s3cret,10.5\n"
               "13900139000,c@d.com,p@ss,20\n").encode("utf-8")
        status, body = api.upload(f"{base}/samples", {
            "projectId": str(project_id), "name": "订单样本", "schemeId": str(scheme_id),
            "retentionDays": "30",
        }, "orders.csv", csv)
        sample = body.get("data") or {}
        check("样本导入成功", status == 200 and body.get("code") == ErrorCode.SUCCESS
              and sample.get("rowCount") == 2, str(body)[:200])
        check("自动识别敏感列", set((sample.get("detection") or {}).keys())
              >= {"phone", "email", "password"}, str(sample.get("detection")))
        check("样本记住方案版本", sample.get("schemeId") == scheme_id
              and sample.get("schemeVersion") == 2, str(sample.get("schemeVersion")))

        sample_id = int(sample["id"])
        resp = api.client.get(f"{base}/samples/{sample_id}/download?projectId={project_id}&format=json")
        text = resp.get_data(as_text=True)
        check("下载不含原始 PII", all(secret not in text for secret in
                                     ("13800138000", "a@b.com", "s3cret")), text[:200])
        check("非敏感列保持原值", "10.5" in text and "amount" in text, text[:200])
        masked_dir = workdir / "samples" / str(sample_id)
        check("落盘只有脱敏副本", masked_dir.is_dir()
              and [p.name for p in masked_dir.iterdir()] == ["masked.json"],
              str(list(masked_dir.glob("*"))))

        bad = api.ok("POST", f"{base}/mask-schemes", json={
            "projectId": project_id, "name": "漏配方案",
            "rules": {"phone": {"type": "mask", "keepPrefix": 3, "keepSuffix": 4}},
        })
        status, body = api.upload(f"{base}/samples", {
            "projectId": str(project_id), "name": "应被拒", "schemeId": str(bad["id"]),
        }, "orders.csv", csv)
        check("敏感列漏配被拒绝", body.get("code") == ErrorCode.PARAM_ERROR
              and "email" in str(body.get("message")), str(body)[:200])

        status, body = api.call("DELETE", f"{base}/mask-schemes/{scheme_id}?projectId={project_id}")
        check("被样本引用的方案不能删", body.get("code") == ErrorCode.RESOURCE_REFERENCED,
              str(body)[:160])

        payload = json.dumps([{"phone": "13700137000", "note": "ok"}]).encode("utf-8")
        status, body = api.upload(f"{base}/samples", {
            "projectId": str(project_id), "name": "JSON 样本",
        }, "rows.json", payload)
        check("JSON 样本可导入并自动脱敏", body.get("code") == ErrorCode.SUCCESS
              and "13700137000" not in str(body.get("data")), str(body)[:200])

        from openpyxl import Workbook

        workbook = Workbook()
        sheet = workbook.active
        sheet.append(["phone", "note"])
        sheet.append(["13600136000", "x"])
        buffer = io.BytesIO()
        workbook.save(buffer)
        status, body = api.upload(f"{base}/samples", {
            "projectId": str(project_id), "name": "XLSX 样本",
        }, "rows.xlsx", buffer.getvalue())
        check("XLSX 样本可导入并自动脱敏", body.get("code") == ErrorCode.SUCCESS
              and (body.get("data") or {}).get("rowCount") == 1
              and "13600136000" not in str(body.get("data")), str(body)[:200])

        status, body = api.upload(f"{base}/samples", {
            "projectId": str(project_id), "name": "坏格式",
        }, "rows.txt", b"phone\n13800138000\n")
        check("不支持的格式被拒绝", body.get("code") == ErrorCode.PARAM_ERROR, str(body)[:160])

        conn = sqlite3.connect(workdir / "sample.db")
        conn.execute("UPDATE biz_test_data_sample SET expires_at = '2000-01-01T00:00:00Z'")
        conn.commit()
        conn.close()
        cleaned = api.ok("POST", f"{base}/samples/cleanup", json={"projectId": project_id})
        check("过期样本被清理", cleaned["count"] >= 1, str(cleaned))
        remaining = api.ok("GET", f"{base}/samples?projectId={project_id}")
        check("清理后列表为空", remaining["list"] == [], str(remaining["list"]))
        check("清理后副本目录已删", not masked_dir.exists(), str(masked_dir))

        api.ok("DELETE", f"{base}/mask-schemes/{scheme_id}?projectId={project_id}")
        check("样本删完后方案可删除", True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print(f"测试数据样本池：PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
