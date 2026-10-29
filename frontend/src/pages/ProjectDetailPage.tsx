import { useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { api, qs } from "../api";
import { AsyncView, ErrorBox, StatusBadge } from "../components/common";
import { JobPanel, useActiveJob } from "../components/JobPanel";
import { useAction, useAsync } from "../hooks";
import { fmtTime } from "../labels";
import type { Project, Scan } from "../types";

interface Inventory {
  file_count: number; truncated: boolean; symlink_count: number;
  detection: {
    languages: string[]; test_frameworks: string[]; package_manager: string | null; warnings: string[];
    dependencies: { name: string; spec: string; ecosystem: string; source: string; dev: boolean; pinned_version: string | null }[];
  };
  git: { is_repo: boolean; branch: string | null; head: string | null; dirty: boolean | null; blocked_reason: string | null;
    counts: Record<string, number>; entries: { path: string; change: string }[] };
  health: { score: number; grade: string; deductions: { points: number; reason: string }[]; notes: string[] };
}

export function ProjectDetailPage() {
  const id = Number(useParams().id);
  const navigate = useNavigate();
  const project = useAsync(() => api.get<Project>(`/projects/${id}`), [id]);
  const scans = useAsync(() => api.get<Scan[]>(`/scans${qs({ project_id: id, kind: "inventory", limit: 50 })}`), [id]);
  const latestId = scans.data?.find((s) => s.status === "completed" || s.status === "partial")?.id;
  const latest = useAsync(async () => (latestId ? api.get<Scan>(`/scans/${latestId}`) : null), [latestId]);
  const [jobId, setJobId] = useActiveJob(id, "inventory_scan");
  const action = useAction();

  const toggle = async (field: "allow_project_tools" | "allow_network_audit", value: boolean) => {
    const text = field === "allow_project_tools"
      ? "允許執行專案工具：eslint、tsc 會執行此專案 node_modules 內的程式碼與設定檔；Git 也會讀取專案自訂的 filter 設定。確定要開啟嗎？"
      : "允許網路稽核：npm audit、pip-audit、semgrep 會把相依套件清單送往外部服務。確定要開啟嗎？";
    if (value && !window.confirm(text)) return;
    if (await action.run(() => api.patch(`/projects/${id}`, { [field]: value }))) project.reload();
  };

  return (
    <AsyncView state={project}>
      {(p) => (
        <>
          <div className="page-title">
            <h2>{p.name}</h2>
            <div className="toolbar">
              <button className="primary" disabled={!!jobId || !p.path_exists}
                onClick={() => action.run(() => api.post<{ job_id: string }>(`/projects/${id}/scan`)).then((r) => r && setJobId(r.job_id))}>
                執行專案盤點
              </button>
              <button className="danger" onClick={async () => {
                if (!window.confirm("只會從控制中心移除此專案的紀錄，不會刪除磁碟上的任何檔案。確定？")) return;
                if (await action.run(() => api.del(`/projects/${id}`))) navigate("/projects");
              }}>移除專案</button>
            </div>
          </div>
          {action.error && <ErrorBox error={action.error} />}
          {!p.path_exists && <div className="notice">專案路徑已不存在：{p.path}</div>}
          {jobId && <JobPanel jobId={jobId} onDone={() => { setJobId(null); scans.reload(); project.reload(); }} />}
          <div className="grid cols-2">
            <div className="panel">
              <h3>基本資訊</h3>
              <dl className="kv">
                <dt>路徑</dt><dd className="mono">{p.path}</dd>
                <dt>語言</dt><dd>{p.languages.join(", ") || "—"}</dd>
                <dt>Git</dt><dd>{p.is_git ? "是" : "否（AI 修復不可用）"}</dd>
                <dt>健康度</dt><dd>{p.health_score ?? "尚未盤點"}</dd>
              </dl>
            </div>
            <div className="panel">
              <h3>授權設定</h3>
              <label className="check"><input type="checkbox" checked={p.allow_project_tools}
                onChange={(e) => toggle("allow_project_tools", e.target.checked)} />允許執行專案工具（eslint、tsc）</label>
              <br />
              <label className="check"><input type="checkbox" checked={p.allow_network_audit}
                onChange={(e) => toggle("allow_network_audit", e.target.checked)} />允許網路稽核（npm audit、pip-audit、semgrep）</label>
              <p className="muted">變更會寫入稽核紀錄。測試指令另有獨立批准流程（測試中心）。</p>
            </div>
          </div>
          <AsyncView state={latest} isEmpty={(d) => d === null} empty="尚未執行專案盤點">
            {(scan) => <InventoryView inv={scan!.summary as unknown as Inventory} />}
          </AsyncView>
          <FileTree projectId={id} />
          <ScanHistory scans={scans} />
        </>
      )}
    </AsyncView>
  );
}

function InventoryView({ inv }: { inv: Inventory }) {
  return (
    <>
      <div className="grid cols-2">
        <div className="panel">
          <h3>健康度報告：{inv.health.score}（{inv.health.grade}）</h3>
          {inv.health.deductions.length === 0 ? <p className="muted">沒有扣分項目</p> : (
            <table><tbody>{inv.health.deductions.map((d, i) => <tr key={i}><td>-{d.points}</td><td>{d.reason}</td></tr>)}</tbody></table>
          )}
          {inv.health.notes.map((n, i) => <p key={i} className="muted">※ {n}</p>)}
        </div>
        <div className="panel">
          <h3>Git 與結構</h3>
          <dl className="kv">
            <dt>檔案數</dt><dd>{inv.file_count}{inv.truncated && <span className="badge warn"> 已截斷</span>}</dd>
            <dt>符號連結</dt><dd>{inv.symlink_count}（不跟隨）</dd>
            <dt>測試框架</dt><dd>{inv.detection.test_frameworks.join(", ") || "未偵測到"}</dd>
            <dt>分支</dt><dd>{inv.git.is_repo ? inv.git.branch ?? "(detached)" : "非 Git"}</dd>
            <dt>Commit</dt><dd className="mono">{inv.git.head?.slice(0, 12) ?? "—"}</dd>
            <dt>工作目錄</dt><dd>{inv.git.dirty === null ? "未知（設定受限）" : inv.git.dirty ? `有 ${inv.git.entries.length} 個未提交變更` : "乾淨"}</dd>
          </dl>
          {inv.git.blocked_reason && <div className="notice">{inv.git.blocked_reason}</div>}
          {inv.detection.warnings.map((w, i) => <p key={i} className="muted">⚠ {w}</p>)}
        </div>
      </div>
      <div className="panel">
        <h3>相依套件（{inv.detection.dependencies.length}）</h3>
        {inv.detection.dependencies.length === 0 ? <p className="muted">未偵測到相依套件</p> : (
          <table>
            <thead><tr><th>名稱</th><th>版本條件</th><th>生態系</th><th>來源</th><th>類型</th></tr></thead>
            <tbody>{inv.detection.dependencies.map((d, i) => (
              <tr key={i}><td>{d.name}</td><td className="mono">{d.spec || "（未指定）"}</td><td>{d.ecosystem}</td>
                <td className="mono">{d.source}</td><td>{d.dev ? "開發" : "執行期"}</td></tr>
            ))}</tbody>
          </table>
        )}
      </div>
    </>
  );
}

function FileTree({ projectId }: { projectId: number }) {
  const [path, setPath] = useState("");
  const tree = useAsync(() => api.get<{ path: string; truncated: boolean; items: { name: string; path: string; type: string; size: number | null }[] }>(
    `/projects/${projectId}/tree${qs({ path })}`), [projectId, path]);
  return (
    <div className="panel">
      <h3>檔案結構：/{path}</h3>
      {path && <button onClick={() => setPath(path.split("/").slice(0, -1).join("/"))}>↑ 上一層</button>}
      <AsyncView state={tree} isEmpty={(d) => d.items.length === 0} empty="空資料夾">
        {(d) => (
          <div>
            {d.items.map((it) => (
              <div key={it.path} className="tree-item">
                {it.type === "dir" ? <a href="#" onClick={(e) => { e.preventDefault(); setPath(it.path); }}>📁 {it.name}</a>
                  : it.type === "symlink" ? <span className="muted">🔗 {it.name}（連結，不跟隨）</span>
                    : <span>📄 {it.name} <span className="muted">{it.size ?? ""}</span></span>}
              </div>
            ))}
            {d.truncated && <p className="muted">項目過多，僅顯示前 2000 筆</p>}
          </div>
        )}
      </AsyncView>
    </div>
  );
}

function ScanHistory({ scans }: { scans: ReturnType<typeof useAsync<Scan[]>> }) {
  const [sel, setSel] = useState<number[]>([]);
  const cmp = useAction();
  const [diff, setDiff] = useState<Record<string, unknown> | null>(null);
  const pick = (id: number) => setSel((s) => (s.includes(id) ? s.filter((x) => x !== id) : [...s, id].slice(-2)));
  return (
    <div className="panel">
      <div className="toolbar"><h3 style={{ margin: 0 }}>盤點紀錄</h3><span className="spacer" />
        <button disabled={sel.length !== 2 || cmp.busy} onClick={async () => {
          const [a, b] = [...sel].sort((x, y) => x - y);
          const r = await cmp.run(() => api.get<{ diff: Record<string, unknown> }>(`/scans/compare${qs({ a, b })}`));
          if (r) setDiff(r.diff);
        }}>比較選取的兩次</button></div>
      {cmp.error && <ErrorBox error={cmp.error} />}
      <AsyncView state={scans} isEmpty={(d) => d.length === 0} empty="尚無盤點紀錄">
        {(d) => (
          <table>
            <thead><tr><th></th><th>#</th><th>狀態</th><th>分支</th><th>Commit</th><th>健康度</th><th>時間</th></tr></thead>
            <tbody>{d.map((s) => (
              <tr key={s.id}><td><input type="checkbox" aria-label={`選取掃描 ${s.id}`} checked={sel.includes(s.id)} onChange={() => pick(s.id)} /></td>
                <td>{s.id}</td><td><StatusBadge status={s.status} /></td><td>{s.branch ?? "—"}</td>
                <td className="mono">{s.commit_sha?.slice(0, 10) ?? "—"}</td><td>{s.health_score ?? "—"}</td>
                <td className="nowrap">{fmtTime(s.created_at)}</td></tr>
            ))}</tbody>
          </table>
        )}
      </AsyncView>
      {diff && <pre className="diff" aria-label="盤點差異">{JSON.stringify(diff, null, 2)}</pre>}
    </div>
  );
}
