#Requires -Version 5.1
<#
  安装 Auto Zhu 开机自启（当前用户登录时自动运行，无窗口）。
  运行前提：已经能手动 `python main.py` 正常工作（token、群号都配好）。
#>
param([string]$TaskName = 'Auto Zhu')

$ErrorActionPreference = 'Stop'

$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$main = Join-Path $root 'main.py'
if (-not (Test-Path $main)) { throw "找不到 main.py：$main" }

$pythonExe = (& python -c 'import sys; print(sys.executable)').Trim()
$pythonw = Join-Path (Split-Path -Parent $pythonExe) 'pythonw.exe'
if (-not (Test-Path $pythonw)) { $pythonw = $pythonExe }

$action = New-ScheduledTaskAction -Execute $pythonw -Argument ('"' + $main + '"') -WorkingDirectory $root
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit ([TimeSpan]::Zero) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Description 'QQ 群消息自动贴表情（Auto Zhu）' -Force | Out-Null

Write-Host "[OK] 已安装开机自启任务：$TaskName"
Write-Host "     解释器：$pythonw"
Write-Host "     入口：  $main"
Write-Host "     日志：  $(Join-Path $root 'logs\auto-zhu.log')"
Write-Host ""
Write-Host "常用命令："
Write-Host "  Start-ScheduledTask -TaskName '$TaskName'     # 立即启动"
Write-Host "  Stop-ScheduledTask  -TaskName '$TaskName'     # 停止"
Write-Host "  Get-ScheduledTask   -TaskName '$TaskName' | Get-ScheduledTaskInfo   # 查看状态"
Write-Host "  右键本目录的 卸载开机自启.ps1 可取消自启"
Write-Host ""
Write-Host "提示：LLBot 也要常驻（建议同样把它加进开机启动），否则本程序会一直等它连上来。"
