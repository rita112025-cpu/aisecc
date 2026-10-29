# FINAL_REPORT — AI 軟體工程控制中心

日期：2026-10-29　｜　環境：Windows 11 Home 10.0.26300、Python 3.12.10、Node v24.21.0、npm 11.19.0、git 2.55、PowerShell 5.1
開發方式：實作 session 加上獨立審查 session，共 6 輪以上審查；每輪意見都逐點回覆「採納／不採納＋理由」。

## 1. 系統架構

```
瀏覽器（React 18 + TS，繁中 9 頁）
   │  /api（只接受 Host=127.0.0.1/localhost；拒絕跨站 Origin 寫入）
FastAPI（uvicorn，127.0.0.1，單一 worker）
   ├─ API：projects / scans / findings / test-commands / test-runs / repairs / jobs / settings / system
   ├─ JobManager：ThreadPool + SQLite 狀態（進度、日誌、取消、去重、重啟恢復、孤兒程序清理）
   ├─ procs.run_command：無 shell、逾時、取消、程序樹終止、記憶體上限、低優先權、輸出遮罩
   ├─ 模組 A：fswalk（.gitignore、不跟隨連結、雲端佔位檔、截斷、取消、編碼）/ gitinfo（只讀、
   │          停用 fsmonitor/外部 diff/hooks、私有 index 副本）/ detect / inventory / health
   ├─ 模組 B：analysis engine（內建分析＋分級授權的外部工具）、fingerprint+occurrence、
   │          redact、誤報沿用、JSON/HTML 報告
   ├─ 模組 C：測試指令偵測、雜湊綁定批准、環境變數 allowlist、JUnit/TRX/Cobertura 解析、
   │          失敗分類、重跑選擇器驗證、跨 commit 比較
   ├─ 模組 D：AI provider adapter（Ollama／OpenAI 相容）、嚴格 JSON 驗證、隔離 clone 工作區、
   │          diff、語法與隔離測試、回歸風險、diff 雜湊確認套用、備份、回滾、復原
   └─ 維運：備份／暫存檔驗證後原子還原、保存期限（dry-run、受保護稽核紀錄）、safe_rmtree
SQLite（WAL、busy_timeout、BEGIN IMMEDIATE、FK、Alembic 0001–0003）
```

資料表：projects、scans、findings、finding_suppressions、test_commands、test_runs、test_results、repair_proposals、audit_logs、jobs、settings。

## 2. 實際完成的功能

逐項的狀態與對應測試見 [FEATURE_MATRIX.md](FEATURE_MATRIX.md)。摘要如下：

- **模組 A 多專案管理**：新增／移除專案（只刪紀錄）、Python／TS／JS／C# 偵測、檔案結構、Git 分支／commit／狀態、相依套件、測試框架、健康度（可追溯的扣分）、盤點歷史與比較、多專案。
- **模組 B 靜態分析**：
  - Ruff、Bandit：已實際執行。
  - ESLint、tsc：需授權；只測了授權閘門與解析，見第 7 節。
  - npm audit、pip-audit：需授權，在隔離環境執行；只測了 mock。
  - Semgrep：選用，本機未安裝。
  - 內建分析：敏感資訊、複雜度／過大函式、重複程式碼、循環依賴、未使用候選。
  - 工具未安裝不算通過；有誤報判定機制；可產出 JSON／HTML 報告；有每條規則的筆數上限與分批寫入。
- **模組 C 測試管理**：
  - 偵測、審核、批准、執行、即時日誌、取消。
  - 結果統計與耗時、歷史紀錄、失敗分析、覆蓋率、跨 commit 比較、只重跑失敗。
  - 逾時、程序樹終止、記憶體上限、環境變數 allowlist。
  - Pytest 已端對端驗證；Vitest／Playwright／.NET 只驗證參數組裝與報告解析。
- **模組 D AI 修復**：如第 1 節；沒有設定模型時，其他模組正常運作。
- **模組 E Web 介面**：9 頁繁中介面，有 Loading／Empty／Error 狀態、搜尋／過濾／排序、任務進度與取消；重新整理後會重新接上進行中的任務。
- **資料庫與任務**：migration、索引與唯一約束、WAL 與寫入鎖、備份還原、保存期限、稽核紀錄、重啟恢復。
- **Windows 部署**：install／start／stop／run_tests 腳本；README、DEPLOYMENT、TROUBLESHOOTING；選用 Docker（未驗證）。

## 3. 檔案結構（版控中，不含 package-lock）

