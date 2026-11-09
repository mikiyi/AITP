"""前端未捕获异常兜底门禁：GAP-26 功能点 4 的契约要能被静态扫出来。

验收标准第 3 条要求「前端任意一处抛出未捕获异常时显示兜底页而非白屏，提示中带 traceId」。
这条契约全靠源码约束——没有哪个接口能替它作证，所以这里逐条扫：Vue 渲染异常、未捕获
Promise、脚本错误三类出口是否都收口；兜底页是否真把追踪号和日志位置铺出来；守卫是否在
挂载之前装好。每条扫描规则都配一个故意写出来的违规样例做正向对照，防止正则空转。

    python backend/scripts/check_error_boundary.py
"""

from __future__ import annotations

import pathlib
import re
import sys
from typing import Any, cast

# TextIO 协议没有 reconfigure，只有具体 TextIOWrapper 才有
cast(Any, sys.stdout).reconfigure(encoding="utf-8", errors="replace")

ROOT = pathlib.Path(__file__).resolve().parents[2]
SRC = ROOT / "frontend" / "src"
GUARD_TS = SRC / "composables" / "useGlobalError.ts"
BOUNDARY_VUE = SRC / "components" / "GlobalErrorBoundary.vue"
TABLE_VUE = SRC / "components" / "BaseProTable.vue"
MAIN_TS = SRC / "main.ts"
APP_VUE = SRC / "App.vue"
HELP_TS = SRC / "views" / "settings" / "productHelpContent.ts"
REQUEST_TS = SRC / "api" / "request.ts"

#: 谁赋值了 Vue 的全局错误处理器
HANDLER_RE = re.compile(r"app\.config\.errorHandler\s*=")
#: 三类异常出口
REJECT_RE = re.compile(r"addEventListener\(\s*['\"]unhandledrejection['\"]")
SCRIPT_ERROR_RE = re.compile(r"addEventListener\(\s*['\"]error['\"]")
#: 资源加载失败（图片/脚本 404）也走 window error，必须只收 ErrorEvent
ERROR_EVENT_GUARD_RE = re.compile(r"instanceof\s+ErrorEvent")
#: 本页追踪号的前缀：控制台日志、兜底页、门禁三方对齐用的记号
CLIENT_TRACE_RE = re.compile(r"fe-\$\{")

#: 失败分支里必须有一条能重新触发取数的重试按钮
RETRY_RE = re.compile(r'loadError[\s\S]{0,400}void load\(\)|loadError[\s\S]{0,400}@click="load"[\s\S]{0,80}重试')

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


def read(path: pathlib.Path) -> str:
    return path.read_text(encoding="utf-8")


def frontend_sources() -> list[pathlib.Path]:
    return sorted(p for p in SRC.rglob("*") if p.suffix in {".ts", ".vue"} and p.is_file())


