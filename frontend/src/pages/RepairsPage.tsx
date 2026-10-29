import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, qs } from "../api";
import { AsyncView, ErrorBox, StatusBadge } from "../components/common";
import { DiffView } from "../components/DiffView";
import { JobPanel } from "../components/JobPanel";
import { ProjectSelect, useSelectedProject } from "../components/ProjectSelect";
import { useAction, useAsync } from "../hooks";
import { fmtTime, label } from "../labels";
import type { Finding, RepairProposal, TestCommand } from "../types";

interface AIStatus { configured: boolean; reason?: string; provider?: string; model?: string; external?: boolean; reachable?: boolean }

export function RepairsPage() {
  const [projectId, setProjectId] = useSelectedProject();
  const [params] = useSearchParams();
  const findingId = params.get("finding");
  const status = useAsync(() => api.get<AIStatus>("/ai/status"), []);
  const [refresh, setRefresh] = useState(0);
  const list = useAsync(async () => (projectId ? api.get<RepairProposal[]>(`/repairs${qs({ project_id: projectId })}`) : []),
    [projectId, refresh]);
  const [selected, setSelected] = useState<number | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const generating = list.data?.find((p) => p.status === "generating" && p.job_id);

  useEffect(() => {
    if (generating?.job_id && !jobId) setJobId(generating.job_id);
  }, [generating, jobId]);

  return (
    <>
      <div className="page-title"><h2>AI 修復建議</h2></div>
      <AsyncView state={status}>
        {(s) => s.configured ? (
          <div className="panel">AI 模型：{s.provider} / {s.model}
            {s.external ? <span className="badge warn" style={{ marginLeft: 8 }}>外部服務（程式碼會送出）</span> : <span className="badge ok" style={{ marginLeft: 8 }}>本機</span>}
            {s.reachable === false && <span className="badge bad" style={{ marginLeft: 8 }}>無法連線</span>}
          </div>
        ) : <div className="notice">尚未設定 AI 模型：{s.reason}。其他功能不受影響，可在「系統設定」設定 Ollama。</div>}
      </AsyncView>
      <div className="panel toolbar"><ProjectSelect value={projectId} onChange={(v) => { setProjectId(v); setSelected(null); }} /></div>
      {jobId && <JobPanel jobId={jobId} onDone={() => { setJobId(null); setRefresh((r) => r + 1); }} />}
      {projectId && status.data?.configured && (
        <CreateRepair projectId={projectId} findingId={findingId ? Number(findingId) : null}
          onCreated={(pid, jid) => { setSelected(pid); setJobId(jid); setRefresh((r) => r + 1); }} />
      )}
      {!projectId ? <div className="state">請先選擇專案（非 Git 專案或工作目錄有未提交修改時無法使用 AI 修復）</div> : (
        <AsyncView state={list} isEmpty={(d) => d.length === 0} empty="尚無修復建議">
          {(items) => (
            <div className="panel">
              <h3>修復歷史</h3>
              <table>
                <thead><tr><th>#</th><th>檔案</th><th>狀態</th><th>風險</th><th>模型</th><th>建立</th></tr></thead>
                <tbody>{items.map((p) => (
                  <tr key={p.id} className={`clickable ${selected === p.id ? "selected" : ""}`} onClick={() => setSelected(p.id)}>
                    <td>{p.id}</td><td className="mono">{p.target_file}</td><td><StatusBadge status={p.status} /></td>
                    <td>{p.risk?.level ? <StatusBadge status={p.risk.level} kind="severity" /> : "—"}</td>
                    <td>{p.model ?? p.provider}</td><td className="nowrap">{fmtTime(p.created_at)}</td>
                  </tr>
                ))}</tbody>
              </table>
            </div>
          )}
        </AsyncView>
      )}
      {selected && <RepairDetail id={selected} refresh={refresh} onChanged={() => setRefresh((r) => r + 1)} />}
    </>
  );
}

