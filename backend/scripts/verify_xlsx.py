"""P7 xlsx 导入导出验收脚本。

断言导出文件严格对齐 D:\\Qoter\\testSKill\\generate_test_cases.py：
- 7 张表的顺序与表头逐格一致
- freeze_panes='A2'、表头填充 DAEEF3、加粗、居中换行、列宽 <= 30
- 优先级列按表头名上色：P0=FFC7CE、P1=FFEB9C，且不会落到「请求方式」列上
- 导出再导入，内容能还原

    python backend/scripts/verify_xlsx.py

输出逐条 [PASS]/[FAIL]，FAIL>0 时退出码为 1。
"""

from __future__ import annotations

import io
import os
import pathlib
import shutil
import sys
import tempfile
from typing import Any, cast

cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from openpyxl import load_workbook  # noqa: E402

from aitp.app import create_app  # noqa: E402
from aitp.config import AppConfig  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.errors import ErrorCode  # noqa: E402
from aitp.services.xlsx_exporter import (  # noqa: E402
    HEADERS_BOUNDARY,
    HEADERS_EXCEPTION,
    HEADERS_FUNCTIONAL,
    HEADERS_INTERFACE,
    HEADERS_MATRIX,
    HEADERS_SECURITY,
    HEADERS_UI,
)
from flask import Flask  # noqa: E402
from werkzeug.test import TestResponse  # noqa: E402

JsonDict = dict[str, Any]

SHEET_ORDER = [
    "功能测试用例",
    "接口测试用例",
    "边界测试用例",
    "权限安全测试用例",
    "UI测试用例",
    "异常场景测试用例",
    "测试覆盖矩阵",
]
EXPECTED_HEADERS = {
    "功能测试用例": HEADERS_FUNCTIONAL,
    "接口测试用例": HEADERS_INTERFACE,
    "边界测试用例": HEADERS_BOUNDARY,
    "权限安全测试用例": HEADERS_SECURITY,
    "UI测试用例": HEADERS_UI,
    "异常场景测试用例": HEADERS_EXCEPTION,
    "测试覆盖矩阵": HEADERS_MATRIX,
}
EXPECTED_HEADER_COUNTS = [16, 19, 18, 17, 16, 17, 4]

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
    db_path = workdir / "verify_xlsx.db"
    init_db(db_path)
    cfg = AppConfig(
        host="127.0.0.1",
        port=8759,
        debug=False,
        db_path=db_path,
        log_path=workdir / "verify_xlsx.log",
        deploy_log_path=workdir / "deploy.log",
        secret_key_path=workdir / ".secret_key",
        prompts_dir=ROOT / "backend" / "prompts",
        static_dir=ROOT / "backend" / "aitp" / "static",
    )
    return create_app(cfg)


def seed_project(api: Api, project_code: str) -> tuple[int, int]:
    """建项目、模块与需求，返回 (projectId, moduleId)。"""
    project = api.data("POST", "/api/v1/projects", {"code": project_code, "name": "示例电商项目"})
    project_id = int(project["id"])
    module = api.data("POST", "/api/v1/modules", {
        "projectId": project_id, "parentId": None, "nodeType": "module", "name": "登录模块",
    })
    module_id = int(module["id"])
    api.data("POST", "/api/v1/requirements", {
        "projectId": project_id, "title": "手机号+验证码登录", "content": "验证码 60 秒有效",
    })
    return project_id, module_id


