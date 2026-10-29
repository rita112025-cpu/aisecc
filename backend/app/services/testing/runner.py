"""Authorised test execution (module C). Only approved, unmodified commands ever run."""

from __future__ import annotations

import os
import xml.etree.ElementTree as ET  # noqa: S405
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db import read_session, write_session
from app.models import Project, TestCommand, TestResult, TestRun, utcnow
from app.services import audit, gitinfo
from app.services.ai import workspace as repair_workspace
from app.services.analysis.secrets import redact
from app.services.fswalk import resolve_inside
from app.services.jobs import JobCancelled, JobContext, JobTimeout
from app.services.procs import RunOptions, run_command
from app.services.testing import failures, reports
from app.services.testing.commands import child_env, command_hash, reporter_args, rerun_args

RESULT_BATCH = 500


class CommandNotApproved(PermissionError):
    pass


def approved_command(project_id: int, key: str) -> TestCommand:
    with read_session() as s:
        cmd = s.query(TestCommand).filter_by(project_id=project_id, key=key).one_or_none()
        if cmd is None:
            raise ValueError(f"找不到測試指令：{key}")
        current = command_hash(cmd)
        if not cmd.approved or cmd.approved_hash != current:
            raise CommandNotApproved("測試指令尚未批准，或批准後已被修改；請重新審核")
        s.expunge(cmd)
        return cmd


def _redact(text: str) -> str:
    return redact(text, set())


def _has_pytest_cov(root: Path) -> bool:
    from app.services.detect import detect
    from app.services.fswalk import walk

    det = detect(root, walk(root, max_files=20_000))
    return any(d.name.lower() == "pytest-cov" for d in det.dependencies)


