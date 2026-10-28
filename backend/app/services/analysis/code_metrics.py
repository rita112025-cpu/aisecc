"""Built-in structural analysers: complexity, function size, duplication, import cycles, unused candidates.

All of these are static heuristics over source text/AST and never execute project code.
"""

from __future__ import annotations

import ast
import hashlib
import re
from collections import defaultdict
from pathlib import PurePosixPath

from app.services.analysis.base import RawFinding, SourceFile, SourceIndex

JS_SUFFIXES = (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs", ".mts", ".cts")


# ---- complexity / size ---------------------------------------------------------------------

_BRANCH_NODES = (ast.If, ast.For, ast.AsyncFor, ast.While, ast.ExceptHandler, ast.With, ast.AsyncWith,
                 ast.Assert, ast.IfExp, ast.comprehension, ast.Match)


def _cyclomatic(fn: ast.AST) -> int:
    score = 1
    for node in ast.walk(fn):
        if node is not fn and isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.Lambda):
            continue
        if isinstance(node, _BRANCH_NODES):
            score += 1
        elif isinstance(node, ast.BoolOp):
            score += len(node.values) - 1
        elif isinstance(node, ast.match_case):
            score += 1
    return score


def python_complexity(index: SourceIndex, complexity_limit: int, length_limit: int) -> tuple[
        list[RawFinding], list[str]]:
    findings: list[RawFinding] = []
    errors: list[str] = []
    for f in index.by_suffix(".py"):
        try:
            tree = ast.parse(f.text, filename=f.rel)
        except (SyntaxError, ValueError) as exc:
            errors.append(f"{f.rel}: 無法解析（{exc.__class__.__name__}）")
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            cc = _cyclomatic(node)
            length = (node.end_lineno or node.lineno) - node.lineno + 1
            if cc > complexity_limit:
                findings.append(RawFinding(
                    "builtin-metrics", "high-complexity", "medium" if cc <= complexity_limit * 2 else "high",
                    "complexity", f"函式 {node.name} 循環複雜度 {cc}（門檻 {complexity_limit}）",
                    f.rel, node.lineno, node.col_offset + 1, node.end_lineno, "reported",
                    {"complexity": cc, "function": node.name},
                ))
            if length > length_limit:
                findings.append(RawFinding(
                    "builtin-metrics", "long-function", "low", "complexity",
                    f"函式 {node.name} 長度 {length} 行（門檻 {length_limit}）",
                    f.rel, node.lineno, node.col_offset + 1, node.end_lineno, "reported",
                    {"length": length, "function": node.name},
                ))
    return findings, errors


_JS_FUNC = re.compile(
    r"(?:function\s*\*?\s*([A-Za-z_$][\w$]*)?\s*\(|([A-Za-z_$][\w$]*)\s*[:=]\s*(?:async\s*)?\([^)]*\)\s*=>\s*\{|"
    r"^\s*(?:async\s+)?([A-Za-z_$][\w$]*)\s*\([^)]*\)\s*\{)"
)
_JS_BRANCH = re.compile(r"\b(if|for|while|case|catch)\b|&&|\|\||\?\?|\?(?!\.)")


def js_complexity(index: SourceIndex, complexity_limit: int, length_limit: int) -> list[RawFinding]:
    """Brace-matching heuristic for JS/TS; strings/comments are not fully parsed, so results are heuristic."""
    findings: list[RawFinding] = []
    for f in index.by_suffix(*JS_SUFFIXES):
        lines = f.lines
        for i, line in enumerate(lines):
            m = _JS_FUNC.search(line)
            if not m or "{" not in line[m.start():]:
                continue
            name = next((g for g in m.groups() if g), "(匿名)")
            if name in ("if", "for", "while", "switch", "catch"):
                continue
            depth, end = 0, None
            for j in range(i, min(len(lines), i + 5000)):
                segment = lines[j] if j != i else lines[j][m.start():]
                depth += segment.count("{") - segment.count("}")
                if depth <= 0 and j >= i and "{" in "".join(lines[i:j + 1]):
                    end = j
                    break
            if end is None:
                continue
            body = "\n".join(lines[i:end + 1])
            cc = 1 + len(_JS_BRANCH.findall(body))
            length = end - i + 1
            if cc > complexity_limit:
                findings.append(RawFinding(
                    "builtin-metrics", "high-complexity", "medium", "complexity",
                    f"函式 {name} 估計複雜度 {cc}（門檻 {complexity_limit}）", f.rel, i + 1, None, end + 1,
                    "heuristic", {"complexity": cc, "function": name}))
            if length > length_limit:
                findings.append(RawFinding(
                    "builtin-metrics", "long-function", "low", "complexity",
                    f"函式 {name} 長度 {length} 行（門檻 {length_limit}）", f.rel, i + 1, None, end + 1,
                    "heuristic", {"length": length, "function": name}))
    return findings


