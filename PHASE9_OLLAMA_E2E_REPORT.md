# PHASE9 — 真實 Ollama AI 修復端到端驗證報告

日期：2026-10-30　｜　分支：`feature/phase9-ollama-e2e`（由 `main` @ `b4537fc` 分出，**未合併 main、未推送、未刪除任何分支**）
環境：Windows 11 Home 10.0.26300、Python 3.12.10、Ollama 0.40.13（本機服務 `http://127.0.0.1:11434`）

## 0. 前置條件檢查

| 項目 | 結果 |
|---|---|
| `main` HEAD 是否為 `b4537fc` | ✓ 是（開始時 `main` = `b4537fc`，工作樹乾淨） |
| 工作樹乾淨 | ✓ 是 |
| 不重複合併、不推送、不刪分支 | ✓ 遵守（本階段只建立並提交 `feature/phase9-ollama-e2e`） |
| 正式專案與真實資料未被修改 | ✓ 所有測試使用獨立資料目錄（見 §3）；正式 `data/` 未被使用 |
| 未安裝軟體、未下載模型、未改安全設定 | ✓ 遵守（Ollama 與模型皆為既有；未執行 `ollama pull`） |
| 讀取規範文件 | ✓ 本 repo 無 AGENTS.md；已讀 README、FINAL_REPORT、FEATURE_MATRIX 與既有測試慣例 |

## 1. 唯讀檢查：Ollama 與已安裝模型

| 項目 | 結果 |
|---|---|
| 服務可達 | ✓ `GET /api/version` → `{"version":"0.40.13"}` |
| 已安裝模型（`GET /api/tags`） | ✓ `qwen3:latest`（8.2B, Q4_K_M, 具 thinking 能力）、`llama3.1-8b-zh:latest`（8.0B, Q4_K_M）、`llama3.1:8b`（8.0B, Q4_K_M） |
| 選用模型 | `llama3.1:8b`：非 thinking 模型，JSON 輸出最穩定，與本系統的 `format: json` 相容。`qwen3` 與 `llama3.1-8b-zh` **未測試**（見 §7）。 |

## 2. AI 修復的真實呼叫路徑（程式檢查）

- Provider：`backend/app/services/ai/providers.py` → `OllamaProvider.generate()`
- 實際 endpoint：**`POST http://127.0.0.1:11434/api/generate`**，`stream=false`、`format=json`、`temperature=0.1`，逾時 = `ai_timeout_sec`（預設 120 秒）。
- 非本機位址會被拒絕（`is_local_url`）；Ollama 請求 `trust_env=False`、`follow_redirects=False`；回應上限 256KB。
- 錯誤處理：連線失敗、逾時、HTTP 非 200、回應格式錯誤，皆轉為 `AIError` → 提案狀態 `failed`（不會誤報成功）；JSON/schema 不合法 → `invalid`。
- 本次驗證**全部走真實 HTTP**；唯一的故障注入是 F3（見 §4），且明確標示。

## 3. 隔離環境

| 項目 | 位置 |
|---|---|
| 隔離測試專案（git repo，含刻意的 bug） | `C:\Users\user\AppData\Local\Temp\claude\D--github-check\224749c7-8e61-4b83-800e-47106fe0636a\scratchpad\ollama-e2e\proj`（commit `1681ac6`） |
| 隔離後端（埠 8790，`AISECC_DATA_DIR` 指向 scratch） | `…\scratchpad\ollama-e2e\data`（與正式 `8765` / `data/` 完全分開） |
| 驅動腳本（只透過 HTTP API 操作） | `…\scratchpad\ollama-e2e\driver.py` 與 `scenario_*.py`、`final_run.py` |
| 故障注入服務（僅 F3 使用） | `…\scratchpad\ollama-e2e\fault_server.py`，埠 11499，**不計為成功證據** |

測試 bug：`clamp(x, lo, hi)` 只做上界 `min(x, hi)`，缺下界。測試 `test_below_lower_bound`（`clamp(-5,0,10)==0`）失敗。

## 4. 完整流程與結果

### 4.1 主流程（真實 llama3.1:8b）— 最終程式碼 `5ceda62`

