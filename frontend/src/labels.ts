/** Traditional Chinese labels for backend enum values. Unknown values fall back to the raw value. */

const STATUS: Record<string, string> = {
  queued: "排隊中", running: "執行中", succeeded: "成功", failed: "失敗", cancelled: "已取消",
  timeout: "逾時", interrupted: "已中斷", completed: "完成", partial: "部分完成", passed: "通過",
  error: "錯誤", no_tests: "沒有測試", skipped: "略過", generating: "產生中", invalid: "不合法",
  validated: "已驗證", applied: "已套用", rejected: "已拒絕", reverted: "已復原", apply_failed: "套用失敗",
  not_run: "未執行", not_isolated: "未隔離", open: "未處理", false_positive: "誤報",
  accepted_risk: "接受風險", confirmed: "已確認", fixed: "已修正",
};

const TOOL_STATE: Record<string, string> = {
  ok: "已執行", not_installed: "未安裝", needs_authorization: "需授權", not_applicable: "不適用",
  skipped: "略過", error: "錯誤", timeout: "逾時", cancelled: "已取消",
};

const SEVERITY: Record<string, string> = { critical: "嚴重", high: "高", medium: "中", low: "低", info: "資訊" };

const CATEGORY: Record<string, string> = {
  lint: "程式風格", type: "型別", security: "安全", secret: "敏感資訊", dependency: "相依套件",
  complexity: "複雜度", duplication: "重複程式碼", cycle: "循環依賴", unused: "未使用",
};

const JOB_TYPE: Record<string, string> = {
  inventory_scan: "專案盤點", analysis_scan: "靜態分析", test_run: "測試執行", ai_repair: "AI 修復",
};

export const label = {
  status: (v: string) => STATUS[v] ?? v,
  tool: (v: string) => TOOL_STATE[v] ?? v,
  severity: (v: string) => SEVERITY[v] ?? v,
  category: (v: string) => CATEGORY[v] ?? v,
  jobType: (v: string) => JOB_TYPE[v] ?? v,
};

export const SEVERITIES = ["critical", "high", "medium", "low", "info"] as const;
export const CATEGORIES = Object.keys(CATEGORY);
export const SECURITY_CATEGORIES = ["security", "secret", "dependency"];

export function tone(status: string): string {
  if (["succeeded", "completed", "passed", "ok", "validated", "applied", "fixed"].includes(status)) return "ok";
  if (["failed", "error", "timeout", "invalid", "apply_failed", "high"].includes(status)) return "bad";
  if (status === "critical") return "crit";
  if (["running", "queued", "generating"].includes(status)) return "run";
  if (["partial", "needs_authorization", "interrupted", "medium", "not_isolated", "no_tests"].includes(status)) {
    return "warn";
  }
  return "";
}

export function fmtTime(iso: string | null | undefined): string {
  if (!iso) return "—";
  const d = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : `${iso}Z`);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString("zh-TW", { hour12: false });
}
