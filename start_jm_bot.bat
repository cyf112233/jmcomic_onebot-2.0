@echo off
REM ============================================================
REM JM Bot launcher (Windows)
REM
REM - 首次运行会在项目内创建 .venv 并安装 requirements.txt
REM - 不会向系统 Python 安装任何包
REM - 通过 JM_BOT_PYTHON 指定已有环境时只做依赖检查，不安装
REM - 机器人以退出码 3 请求重启时自动重新启动
REM ============================================================

setlocal
cd /d "%~dp0"

if defined JM_BOT_PYTHON goto use_external

set "PY=python"
where py >nul 2>nul
if not errorlevel 1 set "PY=py -3"

for /f "delims=" %%i in ('%PY% scripts\bootstrap.py --print-python') do set "APP_PY=%%i"
if not defined APP_PY goto setup_failed
goto run

:use_external
set "APP_PY=%JM_BOT_PYTHON%"
"%APP_PY%" scripts\bootstrap.py --check
if errorlevel 1 goto setup_failed
goto run

:setup_failed
echo [ERR] 依赖准备失败，请根据上方提示修复后重试。
exit /b 1

:run
echo [INFO] Starting jm-bot ...
"%APP_PY%" -m jm_bot.main %*
set "RC=%ERRORLEVEL%"

if "%RC%"=="3" (
    echo [INFO] Restart requested, restarting in 2 seconds ...
    timeout /t 2 /nobreak >nul
    goto run
)

echo [INFO] jm-bot exited with code=%RC%
exit /b %RC%