```
.gitattributes  .gitignore  Dockerfile  docker-compose.yml
README.md  DEPLOYMENT.md  TROUBLESHOOTING.md  FEATURE_MATRIX.md  FINAL_REPORT.md
install.ps1  start.ps1  stop.ps1  run_tests.ps1
backend/
  alembic.ini  pyproject.toml  requirements.txt  requirements-dev.txt  launch.py
  alembic/env.py  alembic/script.py.mako  alembic/versions/0001_initial_schema.py 0002_… 0003_…
  app/__init__.py config.py db.py main.py migrate.py models.py
  app/api/  ai.py common.py findings.py jobs.py projects.py scans.py system.py tests_api.py
  app/services/  audit.py detect.py fswalk.py gitinfo.py health.py inventory.py jobs.py
                 maintenance.py procs.py projects.py settings_store.py
  app/services/analysis/  base.py code_metrics.py diff.py engine.py report.py secrets.py tools.py
  app/services/testing/   commands.py failures.py reports.py runner.py
  app/services/ai/        patch.py prompt.py providers.py repair.py risk.py schema.py workspace.py
  tests/  conftest.py test_db.py test_db_migrations.py test_jobs_procs.py test_fswalk_detect.py
          test_gitinfo_inventory.py test_api_projects.py test_hardening.py test_analysis_builtin.py
          test_analysis_engine.py test_analysis_review.py test_testing_parsers.py test_testing_api.py
          test_ai.py test_review_round6.py test_system_api.py
frontend/
  package.json  vite.config.ts  tsconfig.json  playwright.config.ts  index.html
  src/  main.tsx App.tsx api.ts types.ts hooks.ts labels.ts styles.css test-setup.ts test-utils.tsx api.test.ts
  src/components/  common.tsx DiffView.tsx FindingsView.tsx JobPanel.tsx ProjectSelect.tsx
  src/pages/  DashboardPage ProjectsPage ProjectDetailPage ScansPage SecurityPage TestsPage
              RepairsPage GitPage HistoryPage SettingsPage（.tsx）  pages.test.tsx
  e2e/app.spec.ts
```

## 4. Git 分支與 Commit

`main` 只有初始空 commit（893d691）。各 Phase 依序在 feature 分支上堆疊，沒有推送，沒有合併，也沒有建立 PR。

| 分支 | Commit |
|---|---|
| feature/phase1-2-foundation | 63fcdda、eb68ffa |
| feature/phase3-projects-api | be69711、c42205b |
| feature/phase4-analysis | f2d1c1d、c39bb2f、18802a0 |
| feature/phase5-testing | 5d6ca8e、42d13ae |
| feature/phase5-ai-repair | 540eb1c、4d8021e、d420b13、e460406、7068502、f8c4b80 |
| feature/phase6-frontend | 12697f1 |
| feature/phase8-deploy | 611650b，以及本報告的 commit |

`feature/phase8-deploy` 包含以上全部 commit（線性堆疊）。

## 5. 實際執行的測試指令

```powershell
.\run_tests.ps1
#   = cd backend; ..\.venv\Scripts\python.exe -m pytest -q -rs -p no:cacheprovider
#   + ..\.venv\Scripts\ruff.exe check .
#   + cd frontend; npx tsc --noEmit -p . ; npx vitest run ; npx vite build ; npx playwright test
.\install.ps1                          # 實際執行，exit 0
start.ps1 → health → 重複啟動 → stop.ps1   # 在使用者終端（沙盒外）執行
```

## 6. 結果統計（最後一次 run_tests.ps1，2026-10-29）

| 類別 | PASS | FAIL | SKIP | BLOCKED |
|---|---|---|---|---|
| 後端 pytest | 254 | 0 | 0 | 0 |
| 前端 Vitest | 19 | 0 | 0 | 0 |
| Playwright E2E（真實後端） | 4 | 0 | 0 | 0 |
| **合計自動化測試** | **277** | **0** | **0** | — |
| 靜態檢查（ruff、tsc） | 2 | 0 | 0 | — |
| 功能項目 BLOCKED（見第 7 節） | — | — | — | 3 |

## 7. 未完成項目