def main() -> int:
    guard = read(GUARD_TS)
    boundary = read(BOUNDARY_VUE)
    main_ts = read(MAIN_TS)
    app_vue = read(APP_VUE)
    help_ts = read(HELP_TS)
    request_ts = read(REQUEST_TS)

    # 一、三类异常出口都要收口，且只在一个地方收
    handlers = [p for p in frontend_sources() if HANDLER_RE.search(read(p))]
    check("Vue 全局错误处理器已登记", str(SRC / "composables" / "useGlobalError.ts") in
          [str(p) for p in handlers], str([p.name for p in handlers]))
    check("登记处全局唯一", len(handlers) == 1, str([p.name for p in handlers]))
    check("扫描规则能命中重复登记", bool(HANDLER_RE.search("  app.config.errorHandler = noop")),
          "正向对照失配")
    check("未捕获 Promise 拒绝被收口", bool(REJECT_RE.search(guard)))
    check("浏览器脚本错误被收口", bool(SCRIPT_ERROR_RE.search(guard)))
    check("资源加载失败不铺兜底页", bool(ERROR_EVENT_GUARD_RE.search(guard)),
          "window error 未过滤 ErrorEvent，图片 404 会盖住整个界面")
    check("拒绝扫描规则能命中漏网写法", bool(REJECT_RE.search(
        "window.addEventListener('unhandledrejection', onRej)")))

    # 二、追踪号：每条异常都要有可对上日志的记号
    check("异常带本页追踪号", bool(CLIENT_TRACE_RE.search(guard)), "clientTrace 未用 fe- 前缀")
    check("追踪号打进控制台", "console.error" in guard and "trace" in guard)
    check("后端追踪号按结构读取", "traceId" in guard and "from '@/api" not in guard,
          "兜底记号不该反向依赖 api 层")
    check("ApiError 仍对外暴露 traceId", bool(
        re.search(r"readonly\s+traceId\s*:\s*string", request_ts)))

    # 三、装守卫的时机与兜底页的挂载位置
    install_at = main_ts.find("installGlobalErrorGuard(app)")
    mount_at = main_ts.find("app.mount('#app')")
    check("守卫在挂载前安装", 0 <= install_at < mount_at, f"install={install_at} mount={mount_at}")
    check("守卫从入口调用", "installGlobalErrorGuard" in main_ts and "export function installGlobalErrorGuard" in guard)
    check("兜底页挂在应用外壳里", "<GlobalErrorBoundary />" in app_vue,
          "App.vue 未渲染兜底页")
    # AccessGate 未通过口令时不渲染业务内容；兜底页要在它之外，否则异常提示会被吞掉
    gate_at = app_vue.find("<AccessGate>")
    boundary_at = app_vue.find("<GlobalErrorBoundary />")
    check("兜底页不受访问口令门控", 0 <= gate_at < boundary_at and "</AccessGate>" in app_vue[:boundary_at],
          f"gate={gate_at} boundary={boundary_at}")

    # 四、兜底页本身给得出排查所需的信息
    for name, token in (
        ("显示异常原因", "globalError.message"),
        ("显示本页追踪号", "globalError.clientTraceId"),
        ("显示后端追踪号", "globalError.traceId"),
        ("显示发生位置", "globalError.source"),
        ("显示发生时间", "globalError.at"),
        ("指向本机日志", "data/aitp.log"),
        ("可关闭提示", "dismiss"),
        ("可刷新页面", "window.location.reload()"),
        ("仅异常时出现", 'v-if="globalError"'),
    ):
        check(f"兜底页{name}", token in boundary)
    check("兜底页盖住界面而非白屏", "position: fixed" in boundary and "inset: 0" in boundary)
    check("业务界面在兜底页之下保留", "</AccessGate>" in app_vue)

    # 五、状态是模块级单例：任何一处抛错，兜底页都能读到同一条
    check("异常态为跨页面单例", bool(re.search(r"^const current = ref<GlobalErrorState \| null>",
                                          guard, re.M)), "current 必须是模块级 ref")
    check("关闭时清空异常态", bool(re.search(r"current\.value = null", guard)))

    # 六、帮助页口径同步
    check("帮助页说明异常兜底与追踪号", "追踪号" in help_ts and "兜底" in help_ts,
          "productHelpContent.ts 未同步 GAP-26 功能点 4 的用户口径")

    # 七、GAP-26 功能点 7 的后半句：关键页有空态与重试入口（共用底座 = BaseProTable）
    table = read(TABLE_VUE)
    users = [p for p in frontend_sources() if "BaseProTable" in read(p) and p != TABLE_VUE]
    check("大列表统一走共用表格底座", len(users) >= 10, f"接入页面={len(users)}")
    check("取数失败留下原因", bool(re.search(r"loadError\.value = message", table)),
          "catch 里只弹 toast，界面回落「暂无数据」等于没提示")
    check("失败态给重试入口", bool(RETRY_RE.search(table)),
          "失败分支里没有能重新取数的「重试」按钮")
    check("空态区分无数据与筛选无匹配", "hasActiveFilters" in table
          and "没有符合当前筛选的记录" in table and "暂无数据" in table)
    check("筛选无匹配给重置入口", '@click="reset"' in table or "重置筛选" in table)
    check("首屏用骨架而不是空白", bool(re.search(r'v-if="firstLoading"[\s\S]{0,120}el-skeleton', table)))
    check("加载中不误判为空态", bool(re.search(r'v-else-if="loading"', table)))
    check("失败仍保留可见提示", "ElMessage.error(message)" in table)
    check("重试扫描规则能命中合规写法", bool(RETRY_RE.search(
        '<div v-else-if="loadError"><p>{{ e }}</p>'
        '<el-button @click="load">重试</el-button></div>')))
    check("扫描规则对没有重试入口的写法判红", not RETRY_RE.search(
        '<div v-else-if="loadError"><p>加载失败</p></div>'))

    print("-" * 40)
    print(f"PASS={_passed} FAIL={_failed}")
    return 0 if _failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
