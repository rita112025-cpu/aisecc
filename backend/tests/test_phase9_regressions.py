"""Phase 9 regression: cancelling an AI repair job that never started must not leave its proposal
in 'generating' (found by the real-Ollama E2E: generate() never runs for a cancelled queued job)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import read_session, write_session
from app.models import Job, Project, RepairProposal
from tests.test_ai import ai_client  # noqa: F401 - fixture
from tests.test_api_projects import client  # noqa: F401 - fixture


def _seed(job_status: str, started: bool) -> tuple[int, int]:
    from datetime import UTC, datetime

    with write_session() as s:
        p = Project(name="p", path="C:/phase9", path_key="c:/phase9")
        s.add(p)
        s.flush()
        prop = RepairProposal(project_id=p.id, provider="ollama", status="generating", target_file="a.py",
                              instruction="x")
        s.add(prop)
        s.flush()
        s.add(Job(id=f"job-{job_status}-{started}", type="ai_repair", project_id=p.id, status=job_status,
                  params={"proposal_id": prop.id}, started_at=datetime.now(UTC) if started else None))
        return prop.id, p.id


def test_cancel_queued_repair_finalises_proposal(client: TestClient) -> None:  # noqa: F811
    pid, _ = _seed("queued", started=False)
    r = client.post("/api/jobs/job-queued-False/cancel")
    assert r.status_code == 200 and r.json()["status"] == "cancelled"
    with read_session() as s:
        prop = s.get(RepairProposal, pid)
        assert prop.status == "failed" and "取消" in (prop.error or "")
    # a failed proposal can be cleaned up by the user
    assert client.post(f"/api/repairs/{pid}/reject").status_code == 200


def test_cancel_running_repair_leaves_proposal_to_generate(client: TestClient) -> None:  # noqa: F811
    """A job that already started is finalised by generate() itself; the API must not touch it."""
    pid, _ = _seed("running", started=True)
    client.post("/api/jobs/job-running-True/cancel")
    with read_session() as s:
        assert s.get(RepairProposal, pid).status == "generating"


def test_dependency_link_refuses_cmd_metacharacters(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    """mklink runs through cmd.exe: a path with & and no spaces would execute a second command.
    The link must be refused (no subprocess at all), not created."""
    import subprocess

    from app.services.ai import workspace

    evil_root = tmp_path / "x&ver&rem"
    (evil_root / ".venv").mkdir(parents=True)
    ws = tmp_path / "ws"
    ws.mkdir()
    spawned: list = []
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: spawned.append(a) or None)
    try:
        workspace.link_dependencies(evil_root, ws, ".")
        raised = False
    except workspace.WorkspaceError:
        raised = True
    assert raised and spawned == []
    assert not (ws / ".venv").exists()


def test_cancel_queued_non_repair_job_unaffected(client: TestClient) -> None:  # noqa: F811
    with write_session() as s:
        s.add(Job(id="job-scan", type="inventory_scan", status="queued", params={}))
    assert client.post("/api/jobs/job-scan/cancel").status_code == 200
    with read_session() as s:
        assert s.get(Job, "job-scan").status == "cancelled"


SECRET = "sk-live-0123456789abcdefABCDEF"


def test_repair_diff_is_masked_but_apply_writes_real_bytes(ai_client) -> None:  # noqa: ANN001, F811
    """Design (user-approved): the stored/displayed diff and its confirmation hash use the masked text,
    while apply writes the real verified bytes. The plaintext secret must not reach the proposal."""
    import hashlib
    import json

    from tests.test_ai import FIX, FakeProvider, _propose, git

    c, pid, repo = ai_client
    # secret on the line right above the edited line, so it falls inside the diff context
    (repo / "src" / "calc.py").write_text(
        f'def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n    token = "{SECRET}"\n    return a / b\n',
        encoding="utf-8")
    git(repo, "-c", "user.email=e2e@example.invalid", "-c", "user.name=e2e", "commit", "-qam", "secret fixture")
    p = _propose(c, pid, FakeProvider(json.dumps(FIX)))
    assert p["status"] == "validated", p
    assert SECRET not in p["diff"], "plaintext secret persisted in proposal diff"
    assert SECRET not in json.dumps(p, ensure_ascii=False)
    shown_hash = hashlib.sha256(p["diff"].encode("utf-8")).hexdigest()
    assert p["validation"]["diff_sha256"] == shown_hash  # the hash the user confirms is of the masked view
    r = c.post(f"/api/repairs/{p['id']}/apply", json={"confirm": True, "acknowledge_test_status": "not_run",
                                                     "diff_sha256": shown_hash})
    assert r.status_code == 200, r.text
    applied = (repo / "src" / "calc.py").read_text(encoding="utf-8")
    assert SECRET in applied and "if b else 0" in applied  # real bytes written, not the masked view
