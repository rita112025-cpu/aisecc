"""Analysis scan orchestration (module B): built-in analysers + authorised external tools."""

from __future__ import annotations

import os
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.db import read_session, write_session
from app.models import Finding, FindingSuppression, Project, Scan, utcnow
from app.services import gitinfo
from app.services.analysis import code_metrics, secrets
from app.services.analysis.base import RawFinding, assign_fingerprints, load_sources
from app.services.analysis.report import write_reports
from app.services.analysis.tools import TOOLS, ToolContext, ToolResult, authorised
from app.services.detect import detect
from app.services.fswalk import resolve_inside, walk
from app.services.jobs import JobCancelled, JobContext
from app.services.settings_store import load_settings

OPTIONAL_TOOLS = frozenset({"semgrep"})
MAX_PER_RULE = 2000
BATCH_SIZE = 500


@dataclass
class AnalysisOptions:
    allow_project_tools: bool = False
    allow_network_audit: bool = False
    # Restrict to these tool names (None = all applicable).
    tools: list[str] | None = None


def run_analysis(root: Path, opts: AnalysisOptions, ctx: JobContext | None = None) -> tuple[
        list[RawFinding], dict[str, Any], dict[str, Any]]:
    """Returns (findings, tool_status, meta). Raises JobCancelled when cancelled."""
    settings = load_settings()

    def step(frac: float, msg: str) -> None:
        if ctx:
            ctx.progress(frac, msg)
            ctx.log(msg)

    step(0.02, "走訪檔案")
    walked = walk(root, max_files=settings.scan_max_files, check_cancel=ctx.check_cancelled if ctx else None)
    det = detect(root, walked)
    index = load_sources(root, walked, settings.scan_max_file_bytes)
    tool_status: dict[str, Any] = {}
    findings: list[RawFinding] = []

    def builtin(name: str, fn) -> None:  # noqa: ANN001 - small local helper
        if ctx:
            ctx.check_cancelled()
        result = fn()
        found, note = result if isinstance(result, tuple) else (result, [])
        findings.extend(found)
        tool_status[name] = {"state": "ok", "message": "; ".join(note[:20]), "findings": len(found),
                             "duration_sec": 0.0, "command": None}

    step(0.1, "內建分析：敏感資訊")
    secret_findings, secret_values = secrets.scan_secrets(index)
    builtin("builtin-secrets", lambda: secret_findings)
    step(0.15, "內建分析：複雜度")
    builtin("builtin-metrics", lambda: _metrics(index, settings.complexity_threshold,
                                                settings.function_length_threshold))
    step(0.2, "內建分析：重複程式碼")
    builtin("builtin-duplication", lambda: code_metrics.duplication(index))
    step(0.25, "內建分析：循環依賴")
    builtin("builtin-cycles", lambda: code_metrics.import_cycles(index))
    step(0.3, "內建分析：未使用程式碼候選")
    builtin("builtin-unused", lambda: code_metrics.unused_python_candidates(index))

    tctx = ToolContext(
        root=root, languages=det.languages, manifests=det.manifests,
        allow_project_tools=opts.allow_project_tools, allow_network_audit=opts.allow_network_audit,
        timeout_sec=settings.tool_timeout_sec, cancel_event=ctx.cancel_event if ctx else None,
        on_start=ctx.register_process if ctx else None,
    )
    selected = [t for t in TOOLS if opts.tools is None or t.name in opts.tools]
    for i, tool in enumerate(selected):
        step(0.35 + 0.6 * i / max(1, len(selected)), f"外部工具：{tool.name}")
        if not tool.applies(tctx):
            tool_status[tool.name] = ToolResult("not_applicable", message="專案未使用相關語言").status_dict()
            continue
        if not tool.installed():
            tool_status[tool.name] = ToolResult("not_installed", message=f"{tool.name} 未安裝").status_dict()
            continue
        ok, reason = authorised(tool, tctx)
        if not ok:
            tool_status[tool.name] = ToolResult("needs_authorization", message=reason).status_dict()
            continue
        result = tool.run(tctx)
        if result.state == "cancelled":
            raise JobCancelled()
        kept, dropped = _inside_project(root, result.findings)
        findings.extend(kept)
        tool_status[tool.name] = result.status_dict() | {"findings": len(kept), "dropped_out_of_tree": dropped}
        if ctx:
            ctx.log(secrets.redact(f"{tool.name}: {result.state} {result.message}".strip(), secret_values))

    findings = _redact_all(findings, secret_values)
    findings = _cap_per_rule(findings, tool_status)

    meta = {
        "file_count": len(walked.files),
        "truncated": walked.truncated,
        "languages": det.languages,
        "sources_loaded": len(index.files),
        "sources_skipped": index.skipped[:200],
        "encodings": dict(Counter(f.encoding for f in index.files.values())),
    }
    return assign_fingerprints(findings, index), tool_status, meta


def _inside_project(root: Path, findings: list[RawFinding]) -> tuple[list[RawFinding], int]:
    """Tool output is untrusted (eslint/tsc run project code): drop paths outside the project."""
    kept, dropped = [], 0
    for f in findings:
        if f.file_path:
            try:
                resolved = resolve_inside(root, f.file_path, must_exist=False)
            except (ValueError, FileNotFoundError):
                dropped += 1
                continue
            f.file_path = resolved.relative_to(Path(os.path.realpath(root))).as_posix()
        kept.append(f)
    return kept, dropped


