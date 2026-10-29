import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { JobPanel } from "../components/JobPanel";
import { mockApi, renderAt, reply } from "../test-utils";
import type { Finding, Project, RepairProposal, TestCommand } from "../types";
import { DashboardPage } from "./DashboardPage";
import { ProjectsPage } from "./ProjectsPage";
import { RepairsPage } from "./RepairsPage";
import { ScansPage } from "./ScansPage";
import { SettingsPage } from "./SettingsPage";
import { TestsPage } from "./TestsPage";

const project: Project = {
  id: 1, name: "示範專案", path: "D:\\code\\demo", languages: ["python"], is_git: true, allow_project_tools: false,
  allow_network_audit: false, created_at: "2026-10-29T00:00:00", last_scan_at: null, health_score: 90, path_exists: true,
};

beforeEach(() => {
  vi.spyOn(window, "confirm").mockReturnValue(true);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("DashboardPage", () => {
  it("shows loading, then real counts", async () => {
    mockApi({
      "GET /api/dashboard": () => ({
        counts: { projects: 3, active_jobs: 0, failed_jobs_24h: 1, repairs_pending: 2 },
        open_findings_by_severity: { high: 4 }, open_findings_by_category: { secret: 4 }, projects: [], recent_jobs: [],
      }),
    });
    renderAt(<DashboardPage />);
    expect(screen.getByRole("status")).toHaveTextContent("載入中");
    expect(await screen.findByText("專案數")).toBeInTheDocument();
    expect(screen.getByText("敏感資訊")).toBeInTheDocument();
    expect(screen.getByText(/尚未加入專案/)).toBeInTheDocument();
  });

  it("shows an error with retry when the backend fails", async () => {
    let fail = true;
    mockApi({
      "GET /api/dashboard": () => (fail ? reply(500, { detail: "資料庫忙碌中" }) : {
        counts: { projects: 0, active_jobs: 0, failed_jobs_24h: 0, repairs_pending: 0 },
        open_findings_by_severity: {}, open_findings_by_category: {}, projects: [], recent_jobs: [],
      }),
    });
    renderAt(<DashboardPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("資料庫忙碌中");
    fail = false;
    await userEvent.click(screen.getByRole("button", { name: "重試" }));
    expect(await screen.findByText("專案數")).toBeInTheDocument();
  });
});

describe("ProjectsPage", () => {
  it("adds a project and surfaces backend validation errors", async () => {
    const projects: Project[] = [];
    const { calls } = mockApi({
      "GET /api/projects": () => projects,
      "POST /api/projects": (body) => {
        const b = body as { path: string };
        if (b.path === "relative") return reply(400, { detail: "請輸入絕對路徑" });
        projects.push({ ...project, path: b.path });
        return projects[0];
      },
    });
    renderAt(<ProjectsPage />);
    expect(await screen.findByText("尚未加入任何專案")).toBeInTheDocument();
    const input = screen.getByLabelText("專案路徑");
    await userEvent.type(input, "relative");
    await userEvent.click(screen.getByRole("button", { name: "新增專案" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("請輸入絕對路徑");
    await userEvent.clear(input);
    await userEvent.type(input, "D:\\code\\demo");
    await userEvent.click(screen.getByRole("button", { name: "新增專案" }));
    expect(await screen.findByText("示範專案")).toBeInTheDocument();
    expect(calls.filter((c) => c.method === "POST")).toHaveLength(2);
  });

  it("filters and sorts the project list", async () => {
    mockApi({ "GET /api/projects": () => [project, { ...project, id: 2, name: "Alpha", path: "C:\\alpha", health_score: 50 }] });
    renderAt(<ProjectsPage />);
    await screen.findByText("Alpha");
    await userEvent.type(screen.getByLabelText("搜尋專案"), "alpha");
    expect(screen.queryByText("示範專案")).not.toBeInTheDocument();
    await userEvent.clear(screen.getByLabelText("搜尋專案"));
    await userEvent.click(screen.getByText(/^名稱/));
    const rows = screen.getAllByRole("row").slice(1).map((r) => r.textContent);
    expect(rows[0]).toContain("示範專案");
  });
});

const finding: Finding = {
  id: 7, scan_id: 2, project_id: 1, tool: "builtin-secrets", rule_id: "hardcoded-password", severity: "medium",
  category: "secret", confidence: "heuristic", file_path: "app.py", line: 3, column: 1, message: "疑似硬編碼密碼：Zq8v…（長度 16）",
  fingerprint: "f", triage_status: "open", triage_note: null, extra: {},
};

describe("ScansPage", () => {
  it("shows tool status honestly and requires a reason for false positives", async () => {
    const { calls } = mockApi({
      "GET /api/projects": () => [project],
      "GET /api/jobs": () => [],
      "GET /api/scans": () => [{
        id: 2, project_id: 1, job_id: null, kind: "analysis", status: "partial", branch: "main", commit_sha: "abc",
        health_score: null, created_at: "2026-10-29T00:00:00", finished_at: null,
        tool_status: { eslint: { state: "needs_authorization", message: "需授權" }, ruff: { state: "ok", message: "" } },
      }],
      "GET /api/findings": () => ({ total: 1, items: [finding] }),
      "POST /api/findings/7/triage": (body) => ({ ...finding, triage_status: (body as { status: string }).status }),
    });
    renderAt(<ScansPage />, "/scans?project=1");
    expect(await screen.findByText(/部分工具未執行/)).toBeInTheDocument();
    expect(screen.getAllByText("需授權").length).toBeGreaterThan(0);
    await userEvent.click(await screen.findByText("疑似硬編碼密碼：Zq8v…（長度 16）"));
    await userEvent.selectOptions(screen.getByLabelText("判定結果"), "false_positive");
    const save = screen.getByRole("button", { name: "儲存判定" });
    expect(save).toBeDisabled();
    await userEvent.type(screen.getByLabelText("判定理由"), "測試資料");
    await userEvent.click(save);
    await waitFor(() => expect(calls.some((c) => c.path === "/api/findings/7/triage")).toBe(true));
    expect(calls.find((c) => c.path === "/api/findings/7/triage")?.body).toEqual({ status: "false_positive", note: "測試資料" });
  });

  it("sends search and filter parameters to the server", async () => {
    const { calls } = mockApi({
      "GET /api/projects": () => [project],
      "GET /api/jobs": () => [],
      "GET /api/scans": () => [{ id: 2, project_id: 1, kind: "analysis", status: "completed", tool_status: {}, created_at: "2026-10-29T00:00:00" }],
      "GET /api/findings": () => ({ total: 0, items: [] }),
    });
    renderAt(<ScansPage />, "/scans?project=1");
    await screen.findByText("沒有符合條件的問題");
    await userEvent.selectOptions(screen.getByLabelText("嚴重度"), "high");
    await userEvent.type(screen.getByLabelText("搜尋發現"), "sql");
    await waitFor(() => {
      const last = calls.filter((c) => c.path === "/api/findings").at(-1)!;
      expect(last.url.searchParams.get("severity")).toBe("high");
      expect(last.url.searchParams.get("q")).toBe("sql");
    });
  });
});

const command: TestCommand = {
  id: 1, key: "pytest", framework: "pytest", argv: ["python", "-m", "pytest"], cwd: ".", timeout_sec: 600,
  max_memory_mb: 2048, extra_env_names: [], inherit_full_env: false, approved: false, approved_at: null,
  source: "detected", content_hash: "a".repeat(64),
};

describe("TestsPage", () => {
  it("approves with the reviewed content hash; run is disabled until approved", async () => {
    let cmd = { ...command };
    const { calls } = mockApi({
      "GET /api/projects": () => [project],
      "GET /api/jobs": () => [],
      "GET /api/test-runs": () => [],
      "GET /api/projects/1/test-commands": () => [cmd],
      "POST /api/projects/1/test-commands/pytest/approve": () => { cmd = { ...cmd, approved: true }; return cmd; },
    });
    renderAt(<TestsPage />, "/tests?project=1");
    const run = await screen.findByRole("button", { name: "執行測試" });
    expect(run).toBeDisabled();
    await userEvent.click(screen.getByRole("button", { name: "檢視並批准" }));
    expect(await screen.findByText("已批准")).toBeInTheDocument();
    expect(calls.find((c) => c.path.endsWith("/approve"))?.body).toEqual({ content_hash: "a".repeat(64) });
    expect(screen.getByRole("button", { name: "執行測試" })).toBeEnabled();
  });

  it("rejects invalid argv JSON before calling the API", async () => {
    const { calls } = mockApi({
      "GET /api/projects": () => [project], "GET /api/jobs": () => [], "GET /api/test-runs": () => [],
      "GET /api/projects/1/test-commands": () => [command],
    });
    renderAt(<TestsPage />, "/tests?project=1");
    const argv = await screen.findByLabelText("指令參數");
    await userEvent.clear(argv);
    await userEvent.type(argv, "python -m pytest");
    await userEvent.click(screen.getByRole("button", { name: "儲存（會撤銷批准）" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("JSON");
    expect(calls.some((c) => c.method === "PUT")).toBe(false);
  });
});

const proposal: RepairProposal = {
  id: 5, project_id: 1, finding_id: null, job_id: null, provider: "ollama", model: "qwen", status: "validated",
  target_file: "src/calc.py", instruction: "", summary: "避免除以零", root_cause: "b 可能為 0",
  diff: "--- a/src/calc.py\n+++ b/src/calc.py\n@@ -1 +1 @@\n-    return a / b\n+    return a / b if b else 0\n",
  files: [{ path: "src/calc.py", before_sha256: "x", after_sha256: "y" }], base_commit: "abc",
  validation: { diff_sha256: "d".repeat(64), test_status: "failed" }, risk: { level: "high", factors: [] },
  test_run_id: 3, error: null, created_at: "2026-10-29T00:00:00", applied_at: null, reverted_at: null,
};

describe("RepairsPage", () => {
  it("explains when no model is configured", async () => {
    mockApi({
      "GET /api/ai/status": () => ({ configured: false, reason: "未設定 AI 模型" }),
      "GET /api/projects": () => [project], "GET /api/repairs": () => [],
    });
    renderAt(<RepairsPage />, "/repairs?project=1");
    expect(await screen.findByText(/尚未設定 AI 模型/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "產生修復建議" })).not.toBeInTheDocument();
  });

  it("requires acknowledging failed isolated tests and sends the diff hash", async () => {
    const { calls } = mockApi({
      "GET /api/ai/status": () => ({ configured: true, provider: "ollama", model: "qwen", external: false }),
      "GET /api/projects": () => [project],
      "GET /api/repairs": () => [proposal],
      "GET /api/repairs/5": () => proposal,
      "GET /api/projects/1/test-commands": () => [],
      "POST /api/repairs/5/apply": () => ({ status: "applied" }),
    });
    renderAt(<RepairsPage />, "/repairs?project=1");
    await userEvent.click(await screen.findByText("src/calc.py"));
    expect(await screen.findByLabelText("差異內容")).toHaveTextContent("return a / b if b else 0");
    const apply = screen.getByRole("button", { name: "批准並套用" });
    expect(apply).toBeDisabled();
    await userEvent.click(screen.getByRole("checkbox", { name: /仍要套用/ }));
    await userEvent.click(apply);
    await waitFor(() => expect(calls.some((c) => c.path === "/api/repairs/5/apply")).toBe(true));
    expect(calls.find((c) => c.path === "/api/repairs/5/apply")?.body).toEqual({
      // hash of the displayed diff, not the (deliberately wrong) validation.diff_sha256 from the API
      confirm: true, diff_sha256: "d2332a5448099808d617f8850d5b030ebceed3ddd694a037e4081a02e530e557", acknowledge_test_status: "failed",
    });
  });
});

describe("SettingsPage", () => {
  it("previews retention before deleting", async () => {
    const counts = { jobs: 2, scans: 1, test_runs: 0, repairs: 0, audit_logs: 0, files: 3 };
    const { calls } = mockApi({
      "GET /api/settings": () => ({ retention_days: 90, audit_retention_days: 730, ai_provider: "none" }),
      "GET /api/system/backups": () => [],
      "GET /api/system/tools": () => ({ version: "0.1.0", python: "3.12.10", tools: { git: "git 2.55", semgrep: null } }),
      "POST /api/system/retention": () => counts,
    });
    renderAt(<SettingsPage />);
    await userEvent.click(await screen.findByRole("button", { name: "試算將刪除的資料" }));
    expect(await screen.findByText(/試算結果：任務 2/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "確認清理" }));
    await screen.findByText(/已清理/);
    const posts = calls.filter((c) => c.path === "/api/system/retention").map((c) => c.body);
    expect(posts).toEqual([{ confirm: true, dry_run: true }, { confirm: true }]);
    expect(screen.getByText("未安裝")).toBeInTheDocument();
  });
});

describe("JobPanel", () => {
  it("streams the log, cancels, and reports completion once", async () => {
    let status = "running";
    const onDone = vi.fn();
    const { calls } = mockApi({
      "GET /api/jobs/j1": () => ({ id: "j1", type: "test_run", status, progress: 0.4, message: "執行測試中", cancel_requested: status !== "running" }),
      "GET /api/jobs/j1/log": (_b, url) => (url.searchParams.get("offset") === "0" ? { text: "第一行\n", next_offset: 10 } : { text: "", next_offset: 10 }),
      "POST /api/jobs/j1/cancel": () => { status = "cancelled"; return {}; },
    });
    renderAt(<JobPanel jobId="j1" onDone={onDone} />);
    expect(await screen.findByText(/第一行/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: "取消任務" }));
    await waitFor(() => expect(onDone).toHaveBeenCalledTimes(1), { timeout: 3000 });
    expect(screen.getByText("已取消")).toBeInTheDocument();
    expect(calls.some((c) => c.path === "/api/jobs/j1/log" && c.url.searchParams.get("offset") === "10")).toBe(true);
  });

  it("stops polling on 4xx (job deleted)", async () => {
    const { fetchMock } = mockApi({ "GET /api/jobs/gone": () => reply(404, { detail: "任務不存在" }) });
    renderAt(<JobPanel jobId="gone" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("已停止更新");
    const n = fetchMock.mock.calls.length;
    await act(async () => { await new Promise((r) => setTimeout(r, 2500)); });
    expect(fetchMock.mock.calls.length).toBe(n);
  });

  it("backs off on 5xx instead of hammering the server", async () => {
    const { fetchMock } = mockApi({ "GET /api/jobs/busy": () => reply(503, { detail: "資料庫忙碌中" }) });
    renderAt(<JobPanel jobId="busy" />);
    expect(await screen.findByRole("alert")).toHaveTextContent("重試");
    await act(async () => { await new Promise((r) => setTimeout(r, 3500)); });
    // 1s then 2s back-off: at most 3 requests in 3.5s (fixed 1s polling would make ~4).
    expect(fetchMock.mock.calls.length).toBeLessThanOrEqual(3);
  });
});
