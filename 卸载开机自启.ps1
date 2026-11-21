#Requires -Version 5.1
<#
  取消 Auto Zhu 开机自启，并停止正在运行的实例。
#>
param([string]$TaskName = 'Auto Zhu')

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    Write-Host "没有找到计划任务：$TaskName（可能本来就没装）"
    return
}

Stop-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Write-Host "[OK] 已移除开机自启任务：$TaskName"
