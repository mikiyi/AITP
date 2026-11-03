@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"

set PORT=8760
if not "%AITP_PORT%"=="" set PORT=%AITP_PORT%
if not exist "logs" mkdir "logs" >nul 2>&1

echo [AITP] 停止端口 %PORT% 上的服务 ...
set STOPPED=0
for /f "tokens=5" %%p in ('netstat -ano ^| findstr ":%PORT%" ^| findstr "LISTENING"') do (
    echo [AITP] 终止 PID %%p
    taskkill /PID %%p /F >nul 2>&1
    set STOPPED=1
)

if "%STOPPED%"=="1" (
    2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 服务已停止 - port=%PORT%
    echo [AITP] 已停止。
) else (
    2>nul >>"logs\deploy.log" echo %DATE% %TIME% - DEPLOY - [script] 无运行中的服务 - port=%PORT%
    echo [AITP] 端口 %PORT% 上没有运行中的 AITP。
)
exit /b 0
