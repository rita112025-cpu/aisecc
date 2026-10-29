import { useEffect, useRef, useState } from "react";
import { api, ApiError, qs } from "../api";
import { useAction } from "../hooks";
import { label } from "../labels";
import type { Job } from "../types";
import { ErrorBox, Progress, StatusBadge } from "./common";

const ACTIVE = ["queued", "running"];
const BASE_DELAY = 1000;
const MAX_DELAY = 10_000;

/** Live view of one background job: status, progress, streaming log and cancel. State comes from
 * the backend, so a page refresh simply re-attaches to the same job (the log is re-read from 0).
 * Polling pauses while the tab is hidden, stops on 4xx (e.g. the job was deleted) and backs off
 * exponentially on 5xx / network errors. */
export function JobPanel({ jobId, onDone, showLog = true }: { jobId: string; onDone?: (job: Job) => void; showLog?: boolean }) {
  const [job, setJob] = useState<Job>();
  const [log, setLog] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [stopped, setStopped] = useState(false);
  const offset = useRef(0);
  const doneFired = useRef(false);
  const onDoneRef = useRef(onDone);
  onDoneRef.current = onDone;
  const cancel = useAction();
  const [kick, setKick] = useState(0);
  const active = !stopped && (!job || ACTIVE.includes(job.status));

  useEffect(() => {
    offset.current = 0;
    doneFired.current = false;
    setLog("");
    setJob(undefined);
    setStopped(false);
    setError(null);
  }, [jobId]);

  useEffect(() => {
    let alive = true;
    let timer: number | undefined;
    let delay = BASE_DELAY;

    const poll = async (): Promise<boolean> => {
      const j = await api.get<Job>(`/jobs/${jobId}`);
      if (!alive) return false;
      setJob(j);
      if (showLog) {
        // Drain the log: several chunks may be pending after a refresh.
        for (let i = 0; i < 20; i++) {
          const chunk = await api.get<{ text: string; next_offset: number }>(
            `/jobs/${jobId}/log${qs({ offset: offset.current })}`);
          if (!alive || !chunk.text) break;
          offset.current = chunk.next_offset;
          setLog((prev) => (prev + chunk.text).slice(-200_000));
        }
      }
      setError(null);
      if (!ACTIVE.includes(j.status)) {
        if (!doneFired.current) {
          doneFired.current = true;
          onDoneRef.current?.(j);
        }
        return false;
      }
      return true;
    };

    const tick = async () => {
      if (!alive) return;
      if (document.visibilityState === "hidden") {
        timer = window.setTimeout(tick, BASE_DELAY);
        return;
      }
      try {
        const again = await poll();
        delay = BASE_DELAY;
        if (again && alive) timer = window.setTimeout(tick, delay);
      } catch (e) {
        if (!alive) return;
        const msg = e instanceof Error ? e.message : String(e);
        if (e instanceof ApiError && e.status >= 400 && e.status < 500) {
          setError(`${msg}（已停止更新）`);
          setStopped(true);
          return;
        }
        setError(`${msg}（${Math.round(delay / 1000)} 秒後重試）`);
        timer = window.setTimeout(tick, delay);
        delay = Math.min(delay * 2, MAX_DELAY);
      }
    };

    void tick();
    return () => {
      alive = false;
      window.clearTimeout(timer);
    };
  }, [jobId, showLog, kick]);

  return (
    <div className="panel" data-testid="job-panel">
      <div className="toolbar">
        <strong>{job ? label.jobType(job.type) : "任務"}</strong>
        {job && <StatusBadge status={job.status} />}
        <span className="muted">{job?.message}</span>
        <span className="spacer" />
        {active && (
          <button className="danger" disabled={cancel.busy || job?.cancel_requested}
            onClick={() => cancel.run(() => api.post(`/jobs/${jobId}/cancel`)).then(() => setKick((k) => k + 1))}>
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
