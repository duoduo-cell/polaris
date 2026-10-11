# 注册开机自启：你登录 Windows 后自动在后台运行常驻采集（scripts\run_daemon.ps1）。
# 只注册在你自己的账号下、不用管理员权限。取消：Unregister-ScheduledTask -TaskName "Polaris 常驻采集" -Confirm:$false
$root = Split-Path -Parent $PSScriptRoot
$action = New-ScheduledTaskAction -Execute "powershell.exe" `
    -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\run_daemon.ps1`""
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
    -ExecutionTimeLimit (New-TimeSpan -Days 0) -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName "Polaris 常驻采集" -Action $action -Trigger $trigger -Settings $settings `
    -Description "北极星：按 config\collect.toml 的时间段和每天的量采集；被拦或掉登录会弹通知" -Force | Out-Null
Write-Output "已注册：下次登录 Windows 时自动开始。现在就想开始的话运行：Start-ScheduledTask -TaskName 'Polaris 常驻采集'"
