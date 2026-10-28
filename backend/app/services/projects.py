"""Project registration and browsing."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from sqlalchemy.exc import IntegrityError

from app.db import write_session
from app.models import Project
from app.services import audit, gitinfo
from app.services.fswalk import DEFAULT_IGNORES, is_link_or_junction, resolve_inside


class ProjectError(ValueError):
    pass


def normalise_path(raw: str) -> tuple[Path, str]:
    if not raw or not raw.strip():
        raise ProjectError("請輸入專案路徑")
    p = Path(os.path.expandvars(raw.strip().strip('"'))).expanduser()
    if not p.is_absolute():
        raise ProjectError("請輸入絕對路徑")
    try:
        p = p.resolve(strict=True)
    except (FileNotFoundError, OSError) as exc:
        raise ProjectError(f"路徑不存在：{raw}") from exc
    if not p.is_dir():
        raise ProjectError(f"路徑不是資料夾：{raw}")
    key = os.path.normcase(str(p))
    return p, key


def add_project(raw_path: str, name: str | None = None) -> Project:
    path, key = normalise_path(raw_path)
    is_git = gitinfo.is_git_repo(path)
    try:
        with write_session() as s:
            project = Project(name=(name or path.name).strip()[:200] or path.name, path=str(path),
                              path_key=key, is_git=is_git, languages=[])
            s.add(project)
            s.flush()
            audit.record("project.add", target_type="project", target_id=project.id, project_id=project.id,
                         detail={"path": str(path), "is_git": is_git}, session=s)
    except IntegrityError as exc:
        raise ProjectError("此路徑已經加入過") from exc
    return project


def list_dir(root: Path, rel: str = "", limit: int = 2000) -> dict[str, Any]:
    target = root.resolve() if rel in ("", ".") else resolve_inside(root, rel)
    if not target.is_dir():
        raise ProjectError("不是資料夾")
    items = []
    truncated = False
    with os.scandir(target) as it:
        for entry in sorted(it, key=lambda e: (not e.is_dir(follow_symlinks=False), e.name.lower())):
            if len(items) >= limit:
                truncated = True
                break
            link = is_link_or_junction(entry)
            is_dir = (not link) and entry.is_dir(follow_symlinks=False)
            try:
                size = None if is_dir or link else entry.stat(follow_symlinks=False).st_size
            except OSError:
                size = None
            items.append({
                "name": entry.name,
                "path": Path(entry.path).relative_to(root.resolve()).as_posix(),
                "type": "symlink" if link else "dir" if is_dir else "file",
                "size": size,
                "ignored_by_default": is_dir and entry.name in DEFAULT_IGNORES,
            })
    return {"path": "" if target == root.resolve() else target.relative_to(root.resolve()).as_posix(),
            "items": items, "truncated": truncated}
