"""Compare two analysis scans by (fingerprint, occurrence)."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.models import Finding

LIST_LIMIT = 500


def _brief(f: Finding) -> dict[str, Any]:
    return {"id": f.id, "tool": f.tool, "rule_id": f.rule_id, "severity": f.severity, "category": f.category,
            "file": f.file_path, "line": f.line, "message": f.message}


def compare_analysis_scans(db: Session, a: int, b: int) -> dict[str, Any]:
    fa = {(f.fingerprint, f.occurrence): f for f in db.query(Finding).filter_by(scan_id=a)}
    fb = {(f.fingerprint, f.occurrence): f for f in db.query(Finding).filter_by(scan_id=b)}
    new = [fb[k] for k in sorted(fb.keys() - fa.keys())]
    resolved = [fa[k] for k in sorted(fa.keys() - fb.keys())]
    unchanged = fa.keys() & fb.keys()
    moved = sum(1 for k in unchanged if fa[k].line != fb[k].line)
    return {
        "counts": {"new": len(new), "resolved": len(resolved), "unchanged": len(unchanged), "moved": moved},
        "new": [_brief(f) for f in new[:LIST_LIMIT]],
        "resolved": [_brief(f) for f in resolved[:LIST_LIMIT]],
        "truncated": len(new) > LIST_LIMIT or len(resolved) > LIST_LIMIT,
    }
