from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.common import JobRef, ORMModel, get_project_or_404, job_manager, project_root
from app.db import get_db, write_session
from app.models import Project, Scan
from app.services import audit, gitinfo
from app.services.analysis.engine import run_analysis_scan
from app.services.analysis.tools import TOOLS
from app.services.fswalk import resolve_inside
from app.services.inventory import run_inventory_scan
from app.services.jobs import JobManager
from app.services.projects import ProjectError, add_project, list_dir

router = APIRouter(prefix="/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    path: str = Field(min_length=1, max_length=1024)
    name: str | None = Field(default=None, max_length=200)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    allow_project_tools: bool | None = None
    allow_network_audit: bool | None = None


class AnalyzeIn(BaseModel):
    tools: list[str] | None = None


class ProjectOut(ORMModel):
    id: int
    name: str
    path: str
    languages: list[Any]
    is_git: bool
    allow_project_tools: bool
    allow_network_audit: bool
    created_at: datetime
    last_scan_at: datetime | None
    health_score: float | None = None
    path_exists: bool = True


def _out(db: Session, p: Project) -> ProjectOut:
    from pathlib import Path

    latest = (
        db.query(Scan.health_score)
        .filter(Scan.project_id == p.id, Scan.kind == "inventory", Scan.health_score.isnot(None))
        .order_by(Scan.id.desc())
        .first()
    )
    out = ProjectOut.model_validate(p)
    out.health_score = latest[0] if latest else None
    out.path_exists = Path(p.path).is_dir()
    return out


@router.get("", response_model=list[ProjectOut])
def list_projects(db: Session = Depends(get_db)) -> list[ProjectOut]:
    return [_out(db, p) for p in db.query(Project).order_by(Project.name).all()]


@router.post("", response_model=ProjectOut, status_code=201)
def create_project(body: ProjectCreate, db: Session = Depends(get_db)) -> ProjectOut:
    try:
        project = add_project(body.path, body.name)
    except ProjectError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _out(db, db.get(Project, project.id))


@router.get("/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: Session = Depends(get_db)) -> ProjectOut:
    return _out(db, get_project_or_404(db, project_id))


@router.patch("/{project_id}", response_model=ProjectOut)
def patch_project(project_id: int, body: ProjectPatch, db: Session = Depends(get_db)) -> ProjectOut:
    get_project_or_404(db, project_id)
    changes = body.model_dump(exclude_none=True)
    with write_session() as s:
        p = s.get(Project, project_id)
        for k, v in changes.items():
            setattr(p, k, v)
        audit.record("project.update", target_type="project", target_id=project_id, project_id=project_id,
                     detail=changes, session=s)
    db.rollback()  # end the read snapshot so the committed write is visible
    return _out(db, get_project_or_404(db, project_id))


@router.delete("/{project_id}", status_code=204, response_class=Response, response_model=None)
def delete_project(project_id: int, db: Session = Depends(get_db)) -> None:
    """Removes the project's records from the control center only; files on disk are never touched."""
    p = get_project_or_404(db, project_id)
    path = p.path
    with write_session() as s:
        s.delete(s.get(Project, project_id))
        audit.record("project.remove", target_type="project", target_id=project_id, project_id=project_id,
                     detail={"path": path}, session=s)


@router.get("/{project_id}/tree")
def project_tree(project_id: int, path: str = "", db: Session = Depends(get_db)) -> dict[str, Any]:
    root = project_root(get_project_or_404(db, project_id))
    try:
        return list_dir(root, path)
    except (ProjectError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except FileNotFoundError as exc:
        raise HTTPException(404, "路徑不存在") from exc


@router.get("/{project_id}/git")
def project_git(project_id: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    project = get_project_or_404(db, project_id)
    return gitinfo.get_info(project_root(project), project.allow_project_tools).to_dict()


@router.get("/{project_id}/git/log")
def project_git_log(project_id: int, limit: int = Query(50, ge=1, le=500), db: Session = Depends(get_db)) -> list[
        dict[str, str]]:
    root = project_root(get_project_or_404(db, project_id))
    if not gitinfo.is_git_repo(root):
        raise HTTPException(400, "不是 Git 儲存庫")
    return gitinfo.log(root, limit)


@router.get("/{project_id}/git/diff")
def project_git_diff(
    project_id: int,
    base: str | None = None,
    target: str | None = None,
    path: str | None = None,
    db: Session = Depends(get_db),
) -> dict[str, Any]:
    project = get_project_or_404(db, project_id)
    root = project_root(project)
    if not gitinfo.is_git_repo(root):
        raise HTTPException(400, "不是 Git 儲存庫")
    if path:
        try:
            resolve_inside(root, path, must_exist=False)
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
    try:
        return gitinfo.diff(root, base=base, target=target, path=path, allow_repo_config=project.allow_project_tools)
    except gitinfo.GitUntrustedConfig as exc:
        raise HTTPException(409, str(exc)) from exc
    except gitinfo.GitError as exc:
        raise HTTPException(400, str(exc)) from exc


@router.post("/{project_id}/scan", response_model=JobRef, status_code=202)
def start_inventory_scan(project_id: int, db: Session = Depends(get_db),
                         jobs: JobManager = Depends(job_manager)) -> JobRef:
    project_root(get_project_or_404(db, project_id))
    job_id = jobs.submit(
        "inventory_scan", lambda ctx: run_inventory_scan(project_id, ctx),
        project_id=project_id, dedupe_key=f"inventory:{project_id}",
    )
    return JobRef(job_id=job_id)


@router.post("/{project_id}/analyze", response_model=JobRef, status_code=202)
def start_analysis(project_id: int, body: AnalyzeIn | None = None, db: Session = Depends(get_db),
                   jobs: JobManager = Depends(job_manager)) -> JobRef:
    project_root(get_project_or_404(db, project_id))
    tools = body.tools if body else None
    unknown = set(tools or []) - {t.name for t in TOOLS}
    if unknown:
        raise HTTPException(400, f"未知的工具：{sorted(unknown)}")
    job_id = jobs.submit(
        "analysis_scan", lambda ctx: run_analysis_scan(project_id, ctx, tools),
        project_id=project_id, params={"tools": tools}, dedupe_key=f"analysis:{project_id}",
    )
    return JobRef(job_id=job_id)