# ---- duplication ---------------------------------------------------------------------------

def _normalise(line: str) -> str:
    s = line.strip()
    if not s or s.startswith(("#", "//", "/*", "*", "import ", "from ", "using ")) or s in ("{", "}", ")", "];", "});"):
        return ""
    return re.sub(r"\s+", " ", s)


def duplication(index: SourceIndex, window: int = 6, max_reports: int = 500) -> list[RawFinding]:
    """Rolling hash over `window` consecutive meaningful lines; reports each duplicate block once."""
    blocks: dict[str, list[tuple[str, int]]] = defaultdict(list)
    for f in index.by_suffix(".py", ".cs", *JS_SUFFIXES):
        meaningful = [(n, _normalise(line)) for n, line in enumerate(f.lines, start=1)]
        meaningful = [(n, s) for n, s in meaningful if s]
        for k in range(len(meaningful) - window + 1):
            chunk = meaningful[k:k + window]
            if sum(len(s) for _, s in chunk) < 120:
                continue
            digest = hashlib.sha1("\n".join(s for _, s in chunk).encode("utf-8"), usedforsecurity=False).hexdigest()
            blocks[digest].append((f.rel, chunk[0][0]))
    findings: list[RawFinding] = []
    reported: set[tuple[str, int]] = set()
    for locations in blocks.values():
        unique = sorted(set(locations))
        if len(unique) < 2:
            continue
        first = unique[0]
        for loc in unique[1:]:
            # Skip windows overlapping an already reported block in the same file (long clones).
            if any(r[0] == loc[0] and abs(r[1] - loc[1]) < window for r in reported):
                continue
            reported.add(loc)
            findings.append(RawFinding(
                "builtin-duplication", "duplicate-block", "low", "duplication",
                f"與 {first[0]}:{first[1]} 有至少 {window} 行重複", loc[0], loc[1], None, None, "heuristic",
                {"original": {"file": first[0], "line": first[1]}, "window": window}))
            if len(findings) >= max_reports:
                return findings
    return findings


# ---- import cycles -------------------------------------------------------------------------

def _py_module_name(rel: str) -> str:
    p = PurePosixPath(rel)
    parts = list(p.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts = parts[:-1]
    if parts and parts[0] in ("src", "lib"):
        parts = parts[1:]
    return ".".join(parts)


def python_import_graph(index: SourceIndex) -> dict[str, set[str]]:
    modules = {_py_module_name(f.rel): f.rel for f in index.by_suffix(".py")}
    graph: dict[str, set[str]] = {rel: set() for rel in modules.values()}
    for name, rel in modules.items():
        try:
            tree = ast.parse(index.files[rel].text)
        except (SyntaxError, ValueError):
            continue
        package = name.rsplit(".", 1)[0] if not rel.endswith("__init__.py") else name
        for node in ast.walk(tree):
            targets: list[str] = []
            if isinstance(node, ast.Import):
                targets = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom):
                base = node.module or ""
                if node.level:
                    pkg_parts = package.split(".") if package else []
                    pkg_parts = pkg_parts[: len(pkg_parts) - (node.level - 1)] if node.level > 1 else pkg_parts
                    base = ".".join([*pkg_parts, base] if base else pkg_parts)
                targets = [base, *(f"{base}.{a.name}" for a in node.names)]
            for t in targets:
                if t in modules and modules[t] != rel:
                    graph[rel].add(modules[t])
    return graph


_JS_IMPORT = re.compile(
    r"""(?:import\s[^'"]*?from\s*|import\s*\(\s*|require\s*\(\s*|export\s[^'"]*?from\s*)['"](\.[^'"]+)['"]"""
)


