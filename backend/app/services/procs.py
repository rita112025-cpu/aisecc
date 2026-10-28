"""Safe subprocess execution: no shell, timeout, cancellation, process-tree kill and memory cap."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import psutil

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
BELOW_NORMAL_PRIORITY_CLASS = getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


@dataclass
class ProcResult:
    argv: list[str]
    exit_code: int | None
    # finished | timeout | cancelled | memory_limit | not_found | error
    outcome: str
    duration_sec: float
    output: str = ""
    error: str | None = None
    peak_memory_mb: float = 0.0


@dataclass
class RunOptions:
    cwd: Path
    timeout_sec: float = 600
    max_memory_mb: int | None = None
    env: dict[str, str] | None = None
    log_path: Path | None = None
    capture_limit: int = 5_000_000
    cancel_event: threading.Event | None = None
    on_output: Callable[[str], None] | None = None
    extra_env: dict[str, str] = field(default_factory=dict)


def resolve_executable(name: str, cwd: Path | None = None) -> str | None:
    """Find an executable on PATH (Windows aware: .exe/.cmd/.bat)."""
    candidate = Path(name)
    if candidate.is_absolute() or len(candidate.parts) > 1:
        full = candidate if candidate.is_absolute() else (cwd or Path.cwd()) / candidate
        if full.exists():
            return str(full)
        if sys.platform == "win32":
            for ext in (".exe", ".cmd", ".bat"):
                if full.with_name(full.name + ext).exists():
                    return str(full.with_name(full.name + ext))
        return None
    return shutil.which(name)


def kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
    except psutil.NoSuchProcess:
        return
    procs = parent.children(recursive=True) + [parent]
    for p in procs:
        try:
            p.kill()
        except psutil.NoSuchProcess:
            pass
    psutil.wait_procs(procs, timeout=5)


def _tree_rss_mb(pid: int) -> float:
    try:
        parent = psutil.Process(pid)
        procs = [parent, *parent.children(recursive=True)]
    except psutil.NoSuchProcess:
        return 0.0
    total = 0
    for p in procs:
        try:
            total += p.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            continue
    return total / (1024 * 1024)


def run_command(argv: Sequence[str], opts: RunOptions) -> ProcResult:
    argv = [str(a) for a in argv]
    exe = resolve_executable(argv[0], opts.cwd)
    start = time.monotonic()
    if exe is None:
        return ProcResult(argv, None, "not_found", 0.0, error=f"找不到執行檔：{argv[0]}")
    argv = [exe, *argv[1:]]
    # .cmd/.bat shims need cmd.exe; arguments are passed as a list, never interpolated into a shell string.
    if sys.platform == "win32" and exe.lower().endswith((".cmd", ".bat")):
        argv = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/s", "/c", *argv]

    env = dict(opts.env if opts.env is not None else os.environ)
    env.update(opts.extra_env)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("NO_COLOR", "1")
    env.setdefault("FORCE_COLOR", "0")

    flags = CREATE_NEW_PROCESS_GROUP | BELOW_NORMAL_PRIORITY_CLASS | CREATE_NO_WINDOW
    try:
        proc = subprocess.Popen(  # noqa: S603 - argv list, shell=False
            argv,
            cwd=str(opts.cwd),
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            creationflags=flags,
        )
    except OSError as exc:
        return ProcResult(argv, None, "error", time.monotonic() - start, error=str(exc))

    chunks: list[str] = []
    captured = 0
    log_fh = opts.log_path.open("a", encoding="utf-8", errors="replace") if opts.log_path else None
    lock = threading.Lock()

    def reader() -> None:
        nonlocal captured
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            text = _decode(raw)
            with lock:
                if captured < opts.capture_limit:
                    chunks.append(text)
                    captured += len(text)
                if log_fh:
                    log_fh.write(text)
                    log_fh.flush()
            if opts.on_output:
                opts.on_output(text)

    t = threading.Thread(target=reader, daemon=True)
    t.start()

    outcome = "finished"
    peak = 0.0
    while True:
        if proc.poll() is not None:
            break
        elapsed = time.monotonic() - start
        if opts.cancel_event is not None and opts.cancel_event.is_set():
            outcome = "cancelled"
        elif elapsed > opts.timeout_sec:
            outcome = "timeout"
        elif opts.max_memory_mb:
            rss = _tree_rss_mb(proc.pid)
            peak = max(peak, rss)
            if rss > opts.max_memory_mb:
                outcome = "memory_limit"
        if outcome != "finished":
            kill_tree(proc.pid)
            break
        time.sleep(0.2)

    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        kill_tree(proc.pid)
    t.join(timeout=5)
    if log_fh:
        log_fh.close()
    duration = time.monotonic() - start
    error = {
        "timeout": f"執行逾時（超過 {opts.timeout_sec:.0f} 秒），已終止程序樹",
        "cancelled": "使用者取消，已終止程序樹",
        "memory_limit": f"記憶體超過上限 {opts.max_memory_mb} MB，已終止程序樹",
    }.get(outcome)
    return ProcResult(argv, proc.returncode, outcome, duration, "".join(chunks), error, peak)


def _decode(raw: bytes) -> str:
    for enc in ("utf-8", "cp950", "mbcs"):
        try:
            return raw.decode(enc)
        except (UnicodeDecodeError, LookupError):
            continue
    return raw.decode("utf-8", errors="replace")
