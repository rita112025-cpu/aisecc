# FEATURE_MATRIX

狀態：`DONE`（實作＋測試通過）／`PARTIAL`／`TODO`／`BLOCKED`（缺環境，附替代驗證）
測試欄只記錄「實際執行過」的測試；未執行不得標記。

最後更新：2026-10-28（Phase 3 / 審查輪次 1–2 修正後）

## 待使用者確認的新依賴
| 套件 | 用途 | 狀態 |
|---|---|---|
| fastapi, uvicorn, sqlalchemy, alembic, pydantic, pydantic-settings, pytest | 需求指定技術棧 | 已安裝於 `.venv` |
| psutil | 程序樹終止、記憶體上限、孤兒程序清理 | 已核准（使用者於審查 session 回覆，2026-10-28） |
| pathspec | `.gitignore` 語法比對（自行實作易錯） | 已核准（使用者於審查 session 回覆，2026-10-28） |
| httpx | Ollama/AI Provider HTTP 呼叫、API 測試 | 已核准（使用者於審查 session 回覆，2026-10-28） |
| ~~jinja2~~ | 依審查意見移除，HTML 報告改用 html.escape | 已移除 |
| ruff, bandit, pip-audit, pytest-cov, pytest-timeout | 分析工具（dev） | 已安裝，**請確認** |
| ~~chardet~~ | 依審查意見移除，改用 utf-8 → utf-8-sig → utf-16 BOM → cp950 → latin-1 | 已移除 |

### 前端（Phase 6）— 已核准（使用者於實作 session 回覆「Approve all」，2026-10-29）
需求指定 React + TypeScript + Vite + Vitest + Playwright。以下為最小集合，固定版本，安裝於 `frontend/node_modules`（不使用全域安裝）。

| 套件 | 版本 | 類型 | 用途 |
|---|---|---|---|
| react | 18.3.1 | 執行期 | UI |
| react-dom | 18.3.1 | 執行期 | UI |
| react-router-dom | 6.28.0 | 執行期 | 9 個頁面路由 |
| vite | 5.4.11 | 開發 | 開發伺服器與建置 |
| @vitejs/plugin-react | 4.3.4 | 開發 | Vite React 支援 |
| typescript | 5.6.3 | 開發 | 型別檢查 |
| @types/react | 18.3.12 | 開發 | 型別 |
| @types/react-dom | 18.3.1 | 開發 | 型別 |
| vitest | 2.1.8 | 開發 | 前端單元／元件測試 |
| jsdom | 25.0.1 | 開發 | Vitest 的 DOM 環境 |
| @testing-library/react | 16.1.0 | 開發 | 元件互動測試 |
| @testing-library/user-event | 14.5.2 | 開發 | 模擬使用者操作 |
| @testing-library/jest-dom | 6.6.3 | 開發 | DOM 斷言 |
| @playwright/test | 1.49.1 | 開發 | E2E |
| Playwright Chromium 瀏覽器 | 隨 1.49.1 | 下載（約 150MB，存於使用者快取） | E2E 執行 |

