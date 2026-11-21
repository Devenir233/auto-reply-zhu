@echo off
chcp 65001 >nul
schtasks /End /TN "Auto Zhu" >nul 2>&1
if errorlevel 1 (
  echo 没有正在运行的 Auto Zhu 计划任务
) else (
  echo 已停止 Auto Zhu
)
pause
