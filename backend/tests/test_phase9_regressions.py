"""Phase 9 regression: cancelling an AI repair job that never started must not leave its proposal
in 'generating' (found by the real-Ollama E2E: generate() never runs for a cancelled queued job)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from app.db import read_session, write_session
from app.models import Job, Project, RepairProposal
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


def test_cancel_queued_non_repair_job_unaffected(client: TestClient) -> None:  # noqa: F811
    with write_session() as s:
        s.add(Job(id="job-scan", type="inventory_scan", status="queued", params={}))
    assert client.post("/api/jobs/job-scan/cancel").status_code == 200
    with read_session() as s:
        assert s.get(Job, "job-scan").status == "cancelled"
