@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Auto Zhu
set PYTHONIOENCODING=utf-8

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 没找到 python，请先安装 Python 3.10+ 并加入 PATH
  echo        下载地址: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo ==========================================
echo   Auto Zhu - 前台运行，按 Ctrl+C 停止
echo   关闭这个窗口也会停止
echo ==========================================
echo.
python "%~dp0main.py" %*
set "EXITCODE=%ERRORLEVEL%"
echo.
echo 程序已退出，退出码 %EXITCODE%
pause
