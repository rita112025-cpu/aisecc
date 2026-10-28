"""Inventory scan (module A): files, languages, dependencies, test frameworks and git state."""

from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import Any

from app.db import read_session, write_session
from app.models import Project, Scan, utcnow
from app.services import gitinfo
from app.services.detect import detect
from app.services.fswalk import walk
from app.services.health import health_report
from app.services.jobs import JobContext
from app.services.settings_store import load_settings


def build_inventory(root: Path, ctx: JobContext | None = None, allow_repo_config: bool = False) -> dict[str, Any]:
    settings = load_settings()
    check = ctx.check_cancelled if ctx else None
    if ctx:
        ctx.progress(0.05, "走訪檔案中")
    walked = walk(root, max_files=settings.scan_max_files, check_cancel=check)
    if ctx:
        ctx.log(f"檔案數 {len(walked.files)}，符號連結 {len(walked.symlinks)}，截斷={walked.truncated}")
        ctx.progress(0.5, "偵測語言與依賴")
    det = detect(root, walked)
    if ctx:
        ctx.progress(0.75, "讀取 Git 狀態")
    git = gitinfo.get_info(root, allow_repo_config)
    ext_counts = Counter(Path(f.rel).suffix.lower() or "(none)" for f in walked.files)
    top_dirs = Counter(f.rel.split("/", 1)[0] if "/" in f.rel else "." for f in walked.files)
    return {
        "file_count": len(walked.files),
        "total_bytes": sum(f.size for f in walked.files),
        "truncated": walked.truncated,
        "symlinks": walked.symlinks[:500],
        "symlink_count": len(walked.symlinks),
        "cloud_placeholders": walked.cloud_placeholders[:500],
        "ignored_dirs": walked.ignored_dirs,
        "walk_errors": walked.errors[:200],
        "extensions": dict(ext_counts.most_common(30)),
        "top_dirs": dict(top_dirs.most_common(30)),
        "detection": det.to_dict(),
        "git": git.to_dict(),
    }


def run_inventory_scan(project_id: int, ctx: JobContext) -> dict[str, Any]:
    with read_session() as s:
        project = s.get(Project, project_id)
        if project is None:
            raise ValueError("專案不存在")
        root = Path(project.path)
        allow = project.allow_project_tools
    if not root.is_dir():
        raise FileNotFoundError(f"專案路徑不存在：{root}")
    with write_session() as s:
        scan = Scan(project_id=project_id, job_id=ctx.job_id, kind="inventory", status="running")
        s.add(scan)
        s.flush()
        scan_id = scan.id
    try:
        summary = build_inventory(root, ctx, allow)
    except BaseException as exc:
        with write_session() as s:
            scan = s.get(Scan, scan_id)
            scan.status = "cancelled" if type(exc).__name__ == "JobCancelled" else "failed"
            scan.summary = {"error": str(exc)}
            scan.finished_at = utcnow()
        raise
    report = health_report(summary, latest_analysis=_latest_analysis_summary(project_id))
    with write_session() as s:
        scan = s.get(Scan, scan_id)
        scan.status = "partial" if summary["truncated"] else "completed"
        scan.summary = summary | {"health": report}
        scan.health_score = report["score"]
        scan.branch = summary["git"].get("branch")
        scan.commit_sha = summary["git"].get("head")
        scan.finished_at = utcnow()
        project = s.get(Project, project_id)
        project.languages = summary["detection"]["languages"]
        project.is_git = bool(summary["git"].get("is_repo"))
        project.last_scan_at = utcnow()
    return {"scan_id": scan_id, "file_count": summary["file_count"], "health_score": report["score"]}


def _latest_analysis_summary(project_id: int) -> dict[str, Any] | None:
    with read_session() as s:
        scan = (
            s.query(Scan)
            .filter(Scan.project_id == project_id, Scan.kind == "analysis", Scan.status.in_(("completed", "partial")))
            .order_by(Scan.id.desc())
            .first()
        )
        return dict(scan.summary) if scan else None


def compare_inventories(a: dict[str, Any], b: dict[str, Any]) -> dict[str, Any]:
    def dep_map(summary: dict[str, Any]) -> dict[tuple[str, str], str]:
        return {
            (d["ecosystem"], d["name"].lower()): d["spec"]
            for d in summary.get("detection", {}).get("dependencies", [])
        }

    da, db_ = dep_map(a), dep_map(b)
    added = sorted(f"{k[0]}:{k[1]}" for k in db_.keys() - da.keys())
    removed = sorted(f"{k[0]}:{k[1]}" for k in da.keys() - db_.keys())
    changed = [
        {"name": f"{k[0]}:{k[1]}", "from": da[k], "to": db_[k]}
        for k in sorted(da.keys() & db_.keys()) if da[k] != db_[k]
    ]
    la = set(a.get("detection", {}).get("languages", []))
    lb = set(b.get("detection", {}).get("languages", []))
    return {
        "file_count": {"from": a.get("file_count"), "to": b.get("file_count")},
        "total_bytes": {"from": a.get("total_bytes"), "to": b.get("total_bytes")},
        "languages_added": sorted(lb - la),
        "languages_removed": sorted(la - lb),
        "dependencies_added": added,
        "dependencies_removed": removed,
        "dependencies_changed": changed,
        "commit": {"from": a.get("git", {}).get("head"), "to": b.get("git", {}).get("head")},
        "health": {"from": a.get("health", {}).get("score"), "to": b.get("health", {}).get("score")},
    }
