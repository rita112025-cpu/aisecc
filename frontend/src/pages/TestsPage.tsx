import { useState } from "react";
import { api, qs } from "../api";
import { AsyncView, ErrorBox, StatusBadge } from "../components/common";
import { JobPanel, useActiveJob } from "../components/JobPanel";
import { ProjectSelect, useSelectedProject } from "../components/ProjectSelect";
import { useAction, useAsync } from "../hooks";
import { fmtTime } from "../labels";
import type { Page, TestCommand, TestResult, TestRun } from "../types";

export function TestsPage() {
  const [projectId, setProjectId] = useSelectedProject();
  const [refresh, setRefresh] = useState(0);
  const commands = useAsync(async () => (projectId ? api.get<TestCommand[]>(`/projects/${projectId}/test-commands`) : []),
    [projectId, refresh]);
  const runs = useAsync(async () => (projectId ? api.get<TestRun[]>(`/test-runs${qs({ project_id: projectId, limit: 50 })}`) : []),
    [projectId, refresh]);
  const [jobId, setJobId] = useActiveJob(projectId, "test_run", refresh);
  const [selectedRun, setSelectedRun] = useState<number | null>(null);
  const act = useAction();
  const reloadAll = () => setRefresh((r) => r + 1);

  return (
    <>
      <div className="page-title"><h2>測試中心</h2></div>
      <div className="panel toolbar">
        <ProjectSelect value={projectId} onChange={(v) => { setProjectId(v); setSelectedRun(null); }} />
        <span className="spacer" />
        <button disabled={!projectId || act.busy} onClick={async () => {
          if (await act.run(() => api.post(`/projects/${projectId}/test-commands/detect`))) reloadAll();
        }}>偵測測試指令</button>
      </div>
      <div className="notice">測試指令會執行專案中的程式碼，必須先檢視並批准；修改後批准會自動失效。掃描與偵測不會執行任何測試。</div>
      {act.error && <ErrorBox error={act.error} />}
      {jobId && <JobPanel jobId={jobId} onDone={() => { setJobId(null); reloadAll(); }} />}
      {!projectId ? <div className="state">請先選擇專案</div> : (
        <>
          <AsyncView state={commands} isEmpty={(d) => d.length === 0} empty="尚無測試指令。按「偵測測試指令」或手動新增。">
            {(list) => (
              <div className="panel">
                <h3>測試指令</h3>
                {list.map((c) => (
                  <CommandEditor key={`${c.key}-${c.content_hash}`} projectId={projectId} cmd={c} busy={!!jobId}
                    onChanged={reloadAll} onStarted={setJobId} />
                ))}
              </div>
            )}
          </AsyncView>
          <NewCommand projectId={projectId} onCreated={reloadAll} />
          <AsyncView state={runs} isEmpty={(d) => d.length === 0} empty="尚無測試執行紀錄">
            {(list) => <RunList runs={list} selected={selectedRun} onSelect={setSelectedRun} />}
          </AsyncView>
          {selectedRun && <RunDetail runId={selectedRun} disabled={!!jobId} onRerun={(id) => { setJobId(id); }} />}
        </>
      )}
    </>
  );
}

