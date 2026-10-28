"""Shared API helpers and response schemas."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.models import Job, Project
from app.services.jobs import JobManager


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class JobOut(ORMModel):
    id: str
    type: str
    project_id: int | None
    status: str
    progress: float
    message: str
    params: dict[str, Any]
    result: dict[str, Any] | None
    error: str | None
    cancel_requested: bool
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class JobRef(BaseModel):
    job_id: str


def job_manager(request: Request) -> JobManager:
    return request.app.state.jobs


def get_project_or_404(db: Session, project_id: int) -> Project:
    project = db.get(Project, project_id)
    if project is None:
        raise HTTPException(404, "專案不存在")
    return project


def project_root(project: Project) -> Path:
    root = Path(project.path)
    if not root.is_dir():
        raise HTTPException(410, f"專案路徑已不存在：{project.path}")
    return root


def get_job_or_404(db: Session, job_id: str) -> Job:
    job = db.get(Job, job_id)
    if job is None:
        raise HTTPException(404, "任務不存在")
    return job
