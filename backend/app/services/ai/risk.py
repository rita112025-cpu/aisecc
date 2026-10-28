"""Regression risk heuristics for a proposed patch. Every factor is listed with its reason."""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from app.services.ai.patch import FileChange
from app.services.fswalk import read_text, walk

SOURCE_SUFFIXES = (".py", ".ts", ".tsx", ".js", ".jsx", ".cs")


def _changed_functions(change: FileChange) -> set[str]:
    if not change.path.endswith(".py"):
        return set()
    names = set()
    try:
        before = ast.parse(change.before.decode("utf-8-sig"))
        after = ast.parse(change.after.decode("utf-8-sig"))
    except (SyntaxError, UnicodeDecodeError):
        return names

    def bodies(tree: ast.AST) -> dict[str, str]:
        return {n.name: ast.dump(n) for n in ast.walk(tree) if isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)}

    b, a = bodies(before), bodies(after)
    return {k for k in b.keys() | a.keys() if b.get(k) != a.get(k)}


def assess(project_root: Path, changes: list[FileChange], diff: str, test_status: str | None,
           ai_risk: str) -> dict[str, Any]:
    factors: list[dict[str, Any]] = []
    score = 0
    changed_lines = sum(1 for line in diff.splitlines() if line[:1] in "+-" and line[:3] not in ("+++", "---"))
    if changed_lines > 40:
        score += 2
        factors.append({"factor": "變更行數", "value": changed_lines, "reason": "超過 40 行"})
    elif changed_lines > 10:
        score += 1
        factors.append({"factor": "變更行數", "value": changed_lines, "reason": "超過 10 行"})
    if len(changes) > 1:
        score += 1
        factors.append({"factor": "變更檔數", "value": len(changes), "reason": "跨多個檔案"})

    functions = set().union(*(_changed_functions(c) for c in changes)) if changes else set()
    if functions:
        refs = _reference_counts(project_root, functions)
        hot = {k: v for k, v in refs.items() if v >= 5}
        if hot:
            score += 2
            factors.append({"factor": "被修改函式的引用數", "value": hot, "reason": "被專案中多處引用"})
        else:
            factors.append({"factor": "被修改函式的引用數", "value": refs, "reason": "引用少"})

    if test_status is None:
        score += 2
        factors.append({"factor": "測試", "value": "未執行", "reason": "沒有在隔離工作區驗證"})
    elif test_status != "passed":
        score += 3
        factors.append({"factor": "測試", "value": test_status, "reason": "隔離工作區測試未全部通過"})
    else:
        factors.append({"factor": "測試", "value": "passed", "reason": "隔離工作區測試通過"})
    if ai_risk == "high":
        score += 1
        factors.append({"factor": "AI 自評", "value": ai_risk, "reason": "模型自評為高風險"})
    level = "high" if score >= 4 else "medium" if score >= 2 else "low"
    return {"level": level, "score": score, "factors": factors, "changed_lines": changed_lines}


def _reference_counts(root: Path, names: set[str]) -> dict[str, int]:
    counts = dict.fromkeys(names, 0)
    patterns = {n: re.compile(rf"\b{re.escape(n)}\b") for n in names}
    for f in walk(root, max_files=20_000, max_file_bytes=500_000).files:
        if f.cloud_placeholder or not f.rel.endswith(SOURCE_SUFFIXES):
            continue
        try:
            text = read_text(root / f.rel)
        except (OSError, ValueError):
            continue
        for n, p in patterns.items():
            counts[n] += len(p.findall(text))
    return {n: max(0, c - 1) for n, c in counts.items()}  # minus the definition itself
