@echo off
chcp 65001 >nul 2>&1
cd /d "%~dp0"

REM 检查 Python 是否可用（优先 py launcher，其次 python）
set "PY_CMD="
where py >nul 2>&1 && set "PY_CMD=py"
if not defined PY_CMD (
  where python >nul 2>&1 && set "PY_CMD=python"
)
if not defined PY_CMD (
  echo 错误：未找到 Python，请先安装 Python 3.11 或更高版本。
  echo 下载地址：https://www.python.org/downloads/
  pause
  exit /b 127
)

REM 检查 Python 版本
%PY_CMD% -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 11) else 1)" >nul 2>&1
if errorlevel 1 (
  echo 错误：总控台需要 Python 3.11 或更高版本。
  %PY_CMD% --version
  pause
  exit /b 126
)

%PY_CMD% server.py --launcher
