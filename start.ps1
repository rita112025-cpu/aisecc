<#
.SYNOPSIS
  啟動 AI 軟體工程控制中心（只綁定 127.0.0.1，單一 worker），並開啟瀏覽器。
.PARAMETER Port
  連接埠，預設 8765。
.PARAMETER NoBrowser
  不自動開啟瀏覽器。
#>
[CmdletBinding()]
param(
    [int]$Port = 8765,
    [switch]$NoBrowser
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$RunDir = Join-Path $Root ".run"
$PidFile = Join-Path $RunDir "backend.pid"
$DataDir = if ($env:AISECC_DATA_DIR) { $env:AISECC_DATA_DIR } else { Join-Path $Root "data" }
$LogDir = Join-Path $DataDir "logs"
$Url = "http://127.0.0.1:$Port"

function Fail([string]$Text) {
    Write-Host "[錯誤] $Text" -ForegroundColor Red
    Write-Host "請參考 TROUBLESHOOTING.md。" -ForegroundColor Yellow
    exit 1
}

if (-not (Test-Path $Py)) { Fail "尚未安裝，請先執行 .\install.ps1" }
if (-not (Test-Path (Join-Path $Root "frontend\dist\index.html"))) { Fail "找不到前端建置檔（frontend\dist），請重新執行 .\install.ps1" }

if (Test-Path $PidFile) {
    $old = Get-Content $PidFile -ErrorAction SilentlyContinue
    if ($old -and (Get-Process -Id $old -ErrorAction SilentlyContinue)) {
        Write-Host "已在執行中（PID $old）：$Url" -ForegroundColor Yellow
        if (-not $NoBrowser) { Start-Process $Url }
        exit 0
    }
    Remove-Item $PidFile -Force
}

$inUse = Get-NetTCPConnection -LocalAddress 127.0.0.1 -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($inUse) { Fail "連接埠 $Port 已被其他程式使用（PID $($inUse[0].OwningProcess)）。可用 -Port 指定其他連接埠。" }

New-Item -ItemType Directory -Force $RunDir, $LogDir | Out-Null
$serverLog = Join-Path $LogDir "server.log"
# Start-Process without redirection gives the server its own hidden console and no inherited
# handles, so it keeps running after this shell exits. Quoting is manual (PowerShell 5.1).
$launchArgs = "`"$(Join-Path $Root "backend\launch.py")`" --port $Port --log-dir `"$LogDir`""
$proc = Start-Process -FilePath $Py -ArgumentList $launchArgs -WorkingDirectory $Root -WindowStyle Hidden -PassThru
Set-Content -Path $PidFile -Value $proc.Id -Encoding ascii

Write-Host "啟動中（PID $($proc.Id)）…"
$ready = $false
for ($i = 0; $i -lt 60; $i++) {
    Start-Sleep -Milliseconds 500
    if ($proc.HasExited) { break }
    try {
        $r = Invoke-WebRequest -Uri "$Url/api/health" -UseBasicParsing -TimeoutSec 2
        if ($r.StatusCode -eq 200) { $ready = $true; break }
    } catch { }
}
if (-not $ready) {
    Write-Host (Get-Content $serverLog -Tail 20 -ErrorAction SilentlyContinue | Out-String)
    if (-not $proc.HasExited) { Stop-Process -Id $proc.Id -Force }
    Remove-Item $PidFile -Force -ErrorAction SilentlyContinue
    Fail "後端未能在 30 秒內啟動，記錄檔：$serverLog"
}

Write-Host "已啟動：$Url" -ForegroundColor Green
Write-Host "停止服務：.\stop.ps1；記錄檔：$LogDir"
if (-not $NoBrowser) { Start-Process $Url }