不使用 UI 框架（原生 CSS）。若不核准 Playwright：E2E 記為 BLOCKED，改以 Vitest + 後端 API 測試替代驗證。

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
| 資料備份／還原、保存期限 | DONE | 見「系統功能」 |

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
| npm audit（只複製 package.json/lock 到暫存目錄、node.exe+npm-cli.js、強制 registry.npmjs.org、移除 NPM_TOKEN/npm_config_*）／pip-audit（只保留 name==version 行、--no-deps --disable-pip） | PARTIAL | `test_npm_audit_isolated_from_project_npmrc`、`test_pip_audit_receives_only_pinned_lines`；實際連網稽核未在自動測試中執行 |
| Semgrep（選用） | BLOCKED | 本機未安裝 → not_installed；替代驗證：授權/未安裝狀態測試 |
| 硬編碼密碼與敏感資訊（遮罩、測試路徑降級、佔位字過濾、熵值） | DONE | `test_secrets_*` |
| 複雜度、過大函式（Python AST；JS/TS 啟發式） | DONE | `test_python_complexity_and_length`、`test_js_complexity_heuristic` |
| 重複程式碼 | DONE | `test_duplication_*` |
| 循環依賴（Python、JS/TS） | DONE | `test_python_and_js_import_cycles`、`test_tarjan_components` |
| 未使用程式碼候選 | DONE | `test_unused_candidates` |
| fingerprint（不含行號）＋ occurrence；冪等寫入；移動程式碼判為未變 | DONE | `test_fingerprint_*`、`test_persist_is_idempotent_and_keeps_duplicates`、`test_api_analyze_triage_diff_and_reports` |
| 誤報處理：triage（誤報/接受風險需理由）並依 fingerprint 帶入後續掃描 | DONE | 同上 |
| 搜尋、過濾、排序 findings | DONE | 同上 |
| JSON 與 HTML 報告（html.escape） | DONE | 同上 |
| 機密不外洩：findings/extra/報告/job log 全部經 redact；工具原始輸出不寫入 log | DONE | `test_secret_never_persisted_anywhere` |
| 工具輸出路徑經 resolve_inside 驗證，專案外路徑丟棄並計數 | DONE | `test_out_of_tree_tool_paths_dropped`、`test_engine_records_dropped_count` |
| 每個 tool/rule 上限 2000 筆（truncated 標記）、minified/bundle 檔跳過、findings 每 500 筆分批寫入 | DONE | `test_per_rule_cap`、`test_minified_files_skipped` |
| 分析中取消 → scan 與 job 皆 cancelled；分析期間 API 寫入不被阻塞 | DONE | `test_analysis_cancel_marks_scan_and_job` |

## Phase 5：模組 C 測試管理（後端）
| 項目 | 狀態 | 測試 |
|---|---|---|
| 偵測測試指令（只讀檔、不執行）並存為未批准 | DONE | `test_detect_suggests_unapproved_commands`、`test_scans_never_execute_tests` |
| 批准綁定內容 sha256（argv、cwd、逾時、記憶體、環境變數設定）；任何修改即撤銷 | DONE | `test_unapproved_and_modified_commands_refused`、`test_env_change_revokes_approval_and_hides_token` |
| 子程序環境變數 allowlist（GITHUB_TOKEN 等預設不傳） | DONE | `test_child_env_allowlist` |
| Pytest 執行、即時日誌（遮罩機密）、結果/略過/時間、失敗分類、覆蓋率 | DONE | `test_run_results_failure_analysis_rerun_and_compare`、`test_test_output_redacted_in_log` |
| 結果判定（無報告→error、exit 5→no_tests、exit≠0 但全過→error） | DONE | `test_run_status_rules`、`test_exit_nonzero_without_report_is_error` |
| 重跑失敗（選擇器驗證，拒絕選項注入與專案外路徑） | DONE | `test_rerun_rejects_unsafe_selectors` |
| 逾時／取消／程序樹終止／重複啟動 | DONE | `test_timeout_kills_tree`、`test_cancel_and_duplicate_run` |
| Vitest／Playwright／.NET test | PARTIAL | 參數組裝與 TRX/JUnit 解析有單元測試；本機無範例專案，未實際執行 |

## Phase 5：模組 D AI 修復（後端）
| 項目 | 狀態 | 測試 |
|---|---|---|
| Provider Adapter（Ollama 本機、OpenAI 相容需明確授權）；未設定時其他模組正常 | DONE | `test_ai.py::test_not_configured_keeps_other_modules_working`、provider 測試 |
| AI 回應格式／路徑驗證、大小上限 256KB、不跟隨轉址、拒絕遮罩字串 | DONE | `test_parse_rejects`、`test_redirect_not_followed`、`test_oversized_response_rejected` |
| 隔離工作區（clone）產生 patch、diff、語法檢查、隔離測試、回歸風險 | DONE | `test_propose_apply_revert` |
| 可編輯安裝指回原專案 → not_isolated，套用需額外確認 | DONE | `test_editable_install_marks_not_isolated` |
| 人工批准套用（confirm + diff sha + 測試狀態確認）、防覆蓋、HEAD/dirty 檢查、回滾與 apply_failed 紀錄 | DONE | `test_apply_*`、`test_apply_rollback_*` |
| 復原與修復歷史 | DONE | `test_revert_refused_if_changed_after_apply` |
| 安全刪除（safe_rmtree 不跟隨 junction）、重啟清理孤兒工作區 | DONE | `test_recover_marks_runs_scans_repairs_and_cleans_orphans`、`test_safe_rmtree_refuses_outside_root` |
| 實際接 Ollama 模型 | BLOCKED | 本機已安裝 ollama，但自動測試使用假 provider；未驗證特定模型輸出品質 |

