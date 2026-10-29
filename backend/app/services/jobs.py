"""Background job manager backed by the `jobs` table.

Job state lives in SQLite so it survives page refreshes. Jobs that were running
when the backend stopped are marked `interrupted` at startup (never re-run silently).
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db import DatabaseBusyError, read_session, write_session
from app.models import Job, utcnow
from app.services import audit
from app.services.procs import kill_orphan_tree

log = logging.getLogger(__name__)

ACTIVE_STATUSES = ("queued", "running")
FINAL_WRITE_ATTEMPTS = 3
FINAL_WRITE_RETRY_SEC = 1.0
FINAL_STATUSES = ("succeeded", "failed", "cancelled", "timeout", "interrupted")


class JobCancelled(Exception):
    """Raised inside a job when the user requested cancellation."""


class JobTimeout(Exception):
    """Raised inside a job when a sub-step exceeded its time limit."""


class DuplicateJobError(Exception):
    def __init__(self, existing_id: str) -> None:
        super().__init__(f"相同任務已在執行中：{existing_id}")
        self.existing_id = existing_id


@dataclass
class JobContext:
    job_id: str
    cancel_event: threading.Event
    log_path: Path

    def check_cancelled(self) -> None:
        if self.cancel_event.is_set():
            raise JobCancelled()

    def progress(self, fraction: float, message: str = "") -> None:
        self.check_cancelled()
        with write_session() as s:
            job = s.get(Job, self.job_id)
            if job is not None:
                job.progress = max(0.0, min(1.0, fraction))
                job.heartbeat_at = utcnow()
                if message:
                    job.message = message

    def register_process(self, pid: int, create_time: float) -> None:
        with write_session() as s:
            job = s.get(Job, self.job_id)
            if job is not None:
                job.pid = pid
                job.pid_started_at = create_time
                job.heartbeat_at = utcnow()

    def log(self, text: str) -> None:
        with self.log_path.open("a", encoding="utf-8", errors="replace") as fh:
            fh.write(text if text.endswith("\n") else text + "\n")


JobFn = Callable[[JobContext], dict[str, Any] | None]


class JobManager:
    def __init__(self, logs_dir: Path, max_workers: int = 2) -> None:
        self.logs_dir = logs_dir
        self._executor = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="job")
        self._events: dict[str, threading.Event] = {}
        self._futures: dict[str, Future[None]] = {}
        self._lock = threading.Lock()

    def recover_after_restart(self) -> int:
        """Mark jobs left active by a previous process as interrupted and kill orphan process trees.

        Queued jobs are also marked interrupted: their in-memory callables are gone, and
        re-running side-effectful work (tests) silently would surprise the user.
        """
        # Kill orphans outside the write transaction: kill_tree may wait seconds per job.
        with read_session() as s:
            stale_procs = [(j.id, j.pid, j.pid_started_at)
                           for j in s.query(Job).filter(Job.status.in_(ACTIVE_STATUSES)).all()]
        killed_ids = {jid for jid, pid, started in stale_procs if pid and kill_orphan_tree(pid, started)}
        with write_session() as s:
            stale = s.query(Job).filter(Job.status.in_(ACTIVE_STATUSES)).all()
            for job in stale:
                killed = job.id in killed_ids
                previous = job.status
                job.status = "interrupted"
                job.error = "後端重新啟動時任務尚未完成，已標記為中斷（未自動重跑）"
                job.finished_at = utcnow()
                audit.record(
                    "job.interrupted", target_type="job", target_id=job.id, project_id=job.project_id,
                    detail={"previous_status": previous, "orphan_killed": killed}, session=s,
                )
            return len(stale)

    def submit(
        self,
        job_type: str,
        fn: JobFn,
        *,
        project_id: int | None = None,
        params: dict[str, Any] | None = None,
        dedupe_key: str | None = None,
    ) -> str:
        job_id = str(uuid.uuid4())
        log_path = self.logs_dir / f"{job_id}.log"
        with write_session() as s:
            if dedupe_key:
                existing = (
                    s.query(Job)
                    .filter(Job.dedupe_key == dedupe_key, Job.status.in_(ACTIVE_STATUSES))
                    .first()
                )
                if existing is not None:
                    raise DuplicateJobError(existing.id)
            s.add(
                Job(
                    id=job_id,
                    type=job_type,
                    project_id=project_id,
                    params=params or {},
                    dedupe_key=dedupe_key,
                    log_path=str(log_path),
                    status="queued",
                )
            )
            audit.record(
                "job.submit", target_type="job", target_id=job_id, project_id=project_id,
                detail={"type": job_type, "params": params or {}}, session=s,
            )
        log_path.parent.mkdir(parents=True, exist_ok=True)
        log_path.touch()
        event = threading.Event()
        with self._lock:
            self._events[job_id] = event
            self._futures[job_id] = self._executor.submit(self._run, job_id, fn, event, log_path)
        return job_id

    def _run(self, job_id: str, fn: JobFn, event: threading.Event, log_path: Path) -> None:
        try:
            self._execute(job_id, fn, event, log_path)
        except Exception:  # noqa: BLE001 - worker thread boundary; state is fixed up at next restart
            log.exception("job %s bookkeeping failed", job_id)
        finally:
            with self._lock:
                self._events.pop(job_id, None)
                self._futures.pop(job_id, None)

    def _execute(self, job_id: str, fn: JobFn, event: threading.Event, log_path: Path) -> None:
        ctx = JobContext(job_id, event, log_path)
        with write_session() as s:
            job = s.get(Job, job_id)
            if job is None or job.status != "queued":
                return  # cancelled while queued (cancel() already wrote the final state)
            job.status = "running"
            job.started_at = utcnow()
        status, result, error = "succeeded", None, None
        try:
            result = fn(ctx)
            if event.is_set():
                status = "cancelled"
        except JobCancelled:
            status, error = "cancelled", "使用者已取消"
        except JobTimeout as exc:
            status, error = "timeout", str(exc) or "任務逾時"
        except Exception as exc:  # noqa: BLE001 - job boundary must record any failure
            log.exception("job %s failed", job_id)
            status, error = "failed", f"{type(exc).__name__}: {exc}"
            ctx.log(f"[ERROR] {error}")
        self._write_final(job_id, status, result, error)

    def _write_final(self, job_id: str, status: str, result: dict[str, Any] | None, error: str | None) -> None:
        """Persist the final state, retrying briefly so a transient lock does not leave it 'running'."""
        for attempt in range(FINAL_WRITE_ATTEMPTS):
            try:
                with write_session() as s:
                    job = s.get(Job, job_id)
                    if job is not None:
                        job.status = status
                        job.result = result
                        job.error = error
                        job.finished_at = utcnow()
                        if status == "succeeded":
                            job.progress = 1.0
                return
            except DatabaseBusyError:
                if attempt == FINAL_WRITE_ATTEMPTS - 1:
                    log.error("job %s: could not persist final status %s; recovery will mark it", job_id, status)
                    raise
                time.sleep(FINAL_WRITE_RETRY_SEC)

    def cancel(self, job_id: str) -> bool:
        with write_session() as s:
            job = s.get(Job, job_id)
            if job is None or job.status not in ACTIVE_STATUSES:
                return False
            job.cancel_requested = True
            if job.status == "queued":
                job.status = "cancelled"
                job.finished_at = utcnow()
                job.error = "使用者已取消"
            audit.record("job.cancel", target_type="job", target_id=job_id, project_id=job.project_id, session=s)
        with self._lock:
            event = self._events.get(job_id)
        if event is not None:
            event.set()
        return True

    def wait(self, job_id: str, timeout: float | None = None) -> None:
        with self._lock:
            fut = self._futures.get(job_id)
        if fut is not None:
            fut.result(timeout=timeout)

    def is_active(self, job_id: str) -> bool:
        with read_session() as s:
            job = s.get(Job, job_id)
            return job is not None and job.status in ACTIVE_STATUSES

    def shutdown(self) -> None:
        with self._lock:
            events = list(self._events.values())
        for e in events:
            e.set()
        self._executor.shutdown(wait=True, cancel_futures=True)


def read_log(log_path: str | None, offset: int = 0, limit: int = 200_000) -> tuple[str, int]:
    if not log_path:
        return "", 0
    p = Path(log_path)
    if not p.exists():
        return "", 0
    with p.open("rb") as fh:
        fh.seek(max(0, offset))
        data = fh.read(max(1, limit))
        if len(data) == max(1, limit):
            # Never split a multi-byte UTF-8 character across chunks: stop at the last complete one,
            # reading up to 3 more bytes when the chunk is smaller than a single character.
            data += fh.read(3)
            cut = _utf8_boundary(data, max(1, limit))
            data = data[:cut]
    return data.decode("utf-8", errors="replace"), max(0, offset) + len(data)


def _utf8_boundary(data: bytes, wanted: int) -> int:
    """Largest index <= max(wanted, first char end) that does not cut a UTF-8 sequence."""
    def char_len(b: int) -> int:
        return 1 if b < 0x80 else 2 if b >> 5 == 0b110 else 3 if b >> 4 == 0b1110 else 4 if b >> 3 == 0b11110 else 1

    i = 0
    last_ok = 0
    while i < len(data):
        n = char_len(data[i])
        if i + n > len(data):
            break
        if i + n > wanted and last_ok > 0:
            break
        i += n
        last_ok = i
        if i >= wanted:
            break
    return last_ok or len(data)
