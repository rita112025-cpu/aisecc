import { useState } from "react";
import { api, qs } from "../api";
import { AsyncView, StatusBadge } from "../components/common";
import { JobPanel } from "../components/JobPanel";
import { useAsync } from "../hooks";
import { fmtTime, label } from "../labels";
import type { AuditEntry, Job, Page, Scan } from "../types";

export function HistoryPage() {
  const [tab, setTab] = useState<"jobs" | "scans" | "audit">("jobs");
  return (
    <>
      <div className="page-title"><h2>歷史報告</h2></div>
      <div className="toolbar" role="tablist">
        {([["jobs", "任務紀錄"], ["scans", "掃描報告"], ["audit", "稽核紀錄"]] as const).map(([k, t]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "primary" : ""} onClick={() => setTab(k)}>{t}</button>
        ))}
      </div>
      {tab === "jobs" && <JobsTab />}
      {tab === "scans" && <ScansTab />}
      {tab === "audit" && <AuditTab />}
    </>
  );
}

function JobsTab() {
  const [status, setStatus] = useState("");
  const [type, setType] = useState("");
  const [open, setOpen] = useState<string | null>(null);
  const jobs = useAsync(() => api.get<Job[]>(`/jobs${qs({ status, type, limit: 200 })}`), [status, type]);
  return (
    <div className="panel">
      <div className="toolbar">
        <select aria-label="任務狀態" value={status} onChange={(e) => setStatus(e.target.value)}>
          <option value="">全部狀態</option>
          {["queued,running", "succeeded", "failed", "cancelled", "timeout", "interrupted"].map((s) => (
            <option key={s} value={s}>{s.split(",").map(label.status).join("／")}</option>
          ))}
        </select>
        <select aria-label="任務類型" value={type} onChange={(e) => setType(e.target.value)}>
          <option value="">全部類型</option>
          {["inventory_scan", "analysis_scan", "test_run", "ai_repair"].map((t) => <option key={t} value={t}>{label.jobType(t)}</option>)}
        </select>
        <button onClick={jobs.reload}>重新整理</button>
      </div>
      {open && <JobPanel jobId={open} onDone={jobs.reload} />}
      <AsyncView state={jobs} isEmpty={(d) => d.length === 0} empty="沒有任務紀錄">
        {(list) => (
          <table>
            <thead><tr><th>類型</th><th>狀態</th><th>專案</th><th>訊息／錯誤</th><th>建立</th><th>結束</th></tr></thead>
            <tbody>{list.map((j) => (
              <tr key={j.id} className={`clickable ${open === j.id ? "selected" : ""}`} onClick={() => setOpen(j.id)}>
                <td>{label.jobType(j.type)}</td><td><StatusBadge status={j.status} /></td><td>{j.project_id ?? "—"}</td>
                <td>{j.error ?? j.message}</td><td className="nowrap">{fmtTime(j.created_at)}</td><td className="nowrap">{fmtTime(j.finished_at)}</td>
              </tr>
            ))}</tbody>
          </table>
        )}
      </AsyncView>
    </div>
  );
}

function ScansTab() {
  const [kind, setKind] = useState("analysis");
  const scans = useAsync(() => api.get<Scan[]>(`/scans${qs({ kind, limit: 200 })}`), [kind]);
  return (
    <div className="panel">
      <div className="toolbar">
        <select aria-label="掃描類型" value={kind} onChange={(e) => setKind(e.target.value)}>
          <option value="analysis">靜態分析</option><option value="inventory">專案盤點</option>
        </select>
      </div>
      <AsyncView state={scans} isEmpty={(d) => d.length === 0} empty="沒有掃描紀錄">
        {(list) => (
          <table>
            <thead><tr><th>#</th><th>專案</th><th>狀態</th><th>分支</th><th>Commit</th><th>時間</th><th>報告</th></tr></thead>
            <tbody>{list.map((s) => (
              <tr key={s.id}><td>{s.id}</td><td>{s.project_id}</td><td><StatusBadge status={s.status} /></td><td>{s.branch ?? "—"}</td>
                <td className="mono">{s.commit_sha?.slice(0, 10) ?? "—"}</td><td className="nowrap">{fmtTime(s.created_at)}</td>
                <td>{s.kind === "analysis" && ["completed", "partial"].includes(s.status) ? <>
                  <a href={`/api/scans/${s.id}/report.html`} target="_blank" rel="noreferrer">HTML</a>{" "}
                  <a href={`/api/scans/${s.id}/report.json`} target="_blank" rel="noreferrer">JSON</a></> : "—"}</td></tr>
            ))}</tbody>
          </table>
        )}
      </AsyncView>
    </div>
  );
}

function AuditTab() {
  const [q, setQ] = useState("");
  const [offset, setOffset] = useState(0);
  const logs = useAsync(() => api.get<Page<AuditEntry>>(`/audit-logs${qs({ q, offset, limit: 100 })}`), [q, offset]);
  return (
    <div className="panel">
      <div className="toolbar">
        <input aria-label="搜尋稽核紀錄" placeholder="搜尋動作或目標" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
      </div>
      <AsyncView state={logs} isEmpty={(d) => d.total === 0} empty="沒有稽核紀錄">
        {(d) => (
          <>
            <table>
              <thead><tr><th>時間</th><th>動作</th><th>目標</th><th>專案</th><th>內容</th></tr></thead>
              <tbody>{d.items.map((a) => (
                <tr key={a.id}><td className="nowrap">{fmtTime(a.created_at)}</td><td className="mono">{a.action}</td>
                  <td>{a.target_type ? `${a.target_type}:${a.target_id}` : "—"}</td><td>{a.project_id ?? "—"}</td>
                  <td className="mono" style={{ maxWidth: 480, overflowWrap: "anywhere" }}>{JSON.stringify(a.detail)}</td></tr>
              ))}</tbody>
            </table>
            <div className="pager">
              <span className="muted">共 {d.total} 筆</span>
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 100))}>上一頁</button>
              <button disabled={offset + 100 >= d.total} onClick={() => setOffset(offset + 100)}>下一頁</button>
            </div>
          </>
        )}
      </AsyncView>
    </div>
  );
}