## 系統功能（後端）
| 項目 | 狀態 | 測試 |
|---|---|---|
| 系統設定 API（驗證、稽核） | DONE | `test_settings_get_put_and_audit` |
| 資料庫備份／還原（完整性檢查、還原前自動備份） | DONE | `test_backup_and_restore_roundtrip`、`test_restore_rejects_bad_input` |
| 資料保存期限（只刪舊且已結束的紀錄、保留每專案最新掃描） | DONE | `test_retention_deletes_only_old_finished_records` |
| 重啟恢復（Job、TestRun、Scan、RepairProposal） | DONE | `test_recover_*` |
| Dashboard、稽核紀錄、工具狀態 API | DONE | `test_dashboard_and_tools`、`test_audit_log_filters` |

## Phase 6：模組 E Web 介面
| 項目 | 狀態 | 測試 |
|---|---|---|
| 9 頁繁中介面（總覽、專案、掃描、安全、測試、AI 修復、Git 比較、歷史、設定），全部串接後端 API，無假資料 | DONE | E2E `all nine pages render from the real API without errors` |
| Loading／Empty／Error 狀態與重試 | DONE | Vitest `DashboardPage`、`ProjectsPage` |
| 搜尋、過濾、排序、分頁（findings 由後端處理） | DONE | Vitest `ScansPage`、`ProjectsPage` |
| 長時間任務：進度、即時日誌、取消；重新整理後重新接上進行中的任務 | DONE | E2E `test center: … refresh re-attaches … cancel`、Vitest `JobPanel` |
| 測試指令批准（帶內容雜湊）、AI 套用需確認 diff 與測試狀態 | DONE | Vitest `TestsPage`、`RepairsPage` |
| 掃描 → 判定誤報，機密不以明文顯示 | DONE | E2E `project → inventory → analysis → triage` |
| 保存期限先試算再確認、備份 | DONE | Vitest `SettingsPage`、E2E `settings backup` |
| 桌面解析度 | DONE | 固定側欄 + 流式主內容（未做行動版） |
| E2E 規格檔型別檢查 | PARTIAL | 由 Playwright 轉譯執行並通過；`tsc` 需 `@types/node`（未核准），故 tsconfig 只涵蓋 `src` |

## Phase 7–8
| 模組 | 狀態 |
|---|---|
| E Web 介面 | TODO |
| Windows 部署腳本與文件 | TODO |

## 測試執行紀錄
| 日期 | 指令 | 結果 |
|---|---|---|
| 2026-10-28 | `cd backend; ..\.venv\Scripts\python.exe -m pytest -q` | 23 passed（Phase 2 初版） |
| 2026-10-28 | 同上 | 30 passed（審查輪次 1 修正） |
| 2026-10-28 | 同上 | 77 passed（Phase 3 模組 A） |
| 2026-10-28 | 同上 `-rs` | 115 passed，0 skipped（審查輪次 1–2 修正） |
| 2026-10-28 | 同上 | 145 passed（Phase 4 模組 B） |
| 2026-10-28 | 同上 `-rs` | 161 passed，0 skipped（審查輪次 2–3 修正） |
| 2026-10-28 | 同上 `-rs` | 185 passed（Phase 5 模組 C） |
| 2026-10-28 | 同上 `-rs` | 216 passed（Phase 5 模組 D） |
| 2026-10-29 | 同上 `-rs` | 248 passed，0 skipped（審查累積修正＋系統 API） |
| 2026-10-29 | `..\.venv\Scripts\ruff.exe check .` | All checks passed |
| 2026-10-29 | `cd frontend; npx vitest run` | 19 passed |
| 2026-10-29 | `cd frontend; npx tsc --noEmit -p .` | 0 errors |
| 2026-10-29 | `cd frontend; npx vite build; npx playwright test` | 4 passed（真實後端、暫存資料目錄、port 8799） |
