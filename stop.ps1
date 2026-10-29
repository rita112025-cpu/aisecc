<#
.SYNOPSIS
  停止由 start.ps1 啟動的後端（連同其子程序）。只會停止本專案記錄的程序。
#>
[CmdletBinding()]
param()

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$PidFile = Join-Path $Root ".run\backend.pid"

if (-not (Test-Path $PidFile)) {
    Write-Host "沒有執行中的服務紀錄（.run\backend.pid 不存在）。"
    exit 0
}
$procId = [int](Get-Content $PidFile)
$proc = Get-CimInstance Win32_Process -Filter "ProcessId = $procId" -ErrorAction SilentlyContinue
if (-not $proc) {
    Write-Host "程序 $procId 已不存在，清除紀錄。"
    Remove-Item $PidFile -Force
    exit 0
}
# Guard against a re-used PID: only stop our own backend (started via backend\launch.py).
if (-not $proc.CommandLine -or -not $proc.CommandLine.Contains("backend\launch.py")) {
    Write-Host "[警告] PID $procId 不是本系統的後端（可能已被其他程式重用），不予停止，僅清除紀錄。" -ForegroundColor Yellow
    Remove-Item $PidFile -Force
    exit 0
}
& taskkill.exe /PID $procId /T /F | Out-Null
if ($LASTEXITCODE -ne 0 -and (Get-Process -Id $procId -ErrorAction SilentlyContinue)) {
    Write-Host "[錯誤] 無法停止程序 $procId（exit $LASTEXITCODE）" -ForegroundColor Red
    exit 1
}
Remove-Item $PidFile -Force
Write-Host "已停止（PID $procId）。執行中的任務會在下次啟動時標記為「已中斷」。" -ForegroundColor Green