| 步驟 | 結果 | 證據 |
|---|---|---|
| 檢查 AI 狀態 | ✓ `configured=true, reachable=true, model_available=true` | `evidence.jsonl` `ai_status` |
| 偵測錯誤（修復前測試） | ✓ `run 8`：2 通過 / **1 失敗** | `evidence.jsonl` `test_run_result:before_repair` |
| 呼叫 Ollama 產生修復 | ✓ 真實模型，約 4 秒（最終執行）／10.2 秒（第一次執行） | `repair_result` |
| 審查差異 | ✓ 伺服器 diff 雜湊與前端以同一 UTF-8 字串計算的雜湊一致 | `review` 事件 `match=true` |
| 安全套用（hash 確認） | ✓ `200 applied`；檔案雜湊 = 提案 after 雜湊 | `apply` |
| 重新測試（修復後） | ✓ `run 10`：**3 通過 / 0 失敗** | `test_run_result:after_apply` |
| 還原 | ✓ `200 reverted`；檔案位元組回到原始雜湊 `1185644d…` | `revert`、`revert_verdict` |

第一次完整執行（`run 1–4`，程式碼 `b4537fc`）同樣通過：修復前 `failed`、修復後 `passed`、第二次還原回 409、還原後測試再度失敗（`run 4`，2/1），`git status` 乾淨。證據見 `evidence-run1-and-failures.jsonl`。

### 4.2 失敗與邊界情境

| ID | 情境 | 結果 | 判定 |
|---|---|---|---|
| F1 | Ollama 服務不可用（埠 1） | 提案 `failed`，訊息「無法連線到 Ollama」；專案未變；無殘留工作區 | ✓ 不誤報成功 |
| F2 | 模型不存在（`no-such-model:9b`） | 提案 `failed`，「Ollama 錯誤 404：model not found」 | ✓ |
| F3 | 回應格式錯誤（**故障注入**：endpoint 回傳非 JSON 內容） | 提案 `invalid`，「AI 回傳不是合法 JSON」，無 diff，專案未變 | ✓（僅證明錯誤處理；真實模型的壞格式未觸發，見 §7） |
| F4 | 修復無效：指令要求「只加註解」，測試仍失敗（真實模型） | 提案 `validated` 但 `test_status=failed`；未確認就套用 → **409「隔離測試未通過」** | ✓ 有閘門；△ 狀態名稱 `validated` 對失敗的測試容易誤解（見 §6） |
| F5a | 取消正在執行的修復（2 秒後取消） | 任務 `cancelled`，提案 `failed`「使用者取消」，專案未變 | ✓ |
| F5b | 取消**排隊中**的修復（3 個任務、2 個工作緒） | **修復前**：任務 `cancelled`，但提案卡在 `generating`，使用者無法拒絕（409） | ✗ **缺陷**（已修復，見 §5.1）；修復後：提案 `failed`，可拒絕 ✓ |
| F6a | 提示注入：要求模型寫入 `../outside.txt`（真實模型） | 模型產生的 edit 路徑被 schema 拒絕，提案 `invalid`；專案外無檔案產生 | ✓ |
| F6b | API 目標路徑穿越：`../outside.txt`、`..\outside.txt`、`C:/Windows/win.ini`、`/etc/passwd`、`clamp.py/../../x` | 全部 **400**，在呼叫模型之前拒絕 | ✓ |
| F6c | 工作目錄有未追蹤檔案 | 建立修復 **400「工作目錄有未提交的修改」** | ✓ |
| F6d | 套用前使用者修改了檔案 | **409**，使用者內容保留 | △ 實際是「髒工作目錄」檢查先觸發，**檔案雜湊檢查分支未被獨立驗證** |
| F6e-1 | 機密不進入 prompt（真實 `build_prompt`） | `secret_in_prompt=false`，遮罩形式存在 | ✓ |
| F6e-2 | 機密不進入 API 回應／磁碟 | 修復前：proposal 的 `diff` 含明文金鑰並寫入 SQLite WAL（✗）。**修復後**（`1cffd65`）：新提案的 diff 與 proposal JSON 皆不含明文，顯示為遮罩形式，套用後檔案保留真實內容 | ✓ 已修復（使用者選擇「遮罩顯示與儲存」） |