function CommandEditor({ projectId, cmd, busy, onChanged, onStarted }: {
  projectId: number; cmd: TestCommand; busy: boolean; onChanged: () => void; onStarted: (jobId: string) => void;
}) {
  const [argv, setArgv] = useState(JSON.stringify(cmd.argv));
  const [cwd, setCwd] = useState(cmd.cwd);
  const [timeout, setTimeoutSec] = useState(cmd.timeout_sec);
  const [env, setEnv] = useState(cmd.extra_env_names.join(", "));
  const [coverage, setCoverage] = useState(false);
  const act = useAction();
  const base = `/projects/${projectId}/test-commands/${cmd.key}`;

  const save = () => act.run(async () => {
    let parsed: unknown;
    try {
      parsed = JSON.parse(argv);
    } catch {
      throw new Error("指令參數必須是 JSON 字串陣列，例如 [\"python\", \"-m\", \"pytest\"]");
    }
    await api.put(base, {
      framework: cmd.framework, argv: parsed, cwd, timeout_sec: timeout, max_memory_mb: cmd.max_memory_mb,
      extra_env_names: env.split(",").map((s) => s.trim()).filter(Boolean), inherit_full_env: cmd.inherit_full_env,
    });
    onChanged();
  });

  return (
    <div className="panel" aria-label={`測試指令 ${cmd.key}`}>
      <div className="toolbar">
        <strong>{cmd.key}</strong><span className="badge">{cmd.framework}</span>
        {cmd.approved ? <span className="badge ok">已批准</span> : <span className="badge warn">未批准</span>}
        <span className="muted">{cmd.source === "detected" ? "自動偵測" : "手動"}</span>
      </div>
      <div className="grid cols-2">
        <label className="field">指令參數（JSON 陣列）<input aria-label="指令參數" value={argv} onChange={(e) => setArgv(e.target.value)} /></label>
        <label className="field">工作目錄（相對專案）<input aria-label="工作目錄" value={cwd} onChange={(e) => setCwd(e.target.value)} /></label>
        <label className="field">逾時（秒）<input type="number" aria-label="逾時秒數" value={timeout} onChange={(e) => setTimeoutSec(Number(e.target.value))} /></label>
        <label className="field">額外傳入的環境變數名稱（逗號分隔）<input aria-label="環境變數" value={env} onChange={(e) => setEnv(e.target.value)} /></label>
      </div>
      <p className="muted mono">內容雜湊：{cmd.content_hash.slice(0, 16)}…（批准時會核對）</p>
      <div className="toolbar">
        <button onClick={save} disabled={act.busy}>儲存（會撤銷批准）</button>
        {!cmd.approved && (
          <button className="primary" disabled={act.busy} onClick={() => {
            if (!window.confirm(`批准後將允許執行：\n${cmd.argv.join(" ")}\n工作目錄：${cmd.cwd}\n確定批准？`)) return;
            void act.run(async () => { await api.post(`${base}/approve`, { content_hash: cmd.content_hash }); onChanged(); });
          }}>檢視並批准</button>
        )}
        {cmd.approved && <button onClick={() => act.run(async () => { await api.post(`${base}/revoke`); onChanged(); })}>撤銷批准</button>}
        <label className="check"><input type="checkbox" checked={coverage} onChange={(e) => setCoverage(e.target.checked)} />收集覆蓋率</label>
        <button className="primary" disabled={!cmd.approved || busy || act.busy} onClick={async () => {
          const r = await act.run(() => api.post<{ job_id: string }>(`/projects/${projectId}/test-runs`, { command_key: cmd.key, coverage }));
          if (r) onStarted(r.job_id);
        }}>執行測試</button>
        <span className="spacer" />
        <button className="danger" onClick={() => {
          if (window.confirm(`刪除測試指令 ${cmd.key}？`)) void act.run(async () => { await api.del(base); onChanged(); });
        }}>刪除</button>
      </div>
      {act.error && <ErrorBox error={act.error} />}
    </div>
  );
}

function NewCommand({ projectId, onCreated }: { projectId: number; onCreated: () => void }) {
  const [key, setKey] = useState("");
  const [framework, setFramework] = useState("pytest");
  const [argv, setArgv] = useState('["python", "-m", "pytest"]');
  const act = useAction();
  return (
    <div className="panel">
      <h3>手動新增測試指令</h3>
      <div className="toolbar">
        <input aria-label="新指令名稱" placeholder="名稱（小寫英數）" value={key} onChange={(e) => setKey(e.target.value)} />
        <select aria-label="新指令框架" value={framework} onChange={(e) => setFramework(e.target.value)}>
          {["pytest", "vitest", "playwright", "dotnet"].map((f) => <option key={f}>{f}</option>)}
        </select>
        <input aria-label="新指令參數" style={{ flex: 1 }} value={argv} onChange={(e) => setArgv(e.target.value)} />
        <button disabled={!key || act.busy} onClick={() => act.run(async () => {
          let parsed: unknown;
          try {
            parsed = JSON.parse(argv);
          } catch {
            throw new Error("指令參數必須是 JSON 字串陣列");
          }
          await api.put(`/projects/${projectId}/test-commands/${key}`, { framework, argv: parsed, cwd: "." });
          setKey("");
          onCreated();
        })}>新增</button>
      </div>
      {act.error && <ErrorBox error={act.error} />}
    </div>
  );
}

