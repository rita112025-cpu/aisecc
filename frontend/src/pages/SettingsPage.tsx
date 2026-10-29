import { useEffect, useState } from "react";
import { api } from "../api";
import { AsyncView, ErrorBox } from "../components/common";
import { useAction, useAsync } from "../hooks";
import { fmtTime } from "../labels";
import type { AppSettings } from "../types";

type Counts = Record<string, number>;

export function SettingsPage() {
  const settings = useAsync(() => api.get<AppSettings>("/settings"), []);
  return (
    <>
      <div className="page-title"><h2>系統設定</h2></div>
      <AsyncView state={settings}>{(s) => <SettingsForm initial={s} onSaved={settings.reload} />}</AsyncView>
      <BackupPanel />
      <RetentionPanel />
      <ToolsPanel />
    </>
  );
}

function SettingsForm({ initial, onSaved }: { initial: AppSettings; onSaved: () => void }) {
  const [form, setForm] = useState(initial);
  const [saved, setSaved] = useState(false);
  const act = useAction();
  useEffect(() => setForm(initial), [initial]);
  const set = <K extends keyof AppSettings>(k: K, v: AppSettings[K]) => { setForm({ ...form, [k]: v }); setSaved(false); };
  const num = (k: keyof AppSettings, text: string) => (
    <label className="field">{text}<input type="number" aria-label={text} value={form[k] as number}
      onChange={(e) => set(k, Number(e.target.value) as never)} /></label>
  );
  return (
    <div className="panel">
      <h3>AI 模型</h3>
      <div className="grid cols-2">
        <label className="field">提供者
          <select aria-label="AI 提供者" value={form.ai_provider} onChange={(e) => set("ai_provider", e.target.value as AppSettings["ai_provider"])}>
            <option value="none">不使用 AI</option><option value="ollama">本機 Ollama</option><option value="openai_compatible">OpenAI 相容端點</option>
          </select>
        </label>
        {num("ai_timeout_sec", "AI 逾時（秒）")}
        <label className="field">Ollama 位址（僅限本機）<input aria-label="Ollama 位址" value={form.ollama_url} onChange={(e) => set("ollama_url", e.target.value)} /></label>
        <label className="field">Ollama 模型<input aria-label="Ollama 模型" placeholder="例如 qwen2.5-coder:7b" value={form.ollama_model} onChange={(e) => set("ollama_model", e.target.value)} /></label>
        <label className="field">外部 AI 位址（https）<input aria-label="外部 AI 位址" value={form.external_ai_base_url} onChange={(e) => set("external_ai_base_url", e.target.value)} /></label>
        <label className="field">外部 AI 模型<input aria-label="外部 AI 模型" value={form.external_ai_model} onChange={(e) => set("external_ai_model", e.target.value)} /></label>
        <label className="field">API 金鑰環境變數名稱（AISECC_ 開頭；金鑰本身不儲存）<input aria-label="金鑰環境變數" value={form.external_ai_api_key_env} onChange={(e) => set("external_ai_api_key_env", e.target.value)} /></label>
        {num("max_ai_calls_per_proposal", "每次修復最多 AI 呼叫次數（1–3）")}
      </div>
      <label className="check" style={{ marginTop: 8 }}>
        <input type="checkbox" checked={form.allow_external_ai} onChange={(e) => {
          if (e.target.checked && !window.confirm("開啟後，專案程式碼片段（已遮罩機密）會送往外部 AI 服務。確定？")) return;
          set("allow_external_ai", e.target.checked);
        }} />允許將程式碼送往外部 AI 服務
      </label>
      <h3 style={{ marginTop: 16 }}>掃描與保存</h3>
      <div className="grid cols-2">
        {num("retention_days", "執行紀錄保存天數")}
        {num("audit_retention_days", "一般稽核紀錄保存天數（≥365；批准與變更紀錄永久保存）")}
        {num("scan_max_files", "單次掃描最多檔案數")}
        {num("scan_max_file_bytes", "單檔分析上限（bytes）")}
        {num("tool_timeout_sec", "分析工具逾時（秒）")}
        {num("complexity_threshold", "複雜度門檻")}
        {num("function_length_threshold", "函式長度門檻（行）")}
      </div>
      <div className="toolbar" style={{ marginTop: 12 }}>
        <button className="primary" disabled={act.busy} onClick={async () => {
          if (await act.run(() => api.put("/settings", form))) { setSaved(true); onSaved(); }
        }}>儲存設定</button>
        {saved && <span className="badge ok">已儲存</span>}
      </div>
      {act.error && <ErrorBox error={act.error} />}
    </div>
  );
}

