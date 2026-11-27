@echo off
chcp 65001 >nul
cd /d "%~dp0"
title Auto Zhu 设置
set PYTHONIOENCODING=utf-8

where python >nul 2>&1
if errorlevel 1 (
  echo [错误] 没找到 python，请先安装 Python 3.10+ 并加入 PATH
  echo        下载地址: https://www.python.org/downloads/
  echo.
  pause
  exit /b 1
)

echo 正在打开图形设置页面（浏览器会自动弹出）...
echo 改完点「保存」即可，重启 Auto Zhu 后生效。
echo.
python "%~dp0main.py" --gui
echo.
pause
