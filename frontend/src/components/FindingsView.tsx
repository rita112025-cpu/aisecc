import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, qs } from "../api";
import { useAction, useAsync } from "../hooks";
import { label, SEVERITIES } from "../labels";
import type { Finding, Page } from "../types";
import { AsyncView, ErrorBox, StatusBadge } from "./common";

const TRIAGE = ["open", "confirmed", "false_positive", "accepted_risk", "fixed"];
const PAGE = 50;

/** Findings of one scan with server-side search/filter/sort/paging and triage. */
export function FindingsView({ scanId, projectId, categories }: { scanId: number; projectId: number; categories: string[] }) {
  const [q, setQ] = useState("");
  const [severity, setSeverity] = useState("");
  const [category, setCategory] = useState("");
  const [triage, setTriage] = useState("open,confirmed");
  const [sort, setSort] = useState("severity");
  const [offset, setOffset] = useState(0);
  const [selected, setSelected] = useState<Finding | null>(null);
  const cat = category || categories.join(",");
  const state = useAsync(() => api.get<Page<Finding>>(`/findings${qs({
    scan_id: scanId, q, severity, category: cat, triage, sort, offset, limit: PAGE,
  })}`), [scanId, q, severity, cat, triage, sort, offset]);

  const reset = <T,>(set: (v: T) => void) => (v: T) => { set(v); setOffset(0); };

  return (
    <div className="panel">
      <div className="toolbar">
        <input aria-label="搜尋發現" placeholder="搜尋訊息、檔案、規則" value={q} onChange={(e) => reset(setQ)(e.target.value)} />
        <select aria-label="嚴重度" value={severity} onChange={(e) => reset(setSeverity)(e.target.value)}>
          <option value="">全部嚴重度</option>
          {SEVERITIES.map((s) => <option key={s} value={s}>{label.severity(s)}</option>)}
        </select>
        <select aria-label="類別" value={category} onChange={(e) => reset(setCategory)(e.target.value)}>
          <option value="">全部類別</option>
          {categories.map((c) => <option key={c} value={c}>{label.category(c)}</option>)}
        </select>
        <select aria-label="判定狀態" value={triage} onChange={(e) => reset(setTriage)(e.target.value)}>
          <option value="open,confirmed">未處理／已確認</option>
          <option value="">全部</option>
          {TRIAGE.map((t) => <option key={t} value={t}>{label.status(t)}</option>)}
        </select>
        <select aria-label="排序" value={sort} onChange={(e) => setSort(e.target.value)}>
          <option value="severity">依嚴重度</option><option value="file">依檔案</option>
          <option value="tool">依工具</option><option value="category">依類別</option>
        </select>
      </div>
      <p className="muted">靜態分析結果為「疑似問題」，需人工確認；「啟發式」代表系統以樣式推測。</p>
      <AsyncView state={state} isEmpty={(d) => d.total === 0} empty="沒有符合條件的問題">
        {(d) => (
          <>
            <table>
              <thead><tr><th>嚴重度</th><th>類別</th><th>工具／規則</th><th>位置</th><th>訊息</th><th>判定</th></tr></thead>
              <tbody>{d.items.map((f) => (
                <tr key={f.id} className={`clickable ${selected?.id === f.id ? "selected" : ""}`} onClick={() => setSelected(f)}>
                  <td><StatusBadge status={f.severity} kind="severity" /></td>
                  <td>{label.category(f.category)}{f.confidence === "heuristic" && <span className="muted">（啟發式）</span>}</td>
                  <td>{f.tool}<br /><code>{f.rule_id}</code></td>
                  <td className="mono">{f.file_path ?? "—"}{f.line ? `:${f.line}` : ""}</td>
                  <td>{f.message}</td>
                  <td><StatusBadge status={f.triage_status} /></td>
                </tr>
              ))}</tbody>
            </table>
            <div className="pager">
              <span className="muted">共 {d.total} 筆，第 {offset + 1}–{Math.min(offset + PAGE, d.total)} 筆</span>
              <button disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>上一頁</button>
              <button disabled={offset + PAGE >= d.total} onClick={() => setOffset(offset + PAGE)}>下一頁</button>
            </div>
          </>
        )}
      </AsyncView>
      {selected && <TriagePanel finding={selected} projectId={projectId} onSaved={(f) => { setSelected(f); state.reload(); }} />}
    </div>
  );
}

function TriagePanel({ finding, projectId, onSaved }: { finding: Finding; projectId: number; onSaved: (f: Finding) => void }) {
  const [status, setStatus] = useState(finding.triage_status);
  const [note, setNote] = useState(finding.triage_note ?? "");
  const save = useAction();
  const navigate = useNavigate();
  const needsNote = status === "false_positive" || status === "accepted_risk";
  return (
    <div className="panel" style={{ marginTop: 12 }} aria-label="判定">
      <h3>判定 #{finding.id}：{finding.rule_id}</h3>
      <p className="mono">{finding.file_path}{finding.line ? `:${finding.line}` : ""}</p>
      <div className="toolbar">
        <select aria-label="判定結果" value={status} onChange={(e) => setStatus(e.target.value)}>
          {TRIAGE.map((t) => <option key={t} value={t}>{label.status(t)}</option>)}
        </select>
        <input aria-label="判定理由" style={{ flex: 1 }} placeholder={needsNote ? "必填：說明理由" : "備註（選填）"}
          value={note} onChange={(e) => setNote(e.target.value)} />
        <button className="primary" disabled={save.busy || (needsNote && !note.trim())} onClick={async () => {
          const f = await save.run(() => api.post<Finding>(`/findings/${finding.id}/triage`, { status, note: note || null }));
          if (f) onSaved(f);
        }}>儲存判定</button>
        {finding.file_path && (
          <button onClick={() => navigate(`/repairs?project=${projectId}&finding=${finding.id}`)}>請 AI 提出修復</button>
        )}
      </div>
      <p className="muted">標記為誤報或接受風險後，之後的掃描會依指紋自動沿用此判定。</p>
      {save.error && <ErrorBox error={save.error} />}
    </div>
  );
}