def seed(api: Api, project_code: str) -> tuple[int, int]:
    """建项目并铺 6 类用例，返回 (projectId, moduleId)。"""
    project_id, module_id = seed_project(api, project_code)

    cases = [
        {
            "caseType": "functional", "title": "验证码登录成功", "priority": "P0",
            "preconditions": "用户未登录",
            "steps": [{"action": "输入手机号", "expected": ""}, {"action": "点击获取验证码", "expected": ""}],
            "expectedResult": "登录成功并跳转首页",
            "extra": {"测试数据": "手机号:13800000000", "执行时间": "2026-11-01 10:00", "备注": "主流程"},
        },
        {
            "caseType": "interface", "title": "获取验证码接口", "priority": "P2",
            "preconditions": "用户已登录",
            "steps": [{"action": "调用 GET /api/sms/code", "expected": ""}],
            "expectedResult": "",
            "extra": {
                "模块/接口名": "登录模块/sms/code", "接口URL": "/api/sms/code", "请求方式": "GET",
                "token/鉴权方式": "Bearer Token", "请求头": "Content-Type: application/json",
                "请求参数": "phone=13800000000", "请求体": "", "预期状态码": "200",
                "预期响应体": "{\"code\":0}", "断言规则": "1.验证code为0", "幂等性校验": "是",
                "执行时间": "2026-11-01 10:05", "备注": "",
            },
        },
        {
            "caseType": "boundary", "title": "验证码超时边界", "priority": "P1",
            "preconditions": "验证码已过期",
            "steps": [{"action": "输入过期验证码", "expected": ""}],
            "expectedResult": "提示验证码已过期",
            "extra": {"边界类型": "时间边界", "边界描述": "超过 60 秒", "测试数据": "过期验证码",
                      "执行时间": "", "备注": ""},
        },
        {
            "caseType": "security", "title": "未登录访问行情", "priority": "P0",
            "preconditions": "用户未登录",
            "steps": [{"action": "直接访问行情页", "expected": ""}],
            "expectedResult": "跳转登录页",
            "extra": {"权限类型": "认证安全", "测试数据": "未登录用户", "执行时间": "", "备注": ""},
        },
        {
            "caseType": "ui", "title": "按钮点击反馈", "priority": "P2",
            "preconditions": "页面加载完成",
            "steps": [{"action": "点击登录按钮", "expected": ""}],
            "expectedResult": "按钮有点击反馈",
            "extra": {"测试数据": "", "执行时间": "", "备注": ""},
        },
        {
            "caseType": "exception", "title": "网络异常提示", "priority": "P1",
            "preconditions": "断网状态",
            "steps": [{"action": "进入登录页", "expected": ""}],
            "expectedResult": "显示网络异常提示",
            "extra": {"异常类型": "网络异常", "测试数据": "无网络", "执行时间": "", "备注": ""},
        },
    ]
    for item in cases:
        payload = {
            "projectId": project_id,
            "moduleId": module_id,
            "title": item["title"],
            "caseType": item["caseType"],
            "priority": item["priority"],
            "preconditions": item["preconditions"],
            "steps": item["steps"],
            "expectedResult": item["expectedResult"],
            "extra": item["extra"],
            "source": "manual",
        }
        api.data("POST", "/api/v1/test-cases", payload)
    return project_id, module_id


def sheet_rows(worksheet: Any) -> list[list[Any]]:
    return [list(row) for row in worksheet.iter_rows(values_only=True)]


def check_structure(workbook: Any) -> None:
    check("工作簿含 7 张表", len(workbook.sheetnames) == 7, str(workbook.sheetnames))
    check("表顺序与源脚本一致", workbook.sheetnames == SHEET_ORDER, str(workbook.sheetnames))
    counts = []
    for name in SHEET_ORDER:
        sheet = workbook[name]
        headers = [cell.value for cell in sheet[1]]
        expected = list(EXPECTED_HEADERS[name])
        check(f"「{name}」表头逐格一致", headers == expected, f"{headers}")
        counts.append(len(headers))
    check("各表表头列数为 16/19/18/17/16/17/4",
          counts == EXPECTED_HEADER_COUNTS, str(counts))

    for name in SHEET_ORDER:
        sheet = workbook[name]
        check(f"「{name}」冻结 A2", sheet.freeze_panes == "A2", str(sheet.freeze_panes))
        header = sheet.cell(row=1, column=1)
        check(f"「{name}」表头填充 DAEEF3",
              header.fill.fgColor.rgb in ("00DAEEF3", "FFDAEEF3", "DAEEF3"),
              str(header.fill.fgColor.rgb))
        check(f"「{name}」表头加粗", bool(header.font.bold), str(header.font.bold))
        check(f"「{name}」表头居中换行",
              header.alignment.horizontal == "center" and bool(header.alignment.wrap_text),
              str(header.alignment))
        widths = [dim.width for dim in sheet.column_dimensions.values() if dim.width]
        check(f"「{name}」列宽不超过 30", all(w <= 30 for w in widths), str(widths))

        data_cell = sheet.cell(row=2, column=1)
        if data_cell.value is not None:
            check(f"「{name}」数据单元格居中换行",
                  data_cell.alignment.horizontal == "center" and bool(data_cell.alignment.wrap_text),
                  str(data_cell.alignment))