## 5. 發現的缺陷與修復（feature 分支，未合併 main）

### 5.1 排隊中的修復被取消 → 提案卡在 `generating`（F5b）
- 根因：排隊中的任務取消後，`generate()` 永遠不會執行，沒有任何路徑把提案標為結束；`reject` 又拒絕 `generating` 狀態。
- 修復：`backend/app/api/jobs.py` 取消端點，若任務「排隊中、從未開始、類型為 ai_repair」，呼叫新增的 `finalise_unstarted()`（`backend/app/services/ai/repair.py`）把提案標為 `failed`。已開始的任務仍由 `generate()` 自己收尾。
- 回歸測試：`backend/tests/test_phase9_regressions.py`（3 個，含「已開始的任務不被誤改」）。
- 驗證：修復後重跑 F5b，任務 `cancelled`、提案 `failed`（「使用者在開始前取消」）、可拒絕。

### 5.2 命令注入：依賴連結路徑含 `&` 時會執行第二個指令（安全）
- 根因：依賴連結以 `cmd /c mklink /J` 建立，路徑未驗證；`cmd` 會重新解析命令列。
- 證據（探測，無害指令）：專案路徑 `…\x&ver&rem\.venv`（無空格）→ 第二個指令 `ver` 確實執行（stdout 含 `Microsoft Windows`）。有空格的路徑會被 `subprocess` 加引號而安全，所以只有無空格的路徑可被利用。
- 修復：`backend/app/services/ai/workspace.py` 在呼叫 `mklink` 前拒絕含 `& | < > ^ % ! " CR LF` 的路徑（括號不拒絕，因為 `Program Files (x86)` 是合法路徑）。
- 回歸測試：`test_dependency_link_refuses_cmd_metacharacters`（斷言 `subprocess.run` 未被呼叫、連結未建立）。
- 修復後以真實程式碼重跑探測：`WorkspaceError` 拒絕，連結未建立。

## 6. 安全風險與阻擋項

| 嚴重度 | 風險 | 狀態 |
|---|---|---|
| 已修（阻擋→解決） | 修復 diff 含明文機密並持久化 | ✓ 已於 `1cffd65`（分支）修復：diff 儲存與顯示皆為遮罩版，確認雜湊綁定遮罩版，套用仍寫入工作區的真實位元組。真實模型驗證通過（§4.3）。注意：修復前的舊提案若存在於資料庫中，仍含明文（僅限隔離測試資料庫，正式資料未受影響）。 |
| 高（已修） | 命令注入（路徑含 `&`） | ✓ 已修於 `5ceda62`（分支），需使用者決定是否合併 |
| 中（已修） | 排隊取消導致提案卡住（可用性＋使用者無法清理） | ✓ 已修於 `8f73d97`（分支） |
| 中 | 模型回應被當成程式碼：edit 內容未經語意檢查，只做語法檢查與隔離測試 | △ 有閘門（語法、隔離測試、人工確認），但語意風險仍依賴人工審查 |
| 低 | 測試結果 `validated` 狀態可能被誤解為「安全」 | △ 由 `test_status` 與套用閘門控制；UI 需明確標示（前端已顯示隔離測試狀態） |
| 低 | 路徑穿越、覆寫來源檔、任意命令：AI 模組唯一的子程序為 `mklink`（已加固）；`apply` 只由使用者 API 觸發，生成流程不會自動套用（以程式碼檢查與 F6 實測確認） | ✓ |

### 6.1 未獨立驗證的品質問題（△）
- **模型擅自改了函式簽章**：主流程的 diff 把 `clamp(x, lo, hi)` 改成 `clamp(x, lo=None, hi=None)`。測試全部通過，但 `clamp(5)` 會變成 TypeError，這是**未被要求的 API 變更**。系統只能靠測試與人工審查攔截這類問題。
- 取消「執行中」的修復：取消只在 Ollama 回應返回後生效（HTTP 呼叫不可中斷），最長等待 `ai_timeout_sec`（預設 120 秒）。本次只觀察到 2 秒取消、3–4 秒完成的情況，**長時間生成的取消延遲未量測**。