def _redact_all(findings: list[RawFinding], values: set[str]) -> list[RawFinding]:
    for f in findings:
        f.message = secrets.redact(f.message, values)
        f.extra = secrets.redact_obj(f.extra, values)
    return findings


def _cap_per_rule(findings: list[RawFinding], tool_status: dict[str, Any]) -> list[RawFinding]:
    counts: Counter[tuple[str, str]] = Counter()
    kept = []
    for f in findings:
        key = (f.tool, f.rule_id)
        counts[key] += 1
        if counts[key] <= MAX_PER_RULE:
            kept.append(f)
    for (tool, rule), n in counts.items():
        if n > MAX_PER_RULE:
            st = tool_status.setdefault(tool, {"state": "ok", "message": ""})
            st["truncated"] = True
            st.setdefault("truncated_rules", {})[rule] = n
    return kept


def _metrics(index, complexity: int, length: int) -> tuple[list[RawFinding], list[str]]:  # noqa: ANN001
    py, errors = code_metrics.python_complexity(index, complexity, length)
    return py + code_metrics.js_complexity(index, complexity, length), errors


def persist_findings(scan_id: int, project_id: int, findings: list[RawFinding]) -> dict[str, Any]:
    """Idempotent insert keyed by (fingerprint, occurrence); carries forward triage decisions.

    Commits in batches so a large scan never holds the SQLite write lock for long.
    """
    with read_session() as s:
        suppressions = {sup.fingerprint: (sup.status, sup.note)
                        for sup in s.query(FindingSuppression).filter_by(project_id=project_id).all()}
        existing = {(fp, occ) for fp, occ in s.query(Finding.fingerprint, Finding.occurrence).filter_by(
            scan_id=scan_id)}
    pending = [f for f in findings if (f.fingerprint, f.occurrence) not in existing]
    for start in range(0, len(pending), BATCH_SIZE):
        with write_session() as s:
            for f in pending[start:start + BATCH_SIZE]:
                _add_finding(s, scan_id, project_id, f, suppressions.get(f.fingerprint))
    return summarise(scan_id)


def _add_finding(s, scan_id: int, project_id: int, f: RawFinding,  # noqa: ANN001 - Session
                 sup: tuple[str, str | None] | None) -> None:
    s.add(Finding(
        scan_id=scan_id, project_id=project_id, tool=f.tool, rule_id=f.rule_id[:120], severity=f.severity,
        category=f.category, confidence=f.confidence, file_path=f.file_path, line=f.line, column=f.column,
        end_line=f.end_line, message=f.message[:4000], fingerprint=f.fingerprint, occurrence=f.occurrence,
        triage_status=sup[0] if sup else "open", triage_note=sup[1] if sup else None, extra=f.extra,
    ))


def summarise(scan_id: int) -> dict[str, Any]:
    with read_session() as s:
        rows = s.query(Finding.severity, Finding.category, Finding.tool, Finding.triage_status).filter_by(
            scan_id=scan_id).all()
    open_rows = [r for r in rows if r.triage_status in ("open", "confirmed")]
    return {
        "total": len(rows),
        "open": len(open_rows),
        "by_severity": dict(Counter(r.severity for r in rows)),
        "open_by_severity": dict(Counter(r.severity for r in open_rows)),
        "by_category": dict(Counter(r.category for r in rows)),
        "by_tool": dict(Counter(r.tool for r in rows)),
        "by_triage": dict(Counter(r.triage_status for r in rows)),
    }


def run_analysis_scan(project_id: int, ctx: JobContext, tools: list[str] | None = None) -> dict[str, Any]:
    with read_session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise ValueError("專案不存在")
        root = Path(project.path)
        opts = AnalysisOptions(project.allow_project_tools, project.allow_network_audit, tools)
    if not root.is_dir():
        raise FileNotFoundError(f"專案路徑不存在：{root}")
    git = gitinfo.get_info(root, opts.allow_project_tools)
    with write_session() as s:
        scan = Scan(project_id=project_id, job_id=ctx.job_id, kind="analysis", status="running",
                    branch=git.branch, commit_sha=git.head)
        s.add(scan)
        s.flush()
        scan_id = scan.id
    try:
        findings, tool_status, meta = run_analysis(root, opts, ctx)
    except BaseException as exc:
        with write_session() as s:
            scan = s.get(Scan, scan_id)
            scan.status = "cancelled" if isinstance(exc, JobCancelled) else "failed"
            scan.summary = {"error": str(exc) or type(exc).__name__}
            scan.finished_at = utcnow()
        raise
    ctx.progress(0.96, "寫入結果")
    summary = persist_findings(scan_id, project_id, findings)
    # Optional tools that are simply not installed are shown but do not make the scan partial;
    # otherwise nearly every scan would be partial and the status would lose its meaning.
    incomplete = [n for n, st in tool_status.items() if st["state"] not in ("ok", "not_applicable")
                  and not (n in OPTIONAL_TOOLS and st["state"] == "not_installed")]
    with write_session() as s:
        scan = s.get(Scan, scan_id)
        scan.status = "partial" if (incomplete or meta["truncated"]) else "completed"
        scan.summary = summary | {"meta": meta, "incomplete_tools": incomplete, "tool_status": tool_status}
        scan.tool_status = tool_status
        scan.finished_at = utcnow()
        s.get(Project, project_id).last_scan_at = utcnow()
    paths = write_reports(scan_id)
    ctx.log(f"報告：{paths['json']}")
    return {"scan_id": scan_id, "findings": summary["total"], "open": summary["open"], "status": scan.status,
            "incomplete_tools": incomplete}
