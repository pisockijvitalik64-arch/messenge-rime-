@echo off
chcp 65001 >nul
cd /d "%~dp0"
where python >nul 2>nul
if errorlevel 1 (
  where py >nul 2>nul
  if errorlevel 1 (
    echo Python не найден. Установи Python 3.10+ с https://python.org
    pause
    exit /b 1
  )
  py server.py %*
) else (
  python server.py %*
)
pause
