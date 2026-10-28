"""Regression tests for review findings: command injection, git config execution, path escapes, job state."""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import psutil
import pytest

from app.config import Settings
from app.db import DatabaseBusyError, read_session, write_session
from app.models import Job, Project
from app.services import audit, gitinfo
from app.services import jobs as jobs_mod
from app.services.fswalk import decode_bytes_info, read_text_info, resolve_inside, walk
from app.services.jobs import JobManager
from app.services.procs import RunOptions, run_command
from app.services.settings_store import AppSettings
from tests.conftest import git

win_only = pytest.mark.skipif(sys.platform != "win32", reason="Windows-specific cmd.exe/junction behaviour")


# ---- procs ---------------------------------------------------------------------------------

@win_only
@pytest.mark.parametrize("arg", ["a&echo.INJECTED", "%PATH%", "x|whoami", 'q"&calc'])
def test_bat_rejects_cmd_metacharacters(tmp_path: Path, arg: str) -> None:
    bat = tmp_path / "echoargs.bat"
    bat.write_text("@echo ARGS=%*\r\n", encoding="ascii")
    spawned: list[int] = []
    r = run_command([str(bat), arg], RunOptions(cwd=tmp_path, on_start=lambda pid, _: spawned.append(pid)))
    assert r.outcome == "error" and "INJECTED" not in r.output and spawned == []


@win_only
def test_bat_plain_args_still_work(tmp_path: Path) -> None:
    bat = tmp_path / "echoargs.bat"
    bat.write_text("@echo ARGS=%*\r\n", encoding="ascii")
    r = run_command([str(bat), "hello", "world"], RunOptions(cwd=tmp_path))
    assert r.outcome == "finished" and "ARGS=hello world" in r.output


def test_on_start_failure_kills_child(tmp_path: Path) -> None:
    seen: list[int] = []

    def boom(pid: int, _ct: float) -> None:
        seen.append(pid)
        raise RuntimeError("db busy")

    r = run_command([sys.executable, "-c", "import time; time.sleep(60)"], RunOptions(cwd=tmp_path, on_start=boom))
    assert r.outcome == "error" and "無法記錄程序資訊" in (r.error or "")
    assert seen and not psutil.pid_exists(seen[0])


# ---- db / jobs -----------------------------------------------------------------------------

def test_nested_write_session_fails_fast(db: Path) -> None:
    t0 = time.monotonic()
    with pytest.raises(RuntimeError, match="巢狀"), write_session():
        audit.record("nested")  # opens its own write_session
    assert time.monotonic() - t0 < 0.5


