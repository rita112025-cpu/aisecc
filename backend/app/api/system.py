from __future__ import annotations

import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, ValidationError
from sqlalchemy import func
from sqlalchemy.orm import Session

from app import __version__
from app.api.common import ORMModel
from app.db import get_db
from app.models import AuditLog, Finding, Job, Project, RepairProposal, Scan, TestRun
from app.services import audit
from app.services.analysis.tools import VENV_SCRIPTS
from app.services.maintenance import MaintenanceError, apply_retention, backup_db, list_backups, restore_db
from app.services.settings_store import AppSettings, load_settings, save_settings

router = APIRouter(tags=["system"])


class RestoreIn(BaseModel):
    name: str
    confirm: Literal[True]


class RetentionIn(BaseModel):
    confirm: Literal[True]
    dry_run: bool = False


class AuditOut(ORMModel):
    id: int
    created_at: datetime
    actor: str
    action: str
    target_type: str | None
    target_id: str | None
    project_id: int | None
    detail: dict[str, Any]


@router.get("/settings", response_model=AppSettings)
def get_settings_api() -> AppSettings:
    return load_settings()


@router.put("/settings", response_model=AppSettings)
def put_settings(patch: dict[str, Any]) -> AppSettings:
    unknown = set(patch) - set(AppSettings.model_fields)
    if unknown:
        raise HTTPException(400, f"未知的設定項目：{sorted(unknown)}")
    before = load_settings().model_dump()
    try:
        saved = save_settings(patch)
    except ValidationError as exc:
        raise HTTPException(422, "; ".join(e["msg"] for e in exc.errors())) from exc
    changed = {k: {"from": before[k], "to": v} for k, v in saved.model_dump().items() if before[k] != v}
    audit.record("settings.update", detail=changed)
    return saved


@router.get("/system/backups")
def get_backups(request: Request) -> list[dict[str, Any]]:
    return list_backups(request.app.state.settings)


@router.post("/system/backup", status_code=201)
def create_backup(request: Request) -> dict[str, Any]:
    result = backup_db(request.app.state.settings)
    audit.record("system.backup", detail=result)
    return result


@router.post("/system/restore")
def restore(body: RestoreIn, request: Request) -> dict[str, Any]:
    try:
        return restore_db(request.app.state.settings, body.name)
    except MaintenanceError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/system/retention")
def run_retention(body: RetentionIn, request: Request) -> dict[str, Any]:
    counts = apply_retention(request.app.state.settings, dry_run=body.dry_run)
    return {"dry_run": body.dry_run, **counts}


@router.get("/audit-logs")
def audit_logs(db: Session = Depends(get_db), project_id: int | None = None, action: str | None = None,
               q: str | None = Query(None, max_length=200), offset: int = Query(0, ge=0),
               limit: int = Query(100, ge=1, le=1000)) -> dict[str, Any]:
    query = db.query(AuditLog)
    if project_id is not None:
        query = query.filter(AuditLog.project_id == project_id)
    if action:
        query = query.filter(AuditLog.action.startswith(action, autoescape=True))
    if q:
        query = query.filter(AuditLog.action.contains(q, autoescape=True) | AuditLog.target_id.contains(
            q, autoescape=True))
    total = query.count()
    rows = query.order_by(AuditLog.id.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [AuditOut.model_validate(r).model_dump() for r in rows]}


@router.get("/dashboard")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    latest_analysis = (db.query(Scan.project_id, func.max(Scan.id)).filter(
        Scan.kind == "analysis", Scan.status.in_(("completed", "partial"))).group_by(Scan.project_id).all())
    scan_ids = [sid for _, sid in latest_analysis]
    open_by_sev = dict(db.query(Finding.severity, func.count(Finding.id)).filter(
        Finding.scan_id.in_(scan_ids), Finding.triage_status.in_(("open", "confirmed"))).group_by(
        Finding.severity).all()) if scan_ids else {}
    open_by_cat = dict(db.query(Finding.category, func.count(Finding.id)).filter(
        Finding.scan_id.in_(scan_ids), Finding.triage_status.in_(("open", "confirmed"))).group_by(
        Finding.category).all()) if scan_ids else {}
    projects = []
    for p in db.query(Project).order_by(Project.name):
        inv = db.query(Scan).filter_by(project_id=p.id, kind="inventory").order_by(Scan.id.desc()).first()
        run = db.query(TestRun).filter_by(project_id=p.id).order_by(TestRun.id.desc()).first()
        projects.append({
            "id": p.id, "name": p.name, "health_score": inv.health_score if inv else None,
            "last_scan_at": p.last_scan_at, "languages": p.languages,
            "last_test": {"id": run.id, "status": run.status, "passed": run.passed, "failed": run.failed,
                          "total": run.total} if run else None,
        })
    return {
        "counts": {
            "projects": db.query(Project).count(),
            "active_jobs": db.query(Job).filter(Job.status.in_(("queued", "running"))).count(),
            "failed_jobs_24h": db.query(Job).filter(Job.status.in_(("failed", "timeout"))).count(),
            "repairs_pending": db.query(RepairProposal).filter(RepairProposal.status == "validated").count(),
        },
        "open_findings_by_severity": open_by_sev,
        "open_findings_by_category": open_by_cat,
        "projects": projects,
        "recent_jobs": [{"id": j.id, "type": j.type, "status": j.status, "project_id": j.project_id,
                         "created_at": j.created_at, "progress": j.progress, "message": j.message}
                        for j in db.query(Job).order_by(Job.created_at.desc()).limit(10)],
    }


def _version(cmd: list[str]) -> str | None:
    exe = shutil.which(cmd[0]) if not cmd[0].endswith(".exe") or "\\" not in cmd[0] else cmd[0]
    if not exe:
        return None
    try:
        out = subprocess.run([exe, *cmd[1:]], capture_output=True, timeout=15, text=True,  # noqa: S603
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (out.stdout or out.stderr).strip().splitlines()
    return text[0][:120] if text else None


@router.get("/system/tools")
def tools() -> dict[str, Any]:
    exe = ".exe" if sys.platform == "win32" else ""
    return {
        "version": __version__,
        "python": sys.version.split()[0],
        "tools": {
            "git": _version(["git", "--version"]),
            "node": _version(["node", "--version"]),
            "dotnet": _version(["dotnet", "--version"]),
            "ruff": _version([str(VENV_SCRIPTS / f"ruff{exe}"), "--version"]),
            "bandit": _version([str(VENV_SCRIPTS / f"bandit{exe}"), "--version"]),
            "pip-audit": _version([str(VENV_SCRIPTS / f"pip-audit{exe}"), "--version"]),
            "semgrep": _version(["semgrep", "--version"]),
        },
    }
