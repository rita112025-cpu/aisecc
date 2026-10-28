"""Database backup/restore and data retention."""

from __future__ import annotations

import re
import shutil
import sqlite3
from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import func

from app.config import Settings
from app.db import WRITE_LOCK, dispose_engine, init_engine, read_session, write_session
from app.migrate import upgrade_db
from app.models import AuditLog, Job, RepairProposal, Scan, TestRun
from app.services import audit
from app.services.jobs import ACTIVE_STATUSES
from app.services.settings_store import load_settings

BACKUP_NAME = re.compile(r"^db-\d{8}-\d{6}(-\d{1,3})?(-pre-restore)?\.sqlite3$")


class MaintenanceError(RuntimeError):
    pass


def _stamp() -> str:
    return datetime.now(UTC).strftime("%Y%m%d-%H%M%S")


def backup_db(settings: Settings, suffix: str = "") -> dict[str, Any]:
    """Online, consistent copy via the SQLite backup API (safe while the app is running)."""
    settings.backups_dir.mkdir(parents=True, exist_ok=True)
    stamp = _stamp()
    dest = settings.backups_dir / f"db-{stamp}{suffix}.sqlite3"
    n = 1
    while dest.exists():  # several backups within the same second
        dest = settings.backups_dir / f"db-{stamp}-{n}{suffix}.sqlite3"
        n += 1
    with closing(sqlite3.connect(settings.db_path)) as src, closing(sqlite3.connect(dest)) as dst:
        src.backup(dst)
    return {"name": dest.name, "size": dest.stat().st_size}


def list_backups(settings: Settings) -> list[dict[str, Any]]:
    if not settings.backups_dir.is_dir():
        return []
    items = []
    for p in settings.backups_dir.iterdir():
        if p.is_file() and BACKUP_NAME.match(p.name):
            st = p.stat()
            items.append({"name": p.name, "size": st.st_size,
                          "created_at": datetime.fromtimestamp(st.st_mtime, UTC).isoformat()})
    return sorted(items, key=lambda i: i["name"], reverse=True)


def _validate_backup(path: Path) -> None:
    try:
        with closing(sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)) as conn:
            if conn.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise MaintenanceError("備份檔完整性檢查失敗")
            tables = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    except sqlite3.DatabaseError as exc:
        raise MaintenanceError(f"不是有效的資料庫備份：{exc}") from exc
    if not {"alembic_version", "projects", "jobs"} <= tables:
        raise MaintenanceError("備份檔不是本系統的資料庫")


def restore_db(settings: Settings, name: str) -> dict[str, Any]:
    if not BACKUP_NAME.match(name):
        raise MaintenanceError("備份檔名不合法")
    src = settings.backups_dir / name
    if not src.is_file():
        raise MaintenanceError("備份檔不存在")
    _validate_backup(src)
    with read_session() as s:
        if s.query(Job).filter(Job.status.in_(ACTIVE_STATUSES)).count():
            raise MaintenanceError("有任務正在執行，請等待完成或取消後再還原")
    safety = backup_db(settings, "-pre-restore")
    with WRITE_LOCK:
        dispose_engine()
        try:
            for ext in ("-wal", "-shm"):
                Path(f"{settings.db_path}{ext}").unlink(missing_ok=True)
            shutil.copy2(src, settings.db_path)
            upgrade_db(settings.db_path)
        finally:
            init_engine(settings.db_path)
    audit.record("system.restore", detail={"from": name, "safety_backup": safety["name"]})
    return {"restored": name, "safety_backup": safety["name"]}


def apply_retention(settings: Settings, now: datetime | None = None) -> dict[str, int]:
    """Delete finished records older than `retention_days`, with their log and report files.
    Projects, findings of the latest scan per project and applied repairs' backups are kept."""
    days = load_settings().retention_days
    cutoff = (now or datetime.now(UTC)) - timedelta(days=days)
    cutoff = cutoff.replace(tzinfo=None)  # SQLite stores naive UTC
    counts = {"jobs": 0, "scans": 0, "test_runs": 0, "repairs": 0, "audit_logs": 0, "files": 0}
    with read_session() as s:
        latest_scans = {sid for (sid,) in s.query(func.max(Scan.id)).group_by(Scan.project_id, Scan.kind)}
        old_jobs = [(j.id, j.log_path) for j in s.query(Job).filter(
            Job.created_at < cutoff, Job.status.notin_(ACTIVE_STATUSES))]
        old_scans = [sid for (sid,) in s.query(Scan.id).filter(Scan.created_at < cutoff)
                     if sid not in latest_scans]
        old_runs = [rid for (rid,) in s.query(TestRun.id).filter(TestRun.created_at < cutoff,
                                                                 TestRun.status != "running")]
    for sid in old_scans:
        for ext in ("json", "html"):
            p = settings.reports_dir / f"scan-{sid}.{ext}"
            if p.is_file():
                p.unlink()
                counts["files"] += 1
    for rid in old_runs:
        d = settings.reports_dir / "tests" / str(rid)
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            counts["files"] += 1
    for _, log_path in old_jobs:
        if log_path and Path(log_path).is_file() and settings.logs_dir in Path(log_path).parents:
            Path(log_path).unlink()
            counts["files"] += 1
    with write_session() as s:
        counts["scans"] = s.query(Scan).filter(Scan.id.in_(old_scans)).delete(synchronize_session=False) \
            if old_scans else 0
        counts["test_runs"] = s.query(TestRun).filter(TestRun.id.in_(old_runs)).delete(synchronize_session=False) \
            if old_runs else 0
        counts["repairs"] = s.query(RepairProposal).filter(
            RepairProposal.created_at < cutoff, RepairProposal.status.in_(("rejected", "invalid", "failed"))
        ).delete(synchronize_session=False)
        counts["jobs"] = s.query(Job).filter(Job.id.in_([j for j, _ in old_jobs])).delete(
            synchronize_session=False) if old_jobs else 0
        counts["audit_logs"] = s.query(AuditLog).filter(AuditLog.created_at < cutoff).delete(
            synchronize_session=False)
        audit.record("system.retention", detail={"days": days, **counts}, session=s)
    return counts
