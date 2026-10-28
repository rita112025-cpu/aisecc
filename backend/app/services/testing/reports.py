"""Parsers for test result and coverage files (JUnit XML, TRX, Cobertura, Istanbul json-summary)."""

from __future__ import annotations

import json
import xml.etree.ElementTree as ET  # noqa: S405 - files produced by test runs we launched; expat limits entities
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MAX_MESSAGE = 4000
MAX_REPORT_BYTES = 200_000_000


@dataclass
class CaseResult:
    name: str
    classname: str | None
    file: str | None
    status: str  # passed | failed | error | skipped
    duration_sec: float | None
    message: str | None = None


@dataclass
class ParsedResults:
    cases: list[CaseResult] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def counts(self) -> dict[str, int]:
        out = {"total": len(self.cases), "passed": 0, "failed": 0, "skipped": 0, "errors": 0}
        key = {"passed": "passed", "failed": "failed", "skipped": "skipped", "error": "errors"}
        for c in self.cases:
            out[key[c.status]] += 1
        return out


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _parse_xml(path: Path) -> ET.Element:
    if path.stat().st_size > MAX_REPORT_BYTES:
        raise ValueError("報告檔過大")
    return ET.parse(path).getroot()  # noqa: S314


def parse_junit(path: Path) -> ParsedResults:
    out = ParsedResults()
    root = _parse_xml(path)
    for case in root.iter():
        if _local(case.tag) != "testcase":
            continue
        status, message = "passed", None
        for child in case:
            tag = _local(child.tag)
            if tag in ("failure", "error"):
                status = "failed" if tag == "failure" else "error"
                text = (child.get("message") or "") + ("\n" + child.text if child.text else "")
                message = text.strip()[:MAX_MESSAGE]
                break
            if tag == "skipped":
                status, message = "skipped", (child.get("message") or child.text or "").strip()[:MAX_MESSAGE] or None
        try:
            duration = float(case.get("time")) if case.get("time") else None
        except ValueError:
            duration = None
        out.cases.append(CaseResult(
            name=case.get("name") or "(未命名)", classname=case.get("classname"), file=case.get("file"),
            status=status, duration_sec=duration, message=message,
        ))
    _dedupe(out)
    return out


def parse_trx(path: Path) -> ParsedResults:
    out = ParsedResults()
    root = _parse_xml(path)
    for r in root.iter():
        if _local(r.tag) != "UnitTestResult":
            continue
        outcome = (r.get("outcome") or "").lower()
        status = {"passed": "passed", "failed": "failed", "notexecuted": "skipped", "inconclusive": "skipped",
                  "timeout": "error", "aborted": "error", "error": "error"}.get(outcome, "error")
        message = None
        for el in r.iter():
            if _local(el.tag) in ("Message", "StackTrace") and el.text:
                message = ((message + "\n") if message else "") + el.text.strip()
        out.cases.append(CaseResult(
            name=r.get("testName") or "(未命名)", classname=None, file=None, status=status,
            duration_sec=_trx_duration(r.get("duration")), message=message[:MAX_MESSAGE] if message else None,
        ))
    _dedupe(out)
    return out


def _trx_duration(value: str | None) -> float | None:
    if not value:
        return None
    try:
        h, m, s = value.split(":")
        return int(h) * 3600 + int(m) * 60 + float(s)
    except ValueError:
        return None


def _dedupe(out: ParsedResults) -> None:
    """test_results has UNIQUE(run_id, name): disambiguate repeated names (parametrised, retries)."""
    seen: dict[str, int] = {}
    for c in out.cases:
        base = f"{c.classname}::{c.name}" if c.classname and c.classname not in c.name else c.name
        n = seen.get(base, 0)
        seen[base] = n + 1
        c.name = base if n == 0 else f"{base} [#{n + 1}]"
        if n == 1:
            out.warnings.append(f"重複的測試名稱：{base}")


def parse_cobertura(path: Path) -> dict[str, Any]:
    root = _parse_xml(path)
    if _local(root.tag) != "coverage":
        raise ValueError("不是 Cobertura 格式")
    rate = root.get("line-rate")
    files = []
    for cls in root.iter():
        if _local(cls.tag) == "class" and cls.get("filename"):
            files.append({"file": cls.get("filename").replace("\\", "/"),
                          "line_rate": round(float(cls.get("line-rate") or 0) * 100, 2)})
    return {"format": "cobertura", "percent": round(float(rate) * 100, 2) if rate else None,
            "lines_valid": _int(root.get("lines-valid")), "lines_covered": _int(root.get("lines-covered")),
            "files": sorted(files, key=lambda f: f["line_rate"])[:500]}


def parse_istanbul_summary(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    total = data.get("total", {}).get("lines", {})
    files = [{"file": k.replace("\\", "/"), "line_rate": v.get("lines", {}).get("pct")}
             for k, v in data.items() if k != "total"]
    return {"format": "istanbul", "percent": total.get("pct"), "lines_valid": total.get("total"),
            "lines_covered": total.get("covered"),
            "files": sorted(files, key=lambda f: (f["line_rate"] is None, f["line_rate"]))[:500]}


def _int(v: str | None) -> int | None:
    try:
        return int(v) if v is not None else None
    except ValueError:
        return None