def test_final_status_write_retries(db: Path, settings: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(jobs_mod, "FINAL_WRITE_RETRY_SEC", 0.01)
    m = JobManager(settings.logs_dir)
    original = m._write_final.__func__
    calls = {"n": 0}

    real_ws = jobs_mod.write_session

    def flaky_write_session():
        calls["n"] += 1
        if calls["n"] == 1:
            raise DatabaseBusyError("locked")
        return real_ws()

    def patched(self, *a):
        monkeypatch.setattr(jobs_mod, "write_session", flaky_write_session)
        try:
            return original(self, *a)
        finally:
            monkeypatch.setattr(jobs_mod, "write_session", real_ws)

    monkeypatch.setattr(JobManager, "_write_final", patched)
    try:
        jid = m.submit("demo", lambda ctx: {"ok": 1})
        m.wait(jid, timeout=10)
    finally:
        m.shutdown()
    with read_session() as s:
        assert s.get(Job, jid).status == "succeeded"
    assert calls["n"] == 2


def test_cancel_queued_job_cleans_registry(db: Path, settings: Settings) -> None:
    m = JobManager(settings.logs_dir, max_workers=1)
    gate = threading.Event()
    try:
        blocker = m.submit("block", lambda ctx: gate.wait(5) and {})
        queued = m.submit("queued", lambda ctx: {})
        assert m.cancel(queued) is True
        gate.set()
        m.wait(blocker, timeout=10)
        deadline = time.monotonic() + 5
        while (m._events or m._futures) and time.monotonic() < deadline:
            time.sleep(0.05)
        assert m._events == {} and m._futures == {}
        with read_session() as s:
            assert s.get(Job, queued).status == "cancelled"
    finally:
        m.shutdown()


# ---- settings ------------------------------------------------------------------------------

@pytest.mark.parametrize("patch", [
    {"external_ai_api_key_env": "GITHUB_TOKEN"},
    {"external_ai_api_key_env": "aisecc_lower"},
    {"external_ai_base_url": "http://example.com"},
    {"external_ai_base_url": "ftp://example.com"},
])
def test_ai_settings_reject_exfiltration(patch: dict) -> None:
    with pytest.raises(ValueError):
        AppSettings(**patch)


def test_ai_settings_accept_safe_values() -> None:
    s = AppSettings(external_ai_api_key_env="AISECC_OPENAI_KEY", external_ai_base_url="https://api.example.com/v1")
    assert s.external_ai_base_url.startswith("https://")
    assert AppSettings(external_ai_base_url="http://127.0.0.1:8080").external_ai_base_url


# ---- git config execution ------------------------------------------------------------------

def _marker_script(tmp_path: Path, name: str) -> tuple[Path, Path]:
    marker = tmp_path / f"{name}.marker"
    script = tmp_path / f"{name}.py"
    body = (
        "import pathlib, sys\n"
        f"pathlib.Path(r'{marker}').write_text('ran')\n"
        "sys.stdout.write(sys.stdin.read())\n"
    )
    script.write_text(body, encoding="utf-8")
    cmd = f'"{sys.executable}" "{script}"'.replace("\\", "/")
    return marker, Path(cmd)


def test_fsmonitor_hook_not_executed(git_repo: Path, tmp_path: Path) -> None:
    marker, cmd = _marker_script(tmp_path, "fsmon")
    git(git_repo, "config", "core.fsmonitor", str(cmd))
    (git_repo / "src" / "calc.py").write_text("changed\n", encoding="utf-8")
    info = gitinfo.get_info(git_repo)
    assert info.dirty and not marker.exists()


def test_filter_config_blocks_status(git_repo: Path, tmp_path: Path) -> None:
    marker, cmd = _marker_script(tmp_path, "filter")
    git(git_repo, "config", "filter.x.clean", str(cmd))
    (git_repo / ".gitattributes").write_text("*.py filter=x\n", encoding="utf-8")
    (git_repo / "src" / "calc.py").write_text("changed\n", encoding="utf-8")
    info = gitinfo.get_info(git_repo)
    assert info.blocked_reason and "filter.x.clean" in info.blocked_reason
    assert not marker.exists()
    with pytest.raises(gitinfo.GitUntrustedConfig):
        gitinfo.diff(git_repo)
    assert not marker.exists()


def test_textconv_and_external_diff_not_executed(git_repo: Path, tmp_path: Path) -> None:
    marker, cmd = _marker_script(tmp_path, "textconv")
    git(git_repo, "config", "diff.external", str(cmd))
    (git_repo / "src" / "calc.py").write_text("changed\n", encoding="utf-8")
    d = gitinfo.diff(git_repo)
    assert "+changed" in d["diff"] and not marker.exists()
    git(git_repo, "config", "--unset", "diff.external")
    git(git_repo, "config", "diff.x.textconv", str(cmd))
    with pytest.raises(gitinfo.GitUntrustedConfig):
        gitinfo.diff(git_repo)
    d = gitinfo.diff(git_repo, allow_repo_config=True)  # --no-textconv still applies
    assert not marker.exists()


def test_empty_repo_and_detached_head(tmp_path: Path, git_repo: Path) -> None:
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q", "-b", "main")
    info = gitinfo.get_info(empty)
    assert info.is_repo and info.head is None and info.branch == "main"
    git(git_repo, "checkout", "-q", "--detach", "HEAD")
    info = gitinfo.get_info(git_repo)
    assert info.detached and info.branch is None and info.head


def test_git_missing_from_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PATH", str(tmp_path))
    info = gitinfo.get_info(tmp_path)
    assert info.git_missing and not info.is_repo


# ---- path boundaries -----------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "../x", "C:\\Windows", "C:x", "\\\\?\\C:\\", "\\\\host\\s", "a.txt::$DATA", "a.txt:stream",
    "/etc/passwd", "sub/../../x", "CON", "sub/nul.txt", " lead",
])
def test_resolve_inside_rejects(tmp_path: Path, bad: str) -> None:
    (tmp_path / "sub").mkdir()
    (tmp_path / "a.txt").write_text("x")
    with pytest.raises((ValueError, FileNotFoundError)):
        resolve_inside(tmp_path, bad)


