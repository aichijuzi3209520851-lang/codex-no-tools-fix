@echo off
chcp 65001 >nul 2>&1
setlocal
title Codex Tool Fix
cd /d "%~dp0"
set "RC=0"

echo.
echo ============================================================
echo    Codex Desktop Tool Fix  /  One-click repair
echo ============================================================
echo.

REM ---------- locate Python 3.11+ ----------
set "PY="
where py >nul 2>&1
if not errorlevel 1 (
    py -3.11 -c "import sys" >nul 2>&1
    if not errorlevel 1 set "PY=py -3.11"
)
if not defined PY (
    where python >nul 2>&1
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    if exist "D:\python\python.exe" set "PY=D:\python\python.exe"
)
if not defined PY (
    if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
        set "PY=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
    )
)

if not defined PY goto nopython

echo    Python: %PY%
echo.

REM ---------- run ----------
set PYTHONIOENCODING=utf-8
%PY% "%~dp0fix_codex_tools.py" %*
set "RC=%ERRORLEVEL%"
goto done

:nopython
echo [ERROR] Python 3.11+ not found.
echo.
echo   Install it from https://www.python.org/downloads/
echo   and tick "Add Python to PATH" during setup.
echo.
set "RC=2"
goto done

:done
echo.
echo ============================================================
if "%RC%"=="0" goto ok
if "%RC%"=="1" goto warn
echo   [FAILED] exit code %RC%
goto end
:ok
echo   [OK] done
goto end
:warn
echo   [WARN] some checks failed - see [ERR] lines above
goto end

:end
echo ============================================================
echo.

REM pause only when double-clicked (no arguments)
if "%~1"=="" (
    pause
)
exit /b %RC%
