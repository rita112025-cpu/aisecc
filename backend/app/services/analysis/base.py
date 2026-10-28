"""Shared analysis types: raw findings, the loaded source index and fingerprinting."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.fswalk import WalkResult, read_text_info

SEVERITIES = ("critical", "high", "medium", "low", "info")
SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".cs")
TEXT_SUFFIXES = SOURCE_SUFFIXES + (
    ".json", ".yml", ".yaml", ".toml", ".ini", ".cfg", ".env", ".properties", ".xml", ".config", ".txt", ".md",
    ".ps1", ".sh", ".bat", ".cmd",
)


MINIFIED_SUFFIXES = (".min.js", ".min.css", ".map", ".bundle.js")
MINIFIED_AVG_LINE = 300


@dataclass
class RawFinding:
    tool: str
    rule_id: str
    severity: str
    category: str
    message: str
    file_path: str | None = None
    line: int | None = None
    column: int | None = None
    end_line: int | None = None
    # reported: an external tool said so; heuristic: our own pattern guess. Neither is a confirmed defect.
    confidence: str = "reported"
    extra: dict[str, Any] = field(default_factory=dict)
    fingerprint: str = ""
    occurrence: int = 0


@dataclass
class SourceFile:
    rel: str
    text: str
    encoding: str

    @property
    def lines(self) -> list[str]:
        return self.text.splitlines()


@dataclass
class SourceIndex:
    root: Path
    files: dict[str, SourceFile] = field(default_factory=dict)
    skipped: list[str] = field(default_factory=list)

    def by_suffix(self, *suffixes: str) -> list[SourceFile]:
        return [f for rel, f in sorted(self.files.items()) if rel.lower().endswith(suffixes)]

    def line(self, rel: str | None, line: int | None) -> str:
        if rel is None or line is None or rel not in self.files:
            return ""
        lines = self.files[rel].lines
        return lines[line - 1] if 0 < line <= len(lines) else ""


def load_sources(root: Path, walked: WalkResult, max_file_bytes: int) -> SourceIndex:
    index = SourceIndex(root=root)
    for entry in walked.files:
        if entry.cloud_placeholder or not entry.rel.lower().endswith(TEXT_SUFFIXES):
            continue
        if entry.size > max_file_bytes:
            index.skipped.append(entry.rel)
            continue
        if entry.rel.lower().endswith(MINIFIED_SUFFIXES):
            index.skipped.append(entry.rel)
            continue
        try:
            text, enc = read_text_info(root / entry.rel)
        except (OSError, ValueError):
            index.skipped.append(entry.rel)
            continue
        if is_minified(text):
            index.skipped.append(entry.rel)
            continue
        index.files[entry.rel] = SourceFile(entry.rel, text, enc)
    return index


def is_minified(text: str) -> bool:
    """Generated/minified bundles would flood duplication and complexity results."""
    lines = text.count("\n") + 1
    return len(text) > 2000 and len(text) / lines > MINIFIED_AVG_LINE


_WS = re.compile(r"\s+")


def assign_fingerprints(findings: list[RawFinding], index: SourceIndex) -> list[RawFinding]:
    """Line-number independent identity + per-scan occurrence ordinal.

    The fingerprint hashes the whitespace-normalised source line, so moving code keeps the
    identity while editing the flagged line changes it.
    """
    seen: dict[str, int] = {}
    ordered = sorted(findings, key=lambda f: (f.file_path or "", f.line or 0, f.column or 0, f.tool, f.rule_id))
    for f in ordered:
        content = _WS.sub(" ", index.line(f.file_path, f.line)).strip()
        if not content:
            content = _WS.sub(" ", f.message).strip()
        raw = "\x1f".join((f.tool, f.rule_id, f.file_path or "", content))
        f.fingerprint = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        f.occurrence = seen.get(f.fingerprint, 0)
        seen[f.fingerprint] = f.occurrence + 1
    return ordered


def normalise_severity(value: str | None, default: str = "medium") -> str:
    v = (value or "").strip().lower()
    mapping = {"error": "high", "warning": "medium", "warn": "medium", "note": "low", "information": "info",
               "moderate": "medium", "undefined": "low", "unknown": "low"}
    v = mapping.get(v, v)
    return v if v in SEVERITIES else default