| 項目 | 狀態 | 原因／替代驗證 |
|---|---|---|
| Semgrep 實際執行 | BLOCKED | 本機未安裝（選用工具）；以「未安裝不算通過、不使狀態變為 partial」的測試替代驗證 |
| 實際接 Ollama 模型產生修復 | BLOCKED | 自動測試使用假 provider 與 httpx MockTransport；未驗證特定模型的輸出品質 |
| Docker Compose | BLOCKED | 本機未安裝 Docker；檔案已提供但未驗證 |
| ESLint／tsc 對真實 node 專案執行 | PARTIAL | 已實作且有授權閘門與解析的測試；本機沒有帶 node_modules 的目標專案 |
| npm audit／pip-audit 實際連網 | PARTIAL | 以 mock 驗證隔離（暫存目錄、強制 registry、清除憑證、過濾需求檔）；未實際連網 |
| Vitest／Playwright／.NET 作為「被測專案」執行 | PARTIAL | 參數組裝與 JUnit/TRX 解析有單元測試；只有 Pytest 端對端驗證 |
| E2E 規格檔的 tsc 型別檢查 | PARTIAL | 需要 `@types/node`（未核准）；由 Playwright 轉譯執行並通過 |
| 行動版版面 | 未做 | 需求只要求桌面解析度 |

## 8. 已知風險

1. 隔離測試會透過 junction 使用原專案的 `.venv`／`node_modules`；可編輯安裝（editable install）會被偵測並標為「未隔離」，但 conda 或全域 site-packages 不在偵測範圍。
2. 機密偵測是規則加熵值的啟發式，會有漏報與誤報；遮罩只涵蓋規則命中的部分。
3. 孤兒工作區清理同時參考 DB 狀態和 mtime（超過 1 天）；產生時間超過 1 天的修復任務屬於理論上的邊界情況。
4. SQLite 的單一寫入者特性限制了同時執行的任務數（預設 2 個 worker），不適合多人同時操作。
5. JS/TS 的複雜度與循環依賴分析是啟發式，不是完整的語法剖析。
6. 測試指令一旦批准，就會在使用者權限下執行專案程式碼。這是需求要求的「已授權操作」，系統只能限制環境變數、時間與記憶體，無法沙盒化程式本身。

## 9. Windows 安裝方式

```powershell
cd <專案資料夾>
powershell -ExecutionPolicy Bypass -File .\install.ps1
```

需要 Python 3.12 與 Node.js 18 以上；不需要系統管理員權限，也不需要 WSL。詳見 [DEPLOYMENT.md](DEPLOYMENT.md)。

## 10. Windows 啟動方式

```powershell
.\start.ps1            # http://127.0.0.1:8765
.\stop.ps1
.\run_tests.ps1        # 驗收測試
```

## 11. 後續維護成本與擴充方向

- **維護成本**：
  - 依賴集中在 `backend/requirements*.txt` 和 `frontend/package.json`，版本固定；升級時跑 `run_tests.ps1` 即可回歸。
  - schema 變更一律走 Alembic，並有 models 與 migration 漂移的測試。
  - 新增依賴前需取得使用者同意。目前已核准：psutil、pathspec、httpx，以及 14 個前端套件和 Playwright Chromium。
- **擴充方向**：
  1. 新增 AI provider：在 `services/ai/providers.py` 實作 `generate`／`status`，並在 `build_provider` 註冊。
  2. 新增分析工具：在 `services/analysis/tools.py` 的 `TOOLS` 加一筆，並設定 tier。
  3. JS/TS 改用真正的 AST 解析器：需新依賴。
  4. 以 Windows Job Object 限制測試程序的 CPU 與記憶體，取代輪詢。
  5. 多人使用時改用 PostgreSQL，SQLAlchemy 層可沿用。
  6. 掃描排程。

## 驗收清單

- [x] Windows 原生環境可啟動（install／start／stop 已實測）
- [x] 可新增及管理專案
- [x] 可執行靜態分析
- [x] 可檢視安全掃描報告
- [x] 可執行已授權的測試
- [x] 可取消執行中任務
- [x] AI 修復建議可產生 Diff（以假 provider 驗證；實際 Ollama 為 BLOCKED，見第 7 節）
- [x] 未經批准不得套用 Patch
- [x] Git 原始資料受到保護（只讀指令、私有 index、不 commit；有測試驗證）
- [x] SQLite 持久化正常（含重啟後任務狀態）
- [x] 前後端 API 完整連通（E2E 使用真實後端）
- [x] 至少 100 個有意義的測試案例（277 個）
- [x] 已執行測試沒有未處理的失敗
- [x] 所有略過測試均有記錄（0 skip；BLOCKED／PARTIAL 項目列於第 7 節）
- [x] 沒有硬編碼密碼（金鑰只從環境變數讀取；本專案自掃結果中的敏感資訊命中皆為測試資料）
- [x] 有部署及故障排除文件
- [x] 有完整最終驗收報告
