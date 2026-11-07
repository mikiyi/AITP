@echo off
chcp 65001 >nul
setlocal enabledelayedexpansion
set PYTHONUTF8=1
set PYTHONIOENCODING=utf-8
set PYTHONUNBUFFERED=1
cd /d "%~dp0"

set PORT=8760
if not "%AITP_PORT%"=="" set PORT=%AITP_PORT%
set HOST=127.0.0.1
if not "%AITP_HOST%"=="" set HOST=%AITP_HOST%
set OPEN_BROWSER=1
set FORCE_BUILD=0
set FORCE_REINSTALL=0

:parse_args
if "%~1"=="" goto after_args
if /I "%~1"=="--build" (
    set FORCE_BUILD=1
    shift
    goto parse_args
)
if /I "%~1"=="--reinstall" (
    set FORCE_REINSTALL=1
    shift
    goto parse_args
)
if /I "%~1"=="--no-browser" (
    set OPEN_BROWSER=0
    shift
    goto parse_args
)
if /I "%~1"=="--help" (
    echo 用法: start.bat [--build] [--reinstall] [--no-browser]
    exit /b 0
)
echo 未知参数: %~1
exit /b 2

:after_args
if not exist "logs" mkdir "logs" >nul 2>&1

echo ============================================================
echo   AITestPlatform (AITP) 本地 AI 测试工作台
echo   http://%HOST%:%PORT%
echo ============================================================
echo.

REM ---- 0. 端口检查（服务已在运行则直接打开页面）----
set EXISTING_PID=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    set EXISTING_PID=%%p
    goto :got_pid
)
:got_pid
if defined EXISTING_PID (
    call :health_ok
    if "%ERRORLEVEL%"=="0" (
        echo [0/4] AITP 已在运行 ^(PID %EXISTING_PID%^)，直接打开页面。
        2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 检测到服务已在运行 - port=%PORT%
        if "%OPEN_BROWSER%"=="1" start "" http://%HOST%:%PORT%
        exit /b 0
    )
    echo [AITP] 端口 %PORT% 已被其他进程占用 ^(PID %EXISTING_PID%^)。
    echo        如需终止 AITP 请执行 stop.bat，或设置环境变量 AITP_PORT 使用其他端口。
    2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 端口占用退出 - port=%PORT%
    pause
    exit /b 1
)

REM ---- 1. 虚拟环境 ----
if "%FORCE_REINSTALL%"=="1" (
    if exist ".venv" (
        echo [1/4] 重建虚拟环境 .venv ...
        rmdir /s /q ".venv" >nul 2>&1
    )
)

if not exist ".venv\Scripts\python.exe" (
    if exist ".venv" (
        echo [AITP] 虚拟环境已存在但不可用（可能正在被占用），请先执行 stop.bat 再重试。
        pause
        exit /b 1
    )
    echo [1/4] 创建虚拟环境 .venv ...
    python -m venv .venv 2>nul
    if not exist ".venv\Scripts\python.exe" (
        echo [1/4] 当前 python 缺少 venv 模块，改用 virtualenv ...
        python -m virtualenv .venv 2>nul
    )
    if not exist ".venv\Scripts\python.exe" (
        echo [1/4] 改用 Python 3.13 ...
        py -3.13 -m venv .venv 2>nul
    )
    if not exist ".venv\Scripts\python.exe" (
        echo [AITP] 虚拟环境创建失败，请确认已安装 Python 3.11+ 与 virtualenv。
        pause
        exit /b 1
    )
    echo [1/4] 安装依赖 ...
    ".venv\Scripts\python.exe" -m pip install --upgrade pip -q
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
    if errorlevel 1 (
        echo [AITP] 依赖安装失败，请检查网络。
        pause
        exit /b 1
    )
    2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 虚拟环境创建并安装依赖
) else (
    echo [1/4] 虚拟环境已存在，跳过。
)

if "%FORCE_REINSTALL%"=="1" (
    echo [1/4] 强制重装依赖 ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)

REM ---- 2. 前端构建 ----
set NEED_BUILD=0
if not exist "frontend\package.json" goto :skip_frontend
if not exist "backend\aitp\static\index.html" set NEED_BUILD=1
if "%FORCE_BUILD%"=="1" set NEED_BUILD=1

if "%NEED_BUILD%"=="1" (
    where npm >nul 2>&1
    if errorlevel 1 (
        echo [2/4] 未找到 npm，跳过前端构建（后端仍会以基础页面启动）。
    ) else (
        if not exist "frontend\node_modules" (
            echo [2/4] 安装前端依赖 ...
            pushd frontend
            call npm install
            popd
        )
        echo [2/4] 构建前端 ...
        pushd frontend
        call npm run build
        if errorlevel 1 (
            echo [AITP] 前端构建失败，后端仍会以基础页面启动。
        )
        popd
    )
    goto :after_frontend
)

:skip_frontend
echo [2/4] 未找到 frontend 工程，跳过前端构建。

:after_frontend

REM ---- 3. 端口复查 ----
set EXISTING_PID=
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    set EXISTING_PID=%%p
    goto :got_pid2
)
:got_pid2
if defined EXISTING_PID (
    echo [3/4] 端口 %PORT% 在启动过程中被占用 ^(PID %EXISTING_PID%^)，退出。
    2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 端口二次占用退出 - port=%PORT%
    pause
    exit /b 1
)
echo [3/4] 端口 %PORT% 可用。

REM ---- 4. 启动 ----
echo [4/4] 正在启动服务 ...
2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 启动服务 - host=%HOST% port=%PORT%
if "%OPEN_BROWSER%"=="1" start "" http://%HOST%:%PORT%
".venv\Scripts\python.exe" backend\run.py

echo.
echo [AITP] 服务已停止。
2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 服务退出 - port=%PORT%
pause
exit /b 0

REM ---- 健康检查子程序：0=服务在线 ----
:health_ok
where curl >nul 2>&1
if not errorlevel 1 (
    curl -fsS --max-time 3 "http://%HOST%:%PORT%/api/v1/health" 2>nul | findstr /C:"200000" >nul
    exit /b !ERRORLEVEL!
)
powershell -NoProfile -Command "try { $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 -Uri 'http://%HOST%:%PORT%/api/v1/health'; if ($r.Content -match '200000') { exit 0 } } catch { }; exit 1" >nul 2>&1
exit /b %ERRORLEVEL%
