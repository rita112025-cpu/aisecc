import { useEffect, useRef, useState } from "react";
import { api, qs } from "../api";
import { useAction, useInterval } from "../hooks";
import { label } from "../labels";
import type { Job } from "../types";
import { ErrorBox, Progress, StatusBadge } from "./common";

const ACTIVE = ["queued", "running"];

/** Live view of one background job: status, progress, streaming log and cancel. State comes from
 * the backend, so a page refresh simply re-attaches to the same job. */
export function JobPanel({ jobId, onDone, showLog = true }: { jobId: string; onDone?: (job: Job) => void; showLog?: boolean }) {
  const [job, setJob] = useState<Job>();
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const offset = useRef(0);
  const doneFired = useRef(false);
  const cancel = useAction();
  const active = !job || ACTIVE.includes(job.status);

  useEffect(() => {
    offset.current = 0;
    doneFired.current = false;
    setLog("");
    setJob(undefined);
  }, [jobId]);

  const poll = async () => {
    try {
      const j = await api.get<Job>(`/jobs/${jobId}`);
      setJob(j);
      if (showLog) {
        const chunk = await api.get<{ text: string; next_offset: number }>(
          `/jobs/${jobId}/log${qs({ offset: offset.current })}`);
        if (chunk.text) {
          offset.current = chunk.next_offset;
          setLog((prev) => (prev + chunk.text).slice(-200_000));
        }
      }
      setError(null);
      if (!ACTIVE.includes(j.status) && !doneFired.current) {
        doneFired.current = true;
        onDone?.(j);
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  };

  const pollRef = useRef(poll);
  pollRef.current = poll;
  useEffect(() => {
    void pollRef.current();
  }, [jobId]);
  useInterval(() => void poll(), active ? 1000 : null);

  return (
    <div className="panel" data-testid="job-panel">
      <div className="toolbar">
        <strong>{job ? label.jobType(job.type) : "任務"}</strong>
        {job && <StatusBadge status={job.status} />}
        <span className="muted">{job?.message}</span>
        <span className="spacer" />
        {active && (
          <button className="danger" disabled={cancel.busy || job?.cancel_requested}
            onClick={() => cancel.run(() => api.post(`/jobs/${jobId}/cancel`)).then(() => poll())}>
            {job?.cancel_requested ? "取消中…" : "取消任務"}
          </button>
        )}
      </div>
      {job && <Progress value={job.progress} />}
      {job?.error && <p className="muted">原因：{job.error}</p>}
      {(error || cancel.error) && <ErrorBox error={(error || cancel.error) as string} />}
      {showLog && <pre className="log" aria-label="執行日誌">{log || "（尚無輸出）"}</pre>}
    </div>
  );
}

/** Active jobs for a project/type, so pages can re-attach after a refresh. */
export function useActiveJob(projectId: number | undefined, type: string, refreshKey = 0): [string | null, (id: string | null) => void] {
  const [jobId, setJobId] = useState<string | null>(null);
  useEffect(() => {
    if (!projectId) return;
    let alive = true;
    api.get<Job[]>(`/jobs${qs({ project_id: projectId, type, status: "queued,running", limit: 1 })}`)
      .then((jobs) => alive && setJobId(jobs[0]?.id ?? null))
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [projectId, type, refreshKey]);
  return [jobId, setJobId];
}
