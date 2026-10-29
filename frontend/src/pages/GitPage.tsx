import { useState } from "react";
import { api, qs } from "../api";
import { AsyncView, ErrorBox } from "../components/common";
import { DiffView } from "../components/DiffView";
import { ProjectSelect, useSelectedProject } from "../components/ProjectSelect";
import { useAction, useAsync } from "../hooks";
import { fmtTime } from "../labels";

interface GitInfo {
  is_repo: boolean; branch: string | null; head: string | null; head_subject: string | null; detached: boolean;
  ahead: number | null; behind: number | null; dirty: boolean | null; blocked_reason: string | null; git_missing: boolean;
  entries: { path: string; change: string; staged: boolean }[];
}
interface DiffResult { diff: string; files: { path: string; added: number | null; removed: number | null }[]; truncated: boolean }

const CHANGE: Record<string, string> = {
  modified: "修改", added: "新增", deleted: "刪除", renamed: "更名", untracked: "未追蹤", conflicted: "衝突", typechange: "型別變更",
};

export function GitPage() {
  const [projectId, setProjectId] = useSelectedProject();
  const info = useAsync(async () => (projectId ? api.get<GitInfo>(`/projects/${projectId}/git`) : null), [projectId]);
  const log = useAsync(async () => (projectId && info.data?.is_repo
    ? api.get<{ sha: string; author: string; date: string; subject: string }[]>(`/projects/${projectId}/git/log${qs({ limit: 50 })}`)
    : []), [projectId, info.data?.is_repo]);
  const [base, setBase] = useState("");
  const [target, setTarget] = useState("");
  const [result, setResult] = useState<DiffResult | null>(null);
  const act = useAction();

  const load = async (b?: string, t?: string) => {
    const r = await act.run(() => api.get<DiffResult>(`/projects/${projectId}/git/diff${qs({ base: b, target: t })}`));
    if (r) setResult(r);
  };

  return (
    <>
      <div className="page-title"><h2>Git 變更比較</h2></div>
      <div className="panel toolbar"><ProjectSelect value={projectId} onChange={(v) => { setProjectId(v); setResult(null); }} /></div>
      {!projectId ? <div className="state">請先選擇專案</div> : (
        <AsyncView state={info}>
          {(g) => !g ? null : !g.is_repo ? <div className="state">{g.git_missing ? "系統未安裝 git" : "此專案不是 Git 儲存庫"}</div> : (
            <>
              <div className="panel">
                <dl className="kv">
                  <dt>分支</dt><dd>{g.detached ? "(detached HEAD)" : g.branch}</dd>
                  <dt>HEAD</dt><dd className="mono">{g.head?.slice(0, 12) ?? "（尚無 commit）"} {g.head_subject}</dd>
                  <dt>遠端差距</dt><dd>{g.ahead === null ? "無上游分支" : `領先 ${g.ahead}、落後 ${g.behind}`}</dd>
                  <dt>工作目錄</dt><dd>{g.dirty === null ? "未知（設定受限）" : g.dirty ? `${g.entries.length} 個變更` : "乾淨"}</dd>
                </dl>
                {g.blocked_reason && <div className="notice">{g.blocked_reason}</div>}
                {g.entries.length > 0 && (
                  <table style={{ marginTop: 8 }}><tbody>{g.entries.map((e) => (
                    <tr key={e.path}><td>{CHANGE[e.change] ?? e.change}</td><td className="mono">{e.path}</td><td>{e.staged ? "已暫存" : ""}</td></tr>
                  ))}</tbody></table>
                )}
              </div>
              <div className="panel">
                <div className="toolbar">
                  <button disabled={act.busy} onClick={() => load()}>工作目錄 vs HEAD</button>
                  <span>或比較兩個版本：</span>
                  <input aria-label="基準版本" placeholder="基準（例如 HEAD~3）" value={base} onChange={(e) => setBase(e.target.value)} />
                  <input aria-label="目標版本" placeholder="目標（例如 HEAD）" value={target} onChange={(e) => setTarget(e.target.value)} />
                  <button disabled={act.busy || !base} onClick={() => load(base, target || undefined)}>比較</button>
                </div>
                {act.error && <ErrorBox error={act.error} />}
                {result && (
                  <>
                    <table><tbody>{result.files.map((f) => (
                      <tr key={f.path}><td className="mono">{f.path}</td><td style={{ color: "var(--ok)" }}>+{f.added ?? "bin"}</td><td style={{ color: "var(--bad)" }}>-{f.removed ?? "bin"}</td></tr>
                    ))}</tbody></table>
                    {result.truncated && <div className="notice">差異過大，已截斷顯示</div>}
                    <DiffView diff={result.diff} />
                  </>
                )}
              </div>
              <AsyncView state={log} isEmpty={(d) => d.length === 0} empty="沒有 commit">
                {(commits) => (
                  <div className="panel">
                    <h3>最近 Commit</h3>
                    <table><tbody>{commits.map((c, i) => (
                      <tr key={c.sha}><td className="mono">{c.sha.slice(0, 10)}</td><td>{c.subject}</td><td>{c.author}</td>
                        <td className="nowrap">{fmtTime(c.date)}</td>
                        <td>{i + 1 < commits.length && <button onClick={() => { setBase(commits[i + 1].sha); setTarget(c.sha); void load(commits[i + 1].sha, c.sha); }}>檢視此 commit 變更</button>}</td></tr>
                    ))}</tbody></table>
                  </div>
                )}
              </AsyncView>
            </>
          )}
        </AsyncView>
      )}
    </>
  );
}
