# FEATURE_MATRIX

狀態：`DONE`（實作＋測試通過）／`PARTIAL`／`TODO`／`BLOCKED`（缺環境，附替代驗證）
測試欄只記錄「實際執行過」的測試；未執行不得標記。

最後更新：2026-10-28（Phase 3 / 審查輪次 1–2 修正後）

## 待使用者確認的新依賴
| 套件 | 用途 | 狀態 |
|---|---|---|
| fastapi, uvicorn, sqlalchemy, alembic, pydantic, pydantic-settings, pytest | 需求指定技術棧 | 已安裝於 `.venv` |
| psutil | 程序樹終止、記憶體上限、孤兒程序清理 | 已安裝，**請確認** |
| pathspec | `.gitignore` 語法比對（自行實作易錯） | 已安裝，**請確認** |
| httpx | Ollama/AI Provider HTTP 呼叫、API 測試 | 已安裝，**請確認** |
| jinja2 | HTML 報告 | 已安裝，**請確認** |
| ruff, bandit, pip-audit, pytest-cov, pytest-timeout | 分析工具（dev） | 已安裝，**請確認** |
| ~~chardet~~ | 依審查意見移除，改用 utf-8 → utf-8-sig → utf-16 BOM → cp950 → latin-1 | 已移除 |

## Phase 1：盤點
| 項目 | 狀態 | 說明 |
|---|---|---|
| 工作目錄檢查 | DONE | `D:\github\check` 原為空目錄、非 git repo，無 AGENTS.md/README，故建立新專案 |
| 環境工具 | DONE | Python 3.12.10、Node v24.21.0、npm 11.19.0、git 2.55、dotnet、ollama 已安裝；ruff/bandit/pip-audit 裝於 `.venv`；semgrep 未安裝（選用 → tool_missing） |
| Git | DONE | `git init -b main`（空初始 commit），各 Phase 於 `feature/*` 分支提交，不推送 |

## Phase 2：架構與資料庫
| 項目 | 狀態 | 測試 |
|---|---|---|
| 資料表 projects/scans/findings/test_runs/test_results/repair_proposals/audit_logs/jobs/settings（＋finding_suppressions、test_commands） | DONE | `test_db.py::test_migration_creates_required_tables` |
| Alembic migration、可重複升級、來回、models 無漂移 | DONE | `test_db_migrations.py` |
| findings 唯一鍵 (scan, fingerprint, occurrence)，同檔多處命中皆保留 | DONE | `test_duplicate_finding_fingerprint_rejected` |
| SQLite：WAL（migration 時設定）、busy_timeout、FK、寫入 BEGIN IMMEDIATE、巢狀寫入立即報錯 | DONE | `test_writer_waits_for_external_reserved_lock`、`test_nested_write_session_fails_fast` |
| 任務系統：DB 狀態、進度、日誌、取消、去重、終態寫入重試 | DONE | `test_jobs_procs.py`、`test_final_status_write_retries` |
| 重啟恢復：running 與 **queued 都標 interrupted**（callable 已消失、測試屬副作用不自動重跑），各寫 AuditLog；孤兒程序樹在 pid+create_time 相符時才終止 | DONE | `test_recover_*` |
| 安全子程序：無 shell、逾時、取消、程序樹終止、記憶體上限、低優先權；.cmd/.bat 參數含 cmd 特殊字元一律拒絕 | DONE | `test_run_command_*`、`test_bat_rejects_cmd_metacharacters` |
| 資料備份／還原、保存期限 | TODO | |

