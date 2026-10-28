"""Heuristic classification of test failures and run-to-run comparison."""

from __future__ import annotations

import re
from collections import Counter
from typing import Any

CATEGORIES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("timeout", re.compile(r"(?i)timeout|timed out|exceeded .*time"), "逾時：檢查等待條件或外部相依"),
    ("import", re.compile(r"(?i)ModuleNotFoundError|ImportError|Cannot find module|Could not load file or assembly"),
     "匯入失敗：缺少依賴或路徑設定錯誤"),
    ("syntax", re.compile(r"(?i)SyntaxError|IndentationError|Unexpected token|error CS\d+"), "語法或編譯錯誤"),
    ("snapshot", re.compile(r"(?i)snapshot"), "快照不符：確認是否為預期變更"),
    ("file_not_found", re.compile(r"(?i)FileNotFoundError|\bENOENT\b|No such file|FileNotFoundException"),
     "找不到檔案：檢查工作目錄或測試資料"),
    ("connection",
     re.compile(r"(?i)ConnectionRefused|\bECONNREFUSED\b|connection refused|getaddrinfo"
                r"|\bENOTFOUND\b"),
     "連線失敗：測試依賴外部服務"),
    ("type", re.compile(r"(?i)TypeError|NullReferenceException|InvalidCastException"), "型別或空值錯誤"),
    ("attribute", re.compile(r"(?i)AttributeError|is not a function|has no attribute"), "屬性/方法不存在"),
    ("assertion", re.compile(r"(?i)AssertionError|assert |expect\(|Expected|Assert\.\w+ Failure"),
     "斷言失敗：實際結果與預期不同"),
)
_FRAME = re.compile(
    r'(?:File "(?P<py>[^"]+)", line (?P<pyl>\d+))|(?P<js>[\w./\\-]+\.(?:[cm]?[jt]sx?|py|cs)):(?P<jsl>\d+)'
)


def classify(message: str | None) -> tuple[str, str]:
    text = message or ""
    for name, pattern, hint in CATEGORIES:
        if pattern.search(text):
            return name, hint
    return "other", "無法自動分類，請檢視完整輸出"


def first_location(message: str | None, project_markers: tuple[str, ...] = ()) -> dict[str, Any] | None:
    """First traceback frame, preferring frames that look like project files (not site-packages)."""
    frames = []
    for m in _FRAME.finditer(message or ""):
        path = m.group("py") or m.group("js")
        line = int(m.group("pyl") or m.group("jsl"))
        frames.append((path.replace("\\", "/"), line))
    if not frames:
        return None
    for path, line in frames:
        if "site-packages" not in path and "node_modules" not in path and (
                not project_markers or any(mk in path for mk in project_markers)):
            return {"file": path, "line": line}
    return {"file": frames[0][0], "line": frames[0][1]}


def analyse(cases: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Group failed/error cases by category; returns [{category, hint, count, tests:[...]}]."""
    groups: dict[str, dict[str, Any]] = {}
    for c in cases:
        if c["status"] not in ("failed", "error"):
            continue
        cat, hint = classify(c.get("message"))
        g = groups.setdefault(cat, {"category": cat, "hint": hint, "count": 0, "tests": []})
        g["count"] += 1
        if len(g["tests"]) < 50:
            g["tests"].append({"name": c["name"], "location": first_location(c.get("message")),
                               "summary": (c.get("message") or "").strip().splitlines()[0][:300]
                               if c.get("message") else None})
    return sorted(groups.values(), key=lambda g: -g["count"])


def compare_runs(a: dict[str, str], b: dict[str, str]) -> dict[str, Any]:
    """a/b map test name -> status. Classifies the transition of every test."""
    bad = {"failed", "error"}
    result: dict[str, list[str]] = {"newly_failing": [], "fixed": [], "still_failing": [], "added": [],
                                    "removed": [], "status_changed": []}
    for name in sorted(a.keys() | b.keys()):
        sa, sb = a.get(name), b.get(name)
        if sa is None:
            result["added"].append(name)
        elif sb is None:
            result["removed"].append(name)
        elif sa not in bad and sb in bad:
            result["newly_failing"].append(name)
        elif sa in bad and sb not in bad:
            result["fixed"].append(name)
        elif sa in bad and sb in bad:
            result["still_failing"].append(name)
        elif sa != sb:
            result["status_changed"].append(name)
    counts = Counter({k: len(v) for k, v in result.items()})
    return {"counts": dict(counts), **{k: v[:500] for k, v in result.items()}}