function RunList({ runs, selected, onSelect }: { runs: TestRun[]; selected: number | null; onSelect: (id: number) => void }) {
  const [a, setA] = useState<number | "">("");
  const [b, setB] = useState<number | "">("");
  const cmp = useAction();
  const [diff, setDiff] = useState<Record<string, string[] | Record<string, number>> | null>(null);
  return (
    <div className="panel">
      <h3>執行紀錄</h3>
      <table>
        <thead><tr><th>#</th><th>指令</th><th>狀態</th><th>通過/失敗/略過/錯誤</th><th>耗時</th><th>覆蓋率</th><th>Commit</th><th>時間</th></tr></thead>
        <tbody>{runs.map((r) => (
          <tr key={r.id} className={`clickable ${selected === r.id ? "selected" : ""}`} onClick={() => onSelect(r.id)}>
            <td>{r.id}{r.rerun_of_id ? <span className="muted">（重跑 #{r.rerun_of_id}）</span> : ""}</td><td>{r.command_key}</td>
            <td><StatusBadge status={r.status} /></td><td>{r.passed}/{r.failed}/{r.skipped}/{r.errors}</td>
            <td>{r.duration_sec != null ? `${r.duration_sec.toFixed(1)}s` : "—"}</td>
            <td>{r.coverage_percent != null ? `${r.coverage_percent}%` : "—"}</td>
            <td className="mono">{r.commit_sha?.slice(0, 8) ?? "—"}</td><td className="nowrap">{fmtTime(r.created_at)}</td>
          </tr>
        ))}</tbody>
      </table>
      <div className="toolbar" style={{ marginTop: 12 }}>
        比較
        <select aria-label="比較執行 A" value={a} onChange={(e) => setA(Number(e.target.value))}>
          <option value="">—</option>{runs.map((r) => <option key={r.id} value={r.id}>#{r.id} {r.commit_sha?.slice(0, 8)}</option>)}
        </select>→
        <select aria-label="比較執行 B" value={b} onChange={(e) => setB(Number(e.target.value))}>
          <option value="">—</option>{runs.map((r) => <option key={r.id} value={r.id}>#{r.id} {r.commit_sha?.slice(0, 8)}</option>)}
        </select>
        <button disabled={!a || !b || a === b} onClick={async () => {
          const r = await cmp.run(() => api.get<{ diff: Record<string, string[]> }>(`/test-runs/compare${qs({ a, b })}`));
          if (r) setDiff(r.diff);
        }}>比較</button>
      </div>
      {cmp.error && <ErrorBox error={cmp.error} />}
      {diff && (
        <div className="grid cols-2">
          {[["newly_failing", "新增失敗"], ["fixed", "已修正"], ["still_failing", "持續失敗"], ["added", "新增測試"], ["removed", "移除測試"]].map(([k, t]) => (
            <div key={k}><strong>{t}（{(diff[k] as string[]).length}）</strong>
              <ul>{(diff[k] as string[]).slice(0, 50).map((n) => <li key={n} className="mono">{n}</li>)}</ul></div>
          ))}
        </div>
      )}
    </div>
  );
}

function RunDetail({ runId, disabled, onRerun }: { runId: number; disabled: boolean; onRerun: (jobId: string) => void }) {
  const run = useAsync(() => api.get<TestRun>(`/test-runs/${runId}`), [runId]);
  const [status, setStatus] = useState("");
  const results = useAsync(() => api.get<Page<TestResult>>(`/test-runs/${runId}/results${qs({ status })}`), [runId, status]);
  const act = useAction();
  return (
    <AsyncView state={run}>
      {(r) => (
        <div className="panel">
          <div className="toolbar">
            <h3 style={{ margin: 0 }}>執行 #{r.id} 詳細</h3><StatusBadge status={r.status} />
            <span className="spacer" />
            <button disabled={disabled || act.busy || (r.failed + r.errors === 0)} onClick={async () => {
              const res = await act.run(() => api.post<{ job_id: string }>(`/test-runs/${r.id}/rerun-failed`));
              if (res) onRerun(res.job_id);
            }}>只重跑失敗的測試</button>
          </div>
          {act.error && <ErrorBox error={act.error} />}
          <p className="mono muted">{r.argv.join(" ")}（{r.workdir}）exit={r.exit_code ?? "—"}</p>
          {r.coverage && "unavailable" in r.coverage && <p className="muted">覆蓋率：{String(r.coverage.unavailable)}</p>}
          {r.failure_analysis.length > 0 && (
            <>
              <h3>失敗原因分析</h3>
              {r.failure_analysis.map((g, i) => (
                <div key={i} className="notice"><strong>{g.category}</strong>（{g.count}）：{g.hint}
                  {g.tests.slice(0, 5).map((t) => (
                    <div key={t.name} className="mono">• {t.name}{t.location ? ` @ ${t.location.file}:${t.location.line}` : ""}</div>
                  ))}
                </div>
              ))}
            </>
          )}
          <div className="toolbar">
            <select aria-label="結果過濾" value={status} onChange={(e) => setStatus(e.target.value)}>
              <option value="">全部結果</option><option value="failed,error">失敗與錯誤</option>
              <option value="passed">通過</option><option value="skipped">略過</option>
            </select>
          </div>
          <AsyncView state={results} isEmpty={(d) => d.total === 0} empty="沒有測試結果">
            {(d) => (
              <table>
                <thead><tr><th>狀態</th><th>測試</th><th>耗時</th><th>訊息</th></tr></thead>
                <tbody>{d.items.map((t) => (
                  <tr key={t.id}><td><StatusBadge status={t.status} /></td><td className="mono">{t.name}</td>
                    <td>{t.duration_sec != null ? `${t.duration_sec.toFixed(2)}s` : "—"}</td>
                    <td><pre style={{ margin: 0, whiteSpace: "pre-wrap" }}>{t.message?.slice(0, 1500)}</pre></td></tr>
                ))}</tbody>
              </table>
            )}
          </AsyncView>
        </div>
      )}
    </AsyncView>
  );
}
