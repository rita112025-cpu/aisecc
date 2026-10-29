/** Response shapes of the backend API (see backend/app/api). */

export interface Project {
  id: number;
  name: string;
  path: string;
  languages: string[];
  is_git: boolean;
  allow_project_tools: boolean;
  allow_network_audit: boolean;
  created_at: string;
  last_scan_at: string | null;
  health_score: number | null;
  path_exists: boolean;
}

export type JobStatus = "queued" | "running" | "succeeded" | "failed" | "cancelled" | "timeout" | "interrupted";

export interface Job {
  id: string;
  type: string;
  project_id: number | null;
  status: JobStatus;
  progress: number;
  message: string;
  params: Record<string, unknown>;
  result: Record<string, unknown> | null;
  error: string | null;
  cancel_requested: boolean;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
}

export interface ToolStatus {
  state: string;
  message: string;
  findings?: number;
  duration_sec?: number;
  truncated?: boolean;
  dropped_out_of_tree?: number;
}

export interface Scan {
  id: number;
  project_id: number;
  job_id: string | null;
  kind: "inventory" | "analysis";
  status: string;
  branch: string | null;
  commit_sha: string | null;
  health_score: number | null;
  tool_status: Record<string, ToolStatus>;
  created_at: string;
  finished_at: string | null;
  summary?: Record<string, unknown>;
}

export interface Finding {
  id: number;
  scan_id: number;
  project_id: number;
  tool: string;
  rule_id: string;
  severity: "critical" | "high" | "medium" | "low" | "info";
  category: string;
  confidence: "reported" | "heuristic";
  file_path: string | null;
  line: number | null;
  column: number | null;
  message: string;
  fingerprint: string;
  triage_status: string;
  triage_note: string | null;
  extra: Record<string, unknown>;
}

export interface Page<T> {
  total: number;
  items: T[];
}

export interface TestCommand {
  id: number;
  key: string;
  framework: string;
  argv: string[];
  cwd: string;
  timeout_sec: number;
  max_memory_mb: number;
  extra_env_names: string[];
  inherit_full_env: boolean;
  approved: boolean;
  approved_at: string | null;
  source: string;
  content_hash: string;
}

export interface FailureGroup {
  category: string;
  hint: string;
  count: number;
  tests: { name: string; location: { file: string; line: number } | null; summary: string | null }[];
}

export interface TestRun {
  id: number;
  project_id: number;
  job_id: string | null;
  command_key: string;
  framework: string;
  argv: string[];
  workdir: string;
  branch: string | null;
  commit_sha: string | null;
  status: string;
  exit_code: number | null;
  total: number;
  passed: number;
  failed: number;
  skipped: number;
  errors: number;
  duration_sec: number | null;
  coverage_percent: number | null;
  coverage: Record<string, unknown> | null;
  failure_analysis: FailureGroup[];
  rerun_of_id: number | null;
  created_at: string;
}

export interface TestResult {
  id: number;
  name: string;
  classname: string | null;
  file: string | null;
  status: string;
  duration_sec: number | null;
  message: string | null;
}

export interface RepairProposal {
  id: number;
  project_id: number;
  finding_id: number | null;
  job_id: string | null;
  provider: string;
  model: string | null;
  status: string;
  target_file: string | null;
  instruction: string;
  summary: string | null;
  root_cause: string | null;
  diff: string | null;
  files: { path: string; before_sha256: string; after_sha256: string }[];
  base_commit: string | null;
  validation: { diff_sha256?: string; test_status?: string | null; syntax_errors?: string[] } & Record<string, unknown>;
  risk: { level?: string; factors?: { factor: string; value: unknown; reason: string }[] };
  test_run_id: number | null;
  error: string | null;
  created_at: string;
  applied_at: string | null;
  reverted_at: string | null;
}

export interface AuditEntry {
  id: number;
  created_at: string;
  action: string;
  target_type: string | null;
  target_id: string | null;
  project_id: number | null;
  detail: Record<string, unknown>;
}

export interface AppSettings {
  retention_days: number;
  audit_retention_days: number;
  ai_provider: "none" | "ollama" | "openai_compatible";
  ollama_url: string;
  ollama_model: string;
  ai_timeout_sec: number;
  allow_external_ai: boolean;
  external_ai_base_url: string;
  external_ai_model: string;
  external_ai_api_key_env: string;
  max_ai_calls_per_proposal: number;
  scan_max_files: number;
  scan_max_file_bytes: number;
  tool_timeout_sec: number;
  complexity_threshold: number;
  function_length_threshold: number;
}
