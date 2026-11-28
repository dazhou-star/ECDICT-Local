@echo off
setlocal EnableExtensions
chcp 65001 >nul
title ECDICT-Local 一键部署

cd /d "%~dp0"
echo.
echo ==========================================
echo       ECDICT-Local 一键部署
echo ==========================================
echo.

rem Prefer the existing Python 3.10 installation; do not create another environment.
set "PYTHON_CMD="
py -3.10 --version >nul 2>&1
if not errorlevel 1 set "PYTHON_CMD=py -3.10"

if not defined PYTHON_CMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3,10) else 1)" >nul 2>&1
    if not errorlevel 1 set "PYTHON_CMD=python"
)

if not defined PYTHON_CMD (
    echo [错误] 未找到 Python 3.10 或更高版本。
    echo 请安装 Python 3.10+ 并勾选 Add Python to PATH，或安装 Python Launcher。
    pause
    exit /b 1
)

echo [1/4] 使用 Python：
%PYTHON_CMD% --version
if errorlevel 1 goto :failed

echo.
echo [2/4] 检查并安装项目依赖...
%PYTHON_CMD% -m pip --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 当前 Python 环境没有可用的 pip。
    echo 请先修复该 Python 环境中的 pip，再重新运行本脚本。
    goto :failed
)
%PYTHON_CMD% -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :failed

echo.
echo [3/4] 检查词典数据库...
if not exist "database" mkdir "database"
if not exist "database\image_imports" mkdir "database\image_imports"

if defined ECDICT_DB (
    set "DB_PATH=%ECDICT_DB%"
) else (
    set "DB_PATH=database\dictionary.db"
)

if not exist "%DB_PATH%" (
    echo 尚未发现数据库，首次部署将从 ecdict.csv 创建 SQLite 数据库。
    if not exist "ecdict.csv" (
        echo [错误] 缺少 ecdict.csv，无法初始化词典数据库。
        goto :failed
    )
    %PYTHON_CMD% scripts\build_database.py --csv "ecdict.csv" --db "%DB_PATH%"
    if errorlevel 1 goto :failed
) else (
    echo 已发现数据库：%DB_PATH%
    echo 为保护现有数据，本脚本不会重建、覆盖或迁移替换该数据库。
    %PYTHON_CMD% -c "import os,sqlite3,sys; p=os.environ.get('ECDICT_DB','database/dictionary.db'); c=sqlite3.connect(p); ok=c.execute('SELECT 1 FROM sqlite_master WHERE type=? AND name=?',('table','words')).fetchone(); n=c.execute('SELECT COUNT(*) FROM words').fetchone()[0] if ok else 0; c.close(); print('现有词条数：',n); sys.exit(0 if n>0 else 2)"
    if errorlevel 1 (
        echo [错误] 数据库存在，但没有可用的 words 词条表。
        echo 为避免覆盖用户数据，部署脚本已停止。请先备份 database 文件夹并手动排查。
        goto :failed
    )
)

echo.
echo [4/4] 部署准备完成，正在启动服务...
call "启动.bat"
exit /b 0

:failed
echo.
echo [部署未完成] 请根据上方错误信息处理后重新运行。
pause
exit /b 1