function CreateRepair({ projectId, findingId, onCreated }: {
  projectId: number; findingId: number | null; onCreated: (proposalId: number, jobId: string) => void;
}) {
  const finding = useAsync(async () => (findingId ? api.get<Finding>(`/findings/${findingId}`) : null), [findingId]);
  const commands = useAsync(() => api.get<TestCommand[]>(`/projects/${projectId}/test-commands`), [projectId]);
  const [file, setFile] = useState("");
  const [instruction, setInstruction] = useState("");
  const [testKey, setTestKey] = useState("");
  const act = useAction();
  const target = finding.data?.file_path ?? file;
  return (
    <div className="panel">
      <h3>建立修復建議</h3>
      {finding.data && <p>針對問題 #{finding.data.id}：<code>{finding.data.rule_id}</code> {finding.data.message}（{finding.data.file_path}:{finding.data.line}）</p>}
      <div className="grid cols-2">
        {!finding.data && <label className="field">目標檔案（相對路徑，需為 Git 追蹤檔）<input aria-label="目標檔案" value={file} onChange={(e) => setFile(e.target.value)} /></label>}
        <label className="field">在隔離工作區執行的測試（需已批准）
          <select aria-label="驗證測試" value={testKey} onChange={(e) => setTestKey(e.target.value)}>
            <option value="">不執行測試</option>
            {commands.data?.filter((c) => c.approved).map((c) => <option key={c.key} value={c.key}>{c.key}</option>)}
          </select>
        </label>
      </div>
      <label className="field">修復說明<textarea aria-label="修復說明" rows={3} value={instruction} onChange={(e) => setInstruction(e.target.value)} /></label>
      <div className="toolbar" style={{ marginTop: 8 }}>
        <button className="primary" disabled={act.busy || !target} onClick={async () => {
          const r = await act.run(() => api.post<{ proposal_id: number; job_id: string }>(`/projects/${projectId}/repairs`, {
            finding_id: finding.data?.id ?? null, target_file: finding.data ? null : file, instruction,
            test_command_key: testKey || null,
          }));
          if (r) onCreated(r.proposal_id, r.job_id);
        }}>產生修復建議</button>
        <span className="muted">AI 只會在隔離工作區修改；套用到專案前需要您確認。</span>
      </div>
      {act.error && <ErrorBox error={act.error} />}
    </div>
  );
}

function RepairDetail({ id, refresh, onChanged }: { id: number; refresh: number; onChanged: () => void }) {
  const p = useAsync(() => api.get<RepairProposal>(`/repairs/${id}`), [id, refresh]);
  const [ack, setAck] = useState(false);
  const act = useAction();
  return (
    <AsyncView state={p}>
      {(r) => {
        const testStatus = r.validation.test_status ?? "not_run";
        const needsAck = testStatus !== "passed";
        return (
          <div className="panel" aria-label="修復建議詳細">
            <div className="toolbar"><h3 style={{ margin: 0 }}>建議 #{r.id}</h3><StatusBadge status={r.status} /></div>
            {r.error && <ErrorBox error={r.error} />}
            <dl className="kv">
              <dt>摘要</dt><dd>{r.summary ?? "—"}</dd>
              <dt>根本原因</dt><dd>{r.root_cause ?? "—"}</dd>
              <dt>隔離測試</dt><dd><StatusBadge status={testStatus} />{r.test_run_id && <span className="muted"> 測試執行 #{r.test_run_id}</span>}</dd>
              <dt>回歸風險</dt><dd>{r.risk?.level ? <StatusBadge status={r.risk.level} kind="severity" /> : "—"}</dd>
            </dl>
            {r.risk?.factors && (
              <ul>{r.risk.factors.map((f, i) => <li key={i}>{f.factor}：{JSON.stringify(f.value)}（{f.reason}）</li>)}</ul>
            )}
            {r.diff && <DiffView diff={r.diff} />}
            {r.status === "validated" && (
              <div className="toolbar" style={{ marginTop: 12 }}>
                {needsAck && (
                  <label className="check"><input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} />
                    我了解隔離測試狀態為「{label.status(testStatus)}」，仍要套用</label>
                )}
                <button className="primary" disabled={act.busy || (needsAck && !ack)} onClick={async () => {
                  if (!window.confirm(`將以上 diff 套用到專案（${r.files.length} 個檔案，會先備份，不會 commit）。確定？`)) return;
                  if (await act.run(() => api.post(`/repairs/${r.id}/apply`, {
                    confirm: true, diff_sha256: r.validation.diff_sha256,
                    acknowledge_test_status: needsAck ? testStatus : null,
                  }))) onChanged();
                }}>批准並套用</button>
                <button className="danger" disabled={act.busy} onClick={async () => {
                  if (await act.run(() => api.post(`/repairs/${r.id}/reject`))) onChanged();
                }}>拒絕</button>
              </div>
            )}
            {r.status === "applied" && (
              <div className="toolbar" style={{ marginTop: 12 }}>
                <span>已於 {fmtTime(r.applied_at)} 套用</span>
                <button onClick={async () => {
                  if (!window.confirm("還原為套用前的檔案內容？若之後又修改過將拒絕還原。")) return;
                  if (await act.run(() => api.post(`/repairs/${r.id}/revert`))) onChanged();
                }}>復原</button>
              </div>
            )}
            {act.error && <ErrorBox error={act.error} />}
          </div>
        );
      }}
    </AsyncView>
  );
}
