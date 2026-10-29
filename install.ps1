<#
.SYNOPSIS
  安裝 AI 軟體工程控制中心（Windows 原生，不需要系統管理員權限、不需要 WSL）。
.DESCRIPTION
  1. 檢查 Python 3.12、Node.js、git
  2. 建立 .venv 並安裝後端套件（含 ruff、bandit、pip-audit 分析工具）
  3. 安裝前端套件並建置靜態頁面（frontend\dist）
  4. 初始化 SQLite 資料庫（Alembic migration）
.PARAMETER SkipPlaywrightBrowser
  不下載 Playwright Chromium（約 90–150MB）。之後 run_tests.ps1 的 E2E 會被標為略過。
#>
[CmdletBinding()]
param(
    [switch]$SkipPlaywrightBrowser
)

$ErrorActionPreference = "Stop"
$Root = $PSScriptRoot
$Venv = Join-Path $Root ".venv"
$Py = Join-Path $Venv "Scripts\python.exe"
$Frontend = Join-Path $Root "frontend"

function Write-Step([string]$Text) { Write-Host "==> $Text" -ForegroundColor Cyan }
function Fail([string]$Text) {
    Write-Host "[錯誤] $Text" -ForegroundColor Red
    Write-Host "請參考 TROUBLESHOOTING.md。" -ForegroundColor Yellow
    exit 1
}
function Invoke-Checked([string]$Exe, [string[]]$Arguments, [string]$What) {
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { Fail "$What 失敗（exit $LASTEXITCODE）" }
}

Write-Step "檢查必要工具"
$basePython = $null
if (Get-Command py -ErrorAction SilentlyContinue) {
    $v = & py -3.12 -c "import sys; print(sys.executable)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { $basePython = $v.Trim() }
}
if (-not $basePython -and (Get-Command python -ErrorAction SilentlyContinue)) {
    $ver = & python -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($ver -eq "3.12") { $basePython = (& python -c "import sys; print(sys.executable)").Trim() }
}
if (-not $basePython) { Fail "找不到 Python 3.12。請從 https://www.python.org/downloads/ 安裝（勾選 Add to PATH；不需系統管理員）。" }
Write-Host "Python: $basePython"

if (-not (Get-Command node -ErrorAction SilentlyContinue)) { Fail "找不到 Node.js（需要 18 以上）。請從 https://nodejs.org/ 安裝。" }
$nodeMajor = [int]((& node --version).TrimStart("v").Split(".")[0])
if ($nodeMajor -lt 18) { Fail "Node.js 版本過舊（$(& node --version)），需要 18 以上。" }
Write-Host "Node.js: $(& node --version)"
if (-not (Get-Command npm -ErrorAction SilentlyContinue)) { Fail "找不到 npm（通常隨 Node.js 安裝）。" }
if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
    Write-Host "[警告] 找不到 git：Git 資訊與 AI 修復將無法使用，其他功能正常。" -ForegroundColor Yellow
} else {
    Write-Host "git: $(& git --version)"
}

Write-Step "建立 Python 虛擬環境 (.venv)"
if (-not (Test-Path $Py)) { Invoke-Checked $basePython @("-m", "venv", $Venv) "建立 .venv" }
Invoke-Checked $Py @("-m", "pip", "install", "--disable-pip-version-check", "-q", "-r", (Join-Path $Root "backend\requirements-dev.txt")) "安裝後端套件"

Write-Step "安裝前端套件並建置"
Push-Location $Frontend
try {
    if (Test-Path "package-lock.json") {
        Invoke-Checked "npm" @("ci", "--no-fund", "--no-audit") "npm ci"
    } else {
        Invoke-Checked "npm" @("install", "--no-fund", "--no-audit") "npm install"
    }
    Invoke-Checked "npx" @("--no-install", "vite", "build") "前端建置"
    if (-not $SkipPlaywrightBrowser) {
        Invoke-Checked "npx" @("--no-install", "playwright", "install", "chromium") "下載 Playwright Chromium"
    }
} finally {
    Pop-Location
}

Write-Step "初始化資料庫"
Push-Location (Join-Path $Root "backend")
try {
    Invoke-Checked $Py @("-m", "app.migrate") "資料庫初始化"
} finally {
    Pop-Location
}

Write-Host ""
Write-Host "安裝完成。執行 .\start.ps1 啟動（只綁定 127.0.0.1）。" -ForegroundColor Green
