"""校验功能测试用例文档对接口与路由的覆盖率。

用法：python scripts/check_case_coverage.py
输出：未覆盖的接口/路由清单与覆盖率百分比。
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "AITP-功能测试用例.md"
ENDPOINTS_FILE = ROOT / "data" / "api_endpoints.txt"
ROUTES_FILE = ROOT / "data" / "frontend_routes.txt"

ENDPOINT_RE = re.compile(r"`(GET|POST|PUT|DELETE|PATCH)\s+(/api/v1/[^`]+)`")
ROUTE_RE = re.compile(r"`(/[^`\s]*|无（接口级）)`")


PATH_PARAM_RE = re.compile(r"<(int:)?([A-Za-z_][A-Za-z0-9_]*)>")
BRACE_PARAM_RE = re.compile(r"\{([A-Za-z_][A-Za-z0-9_:]*)\}")


def normalize_endpoint(method: str, path: str) -> str:
    path = path.strip()
    # entity_type 是有语义的路径参数，保留；其余参数统一归一为 <pk>
    path = BRACE_PARAM_RE.sub(lambda m: f"<{m.group(1)}>" if m.group(1) == "entity_type" else "<pk>", path)
    path = PATH_PARAM_RE.sub(
        lambda m: "<entity_type>" if m.group(2) == "entity_type" else "<pk>", path
    )
    return f"{method} {path}"


def load_endpoints() -> list[str]:
    raw = []
    for line in ENDPOINTS_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            raw.append(normalize_endpoint(*line.split(" ", 1)))
    return raw


GROUP_RE = re.compile(r":(\w+)\(([\w|]+)\)$")


def load_routes() -> list[str]:
    routes: list[str] = []
    for line in ROUTES_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        match = GROUP_RE.search(line)
        if match:
            for option in match.group(2).split("|"):
                routes.append(line[: match.start()] + option)
        else:
            routes.append(line)
    return routes


def parse_doc(text: str) -> tuple[set[str], set[str], list[str]]:
    endpoints: set[str] = set()
    routes: set[str] = set()
    case_ids: list[str] = []
    for line in text.splitlines():
        if line.startswith("### TC-"):
            case_ids.append(line[4:].split()[0])
        for method, path in ENDPOINT_RE.findall(line):
            endpoints.add(normalize_endpoint(method, path))
        for route in ROUTE_RE.findall(line):
            if route.startswith("/"):
                routes.add(route)
    return endpoints, routes, case_ids


def main() -> int:
    if not DOC.exists():
        print(f"文档不存在：{DOC}")
        return 1
    text = DOC.read_text(encoding="utf-8")
    covered_endpoints, covered_routes, case_ids = parse_doc(text)
    all_endpoints = load_endpoints()
    all_routes = load_routes()

    missing_ep = [e for e in all_endpoints if e not in covered_endpoints]
    missing_rt = [r for r in all_routes if r not in covered_routes]

    # 动态路由参数归一化：/lab/:capability 与 /lab/xmind 视为同一入口
    def route_bucket(route: str) -> str:
        return re.sub(r":\w+", "*", route)

    covered_buckets = {route_bucket(r) for r in covered_routes}
    missing_rt = [r for r in missing_rt if route_bucket(r) not in covered_buckets]

    covered_ep_count = len(all_endpoints) - len(missing_ep)
    covered_rt_count = len(all_routes) - len(missing_rt)
    total = len(all_endpoints) + len(all_routes)
    pct = (covered_ep_count + covered_rt_count) / total * 100 if total else 0

    print(f"用例数：{len(case_ids)}")
    print(f"接口覆盖：{covered_ep_count}/{len(all_endpoints)}")
    print(f"路由覆盖：{covered_rt_count}/{len(all_routes)}")
    print(f"合计覆盖率：{pct:.2f}%")
    if missing_ep:
        print(f"\n未覆盖接口（{len(missing_ep)}）：")
        for e in missing_ep:
            print(f"  - {e}")
    if missing_rt:
        print(f"\n未覆盖路由（{len(missing_rt)}）：")
        for r in missing_rt:
            print(f"  - {r}")
    return 0 if pct >= 98 else 1


if __name__ == "__main__":
    sys.exit(main())
