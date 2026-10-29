from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.db import read_session, write_session
from app.models import AuditLog, Job, Project, Scan
from app.services.maintenance import apply_retention
from tests.test_api_projects import client, wait_job  # noqa: F401


def test_settings_get_put_and_audit(client: TestClient) -> None:  # noqa: F811
    assert client.get("/api/settings").json()["retention_days"] == 90
    r = client.put("/api/settings", json={"retention_days": 30, "ai_provider": "ollama", "ollama_model": "qwen"})
    assert r.status_code == 200 and r.json()["retention_days"] == 30
    assert client.put("/api/settings", json={"nope": 1}).status_code == 400
    assert client.put("/api/settings", json={"retention_days": 0}).status_code == 422
    assert client.put("/api/settings", json={"external_ai_api_key_env": "GITHUB_TOKEN"}).status_code == 422
    logs = client.get("/api/audit-logs", params={"action": "settings."}).json()
    assert logs["total"] == 1 and logs["items"][0]["detail"]["retention_days"] == {"from": 90, "to": 30}


def test_backup_and_restore_roundtrip(client: TestClient, tmp_path: Path, settings: Settings) -> None:  # noqa: F811
    d = tmp_path / "keep"
    d.mkdir()
    client.post("/api/projects", json={"path": str(d)})
    b = client.post("/api/system/backup")
    assert b.status_code == 201
    name = b.json()["name"]
    client.delete(f"/api/projects/{client.get('/api/projects').json()[0]['id']}")
    assert client.get("/api/projects").json() == []
    assert [x["name"] for x in client.get("/api/system/backups").json()] == [name]
    r = client.post("/api/system/restore", json={"name": name, "confirm": True})
    assert r.status_code == 200, r.text
    assert [p["name"] for p in client.get("/api/projects").json()] == ["keep"]
    assert any(x["name"] == r.json()["safety_backup"] for x in client.get("/api/system/backups").json())


def test_restore_rejects_bad_input(client: TestClient, settings: Settings) -> None:  # noqa: F811
    assert client.post("/api/system/restore", json={"name": "../aisecc.sqlite3", "confirm": True}).status_code == 409
    assert client.post("/api/system/restore", json={"name": "db-20260101-000000.sqlite3"}).status_code == 422
    bogus = settings.backups_dir / "db-20260101-000000.sqlite3"
    with sqlite3.connect(bogus) as conn:
        conn.execute("create table x (a)")
    r = client.post("/api/system/restore", json={"name": bogus.name, "confirm": True})
    assert r.status_code == 409 and "不是本系統" in r.json()["detail"]
    (settings.backups_dir / "db-20260101-000001.sqlite3").write_bytes(b"garbage" * 100)
    r = client.post("/api/system/restore", json={"name": "db-20260101-000001.sqlite3", "confirm": True})
    assert r.status_code == 409


def _seed_old(tmp_path: Path) -> None:
    old = (datetime.now(UTC) - timedelta(days=200)).replace(tzinfo=None)
    ancient = (datetime.now(UTC) - timedelta(days=800)).replace(tzinfo=None)
    with write_session() as s:
        p = Project(name="p", path=str(tmp_path), path_key="p")
        s.add(p)
        s.flush()
        s.add_all([
            Scan(project_id=p.id, kind="analysis", status="completed", created_at=old),
            Scan(project_id=p.id, kind="analysis", status="completed", created_at=old),  # latest: kept
            Job(id="old", type="x", status="succeeded", created_at=old),
            Job(id="old-running", type="x", status="running", created_at=old),
            Job(id="new", type="x", status="succeeded"),
            AuditLog(action="job.submit", created_at=ancient),
            AuditLog(action="repair.apply", created_at=ancient),
            AuditLog(action="job.cancel", created_at=old),
        ])


def test_retention_deletes_only_old_finished_records(db: Path, settings: Settings, tmp_path: Path) -> None:
    _seed_old(tmp_path)
    (settings.reports_dir / "scan-1.json").write_text("{}", encoding="utf-8")
    preview = apply_retention(settings, dry_run=True)
    with read_session() as s:
        assert s.query(Job).count() == 3 and s.query(AuditLog).count() == 3  # dry run changed nothing
    counts = apply_retention(settings)
    assert counts == preview
    assert counts["scans"] == 1 and counts["jobs"] == 1 and counts["audit_logs"] == 1 and counts["files"] == 1
    with read_session() as s:
        assert {j.id for j in s.query(Job)} == {"old-running", "new"}
        assert [sc.id for sc in s.query(Scan)] == [2]
        actions = {a.action for a in s.query(AuditLog)}
    # approvals/changes are always kept; ordinary entries younger than audit_retention_days too
    assert {"repair.apply", "job.cancel", "system.retention"} <= actions and "job.submit" not in actions


