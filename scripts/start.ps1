# 一键启动北极星的网页端：数据库 → API（8000）→ 网页（3000）→ 打开浏览器。
# 用法（在 PowerShell 里，仓库根目录下）：
#   .\scripts\start.ps1            用真实的库（你的真实记忆）
#   .\scripts\start.ps1 -Sandbox   用沙盒库 polaris_sandbox（随便测，不碰真实记忆；先运行一次 uv run polaris sandbox）
# API 和网页各开一个窗口，关掉窗口就停了。
param([switch]$Sandbox)

# 不用 Stop：docker 往 stderr 打的警告在 PowerShell 5.1 里会被当成错误中断；这里靠 $LASTEXITCODE 判断
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Test-Port([int]$Port) {
    $c = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) {
        $p = Get-Process -Id $c.OwningProcess -ErrorAction SilentlyContinue
        Write-Host "端口 $Port 已经被占用（进程 $($c.OwningProcess) $($p.ProcessName)）。" -ForegroundColor Yellow
        Write-Host "  如果是之前开的北极星窗口，关掉它；或者运行：Stop-Process -Id $($c.OwningProcess)" -ForegroundColor Yellow
        return $false
    }
    return $true
}

Write-Host "1/4 检查 Docker 和数据库……"
docker info *> $null
if ($LASTEXITCODE -ne 0) { Write-Host "Docker Desktop 没开：先打开 Docker Desktop，等它变绿再运行。" -ForegroundColor Red; exit 1 }
docker compose up -d db neo4j | Out-Null
$ok = $false
foreach ($i in 1..30) {
    docker exec polaris-db-1 pg_isready -U polaris -d polaris *> $null
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
    Start-Sleep -Seconds 2
}
if (-not $ok) { Write-Host "数据库 60 秒内没起来，看 docker compose logs db" -ForegroundColor Red; exit 1 }

Write-Host "2/4 检查端口……"
if (-not ((Test-Port 8000) -and (Test-Port 3000))) { exit 1 }

$db = "polaris"
$envLine = ""
if ($Sandbox) {
    $db = "polaris_sandbox"
    $exists = docker exec polaris-db-1 psql -U polaris -d postgres -Atc "select 1 from pg_database where datname='polaris_sandbox'"
    if ($exists -ne "1") { Write-Host "还没有沙盒库：先运行 uv run polaris sandbox" -ForegroundColor Red; exit 1 }
    $envLine = "`$env:DATABASE_URL='postgresql+psycopg://polaris:polaris@127.0.0.1:5432/polaris_sandbox'; "
}

Write-Host "3/4 启动 API（连的库：$db）……"
$apiCmd = "Set-Location '$Root'; `$Host.UI.RawUI.WindowTitle='北极星 API ($db)'; $envLine uv run polaris serve --port 8000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $apiCmd | Out-Null

Write-Host "4/4 启动网页……"
if (-not (Test-Path "$Root\web\node_modules")) {
    Write-Host "  第一次用，先装网页的依赖（几分钟）……"
    npm --prefix web install --registry https://registry.npmmirror.com
}
$webCmd = "Set-Location '$Root\web'; `$Host.UI.RawUI.WindowTitle='北极星 网页'; npm run dev -- --port 3000"
Start-Process powershell -ArgumentList "-NoExit", "-Command", $webCmd | Out-Null

foreach ($i in 1..60) {
    try { Invoke-WebRequest -UseBasicParsing http://127.0.0.1:8000/health -TimeoutSec 2 | Out-Null; break } catch { Start-Sleep -Seconds 2 }
}
foreach ($i in 1..60) {
    try { Invoke-WebRequest -UseBasicParsing http://localhost:3000 -TimeoutSec 5 | Out-Null; break } catch { Start-Sleep -Seconds 2 }
}
Start-Process "http://localhost:3000"
Write-Host "好了：http://localhost:3000 （库：$db）。停掉：关掉那两个窗口。" -ForegroundColor Green
