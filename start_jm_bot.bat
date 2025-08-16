@echo off
setlocal enabledelayedexpansion

REM ============================================
REM JM Bot launcher (Windows)
REM - Check and install missing dependencies
REM - Launch the bot main module
REM Usage: double-click or run in a terminal
REM ============================================

REM Change to the script directory
cd /d "%~dp0"

REM Choose Python launcher
where py >nul 2>nul
if %errorlevel%==0 (
  set "PY=py -3"
) else (
  set "PY=python"
)

echo [INFO] Using interpreter: %PY%
%PY% --version

echo.
echo [STEP] Checking and installing dependencies...
%PY% scripts\check_and_install.py
set RC=%ERRORLEVEL%
if not "%RC%"=="0" (
  echo [WARN] Dependency check returned: %RC%. Trying to launch anyway...
)

echo.
echo [STEP] Launching JM Bot main...
%PY% -m jm_bot.main
set APP_RC=%ERRORLEVEL%
echo.
echo [EXIT] JM Bot exited with code=%APP_RC%

echo.
pause
endlocal & exit /b %APP_RC%
