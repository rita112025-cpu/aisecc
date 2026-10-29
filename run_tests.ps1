<#
.SYNOPSIS
  執行全部自動化測試並輸出摘要：後端 pytest + ruff、前端 tsc + vitest、Playwright E2E。
.PARAMETER SkipE2E
  略過 E2E（例如未下載 Playwright Chromium 時）。略過會明確記錄為 SKIPPED，不算通過。
#>
[CmdletBinding()]
param(
    [switch]$SkipE2E
)

$ErrorActionPreference = "Continue"
$Root = $PSScriptRoot
$Py = Join-Path $Root ".venv\Scripts\python.exe"
$Ruff = Join-Path $Root ".venv\Scripts\ruff.exe"
$results = [ordered]@{}

if (-not (Test-Path $Py)) {
    Write-Host "[錯誤] 尚未安裝，請先執行 .\install.ps1" -ForegroundColor Red
    exit 1
}

function Run-Step([string]$Name, [string]$Dir, [scriptblock]$Block) {
    Write-Host ""
    Write-Host "==> $Name" -ForegroundColor Cyan
    Push-Location $Dir
    try {
        & $Block
        $code = $LASTEXITCODE
    } finally {
        Pop-Location
    }
    $script:results[$Name] = if ($code -eq 0) { "PASS" } else { "FAIL (exit $code)" }
}

Run-Step "後端 pytest" (Join-Path $Root "backend") { & $Py -m pytest -q -rs -p no:cacheprovider }
Run-Step "後端 ruff" (Join-Path $Root "backend") { & $Ruff check . }
Run-Step "前端 tsc" (Join-Path $Root "frontend") { & npx --no-install tsc --noEmit -p . }
Run-Step "前端 vitest" (Join-Path $Root "frontend") { & npx --no-install vitest run }
if ($SkipE2E) {
    $results["E2E playwright"] = "SKIPPED（-SkipE2E）"
} else {
    Run-Step "前端建置" (Join-Path $Root "frontend") { & npx --no-install vite build }
    Run-Step "E2E playwright" (Join-Path $Root "frontend") { & npx --no-install playwright test }
}

Write-Host ""
Write-Host "================ 測試摘要 ================"
$failed = $false
foreach ($k in $results.Keys) {
    $v = $results[$k]
    $color = if ($v -eq "PASS") { "Green" } elseif ($v -like "SKIPPED*") { "Yellow" } else { "Red" }
    if ($v -notin @("PASS") -and $v -notlike "SKIPPED*") { $failed = $true }
    Write-Host ("{0,-20} {1}" -f $k, $v) -ForegroundColor $color
}
if ($failed) { exit 1 }
exit 0
