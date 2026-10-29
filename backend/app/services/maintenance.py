"""Database backup/restore and data retention."""

from __future__ import annotations

import os
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
from app.services.ai import workspace
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
    """Stage the backup next to the live DB, validate and migrate the staged copy, and only then
    swap it in with os.replace. Any failure leaves the live database untouched."""
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
    staged = settings.db_path.with_suffix(".restoring")
    try:
        shutil.copy2(src, staged)
        _validate_backup(staged)
        upgrade_db(staged)
    except Exception as exc:  # noqa: BLE001 - any staging failure must leave the live DB intact
        _cleanup_staged(staged)
        raise MaintenanceError(f"還原失敗，原資料庫未變更：{exc}") from exc
    with WRITE_LOCK:
        dispose_engine()
        try:
            for ext in ("-wal", "-shm"):
                Path(f"{settings.db_path}{ext}").unlink(missing_ok=True)
            os.replace(staged, settings.db_path)
        except OSError as exc:
            _cleanup_staged(staged)
            raise MaintenanceError(f"還原失敗，原資料庫未變更：{exc}") from exc
        finally:
            init_engine(settings.db_path)
    audit.record("system.restore", detail={"from": name, "safety_backup": safety["name"]})
    return {"restored": name, "safety_backup": safety["name"]}


def _cleanup_staged(staged: Path) -> None:
    for p in (staged, Path(f"{staged}-wal"), Path(f"{staged}-shm")):
        p.unlink(missing_ok=True)


def recover_records(settings: Settings) -> dict[str, int]:
    """Called at startup after job recovery: nothing can be running yet, so any run/scan still
    marked running was interrupted, and repair workspaces without a pending proposal are orphans."""
    counts = {"test_runs": 0, "scans": 0, "repairs": 0, "workspaces": 0}
    now = datetime.now(UTC)
    with write_session() as s:
        for run in s.query(TestRun).filter(TestRun.status == "running"):
            run.status, run.finished_at = "interrupted", now
            counts["test_runs"] += 1
        for scan in s.query(Scan).filter(Scan.status == "running"):
            scan.status, scan.finished_at = "interrupted", now
            counts["scans"] += 1
        for p in s.query(RepairProposal).filter(RepairProposal.status == "generating"):
            p.status, p.error = "failed", "後端重新啟動，產生過程中斷"
            counts["repairs"] += 1
        keep = {p.workspace_path for p in s.query(RepairProposal).filter(RepairProposal.status == "validated")}
        if any(counts.values()):
            audit.record("system.recover", detail=dict(counts), session=s)
    ws_root = settings.workspaces_dir
    if ws_root.is_dir():
        for d in ws_root.glob("repair-*"):
            if str(d) not in keep:
                try:
                    workspace.safe_rmtree(d, ws_root)
                    counts["workspaces"] += 1
                except (OSError, workspace.WorkspaceError):
                    continue
    return counts


# Audit records of approvals and changes are kept regardless of age (requirement: keep execution
# records). Other audit entries follow audit_retention_days (minimum one year).
PROTECTED_AUDIT_PREFIXES = ("repair.", "test_command.", "project.", "settings.", "system.")


def apply_retention(settings: Settings, now: datetime | None = None, dry_run: bool = False) -> dict[str, int]:
    """Delete finished records older than `retention_days`, with their log and report files.
    Projects, the latest scan per project and kind, and protected audit records are kept.
    With dry_run=True nothing is changed and the counts of what would be deleted are returned."""
    cfg = load_settings()
    now = (now or datetime.now(UTC)).replace(tzinfo=None)  # SQLite stores naive UTC
    cutoff = now - timedelta(days=cfg.retention_days)
    audit_cutoff = now - timedelta(days=cfg.audit_retention_days)
    with read_session() as s:
        latest_scans = {sid for (sid,) in s.query(func.max(Scan.id)).group_by(Scan.project_id, Scan.kind)}
        old_jobs = [(j.id, j.log_path) for j in s.query(Job).filter(
            Job.created_at < cutoff, Job.status.notin_(ACTIVE_STATUSES))]
        old_scans = [sid for (sid,) in s.query(Scan.id).filter(Scan.created_at < cutoff, Scan.status != "running")
                     if sid not in latest_scans]
        old_runs = [rid for (rid,) in s.query(TestRun.id).filter(TestRun.created_at < cutoff,
                                                                 TestRun.status != "running")]
        old_repairs = [rid for (rid,) in s.query(RepairProposal.id).filter(
            RepairProposal.created_at < cutoff, RepairProposal.status.in_(("rejected", "invalid", "failed")))]
        audit_q = s.query(AuditLog.id).filter(AuditLog.created_at < audit_cutoff)
        for prefix in PROTECTED_AUDIT_PREFIXES:
            audit_q = audit_q.filter(~AuditLog.action.startswith(prefix))
        old_audit = [aid for (aid,) in audit_q]
        keep_ws = {p.workspace_path for p in s.query(RepairProposal).filter(RepairProposal.status == "validated")}
    orphan_ws = [d for d in settings.workspaces_dir.glob("repair-*")
                 if str(d) not in keep_ws and d.stat().st_mtime < (now - timedelta(days=1)).replace(
                     tzinfo=UTC).timestamp()] if settings.workspaces_dir.is_dir() else []
    files = [settings.reports_dir / f"scan-{sid}.{ext}" for sid in old_scans for ext in ("json", "html")]
    files += [Path(lp) for _, lp in old_jobs if lp and settings.logs_dir in Path(lp).parents]
    files = [f for f in files if f.is_file()]
    dirs = [d for d in (settings.reports_dir / "tests" / str(rid) for rid in old_runs) if d.is_dir()] + orphan_ws
    counts = {"jobs": len(old_jobs), "scans": len(old_scans), "test_runs": len(old_runs),
              "repairs": len(old_repairs), "audit_logs": len(old_audit), "files": len(files) + len(dirs)}
    if dry_run:
        return counts
    for f in files:
        f.unlink(missing_ok=True)
    for d in dirs:
        # Directory contents may be written by project code (test reports): never follow links.
        workspace.safe_rmtree(d, settings.data_dir)
    with write_session() as s:
        for model, ids in ((Scan, old_scans), (TestRun, old_runs), (RepairProposal, old_repairs),
                           (Job, [j for j, _ in old_jobs]), (AuditLog, old_audit)):
            for start in range(0, len(ids), 500):
                s.query(model).filter(model.id.in_(ids[start:start + 500])).delete(synchronize_session=False)
        audit.record("system.retention", detail={"days": cfg.retention_days,
                                                 "audit_days": cfg.audit_retention_days, **counts}, session=s)
    return counts
