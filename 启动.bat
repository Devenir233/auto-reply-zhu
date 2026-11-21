@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Auto Zhu
echo ==========================================
echo   Auto Zhu - 前台运行，按 Ctrl+C 停止
echo ==========================================
echo.
python main.py %*
set EXITCODE=%ERRORLEVEL%
echo.
echo 程序已退出，退出码 %EXITCODE%
pause
