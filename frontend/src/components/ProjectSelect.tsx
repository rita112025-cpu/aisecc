import { useSearchParams } from "react-router-dom";
import { api } from "../api";
import { useAsync } from "../hooks";
import type { Project } from "../types";
import { ErrorBox } from "./common";

/** Project picker bound to the `?project=` URL parameter so a refresh keeps the selection. */
export function useSelectedProject(): [number | undefined, (id: number | undefined) => void] {
  const [params, setParams] = useSearchParams();
  const raw = params.get("project");
  const id = raw ? Number(raw) : undefined;
  const set = (v: number | undefined) => {
    const next = new URLSearchParams(params);
    if (v) next.set("project", String(v));
    else next.delete("project");
    setParams(next, { replace: true });
  };
  return [Number.isFinite(id) ? id : undefined, set];
}

export function ProjectSelect({ value, onChange }: { value: number | undefined; onChange: (id: number | undefined) => void }) {
  const projects = useAsync(() => api.get<Project[]>("/projects"), []);
  if (projects.error) return <ErrorBox error={projects.error} onRetry={projects.reload} />;
  return (
    <label className="check">
      專案
      <select aria-label="選擇專案" value={value ?? ""} onChange={(e) => onChange(e.target.value ? Number(e.target.value) : undefined)}>
        <option value="">{projects.loading ? "載入中…" : "— 請選擇 —"}</option>
        {projects.data?.map((p) => (
          <option key={p.id} value={p.id}>{p.name}</option>
        ))}
      </select>
    </label>
  );
}
