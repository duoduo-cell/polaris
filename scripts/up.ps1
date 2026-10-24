# 把北极星整套在后台拉起来：数据库 → API(8000) → 网页(3000) → 常驻采集（它自己会打开两个采集窗口）。
# 加 -Demo 再起合成演示：独立库(55432) → 演示 API(8010) → 演示网页(3010)。已经在跑的部分跳过，可以重复运行。
#
# 用 WMI 启动，进程不挂在当前终端或编程软件下面（10-24：从编程工具里起的采集和网页，工具一重启就被一起带走）。
# 日志在 data\logs\。停止：关掉对应进程，或 Stop-Process 端口对应的进程；采集用 polaris status 看。
# 模型：个人 .env 里的中转服务目前不可用，API 和采集都用百炼（scripts\run_*_bailian.py 只在进程里覆盖，不改 .env）。
param([switch]$Demo, [switch]$NoCollect)

$root = Split-Path -Parent $PSScriptRoot
$py = "$root\.venv\Scripts\python.exe"
$logs = "$root\data\logs"
New-Item -ItemType Directory -Force -Path $logs | Out-Null

function Start-Detached([string]$command, [string]$dir) {
    $startup = New-CimInstance -ClassName Win32_ProcessStartup -ClientOnly -Property @{ ShowWindow = [uint16]0 }
    $r = Invoke-CimMethod -ClassName Win32_Process -MethodName Create -Arguments @{
        CommandLine = $command; CurrentDirectory = $dir; ProcessStartupInformation = $startup }
    if ($r.ReturnValue -ne 0) { Write-Host "启动失败（$($r.ReturnValue)）：$command" -ForegroundColor Red }
}
function Test-Listening([int]$port) { [bool](Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) }
function Wait-Http([string]$url, [int]$seconds = 120) {
    foreach ($i in 1..($seconds / 2)) {
        try { Invoke-WebRequest -UseBasicParsing $url -TimeoutSec 5 | Out-Null; return $true } catch { Start-Sleep 2 }
    }
    return $false
}

Write-Host "数据库……"
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop 没开：先打开它，等变绿再运行。" -ForegroundColor Red; exit 1 }
Set-Location $root
docker compose up -d db neo4j 2>&1 | Out-Null
foreach ($i in 1..30) { docker exec polaris-db-1 pg_isready -U polaris -d polaris *> $null; if ($LASTEXITCODE -eq 0) { break }; Start-Sleep 2 }

if (Test-Listening 8000) { Write-Host "API 8000 已在跑" } else {
    Start-Detached "cmd /c set PYTHONUTF8=1&& `"$py`" -X utf8 scripts\run_web_bailian.py --port 8000 1>>`"$logs\api.out.log`" 2>>`"$logs\api.err.log`"" $root
    Write-Host ("API 8000：" + $(if (Wait-Http "http://127.0.0.1:8000/health") { "好了" } else { "没起来，看 data\logs\api.err.log" }))
}
if (Test-Listening 3000) { Write-Host "网页 3000 已在跑" } else {
    Start-Detached "cmd /c npm run dev -- -H 127.0.0.1 --port 3000 1>>`"$logs\web.out.log`" 2>>`"$logs\web.err.log`"" "$root\web"
    Write-Host ("网页 3000：" + $(if (Wait-Http "http://127.0.0.1:3000") { "好了" } else { "没起来，看 data\logs\web.err.log" }))
}
if (-not $NoCollect) {
    # 常驻采集自带单实例锁：已经在跑时新起的会自己退出
    Start-Detached "powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$root\scripts\run_daemon.ps1`" -Bailian" $root
    Write-Host "常驻采集：已启动（日志 data\logs\daemon.log；Boss 要在它打开的专用窗口里登录）"
}

if ($Demo) {
    $envFile = "$root\.env.demo"
    if (-not (Test-Path $envFile)) { Write-Host "没有 .env.demo：按 docs\DEMO.md 第 1 步准备" -ForegroundColor Red; exit 1 }
    docker compose --env-file $envFile -f compose.demo.yml up -d --wait db 2>&1 | Out-Null
    $demoEnv = "set PYTHONUTF8=1&& set POLARIS_ENV_FILE=$envFile&& set POLARIS_API=http://127.0.0.1:8010&& set POLARIS_NEXT_DIST=.next-demo&&"
    if (Test-Listening 8010) { Write-Host "演示 API 8010 已在跑" } else {
        Start-Detached "cmd /c $demoEnv `"$py`" -X utf8 scripts\run_demo_bailian.py --port 8010 1>>`"$logs\demo-api.out.log`" 2>>`"$logs\demo-api.err.log`"" $root
        Write-Host ("演示 API 8010：" + $(if (Wait-Http "http://127.0.0.1:8010/health") { "好了" } else { "没起来，看 data\logs\demo-api.err.log" }))
    }
    if (Test-Listening 3010) { Write-Host "演示网页 3010 已在跑" } else {
        if (-not (Test-Path "$root\web\.next-demo\BUILD_ID")) {
            Write-Host "第一次用演示网页，先构建（几分钟）……"
            cmd /c "$demoEnv npm --prefix web run build" | Out-Null
        }
        Start-Detached "cmd /c $demoEnv npx next start -H 127.0.0.1 -p 3010 1>>`"$logs\demo-web.out.log`" 2>>`"$logs\demo-web.err.log`"" "$root\web"
        Write-Host ("演示网页 3010：" + $(if (Wait-Http "http://127.0.0.1:3010") { "好了" } else { "没起来，看 data\logs\demo-web.err.log" }))
    }
}
Write-Host ("好了：个人 http://127.0.0.1:3000" + $(if ($Demo) { "，演示 http://127.0.0.1:3010" } else { "" })) -ForegroundColor Green
