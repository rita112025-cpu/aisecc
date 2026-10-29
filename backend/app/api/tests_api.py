from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.api.common import JobRef, ORMModel, get_project_or_404, job_manager, project_root
from app.db import get_db, write_session
from app.models import TestCommand, TestResult, TestRun, utcnow
from app.services import audit
from app.services.detect import detect
from app.services.fswalk import resolve_inside, walk
from app.services.jobs import JobManager
from app.services.testing.commands import ENV_NAME, FRAMEWORKS, KEY_PATTERN, command_hash, suggest_commands
from app.services.testing.failures import compare_runs
from app.services.testing.runner import CommandNotApproved, approved_command, run_tests

router = APIRouter(tags=["tests"])


class CommandOut(ORMModel):
    id: int
    project_id: int
    key: str
    framework: str
    argv: list[str]
    cwd: str
    timeout_sec: int
    max_memory_mb: int
    extra_env_names: list[str]
    inherit_full_env: bool
    approved: bool
    approved_at: datetime | None
    source: str
    updated_at: datetime
    content_hash: str = ""


class CommandIn(BaseModel):
    framework: str
    argv: list[str] = Field(min_length=1, max_length=50)
    cwd: str = "."
    timeout_sec: int = Field(600, ge=5, le=4 * 3600)
    max_memory_mb: int = Field(2048, ge=128, le=65536)
    extra_env_names: list[str] = Field(default_factory=list, max_length=50)
    inherit_full_env: bool = False

    @field_validator("extra_env_names")
    @classmethod
    def _env_names(cls, v: list[str]) -> list[str]:
        bad = [n for n in v if not ENV_NAME.match(n) or n.upper().startswith("AISECC_")]
        if bad:
            raise ValueError(f"不合法的環境變數名稱：{bad}")
        return sorted({n.upper() for n in v})

    @field_validator("framework")
    @classmethod
    def _fw(cls, v: str) -> str:
        if v not in FRAMEWORKS:
            raise ValueError(f"不支援的框架：{v}")
        return v

    @field_validator("argv")
    @classmethod
    def _argv(cls, v: list[str]) -> list[str]:
        if any(not a or len(a) > 1000 or "\x00" in a or "\n" in a for a in v):
            raise ValueError("指令參數不可為空、過長或含控制字元")
        return v


class ApproveIn(BaseModel):
    # sha256 of the command content the user reviewed; prevents approving a different version.
    content_hash: str = Field(min_length=64, max_length=64)


class RunIn(BaseModel):
    command_key: str
    coverage: bool = False


class TestRunOut(ORMModel):
    id: int
    project_id: int
    job_id: str | None
    command_key: str
    framework: str
    argv: list[Any]
    workdir: str
    branch: str | None
    commit_sha: str | None
    status: str
    exit_code: int | None
    total: int
    passed: int
    failed: int
    skipped: int
    errors: int
    duration_sec: float | None
    coverage_percent: float | None
    coverage: dict[str, Any] | None
    failure_analysis: list[Any]
    rerun_of_id: int | None
    created_at: datetime
    finished_at: datetime | None


class TestResultOut(ORMModel):
    id: int
    name: str
    classname: str | None
    file: str | None
    status: str
    duration_sec: float | None
    message: str | None


def _cmd_out(c: TestCommand) -> CommandOut:
    out = CommandOut.model_validate(c)
    out.content_hash = command_hash(c)
    out.approved = c.approved and c.approved_hash == out.content_hash
    return out


def _get_cmd(db: Session, project_id: int, key: str) -> TestCommand:
    c = db.query(TestCommand).filter_by(project_id=project_id, key=key).one_or_none()
    if c is None:
        raise HTTPException(404, "測試指令不存在")
    return c


@router.get("/projects/{project_id}/test-commands", response_model=list[CommandOut])
def list_commands(project_id: int, db: Session = Depends(get_db)) -> list[CommandOut]:
    get_project_or_404(db, project_id)
    return [_cmd_out(c) for c in db.query(TestCommand).filter_by(project_id=project_id).order_by(TestCommand.key)]


