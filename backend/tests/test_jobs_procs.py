from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from app.config import Settings
from app.db import read_session, write_session
from app.models import Job
from app.services.jobs import DuplicateJobError, JobContext, JobManager, read_log
from app.services.procs import RunOptions, run_command


@pytest.fixture
def manager(db: Path, settings: Settings):
    m = JobManager(settings.logs_dir, max_workers=2)
    yield m
    m.shutdown()


def _status(job_id: str) -> Job:
    with read_session() as s:
        return s.get(Job, job_id)


def test_job_success_records_result(manager: JobManager) -> None:
    def fn(ctx: JobContext):
        ctx.progress(0.5, "半途")
        ctx.log("hello 中文")
        return {"ok": True}

    jid = manager.submit("demo", fn)
    manager.wait(jid, timeout=10)
    job = _status(jid)
    assert job.status == "succeeded" and job.result == {"ok": True} and job.progress == 1.0
    text, _ = read_log(job.log_path)
    assert "hello 中文" in text


def test_job_failure_is_recorded(manager: JobManager) -> None:
    def fn(ctx: JobContext):
        raise ValueError("boom")

    jid = manager.submit("demo", fn)
    manager.wait(jid, timeout=10)
    job = _status(jid)
    assert job.status == "failed" and "boom" in job.error


def test_job_cancel_running(manager: JobManager) -> None:
    started = threading.Event()

    def fn(ctx: JobContext):
        started.set()
        for _ in range(200):
            ctx.check_cancelled()
            time.sleep(0.05)
        return {}

    jid = manager.submit("demo", fn)
    assert started.wait(5)
    assert manager.cancel(jid) is True
    manager.wait(jid, timeout=10)
    assert _status(jid).status == "cancelled"


def test_cancel_finished_job_returns_false(manager: JobManager) -> None:
    jid = manager.submit("demo", lambda ctx: {})
    manager.wait(jid, timeout=10)
    assert manager.cancel(jid) is False


def test_duplicate_active_job_rejected(manager: JobManager) -> None:
    gate = threading.Event()

    def fn(ctx: JobContext):
        gate.wait(5)
        return {}

    jid = manager.submit("scan", fn, dedupe_key="scan:1")
    with pytest.raises(DuplicateJobError) as exc:
        manager.submit("scan", fn, dedupe_key="scan:1")
    assert exc.value.existing_id == jid
    gate.set()
    manager.wait(jid, timeout=10)
    # once finished, the same key may run again
    jid2 = manager.submit("scan", lambda ctx: {}, dedupe_key="scan:1")
    manager.wait(jid2, timeout=10)


def test_recover_after_restart_marks_interrupted(db: Path, settings: Settings) -> None:
    with write_session() as s:
        s.add(Job(id="stale-1", type="scan", status="running"))
        s.add(Job(id="stale-2", type="scan", status="queued"))
        s.add(Job(id="done-1", type="scan", status="succeeded"))
    m = JobManager(settings.logs_dir)
    try:
        assert m.recover_after_restart() == 2
    finally:
        m.shutdown()
    assert _status("stale-1").status == "interrupted"
    assert _status("stale-2").status == "interrupted"
    assert _status("done-1").status == "succeeded"
    from app.models import AuditLog

    with read_session() as s:
        logs = s.query(AuditLog).filter(AuditLog.action == "job.interrupted").all()
    assert {log.target_id for log in logs} == {"stale-1", "stale-2"}


def test_recover_kills_orphan_process_tree(db: Path, settings: Settings) -> None:
    import subprocess

    orphan = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        created = psutil.Process(orphan.pid).create_time()
        with write_session() as s:
            s.add(Job(id="orphan", type="test_run", status="running", pid=orphan.pid, pid_started_at=created))
            # pid re-use guard: wrong create_time must not kill the process
            s.add(Job(id="reused", type="test_run", status="running", pid=os.getpid(), pid_started_at=1.0))
        m = JobManager(settings.logs_dir)
        try:
            m.recover_after_restart()
        finally:
            m.shutdown()
        orphan.wait(timeout=10)
        assert orphan.returncode is not None
    finally:
        if orphan.poll() is None:
            orphan.kill()


def test_run_command_reports_pid(tmp_path: Path) -> None:
    seen: list[tuple[int, float]] = []
    run_command([sys.executable, "-c", "pass"],
                RunOptions(cwd=tmp_path, on_start=lambda pid, ct: seen.append((pid, ct))))
    assert len(seen) == 1 and seen[0][0] > 0


def test_read_log_offset(tmp_path: Path) -> None:
    p = tmp_path / "x.log"
    p.write_text("abcdef", encoding="utf-8")
    text, off = read_log(str(p), 2)
    assert text == "cdef" and off == 6
    assert read_log(str(tmp_path / "missing.log")) == ("", 0)


def test_run_command_captures_output(tmp_path: Path) -> None:
    r = run_command([sys.executable, "-c", "print('你好')"], RunOptions(cwd=tmp_path, timeout_sec=30))
    assert r.outcome == "finished" and r.exit_code == 0 and "你好" in r.output


def test_run_command_missing_executable(tmp_path: Path) -> None:
    r = run_command(["definitely-not-a-real-tool-xyz"], RunOptions(cwd=tmp_path))
    assert r.outcome == "not_found" and r.exit_code is None


def _spawn_tree_script() -> str:
    return (
        "import subprocess, sys, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
        "print(child.pid, flush=True)\n"
        "time.sleep(60)\n"
    )


def test_run_command_timeout_kills_process_tree(tmp_path: Path) -> None:
    r = run_command([sys.executable, "-c", _spawn_tree_script()], RunOptions(cwd=tmp_path, timeout_sec=3))
    assert r.outcome == "timeout"
    child_pid = int(r.output.split()[0])
    time.sleep(0.5)
    assert not psutil.pid_exists(child_pid) or psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE


def test_run_command_cancel(tmp_path: Path) -> None:
    ev = threading.Event()
    threading.Timer(1.0, ev.set).start()
    r = run_command([sys.executable, "-c", "import time; time.sleep(60)"],
                    RunOptions(cwd=tmp_path, timeout_sec=60, cancel_event=ev))
    assert r.outcome == "cancelled" and r.duration_sec < 30


def test_run_command_memory_limit(tmp_path: Path) -> None:
    script = "import time\nx = bytearray(400 * 1024 * 1024)\ntime.sleep(30)\n"
    r = run_command([sys.executable, "-c", script], RunOptions(cwd=tmp_path, timeout_sec=60, max_memory_mb=150))
    assert r.outcome == "memory_limit"


def test_run_command_cwd_with_chinese_and_spaces(tmp_path: Path) -> None:
    d = tmp_path / "中文 目錄"
    d.mkdir()
    r = run_command([sys.executable, "-c", "import os; print(os.getcwd())"], RunOptions(cwd=d))
    assert "中文 目錄" in r.output
