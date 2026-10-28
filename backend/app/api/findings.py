from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import case, func, or_
from sqlalchemy.orm import Session

from app.api.common import ORMModel
from app.db import get_db, write_session
from app.models import Finding, FindingSuppression, Scan
from app.services import audit

router = APIRouter(prefix="/findings", tags=["findings"])

SEVERITY_RANK = case(
    {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}, value=Finding.severity, else_=5
)
SORTS = {
    "severity": (SEVERITY_RANK, Finding.file_path, Finding.line),
    "file": (Finding.file_path, Finding.line),
    "tool": (Finding.tool, Finding.rule_id),
    "category": (Finding.category, SEVERITY_RANK),
}


class FindingOut(ORMModel):
    id: int
    scan_id: int
    project_id: int
    tool: str
    rule_id: str
    severity: str
    category: str
    confidence: str
    file_path: str | None
    line: int | None
    column: int | None
    end_line: int | None
    message: str
    fingerprint: str
    occurrence: int
    triage_status: str
    triage_note: str | None
    extra: dict[str, Any]


class FindingPage(BaseModel):
    total: int
    items: list[FindingOut]


class TriageIn(BaseModel):
    status: Literal["open", "false_positive", "accepted_risk", "confirmed", "fixed"]
    note: str | None = Field(default=None, max_length=2000)
    # Apply to future scans via the fingerprint (default for false positives / accepted risk).
    remember: bool = True


def _latest_analysis_scan(db: Session, project_id: int) -> int | None:
    row = (db.query(Scan.id).filter(Scan.project_id == project_id, Scan.kind == "analysis",
                                    Scan.status.in_(("completed", "partial")))
           .order_by(Scan.id.desc()).first())
    return row[0] if row else None


@router.get("", response_model=FindingPage)
def list_findings(
    db: Session = Depends(get_db),
    scan_id: int | None = None,
    project_id: int | None = None,
    severity: str | None = Query(None, description="逗號分隔"),
    category: str | None = Query(None, description="逗號分隔"),
    tool: str | None = None,
    triage: str | None = Query(None, description="逗號分隔，例如 open,confirmed"),
    q: str | None = Query(None, max_length=200),
    sort: Literal["severity", "file", "tool", "category"] = "severity",
    desc: bool = False,
    offset: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
) -> FindingPage:
    if scan_id is None:
        if project_id is None:
            raise HTTPException(400, "需指定 scan_id 或 project_id")
        scan_id = _latest_analysis_scan(db, project_id)
        if scan_id is None:
            return FindingPage(total=0, items=[])
    query = db.query(Finding).filter(Finding.scan_id == scan_id)
    for col, value in ((Finding.severity, severity), (Finding.category, category), (Finding.triage_status, triage)):
        if value:
            query = query.filter(col.in_([v.strip() for v in value.split(",") if v.strip()]))
    if tool:
        query = query.filter(Finding.tool == tool)
    if q:
        like = f"%{q.replace('%', r'\%').replace('_', r'\_')}%"
        query = query.filter(or_(Finding.message.ilike(like, escape="\\"), Finding.file_path.ilike(like, escape="\\"),
                                 Finding.rule_id.ilike(like, escape="\\")))
    total = query.with_entities(func.count(Finding.id)).scalar() or 0
    order = [c.desc() if desc else c.asc() for c in SORTS[sort]]
    items = query.order_by(*order, Finding.id).offset(offset).limit(limit).all()
    return FindingPage(total=total, items=[FindingOut.model_validate(i) for i in items])


@router.get("/{finding_id}", response_model=FindingOut)
def get_finding(finding_id: int, db: Session = Depends(get_db)) -> Finding:
    f = db.get(Finding, finding_id)
    if f is None:
        raise HTTPException(404, "發現項目不存在")
    return f


@router.post("/{finding_id}/triage", response_model=FindingOut)
def triage_finding(finding_id: int, body: TriageIn, db: Session = Depends(get_db)) -> Finding:
    if db.get(Finding, finding_id) is None:
        raise HTTPException(404, "發現項目不存在")
    if body.status in ("false_positive", "accepted_risk") and not (body.note or "").strip():
        raise HTTPException(400, "標記為誤報或接受風險時必須填寫理由")
    with write_session() as s:
        f = s.get(Finding, finding_id)
        before = f.triage_status
        f.triage_status = body.status
        f.triage_note = body.note
        sup = s.query(FindingSuppression).filter_by(project_id=f.project_id, fingerprint=f.fingerprint).one_or_none()
        if body.remember and body.status != "open":
            if sup is None:
                s.add(FindingSuppression(project_id=f.project_id, fingerprint=f.fingerprint, status=body.status,
                                         note=body.note))
            else:
                sup.status, sup.note = body.status, body.note
        elif sup is not None:
            s.delete(sup)
        audit.record("finding.triage", target_type="finding", target_id=finding_id, project_id=f.project_id,
                     detail={"from": before, "to": body.status, "note": body.note, "remember": body.remember},
                     session=s)
    db.rollback()  # end the read snapshot so the committed write is visible
    return db.get(Finding, finding_id)
