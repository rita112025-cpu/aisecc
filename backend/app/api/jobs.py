from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.api.common import JobOut, get_job_or_404, job_manager
from app.db import get_db
from app.models import Job
from app.services.jobs import JobManager, read_log

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobOut])
def list_jobs(
    db: Session = Depends(get_db),
    project_id: int | None = None,
    status: str | None = None,
    type: str | None = None,  # noqa: A002 - query parameter name
    limit: int = Query(100, ge=1, le=1000),
) -> list[Job]:
    q = db.query(Job)
    if project_id is not None:
        q = q.filter(Job.project_id == project_id)
    if status:
        q = q.filter(Job.status.in_(status.split(",")))
    if type:
        q = q.filter(Job.type == type)
    return q.order_by(Job.created_at.desc()).limit(limit).all()


@router.get("/{job_id}", response_model=JobOut)
def get_job(job_id: str, db: Session = Depends(get_db)) -> Job:
    return get_job_or_404(db, job_id)


@router.get("/{job_id}/log")
def get_job_log(job_id: str, offset: int = Query(0, ge=0), db: Session = Depends(get_db)) -> dict[str, Any]:
    job = get_job_or_404(db, job_id)
    text, next_offset = read_log(job.log_path, offset)
    return {"text": text, "next_offset": next_offset, "done": job.status not in ("queued", "running")}


@router.post("/{job_id}/cancel", response_model=JobOut)
def cancel_job(job_id: str, db: Session = Depends(get_db), jobs: JobManager = Depends(job_manager)) -> Job:
    get_job_or_404(db, job_id)
    if not jobs.cancel(job_id):
        raise HTTPException(409, "任務已結束，無法取消")
    db.rollback()  # end the read snapshot so the committed write is visible
    return get_job_or_404(db, job_id)
