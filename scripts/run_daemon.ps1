# 常驻采集的启动脚本：等数据库起来，再启动 polaris daemon，输出写到 data\logs\daemon.log。
# 开机自启由 scripts\install_autostart.ps1 注册（只在你同意后运行）。
$ErrorActionPreference = "Continue"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root
New-Item -ItemType Directory -Force -Path "$root\data\logs" | Out-Null
$env:PYTHONUTF8 = "1"

# Docker Desktop 开机后要一两分钟才起来；数据库没好就等，最多 10 分钟
for ($i = 0; $i -lt 60; $i++) {
    docker exec polaris-db-1 pg_isready -U polaris *> $null
    if ($LASTEXITCODE -eq 0) { break }
    Start-Sleep -Seconds 10
}

# 日志由程序自己用 UTF-8 写（PowerShell 转写会把中文弄成乱码）；报错另存一份
& "$root\.venv\Scripts\python.exe" -X utf8 -m polaris.cli daemon --log "$root\data\logs\daemon.log" 2>> "$root\data\logs\daemon.err.log" | Out-Null
