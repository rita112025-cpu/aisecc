# AI 軟體工程控制中心（AI Software Engineering Control Center）

在 Windows 11 本機執行的軟體工程管理平台：多專案管理、靜態分析與安全掃描、測試管理、AI 修復建議（需人工批准），以及繁體中文 Web 介面。

- 後端：Python 3.12、FastAPI、SQLAlchemy、Alembic、SQLite、Pytest
- 前端：React 18、TypeScript、Vite、原生 CSS、Vitest、Playwright
- 預設只綁定 `127.0.0.1`，不需要系統管理員權限，也不需要 WSL

## 快速開始

```powershell
.\install.ps1      # 檢查 Python 3.12 / Node.js / git，建立 .venv、安裝套件、建置前端、初始化資料庫
.\start.ps1        # 啟動並開啟 http://127.0.0.1:8765
.\stop.ps1         # 停止
.\run_tests.ps1    # 執行全部測試並輸出摘要
```

若 PowerShell 執行原則阻擋腳本，可只對本次執行放寬（不變更系統設定）：

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

細節見 [DEPLOYMENT.md](DEPLOYMENT.md)，問題排除見 [TROUBLESHOOTING.md](TROUBLESHOOTING.md)，功能與測試對照見 [FEATURE_MATRIX.md](FEATURE_MATRIX.md)，最終驗收見 [FINAL_REPORT.md](FINAL_REPORT.md)。

## 功能

| 頁面 | 內容 |
|---|---|
| 總覽 Dashboard | 專案數、執行中任務、未處理問題（依嚴重度／類別）、最近任務 |
| 專案管理 | 新增本機專案（含中文與空白路徑）、檔案結構、Git 狀態、相依套件、測試框架、健康度、盤點紀錄比較、授權開關 |
| 程式碼掃描 | Ruff、Bandit、ESLint、tsc、內建分析（敏感資訊、複雜度、重複程式碼、循環依賴、未使用候選）；搜尋、過濾、排序、判定、比較、JSON/HTML 報告 |
| 安全風險 | 安全、敏感資訊、相依套件弱點（npm audit、pip-audit、選用 semgrep） |
| 測試中心 | 偵測 Pytest / Vitest / Playwright / .NET test 指令、審核與批准、執行、即時日誌、取消、只重跑失敗、覆蓋率、失敗分析、跨 commit 比較 |
| AI 修復建議 | 本機 Ollama 或（明確授權的）OpenAI 相容端點；在隔離工作區產生 patch、diff、語法檢查與隔離測試、回歸風險；人工批准後才套用，可復原 |
| Git 變更比較 | 工作目錄 vs HEAD、任兩個版本、commit 紀錄 |
| 歷史報告 | 任務紀錄、掃描報告、稽核紀錄 |
| 系統設定 | AI 模型、保存期限、掃描門檻、資料庫備份與還原、保存期限清理（先試算）、工具狀態 |

## 安全模型

- **預設只讀取**：新增專案、盤點、分析都只讀檔案；不會執行專案的腳本或測試。
- **三層授權**：
  1. 執行專案程式碼的工具（eslint、tsc）需開啟「允許執行專案工具」
  2. 會連網的稽核（npm audit、pip-audit、semgrep）需開啟「允許網路稽核」
  3. 測試指令需逐一批准，批准會綁定指令內容雜湊，任何修改都會撤銷批准
- **Git 只讀**：強制關閉 fsmonitor、外部 diff、textconv、hooks；專案本地設定了 filter 等可執行程式的項目時，需授權才會讀取；不會 commit、push、merge。
- **AI 不直接修改專案**：修改只在 `data/workspaces` 的隔離 clone 進行。套用前必須確認 diff 雜湊；未通過隔離測試時需要再確認一次；工作目錄必須乾淨，套用前會備份並可復原。
- **機密遮罩**：分析結果、報告、任務日誌、測試輸出、送給 AI 的 prompt 都會遮罩機密值。外部 AI 預設關閉，API 金鑰只從 `AISECC_*` 環境變數讀取，不寫入資料庫。
- **本機限制**：只接受 Host 為 `127.0.0.1`／`localhost` 的請求（防 DNS rebinding），並拒絕跨站 Origin 的寫入請求。

## 專案結構

```
backend/            FastAPI 應用、Alembic migration、pytest 測試
  app/api/          REST API（專案、掃描、發現、測試、AI、任務、系統）
  app/services/     檔案走訪、Git、偵測、分析引擎、測試執行、AI 修復、任務、維運
  launch.py         start.ps1 使用的後端進入點（只綁定 127.0.0.1）
frontend/           React + Vite 前端、Vitest 元件測試、Playwright E2E
data/               執行期資料（SQLite、日誌、報告、隔離工作區、備份；不納入版控）
install.ps1 / start.ps1 / stop.ps1 / run_tests.ps1
Dockerfile / docker-compose.yml   選用（未在本機驗證）
```