## Phase 3：模組 A 多專案管理（後端）
| 項目 | 狀態 | 測試 |
|---|---|---|
| 新增／列表／更名／移除專案（移除只刪 DB 記錄） | DONE | `test_api_projects.py` |
| 非 git 專案可加入、掃描、分析；AI 修復需 git 且工作樹乾淨（UI 需標示） | PARTIAL | 後端完成；UI 待 Phase 6 |
| 檔案走訪：巢狀 .gitignore、不跟隨 symlink/junction、雲端佔位檔不讀取、上限截斷、取消、> MAX_PATH | DONE | `test_fswalk_detect.py`、`test_hardening.py` |
| 編碼：UTF-8/BOM/UTF-16/cp950/latin-1，二進位檔跳過 | DONE | `test_decode_*` |
| 路徑邊界（.., 絕對、C:x、UNC、\\?\、ADS、裝置名、8.3、junction 逃逸） | DONE | `test_resolve_inside_*` |
| Git 資訊：分支、commit、狀態、ahead/behind、log、diff；空 repo、detached、中文檔名、rename、git 不在 PATH | DONE | `test_gitinfo_inventory.py`、`test_hardening.py` |
| Git 不執行 repo 設定的程式：強制 fsmonitor=false、diff.external 清空、--no-ext-diff/--no-textconv；本地 filter/textconv/include 設定 → 需專案授權 | DONE | `test_fsmonitor_hook_not_executed`、`test_filter_config_blocks_status`、`test_textconv_and_external_diff_not_executed` |
| 依賴掃描（requirements、pyproject PEP621/poetry、package.json、csproj） | DONE | `test_parse_*` |
| 測試框架偵測（pytest/vitest/jest/playwright/dotnet） | DONE | `test_detect_multi_language_project` |
| 健康度報告（逐項扣分可追溯；未執行工具不算通過） | DONE | `test_health_report_*` |
| 掃描歷史與比較（同專案、同類型） | DONE | `test_inventory_scan_job_and_compare` |
| 本機限制：Host 白名單（防 DNS rebinding）、跨站 Origin 拒絕 | DONE | `test_rejects_*` |

## Phase 4：模組 B 靜態分析與安全掃描（後端）
| 項目 | 狀態 | 測試 |
|---|---|---|
| 工具分級：auto（ruff、bandit、內建）／project_code（eslint、tsc，需 allow_project_tools）／network（npm audit、pip-audit、semgrep，需 allow_network_audit） | DONE | `test_project_tier_tools_need_authorization_and_never_spawn`、`test_authorised_matrix` |
| 未安裝／逾時／輸出異常 → not_installed／timeout／error，不算通過；有工具未執行時 scan=partial | DONE | `test_tool_missing_is_not_pass`、`test_tool_timeout_reported`、`test_malformed_tool_output_is_error`、`test_api_full_scan_is_partial_when_tools_skipped` |
| Ruff、Bandit 實際執行並轉換為檔案＋行號 | DONE | `test_real_ruff_and_bandit_findings` |
| ESLint／tsc（node.exe 直接執行 package bin，不經 .cmd） | PARTIAL | 已實作；本環境無含 node_modules 的範例專案，未做實際執行測試 |
| npm audit（--package-lock-only --ignore-scripts）／pip-audit（--no-deps --disable-pip） | PARTIAL | 已實作授權閘門測試；實際連網稽核未在自動測試中執行 |
| Semgrep（選用） | BLOCKED | 本機未安裝 → not_installed；替代驗證：授權/未安裝狀態測試 |
| 硬編碼密碼與敏感資訊（遮罩、測試路徑降級、佔位字過濾、熵值） | DONE | `test_secrets_*` |
| 複雜度、過大函式（Python AST；JS/TS 啟發式） | DONE | `test_python_complexity_and_length`、`test_js_complexity_heuristic` |
| 重複程式碼 | DONE | `test_duplication_*` |
| 循環依賴（Python、JS/TS） | DONE | `test_python_and_js_import_cycles`、`test_tarjan_components` |
| 未使用程式碼候選 | DONE | `test_unused_candidates` |
| fingerprint（不含行號）＋ occurrence；冪等寫入；移動程式碼判為未變 | DONE | `test_fingerprint_*`、`test_persist_is_idempotent_and_keeps_duplicates`、`test_api_analyze_triage_diff_and_reports` |
| 誤報處理：triage（誤報/接受風險需理由）並依 fingerprint 帶入後續掃描 | DONE | 同上 |
| 搜尋、過濾、排序 findings | DONE | 同上 |
| JSON 與 HTML 報告（jinja2 autoescape） | DONE | 同上 |

## Phase 5–8
| 模組 | 狀態 |
|---|---|
| C 測試管理平台 | TODO |
| D AI 修復助手 | TODO |
| E Web 介面 | TODO |
| Windows 部署腳本與文件 | TODO |

## 測試執行紀錄
| 日期 | 指令 | 結果 |
|---|---|---|
| 2026-10-28 | `cd backend; ..\.venv\Scripts\python.exe -m pytest -q` | 23 passed（Phase 2 初版） |
| 2026-10-28 | 同上 | 30 passed（審查輪次 1 修正） |
| 2026-10-28 | 同上 | 77 passed（Phase 3 模組 A） |
| 2026-10-28 | 同上 `-rs` | 115 passed，0 skipped（審查輪次 1–2 修正） |
| 2026-10-28 | `..\.venv\Scripts\ruff.exe check .` | All checks passed |
