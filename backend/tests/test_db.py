from __future__ import annotations

import sqlite3
import threading
from pathlib import Path

import pytest
from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError

from app.db import DatabaseBusyError, get_engine, read_session, write_session
from app.models import Finding, Project, Scan
from app.services import audit
from app.services.settings_store import load_settings, save_settings

REQUIRED_TABLES = {
    "projects", "scans", "findings", "test_runs", "test_results",
    "repair_proposals", "audit_logs", "jobs", "settings",
}


def _project(s, path: str = "C:/x") -> Project:
    p = Project(name="p", path=path, path_key=path.lower())
    s.add(p)
    s.flush()
    return p


def test_migration_creates_required_tables(db: Path) -> None:
    tables = set(inspect(get_engine()).get_table_names())
    assert REQUIRED_TABLES <= tables
    assert "alembic_version" in tables


def test_migration_is_idempotent(db: Path) -> None:
    from app.migrate import upgrade_db

    upgrade_db(db)  # second run must be a no-op, not fail
    with sqlite3.connect(db) as conn:
        assert conn.execute("select version_num from alembic_version").fetchone()[0] == "0001"


def test_wal_mode_enabled(db: Path) -> None:
    with get_engine().connect() as conn:
        assert conn.exec_driver_sql("PRAGMA journal_mode").scalar() == "wal"
        assert conn.exec_driver_sql("PRAGMA foreign_keys").scalar() == 1


def test_duplicate_project_path_rejected(db: Path) -> None:
    with write_session() as s:
        _project(s, "C:/dup")
    with pytest.raises(IntegrityError), write_session() as s:
        _project(s, "C:/dup")


def test_duplicate_finding_fingerprint_rejected(db: Path) -> None:
    with write_session() as s:
        p = _project(s)
        scan = Scan(project_id=p.id, kind="analysis")
        s.add(scan)
        s.flush()
        scan_id, pid = scan.id, p.id
        s.add(Finding(scan_id=scan_id, project_id=pid, tool="t", rule_id="r", severity="low",
                      category="lint", message="m", fingerprint="abc"))
    with pytest.raises(IntegrityError), write_session() as s:
        s.add(Finding(scan_id=scan_id, project_id=pid, tool="t", rule_id="r", severity="low",
                      category="lint", message="m", fingerprint="abc"))


def test_project_delete_cascades_to_scans(db: Path) -> None:
    with write_session() as s:
        p = _project(s)
        s.add(Scan(project_id=p.id, kind="inventory"))
    with write_session() as s:
        s.delete(s.query(Project).one())
    with read_session() as s:
        assert s.query(Scan).count() == 0


def test_concurrent_writers_do_not_lock(db: Path) -> None:
    errors: list[Exception] = []

    def worker(n: int) -> None:
        try:
            for i in range(20):
                audit.record("test.write", target_id=f"{n}-{i}")
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    from app.models import AuditLog

    with read_session() as s:
        assert s.query(AuditLog).filter(AuditLog.action == "test.write").count() == 120


def test_external_process_lock_reports_busy(db: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.db as dbmod

    monkeypatch.setattr(dbmod, "BUSY_TIMEOUT_MS", 200)
    dbmod.init_engine(db)  # re-create engine with short timeout
    blocker = sqlite3.connect(db, timeout=0.1)
    blocker.execute("BEGIN EXCLUSIVE")
    try:
        with pytest.raises(DatabaseBusyError):
            with write_session() as s:
                _project(s, "C:/busy")
    finally:
        blocker.rollback()
        blocker.close()


def test_settings_defaults_and_persist(db: Path) -> None:
    assert load_settings().retention_days == 90
    save_settings({"retention_days": 30})
    assert load_settings().retention_days == 30


def test_settings_reject_remote_ollama(db: Path) -> None:
    with pytest.raises(ValueError):
        save_settings({"ollama_url": "http://example.com:11434"})
