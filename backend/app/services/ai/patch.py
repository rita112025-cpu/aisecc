"""Apply validated AI edits inside the workspace, build the diff, and apply/revert on the real project."""

from __future__ import annotations

import ast
import difflib
import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path

from app.services.ai.schema import Edit
from app.services.fswalk import BinaryFileError, decode_bytes_info, resolve_inside

MAX_FILE_BYTES = 1_000_000


class PatchError(ValueError):
    pass


@dataclass
class FileChange:
    path: str
    before: bytes
    after: bytes

    @property
    def before_sha(self) -> str:
        return sha256(self.before)

    @property
    def after_sha(self) -> str:
        return sha256(self.after)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_sha(path: Path) -> str | None:
    return sha256(path.read_bytes()) if path.is_file() else None


def _decode(data: bytes, path: str) -> tuple[str, str]:
    try:
        text, enc = decode_bytes_info(data)
    except BinaryFileError as exc:
        raise PatchError(f"{path} 是二進位檔，不允許修改") from exc
    if enc not in ("utf-8", "utf-8-sig"):
        raise PatchError(f"{path} 不是 UTF-8 編碼（{enc}），為避免破壞內容不自動修改")
    return text, enc


def apply_edits(workspace: Path, edits: list[Edit], tracked: set[str]) -> list[FileChange]:
    """Apply search/replace edits to files in the workspace. Each search must match exactly once
    (after normalising line endings); the file's original newline style and BOM are preserved."""
    grouped: dict[str, list[Edit]] = {}
    for e in edits:
        grouped.setdefault(e.path, []).append(e)
    changes = []
    for rel, file_edits in grouped.items():
        if rel not in tracked:
            raise PatchError(f"{rel} 不是 Git 追蹤中的檔案，不允許修改")
        try:
            full = resolve_inside(workspace, rel)
        except (ValueError, FileNotFoundError) as exc:
            raise PatchError(f"路徑不合法：{exc}") from exc
        if full.is_symlink() or not full.is_file():
            raise PatchError(f"{rel} 不是一般檔案")
        before = full.read_bytes()
        if len(before) > MAX_FILE_BYTES:
            raise PatchError(f"{rel} 超過 {MAX_FILE_BYTES} bytes，不自動修改")
        text, enc = _decode(before, rel)
        crlf = "\r\n" in text
        work = text.replace("\r\n", "\n")
        for e in file_edits:
            search = e.search.replace("\r\n", "\n")
            count = work.count(search)
            if count != 1:
                raise PatchError(f"{rel}：要取代的內容出現 {count} 次（必須剛好 1 次）")
            work = work.replace(search, e.replace.replace("\r\n", "\n"), 1)
        if crlf:
            work = work.replace("\n", "\r\n")
        after = (b"\xef\xbb\xbf" if enc == "utf-8-sig" else b"") + work.encode("utf-8")
        if after == before:
            raise PatchError(f"{rel}：修改後內容沒有變化")
        full.write_bytes(after)
        changes.append(FileChange(rel, before, after))
    return changes


def unified_diff(changes: list[FileChange]) -> str:
    parts = []
    for c in changes:
        a = _decode(c.before, c.path)[0].replace("\r\n", "\n").splitlines(keepends=True)
        b = _decode(c.after, c.path)[0].replace("\r\n", "\n").splitlines(keepends=True)
        parts.extend(difflib.unified_diff(a, b, f"a/{c.path}", f"b/{c.path}"))
    return "".join(p if p.endswith("\n") else p + "\n\\ No newline at end of file\n" for p in parts)


def syntax_check(workspace: Path, changes: list[FileChange]) -> list[str]:
    errors = []
    for c in changes:
        if c.path.endswith(".py"):
            try:
                ast.parse(c.after.decode("utf-8-sig"), filename=c.path)
            except SyntaxError as exc:
                errors.append(f"{c.path}:{exc.lineno}: {exc.msg}")
    return errors


def atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_name(f".{path.name}.aisecc-tmp")
    tmp.write_bytes(data)
    try:
        shutil.copymode(path, tmp)
    except OSError:
        pass
    os.replace(tmp, path)
