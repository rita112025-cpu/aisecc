# FEATURE_MATRIX

狀態：`DONE`（實作＋測試通過）／`PARTIAL`／`TODO`／`BLOCKED`（缺環境，附替代驗證）
測試欄只記錄「實際執行過」的測試；未執行不得標記。

最後更新：2026-10-28（Phase 1–2）

## Phase 1：盤點
| 項目 | 狀態 | 說明 |
|---|---|---|
| 工作目錄檢查 | DONE | `D:\github\check` 原為空目錄、非 git repo，無 AGENTS.md/README，故建立新專案 |
| 環境工具 | DONE | Python 3.12.10、Node v24.21.0、npm 11.19.0、git 2.55、dotnet、ollama 已安裝；ruff/bandit/pip-audit 裝於 `.venv`；semgrep 未安裝（選用） |
| Git | DONE | `git init -b main`（空初始 commit），各 Phase 於 `feature/*` 分支提交，不推送 |

## Phase 2：架構與資料庫
| 項目 | 狀態 | 測試 |
|---|---|---|
| 資料表 projects/scans/findings/test_runs/test_results/repair_proposals/audit_logs/jobs/settings（＋finding_suppressions、test_commands） | DONE | `tests/test_db.py::test_migration_creates_required_tables` |
| Alembic migration（0001）、可重複升級 | DONE | `test_migration_is_idempotent` |
| WAL / busy_timeout / FK | DONE | `test_wal_mode_enabled` |
| 重複記錄防護（project path_key、finding (scan,fingerprint)） | DONE | `test_duplicate_*` |
| SQLite 寫入衝突（程序內寫鎖 + busy_timeout，外部鎖回報可讀錯誤） | DONE | `test_concurrent_writers_do_not_lock`、`test_external_process_lock_reports_busy` |
| 任務系統（DB 狀態、進度、日誌、取消、去重、重啟後標記 interrupted） | DONE | `tests/test_jobs_procs.py` |
| 安全子程序（無 shell、逾時、取消、程序樹終止、記憶體上限、低優先權） | DONE | `test_run_command_*` |
| 資料備份／還原、保存期限 | TODO | |

## Phase 3–8
| 模組 | 狀態 |
|---|---|
| A 多專案管理 | TODO |
| B 靜態分析與安全掃描 | TODO |
| C 測試管理平台 | TODO |
| D AI 修復助手 | TODO |
| E Web 介面 | TODO |
| Windows 部署腳本與文件 | TODO |

## 測試執行紀錄
| 日期 | 指令 | 結果 |
|---|---|---|
| 2026-10-28 | `cd backend; ..\.venv\Scripts\python.exe -m pytest -q` | 23 passed |
| 2026-10-28 | `cd backend; ..\.venv\Scripts\ruff.exe check .` | 0 errors |