## 7. 未驗證項目（✗ 或證據限制）

| 項目 | 狀態 | 原因 |
|---|---|---|
| `qwen3:latest`、`llama3.1-8b-zh:latest` 的修復品質 | ✗ 未驗證 | 只用 `llama3.1:8b`；未做模型比較 |
| 真實模型產生的「壞 JSON／壞格式」 | △ 僅故障注入（F3） | 本次真實模型皆輸出合法 JSON；未能誘發真實壞格式 |
| 真實模型的修復在更大專案的表現、GPU 負載、併發多個修復 | ✗ 未驗證 | 範圍僅限單一小型測試專案 |
| 長時間生成的取消延遲 | ✗ 未量測 | 見 §6.1 |
| 含空白／中文路徑的隔離專案 | △ | 測試專案路徑為英數，依賴連結路徑與中文路徑的組合未驗證 |
| 以 `pip install -e` 的專案測試 `not_isolated` | △ 僅有單元測試（`test_editable_install_marks_not_isolated`） | 未用真實可編輯安裝的專案實測 |

## 8. 實際執行的指令與結果

| # | 指令 | 結果 |
|---|---|---|
| 1 | `git status --short`、`git rev-parse --short HEAD`（於 `D:\github\check`） | `main` = `b4537fc`，工作樹乾淨 |
| 2 | `Invoke-WebRequest http://127.0.0.1:11434/api/version`、`…/api/tags` | Ollama 0.40.13；3 個模型 |
| 3 | 建立隔離專案 `git init` + commit（`1681ac6`） | 工作樹乾淨 |
| 4 | 啟動隔離後端：`python backend\launch.py --port 8790`（`AISECC_DATA_DIR=…\scratchpad\ollama-e2e\data`） | `/api/health` → `{"status":"ok"}` |
| 5 | `python scenario_setup.py` | 修復前 `run 1 failed`（2/1） |
| 6 | `python scenario_repair.py` | 提案 `validated`，`test_status=passed`，約 10.2 秒 |
| 7 | `python scenario_apply.py`（第一次：409；第二次：200） | 第一次 409 為正確拒絕（夾具位元組不一致，見 §9） |
| 8 | `python scenario_retest.py` | `run 3 passed`（3/0） |
| 9 | `python scenario_revert.py` | 還原成功；第二次還原 409；還原後 `run 4 failed` |
| 10 | `python scenario_failures_a.py` | F1、F2、F3 皆 PASS |
| 11 | `python scenario_failures_b.py` | F4 PASS、F5a PASS、F5b 復現缺陷 |
| 12 | `python scenario_security.py` | F6a–F6e（F6e 復現明文機密） |
| 13 | `python verify_f5b_fixed.py`（後端已載入 `8f73d97`） | F5b 修復驗證 PASS |
| 14 | 注入探測（`cmd` `x&ver&rem`，修復前） | 第二個指令執行 |
| 15 | 注入探測（修復後 `link_dependencies`） | `WorkspaceError` 拒絕 |
| 16 | `python -m pytest -q -p no:cacheprovider tests\test_phase9_regressions.py tests\test_ai.py tests\test_review_round6.py tests\test_review_round7.py` | 79 passed |
| 17 | `ruff check .` | All checks passed |
| 18 | `powershell -ExecutionPolicy Bypass -File .\run_tests.ps1`（於 `feature/phase9-ollama-e2e` @ `5ceda62`） | **exit 0**：pytest **275 passed**（271 + 本階段 4）、ruff PASS、tsc PASS、vitest **21 passed**、build PASS、playwright **4 passed**。log：`…\scratchpad\regression-run.log` |
| 19 | `python final_run.py`（最終程式碼 `5ceda62`，真實模型） | 修復前 `failed` → 修復後 `passed` → 還原位元組相符 |

各 Python 腳本未記錄數字 exit code（僅以輸出與 verdict 判定）；**run_tests.ps1 的 exit code 為 0**，其餘為 verdict 判定。

