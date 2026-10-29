"""ORM models. Every schema change must go through an Alembic migration."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(200))
    # Normalised absolute path (case-folded on Windows) used for duplicate detection.
    path: Mapped[str] = mapped_column(String(1024))
    path_key: Mapped[str] = mapped_column(String(1024), unique=True)
    languages: Mapped[list[Any]] = mapped_column(JSON, default=list)
    is_git: Mapped[bool] = mapped_column(Boolean, default=False)
    # Allows running project-provided tools (eslint/tsc from node_modules), which execute project code.
    allow_project_tools: Mapped[bool] = mapped_column(Boolean, default=False)
    # Allows sending dependency lists to package registries / fetching rules (npm audit, pip-audit, semgrep).
    allow_network_audit: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
    last_scan_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    scans: Mapped[list[Scan]] = relationship(back_populates="project", cascade="all, delete-orphan")


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_status_created", "status", "created_at"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    type: Mapped[str] = mapped_column(String(40), index=True)
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("projects.id", ondelete="CASCADE"), nullable=True, index=True
    )
    # queued | running | succeeded | failed | cancelled | timeout | interrupted
    status: Mapped[str] = mapped_column(String(20), default="queued")
    # Identical active jobs (same dedupe_key) are rejected to avoid duplicate runs.
    dedupe_key: Mapped[str | None] = mapped_column(String(200), nullable=True, index=True)
    progress: Mapped[float] = mapped_column(Float, default=0.0)
    message: Mapped[str] = mapped_column(Text, default="")
    params: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    result: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    cancel_requested: Mapped[bool] = mapped_column(Boolean, default=False)
    log_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # Root pid of the external process tree (if any) and its create_time, to kill orphans
    # after a crash without hitting a re-used PID.
    pid: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pid_started_at: Mapped[float | None] = mapped_column(Float, nullable=True)
    heartbeat_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class Scan(Base):
    __tablename__ = "scans"
    __table_args__ = (Index("ix_scans_project_created", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    # inventory (files/deps/git) | analysis (static + security analysers)
    kind: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="running")
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    summary: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    # Per-tool execution state: ok | not_installed | requires_authorization | error | skipped
    tool_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    health_score: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    project: Mapped[Project] = relationship(back_populates="scans")
    findings: Mapped[list[Finding]] = relationship(back_populates="scan", cascade="all, delete-orphan")


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (
        UniqueConstraint("scan_id", "fingerprint", "occurrence", name="uq_findings_scan_fp_occ"),
        Index("ix_findings_project_fp", "project_id", "fingerprint"),
        Index("ix_findings_scan_severity", "scan_id", "severity"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    scan_id: Mapped[int] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"))
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    tool: Mapped[str] = mapped_column(String(40))
    rule_id: Mapped[str] = mapped_column(String(120))
    # critical | high | medium | low | info
    severity: Mapped[str] = mapped_column(String(10))
    # lint | type | security | secret | dependency | complexity | duplication | cycle | unused
    category: Mapped[str] = mapped_column(String(20))
    # reported (tool said so) | heuristic (our own guess); neither is a confirmed defect.
    confidence: Mapped[str] = mapped_column(String(12), default="reported")
    file_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    column: Mapped[int | None] = mapped_column(Integer, nullable=True)
    end_line: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str] = mapped_column(Text)
    # Line-independent identity (tool+rule+path+hash of normalised source line) for cross-scan matching.
    fingerprint: Mapped[str] = mapped_column(String(64))
    # Ordinal among identical fingerprints within one scan, so repeated hits are all kept.
    occurrence: Mapped[int] = mapped_column(Integer, default=0)
    # open | false_positive | accepted_risk | confirmed | fixed
    triage_status: Mapped[str] = mapped_column(String(20), default="open")
    triage_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    extra: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    scan: Mapped[Scan] = relationship(back_populates="findings")


class FindingSuppression(Base):
    """Triage decisions carried forward to later scans by fingerprint."""

    __tablename__ = "finding_suppressions"
    __table_args__ = (UniqueConstraint("project_id", "fingerprint", name="uq_suppression_project_fp"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    fingerprint: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20))
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class TestCommand(Base):
    __test__ = False  # not a pytest test class
    """A test command the user reviewed. Only approved commands can be executed."""

    __tablename__ = "test_commands"
    __table_args__ = (UniqueConstraint("project_id", "key", name="uq_test_command_key"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    key: Mapped[str] = mapped_column(String(60))
    framework: Mapped[str] = mapped_column(String(20))
    argv: Mapped[list[Any]] = mapped_column(JSON)
    cwd: Mapped[str] = mapped_column(String(1024), default=".")
    timeout_sec: Mapped[int] = mapped_column(Integer, default=600)
    max_memory_mb: Mapped[int] = mapped_column(Integer, default=2048)
    # Environment: a minimal system allowlist plus these explicitly named variables, unless the
    # user opts into inheriting everything. Both are part of the approval hash.
    extra_env_names: Mapped[list[Any]] = mapped_column(JSON, default=list, server_default="[]")
    inherit_full_env: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    # sha256 of argv/cwd at approval time; any edit invalidates the approval.
    approved_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="detected")
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


class TestRun(Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "test_runs"
    __table_args__ = (Index("ix_test_runs_project_created", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    command_key: Mapped[str] = mapped_column(String(60))
    framework: Mapped[str] = mapped_column(String(20))
    argv: Mapped[list[Any]] = mapped_column(JSON)
    workdir: Mapped[str] = mapped_column(String(1024))
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    commit_sha: Mapped[str | None] = mapped_column(String(64), nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    exit_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total: Mapped[int] = mapped_column(Integer, default=0)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    failed: Mapped[int] = mapped_column(Integer, default=0)
    skipped: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[int] = mapped_column(Integer, default=0)
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    coverage: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    failure_analysis: Mapped[list[Any]] = mapped_column(JSON, default=list)
    rerun_of_id: Mapped[int | None] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    results: Mapped[list[TestResult]] = relationship(back_populates="run", cascade="all, delete-orphan")


class TestResult(Base):
    __test__ = False  # not a pytest test class
    __tablename__ = "test_results"
    __table_args__ = (
        UniqueConstraint("run_id", "name", name="uq_test_result_name"),
        Index("ix_test_results_run_status", "run_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("test_runs.id", ondelete="CASCADE"))
    name: Mapped[str] = mapped_column(String(1024))
    classname: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    # passed | failed | error | skipped
    status: Mapped[str] = mapped_column(String(10))
    duration_sec: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)

    run: Mapped[TestRun] = relationship(back_populates="results")


class RepairProposal(Base):
    __tablename__ = "repair_proposals"
    __table_args__ = (Index("ix_repair_project_created", "project_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"))
    finding_id: Mapped[int | None] = mapped_column(ForeignKey("findings.id", ondelete="SET NULL"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True)
    provider: Mapped[str] = mapped_column(String(40))
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    # generating | invalid | validated | applied | rejected | reverted | failed
    status: Mapped[str] = mapped_column(String(20), default="generating")
    target_file: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    instruction: Mapped[str] = mapped_column(Text, default="")
    summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    root_cause: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_response: Mapped[str | None] = mapped_column(Text, nullable=True)
    diff: Mapped[str | None] = mapped_column(Text, nullable=True)
    # [{path, before_sha256, after_sha256}]
    files: Mapped[list[Any]] = mapped_column(JSON, default=list)
    base_commit: Mapped[str | None] = mapped_column(String(64), nullable=True)
    workspace_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    validation: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    risk: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    test_run_id: Mapped[int | None] = mapped_column(ForeignKey("test_runs.id", ondelete="SET NULL"), nullable=True)
    backup_path: Mapped[str | None] = mapped_column(String(1024), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    applied_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    reverted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (Index("ix_audit_created", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    actor: Mapped[str] = mapped_column(String(60), default="local-user")
    action: Mapped[str] = mapped_column(String(60), index=True)
    target_type: Mapped[str | None] = mapped_column(String(40), nullable=True)
    target_id: Mapped[str | None] = mapped_column(String(60), nullable=True)
    project_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(80), primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)