def run_tests(project_id: int, key: str, ctx: JobContext, *, coverage: bool = False,
              rerun_of: int | None = None, workspace: Path | None = None) -> dict[str, Any]:
    """Run an approved command. With `workspace`, the same command runs inside an isolated repair
    workspace (dependency folders junctioned in) instead of the real project."""
    cmd = approved_command(project_id, key)
    if workspace is not None:
        # Only the repair module passes a workspace; it must be one of ours.
        ws_root = Path(os.path.realpath(get_settings().workspaces_dir))
        if ws_root not in Path(os.path.realpath(workspace)).parents:
            raise ValueError("工作區必須位於 data/workspaces 之下")
    with read_session() as s:
        project = s.get(Project, project_id)
        root = Path(project.path)
        allow_cfg = project.allow_project_tools
    base_dir = root if workspace is None else workspace
    if workspace is not None:
        ctx.log(f"連結依賴資料夾：{repair_workspace.link_dependencies(root, workspace, cmd.cwd)}")
    workdir = resolve_inside(base_dir, cmd.cwd) if cmd.cwd not in ("", ".") else Path(os.path.realpath(base_dir))
    selection: list[str] = []
    if rerun_of is not None:
        with read_session() as s:
            failed = [{"name": r.name, "classname": r.classname, "file": r.file}
                      for r in s.query(TestResult).filter(TestResult.run_id == rerun_of,
                                                          TestResult.status.in_(("failed", "error")))]
        selection = rerun_args(cmd.framework, failed, workdir)

    branch = commit = None
    if gitinfo.is_git_repo(base_dir):
        branch, commit = gitinfo.current_branch(base_dir), gitinfo.head_commit(base_dir)
    with write_session() as s:
        run = TestRun(project_id=project_id, job_id=ctx.job_id, command_key=key, framework=cmd.framework,
                      argv=list(cmd.argv), workdir=str(workdir), branch=branch, commit_sha=commit,
                      status="running", rerun_of_id=rerun_of)
        s.add(run)
        s.flush()
        run_id = run.id
        audit.record("test.run", target_type="test_run", target_id=run_id, project_id=project_id,
                     detail={"key": key, "argv": cmd.argv, "rerun_of": rerun_of, "coverage": coverage,
                             "approved_hash": cmd.approved_hash, "git_config_trusted": allow_cfg,
                             "workspace": str(workspace) if workspace else None}, session=s)

    out_dir = get_settings().reports_dir / "tests" / str(run_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    extra_args, extra_env = reporter_args(cmd.framework, out_dir, coverage,
                                          coverage and cmd.framework == "pytest" and _has_pytest_cov(root))
    argv = [*cmd.argv, *extra_args, *selection]
    ctx.log(f"$ {' '.join(argv)}\n(工作目錄：{workdir})")
    ctx.progress(0.05, "執行測試中")
    res = run_command(argv, RunOptions(
        cwd=workdir, timeout_sec=cmd.timeout_sec, max_memory_mb=cmd.max_memory_mb, log_path=ctx.log_path,
        cancel_event=ctx.cancel_event, on_start=ctx.register_process, redact=_redact,
        env=child_env(list(cmd.extra_env_names or []), bool(cmd.inherit_full_env), extra_env),
    ))
    ctx.log(f"\n[結束] outcome={res.outcome} exit={res.exit_code} 耗時={res.duration_sec:.1f}s")

    parsed, parse_note = _collect_results(cmd.framework, out_dir)
    cov, cov_note = _collect_coverage(cmd.framework, out_dir, coverage)
    counts = parsed.counts() if parsed else {"total": 0, "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
    if parsed:
        for c in parsed.cases:
            c.message = _redact(c.message) if c.message else c.message
    status, status_note = _run_status(cmd.framework, res.outcome, res.exit_code, counts, parsed is not None)
    cases = [{"name": c.name, "status": c.status, "message": c.message} for c in (parsed.cases if parsed else [])]
    analysis = failures.analyse(cases)
    notes = [n for n in (status_note, parse_note, cov_note, res.error) if n]

    with write_session() as s:
        run = s.get(TestRun, run_id)
        run.status, run.exit_code, run.duration_sec = status, res.exit_code, round(res.duration_sec, 3)
        run.total, run.passed, run.failed = counts["total"], counts["passed"], counts["failed"]
        run.skipped, run.errors = counts["skipped"], counts["errors"]
        run.coverage_percent = cov.get("percent") if cov else None
        run.coverage = cov if cov else ({"unavailable": cov_note} if coverage else None)
        run.failure_analysis = analysis + ([{"category": "run", "hint": "；".join(notes), "count": 0, "tests": []}]
                                           if notes else [])
        run.finished_at = utcnow()
    for start in range(0, len(parsed.cases) if parsed else 0, RESULT_BATCH):
        with write_session() as s:
            for c in parsed.cases[start:start + RESULT_BATCH]:
                s.add(TestResult(run_id=run_id, name=c.name[:1024], classname=c.classname, file=c.file,
                                 status=c.status, duration_sec=c.duration_sec, message=c.message))
    if res.outcome == "cancelled":
        raise JobCancelled()
    if res.outcome == "timeout":
        raise JobTimeout(res.error or "測試逾時")
    return {"test_run_id": run_id, "status": status, **counts}


def _run_status(framework: str, outcome: str, exit_code: int | None, counts: dict[str, int],
                parsed: bool) -> tuple[str, str | None]:
    """Status from exit code and report together; a missing report is never treated as success."""
    if outcome in ("cancelled", "timeout"):
        return outcome, None
    if outcome in ("memory_limit", "not_found", "error"):
        return "error", None
    if framework == "pytest" and exit_code == 5:
        return "no_tests", "pytest 沒有收集到任何測試（exit 5）"
    if not parsed:
        return "error", "未產生測試報告"
    if counts["failed"] or counts["errors"]:
        return "failed", None
    if exit_code not in (0, None):
        return "error", f"exit {exit_code}：測試皆通過但程序以非 0 結束（teardown／session fixture？）"
    return "passed", None


def _collect_results(framework: str, out_dir: Path) -> tuple[reports.ParsedResults | None, str | None]:
    try:
        if framework == "dotnet":
            trx = next(iter(sorted(out_dir.rglob("*.trx"))), None)
            return (reports.parse_trx(trx), None) if trx else (None, "找不到 TRX 結果檔")
        junit = out_dir / "results.xml"
        if not junit.is_file():
            return None, "找不到 JUnit 結果檔（測試可能未啟動或框架不支援報告參數）"
        return reports.parse_junit(junit), None
    except (ET.ParseError, ValueError, OSError) as exc:
        return None, f"結果檔解析失敗：{exc}"


def _collect_coverage(framework: str, out_dir: Path, wanted: bool) -> tuple[dict[str, Any] | None, str | None]:
    if not wanted:
        return None, None
    try:
        if framework == "pytest":
            p = out_dir / "coverage.xml"
            return (reports.parse_cobertura(p), None) if p.is_file() else (None, "專案未安裝 pytest-cov，無覆蓋率資料")
        if framework == "vitest":
            p = out_dir / "coverage" / "coverage-summary.json"
            return (reports.parse_istanbul_summary(p), None) if p.is_file() else (
                None, "未產生覆蓋率（需要 @vitest/coverage-v8 或 istanbul）")
        if framework == "dotnet":
            p = next(iter(sorted(out_dir.rglob("coverage.cobertura.xml"))), None)
            return (reports.parse_cobertura(p), None) if p else (None, "未產生覆蓋率（需要 coverlet.collector）")
    except (ET.ParseError, ValueError, OSError) as exc:
        return None, f"覆蓋率解析失敗：{exc}"
    return None, f"{framework} 不支援覆蓋率收集"
