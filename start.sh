#!/usr/bin/env bash
# AITestPlatform (AITP) 启动脚本 —— macOS / Linux / Windows(Git Bash)
# 用法: ./start.sh [--build] [--reinstall] [--no-browser]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

export PYTHONUTF8=1
export PYTHONIOENCODING=utf-8
export PYTHONUNBUFFERED=1

PORT="${AITP_PORT:-8760}"
HOST="${AITP_HOST:-127.0.0.1}"
OPEN_BROWSER="${AITP_OPEN_BROWSER:-1}"
FORCE_BUILD=0
FORCE_REINSTALL=0

for arg in "$@"; do
    case "$arg" in
        --build) FORCE_BUILD=1 ;;
        --reinstall) FORCE_REINSTALL=1 ;;
        --no-browser) OPEN_BROWSER=0 ;;
        -h|--help)
            echo "用法: ./start.sh [--build] [--reinstall] [--no-browser]"
            echo "  --build       强制重新构建前端"
            echo "  --reinstall   重建虚拟环境并重装依赖"
            echo "  --no-browser  启动时不自动打开浏览器"
            exit 0 ;;
        *) echo "未知参数: $arg"; exit 2 ;;
    esac
done

VENV_DIR="$SCRIPT_DIR/.venv"
LOG_DIR="$SCRIPT_DIR/logs"
DEPLOY_LOG="$LOG_DIR/deploy.log"
mkdir -p "$LOG_DIR"