def js_import_graph(index: SourceIndex) -> dict[str, set[str]]:
    files = {f.rel for f in index.by_suffix(*JS_SUFFIXES)}
    graph: dict[str, set[str]] = {rel: set() for rel in files}
    for rel in files:
        base = PurePosixPath(rel).parent
        for spec in _JS_IMPORT.findall(index.files[rel].text):
            target = _resolve_js(base, spec, files)
            if target and target != rel:
                graph[rel].add(target)
    return graph


def _resolve_js(base: PurePosixPath, spec: str, files: set[str]) -> str | None:
    parts: list[str] = list(base.parts)
    for seg in PurePosixPath(spec).parts:
        if seg == "..":
            if not parts:
                return None
            parts.pop()
        elif seg != ".":
            parts.append(seg)
    stem = "/".join(parts)
    candidates = [stem, *(stem + s for s in JS_SUFFIXES), *(f"{stem}/index{s}" for s in JS_SUFFIXES)]
    if stem.endswith(".js"):
        candidates += [stem[:-3] + ".ts", stem[:-3] + ".tsx"]
    return next((c for c in candidates if c in files), None)


def strongly_connected(graph: dict[str, set[str]]) -> list[list[str]]:
    """Iterative Tarjan; returns components with more than one node."""
    index_of: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    result: list[list[str]] = []
    counter = 0
    for start in sorted(graph):
        if start in index_of:
            continue
        work = [(start, iter(sorted(graph[start])))]
        index_of[start] = low[start] = counter
        counter += 1
        stack.append(start)
        on_stack.add(start)
        while work:
            node, children = work[-1]
            advanced = False
            for child in children:
                if child not in graph:
                    continue
                if child not in index_of:
                    index_of[child] = low[child] = counter
                    counter += 1
                    stack.append(child)
                    on_stack.add(child)
                    work.append((child, iter(sorted(graph[child]))))
                    advanced = True
                    break
                if child in on_stack:
                    low[node] = min(low[node], index_of[child])
            if advanced:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] == index_of[node]:
                comp = []
                while True:
                    w = stack.pop()
                    on_stack.discard(w)
                    comp.append(w)
                    if w == node:
                        break
                if len(comp) > 1:
                    result.append(sorted(comp))
    return sorted(result)


def import_cycles(index: SourceIndex) -> list[RawFinding]:
    findings = []
    for lang, graph in (("python", python_import_graph(index)), ("js", js_import_graph(index))):
        for comp in strongly_connected(graph):
            chain = " → ".join(comp[:8]) + (" …" if len(comp) > 8 else "")
            findings.append(RawFinding(
                "builtin-cycles", f"import-cycle-{lang}", "low", "cycle",
                f"循環依賴（{len(comp)} 個模組）：{chain}", comp[0], 1, None, None, "heuristic",
                {"modules": comp}))
    return findings


# ---- unused code candidates ----------------------------------------------------------------

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def unused_python_candidates(index: SourceIndex) -> list[RawFinding]:
    """Top-level functions/classes whose name never appears elsewhere. Candidates only: dynamic
    access (getattr, entry points, frameworks, decorators registering routes) is invisible here."""
    py_files: list[SourceFile] = index.by_suffix(".py")
    usage: dict[str, int] = defaultdict(int)
    for f in index.files.values():
        for tok in _IDENT.findall(f.text):
            usage[tok] += 1
    findings = []
    for f in py_files:
        if re.search(r"(^|/)(tests?|migrations|alembic)/|(^|/)(test_[^/]*|conftest|setup|__init__|__main__)\.py$",
                     f.rel):
            continue
        try:
            tree = ast.parse(f.text)
        except (SyntaxError, ValueError):
            continue
        exported = _dunder_all(tree)
        for node in tree.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                continue
            name = node.name
            if name.startswith("__") or name in exported or node.decorator_list or name == "main":
                continue
            if usage[name] <= 1:
                kind = "類別" if isinstance(node, ast.ClassDef) else "函式"
                findings.append(RawFinding(
                    "builtin-unused", "unused-candidate", "info", "unused",
                    f"{kind} {name} 在專案中沒有其他參照（候選，請人工確認）",
                    f.rel, node.lineno, node.col_offset + 1, node.end_lineno, "heuristic", {"name": name}))
    return findings


def _dunder_all(tree: ast.Module) -> set[str]:
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == "__all__" for t in node.targets):
            if isinstance(node.value, ast.List | ast.Tuple):
                return {e.value for e in node.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)}
    return set()
