$Root = "D:\黑客松\polaris-docs\polaris"
$py = "$Root\.venv\Scripts\python.exe"
$check = "from polaris.collectors.locks import platform_lock, PlatformBusy`nimport sys`nbusy=[]`nfor s in ('xhs','maimai','boss','tencent','bytedance','xhs_careers'):`n    try:`n        with platform_lock(s, timeout=0.3): pass`n    except PlatformBusy: busy.append(s)`nprint(','.join(busy) or 'idle')"
for ($i = 0; $i -lt 240; $i++) {
    Set-Location $Root
    $state = (& $py -X utf8 -c $check 2>$null | Select-Object -Last 1)
    $last = (Get-Item "$Root\data\logs\daemon.log").LastWriteTime
    if ($state -eq "idle" -and ((Get-Date) - $last).TotalSeconds -gt 20) {
        Stop-ScheduledTask -TaskName "Polaris 常驻采集"
        for ($j = 0; $j -lt 30; $j++) {
            $alive = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'polaris.cli daemon' }
            if (-not $alive) { break }
            Start-Sleep -Seconds 1
        }
        $alive = Get-CimInstance Win32_Process -Filter "Name='python.exe'" | Where-Object { $_.CommandLine -match 'polaris.cli daemon' }
        if ($alive) { $alive | ForEach-Object { Stop-Process -Id $_.ProcessId -Force } }
        Start-ScheduledTask -TaskName "Polaris 常驻采集"
        "restarted at $(Get-Date -Format HH:mm:ss) (was idle)"
        exit 0
    }
    Start-Sleep -Seconds 30
}
"gave up: daemon never idle within 2 hours"
