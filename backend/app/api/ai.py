from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import ORMModel, get_project_or_404, job_manager, project_root
from app.db import get_db
from app.models import RepairProposal
from app.services.ai import repair
from app.services.ai.providers import AINotConfigured, AIProvider, build_provider
from app.services.jobs import JobManager
from app.services.settings_store import load_settings

router = APIRouter(tags=["ai"])


class RepairIn(BaseModel):
    finding_id: int | None = None
    target_file: str | None = Field(default=None, max_length=1024)
    instruction: str = Field(default="", max_length=4000)
    test_command_key: str | None = Field(default=None, max_length=60)


class ApplyIn(BaseModel):
    confirm: Literal[True]
    diff_sha256: str = Field(min_length=64, max_length=64)


class RepairOut(ORMModel):
    id: int
    project_id: int
    finding_id: int | None
    job_id: str | None
    provider: str
    model: str | None
    status: str
    target_file: str | None
    instruction: str
    summary: str | None
    root_cause: str | None
    diff: str | None
    files: list[Any]
    base_commit: str | None
    validation: dict[str, Any]
    risk: dict[str, Any]
    test_run_id: int | None
    error: str | None
    created_at: datetime
    applied_at: datetime | None
    reverted_at: datetime | None


def _provider(request: Request) -> AIProvider:
    """app.state.ai_provider_factory lets tests inject a fake provider."""
    factory = getattr(request.app.state, "ai_provider_factory", None)
    try:
        return factory() if factory else build_provider(load_settings())
    except AINotConfigured as exc:
        raise HTTPException(409, str(exc)) from exc


@router.get("/ai/status")
def ai_status(request: Request) -> dict[str, Any]:
    try:
        provider = _provider(request)
    except HTTPException as exc:
        return {"configured": False, "reason": exc.detail}
    return {"configured": True, "external": provider.external, **provider.status()}


@router.post("/projects/{project_id}/repairs", status_code=202)
def create_repair(project_id: int, body: RepairIn, request: Request, db: Session = Depends(get_db),
                  jobs: JobManager = Depends(job_manager)) -> dict[str, Any]:
    project_root(get_project_or_404(db, project_id))
    provider = _provider(request)
    try:
        pid = repair.create_proposal(project_id, finding_id=body.finding_id, target_file=body.target_file,
                                     line=None, instruction=body.instruction,
                                     test_command_key=body.test_command_key, provider_name=provider.name)
    except repair.RepairError as exc:
        raise HTTPException(400, str(exc)) from exc
    job_id = jobs.submit("ai_repair", lambda ctx: repair.generate(pid, ctx, lambda: provider),
                         project_id=project_id, params={"proposal_id": pid}, dedupe_key=f"repair:{pid}")
    return {"proposal_id": pid, "job_id": job_id}


@router.get("/repairs", response_model=list[RepairOut])
def list_repairs(db: Session = Depends(get_db), project_id: int | None = None, status: str | None = None,
                 limit: int = Query(100, ge=1, le=1000)) -> list[RepairProposal]:
    q = db.query(RepairProposal)
    if project_id is not None:
        q = q.filter(RepairProposal.project_id == project_id)
    if status:
        q = q.filter(RepairProposal.status.in_(status.split(",")))
    return q.order_by(RepairProposal.id.desc()).limit(limit).all()


@router.get("/repairs/{proposal_id}", response_model=RepairOut)
def get_repair(proposal_id: int, db: Session = Depends(get_db)) -> RepairProposal:
    p = db.get(RepairProposal, proposal_id)
    if p is None:
        raise HTTPException(404, "修復建議不存在")
    return p


def _act(fn, *args) -> dict[str, Any]:  # noqa: ANN001 - small dispatcher
    try:
        return fn(*args)
    except repair.RepairError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/repairs/{proposal_id}/apply")
def apply_repair(proposal_id: int, body: ApplyIn) -> dict[str, Any]:
    return _act(repair.apply, proposal_id, body.diff_sha256)


@router.post("/repairs/{proposal_id}/revert")
def revert_repair(proposal_id: int) -> dict[str, Any]:
    return _act(repair.revert, proposal_id)


@router.post("/repairs/{proposal_id}/reject")
def reject_repair(proposal_id: int) -> dict[str, Any]:
    return _act(repair.reject, proposal_id)
