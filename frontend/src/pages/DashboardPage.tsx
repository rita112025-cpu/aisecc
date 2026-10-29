import { Link } from "react-router-dom";
import { api } from "../api";
import { AsyncView, StatusBadge } from "../components/common";
import { useAsync, useInterval } from "../hooks";
import { fmtTime, label, SEVERITIES } from "../labels";

interface Dashboard {
  counts: { projects: number; active_jobs: number; failed_jobs_24h: number; repairs_pending: number };
  open_findings_by_severity: Record<string, number>;
  open_findings_by_category: Record<string, number>;
  projects: {
    id: number; name: string; health_score: number | null; last_scan_at: string | null; languages: string[];
    last_test: { id: number; status: string; passed: number; failed: number; total: number } | null;
  }[];
  recent_jobs: { id: string; type: string; status: string; created_at: string; message: string; progress: number }[];
}

export function DashboardPage() {
  const state = useAsync(() => api.get<Dashboard>("/dashboard"), []);
  useInterval(state.reload, (state.data?.counts.active_jobs ?? 0) > 0 ? 3000 : null);

  return (
    <>
      <div className="page-title"><h2>總覽 Dashboard</h2><button onClick={state.reload}>重新整理</button></div>
      <AsyncView state={state}>
        {(d) => (
          <>
            <div className="grid cols-4">
              <Stat label="專案數" value={d.counts.projects} />
              <Stat label="執行中任務" value={d.counts.active_jobs} />
              <Stat label="失敗／逾時任務" value={d.counts.failed_jobs_24h} />
              <Stat label="待批准的 AI 修復" value={d.counts.repairs_pending} />
            </div>
            <div className="grid cols-2" style={{ marginTop: 16 }}>
              <div className="panel">
                <h3>未處理問題（依嚴重度）</h3>
                {Object.keys(d.open_findings_by_severity).length === 0
                  ? <p className="muted">尚無分析結果，或所有問題都已處理。</p>
                  : <table><tbody>{SEVERITIES.filter((s) => d.open_findings_by_severity[s]).map((s) => (
                    <tr key={s}><td><StatusBadge status={s} kind="severity" /></td><td>{d.open_findings_by_severity[s]}</td></tr>
                  ))}</tbody></table>}
              </div>
              <div className="panel">
                <h3>未處理問題（依類別）</h3>
                {Object.keys(d.open_findings_by_category).length === 0
                  ? <p className="muted">無資料</p>
                  : <table><tbody>{Object.entries(d.open_findings_by_category).map(([k, v]) => (
                    <tr key={k}><td>{label.category(k)}</td><td>{v}</td></tr>
                  ))}</tbody></table>}
              </div>
            </div>
            <div className="panel">
              <h3>專案狀態</h3>
              {d.projects.length === 0 ? (
                <p className="muted">尚未加入專案。前往 <Link to="/projects">專案管理</Link> 新增。</p>
              ) : (
                <table>
                  <thead><tr><th>專案</th><th>語言</th><th>健康度</th><th>最近測試</th><th>最近掃描</th></tr></thead>
                  <tbody>
                    {d.projects.map((p) => (
                      <tr key={p.id}>
                        <td><Link to={`/projects/${p.id}`}>{p.name}</Link></td>
                        <td>{p.languages.join(", ") || "—"}</td>
                        <td>{p.health_score ?? "—"}</td>
                        <td>{p.last_test ? <><StatusBadge status={p.last_test.status} /> {p.last_test.passed}/{p.last_test.total}</> : "—"}</td>
                        <td>{fmtTime(p.last_scan_at)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
            </div>
            <div className="panel">
              <h3>最近任務</h3>
              {d.recent_jobs.length === 0 ? <p className="muted">尚無任務</p> : (
                <table>
                  <thead><tr><th>類型</th><th>狀態</th><th>訊息</th><th>建立時間</th></tr></thead>
                  <tbody>{d.recent_jobs.map((j) => (
                    <tr key={j.id}><td>{label.jobType(j.type)}</td><td><StatusBadge status={j.status} /></td>
                      <td>{j.message}</td><td className="nowrap">{fmtTime(j.created_at)}</td></tr>
                  ))}</tbody>
                </table>
              )}
            </div>
          </>
        )}
      </AsyncView>
    </>
  );
}

function Stat({ label: text, value }: { label: string; value: number }) {
  return <div className="panel stat"><div className="value">{value}</div><div className="label">{text}</div></div>;
}
