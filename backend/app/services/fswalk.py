"""Bounded, cancellable project file walker.

- Never follows symlinks or Windows junctions; they are reported instead.
- Cloud placeholders (OneDrive "files on demand") are listed but never read, to avoid downloads.
- Honours .gitignore files at every level plus a built-in ignore list.
- Stops at a file-count limit and reports `truncated` rather than hanging on huge trees.
"""

from __future__ import annotations

import os
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

import pathspec

DEFAULT_IGNORES = frozenset({
    ".git", ".hg", ".svn", "node_modules", ".venv", "venv", "env", "__pycache__", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", ".tox", "dist", "build", ".next", ".nuxt", "coverage", "htmlcov",
    "bin", "obj", ".idea", ".vs", ".vscode", "target", ".gradle", ".turbo", ".cache", "site-packages",
})
FILE_ATTRIBUTE_OFFLINE = 0x1000
FILE_ATTRIBUTE_RECALL_ON_OPEN = 0x40000
FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS = 0x400000
CLOUD_ATTRS = FILE_ATTRIBUTE_OFFLINE | FILE_ATTRIBUTE_RECALL_ON_OPEN | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
FALLBACK_ENCODINGS = ("cp950", "latin-1")
BINARY_SNIFF_BYTES = 8192
NUL = b"\x00"
WINDOWS_DEVICE_NAMES = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)(\..*)?$", re.IGNORECASE)


@dataclass
class WalkEntry:
    rel: str  # POSIX-style path relative to root
    size: int
    # OneDrive-style placeholder: reading content would trigger a download, so analysers skip it.
    cloud_placeholder: bool = False


@dataclass
class WalkResult:
    files: list[WalkEntry] = field(default_factory=list)
    symlinks: list[str] = field(default_factory=list)
    skipped_large: list[str] = field(default_factory=list)
    cloud_placeholders: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    ignored_dirs: int = 0
    truncated: bool = False


def is_link_or_junction(entry: os.DirEntry[str]) -> bool:
    return entry.is_symlink() or entry.is_junction()


def _load_gitignore(directory: Path) -> pathspec.PathSpec | None:
    gi = directory / ".gitignore"
    if not gi.is_file():
        return None
    try:
        lines = read_text(gi).splitlines()
    except (OSError, ValueError):
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
                stack.append((path, [*specs, (path, child_spec)] if child_spec else specs))
                continue
            if len(result.files) >= max_files:
                result.truncated = True
                return result
            try:
                st = entry.stat(follow_symlinks=False)
            except OSError as exc:
                result.errors.append(f"{rel}: {exc}")
                continue
            if max_file_bytes is not None and st.st_size > max_file_bytes:
                result.skipped_large.append(rel)
                continue
            cloud = bool(getattr(st, "st_file_attributes", 0) & CLOUD_ATTRS)
            if cloud:
                result.cloud_placeholders.append(rel)
            result.files.append(WalkEntry(rel=rel, size=st.st_size, cloud_placeholder=cloud))
    result.files.sort(key=lambda f: f.rel)
    return result


def iter_files(result: WalkResult, suffixes: tuple[str, ...]) -> Iterator[WalkEntry]:
    for f in result.files:
        if not f.cloud_placeholder and f.rel.lower().endswith(suffixes):
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


class BinaryFileError(ValueError):
    pass


def read_text(path: Path, max_bytes: int | None = None) -> str:
    return read_text_info(path, max_bytes)[0]


def read_text_info(path: Path, max_bytes: int | None = None) -> tuple[str, str]:
    """Return (text, encoding used). Raises BinaryFileError for files with NUL bytes up front."""
    with path.open("rb") as fh:
        data = fh.read() if max_bytes is None else fh.read(max_bytes)
    return decode_bytes_info(data)


def decode_bytes(data: bytes) -> str:
    return decode_bytes_info(data)[0]


def decode_bytes_info(data: bytes) -> tuple[str, str]:
    """UTF-8 (BOM aware) / UTF-16 BOM, then cp950 (Big5) and latin-1 fallbacks; no guessing library."""
    if data.startswith(b"\xef\xbb\xbf"):
        return data[3:].decode("utf-8", errors="replace"), "utf-8-sig"
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        return data.decode("utf-16", errors="replace"), "utf-16"
    if NUL in data[:BINARY_SNIFF_BYTES]:
        raise BinaryFileError("二進位檔")
    try:
        return data.decode("utf-8"), "utf-8"
    except UnicodeDecodeError:
        pass
    for enc in FALLBACK_ENCODINGS:
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace"), "utf-8-replace"


def resolve_inside(root: Path, rel: str, *, must_exist: bool = True) -> Path:
    r"""Resolve `rel` under `root`. Shared by every path-taking endpoint and by patch validation.

    Rejects absolute, drive-relative (C:x), UNC/device (\\host, \\?\), alternate data streams
    (a::$DATA), '..', device names, and anything whose real path (8.3 names, junctions and
    symlinks resolved) is not inside the real project root.
    """
    if not rel or "\x00" in rel or rel.strip() != rel:
        raise ValueError("路徑不可為空或含控制字元")
    if rel.startswith(("/", "\\")) or ":" in rel:
        raise ValueError(f"不允許絕對路徑、磁碟機代號或資料流：{rel}")
    parts = re.split(r"[\\/]+", rel)
    if any(p == ".." for p in parts):
        raise ValueError(f"不允許上層目錄：{rel}")
    if any(WINDOWS_DEVICE_NAMES.match(p) for p in parts):
        raise ValueError(f"不允許裝置名稱：{rel}")
    real_root = os.path.realpath(root, strict=True)
    joined = os.path.join(real_root, *[p for p in parts if p not in ("", ".")])
    try:
        real = os.path.realpath(joined, strict=must_exist)
    except OSError as exc:
        raise FileNotFoundError(f"路徑不存在：{rel}") from exc
    if os.path.normcase(os.path.commonpath([real, real_root])) != os.path.normcase(real_root):
        raise ValueError(f"路徑超出專案範圍：{rel}")
    return Path(real)