def check_priority_color(workbook: Any) -> None:
    sheet = workbook["功能测试用例"]
    headers = [cell.value for cell in sheet[1]]
    index = headers.index("优先级") + 1
    check("功能表 P0 单元格填 FFC7CE",
          sheet.cell(row=2, column=index).fill.fgColor.rgb in ("00FFC7CE", "FFFC7CE", "FFC7CE"),
          str(sheet.cell(row=2, column=index).fill.fgColor.rgb))

    boundary = workbook["边界测试用例"]
    boundary_headers = [cell.value for cell in boundary[1]]
    boundary_index = boundary_headers.index("优先级") + 1
    check("边界表 P1 单元格填 FFEB9C",
          boundary.cell(row=2, column=boundary_index).fill.fgColor.rgb in ("00FFEB9C", "FFFFEB9C", "FFEB9C"),
          str(boundary.cell(row=2, column=boundary_index).fill.fgColor.rgb))
    check("非优先级单元格不上色",
          sheet.cell(row=2, column=1).fill.fill_type in (None, "none"),
          str(sheet.cell(row=2, column=1).fill.fgColor.rgb))

    # 接口表没有优先级列，源脚本会误把「请求方式」列上色；这里断言不上色
    it_sheet = workbook["接口测试用例"]
    it_headers = [cell.value for cell in it_sheet[1]]
    method_index = it_headers.index("请求方式") + 1
    fill = it_sheet.cell(row=2, column=method_index).fill
    check("接口表「请求方式」列不被误上色",
          fill.fill_type in (None, "none"), str(fill.fgColor.rgb))


def compare_workbooks(left: Any, right: Any) -> None:
    for name in SHEET_ORDER:
        left_rows = sheet_rows(left[name])
        right_rows = sheet_rows(right[name])
        check(f"「{name}」导出再导入内容一致", left_rows == right_rows,
              f"{left_rows[:2]} != {right_rows[:2]}")


def main() -> int:
    workdir = pathlib.Path(tempfile.mkdtemp(prefix="aitp_xlsx_"))
    try:
        app = build_app(workdir)
        api = Api(app)
        project_id, _ = seed(api, "XL-EC")

        resp, _ = api.call("GET", f"/api/v1/test-cases/export?projectId={project_id}")
        content = resp.get_data()
        check("导出返回 xlsx 二进制", content[:2] == b"PK", str(content[:8]))
        check("导出响应带附件头",
              "attachment" in resp.headers.get("Content-Disposition", ""),
              resp.headers.get("Content-Disposition", ""))

        workbook = load_workbook(filename=io.BytesIO(content))
        check_structure(workbook)
        check_priority_color(workbook)

        # 导出再导入到另一个项目，内容应能还原
        project_b, _ = seed_project(api, "XL-EC-2")
        imported = api.data(
            "POST", "/api/v1/test-cases/import",
            None,
            data={"file": (io.BytesIO(content), "cases.xlsx"), "projectId": str(project_b)},
            content_type="multipart/form-data",
        )
        check("导入条数与导出一致", int(imported.get("created", 0)) == 6, str(imported))

        resp_b, _ = api.call("GET", f"/api/v1/test-cases/export?projectId={project_b}")
        workbook_b = load_workbook(filename=io.BytesIO(resp_b.get_data()))
        compare_workbooks(workbook, workbook_b)

        # 编号前缀统计
        rows = api.data("GET", f"/api/v1/test-cases?projectId={project_b}&pageSize=100")
        numbers = [str(item.get("caseNo")) for item in rows["list"]]
        prefixes = {number.split("-")[0] for number in numbers}
        check("编号前缀覆盖 FT/IT/BT/ST/UIT/ET",
              prefixes == {"FT", "IT", "BT", "ST", "UIT", "ET"}, str(sorted(prefixes)))

        # 非 xlsx 文件应被拒绝
        _, body = api.call(
            "POST", "/api/v1/test-cases/import",
            None,
            data={"file": (io.BytesIO(b"hello"), "bad.txt"), "projectId": str(project_b)},
            content_type="multipart/form-data",
        )
        check("非 xlsx 文件被拒绝", body.get("code") == ErrorCode.PARAM_ERROR, str(body))
    finally:
        shutil.rmtree(workdir, ignore_errors=True)

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    if _failures:
        print("失败项：")
        for item in _failures:
            print(f"  - {item}")
    return 1 if _failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
