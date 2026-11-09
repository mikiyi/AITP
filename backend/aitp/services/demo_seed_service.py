"""示例项目（DEMO-EC）数据装配：CLI seed_demo.py 与产品内注入 API 共用。

只写库，不碰配置；知识条目会同步落盘 Markdown（knowledge_storage）。
seed() 是回归基线数据：check_db.py 与 check_walkthrough.py 直接 import 使用，
改签名或语义会破门禁；追加注入走 seed_append()，只重建示例项目自身。
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from typing import cast

from ..common.security import content_checksum
from ..common.time_utils import utcnow_iso
from . import knowledge_storage


def seed_append(conn: sqlite3.Connection) -> dict[str, int]:
    """追加式注入：只清示例项目自有数据后重建，其他项目一行不动。"""
    stale = _clear_demo_rows(conn, _demo_project_ids(conn))
    counts = seed(conn)
    conn.commit()
    _drop_stale_mirror_files(conn, stale)
    return counts


def _demo_project_ids(conn: sqlite3.Connection) -> list[int]:
    """示例项目（含软删）的 id 列表；追加模式只动这些 id 名下的数据。"""
    return [
        int(row[0]) for row in conn.execute(
            "SELECT id FROM biz_project WHERE code = ?", (DEMO_CODE,)
        )
    ]


def _clear_demo_rows(conn: sqlite3.Connection, project_ids: list[int]) -> list[str]:
    """只清示例项目自有数据（追加模式用），其他项目一行不动。

    返回清空前需要删除的落盘知识文件。报告模板 code='standard' 不清：
    用户可能改过它，追加模式用「不存在才插」保住用户改动。

    老示例项目可能被早期版本或其他功能挂过额外资产（待办、回归、计划、轮次、
    准出、SQL 分析、测试数据批次、AI 任务/记录等），清库时按外键依赖逆序全覆盖，
    否则重建会因 FOREIGN KEY constraint failed 半途失败。
    """
    if not project_ids:
        return []
    marks = ",".join("?" for _ in project_ids)

    def _ids(sql: str, params: list[int]) -> list[int]:
        return [int(row[0]) for row in conn.execute(sql, params)]

    def _m(values: list[int]) -> str:
        return ",".join("?" for _ in values) if values else "NULL"

    def _del(sql: str, params: list[int]) -> None:
        conn.execute(sql, params)

    module_ids = _ids(f"SELECT id FROM biz_module WHERE project_id IN ({marks})", project_ids)  # noqa: S608
    article_ids = _ids(f"SELECT id FROM biz_knowledge_article WHERE project_id IN ({marks})", project_ids)  # noqa: S608
    stale_files = [
        str(row[0]) for row in conn.execute(
            f"SELECT file_path FROM biz_knowledge_article WHERE project_id IN ({marks})"  # noqa: S608
            " AND file_path IS NOT NULL AND file_path <> ''",
            project_ids,
        )
    ]
    case_ids = _ids(f"SELECT id FROM biz_test_case WHERE project_id IN ({marks})", project_ids)  # noqa: S608
    bug_ids = _ids(f"SELECT id FROM biz_bug WHERE project_id IN ({marks})", project_ids)  # noqa: S608
    report_ids = _ids(f"SELECT id FROM biz_report WHERE project_id IN ({marks})", project_ids)  # noqa: S608
    script_ids = _ids(f"SELECT id FROM biz_automation_script WHERE project_id IN ({marks})", project_ids)  # noqa: S608
    session_ids = _ids(
        f"SELECT DISTINCT session_id FROM biz_knowledge_article"  # noqa: S608
        f" WHERE id IN ({_m(article_ids)}) AND session_id IS NOT NULL",
        article_ids,
    )
    run_ids = _ids(
        f"SELECT id FROM biz_test_run WHERE project_id IN ({marks})", project_ids  # noqa: S608
    )
    regression_ids = _ids(
        f"SELECT id FROM biz_regression WHERE project_id IN ({marks})", project_ids  # noqa: S608
    )
    batch_ids = _ids(
        f"SELECT id FROM biz_test_data_batch WHERE project_id IN ({marks})", project_ids  # noqa: S608
    )
    task_ids = _ids(
        f"SELECT id FROM biz_ai_task WHERE module_id IN ({_m(module_ids)})", module_ids  # noqa: S608
    )

    # 示例项目是整体重建：删除集合封闭在 DEMO-EC 名下，事务内暂关外键检查，
    # 避免早期版本挂过的资产（版本/计划/轮次/准出/AI 步骤等）按声明顺序删不干净
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        # 依赖末梢先行
        _del(f"DELETE FROM biz_ai_step WHERE task_id IN ({_m(task_ids)}) OR parent_task_id IN ({_m(task_ids)})", task_ids + task_ids)  # noqa: S608
        _del(f"DELETE FROM biz_ai_record WHERE task_id IN ({_m(task_ids)}) OR session_id IN ({_m(session_ids)})", task_ids + session_ids)  # noqa: S608
        _del(f"DELETE FROM biz_ai_task WHERE module_id IN ({_m(module_ids)}) OR session_id IN ({_m(session_ids)})", module_ids + session_ids)  # noqa: S608
        _del(f"DELETE FROM biz_case_execution WHERE case_id IN ({_m(case_ids)}) OR bug_id IN ({_m(bug_ids)}) OR run_id IN ({_m(run_ids)})", case_ids + bug_ids + run_ids)  # noqa: S608
        _del(f"DELETE FROM biz_release_gate WHERE project_id IN ({marks}) OR run_id IN ({_m(run_ids)})", project_ids + run_ids)  # noqa: S608
        _del(f"DELETE FROM biz_test_run_case WHERE run_id IN ({_m(run_ids)}) OR case_id IN ({_m(case_ids)})", run_ids + case_ids)  # noqa: S608
        _del(f"DELETE FROM biz_test_run WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_test_plan WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_regression_item WHERE regression_id IN ({_m(regression_ids)})", regression_ids)  # noqa: S608
        _del(f"DELETE FROM biz_regression WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_test_data_file WHERE batch_id IN ({_m(batch_ids)})", batch_ids)  # noqa: S608
        _del(f"DELETE FROM biz_test_data_batch WHERE project_id IN ({marks}) OR module_id IN ({_m(module_ids)})", project_ids + module_ids)  # noqa: S608
        _del(f"DELETE FROM biz_todo WHERE project_id IN ({marks}) OR module_id IN ({_m(module_ids)})", project_ids + module_ids)  # noqa: S608
        _del(f"DELETE FROM biz_sql_analysis WHERE project_id IN ({marks}) OR module_id IN ({_m(module_ids)})", project_ids + module_ids)  # noqa: S608
        _del(f"DELETE FROM biz_report_item WHERE report_id IN ({_m(report_ids)})", report_ids)  # noqa: S608
        _del(f"DELETE FROM biz_report WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_knowledge_draft WHERE module_id IN ({_m(module_ids)}) OR article_id IN ({_m(article_ids)}) OR project_id IN ({marks})", module_ids + article_ids + project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_knowledge_version WHERE article_id IN ({_m(article_ids)})", article_ids)  # noqa: S608
        _del(f"DELETE FROM biz_knowledge_ref WHERE article_id IN ({_m(article_ids)})", article_ids)  # noqa: S608
        _del(f"DELETE FROM biz_knowledge_article WHERE id IN ({_m(article_ids)})", article_ids)  # noqa: S608
        _del(f"DELETE FROM biz_bug_status_log WHERE bug_id IN ({_m(bug_ids)})", bug_ids)  # noqa: S608
        _del(  # 关系表挂用例或缺陷任一侧都算示例数据
            f"DELETE FROM biz_case_bug_rel WHERE case_id IN ({_m(case_ids)}) OR bug_id IN ({_m(bug_ids)})",  # noqa: S608
            case_ids + bug_ids,
        )
        _del(f"DELETE FROM biz_test_case WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_bug WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_requirement WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_ai_message WHERE session_id IN ({_m(session_ids)})", session_ids)  # noqa: S608
        _del(f"DELETE FROM biz_ai_session WHERE id IN ({_m(session_ids)})", session_ids)  # noqa: S608
        _del(f"DELETE FROM biz_automation_run WHERE script_id IN ({_m(script_ids)})", script_ids)  # noqa: S608
        _del(f"DELETE FROM biz_automation_script WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_module_ai_capability WHERE module_id IN ({_m(module_ids)})", module_ids)  # noqa: S608
        # 挂在示例模块上的全局/他项目知识条目（project_id 为空或属其他项目）是用户存量数据，
        # 不是示例资产：只摘掉模块引用，绝不随示例删除，否则会造成存量知识丢失
        conn.execute(
            f"UPDATE biz_knowledge_article SET module_id = NULL"  # noqa: S608
            f" WHERE module_id IN ({_m(module_ids)}) AND (project_id IS NULL OR project_id NOT IN ({marks}))",
            module_ids + project_ids,
        )
        _del(f"DELETE FROM biz_module WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_project_version WHERE project_id IN ({marks})", project_ids)  # noqa: S608
        _del(f"DELETE FROM biz_project WHERE id IN ({marks})", project_ids)  # noqa: S608
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.execute("PRAGMA foreign_keys = ON")
    return stale_files


TABLES = [
    "biz_report_item",
    "biz_report",
    "biz_report_template",
    "biz_knowledge_draft",
    "biz_knowledge_version",
    "biz_knowledge_ref",
    "biz_knowledge_article",
    "biz_bug_status_log",
    "biz_case_execution",
    "biz_case_bug_rel",
    "biz_test_case",
    "biz_bug",
    "biz_requirement",
    "biz_ai_message",
    "biz_ai_task",
    "biz_ai_session",
    "biz_automation_run",
    "biz_automation_script",
    "biz_module_ai_capability",
    "biz_module",
    "biz_project",
]

DEMO_CODE = "DEMO-EC"


def _steps(*items: tuple[str, str]) -> str:
    return json.dumps(
        [{"step": i + 1, "action": a, "expected": e} for i, (a, e) in enumerate(items)],
        ensure_ascii=False,
    )


def _mirror_article(conn: sqlite3.Connection, article_id: int, title: str, content: str,
                    category: str, tags: str, now: str) -> None:
    """把演示条目同步写成 Markdown，保持落盘目录与数据库一致。"""
    relative = knowledge_storage.write_article(conn, {
        "id": article_id,
        "title": title,
        "content": content,
        "category": category,
        "tags": tags,
        "review_status": "published",
        "current_version": 1,
        "updated_at": now,
    })
    if relative:
        conn.execute(
            "UPDATE biz_knowledge_article SET file_path = ?, content_checksum = ? WHERE id = ?",
            (relative, content_checksum(content), article_id),
        )


def _drop_stale_mirror_files(conn: sqlite3.Connection, stale: list[str]) -> None:
    """删除条目已被清空后失效的落盘文件，不触碰用户自己放进去的 .md。"""
    if not stale:
        return
    root = knowledge_storage.storage_root(conn)
    if not root.exists():
        return
    for relative in stale:
        try:
            target = knowledge_storage.safe_join(root, relative)
        except Exception:
            continue
        if target.exists():
            target.unlink()


def seed(conn: sqlite3.Connection) -> dict[str, int]:
    now = utcnow_iso()
    counts: dict[str, int] = {}

    # ---- 项目 ----
    cur = conn.execute(
        "INSERT INTO biz_project (code, name, description, owner, status, created_at, updated_at)"
        " VALUES (?, ?, ?, ?, 'active', ?, ?)",
        ("DEMO-EC", "示例电商项目", "覆盖登录、下单、支付的主流程演示数据", "yichengren", now, now),
    )
    project_id = int(cast("int", cur.lastrowid))

    # ---- 四级模块树 ----
    module_ids: dict[str, int] = {}
    module_names: dict[str, str] = {}
    for key, name, parent_key, node_type, sort_order in [
        ("login", "登录模块", None, "module", 1),
        ("login_sms", "手机号登录", "login", "submodule", 1),
        ("login_code", "验证码时效", "login_sms", "feature", 1),
        ("order", "交易模块", None, "module", 2),
        ("order_cart", "购物车", "order", "submodule", 1),
        ("order_pay", "支付回调", "order_cart", "feature", 1),
    ]:
        cur = conn.execute(
            "INSERT INTO biz_module (project_id, parent_id, node_type, name, sort_order, status,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 'active', ?, ?)",
            (
                project_id,
                module_ids.get(parent_key) if parent_key else None,
                node_type,
                name,
                sort_order,
                now,
                now,
            ),
        )
        module_ids[key] = int(cast("int", cur.lastrowid))
        module_names[key] = name

    # 登录功能点挂 3 个 AI 能力，其中「需求测试点生成」用专属提示词
    for capability, prompt_code, config in [
        ("testpoint_generation", None, {"count": 8}),
        ("case_generation", "case-generation", {"count": 10, "caseTypes": "functional,boundary"}),
        ("log_analysis", None, {}),
    ]:
        conn.execute(
            "INSERT INTO biz_module_ai_capability (module_id, capability, prompt_code, enabled,"
            " config_json, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
            (module_ids["login_code"], capability, prompt_code, json.dumps(config, ensure_ascii=False), now, now),
        )
    for capability, prompt_code, config in [
        ("bug_analysis", None, {}),
        ("sql_analysis", None, {}),
    ]:
        conn.execute(
            "INSERT INTO biz_module_ai_capability (module_id, capability, prompt_code, enabled,"
            " config_json, created_at, updated_at) VALUES (?, ?, ?, 1, ?, ?, ?)",
            (module_ids["order_pay"], capability, prompt_code, json.dumps(config, ensure_ascii=False), now, now),
        )

    # ---- AI 会话 ----
    cur = conn.execute(
        "INSERT INTO biz_ai_session (title, module, biz_type, biz_id, model, created_at, updated_at)"
        " VALUES (?, 'lab', NULL, NULL, ?, ?, ?)",
        ("登录用例设计讨论", "gpt-4o-mini", now, now),
    )
    session_id = int(cast("int", cur.lastrowid))
    for role, content in [
        ("user", "帮我梳理一下手机号登录可能出现的边界场景。"),
        ("assistant", "手机号登录常见边界：验证码 60 秒倒计时、空格与全角字符、手机号前导 0、异地登录风控提示。"),
        ("user", "补充一条并发登录同一账号的用例。"),
        ("assistant", "已补充：同一账号在两台设备登录时，后登录会话是否挤掉前一个会话，需确认产品设计。"),
    ]:
        conn.execute(
            "INSERT INTO biz_ai_message (session_id, role, content, model, tokens_prompt,"
            " tokens_completion, finish_reason, created_at, updated_at)"
            " VALUES (?, ?, ?, 'gpt-4o-mini', 32, 48, 'stop', ?, ?)",
            (session_id, role, content, now, now),
        )

    # ---- 需求 ----
    req_ids = []
    for req_no, title, content in [
        ("REQ-001", "手机号验证码登录", "用户输入手机号获取验证码，校验通过后登录成功。验证码 60 秒有效。"),
        ("REQ-002", "购物车与下单", "已登录用户可添加商品到购物车并提交订单，订单金额需实时计算。"),
        ("REQ-003", "订单支付", "支持微信与支付宝支付，支付成功后订单状态变更为已支付。"),
    ]:
        cur = conn.execute(
            "INSERT INTO biz_requirement (project_id, req_no, title, content, source, version,"
            " status, priority, tags, created_at, updated_at) VALUES (?, ?, ?, ?, '手工录入', 'V1.0',"
            " 'approved', 'P1', '核心流程', ?, ?)",
            (project_id, req_no, title, content, now, now),
        )
        req_ids.append(int(cast("int", cur.lastrowid)))

    # ---- 用例（6 种类型各若干）----
    case_specs = [
        ("FT-001", "手机号登录-正常流程", "functional", "P0", "pass", req_ids[0], "login_code",
         _steps(("输入手机号13800138000", "显示已发送"), ("输入验证码123456", "登录成功进入首页")), "登录成功后跳转首页", None),
        ("FT-002", "手机号登录-验证码错误", "functional", "P1", "fail", req_ids[0], "login_code",
         _steps(("输入手机号13800138000", "显示已发送"), ("输入错误验证码000000", "提示验证码错误")), "提示验证码错误且可重新输入", None),
        ("IT-001", "登录发送验证码接口", "interface", "P0", "pass", req_ids[0], "login_code",
         _steps(("POST /api/auth/sms/send", "返回 code=0"),), "返回 200 且 code=0", None),
        ("IT-002", "登录校验接口", "interface", "P0", "pass", req_ids[0], "login_code",
         _steps(("POST /api/auth/login", "返回 token"),), "返回 200 且携带 token", None),
        ("BT-001", "验证码 60 秒倒计时边界", "boundary", "P1", "not_executed", req_ids[0], "login_code",
         _steps(("点击获取后立即再次点击", "按钮禁用")), "59 秒内不可重复获取", "时间型边界：60 秒有效期临界"),
        ("BT-002", "手机号长度边界", "boundary", "P2", "not_executed", req_ids[0], "login_code",
         _steps(("输入 10 位手机号", "提示格式错误")), "少于 11 位时提示格式错误", "长度边界：10/11/12 位"),
        ("ST-001", "未登录访问订单页", "security", "P0", "pass", req_ids[1], "order_pay",
         _steps(("直接访问订单详情页", "跳转登录页")), "未登录用户被拦截到登录页", None),
        ("ST-002", "他人订单越权访问", "security", "P0", "pass", req_ids[1], "order_pay",
         _steps(("用 A 账号访问 B 的订单号", "返回无权限")), "返回 403 且不泄露订单信息", None),
        ("UIT-001", "登录页视觉校验", "ui", "P3", "not_executed", req_ids[0], "login_code",
         _steps(("打开登录页", "元素齐全且对齐")), "验证码按钮与输入框不重叠", None),
        ("ET-001", "验证码服务超时", "exception", "P1", "not_executed", req_ids[0], "login_code",
         _steps(("mock 短信服务超时", "提示稍后重试")), "提示友好且可重试，不出现白屏", "外部依赖异常"),
        ("ET-002", "网络断开时登录", "exception", "P2", "not_executed", req_ids[0], "login_code",
         _steps(("断网后点击登录", "提示网络异常")), "提示网络异常而非静默无响应", "网络异常"),
        ("FT-003", "购物车加购商品", "functional", "P1", "pass", req_ids[1], "order_cart",
         _steps(("商品详情页点击加入购物车", "购物车角标+1")), "购物车数量增加 1", None),
    ]
    case_ids = []
    for case_no, title, case_type, priority, status, req_id, module_key, steps, expected, tags in case_specs:
        cur = conn.execute(
            "INSERT INTO biz_test_case (project_id, requirement_id, module_id, case_no, title, case_type,"
            " priority, status, preconditions, steps_json, expected_result, actual_result, tags,"
            " source, creator, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, '已登录', ?, ?, NULL, ?, 'manual', 'yichengren', ?, ?)",
            (project_id, req_id, module_ids[module_key], case_no, title, case_type, priority, status,
             steps, expected, tags, now, now),
        )
        case_ids.append(int(cast("int", cur.lastrowid)))

    # ---- 缺陷 ----
    bug_specs = [
        ("BUG-001", "验证码 60 秒后可重复获取", "P1", "open", "functional", "login_code",
         "点击获取后第 61 秒可再次获取验证码，旧验证码仍然有效"),
        ("BUG-002", "购物车金额未按折扣重算", "P0", "assigned", "functional", "order_cart",
         "使用满减券后购物车总价仍为原价"),
        ("BUG-003", "订单详情页暴露手机号明文", "P0", "fixed", "security", "order_pay",
         "非管理员可见订单完整手机号"),
        ("BUG-004", "支付回调重复通知导致订单金额翻倍", "P0", "closed", "functional", "order_pay",
         "支付网关重复回调后订单金额被累加两次"),
        ("BUG-005", "弱网下登录按钮无 loading 态", "P3", "rejected", "ui", "login_code",
         "点击登录后按钮可重复点击"),
    ]
    bug_ids = []
    for bug_no, title, severity, status, bug_type, module_key, actual in bug_specs:
        cur = conn.execute(
            "INSERT INTO biz_bug (project_id, module_id, bug_no, title, severity, status, priority, bug_type,"
            " description, steps_json, expected_result, actual_result, assignee, reporter, version,"
            " env, module, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'yichengren', 'yichengren', 'V1.0.3',"
            " '测试环境', ?, ?, ?)",
            (project_id, module_ids[module_key], bug_no, title, severity, status, severity, bug_type, title,
             _steps(("复现操作", "观察结果")), "符合需求描述", actual, module_names[module_key], now, now),
        )
        bug_ids.append(int(cast("int", cur.lastrowid)))

    # ---- 用例-缺陷关联 ----
    for case_id, bug_id, rel in [(case_ids[4], bug_ids[0], "caused_by"), (case_ids[7], bug_ids[2], "caused_by"),
                                 (case_ids[1], bug_ids[0], "related")]:
        conn.execute(
            "INSERT INTO biz_case_bug_rel (case_id, bug_id, relation_type, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?)",
            (case_id, bug_id, rel, now, now),
        )

    # ---- 执行记录 ----
    for case_id, result in [(case_ids[0], "pass"), (case_ids[1], "fail"), (case_ids[2], "pass"),
                            (case_ids[3], "pass"), (case_ids[6], "pass"), (case_ids[7], "pass"),
                            (case_ids[11], "pass")]:
        conn.execute(
            "INSERT INTO biz_case_execution (case_id, result, duration_ms, environment, version,"
            " executed_by, executed_at, remark, created_at, updated_at)"
            " VALUES (?, ?, ?, '测试环境', 'V1.0.3', 'yichengren', ?, NULL, ?, ?)",
            (case_id, result, 1200 + case_id * 37, now, now, now),
        )

    # ---- 缺陷流转日志 ----
    for bug_id, from_status, to_status, comment in [
        (bug_ids[0], "open", "assigned", "已分配给开发"),
        (bug_ids[1], "open", "assigned", "开发已开始修复"),
        (bug_ids[2], "open", "fixed", "开发提交修复"),
        (bug_ids[2], "fixed", "retest", "已提交回归"),
        (bug_ids[2], "retest", "closed", "回归验证通过"),
        (bug_ids[3], "open", "fixed", "已修复"),
        (bug_ids[3], "fixed", "retest", "已提交回归"),
        (bug_ids[3], "retest", "closed", "验证通过"),
    ]:
        conn.execute(
            "INSERT INTO biz_bug_status_log (bug_id, from_status, to_status, operator, comment,"
            " created_at, updated_at) VALUES (?, ?, ?, 'yichengren', ?, ?, ?)",
            (bug_id, from_status, to_status, comment, now, now),
        )

    # ---- 知识文章 ----
    articles = [
        ("手机号登录测试用例设计要点", "手机号登录的测试重点包括：验证码时效、错误次数限制、异地风控提示、"
         "同一账号多端登录。验证码登录失败 5 次后锁定 15 分钟。", "测试方法", "登录,验证码", "manual",
         "login_code", None),
        ("支付回调重复通知的处理方案", "支付网关可能重复推送回调，必须以订单号做幂等控制：先查后写 + "
         "唯一索引 + 状态机校验。已在本项目 BUG-004 中验证。", "踩坑记录", "支付,幂等", "bug",
         "order_pay", None),
        ("弱网场景的界面反馈规范", "弱网下所有异步操作必须展示 loading 态并禁用重复提交，失败后给出可重试提示。",
         "经验总结", "弱网,体验", "manual", "login_code", None),
        ("订单金额计算的浮点陷阱", "金额计算禁止使用浮点数，统一用分为单位的整数或 Decimal，避免 0.1+0.2 问题。",
         "经验总结", "金额,精度", "manual", "order_cart", None),
        ("越权访问的常见形态", "水平越权：改 URL 中的订单号；垂直越权：普通用户调用管理接口。测试时至少覆盖"
         "自属与他属两类数据。", "安全测试", "越权,安全", "manual", "order_pay", None),
        ("登录模块需求讨论纪要", "本次讨论聚焦手机号登录的边界场景，包括验证码 60 秒倒计时、空格与全角字符、"
         "手机号前导 0，以及并发登录同一账号的产品设计确认。", "会议纪要", "登录,会议", "session",
         "login_code", session_id),
    ]
    article_ids: list[int] = []
    for title, content, category, tags, source_type, module_key, sess in articles:
        cur = conn.execute(
            "INSERT INTO biz_knowledge_article (title, content, summary, category, tags, source_type,"
            " session_id, ai_task_id, project_id, module_id, review_status, current_version,"
            " draft_version, view_count, status, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, 'published', 1, 1, 0, 'published', ?, ?)",
            (title, content, content[:60], category, tags, source_type, sess, project_id,
             module_ids[module_key], now, now),
        )
        article_id = int(cast("int", cur.lastrowid))
        article_ids.append(article_id)
        _mirror_article(conn, article_id, title, content, category, tags, now)

    # 每篇文章建一条 v1 版本记录
    for index, article_id in enumerate(article_ids):
        title, content, category, tags = articles[index][0], articles[index][1], articles[index][2], articles[index][3]
        conn.execute(
            "INSERT INTO biz_knowledge_version (article_id, version, title, content, summary, category,"
            " tags, review_status, reviewer, reviewed_at, change_log, checksum, created_by,"
            " created_at, updated_at) VALUES (?, 1, ?, ?, ?, ?, ?, 'published', 'yichengren', ?,"
            " '初始版本', ?, 'yichengren', ?, ?)",
            (article_id, title, content, content[:60], category, tags, now,
             hashlib.sha256(content.encode("utf-8")).hexdigest()[:16], now, now),
        )

    # ---- 待审核池：2 条 AI 产出物 ----
    for title, content, producer, module_key in [
        ("验证码时效测试点脑图", "登录模块 / 手机号登录 / 验证码时效：覆盖 60 秒倒计时、错误次数锁定、"
         "并发获取、跨端登录四类测试点。", "testpoint_generation", "login_code"),
        ("支付回调幂等排查结论", "先查后写 + 订单号唯一索引 + 状态机三重校验，缺一不可。", "bug_analysis", "order_pay"),
    ]:
        conn.execute(
            "INSERT INTO biz_knowledge_draft (title, content, summary, category, tags, module_id,"
            " source_type, ref_type, producer, review_status, created_at, updated_at)"
            " VALUES (?, ?, ?, 'general', '', ?, 'ai_output', 'task', ?, 'pending', ?, ?)",
            (title, content, content[:60], module_ids[module_key], producer, now, now),
        )


    # ---- 报告 ----
    cur = conn.execute(
        "INSERT INTO biz_report (project_id, report_no, title, report_type, period_start, period_end,"
        " summary, content, status, creator, created_at, updated_at)"
        " VALUES (?, 'RPT-001', '示例电商项目第一轮测试报告', 'summary', '2026-10-01', '2026-10-31',"
        " '共 12 条用例，5 个缺陷，其中 2 个 P0 未闭环。', ?, 'published', 'yichengren', ?, ?)",
        (project_id, "# 第一轮测试报告\n\n## 结论\n支付回调幂等问题需在发版前闭环。", now, now),
    )
    report_id = int(cast("int", cur.lastrowid))
    metrics = {
        "totalCases": 12,
        "passed": 6,
        "failed": 1,
        "blocked": 0,
        "notExecuted": 5,
        "passRate": 0.5,
        "totalBugs": 5,
        "openBugs": 2,
        "coverageRate": 1.0,
    }
    for section, title_text, content_text, metric in [
        ("overview", "测试概览", "本轮共执行 12 条用例，覆盖 3 个需求。", metrics),
        ("case", "用例执行情况", "功能用例通过率 50%，主要缺口在边界与异常场景。", {"passed": 6, "failed": 1}),
        ("bug", "缺陷情况", "共 5 个缺陷，2 个 P0 未闭环。", {"totalBugs": 5, "openBugs": 2}),
    ]:
        conn.execute(
            "INSERT INTO biz_report_item (report_id, section, title, content, metric_json, sort_order,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, ?, 0, ?, ?)",
            (report_id, section, title_text, content_text, json.dumps(metric, ensure_ascii=False), now, now),
        )

    # ---- 报告模板（追加模式下不清模板表，重复执行只补缺）----
    exists = conn.execute(
        "SELECT 1 FROM biz_report_template WHERE code = 'standard' AND is_active = 1"
    ).fetchone()
    if not exists:
        conn.execute(
            "INSERT INTO biz_report_template (name, code, description, sections_json, is_default, is_active,"
            " created_at, updated_at) VALUES (?, ?, ?, ?, 1, 1, ?, ?)",
            ("标准测试报告", "standard", "覆盖概览、用例、缺陷、覆盖率、AI 分析的标准结构",
             json.dumps(["overview", "case", "bug", "coverage", "ai_analysis"], ensure_ascii=False), now, now),
        )

    # ---- 自动化脚本样例（纯标准库，可直接执行演示）----
    api_script = '''"""示例接口自动化：演示断言写法，全部走本地桩，不依赖外部服务。"""

CASES = [
    ("登录成功返回 token", {"code": 0, "token": "demo-token"}, lambda r: r["code"] == 0 and bool(r["token"])),
    ("验证码错误返回 1001", {"code": 1001, "message": "验证码错误"}, lambda r: r["code"] == 1001),
]

failed = 0
for name, response, assertion in CASES:
    if assertion(response):
        print(f"[PASS] {name}")
    else:
        failed += 1
        print(f"[FAIL] {name} 实际 {response}")
print(f"合计 {len(CASES)} 条，失败 {failed}")
raise SystemExit(1 if failed else 0)
'''
    conn.execute(
        "INSERT INTO biz_automation_script (script_no, name, category, target_type, framework,"
        " language, project_id, module_id, description, file_name, content, source, run_mode,"
        " entry_command, timeout_seconds, status, remark, created_at, updated_at)"
        " VALUES ('AUTO-API-001', '登录接口冒烟断言示例', 'api', 'interface', 'python', 'python',"
        " ?, ?, '演示接口自动化：纯标准库本地桩，可直接执行体验断言与退出码留痕',"
        " 'login_smoke.py', ?, 'manual', 'script', NULL, 60, 'ready',"
        " '示例项目自带，可整项目删除', ?, ?)",
        (project_id, module_ids["login_code"], api_script, now, now),
    )

    counts = {
        "项目": 1,
        "模块节点": len(module_ids),
        "能力绑定": 5,
        "需求": len(req_ids),
        "用例": len(case_ids),
        "缺陷": len(bug_ids),
        "用例-缺陷关联": 3,
        "知识文章": len(article_ids),
        "待审产出": 2,
        "报告": 1,
        "会话": 1,
        "消息": 4,
        "自动化脚本": 1,
    }
    return counts