log_deploy() {
    { printf '%s | DEPLOY | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; } 2>/dev/null >> "$DEPLOY_LOG" || true
}

echo "============================================================"
echo "  AITestPlatform (AITP) 本地 AI 测试工作台"
echo "  http://${HOST}:${PORT}"
echo "============================================================"

# ---- 0. 端口检查（服务已在运行则直接打开页面，不碰虚拟环境）----
port_pids() {
    if command -v lsof >/dev/null 2>&1; then
        lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true
    elif command -v ss >/dev/null 2>&1; then
        ss -lptnH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 || true
    elif command -v fuser >/dev/null 2>&1; then
        fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true
    elif command -v netstat >/dev/null 2>&1; then
        netstat -ano 2>/dev/null | awk -v port="$PORT" '$4=="LISTENING" && $2 ~ (":" port "$") {print $5}' | sort -u
    fi
}

health_ok() {
    if command -v curl >/dev/null 2>&1; then
        curl -fsS --max-time 3 "http://${HOST}:${PORT}/api/v1/health" 2>/dev/null | grep -q '200000'
    else
        return 1
    fi
}

# open 只在 macOS 存在；Linux 用 xdg-open，Git Bash 用 rundll32
open_url() {
    if command -v open >/dev/null 2>&1; then
        open "$1"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$1"
    elif command -v rundll32.exe >/dev/null 2>&1; then
        rundll32.exe url.dll,FileProtocolHandler "$1"
    fi
}

EXISTING_PIDS="$(port_pids)"
if [ -n "$EXISTING_PIDS" ]; then
    if health_ok; then
        echo "[0/4] AITP 已在运行（PID: $(echo "$EXISTING_PIDS" | tr '\n' ' ')），直接打开页面。"
        log_deploy "[script] 检测到服务已在运行，跳过启动 | port=$PORT"
        if [ "$OPEN_BROWSER" = "1" ]; then
            open_url "http://${HOST}:${PORT}"
        fi
        exit 0
    fi
    echo "[AITP] 端口 ${PORT} 已被其他进程占用（PID: $(echo "$EXISTING_PIDS" | tr '\n' ' ')）。"
    echo "       如需终止 AITP 请执行 ./stop.sh，或设置 AITP_PORT 使用其他端口。"
    log_deploy "[script] 端口占用退出 | port=$PORT"
    exit 1
fi

# ---- 1. 虚拟环境 ----
# 虚拟环境布局随平台不同：POSIX 为 .venv/bin/python，Windows 为 .venv/Scripts/python.exe
venv_python() {
    if [ -x "$VENV_DIR/bin/python" ]; then
        echo "$VENV_DIR/bin/python"
    elif [ -x "$VENV_DIR/Scripts/python.exe" ]; then
        echo "$VENV_DIR/Scripts/python.exe"
    fi
}

# 首选 3.12 / 3.11：这两个区间是 AITP 全部门禁验证过的版本，3.13 只按"能装就装"处理
find_python() {
    for candidate in python3.12 python3.11 python3.13 python3 python; do
        if command -v "$candidate" >/dev/null 2>&1; then
            if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
                command -v "$candidate"
                return 0
            fi
        fi
    done
    return 1
}

PY_BIN="$(venv_python || true)"

if [ "$FORCE_REINSTALL" = "1" ] && [ -d "$VENV_DIR" ]; then
    echo "[1/4] 重建虚拟环境 .venv ..."
    rm -rf "$VENV_DIR" 2>/dev/null || true
    PY_BIN=""
fi

if [ -z "$PY_BIN" ]; then
    if [ -d "$VENV_DIR" ] && [ ! -x "$VENV_DIR/bin/python" ] && [ ! -x "$VENV_DIR/Scripts/python.exe" ]; then
        echo "[AITP] 虚拟环境已存在但不可用（可能正在被占用），请先执行 ./stop.sh 再重试。"
        exit 1
    fi
    echo "[1/4] 创建虚拟环境 .venv ..."
    BASE_PY="$(find_python || true)"
    if [ -z "$BASE_PY" ]; then
        echo "[AITP] 未找到 Python 3.11+。macOS 请先安装：brew install python@3.12"
        exit 1
    fi
    "$BASE_PY" -m venv "$VENV_DIR" || "$BASE_PY" -m virtualenv "$VENV_DIR" || true
    PY_BIN="$(venv_python || true)"
    if [ -z "$PY_BIN" ]; then
        echo "[AITP] 虚拟环境创建失败。"
        exit 1
    fi
    echo "[1/4] 安装依赖 ..."
    "$PY_BIN" -m pip install --upgrade pip -q
    "$PY_BIN" -m pip install -r requirements.txt
    log_deploy "[script] 虚拟环境创建并安装依赖 | python=$("$PY_BIN" -V 2>&1)"
else
    echo "[1/4] 虚拟环境已存在，跳过。"
fi

if [ "$FORCE_REINSTALL" = "1" ]; then
    echo "[1/4] 强制重装依赖 ..."
    "$PY_BIN" -m pip install -r requirements.txt
fi

# ---- 2. 前端构建 ----
NEED_BUILD=0
if [ -f "$SCRIPT_DIR/frontend/package.json" ]; then
    if [ ! -f "$SCRIPT_DIR/backend/aitp/static/index.html" ]; then NEED_BUILD=1; fi
    if [ "$FORCE_BUILD" = "1" ]; then NEED_BUILD=1; fi
fi

if [ "$NEED_BUILD" = "1" ]; then
    if ! command -v npm >/dev/null 2>&1; then
        echo "[2/4] 未找到 npm，跳过前端构建（后端仍会以基础页面启动）。"
    else
        if [ ! -d "$SCRIPT_DIR/frontend/node_modules" ]; then
            echo "[2/4] 安装前端依赖 ..."
            (cd "$SCRIPT_DIR/frontend" && npm install)
        fi
        echo "[2/4] 构建前端 ..."
        (cd "$SCRIPT_DIR/frontend" && npm run build) || \
            echo "[AITP] 前端构建失败，后端仍会以基础页面启动。"
    fi
else
    echo "[2/4] 前端构建产物已存在，跳过构建（--build 可强制重建）。"
fi

# ---- 3. 端口复查 ----
EXISTING_PIDS="$(port_pids)"
if [ -n "$EXISTING_PIDS" ]; then
    echo "[3/4] 端口 ${PORT} 在启动过程中被占用（PID: $(echo "$EXISTING_PIDS" | tr '\n' ' ')），退出。"
    log_deploy "[script] 端口二次占用退出 | port=$PORT"
    exit 1
fi
echo "[3/4] 端口 ${PORT} 可用。"

# ---- 4. 启动 ----
echo "[4/4] 正在启动服务 ..."
log_deploy "[script] 启动服务 | host=$HOST port=$PORT"
if [ "$OPEN_BROWSER" = "1" ]; then
    (
        sleep 2
        open_url "http://${HOST}:${PORT}"
    ) &
fi

if "$PY_BIN" backend/run.py; then
    echo
    echo "[AITP] 服务已停止。"
    log_deploy "[script] 服务退出 | host=$HOST port=$PORT"
else
    STATUS=$?
    echo
    echo "[AITP] 服务未能启动（退出码 ${STATUS}）。环境自检的原因见上方提示，排查后可重跑 ./start.sh。"
    log_deploy "[script] 服务启动失败 | host=$HOST port=$PORT exit=$STATUS"
    exit "$STATUS"
fi
