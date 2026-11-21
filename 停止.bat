@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 停止 Auto Zhu

echo 正在停止 Auto Zhu ...

schtasks /End /TN "Auto Zhu" >nul 2>&1
if not errorlevel 1 echo   [OK] 已结束计划任务 "Auto Zhu"

powershell -NoProfile -ExecutionPolicy Bypass -Command "$p = @(Get-CimInstance Win32_Process | Where-Object { $_.Name -like 'python*' -and $_.CommandLine -and $_.CommandLine.Contains('%~dp0main.py') }); if ($p.Count -gt 0) { $p | ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue; Write-Host ('  [OK] 已停止进程 PID ' + $_.ProcessId) } } else { Write-Host '  [INFO] 没有找到本目录下运行的实例' }"

echo.
pause
