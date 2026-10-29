# 部署說明（Windows 11）

## 需求

| 項目 | 版本 | 說明 |
|---|---|---|
| Windows | 10/11 | 一般使用者權限即可，不需 WSL |
| Python | 3.12 | python.org 安裝程式（勾選 Add to PATH）；`py -3.12` 或 `python` 皆可被偵測 |
| Node.js | 18 以上 | 已驗證 v24.21.0 |
| git | 任意近期版本 | 可選；沒有 git 時 Git 資訊與 AI 修復不可用 |
| 磁碟 | 約 600MB | `.venv`、`node_modules`、Playwright Chromium（約 90MB） |
| Ollama | 可選 | 只在使用 AI 修復時需要；只接受本機位址 |

## 安裝

```powershell
.\install.ps1
```

步驟：檢查工具 → 建立 `.venv` → 安裝 `backend\requirements-dev.txt` → `npm ci` → `vite build` → 下載 Playwright Chromium → `python -m app.migrate` 初始化 `data\aisecc.sqlite3`。

不需要 E2E 時，可加 `-SkipPlaywrightBrowser` 略過瀏覽器下載；之後執行 `run_tests.ps1 -SkipE2E`，E2E 會標示為 SKIPPED，不會算成通過。

## 啟動與停止

```powershell
.\start.ps1                 # 預設 http://127.0.0.1:8765，會開啟瀏覽器
.\start.ps1 -Port 9000 -NoBrowser
.\stop.ps1
```

- `start.ps1` 以獨立的隱藏主控台啟動 `backend\launch.py`，固定綁定 `127.0.0.1`、單一 worker（SQLite 單一寫入者）。PID 記在 `.run\backend.pid`。
- 重複啟動時會偵測到已在執行中；連接埠被占用時會顯示占用的 PID。
- `stop.ps1` 只會停止命令列包含 `backend\launch.py` 的程序，避免 PID 被重用時誤殺其他程式；會連同子程序一起終止。
- 停止時若有執行中的任務，下次啟動會標示為「已中斷」，不會自動重跑；殘留的 AI 隔離工作區會被安全清除。

## 資料與設定

| 位置 | 內容 |
|---|---|
| `data\aisecc.sqlite3` | 資料庫（WAL 模式） |
| `data\logs\` | `server.log`、`access.log`、各任務日誌 |
| `data\reports\` | 掃描 JSON/HTML 報告、測試結果檔 |
| `data\workspaces\` | AI 修復隔離工作區 |
| `data\backups\` | 資料庫備份（含還原前自動備份、AI 套用前的檔案備份） |

可用環境變數 `AISECC_DATA_DIR` 改變資料位置。系統設定（保存期限、AI 模型、門檻值等）在介面「系統設定」頁調整，並寫入稽核紀錄。

## AI 模型（選用）

1. 安裝 Ollama，並下載模型，例如 `ollama pull qwen2.5-coder:7b`
2. 在「系統設定」選擇「本機 Ollama」，填入模型名稱並儲存
3. 外部 OpenAI 相容端點：設定 https 位址與模型，把金鑰放在 `AISECC_` 開頭的環境變數（例如 `AISECC_EXTERNAL_AI_KEY`），並勾選「允許將程式碼送往外部 AI 服務」。設定後需重新啟動，環境變數才會生效。

## 升級

```powershell
.\stop.ps1
git pull                     # 或覆蓋程式檔
.\install.ps1                # 會執行新的 migration；0003 會撤銷既有測試指令批准，需重新批准
.\start.ps1
```

升級前建議先在「系統設定」建立資料庫備份。

## Docker Compose（選用，未驗證）

本機沒有安裝 Docker，以下檔案未經實際驗證：

```powershell
$env:AISECC_PROJECTS_DIR = "D:\code"
docker compose up -d --build
```

容器只對主機的 `127.0.0.1:8765` 開放；要分析的專案需掛載到 `/projects`（唯讀），並以容器內的路徑新增。唯讀掛載下，AI 修復無法套用。
