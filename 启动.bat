@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ECDICT-Local 启动

cd /d "%~dp0"

set "PYTHON_CMD="
py -3.10 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.10"
if not defined PYTHON_CMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)
if not defined PYTHON_CMD (
    echo [错误] 未找到 Python 3.10 或更高版本。
    pause
    exit /b 1
)

if not defined ECDICT_PORT set "ECDICT_PORT=8000"

if not exist "database\dictionary.db" if not defined ECDICT_DB (
    echo [错误] 尚未创建 database\dictionary.db，请先运行 一键部署.bat。
    pause
    exit /b 1
)

echo ECDICT-Local 正在启动...
echo 本机访问：http://127.0.0.1:%ECDICT_PORT%
echo 局域网访问：请使用本机局域网 IP 和端口 %ECDICT_PORT%
echo 按 Ctrl+C 可停止服务。
echo.

start "ECDICT-Local Server" /D "%~dp0" cmd /k "%PYTHON_CMD% -m uvicorn backend.main:app --host 0.0.0.0 --port %ECDICT_PORT%"
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:%ECDICT_PORT%"
echo.
echo 服务已在独立窗口中启动。关闭服务窗口或按 Ctrl+C 可停止服务。
pause