@router.post("/projects/{project_id}/test-commands/detect", response_model=list[CommandOut])
def detect_commands(project_id: int, db: Session = Depends(get_db)) -> list[CommandOut]:
    """Reads project files to suggest commands. Suggestions are stored unapproved; nothing is executed."""
    root = project_root(get_project_or_404(db, project_id))
    walked = walk(root, max_files=50_000)
    specs = suggest_commands(root, detect(root, walked), [f.rel for f in walked.files])
    with write_session() as s:
        existing = {c.key for c in s.query(TestCommand).filter_by(project_id=project_id)}
        for spec in specs:
            if spec.key not in existing:
                s.add(TestCommand(project_id=project_id, key=spec.key, framework=spec.framework, argv=spec.argv,
                                  cwd=spec.cwd, timeout_sec=spec.timeout_sec, max_memory_mb=spec.max_memory_mb,
                                  extra_env_names=[], inherit_full_env=False, approved=False, source="detected"))
        audit.record("test_command.detect", target_type="project", target_id=project_id, project_id=project_id,
                     detail={"suggested": [s_.key for s_ in specs]}, session=s)
    db.rollback()
    return list_commands(project_id, db)


@router.put("/projects/{project_id}/test-commands/{key}", response_model=CommandOut)
def upsert_command(project_id: int, key: str, body: CommandIn, db: Session = Depends(get_db)) -> CommandOut:
    root = project_root(get_project_or_404(db, project_id))
    if not KEY_PATTERN.match(key):
        raise HTTPException(400, "指令名稱只能使用小寫英數、. _ -")
    if body.cwd not in ("", "."):
        try:
            if not resolve_inside(root, body.cwd).is_dir():
                raise HTTPException(400, "工作目錄不是資料夾")
        except (ValueError, FileNotFoundError) as exc:
            raise HTTPException(400, str(exc)) from exc
    with write_session() as s:
        c = s.query(TestCommand).filter_by(project_id=project_id, key=key).one_or_none()
        if c is None:
            c = TestCommand(project_id=project_id, key=key, source="user")
            s.add(c)
        c.framework, c.argv, c.cwd = body.framework, body.argv, body.cwd or "."
        c.timeout_sec, c.max_memory_mb = body.timeout_sec, body.max_memory_mb
        c.extra_env_names, c.inherit_full_env = body.extra_env_names, body.inherit_full_env
        # Any edit invalidates a previous approval.
        c.approved, c.approved_hash, c.approved_at = False, None, None
        c.updated_at = utcnow()
        audit.record("test_command.update", target_type="test_command", target_id=key, project_id=project_id,
                     detail=body.model_dump(), session=s)
    db.rollback()
    return _cmd_out(_get_cmd(db, project_id, key))


@router.post("/projects/{project_id}/test-commands/{key}/approve", response_model=CommandOut)
def approve_command(project_id: int, key: str, body: ApproveIn, db: Session = Depends(get_db)) -> CommandOut:
    c = _get_cmd(db, project_id, key)
    current = command_hash(c)
    if body.content_hash != current:
        raise HTTPException(409, "指令內容已變更，請重新檢視後再批准")
    with write_session() as s:
        row = s.get(TestCommand, c.id)
        row.approved, row.approved_hash, row.approved_at = True, current, utcnow()
        audit.record("test_command.approve", target_type="test_command", target_id=key, project_id=project_id,
                     detail={"argv": row.argv, "cwd": row.cwd, "hash": current}, session=s)
    db.rollback()
    return _cmd_out(_get_cmd(db, project_id, key))


@router.post("/projects/{project_id}/test-commands/{key}/revoke", response_model=CommandOut)
def revoke_command(project_id: int, key: str, db: Session = Depends(get_db)) -> CommandOut:
    c = _get_cmd(db, project_id, key)
    with write_session() as s:
        row = s.get(TestCommand, c.id)
        row.approved, row.approved_hash, row.approved_at = False, None, None
        audit.record("test_command.revoke", target_type="test_command", target_id=key, project_id=project_id,
                     session=s)
    db.rollback()
    return _cmd_out(_get_cmd(db, project_id, key))


@router.delete("/projects/{project_id}/test-commands/{key}", status_code=204, response_class=Response,
               response_model=None)
