"""Isolated repair workspaces: a read-only clone of the project's HEAD under data/workspaces.

The source repository is only read. Dependency folders (.venv, node_modules) are linked in with
junctions for test validation and are always unlinked before the workspace is deleted.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from app.config import get_settings
from app.services import gitinfo

LINKED_DIRS = (".venv", "venv", "node_modules")
CMD_UNSAFE = re.compile(r'[&|<>^%!"\r\n]')


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
                    # mklink runs through cmd.exe, which re-parses the line: refuse rather than execute
                    # any command embedded in a directory name (e.g. "x&del ...").
                    if CMD_UNSAFE.search(str(src)) or CMD_UNSAFE.search(str(dst)):
                        raise WorkspaceError(
                            "專案路徑含有 cmd 特殊字元（& | < > ^ % ! 等），無法建立依賴連結，已拒絕")
                    proc = subprocess.run(["cmd", "/d", "/c", "mklink", "/J", str(dst), str(src)],  # noqa: S603,S607
                                          capture_output=True, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    if proc.returncode != 0:
                        continue
                else:
                    os.symlink(src, dst, target_is_directory=True)
                linked.append((Path(base) / name).as_posix())
    return linked


def editable_installs(project_root: Path) -> list[str]:
    """Files in the project's dependency folders that point back into the project itself
    (pip install -e, workspace symlinks). Tests in a workspace would then import the original,
    unpatched code, so such a run cannot count as validation."""
    root = os.path.normcase(os.path.realpath(project_root))
    hits: list[str] = []

    def points_inside(target: str) -> bool:
        try:
            real = os.path.normcase(os.path.realpath(target.strip()))
        except (OSError, ValueError):
            return False
        return real == root or real.startswith(root + os.sep)

    for venv in (".venv", "venv"):
        for sp in (project_root / venv).glob("**/site-packages"):
            for f in [*sp.glob("*.pth"), *sp.glob("*.egg-link")]:
                try:
                    lines = f.read_text(encoding="utf-8", errors="ignore").splitlines()
                except OSError:
                    continue
                if f.name.startswith("__editable__") or any(
                        ln.strip() and not ln.startswith("import") and points_inside(ln) for ln in lines):
                    hits.append(f.relative_to(project_root).as_posix())
    nm = project_root / "node_modules"
    if nm.is_dir():
        for entry in list(nm.iterdir()) + [p for d in nm.glob("@*") for p in d.iterdir()]:
            if (entry.is_symlink() or entry.is_junction()) and points_inside(str(entry)):
                hits.append(entry.relative_to(project_root).as_posix())
    return sorted(hits)


def safe_rmtree(path: Path, allowed_root: Path) -> None:
    """Delete `path` only if it lies under `allowed_root`; links and junctions inside are removed as
    links and never followed, so linked project folders (.venv, node_modules) are never touched."""
    real_root = os.path.normcase(os.path.realpath(allowed_root))
    real = os.path.normcase(os.path.realpath(path))
    if not real.startswith(real_root + os.sep):
        raise WorkspaceError(f"拒絕刪除 {path}：不在 {allowed_root} 之下")
    if not path.exists() and not path.is_junction():
        return
    for dirpath, dirnames, filenames in os.walk(path, topdown=True, followlinks=False):
        for d in list(dirnames):
            p = Path(dirpath) / d
            if p.is_junction() or p.is_symlink():
                (os.rmdir if p.is_junction() else os.unlink)(p)
                dirnames.remove(d)
        for name in filenames:
            p = Path(dirpath) / name
            if p.is_symlink():
                p.unlink()

    def _onexc(func, p, _exc):  # noqa: ANN001 - shutil callback
        os.chmod(p, 0o700)  # .git objects are read-only on Windows
        func(p)

    shutil.rmtree(path, onexc=_onexc)


def remove(workspace: Path) -> None:
    safe_rmtree(workspace, get_settings().workspaces_dir)
