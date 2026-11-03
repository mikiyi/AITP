#!/usr/bin/env bash
# AITestPilot (AITP) 停止脚本 —— macOS / Linux
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PORT="${AITP_PORT:-8760}"
HOST="${AITP_HOST:-127.0.0.1}"
LOG_DIR="$SCRIPT_DIR/logs"
DEPLOY_LOG="$LOG_DIR/deploy.log"
mkdir -p "$LOG_DIR"

log_deploy() {
    { printf '%s | DEPLOY | %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"; } 2>/dev/null >> "$DEPLOY_LOG" || true
}

echo "[AITP] 停止端口 ${PORT} 上的服务 ..."

stopped=0
PIDS=""
if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
elif command -v ss >/dev/null 2>&1; then
    PIDS="$(ss -lptnH "sport = :$PORT" 2>/dev/null | grep -o 'pid=[0-9]*' | cut -d= -f2 || true)"
elif command -v fuser >/dev/null 2>&1; then
    PIDS="$(fuser -n tcp "$PORT" 2>/dev/null | tr -s ' ' '\n' | grep -E '^[0-9]+$' || true)"
elif command -v netstat >/dev/null 2>&1; then
    PIDS="$(netstat -ano 2>/dev/null | awk -v port="$PORT" '$4=="LISTENING" && $2 ~ (":" port "$") {print $5}' | sort -u)"
fi

stop_pid() {
    kill "$1" 2>/dev/null && return 0
    if command -v taskkill >/dev/null 2>&1; then
        MSYS_NO_PATHCONV=1 taskkill /PID "$1" /F >/dev/null 2>&1 && return 0
    fi
    return 1
}

for pid in $PIDS; do
    echo "[AITP] 终止 PID ${pid}"
    stop_pid "$pid" && stopped=1
done

if [ "$stopped" = "1" ]; then
    sleep 1
    for pid in $PIDS; do
        stop_pid "$pid" || true
    done
fi

# 兜底：按 run.py 进程匹配
if command -v pgrep >/dev/null 2>&1; then
    for pid in $(pgrep -f "backend/run.py" 2>/dev/null || true); do
        kill "$pid" 2>/dev/null || true
        stopped=1
    done
fi

if [ "$stopped" = "1" ]; then
    log_deploy "[script] 服务已停止 | port=$PORT"
    echo "[AITP] 已停止。"
else
    log_deploy "[script] 无运行中的服务 | port=$PORT"
    echo "[AITP] 端口 ${PORT} 上没有运行中的 AITP。"
fi