def delete_command(project_id: int, key: str, db: Session = Depends(get_db)) -> None:
    c = _get_cmd(db, project_id, key)
    with write_session() as s:
        s.delete(s.get(TestCommand, c.id))
        audit.record("test_command.delete", target_type="test_command", target_id=key, project_id=project_id,
                     session=s)


def _submit_run(jobs: JobManager, project_id: int, key: str, coverage: bool, rerun_of: int | None) -> str:
    try:
        approved_command(project_id, key)
    except CommandNotApproved as exc:
        raise HTTPException(403, str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc
    return jobs.submit(
        "test_run", lambda ctx: run_tests(project_id, key, ctx, coverage=coverage, rerun_of=rerun_of),
        project_id=project_id, params={"command_key": key, "coverage": coverage, "rerun_of": rerun_of},
        dedupe_key=f"test:{project_id}:{key}",
    )


@router.post("/projects/{project_id}/test-runs", response_model=JobRef, status_code=202)
def start_test_run(project_id: int, body: RunIn, db: Session = Depends(get_db),
                   jobs: JobManager = Depends(job_manager)) -> JobRef:
    project_root(get_project_or_404(db, project_id))
    return JobRef(job_id=_submit_run(jobs, project_id, body.command_key, body.coverage, None))


@router.get("/test-runs", response_model=list[TestRunOut])
def list_runs(db: Session = Depends(get_db), project_id: int | None = None, command_key: str | None = None,
              status: str | None = None, limit: int = Query(100, ge=1, le=1000)) -> list[TestRun]:
    q = db.query(TestRun)
    if project_id is not None:
        q = q.filter(TestRun.project_id == project_id)
    if command_key:
        q = q.filter(TestRun.command_key == command_key)
    if status:
        q = q.filter(TestRun.status.in_(status.split(",")))
    return q.order_by(TestRun.id.desc()).limit(limit).all()


def _run(db: Session, run_id: int) -> TestRun:
    r = db.get(TestRun, run_id)
    if r is None:
        raise HTTPException(404, "測試執行紀錄不存在")
    return r


@router.get("/test-runs/compare")
def compare_test_runs(a: int, b: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    ra, rb = _run(db, a), _run(db, b)
    if ra.project_id != rb.project_id:
        raise HTTPException(400, "只能比較同一專案的測試")

    def statuses(run_id: int) -> dict[str, str]:
        return {r.name: r.status for r in db.query(TestResult.name, TestResult.status).filter_by(run_id=run_id)}

    return {"a": {"id": a, "commit": ra.commit_sha, "status": ra.status},
            "b": {"id": b, "commit": rb.commit_sha, "status": rb.status},
            "diff": compare_runs(statuses(a), statuses(b))}


@router.get("/test-runs/{run_id}", response_model=TestRunOut)
def get_run(run_id: int, db: Session = Depends(get_db)) -> TestRun:
    return _run(db, run_id)


@router.get("/test-runs/{run_id}/results")
def run_results(run_id: int, db: Session = Depends(get_db), status: str | None = None,
                q: str | None = Query(None, max_length=200), offset: int = Query(0, ge=0),
                limit: int = Query(200, ge=1, le=2000)) -> dict[str, Any]:
    _run(db, run_id)
    query = db.query(TestResult).filter(TestResult.run_id == run_id)
    if status:
        query = query.filter(TestResult.status.in_(status.split(",")))
    if q:
        query = query.filter(TestResult.name.contains(q, autoescape=True))
    total = query.count()
    order = {"error": 0, "failed": 1, "skipped": 2, "passed": 3}
    rows = sorted(query.all(), key=lambda r: (order.get(r.status, 9), r.name))[offset:offset + limit]
    return {"total": total, "items": [TestResultOut.model_validate(r).model_dump() for r in rows]}


@router.post("/test-runs/{run_id}/rerun-failed", response_model=JobRef, status_code=202)
def rerun_failed(run_id: int, db: Session = Depends(get_db), jobs: JobManager = Depends(job_manager)) -> JobRef:
    run = _run(db, run_id)
    if not run.failed and not run.errors:
        raise HTTPException(400, "這次執行沒有失敗的測試")
    project_root(get_project_or_404(db, run.project_id))
    return JobRef(job_id=_submit_run(jobs, run.project_id, run.command_key, False, run_id))
