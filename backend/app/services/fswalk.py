"""Bounded, cancellable project file walker.

- Never follows symlinks or Windows junctions (reparse points); they are reported instead.
- Honours .gitignore files at every level plus a built-in ignore list.
- Stops at a file-count limit and reports `truncated` rather than hanging on huge trees.
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import chardet
import pathspec

DEFAULT_IGNORES = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".next", ".nuxt", "coverage", "htmlcov",
    "bin", "obj", ".idea", ".vs", ".vscode", "target", ".gradle", ".turbo", ".cache", "site-packages",
})
FILE_ATTRIBUTE_REPARSE_POINT = 0x400


@dataclass
class WalkEntry:
    rel: str  # POSIX-style path relative to root
    size: int
    is_symlink: bool = False


@dataclass
class WalkResult:
    files: list[WalkEntry] = field(default_factory=list)
    symlinks: list[str] = field(default_factory=list)
    skipped_large: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ignored_dirs: int = 0
    truncated: bool = False


def is_link_or_junction(entry: os.DirEntry[str]) -> bool:
    if entry.is_symlink():
        return True
    try:
        st = entry.stat(follow_symlinks=False)
    except OSError:
        return False
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & FILE_ATTRIBUTE_REPARSE_POINT) and stat.S_ISDIR(st.st_mode)


def _load_gitignore(directory: Path) -> pathspec.PathSpec | None:
    gi = directory / ".gitignore"
    if not gi.is_file():
        return None
    try:
        lines = read_text(gi).splitlines()
    except OSError:
        return None
    return pathspec.PathSpec.from_lines("gitwildmatch", lines)


def walk(
    root: Path,
    *,
    max_files: int = 200_000,
    max_file_bytes: int | None = None,
    check_cancel: Callable[[], None] | None = None,
    extra_ignores: frozenset[str] = frozenset(),
) -> WalkResult:
    root = root.resolve()
    result = WalkResult()
    ignores = DEFAULT_IGNORES | extra_ignores
    # Stack of (directory, [(spec_base_dir, spec), ...]) – iterative to survive deep trees.
    stack: list[tuple[Path, list[tuple[Path, pathspec.PathSpec]]]] = []
    root_spec = _load_gitignore(root)
    stack.append((root, [(root, root_spec)] if root_spec else []))
    counter = 0
    while stack:
        directory, specs = stack.pop()
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError as exc:
            result.errors.append(f"{_rel(root, directory)}: {exc.strerror or exc}")
            continue
        for entry in entries:
            counter += 1
            if check_cancel is not None and counter % 500 == 0:
                check_cancel()
            path = Path(entry.path)
            rel = _rel(root, path)
            if is_link_or_junction(entry):
                result.symlinks.append(rel)
                continue
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError as exc:
                result.errors.append(f"{rel}: {exc}")
                continue
            if _ignored(path, is_dir, specs):
                if is_dir:
                    result.ignored_dirs += 1
                continue
            if is_dir:
                if entry.name in ignores:
                    result.ignored_dirs += 1
                    continue
                child_spec = _load_gitignore(path)
                stack.append((path, specs + [(path, child_spec)] if child_spec else specs))
                continue
            if len(result.files) >= max_files:
                result.truncated = True
                return result
            try:
                size = entry.stat(follow_symlinks=False).st_size
            except OSError as exc:
                result.errors.append(f"{rel}: {exc}")
                continue
            if max_file_bytes is not None and size > max_file_bytes:
                result.skipped_large.append(rel)
                continue
            result.files.append(WalkEntry(rel=rel, size=size))
    result.files.sort(key=lambda f: f.rel)
    return result


def iter_files(result: WalkResult, suffixes: tuple[str, ...]) -> Iterator[WalkEntry]:
    for f in result.files:
        if f.rel.lower().endswith(suffixes):
            yield f


def _ignored(path: Path, is_dir: bool, specs: list[tuple[Path, pathspec.PathSpec]]) -> bool:
    for base, spec in specs:
        rel = path.relative_to(base).as_posix()
        if spec.match_file(rel + "/" if is_dir else rel):
            return True
    return False


def _rel(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix() or "."
    except ValueError:
        return path.as_posix()


def read_text(path: Path, max_bytes: int | None = None) -> str:
    """Decode a source file robustly: UTF-8 (with/without BOM), UTF-16 BOM, then chardet guess."""
    data = path.read_bytes() if max_bytes is None else path.open("rb").read(max_bytes)
    return decode_bytes(data)


def decode_bytes(data: bytes) -> str:
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace")
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    guess = chardet.detect(data[:100_000])
    enc = guess.get("encoding") or "cp950"
    try:
        return data.decode(enc, errors="replace")
    except LookupError:
        return data.decode("utf-8", errors="replace")


def resolve_inside(root: Path, rel: str) -> Path:
    """Resolve `rel` under `root`, rejecting traversal, absolute paths and symlink escapes."""
    if not rel or "\x00" in rel:
        raise ValueError("路徑不可為空")
    candidate = Path(rel)
    if candidate.is_absolute() or candidate.drive or rel.startswith(("/", "\\")):
        raise ValueError(f"不允許絕對路徑：{rel}")
    if any(part == ".." for part in candidate.parts):
        raise ValueError(f"不允許上層目錄：{rel}")
    root_resolved = root.resolve()
    full = (root_resolved / candidate).resolve()
    if full != root_resolved and root_resolved not in full.parents:
        raise ValueError(f"路徑超出專案範圍：{rel}")
    return full
