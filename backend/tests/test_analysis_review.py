"""Round-3 review regressions: secret leakage, untrusted tool paths, caps, isolated audits, cancellation."""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import read_session
from app.models import Finding, Job, Scan
from app.services.analysis import engine as engine_mod
from app.services.analysis import tools as tools_mod
from app.services.analysis.base import RawFinding
from app.services.analysis.engine import AnalysisOptions, _inside_project, run_analysis
from app.services.analysis.tools import Tool, ToolContext, ToolResult, sanitise_requirements
from app.services.procs import ProcResult
from tests.test_api_projects import client, wait_job  # noqa: F401

SECRET = "sk-live-0123456789abcdefABCDEF"


def _proj(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "proj"
    for rel, text in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return root


def test_secret_never_persisted_anywhere(client: TestClient, tmp_path: Path, settings: Settings) -> None:  # noqa: F811
    root = _proj(tmp_path, {"app/cfg.py": f'API_KEY = "{SECRET}"\npassword = "{SECRET}"\n'})
    pid = client.post("/api/projects", json={"path": str(root)}).json()["id"]
    job = wait_job(client, client.post(f"/api/projects/{pid}/analyze",
                                       json={"tools": ["ruff", "bandit"]}).json()["job_id"], 120)
    assert job["status"] == "succeeded"
    sid = job["result"]["scan_id"]
    with read_session() as s:
        rows = s.query(Finding).filter_by(scan_id=sid).all()
        dumped = json.dumps([{c.name: getattr(r, c.name) for c in Finding.__table__.columns} for r in rows],
                            ensure_ascii=False, default=str)
        log_path = s.get(Job, job["id"]).log_path
    assert {r.tool for r in rows} >= {"builtin-secrets", "bandit"}
    assert SECRET not in dumped
    assert SECRET not in client.get(f"/api/scans/{sid}/report.json").text
    assert SECRET not in client.get(f"/api/scans/{sid}/report.html").text
    assert SECRET not in Path(log_path).read_text(encoding="utf-8")
    for f in settings.data_dir.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".html", ".log"):
            assert SECRET not in f.read_text(encoding="utf-8", errors="ignore"), f


def test_out_of_tree_tool_paths_dropped(tmp_path: Path) -> None:
    root = _proj(tmp_path, {"src/a.ts": "x"})
    fs = [RawFinding("tsc", "TS1", "high", "type", "m", "..\\..\\Windows\\win.ini", 1),
          RawFinding("tsc", "TS1", "high", "type", "m", "C:/Windows/win.ini", 1),
          RawFinding("tsc", "TS1", "high", "type", "m", "src/a.ts", 1)]
    kept, dropped = _inside_project(root, fs)
    assert [f.file_path for f in kept] == ["src/a.ts"] and dropped == 2


