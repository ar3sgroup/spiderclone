@echo off
rem SpiderClone - lanceur Windows
rem by xyrek from ar3s - 2026-09-22
setlocal
title SpiderClone
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python was not found in PATH. Install Python 3 and retry.
    pause
    exit /b 1
)

python spiderclone.py

echo.
pause
endlocal