@win_only
def test_resolve_inside_rejects_junction_escape(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("s")
    proj = tmp_path / "proj"
    proj.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(proj / "j"), str(outside)], check=True, capture_output=True)
    with pytest.raises(ValueError):
        resolve_inside(proj, "j/secret.txt")


@win_only
def test_resolve_inside_short_name_inside_ok(tmp_path: Path) -> None:
    d = tmp_path / "Long Directory Name"
    d.mkdir()
    (d / "f.txt").write_text("x")
    import ctypes

    buf = ctypes.create_unicode_buffer(500)
    ctypes.windll.kernel32.GetShortPathNameW(str(d), buf, 500)
    short = Path(buf.value).name
    if short == d.name:
        pytest.skip("8.3 short names disabled on this volume")
    assert resolve_inside(tmp_path, f"{short}/f.txt").name == "f.txt"


def test_resolve_inside_chinese_ok(tmp_path: Path) -> None:
    (tmp_path / "中文 目錄").mkdir()
    (tmp_path / "中文 目錄" / "檔 案.py").write_text("x")
    assert resolve_inside(tmp_path, "中文 目錄/檔 案.py").name == "檔 案.py"


@win_only
def test_deep_path_over_max_path(tmp_path: Path) -> None:
    deep = tmp_path
    for i in range(30):
        deep = deep / f"深層目錄_{i:02d}"
    os.makedirs("\\\\?\\" + str(deep), exist_ok=True)
    with open("\\\\?\\" + str(deep / "x.py"), "w", encoding="utf-8") as fh:
        fh.write("x = 1\n")
    assert len(str(deep)) > 260
    r = walk(tmp_path)
    assert any(f.rel.endswith("x.py") for f in r.files) or r.errors


# ---- encodings -----------------------------------------------------------------------------

def test_decode_encodings_and_binary(tmp_path: Path) -> None:
    assert decode_bytes_info("繁體中文註解".encode("cp950"))[1] == "cp950"
    assert decode_bytes_info(b"\xef\xbb\xbfx = 1")[1] == "utf-8-sig"
    assert decode_bytes_info(b"")[0] == ""
    binf = tmp_path / "b.bin"
    binf.write_bytes(b"abc\x00def")
    with pytest.raises(ValueError):
        read_text_info(binf)


def test_walk_skips_binary_gitignore(tmp_path: Path) -> None:
    (tmp_path / ".gitignore").write_bytes(b"\x00\x01garbage")
    (tmp_path / "a.py").write_text("x")
    assert {f.rel for f in walk(tmp_path).files} == {".gitignore", "a.py"}


# ---- scan cancel & API edge cases ----------------------------------------------------------

def test_inventory_scan_cancel_marks_scan(db: Path, settings: Settings, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch) -> None:
    from app.models import Scan
    from app.services import inventory

    proj = tmp_path / "big"
    for i in range(3000):
        p = proj / f"d{i // 100}" / f"f{i}.txt"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")
    with write_session() as s:
        s.add(Project(name="big", path=str(proj), path_key=str(proj).lower()))
    with read_session() as s:
        pid = s.query(Project).one().id
    m = JobManager(settings.logs_dir)
    started = threading.Event()
    real_walk = inventory.walk

    def slow_walk(*a, **kw):
        started.set()
        time.sleep(0.5)
        return real_walk(*a, **kw)

    monkeypatch.setattr(inventory, "walk", slow_walk)
    try:
        jid = m.submit("inventory_scan", lambda ctx: inventory.run_inventory_scan(pid, ctx))
        assert started.wait(5)
        m.cancel(jid)
        m.wait(jid, timeout=30)
    finally:
        m.shutdown()
    with read_session() as s:
        assert s.get(Job, jid).status == "cancelled"
        assert s.query(Scan).one().status == "cancelled"
