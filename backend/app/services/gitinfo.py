"""Read-only Git queries. Never writes to the user's repository (no fetch/commit/checkout/index refresh)."""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

GIT_TIMEOUT = 120
MAX_STATUS_ENTRIES = 5000


class GitError(RuntimeError):
    pass


def git_available() -> bool:
    return shutil.which("git") is not None


def _env() -> dict[str, str]:
    env = dict(os.environ)
    # Prevent git from taking index.lock for opportunistic stat refreshes.
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["LC_ALL"] = "C.UTF-8"
    return env


def run_git(repo: Path, *args: str, timeout: int = GIT_TIMEOUT, check: bool = True) -> str:
    cmd = ["git", "-c", "core.quotepath=off", "-c", "color.ui=never", *args]
    try:
        proc = subprocess.run(  # noqa: S603 - fixed git argv
            cmd, cwd=str(repo), capture_output=True, timeout=timeout, env=_env(),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except FileNotFoundError as exc:
        raise GitError("系統未安裝 git") from exc
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args[:2])} 逾時（{timeout}s）") from exc
    out = proc.stdout.decode("utf-8", errors="replace")
    if check and proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", errors="replace").strip() or f"git exit {proc.returncode}")
    return out


def is_git_repo(path: Path) -> bool:
    if not git_available():
        return False
    try:
        out = run_git(path, "rev-parse", "--is-inside-work-tree", timeout=15)
    except GitError:
        return False
    return out.strip() == "true"


def repo_toplevel(path: Path) -> Path | None:
    try:
        return Path(run_git(path, "rev-parse", "--show-toplevel", timeout=15).strip())
    except GitError:
        return None


@dataclass
class StatusEntry:
    path: str
    # modified | added | deleted | renamed | untracked | conflicted | typechange
    change: str
    staged: bool
    unstaged: bool


@dataclass
class GitInfo:
    is_repo: bool
    branch: str | None = None
    head: str | None = None
    head_subject: str | None = None
    detached: bool = False
    upstream: str | None = None
    ahead: int | None = None
    behind: int | None = None
    dirty: bool = False
    entries: list[StatusEntry] = field(default_factory=list)
    entries_truncated: bool = False
    counts: dict[str, int] = field(default_factory=dict)
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_XY = {"M": "modified", "A": "added", "D": "deleted", "R": "renamed", "C": "renamed", "T": "typechange"}


def parse_status_v2(raw: str) -> tuple[dict[str, Any], list[StatusEntry]]:
    """Parse `git status --porcelain=v2 --branch -z` output."""
    header: dict[str, Any] = {}
    entries: list[StatusEntry] = []
    tokens = raw.split("\x00")
    i = 0
    while i < len(tokens):
        tok = tokens[i]
        i += 1
        if not tok:
            continue
        if tok.startswith("# "):
            key, _, value = tok[2:].partition(" ")
            header[key] = value
        elif tok.startswith("1 "):
            parts = tok.split(" ", 8)
            entries.append(_entry(parts[1], parts[8]))
        elif tok.startswith("2 "):
            parts = tok.split(" ", 9)
            entries.append(_entry(parts[1], parts[9]))
            i += 1  # skip original path token
        elif tok.startswith("u "):
            parts = tok.split(" ", 10)
            entries.append(StatusEntry(parts[10], "conflicted", True, True))
        elif tok.startswith("? "):
            entries.append(StatusEntry(tok[2:], "untracked", False, True))
    return header, entries


def _entry(xy: str, path: str) -> StatusEntry:
    x, y = xy[0], xy[1]
    code = x if x != "." else y
    return StatusEntry(path, _XY.get(code, "modified"), x != ".", y != ".")


def get_info(repo: Path) -> GitInfo:
    if not is_git_repo(repo):
        return GitInfo(is_repo=False)
    info = GitInfo(is_repo=True)
    try:
        raw = run_git(repo, "status", "--porcelain=v2", "--branch", "-z", "--untracked-files=normal")
    except GitError as exc:
        info.error = str(exc)
        return info
    header, entries = parse_status_v2(raw)
    head = header.get("branch.oid")
    info.head = None if head in (None, "(initial)") else head
    br = header.get("branch.head")
    info.detached = br == "(detached)"
    info.branch = None if info.detached else br
    info.upstream = header.get("branch.upstream")
    if "branch.ab" in header:
        a, b = header["branch.ab"].split()
        info.ahead, info.behind = int(a), abs(int(b))
    info.dirty = bool(entries)
    counts: dict[str, int] = {}
    for e in entries:
        counts[e.change] = counts.get(e.change, 0) + 1
    info.counts = counts
    info.entries_truncated = len(entries) > MAX_STATUS_ENTRIES
    info.entries = entries[:MAX_STATUS_ENTRIES]
    if info.head:
        try:
            info.head_subject = run_git(repo, "log", "-1", "--format=%s", info.head).strip()
        except GitError:
            info.head_subject = None
    return info


def head_commit(repo: Path) -> str | None:
    try:
        return run_git(repo, "rev-parse", "HEAD", timeout=15).strip() or None
    except GitError:
        return None


def current_branch(repo: Path) -> str | None:
    try:
        name = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD", timeout=15).strip()
    except GitError:
        return None
    return None if name == "HEAD" else name


def log(repo: Path, limit: int = 50) -> list[dict[str, str]]:
    sep = "\x1f"
    out = run_git(repo, "log", f"-{max(1, min(limit, 500))}", f"--format=%H{sep}%an{sep}%aI{sep}%s", check=False)
    commits = []
    for line in out.splitlines():
        parts = line.split(sep)
        if len(parts) == 4:
            commits.append({"sha": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    return commits


def diff(repo: Path, *, base: str | None = None, target: str | None = None, path: str | None = None,
         max_bytes: int = 2_000_000) -> dict[str, Any]:
    """Unified diff: working tree vs HEAD (default), or between two revisions."""
    for rev in (base, target):
        if rev is not None and (rev.startswith("-") or not rev.strip()):
            raise GitError("不合法的版本參數")
    args = ["diff", "--no-ext-diff", "--no-textconv", "--find-renames"]
    if base and target:
        args += [base, target]
    elif base:
        args.append(base)
    else:
        args.append("HEAD")
    if path:
        args += ["--", path]
    text = run_git(repo, *args)
    stat_out = run_git(repo, *(["diff", "--numstat", *args[1:]]))
    files = []
    for line in stat_out.splitlines():
        parts = line.split("\t")
        if len(parts) == 3:
            add, rem, name = parts
            files.append({"path": name, "added": None if add == "-" else int(add),
                          "removed": None if rem == "-" else int(rem)})
    truncated = len(text.encode("utf-8")) > max_bytes
    return {"diff": text.encode("utf-8")[:max_bytes].decode("utf-8", errors="ignore"),
            "files": files, "truncated": truncated}


def dirty_paths(repo: Path) -> set[str]:
    raw = run_git(repo, "status", "--porcelain=v2", "-z", "--untracked-files=all")
    _, entries = parse_status_v2(raw)
    return {e.path for e in entries}
