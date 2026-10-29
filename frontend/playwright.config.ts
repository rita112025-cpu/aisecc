import { defineConfig } from "@playwright/test";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

// E2E runs the real backend (serving the built frontend) on its own port with a throwaway data dir,
// so it never touches the user's data/ folder.
const PORT = 8799;
const dataDir = process.env.AISECC_E2E_DATA ?? mkdtempSync(path.join(tmpdir(), "aisecc-e2e-"));
process.env.AISECC_E2E_DATA = dataDir;
const repoRoot = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const python = path.join(repoRoot, ".venv", "Scripts", "python.exe");

export default defineConfig({
  testDir: "./e2e",
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: { baseURL: `http://127.0.0.1:${PORT}`, locale: "zh-TW", trace: "retain-on-failure" },
  webServer: {
    command: `"${python}" -m uvicorn app.main:create_app --factory --app-dir "${path.join(repoRoot, "backend")}" --host 127.0.0.1 --port ${PORT} --workers 1`,
    url: `http://127.0.0.1:${PORT}/api/health`,
    reuseExistingServer: false,
    timeout: 60_000,
    env: { AISECC_DATA_DIR: dataDir },
  },
});
