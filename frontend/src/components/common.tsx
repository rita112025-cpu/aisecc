import type { ReactNode } from "react";
import { label, tone } from "../labels";

export function Loading({ text = "載入中…" }: { text?: string }) {
  return <div className="state" role="status">{text}</div>;
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="state">{children}</div>;
}

export function ErrorBox({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="state error" role="alert">
      <strong>發生錯誤：</strong>{error}
      {onRetry && (
        <div style={{ marginTop: 8 }}>
          <button onClick={onRetry}>重試</button>
        </div>
      )}
    </div>
  );
}

/** Renders loading / error / empty states before the content. */
export function AsyncView<T>({
  state, empty, isEmpty, children,
}: {
  state: { data: T | undefined; error: string | null; loading: boolean; reload: () => void };
  empty?: ReactNode;
  isEmpty?: (d: T) => boolean;
  children: (data: T) => ReactNode;
}) {
  if (state.error) return <ErrorBox error={state.error} onRetry={state.reload} />;
  if (state.data === undefined) return <Loading />;
  if (empty && isEmpty?.(state.data)) return <Empty>{empty}</Empty>;
  return <>{children(state.data)}</>;
}

export function StatusBadge({ status, kind = "status" }: { status: string; kind?: "status" | "tool" | "severity" }) {
  const text = kind === "tool" ? label.tool(status) : kind === "severity" ? label.severity(status) : label.status(status);
  return <span className={`badge ${tone(status)}`}>{text}</span>;
}

export function Progress({ value }: { value: number }) {
  return (
    <div className="progress" aria-label="進度" aria-valuenow={Math.round(value * 100)} role="progressbar">
      <div style={{ width: `${Math.round(Math.max(0, Math.min(1, value)) * 100)}%` }} />
    </div>
  );
}

export function SortHeader<K extends string>({
  label: text, field, sort, onSort,
}: { label: string; field: K; sort: { field: K; desc: boolean }; onSort: (s: { field: K; desc: boolean }) => void }) {
  const active = sort.field === field;
  return (
    <th className="sortable" onClick={() => onSort({ field, desc: active ? !sort.desc : false })}>
      {text}{active ? (sort.desc ? " ▼" : " ▲") : ""}
    </th>
  );
}
