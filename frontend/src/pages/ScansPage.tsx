import { useState } from "react";
import { api, qs } from "../api";
import { AsyncView, ErrorBox, StatusBadge } from "../components/common";
import { FindingsView } from "../components/FindingsView";
import { JobPanel, useActiveJob } from "../components/JobPanel";
import { ProjectSelect, useSelectedProject } from "../components/ProjectSelect";
import { useAction, useAsync } from "../hooks";
import { CATEGORIES, fmtTime, label } from "../labels";
import type { Scan } from "../types";

interface AnalysisDiff {
  counts: { new: number; resolved: number; unchanged: number; moved: number };
  new: { id: number; rule_id: string; severity: string; file: string | null; line: number | null; message: string }[];
  resolved: { id: number; rule_id: string; severity: string; file: string | null; line: number | null; message: string }[];
}

/** Shared by the code-scan page and the security page (which narrows the categories). */
export function AnalysisPage({ title, categories }: { title: string; categories: string[] }) {
  const [projectId, setProjectId] = useSelectedProject();
  const scans = useAsync(async () => (projectId
    ? api.get<Scan[]>(`/scans${qs({ project_id: projectId, kind: "analysis", limit: 50 })}`) : []), [projectId]);
  const [scanId, setScanId] = useState<number | null>(null);
  const [jobId, setJobId] = useActiveJob(projectId, "analysis_scan");
  const start = useAction();
  const current = scans.data?.find((s) => s.id === scanId) ?? scans.data?.find((s) => s.status !== "running");

  return (
    <>
      <div className="page-title"><h2>{title}</h2></div>
      <div className="panel toolbar">
        <ProjectSelect value={projectId} onChange={(v) => { setProjectId(v); setScanId(null); }} />
        <span className="spacer" />
        <button className="primary" disabled={!projectId || !!jobId || start.busy} onClick={async () => {
          const r = await start.run(() => api.post<{ job_id: string }>(`/projects/${projectId}/analyze`));
          if (r) setJobId(r.job_id);
        }}>執行靜態分析</button>
      </div>
      {start.error && <ErrorBox error={start.error} />}
      {jobId && <JobPanel jobId={jobId} onDone={() => { setJobId(null); setScanId(null); scans.reload(); }} />}
      {!projectId ? <div className="state">請先選擇專案</div> : (
        <AsyncView state={scans} isEmpty={(d) => d.length === 0} empty="此專案尚未執行靜態分析">
          {(list) => (
            <>
              <div className="panel toolbar">
                <label className="check">掃描
                  <select aria-label="選擇掃描" value={current?.id ?? ""} onChange={(e) => setScanId(Number(e.target.value))}>
                    {list.map((s) => <option key={s.id} value={s.id}>#{s.id} {fmtTime(s.created_at)} {label.status(s.status)}</option>)}
                  </select>
                </label>
                {current && <StatusBadge status={current.status} />}
                <span className="spacer" />
                {current && <>
                  <a href={`/api/scans/${current.id}/report.html`} target="_blank" rel="noreferrer">HTML 報告</a>
                  <a href={`/api/scans/${current.id}/report.json`} target="_blank" rel="noreferrer">JSON 報告</a>
                </>}
              </div>
              {current && <ToolStatusTable scan={current} />}
              {current && list.length > 1 && <CompareBox scans={list} />}
              {current && current.status !== "running" && (
                <FindingsView scanId={current.id} projectId={current.project_id} categories={categories} />
              )}
            </>
          )}
        </AsyncView>
      )}
    </>
  );
}

function ToolStatusTable({ scan }: { scan: Scan }) {
  const entries = Object.entries(scan.tool_status ?? {});
  if (entries.length === 0) return null;
  return (
    <div className="panel">
      <h3>工具執行狀態</h3>
      {scan.status === "partial" && <div className="notice">部分工具未執行或失敗，結果不代表完整通過。</div>}
      <table>
        <thead><tr><th>工具</th><th>狀態</th><th>發現數</th><th>耗時(秒)</th><th>說明</th></tr></thead>
        <tbody>{entries.map(([name, st]) => (
          <tr key={name}><td>{name}</td><td><StatusBadge status={st.state} kind="tool" /></td><td>{st.findings ?? "—"}</td>
            <td>{st.duration_sec ?? "—"}</td>
            <td>{st.message}{st.truncated ? "（結果已截斷）" : ""}{st.dropped_out_of_tree ? `（略過 ${st.dropped_out_of_tree} 筆專案外路徑）` : ""}</td></tr>
        ))}</tbody>
      </table>
    </div>
  );
}

function CompareBox({ scans }: { scans: Scan[] }) {
  const [a, setA] = useState(scans[1]?.id);
  const [b, setB] = useState(scans[0]?.id);
  const run = useAction();
  const [diff, setDiff] = useState<AnalysisDiff | null>(null);
  return (
    <div className="panel">
      <div className="toolbar">
        <h3 style={{ margin: 0 }}>比較兩次掃描</h3>
        <select aria-label="比較基準" value={a} onChange={(e) => setA(Number(e.target.value))}>
          {scans.map((s) => <option key={s.id} value={s.id}>#{s.id}</option>)}
        </select>
        →
        <select aria-label="比較目標" value={b} onChange={(e) => setB(Number(e.target.value))}>
          {scans.map((s) => <option key={s.id} value={s.id}>#{s.id}</option>)}
        </select>
        <button disabled={!a || !b || a === b || run.busy} onClick={async () => {
          const r = await run.run(() => api.get<{ diff: AnalysisDiff }>(`/scans/compare${qs({ a, b })}`));
          if (r) setDiff(r.diff);
        }}>比較</button>
      </div>
      {run.error && <ErrorBox error={run.error} />}
      {diff && (
        <>
          <p>新增 {diff.counts.new}、已解決 {diff.counts.resolved}、未變 {diff.counts.unchanged}（其中位置移動 {diff.counts.moved}）</p>
          <div className="split">
            <DiffList title="新增" items={diff.new} />
            <DiffList title="已解決" items={diff.resolved} />
          </div>
        </>
      )}
    </div>
  );
}

function DiffList({ title, items }: { title: string; items: AnalysisDiff["new"] }) {
  return (
    <div>
      <h3>{title}</h3>
      {items.length === 0 ? <p className="muted">無</p> : (
        <table><tbody>{items.map((f) => (
          <tr key={f.id}><td><StatusBadge status={f.severity} kind="severity" /></td><td><code>{f.rule_id}</code></td>
            <td className="mono">{f.file}{f.line ? `:${f.line}` : ""}</td><td>{f.message}</td></tr>
        ))}</tbody></table>
      )}
    </div>
  );
}

export function ScansPage() {
  return <AnalysisPage title="程式碼掃描" categories={CATEGORIES} />;
}