def test_engine_records_dropped_count(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _proj(tmp_path, {"a.py": "x = 1\n"})
    fake = Tool("fake", "auto", lambda ctx: True, lambda ctx: ToolResult("ok", [
        RawFinding("fake", "R", "low", "lint", "m", "../../Windows/win.ini", 1),
        RawFinding("fake", "R", "low", "lint", "m", "a.py", 1)]))
    monkeypatch.setattr(engine_mod, "TOOLS", (fake,))
    findings, status, _ = run_analysis(root, AnalysisOptions())
    assert status["fake"]["dropped_out_of_tree"] == 1 and status["fake"]["findings"] == 1
    assert [f.file_path for f in findings if f.tool == "fake"] == ["a.py"]


def test_per_rule_cap(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    lines = "".join(f'password_{i} = "Zq8vR2kLm9Xw{i:05d}"\n' for i in range(2500))
    root = _proj(tmp_path, {"cfg.py": lines})
    monkeypatch.setattr(engine_mod, "TOOLS", ())
    findings, status, _ = run_analysis(root, AnalysisOptions())
    assert sum(1 for f in findings if f.rule_id == "hardcoded-password") == 2000
    assert status["builtin-secrets"]["truncated"] is True


def test_minified_files_skipped(db: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _proj(tmp_path, {"dist2/app.min.js": "var a=1;" * 10, "web/bundle.js": "x=1;" * 2000 + "\n"})
    monkeypatch.setattr(engine_mod, "TOOLS", ())
    _, _, meta = run_analysis(root, AnalysisOptions())
    assert set(meta["sources_skipped"]) == {"dist2/app.min.js", "web/bundle.js"}


def test_npm_audit_isolated_from_project_npmrc(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    if tools_mod._npm_cli() is None:
        pytest.skip("node/npm-cli.js 不存在於本機，無法解析 npm 入口")
    root = _proj(tmp_path, {"package.json": '{"name":"x"}', "package-lock.json": '{"lockfileVersion":3}',
                            ".npmrc": "registry=http://127.0.0.1:9/\n//127.0.0.1:9/:_authToken=${NPM_TOKEN}\n"})
    monkeypatch.setenv("NPM_TOKEN", "user-secret-token")
    monkeypatch.setenv("npm_config_registry", "http://127.0.0.1:9/")
    seen: dict = {}

    def fake_run(argv, opts):  # noqa: ANN001
        seen.update(argv=list(argv), cwd=Path(opts.cwd), env=dict(opts.env or {}),
                    files=sorted(p.name for p in Path(opts.cwd).iterdir()))
        return ProcResult(list(argv), 0, "finished", 0.1, output='{"vulnerabilities":{}}')

    monkeypatch.setattr(tools_mod, "run_command", fake_run)
    ctx = ToolContext(root, ["javascript"], ["package.json"], False, True, 30)
    r = tools_mod.run_npm_audit(ctx)
    assert r.state == "ok"
    assert f"--registry={tools_mod.NPM_REGISTRY}" in seen["argv"] and seen["argv"][0].lower().endswith("node.exe")
    assert seen["cwd"].resolve() != root.resolve() and seen["files"] == ["package-lock.json", "package.json"]
    assert "NPM_TOKEN" not in seen["env"] and not any(k.lower().startswith("npm_config_") for k in seen["env"])


def test_sanitise_requirements() -> None:
    kept, dropped = sanitise_requirements(
        "requests==2.31.0\n-r ../../secret.txt\n--index-url https://evil\n-e git+https://x/y\n"
        "flask>=2\nuvicorn[standard]==0.30.0 ; python_version > '3.8'  # c\nhttps://x/pkg.whl\n")
    assert kept == ["requests==2.31.0", "uvicorn[standard]==0.30.0"]
    assert len(dropped) == 5


def test_pip_audit_receives_only_pinned_lines(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = _proj(tmp_path, {"requirements.txt": "requests==2.31.0\n-r ../x.txt\n"})
    seen: dict = {}

    def fake_run(argv, opts):  # noqa: ANN001
        req = Path(argv[argv.index("-r") + 1])
        seen.update(content=req.read_text(encoding="utf-8"), cwd=Path(opts.cwd))
        return ProcResult(list(argv), 0, "finished", 0.1, output='{"dependencies":[]}')

    monkeypatch.setattr(tools_mod, "run_command", fake_run)
    r = tools_mod.run_pip_audit(ToolContext(root, ["python"], ["requirements.txt"], False, True, 30))
    assert r.state == "ok" and seen["content"] == "requests==2.31.0\n"
    assert seen["cwd"].resolve() != root.resolve() and "略過 1 行" in r.message


def test_ruff_ignores_project_config_extend(db: Path, tmp_path: Path) -> None:
    root = _proj(tmp_path, {"pyproject.toml": '[tool.ruff]\nextend = "../../does-not-exist.toml"\n',
                            "a.py": "import os\n"})
    _, status, _ = run_analysis(root, AnalysisOptions(tools=["ruff"]))
    assert status["ruff"]["state"] == "ok"


def test_analysis_cancel_marks_scan_and_job(client: TestClient, tmp_path: Path,  # noqa: F811
                                            monkeypatch: pytest.MonkeyPatch) -> None:
    root = _proj(tmp_path, {"a.py": "x = 1\n"})
    started = threading.Event()

    def slow(ctx: ToolContext) -> ToolResult:
        started.set()
        for _ in range(100):
            if ctx.cancel_event is not None and ctx.cancel_event.is_set():
                return ToolResult("cancelled")
            time.sleep(0.05)
        return ToolResult("ok")

    monkeypatch.setattr(engine_mod, "TOOLS", (Tool("slow", "auto", lambda ctx: True, slow),))
    pid = client.post("/api/projects", json={"path": str(root)}).json()["id"]
    jid = client.post(f"/api/projects/{pid}/analyze").json()["job_id"]
    assert started.wait(10)
    # API writes stay responsive while a tool runs (no DB lock held during tool execution).
    t0 = time.monotonic()
    assert client.patch(f"/api/projects/{pid}", json={"name": "改名"}).status_code == 200
    assert time.monotonic() - t0 < 1.0
    client.post(f"/api/jobs/{jid}/cancel")
    assert wait_job(client, jid)["status"] == "cancelled"
    with read_session() as s:
        assert s.query(Scan).filter_by(project_id=pid, kind="analysis").one().status == "cancelled"
