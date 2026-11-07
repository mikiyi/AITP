"""写入演示数据，便于快速体验与验收。

演示数据默认写进独立的 `data/demo.db`，不碰正在使用的 `data/aitp.db`：
这个脚本带 `--reset`，会无差别清空 19 张业务表，一旦指错库就是真实数据全没。
确实要往正式库里灌演示数据时，显式加 `--force`。

    python backend/scripts/seed_demo.py                 # 写 data/demo.db
    python backend/scripts/seed_demo.py --reset         # 先清空 demo 库业务数据
    python backend/scripts/seed_demo.py --db data/aitp.db --force   # 明确要对正式库动手

重复执行时，若库里只有演示项目 DEMO-EC，会自动清空后重建；
检测到其他项目则拒绝执行，避免误删真实数据。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import sqlite3
import sys
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))

from aitp.common.security import content_checksum  # noqa: E402
from aitp.common.time_utils import utcnow_iso  # noqa: E402
from aitp.config import load_config  # noqa: E402
from aitp.db.connection import get_standalone_conn  # noqa: E402
from aitp.db.migrator import init_db  # noqa: E402
from aitp.services import knowledge_storage  # noqa: E402

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
    "biz_module_ai_capability",
    "biz_module",
    "biz_project",
]


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

    # ---- 报告模板 ----
    conn.execute(
        "INSERT INTO biz_report_template (name, code, description, sections_json, is_default, is_active,"
        " created_at, updated_at) VALUES (?, ?, ?, ?, 1, 1, ?, ?)",
        ("标准测试报告", "standard", "覆盖概览、用例、缺陷、覆盖率、AI 分析的标准结构",
         json.dumps(["overview", "case", "bug", "coverage", "ai_analysis"], ensure_ascii=False), now, now),
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
    }
    return counts


def _live_db_path() -> pathlib.Path:
    """当前服务实际在用的库；演示脚本默认不许碰它。"""
    return load_config().db_path.resolve()


def _project_codes(conn: sqlite3.Connection) -> set[str]:
    try:
        return {str(row[0]) for row in conn.execute("SELECT code FROM biz_project")}
    except sqlite3.OperationalError:
        return set()


def _clear_business_tables(conn: sqlite3.Connection) -> list[str]:
    """清空 19 张业务表，返回清空前需要删除的落盘文件。"""
    stale_files = [
        str(row[0]) for row in conn.execute(
            "SELECT file_path FROM biz_knowledge_article"
            " WHERE file_path IS NOT NULL AND file_path <> ''")
    ]
    for table in TABLES:
        conn.execute(f"DELETE FROM {table}")
    conn.commit()
    return stale_files


def main() -> int:
    parser = argparse.ArgumentParser(description="AITP 演示数据")
    parser.add_argument("--db", default=str(ROOT / "data" / "demo.db"))
    parser.add_argument("--reset", action="store_true", help="先清空业务数据")
    parser.add_argument("--force", action="store_true",
                        help="允许对正在使用的正式库写入并清空")
    args = parser.parse_args()

    db_path = pathlib.Path(args.db)
    if not db_path.exists():
        print(f"[INFO] 数据库不存在，新建并建表：{db_path}")
        init_db(db_path)

    if not args.force and db_path.resolve() == _live_db_path():
        print(
            "[FAIL] 这是正在使用的正式库，演示脚本会清空业务表，已拒绝执行。\n"
            f"       目标库：{db_path}\n"
            "       要体验演示数据：不带 --db 运行，脚本会写 data/demo.db；\n"
            "       确实要对正式库动手：加 --force。"
        )
        return 1

    conn = get_standalone_conn(db_path)
    try:
        stale_files: list[str] = []
        if args.reset:
            print(f"[WARN] 即将清空 {db_path} 的 19 张业务表。")
            stale_files = _clear_business_tables(conn)
            print("已清空业务数据。")
        else:
            codes = _project_codes(conn)
            if codes == {"DEMO-EC"}:
                print("[INFO] 目标库已有演示数据，自动重建（等价于 --reset）。")
                stale_files = _clear_business_tables(conn)
            elif codes:
                preview = "、".join(sorted(codes)[:5])
                print(
                    f"[FAIL] 目标库已有业务数据（项目：{preview}），未做清空。\n"
                    "       重复灌演示数据会与现有数据冲突；确认要重建请加 --reset。"
                )
                return 1

        counts = seed(conn)
        conn.commit()
        _drop_stale_mirror_files(conn, stale_files)
        print(f"目标库：{db_path}")
        print(" | ".join(f"{k} {v}" for k, v in counts.items()))
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