def test_retention_never_follows_links(db: Path, settings: Settings, tmp_path: Path) -> None:
    import os
    import subprocess
    import sys

    sentinel = tmp_path / "sentinel"
    sentinel.mkdir()
    (sentinel / "KEEP.txt").write_text("keep", encoding="utf-8")
    old = (datetime.now(UTC) - timedelta(days=200)).replace(tzinfo=None)
    with write_session() as s:
        p = Project(name="p", path=str(tmp_path), path_key="p")
        s.add(p)
        s.flush()
        from app.models import TestRun

        run = TestRun(project_id=p.id, command_key="k", framework="pytest", argv=[], workdir=".",
                      status="passed", created_at=old)
        s.add(run)
        s.flush()
        rid = run.id
    rep = settings.reports_dir / "tests" / str(rid)
    rep.mkdir(parents=True)
    orphan = settings.workspaces_dir / "repair-77"
    orphan.mkdir(parents=True)
    for link in (rep / "evil", orphan / ".venv"):
        if sys.platform == "win32":
            subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(sentinel)], check=True, capture_output=True)
        else:
            link.symlink_to(sentinel, target_is_directory=True)
    two_days = (datetime.now(UTC) - timedelta(days=2)).timestamp()
    os.utime(orphan, (two_days, two_days))
    apply_retention(settings)
    assert not rep.exists() and not orphan.exists()
    assert (sentinel / "KEEP.txt").read_text(encoding="utf-8") == "keep"


def test_retention_and_restore_require_confirm(client: TestClient) -> None:  # noqa: F811
    assert client.post("/api/system/retention", json={}).status_code == 422
    r = client.post("/api/system/retention", json={"confirm": True, "dry_run": True})
    assert r.status_code == 200 and r.json()["dry_run"] is True


def test_restore_failure_keeps_live_db(client: TestClient, tmp_path: Path, monkeypatch) -> None:  # noqa: F811, ANN001
    import app.services.maintenance as m

    d = tmp_path / "keep"
    d.mkdir()
    name = client.post("/api/system/backup").json()["name"]
    client.post("/api/projects", json={"path": str(d)})

    def boom(path: Path) -> None:
        raise RuntimeError("migration failed")

    monkeypatch.setattr(m, "upgrade_db", boom)
    r = client.post("/api/system/restore", json={"name": name, "confirm": True})
    assert r.status_code == 409 and "原資料庫未變更" in r.json()["detail"]
    assert [p["name"] for p in client.get("/api/projects").json()] == ["keep"]
    assert not list(d.parent.glob("*.restoring"))


def test_restore_refused_with_running_job(client: TestClient) -> None:  # noqa: F811
    name = client.post("/api/system/backup").json()["name"]
    with write_session() as s:
        s.add(Job(id="busy", type="x", status="running"))
    r = client.post("/api/system/restore", json={"name": name, "confirm": True})
    assert r.status_code == 409 and "任務" in r.json()["detail"]


def test_dashboard_and_tools(client: TestClient, tmp_path: Path) -> None:  # noqa: F811
    d = tmp_path / "dash"
    d.mkdir()
    (d / "a.py").write_text('password = "Zq8vR2kLm9Xw4TnB"\n', encoding="utf-8")
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    wait_job(client, client.post(f"/api/projects/{pid}/analyze", json={"tools": ["ruff"]}).json()["job_id"], 120)
    dash = client.get("/api/dashboard").json()
    assert dash["counts"]["projects"] == 1 and dash["open_findings_by_category"].get("secret", 0) >= 1
    assert dash["projects"][0]["name"] == "dash" and dash["recent_jobs"]
    tools = client.get("/api/system/tools").json()
    assert tools["tools"]["ruff"] and tools["tools"]["git"] and tools["python"].startswith("3.12")


def test_audit_log_filters(client: TestClient, tmp_path: Path) -> None:  # noqa: F811
    d = tmp_path / "a"
    d.mkdir()
    pid = client.post("/api/projects", json={"path": str(d)}).json()["id"]
    page = client.get("/api/audit-logs", params={"project_id": pid}).json()
    assert page["total"] >= 1 and all(i["project_id"] == pid for i in page["items"])
    assert client.get("/api/audit-logs", params={"q": "project.add"}).json()["total"] >= 1


def test_retention_keeps_generating_workspace(db: Path, settings: Settings, tmp_path: Path) -> None:
    import os

    from app.models import RepairProposal

    ws = settings.workspaces_dir / "repair-8"
    ws.mkdir(parents=True)
    three_days = (datetime.now(UTC) - timedelta(days=3)).timestamp()
    os.utime(ws, (three_days, three_days))
    with write_session() as s:
        p = Project(name="p", path=str(tmp_path), path_key="p")
        s.add(p)
        s.flush()
        s.add(RepairProposal(project_id=p.id, provider="x", status="generating", workspace_path=str(ws)))
    apply_retention(settings)
    assert ws.is_dir()
