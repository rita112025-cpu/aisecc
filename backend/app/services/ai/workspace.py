"""Isolated repair workspaces: a read-only clone of the project's HEAD under data/workspaces.

The source repository is only read. Dependency folders (.venv, node_modules) are linked in with
junctions for test validation and are always unlinked before the workspace is deleted.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from app.services import gitinfo

LINKED_DIRS = (".venv", "venv", "node_modules")


class WorkspaceError(RuntimeError):
    pass


def create(project_root: Path, dest: Path, commit: str) -> Path:
    if dest.exists():
        raise WorkspaceError(f"工作區已存在：{dest}")
    dest.parent.mkdir(parents=True, exist_ok=True)
    try:
        # clone only reads the source repo; run_git disables hooks/fsmonitor for the checkout.
        gitinfo.run_git(dest.parent, "clone", "--quiet", "--no-hardlinks", "--no-checkout",
                        str(project_root), dest.name, timeout=600)
        gitinfo.run_git(dest, "checkout", "--quiet", "--detach", commit, timeout=600)
    except gitinfo.GitError as exc:
        remove(dest)
        raise WorkspaceError(f"建立隔離工作區失敗：{exc}") from exc
    return dest


def link_dependencies(project_root: Path, workspace: Path, cwd: str = ".") -> list[str]:
    """Junction dependency folders from the project into the workspace (no admin rights needed)."""
    linked = []
    for base in {".", cwd}:
        for name in LINKED_DIRS:
            src = (project_root / base / name)
            dst = (workspace / base / name)
            if src.is_dir() and not dst.exists() and dst.parent.is_dir():
                if sys.platform == "win32":
                    proc = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(dst), str(src)],  # noqa: S603,S607
                                          capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if proc.returncode != 0:
                        continue
                else:
                    os.symlink(src, dst, target_is_directory=True)
                linked.append((Path(base) / name).as_posix())
    return linked


def _unlink_links(root: Path) -> None:
    for dirpath, dirnames, _ in os.walk(root):
        for d in list(dirnames):
            p = Path(dirpath) / d
            if p.is_junction() or p.is_symlink():
                # rmdir/unlink removes only the link, never the target's contents.
                (os.rmdir if p.is_junction() else os.unlink)(p)
                dirnames.remove(d)


def remove(workspace: Path) -> None:
    if not workspace.exists():
        return
    _unlink_links(workspace)

    def _onexc(func, path, _exc):  # noqa: ANN001 - shutil callback
        os.chmod(path, 0o700)  # .git objects are read-only on Windows
        func(path)

    shutil.rmtree(workspace, onexc=_onexc)
