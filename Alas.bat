@rem
@echo off
setlocal

set "_root=%~dp0"
set "_root=%_root:~0,-1%"
cd /d "%_root%"

color F0

set "_pyBin=%_root%\.venv\Scripts"
set "_GitBin=%_root%\.venv\Scripts\git\cmd"

if exist "%_pyBin%\python.exe" (
    set "PYTHON_EXE=%_pyBin%\python.exe"
    set "PYTHONW_EXE=%_pyBin%\pythonw.exe"
    set "PATH=%_pyBin%;%_GitBin%;%PATH%"
) else (
    set "PYTHON_EXE=python"
    set "PYTHONW_EXE=pythonw"
)

title AzurPilot Auto Sync & Updater
echo ============================================================
echo  AzurPilot 正在检查并同步上游官方更新...
echo ============================================================

"%PYTHON_EXE%" -m deploy.sync_upstream
if %errorlevel% neq 0 (
    echo.
    echo ============================================================
    echo [ERROR] 上游代码同步发生冲突，启动已终止！
    echo 请打开 DSH 告诉小肥鱼协助排错修复喵~
    echo ============================================================
    echo.
    pause
    exit /b 1
)

"%PYTHON_EXE%" -m deploy.installer
if %errorlevel% neq 0 (
    echo [Installer] 安装与环境就绪检查未通过，请检查错误输出。
    pause
) else (
    start "AzurPilot" "%PYTHONW_EXE%" "%_root%\gui.py" --electron
)
