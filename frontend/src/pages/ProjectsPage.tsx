import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api";
import { AsyncView, ErrorBox, SortHeader } from "../components/common";
import { useAction, useAsync } from "../hooks";
import { fmtTime } from "../labels";
import type { Project } from "../types";

type SortField = "name" | "health_score" | "last_scan_at";

export function ProjectsPage() {
  const state = useAsync(() => api.get<Project[]>("/projects"), []);
  const [path, setPath] = useState("");
  const [name, setName] = useState("");
  const [query, setQuery] = useState("");
  const [sort, setSort] = useState<{ field: SortField; desc: boolean }>({ field: "name", desc: false });
  const add = useAction();

  const rows = useMemo(() => {
    const q = query.trim().toLowerCase();
    const list = (state.data ?? []).filter((p) => !q || p.name.toLowerCase().includes(q) || p.path.toLowerCase().includes(q));
    return [...list].sort((a, b) => {
      const av = a[sort.field] ?? "";
      const bv = b[sort.field] ?? "";
      const c = av < bv ? -1 : av > bv ? 1 : 0;
      return sort.desc ? -c : c;
    });
  }, [state.data, query, sort]);

  const submit = async () => {
    const ok = await add.run(() => api.post<Project>("/projects", { path, name: name || undefined }));
    if (ok) {
      setPath("");
      setName("");
      state.reload();
    }
  };

  return (
    <>
      <div className="page-title"><h2>專案管理</h2></div>
      <div className="panel">
        <h3>新增本機專案</h3>
        <div className="toolbar">
          <input aria-label="專案路徑" placeholder="例如 D:\code\my-app（絕對路徑）" value={path}
            onChange={(e) => setPath(e.target.value)} style={{ flex: 1, minWidth: 320 }} />
          <input aria-label="顯示名稱" placeholder="顯示名稱（選填）" value={name} onChange={(e) => setName(e.target.value)} />
          <button className="primary" disabled={!path.trim() || add.busy} onClick={submit}>
            {add.busy ? "新增中…" : "新增專案"}
          </button>
        </div>
        <p className="muted">只會讀取檔案進行掃描；執行測試、專案工具或網路稽核都需要另外授權。</p>
        {add.error && <ErrorBox error={add.error} />}
      </div>
      <div className="panel">
        <div className="toolbar">
          <input aria-label="搜尋專案" placeholder="搜尋名稱或路徑" value={query} onChange={(e) => setQuery(e.target.value)} />
          <span className="spacer" />
          <button onClick={state.reload}>重新整理</button>
        </div>
        <AsyncView state={state} isEmpty={(d) => d.length === 0} empty="尚未加入任何專案">
          {() => rows.length === 0 ? <p className="muted">沒有符合搜尋條件的專案</p> : (
            <table>
              <thead>
                <tr>
                  <SortHeader label="名稱" field="name" sort={sort} onSort={setSort} />
                  <th>路徑</th><th>語言</th><th>Git</th>
                  <SortHeader label="健康度" field="health_score" sort={sort} onSort={setSort} />
                  <SortHeader label="最近掃描" field="last_scan_at" sort={sort} onSort={setSort} />
                </tr>
              </thead>
              <tbody>
                {rows.map((p) => (
                  <tr key={p.id}>
                    <td><Link to={`/projects/${p.id}`}>{p.name}</Link></td>
                    <td className="mono">{p.path}{!p.path_exists && <span className="badge bad" style={{ marginLeft: 6 }}>路徑不存在</span>}</td>
                    <td>{p.languages.join(", ") || "—"}</td>
                    <td>{p.is_git ? "是" : <span className="muted">否（AI 修復不可用）</span>}</td>
                    <td>{p.health_score ?? "—"}</td>
                    <td className="nowrap">{fmtTime(p.last_scan_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </AsyncView>
      </div>
    </>
  );
}
