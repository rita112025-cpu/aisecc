from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.api.common import ORMModel
from app.config import get_settings
from app.db import get_db
from app.models import Scan
from app.services.analysis.diff import compare_analysis_scans
from app.services.analysis.report import write_reports
from app.services.inventory import compare_inventories

router = APIRouter(prefix="/scans", tags=["scans"])


class ScanOut(ORMModel):
    id: int
    project_id: int
    job_id: str | None
    kind: str
    status: str
    branch: str | None
    commit_sha: str | None
    health_score: float | None
    tool_status: dict[str, Any]
    created_at: datetime
    finished_at: datetime | None


class ScanDetail(ScanOut):
    summary: dict[str, Any]


def _get(db: Session, scan_id: int) -> Scan:
    scan = db.get(Scan, scan_id)
    if scan is None:
        raise HTTPException(404, "掃描紀錄不存在")
    return scan


@router.get("", response_model=list[ScanOut])
def list_scans(
    db: Session = Depends(get_db),
    project_id: int | None = None,
    kind: str | None = None,
    limit: int = Query(100, ge=1, le=1000),
) -> list[Scan]:
    q = db.query(Scan)
    if project_id is not None:
        q = q.filter(Scan.project_id == project_id)
    if kind:
        q = q.filter(Scan.kind == kind)
    return q.order_by(Scan.id.desc()).limit(limit).all()


@router.get("/compare")
def compare_scans(a: int, b: int, db: Session = Depends(get_db)) -> dict[str, Any]:
    sa, sb = _get(db, a), _get(db, b)
    if sa.project_id != sb.project_id:
        raise HTTPException(400, "只能比較同一專案的掃描")
    if sa.kind != sb.kind:
        raise HTTPException(400, "掃描類型不同，無法比較")
    if sa.kind == "inventory":
        return {"kind": "inventory", "a": a, "b": b, "diff": compare_inventories(sa.summary, sb.summary)}
    return {"kind": "analysis", "a": a, "b": b, "diff": compare_analysis_scans(db, a, b)}


@router.get("/{scan_id}/report.{fmt}")
def scan_report(scan_id: int, fmt: str, db: Session = Depends(get_db)) -> FileResponse:
    scan = _get(db, scan_id)
    if fmt not in ("json", "html"):
        raise HTTPException(404, "不支援的格式")
    if scan.kind != "analysis":
        raise HTTPException(400, "僅分析掃描有報告")
    path = get_settings().reports_dir / f"scan-{scan_id}.{fmt}"
    if not path.is_file():
        if scan.status not in ("completed", "partial"):
            raise HTTPException(409, "掃描尚未完成")
        write_reports(scan_id)
    media = "application/json" if fmt == "json" else "text/html"
    return FileResponse(path, media_type=f"{media}; charset=utf-8", filename=f"scan-{scan_id}.{fmt}")


@router.get("/{scan_id}", response_model=ScanDetail)
def get_scan(scan_id: int, db: Session = Depends(get_db)) -> Scan:
    return _get(db, scan_id)
