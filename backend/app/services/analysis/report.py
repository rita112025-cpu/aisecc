"""JSON and HTML scan reports written to data/reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape

from app.config import get_settings
from app.db import read_session
from app.models import Finding, Project, Scan

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"
SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
_env = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html"]))


def report_data(scan_id: int) -> dict[str, Any]:
    with read_session() as s:
        scan = s.get(Scan, scan_id)
        if scan is None:
            raise ValueError("掃描紀錄不存在")
        project = s.get(Project, scan.project_id)
        findings = s.query(Finding).filter_by(scan_id=scan_id).all()
        rows = [{
            "id": f.id, "tool": f.tool, "rule_id": f.rule_id, "severity": f.severity, "category": f.category,
            "confidence": f.confidence, "file": f.file_path, "line": f.line, "column": f.column,
            "message": f.message, "fingerprint": f.fingerprint, "occurrence": f.occurrence,
            "triage_status": f.triage_status, "triage_note": f.triage_note,
        } for f in findings]
        rows.sort(key=lambda r: (SEVERITY_ORDER.get(r["severity"], 9), r["file"] or "", r["line"] or 0))
        return {
            "generated_by": "AI Software Engineering Control Center",
            "disclaimer": (
                "靜態分析結果為疑似問題，需人工確認；"
                "未執行（needs_authorization/not_installed/error）的工具不代表通過。"
            ),
            "project": {"id": project.id, "name": project.name, "path": project.path},
            "scan": {"id": scan.id, "kind": scan.kind, "status": scan.status, "branch": scan.branch,
                     "commit": scan.commit_sha, "created_at": scan.created_at.isoformat(),
                     "finished_at": scan.finished_at.isoformat() if scan.finished_at else None},
            "summary": {k: v for k, v in (scan.summary or {}).items() if k != "tool_status"},
            "tool_status": scan.tool_status,
            "findings": rows,
        }


def write_reports(scan_id: int) -> dict[str, str]:
    data = report_data(scan_id)
    out = get_settings().reports_dir
    out.mkdir(parents=True, exist_ok=True)
    json_path = out / f"scan-{scan_id}.json"
    html_path = out / f"scan-{scan_id}.html"
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    html_path.write_text(_env.get_template("report.html").render(**data), encoding="utf-8")
    return {"json": str(json_path), "html": str(html_path)}