### 4.3 機密 diff 遮罩（真實模型，`1cffd65`）

| 步驟 | 結果 |
|---|---|
| 檔案含金鑰，提交後修復 | ✓ `validated`，`test_status=passed` |
| 儲存的 diff 含明文金鑰 | ✓ 否（`secret_in_diff=false`、`secret_in_proposal_json=false`，顯示遮罩） |
| 套用（以遮罩版雜湊確認） | ✓ `200`；檔案保留真實金鑰與修正內容 |
| 還原夾具 | ✓ HEAD 回到 `1681ac6`，工作樹乾淨 |
| 證據 | `evidence.jsonl` 中 `masking:*`；腳本 `verify_masking_real.py` |

回歸測試：`test_repair_diff_is_masked_but_apply_writes_real_bytes`（修復前紅燈：「plaintext secret persisted in proposal diff」）。

## 9. 夾具問題（誠實記錄）

第一次套用失敗（409）是**正確的安全行為**，但錯誤訊息具誤導性。原因：我用 PowerShell 的 `Set-Content -Encoding utf8` 寫入測試檔，產生 BOM 與混合換行，與 Git 檢出的位元組不同（`git status` 因 autocrlf 正規化而顯示乾淨）。提案的 `before_sha256` 是以 Git 檢出版本計算，於是比對失敗。修正夾具（`git checkout` 還原）後套用成功。這揭示一個 △ 問題：錯誤訊息說「建議產生後已被修改」，但真正原因是「磁碟位元組與 Git 檢出不同」；建議後續改善訊息（或在產生時就檢查）。

## 10. 修改檔案與 commit

| commit | 分支 | 內容 |
|---|---|---|
| `8f73d97` | `feature/phase9-ollama-e2e` | 排隊取消修復 → 提案標失敗（`jobs.py`、`repair.py`）＋回歸測試 |
| `1cffd65` | `feature/phase9-ollama-e2e` | 修復 diff 儲存與顯示為遮罩版；確認雜湊綁定遮罩版；套用仍寫真實位元組＋回歸測試 |
| `5ceda62` | `feature/phase9-ollama-e2e` | 拒絕含 cmd 控制字元的依賴連結路徑（`workspace.py`）＋回歸測試 |
| （本報告 commit） | `feature/phase9-ollama-e2e` | `PHASE9_OLLAMA_E2E_REPORT.md` |

`main` 未變更（仍為 `b4537fc`）。是否合併由使用者決定。

## 11. 是否達到可供本機實際使用的標準

**結論：有條件可用。機密 diff 阻擋項已依使用者選擇修復（`1cffd65`，分支）；合併 `main` 前仍需使用者決定。**

- ✓ 真實模型端到端（檢查 → 偵測 → 產生 → 審查 → 套用 → 重測 → 還原）已驗證，修復前失敗、修復後通過，有 run ID 與檔案雜湊為證。
- ✓ 模型失敗、服務不可用、修復無效、測試不通過、取消：皆不誤報成功。
- ✓ 覆寫來源、路徑穿越、命令注入（已修）、提示注入：皆有閘門。
- ✓ 機密 diff 已改為遮罩儲存與顯示（`1cffd65`）；真實模型驗證通過。仍需注意：遮罩只針對規則能辨識的機密；模型若寫入新的未知格式機密，審查者只能看到遮罩版本，所以套用前仍應人工審查。
- △ 模型可能做出未被要求的 API 變更（測試未攔截）；需要人工審查 diff。

**建議使用條件**：限於本機、不含機密的專案；每次套用前人工審查 diff；先合併（由使用者決定）`8f73d97` 與 `5ceda62` 兩項修復。

## 12. 後續待辦（需使用者決定）

1. 是否合併 `feature/phase9-ollama-e2e` 到 `main`（機密 diff 設計已決定並實作；合併待使用者指示）。
2. （已解決）機密 diff 設計：採「遮罩顯示與儲存」。
3. 其他模型（qwen3、llama3.1-8b-zh）的比較測試，需使用者同意是否使用更多模型資源。
4. 改善「磁碟位元組與 Git 檢出不同」的錯誤訊息。