function BackupPanel() {
  const backups = useAsync(() => api.get<{ name: string; size: number; created_at: string }[]>("/system/backups"), []);
  const act = useAction();
  const [msg, setMsg] = useState<string | null>(null);
  return (
    <div className="panel">
      <div className="toolbar"><h3 style={{ margin: 0 }}>資料庫備份與還原</h3><span className="spacer" />
        <button className="primary" disabled={act.busy} onClick={async () => {
          const r = await act.run(() => api.post<{ name: string }>("/system/backup"));
          if (r) { setMsg(`已建立備份 ${r.name}`); backups.reload(); }
        }}>立即備份</button></div>
      {msg && <p className="badge ok">{msg}</p>}
      {act.error && <ErrorBox error={act.error} />}
      <AsyncView state={backups} isEmpty={(d) => d.length === 0} empty="尚無備份">
        {(list) => (
          <table>
            <thead><tr><th>檔名</th><th>大小</th><th>時間</th><th></th></tr></thead>
            <tbody>{list.map((b) => (
              <tr key={b.name}><td className="mono">{b.name}</td><td>{(b.size / 1024).toFixed(0)} KB</td><td>{fmtTime(b.created_at)}</td>
                <td><button className="danger" disabled={act.busy} onClick={async () => {
                  if (!window.confirm(`以 ${b.name} 取代目前資料庫？還原前會自動備份目前資料。執行中的任務必須先結束。`)) return;
                  const r = await act.run(() => api.post<{ safety_backup: string }>("/system/restore", { name: b.name, confirm: true }));
                  if (r) { setMsg(`已還原；還原前的資料備份為 ${r.safety_backup}`); backups.reload(); }
                }}>還原</button></td></tr>
            ))}</tbody>
          </table>
        )}
      </AsyncView>
    </div>
  );
}

function RetentionPanel() {
  const [preview, setPreview] = useState<Counts | null>(null);
  const [done, setDone] = useState<Counts | null>(null);
  const act = useAction();
  const fmt = (c: Counts) => `任務 ${c.jobs}、掃描 ${c.scans}、測試 ${c.test_runs}、修復 ${c.repairs}、稽核 ${c.audit_logs}、檔案/資料夾 ${c.files}`;
  return (
    <div className="panel">
      <h3>資料保存期限清理</h3>
      <div className="toolbar">
        <button disabled={act.busy} onClick={async () => {
          const r = await act.run(() => api.post<Counts>("/system/retention", { confirm: true, dry_run: true }));
          if (r) { setPreview(r); setDone(null); }
        }}>試算將刪除的資料</button>
        {preview && (
          <button className="danger" disabled={act.busy} onClick={async () => {
            if (!window.confirm(`將刪除：${fmt(preview)}。確定？`)) return;
            const r = await act.run(() => api.post<Counts>("/system/retention", { confirm: true }));
            if (r) { setDone(r); setPreview(null); }
          }}>確認清理</button>
        )}
      </div>
      {preview && <p>試算結果：{fmt(preview)}</p>}
      {done && <p className="badge ok">已清理：{fmt(done)}</p>}
      {act.error && <ErrorBox error={act.error} />}
    </div>
  );
}

function ToolsPanel() {
  const tools = useAsync(() => api.get<{ version: string; python: string; tools: Record<string, string | null> }>("/system/tools"), []);
  return (
    <div className="panel">
      <h3>工具狀態</h3>
      <AsyncView state={tools}>
        {(t) => (
          <table><tbody>
            <tr><td>控制中心版本</td><td>{t.version}</td></tr>
            <tr><td>Python</td><td>{t.python}</td></tr>
            {Object.entries(t.tools).map(([k, v]) => (
              <tr key={k}><td>{k}</td><td>{v ?? <span className="badge warn">未安裝</span>}</td></tr>
            ))}
          </tbody></table>
        )}
      </AsyncView>
    </div>
  );
}
