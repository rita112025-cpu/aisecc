import { expect, test, type Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdirSync, mkdtempSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const python = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", ".venv", "Scripts", "python.exe");

/** A small git project at a path with Chinese characters and spaces. */
function makeProject(): { root: string; name: string } {
  // Unique name: repeated runs share one backend, so names must not collide.
  const name = `端對端 專案 ${Math.random().toString(36).slice(2, 8)}`;
  const root = path.join(mkdtempSync(path.join(tmpdir(), "aisecc-proj-")), name);
  mkdirSync(path.join(root, "tests"), { recursive: true });
  writeFileSync(path.join(root, "app.py"), 'import subprocess\n\nAPI_TOKEN = "Zq8vR2kLm9Xw4TnB7pQs"\n\n\ndef run(c):\n    return subprocess.call(c, shell=True)\n');
  writeFileSync(path.join(root, "tests", "test_ok.py"), "def test_ok():\n    assert 1 + 1 == 2\n\n\ndef test_bad():\n    assert 1 == 2\n");
  writeFileSync(path.join(root, "tests", "test_slow.py"), "import time\n\n\ndef test_slow():\n    time.sleep(60)\n");
  writeFileSync(path.join(root, "requirements.txt"), "pytest==8.3.5\n");
  const git = (...args: string[]) => execFileSync("git", args, { cwd: root });
  git("init", "-q", "-b", "main");
  git("-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e", "add", "-A");
  git("-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e", "commit", "-q", "-m", "init");
  return { root, name };
}

async function noErrors(page: Page) {
  await expect(page.getByRole("alert")).toHaveCount(0);
}

const PAGES: [string, string][] = [
  ["/", "總覽 Dashboard"], ["/projects", "專案管理"], ["/scans", "程式碼掃描"], ["/security", "安全風險"],
  ["/tests", "測試中心"], ["/repairs", "AI 修復建議"], ["/git", "Git 變更比較"], ["/history", "歷史報告"],
  ["/settings", "系統設定"],
];

test("all nine pages render from the real API without errors", async ({ page }) => {
  const consoleErrors: string[] = [];
  page.on("console", (m) => { if (m.type() === "error") consoleErrors.push(m.text()); });
  page.on("pageerror", (e) => consoleErrors.push(e.message));
  const first = await page.goto("/");
  // The app is served with a strict CSP and anti-framing headers; nothing may violate them.
  expect(first?.headers()["content-security-policy"]).toContain("frame-ancestors 'none'");
  expect(first?.headers()["x-frame-options"]).toBe("DENY");
  for (const [url, title] of PAGES) {
    await page.goto(url);
    await expect(page.getByRole("heading", { level: 2, name: title })).toBeVisible();
    await expect(page.getByRole("status")).toHaveCount(0, { timeout: 15_000 });
    await noErrors(page);
  }
  await page.getByRole("link", { name: "專案管理" }).click();
  await expect(page).toHaveURL(/\/projects$/);
  expect(consoleErrors).toEqual([]);
});

test("project → inventory → analysis → triage", async ({ page }) => {
  const { root, name } = makeProject();
  await page.goto("/projects");
  await page.getByLabel("專案路徑").fill(root);
  await page.getByRole("button", { name: "新增專案" }).click();
  await page.getByRole("link", { name }).click();

  await page.getByRole("button", { name: "執行專案盤點" }).click();
  await expect(page.getByText(/健康度報告：/)).toBeVisible({ timeout: 60_000 });
  await expect(page.getByText("pytest", { exact: true }).first()).toBeVisible();

  const id = page.url().split("/").pop();
  await page.goto(`/scans?project=${id}`);
  await page.getByRole("button", { name: "執行靜態分析" }).click();
  await expect(page.getByText("工具執行狀態")).toBeVisible({ timeout: 90_000 });
  await expect(page.getByRole("cell", { name: "需授權" }).first()).toBeVisible();

  await page.goto(`/security?project=${id}`);
  const secret = page.getByRole("cell", { name: /疑似硬編碼密碼/ }).first();
  await expect(secret).toBeVisible();
  await expect(page.getByText("Zq8vR2kLm9Xw4TnB7pQs")).toHaveCount(0); // never shown in clear text
  await secret.click();
  await page.getByLabel("判定結果").selectOption("false_positive");
  await page.getByLabel("判定理由").fill("E2E 測試資料");
  await page.getByRole("button", { name: "儲存判定" }).click();
  await expect(page.getByRole("cell", { name: /疑似硬編碼密碼/ })).toHaveCount(0); // filtered out of "open"
  await noErrors(page);
});

test("test center: approval, run, refresh re-attaches to a running job, cancel", async ({ page }) => {
  const { root, name } = makeProject();
  await page.goto("/projects");
  await page.getByLabel("專案路徑").fill(root);
  await page.getByRole("button", { name: "新增專案" }).click();
  await page.getByRole("link", { name }).click();
  const id = page.url().split("/").pop();
  await page.goto(`/tests?project=${id}`);

  const cmd = JSON.stringify([python, "-m", "pytest", "tests/test_ok.py"]);
  await page.getByLabel("新指令名稱").fill("quick");
  await page.getByLabel("新指令參數").fill(cmd);
  await page.getByRole("button", { name: "新增" }).click();
  const quick = page.getByLabel("測試指令 quick");
  await expect(quick.getByRole("button", { name: "執行測試" })).toBeDisabled();
  page.once("dialog", (d) => d.accept());
  await quick.getByRole("button", { name: "檢視並批准" }).click();
  await quick.getByRole("button", { name: "執行測試" }).click();
  await expect(page.getByRole("cell", { name: "1/1/0/0" })).toBeVisible({ timeout: 60_000 });
  await page.getByRole("cell", { name: "quick" }).first().click();
  await expect(page.getByText("斷言失敗：實際結果與預期不同")).toBeVisible();

  await page.getByLabel("新指令名稱").fill("slow");
  await page.getByLabel("新指令參數").fill(JSON.stringify([python, "-m", "pytest", "tests/test_slow.py"]));
  await page.getByRole("button", { name: "新增" }).click();
  const slow = page.getByLabel("測試指令 slow");
  page.once("dialog", (d) => d.accept());
  await slow.getByRole("button", { name: "檢視並批准" }).click();
  await slow.getByRole("button", { name: "執行測試" }).click();
  await expect(page.getByTestId("job-panel")).toBeVisible();

  await page.reload(); // backend keeps job state; the page re-attaches
  const panel = page.getByTestId("job-panel");
  await expect(panel).toBeVisible({ timeout: 15_000 });
  await expect(panel.getByText("執行中")).toBeVisible({ timeout: 15_000 });
  await panel.getByRole("button", { name: "取消任務" }).click();
  await expect(page.getByRole("cell", { name: "已取消" }).first()).toBeVisible({ timeout: 30_000 });
  await noErrors(page);
});

test("AI repair page explains missing model; settings backup works", async ({ page }) => {
  await page.goto("/repairs");
  await expect(page.getByText(/尚未設定 AI 模型/)).toBeVisible();
  await page.goto("/settings");
  await page.getByRole("button", { name: "立即備份" }).click();
  await expect(page.getByText(/已建立備份 db-/)).toBeVisible();
  await expect(page.getByRole("cell", { name: /^db-\d{8}-\d{6}.*\.sqlite3$/ }).first()).toBeVisible();
  await page.getByRole("button", { name: "試算將刪除的資料" }).click();
  await expect(page.getByText(/試算結果：/)).toBeVisible();
  await noErrors(page);
});
