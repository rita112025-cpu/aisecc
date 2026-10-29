"""Regressions for review items gathered before round 6 (redaction, env, rerun, recovery, repair safety)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.db import read_session, write_session
from app.models import AuditLog, Job, Project, RepairProposal, Scan, TestRun
from app.services.ai import workspace
from app.services.ai.providers import MAX_RESPONSE_BYTES, AIError, OllamaProvider, OpenAICompatibleProvider
from app.services.ai.schema import InvalidAIResponse, parse_response
from app.services.analysis import engine as engine_mod
from app.services.analysis.base import RawFinding
from app.services.analysis.engine import AnalysisOptions, _redact_all, run_analysis
from app.services.analysis.secrets import redact_obj
from app.services.maintenance import recover_records
from app.services.testing.commands import child_env, rerun_args
from app.services.testing.runner import _run_status
from tests.conftest import git
from tests.test_ai import FIX, FakeProvider, _propose, ai_client  # noqa: F401
from tests.test_api_projects import client, wait_job  # noqa: F401

PW = "Zq8!vR3kLp0xW2mN"


# ---- redaction -----------------------------------------------------------------------------

def test_redact_obj_quoted_and_backslash() -> None:
    a = redact_obj({"snippet": f'db_password = "{PW}"'}, set())
    assert PW not in json.dumps(a, ensure_ascii=False)
    b = redact_obj({"snippet": "db_password = Zq8\\vR3kLp0xW2mN", "k": ["x"]}, {"Zq8\\vR3kLp0xW2mN"})
    dumped = json.dumps(b, ensure_ascii=False)
    assert "Zq8\\vR3kLp0xW2mN" not in b["snippet"] and "\x0b" not in dumped and b["k"] == ["x"]


def test_redact_all_handles_extra() -> None:
    f = RawFinding("eslint", "r", "low", "lint", "m", "a.js", 1, extra={"src": f"token: '{PW}'"})
    (out,) = _redact_all([f], set())
    assert PW not in json.dumps(out.extra, ensure_ascii=False)


def test_bandit_ignores_project_ini(db: Path, tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    (root / ".bandit").write_text("[bandit]\nskips = B105\n", encoding="utf-8")
    (root / "app.py").write_text(f'db_password = "{PW}"\n', encoding="utf-8")
    findings, status, _ = run_analysis(root, AnalysisOptions(tools=["bandit"]))
    assert [f.rule_id for f in findings if f.tool == "bandit"] == ["B105"]
    assert "B105" in status["bandit"]["message"] and "未套用" in status["bandit"]["message"]
    assert all(PW not in f.message for f in findings)


def test_optional_tool_missing_not_partial(client: TestClient, tmp_path: Path, monkeypatch) -> None:  # noqa: F811, ANN001
    from app.services.analysis.tools import TOOLS

    monkeypatch.setattr(engine_mod, "TOOLS", tuple(t for t in TOOLS if t.name in ("ruff", "semgrep")))
    root = tmp_path / "p"
    root.mkdir()
    (root / "a.py").write_text("x = 1\n", encoding="utf-8")
    pid = client.post("/api/projects", json={"path": str(root)}).json()["id"]
    job = wait_job(client, client.post(f"/api/projects/{pid}/analyze").json()["job_id"], 120)
    scan = client.get(f"/api/scans/{job['result']['scan_id']}").json()
    if scan["tool_status"]["semgrep"]["state"] != "not_installed":
        pytest.skip("semgrep 已安裝於本機，無法驗證未安裝情境")
    assert scan["status"] == "completed"


# ---- test runner ---------------------------------------------------------------------------

def test_child_env_allowlist(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "x")
    monkeypatch.setenv("AISECC_EXTERNAL_AI_KEY", "y")
    env = child_env([], False, {"A": "1"})
    assert "GITHUB_TOKEN" not in env and "PATH" in {k.upper() for k in env} and env["A"] == "1"
    assert "GITHUB_TOKEN" in child_env(["github_token"], False, {})
    assert "AISECC_EXTERNAL_AI_KEY" not in child_env([], True, {})


def test_env_change_revokes_approval_and_hides_token(client: TestClient, tmp_path: Path,  # noqa: F811
                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "tok-123")
    root = tmp_path / "envproj"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_env.py").write_text(
        "import os\n\ndef test_env():\n    print('TOKEN=' + os.environ.get('GITHUB_TOKEN', 'absent'))\n",
        encoding="utf-8")
    pid = client.post("/api/projects", json={"path": str(root)}).json()["id"]
    body = {"framework": "pytest", "argv": [sys.executable, "-m", "pytest", "-s", "tests"], "cwd": "."}
    client.put(f"/api/projects/{pid}/test-commands/t", json=body)

    def approve() -> None:
        c = client.get(f"/api/projects/{pid}/test-commands").json()[0]
        client.post(f"/api/projects/{pid}/test-commands/t/approve", json={"content_hash": c["content_hash"]})

    def run_log() -> str:
        jid = client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "t"}).json()["job_id"]
        wait_job(client, jid, 120)
        return client.get(f"/api/jobs/{jid}/log").json()["text"]

    approve()
    assert "TOKEN=absent" in run_log()
    client.put(f"/api/projects/{pid}/test-commands/t", json=body | {"extra_env_names": ["GITHUB_TOKEN"]})
    assert client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "t"}).status_code == 403
    approve()
    assert "TOKEN=tok-123" in run_log()


def test_test_output_redacted_in_log(client: TestClient, tmp_path: Path) -> None:  # noqa: F811
    root = tmp_path / "leak"
    (root / "tests").mkdir(parents=True)
    (root / "tests" / "test_leak.py").write_text(
        f"def test_leak():\n    print('password = \"{PW}\"')\n    assert False, 'api_key=\"{PW}\"'\n", encoding="utf-8")
    pid = client.post("/api/projects", json={"path": str(root)}).json()["id"]
    client.put(f"/api/projects/{pid}/test-commands/t",
               json={"framework": "pytest", "argv": [sys.executable, "-m", "pytest", "-s", "tests"]})
    c = client.get(f"/api/projects/{pid}/test-commands").json()[0]
    client.post(f"/api/projects/{pid}/test-commands/t/approve", json={"content_hash": c["content_hash"]})
    job = wait_job(client, client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "t"}).json()["job_id"])
    rid = job["result"]["test_run_id"]
    assert PW not in client.get(f"/api/jobs/{job['id']}/log").json()["text"]
    assert PW not in json.dumps(client.get(f"/api/test-runs/{rid}/results").json(), ensure_ascii=False)
    assert PW not in json.dumps(client.get(f"/api/test-runs/{rid}").json(), ensure_ascii=False)


@pytest.mark.parametrize("bad", [
    {"file": "--basetemp=C:/Users/victim", "name": "test_a", "classname": None},
    {"file": "../../outside/test_x.py", "name": "test_a", "classname": None},
    {"file": "tests/test_a.py", "name": "--rootdir=C:/", "classname": None},
    {"file": "tests/test_a.py", "name": "x\ny", "classname": None},
])
def test_rerun_rejects_unsafe_selectors(tmp_path: Path, bad: dict) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="無法"):
        rerun_args("pytest", [bad], tmp_path)


def test_rerun_other_frameworks_filter_names(tmp_path: Path) -> None:
    assert rerun_args("vitest", [{"name": "adds > works"}, {"name": "-t evil"}], tmp_path) == ["-t", "works"]
    with pytest.raises(ValueError):
        rerun_args("dotnet", [{"name": "A|B"}], tmp_path)


@pytest.mark.parametrize(("fw", "outcome", "code", "counts", "parsed", "expected"), [
    ("pytest", "finished", 1, {"failed": 0, "errors": 0}, False, "error"),
    ("pytest", "finished", 5, {"failed": 0, "errors": 0}, True, "no_tests"),
    ("pytest", "finished", 3, {"failed": 0, "errors": 0}, True, "error"),
    ("pytest", "finished", 1, {"failed": 1, "errors": 0}, True, "failed"),
    ("pytest", "finished", 0, {"failed": 0, "errors": 0}, True, "passed"),
])
def test_run_status_rules(fw, outcome, code, counts, parsed, expected) -> None:  # noqa: ANN001
    status, note = _run_status(fw, outcome, code, counts, parsed)
    assert status == expected
    if code == 3:
        assert "exit 3" in note


def test_exit_nonzero_without_report_is_error(client: TestClient, tmp_path: Path) -> None:  # noqa: F811
    root = tmp_path / "exit1"
    root.mkdir()
    pid = client.post("/api/projects", json={"path": str(root)}).json()["id"]
    client.put(f"/api/projects/{pid}/test-commands/t",
               json={"framework": "pytest", "argv": [sys.executable, "-c", "import sys; sys.exit(1)"]})
    c = client.get(f"/api/projects/{pid}/test-commands").json()[0]
    client.post(f"/api/projects/{pid}/test-commands/t/approve", json={"content_hash": c["content_hash"]})
    job = wait_job(client, client.post(f"/api/projects/{pid}/test-runs", json={"command_key": "t"}).json()["job_id"])
    assert client.get(f"/api/test-runs/{job['result']['test_run_id']}").json()["status"] == "error"


# ---- recovery & workspaces -----------------------------------------------------------------

def _junction(link: Path, target: Path) -> None:
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def test_recover_marks_runs_scans_repairs_and_cleans_orphans(db: Path, settings: Settings, tmp_path: Path) -> None:
    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    (sentinel / "KEEP.txt").write_text("keep", encoding="utf-8")
    orphan = settings.workspaces_dir / "repair-999"
    orphan.mkdir(parents=True)
    _junction(orphan / ".venv", sentinel)
    with write_session() as s:
        p = Project(name="p", path=str(tmp_path), path_key=str(tmp_path).lower())
        s.add(p)
        s.flush()
        s.add(Job(id="j", type="test_run", status="interrupted"))
        s.add(TestRun(project_id=p.id, job_id="j", command_key="k", framework="pytest", argv=[], workdir=".",
                      status="running"))
        s.add(Scan(project_id=p.id, kind="analysis", status="running"))
        s.add(RepairProposal(project_id=p.id, provider="x", status="generating"))
    counts = recover_records(settings)
    assert counts == {"test_runs": 1, "scans": 1, "repairs": 1, "workspaces": 1}
    with read_session() as s:
        assert s.query(TestRun).one().status == "interrupted"
        assert s.query(Scan).one().status == "interrupted"
        assert s.query(RepairProposal).one().status == "failed"
    assert not orphan.exists() and (sentinel / "KEEP.txt").read_text(encoding="utf-8") == "keep"


def test_safe_rmtree_refuses_outside_root(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    root.mkdir()
    other = tmp_path / "other"
    other.mkdir()
    with pytest.raises(workspace.WorkspaceError):
        workspace.safe_rmtree(other, root)
    assert other.exists()


def test_editable_install_marks_not_isolated(ai_client) -> None:  # noqa: ANN001, F811
    c, pid, repo = ai_client
    sp = repo / ".venv" / "Lib" / "site-packages"
    sp.mkdir(parents=True)
    (sp / "__editable__.demo-0.1.pth").write_text(str(repo / "src") + "\n", encoding="utf-8")
    (repo / ".gitignore").write_text("ignored_dir/\n*.tmp\n.venv/\n", encoding="utf-8")
    git(repo, "commit", "-qam", "ignore venv")
    c.put(f"/api/projects/{pid}/test-commands/pytest",
          json={"framework": "pytest", "argv": [sys.executable, "-m", "pytest", "-q"]})
    cmd = c.get(f"/api/projects/{pid}/test-commands").json()[0]
    c.post(f"/api/projects/{pid}/test-commands/pytest/approve", json={"content_hash": cmd["content_hash"]})
    p = _propose(c, pid, FakeProvider(json.dumps(FIX)), test_command_key="pytest")
    assert p["validation"]["test_status"] == "not_isolated" and p["test_run_id"] is None
    sha = p["validation"]["diff_sha256"]
    r = c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "diff_sha256": sha})
    assert r.status_code == 409 and "not_isolated" in r.json()["detail"]
    r = c.post(f"/api/repairs/{p['id']}/apply",
               json={"confirm": True, "diff_sha256": sha, "acknowledge_test_status": "not_isolated"})
    assert r.status_code == 200


def test_apply_rollback_failure_recorded(ai_client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    from app.services.ai import patch as patch_mod

    c, pid, repo = ai_client
    (repo / "src" / "util.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "util")
    two = FIX | {"edits": [FIX["edits"][0], {"path": "src/util.py", "search": "return 1", "replace": "return 2"}]}
    p = _propose(c, pid, FakeProvider(json.dumps(two)))
    assert p["status"] == "validated", p
    calls = {"n": 0}
    real = patch_mod.atomic_write

    def flaky(path: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] >= 2:  # second file and the rollback of the first both fail
            raise PermissionError("locked by IDE")
        real(path, data)

    monkeypatch.setattr(patch_mod, "atomic_write", flaky)
    r = c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "acknowledge_test_status": "not_run",
                                                      "diff_sha256": p["validation"]["diff_sha256"]})
    assert r.status_code == 409
    assert c.get(f"/api/repairs/{p['id']}").json()["status"] == "apply_failed"
    with read_session() as s:
        log = s.query(AuditLog).filter_by(action="repair.apply_failed").one()
    assert log.detail["rollback_failed"] == ["src/calc.py"]


def test_apply_rollback_success_keeps_validated(ai_client, monkeypatch: pytest.MonkeyPatch) -> None:  # noqa: ANN001, F811
    from app.services.ai import patch as patch_mod

    c, pid, repo = ai_client
    (repo / "src" / "util.py").write_text("def one():\n    return 1\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", "util")
    originals = {f: (repo / "src" / f).read_bytes() for f in ("calc.py", "util.py")}
    two = FIX | {"edits": [FIX["edits"][0], {"path": "src/util.py", "search": "return 1", "replace": "return 2"}]}
    p = _propose(c, pid, FakeProvider(json.dumps(two)))
    real = patch_mod.atomic_write
    calls = {"n": 0}

    def fail_second(path: Path, data: bytes) -> None:
        calls["n"] += 1
        if calls["n"] == 2:
            raise PermissionError("locked")
        real(path, data)

    monkeypatch.setattr(patch_mod, "atomic_write", fail_second)
    r = c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "acknowledge_test_status": "not_run",
                                                      "diff_sha256": p["validation"]["diff_sha256"]})
    assert r.status_code == 409
    assert {f: (repo / "src" / f).read_bytes() for f in originals} == originals
    assert c.get(f"/api/repairs/{p['id']}").json()["status"] == "validated"


# ---- AI provider limits --------------------------------------------------------------------

def test_redirect_not_followed() -> None:
    seen = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(str(req.url))
        return httpx.Response(302, headers={"location": "http://evil.example/steal"})

    p = OpenAICompatibleProvider("https://api.example.com/v1", "m", "k", 5, httpx.MockTransport(handler))
    with pytest.raises(AIError, match="轉址"):
        p.generate("x")
    assert len(seen) == 1


def test_oversized_response_rejected() -> None:
    big = json.dumps({"response": "x" * (MAX_RESPONSE_BYTES + 50_000)})
    p = OllamaProvider("http://127.0.0.1:11434", "m", 5, httpx.MockTransport(lambda r: httpx.Response(200, text=big)))
    with pytest.raises(AIError, match="過大"):
        p.generate("x")


def test_masked_secret_in_replace_rejected() -> None:
    bad = FIX | {"edits": [FIX["edits"][0] | {"replace": 'TOKEN = "ghp_…xY（長度 40）"'}]}
    with pytest.raises(InvalidAIResponse):
        parse_response(json.dumps(bad))
    banner = FIX | {"edits": [FIX["edits"][0] | {"replace": "# ************\n    return a / b if b else 0"}]}
    assert parse_response(json.dumps(banner))


def test_recover_keeps_validated_workspace_despite_path_spelling(db: Path, settings: Settings, tmp_path: Path) -> None:
    ws = settings.workspaces_dir / "repair-5"
    ws.mkdir(parents=True)
    spelled = str(ws).replace("\\", "/")
    spelled = spelled[0].swapcase() + spelled[1:]
    with write_session() as s:
        p = Project(name="p", path=str(tmp_path), path_key="p")
        s.add(p)
        s.flush()
        s.add(RepairProposal(project_id=p.id, provider="x", status="validated", workspace_path=spelled))
    recover_records(settings)
    assert ws.is_dir()
