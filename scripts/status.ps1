# 看采集进度：双击运行（或在终端里运行 .\scripts\status.ps1）。
# 显示：常驻采集在不在跑、两个采集窗口、各平台登录、今天的量、最近采到的内容。
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
$env:PYTHONUTF8 = "1"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
& "$root\.venv\Scripts\python.exe" -X utf8 -m polaris.cli status
Write-Host ""
Write-Host "今天的日报："
& "$root\.venv\Scripts\python.exe" -X utf8 -m polaris.cli report
Write-Host ""
Read-Host "按回车关闭"
