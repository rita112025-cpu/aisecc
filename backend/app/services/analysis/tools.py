"""External analyser adapters.

Tier "auto": runs our own pinned tool from the control-center venv over project files, never
executes project code and never touches the network (ruff, bandit).
Tier "project_code": executes tools/config shipped inside the project (eslint, tsc) and requires
the project's `allow_project_tools` authorisation.
Tier "network": sends dependency lists or fetches rules over the network (npm audit, pip-audit,
semgrep) and requires `allow_network_audit`.

A tool that did not run is never reported as passing: its state says why.
"""

from __future__ import annotations

import json
import re
import shutil
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from app.services.analysis.base import RawFinding, normalise_severity
from app.services.procs import ProcResult, RunOptions, run_command

VENV_SCRIPTS = Path(sys.executable).parent
RUFF_SELECT = "E,F,W,B,S,UP,BLE,PL"
EXCLUDES = ("node_modules", ".venv", "venv", "dist", "build", ".git", "__pycache__", "site-packages")


@dataclass
class ToolContext:
    root: Path
    languages: list[str]
    manifests: list[str]
    allow_project_tools: bool
    allow_network_audit: bool
    timeout_sec: float
    cancel_event: threading.Event | None = None
    log_path: Path | None = None
    on_start: Callable[[int, float], None] | None = None


@dataclass
class ToolResult:
    # ok | not_installed | needs_authorization | not_applicable | skipped | error | timeout | cancelled
    state: str
    findings: list[RawFinding] = field(default_factory=list)
    message: str = ""
    duration_sec: float = 0.0
    command: list[str] | None = None

    def status_dict(self) -> dict[str, Any]:
        return {"state": self.state, "message": self.message, "duration_sec": round(self.duration_sec, 2),
                "findings": len(self.findings), "command": self.command}


@dataclass
class Tool:
    name: str
    tier: str  # auto | project_code | network
    applies: Callable[[ToolContext], bool]
    run: Callable[[ToolContext], ToolResult]
    # Checked before authorisation so an absent optional tool reports not_installed.
    installed: Callable[[], bool] = lambda: True


def _rel(root: Path, filename: str) -> str:
    p = Path(filename)
    try:
        return (p if p.is_absolute() else (root / p)).resolve().relative_to(root.resolve()).as_posix()
    except (ValueError, OSError):
        return filename.replace("\\", "/")


def _exec(ctx: ToolContext, argv: list[str], cwd: Path | None = None) -> ProcResult:
    return run_command(argv, RunOptions(
        cwd=cwd or ctx.root, timeout_sec=ctx.timeout_sec, cancel_event=ctx.cancel_event,
        log_path=ctx.log_path, on_start=ctx.on_start, max_memory_mb=4096,
    ))


def _outcome_failure(res: ProcResult) -> ToolResult | None:
    if res.outcome == "finished":
        return None
    state = {"timeout": "timeout", "cancelled": "cancelled", "not_found": "not_installed"}.get(res.outcome, "error")
    return ToolResult(state, message=res.error or res.outcome, duration_sec=res.duration_sec, command=res.argv)


def _json_or_error(res: ProcResult, text: str | None = None) -> tuple[Any, ToolResult | None]:
    raw = text if text is not None else res.output
    start = min((i for i in (raw.find("{"), raw.find("[")) if i >= 0), default=-1)
    if start < 0:
        return None, ToolResult("error", message=f"輸出不是 JSON（exit {res.exit_code}）：{raw[:300]}",
                                duration_sec=res.duration_sec, command=res.argv)
    try:
        return json.JSONDecoder().raw_decode(raw[start:])[0], None
    except json.JSONDecodeError as exc:
        return None, ToolResult("error", message=f"JSON 解析失敗：{exc}", duration_sec=res.duration_sec,
                                command=res.argv)


# ---- ruff ----------------------------------------------------------------------------------

def _ruff_exe() -> str | None:
    local = VENV_SCRIPTS / ("ruff.exe" if sys.platform == "win32" else "ruff")
    return str(local) if local.exists() else shutil.which("ruff")


def _ruff_category(code: str) -> tuple[str, str]:
    if code.startswith("S"):
        return "security", "medium"
    if code.startswith(("F", "B", "BLE")):
        return "lint", "medium"
    if code.startswith("E9"):
        return "lint", "high"
    return "lint", "low"


def run_ruff(ctx: ToolContext) -> ToolResult:
    exe = _ruff_exe()
    if exe is None:
        return ToolResult("not_installed", message="找不到 ruff")
    # --isolated: ignore project config (it could extend files outside the project).
    argv = [exe, "check", "--isolated", "--no-cache", "--exit-zero", "--output-format", "json",
            "--select", RUFF_SELECT, "--extend-exclude", ",".join(EXCLUDES), "."]
    res = _exec(ctx, argv)
    if fail := _outcome_failure(res):
        return fail
    data, err = _json_or_error(res)
    if err:
        return err
    findings = []
    for item in data:
        code = item.get("code") or "syntax-error"
        category, severity = _ruff_category(code)
        loc, end = item.get("location") or {}, item.get("end_location") or {}
        findings.append(RawFinding(
            "ruff", code, severity, category, item.get("message", ""), _rel(ctx.root, item.get("filename", "")),
            loc.get("row"), loc.get("column"), end.get("row"), "reported", {"url": item.get("url")}))
    return ToolResult("ok", findings, duration_sec=res.duration_sec, command=argv)


# ---- bandit --------------------------------------------------------------------------------

def run_bandit(ctx: ToolContext) -> ToolResult:
    local = VENV_SCRIPTS / ("bandit.exe" if sys.platform == "win32" else "bandit")
    exe = str(local) if local.exists() else shutil.which("bandit")
    if exe is None:
        return ToolResult("not_installed", message="找不到 bandit")
    excludes = ",".join(f"./{d}" for d in EXCLUDES) + ",./tests,./test"
    argv = [exe, "-r", ".", "-f", "json", "-q", "-x", excludes]
    res = _exec(ctx, argv)
    if fail := _outcome_failure(res):
        return fail
    data, err = _json_or_error(res)
    if err:
        return err
    findings = []
    for r in data.get("results", []):
        conf = (r.get("issue_confidence") or "").lower()
        findings.append(RawFinding(
            "bandit", r.get("test_id", "B000"), normalise_severity(r.get("issue_severity")), "security",
            f"{r.get('issue_text', '')}（工具信心：{conf or '未知'}）", _rel(ctx.root, r.get("filename", "")),
            r.get("line_number"), r.get("col_offset"), (r.get("line_range") or [None])[-1], "reported",
            {"tool_confidence": conf, "cwe": (r.get("issue_cwe") or {}).get("id"), "url": r.get("more_info")}))
    errors = data.get("errors") or []
    msg = f"{len(errors)} 個檔案無法解析" if errors else ""
    return ToolResult("ok", findings, msg, res.duration_sec, argv)


# ---- node based (project code) -------------------------------------------------------------

def _package_dirs(ctx: ToolContext) -> list[Path]:
    dirs = [ctx.root / Path(m).parent for m in ctx.manifests if Path(m).name == "package.json"]
    return sorted({d for d in dirs if "node_modules" not in d.parts})


def _node_bin(pkg_dir: Path, package: str, bin_name: str) -> Path | None:
    """Resolve a package's bin script so it runs via node.exe directly (never through a .cmd shim)."""
    pkg_json = pkg_dir / "node_modules" / package / "package.json"
    if not pkg_json.is_file():
        return None
    try:
        meta = json.loads(pkg_json.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    bins = meta.get("bin")
    rel = bins if isinstance(bins, str) else (bins or {}).get(bin_name)
    if not rel:
        return None
    target = (pkg_json.parent / rel).resolve()
    return target if target.is_file() and pkg_json.parent.resolve() in target.parents else None


def run_eslint(ctx: ToolContext) -> ToolResult:
    node = shutil.which("node")
    if node is None:
        return ToolResult("not_installed", message="找不到 Node.js")
    findings: list[RawFinding] = []
    ran, total, messages, cmd = 0, 0.0, [], None
    for d in _package_dirs(ctx):
        script = _node_bin(d, "eslint", "eslint")
        if script is None:
            continue
        cmd = [node, str(script), "-f", "json", "--no-error-on-unmatched-pattern", "."]
        res = _exec(ctx, cmd, d)
        total += res.duration_sec
        if fail := _outcome_failure(res):
            return fail
        if res.exit_code not in (0, 1):
            messages.append(f"{d.name}: eslint exit {res.exit_code}（設定錯誤？）{res.output[-300:]}")
            continue
        data, err = _json_or_error(res)
        if err:
            return err
        ran += 1
        for file in data:
            for m in file.get("messages", []):
                findings.append(RawFinding(
                    "eslint", m.get("ruleId") or "parse-error", "medium" if m.get("severity") == 2 else "low",
                    "lint", m.get("message", ""), _rel(ctx.root, file.get("filePath", "")), m.get("line"),
                    m.get("column"), m.get("endLine")))
    if ran == 0:
        state = "error" if messages else "not_installed"
        return ToolResult(state, message="; ".join(messages) or "專案 node_modules 中沒有 eslint", duration_sec=total)
    return ToolResult("ok", findings, "; ".join(messages), total, cmd)


_TSC_LINE = re.compile(
    r"^(?P<file>.+?)\((?P<line>\d+),(?P<col>\d+)\): (?P<sev>error|warning) (?P<code>TS\d+): (?P<msg>.*)$"
)


def run_tsc(ctx: ToolContext) -> ToolResult:
    node = shutil.which("node")
    if node is None:
        return ToolResult("not_installed", message="找不到 Node.js")
    findings: list[RawFinding] = []
    ran, total, cmd = 0, 0.0, None
    for d in _package_dirs(ctx):
        script = _node_bin(d, "typescript", "tsc")
        if script is None or not (d / "tsconfig.json").is_file():
            continue
        cmd = [node, str(script), "--noEmit", "--pretty", "false", "-p", "tsconfig.json"]
        res = _exec(ctx, cmd, d)
        total += res.duration_sec
        if fail := _outcome_failure(res):
            return fail
        ran += 1
        for line in res.output.splitlines():
            m = _TSC_LINE.match(line.strip())
            if m:
                findings.append(RawFinding(
                    "tsc", m["code"], "high" if m["sev"] == "error" else "medium", "type", m["msg"],
                    _rel(ctx.root, str(d / m["file"])),
                    int(m["line"]), int(m["col"])))
    if ran == 0:
        return ToolResult("not_installed", message="專案 node_modules 中沒有 typescript 或缺 tsconfig.json")
    return ToolResult("ok", findings, duration_sec=total, command=cmd)


# ---- network audits ------------------------------------------------------------------------

def run_npm_audit(ctx: ToolContext) -> ToolResult:
    npm = shutil.which("npm")
    if npm is None:
        return ToolResult("not_installed", message="找不到 npm")
    findings: list[RawFinding] = []
    ran, total, cmd, skipped = 0, 0.0, None, []
    for d in _package_dirs(ctx):
        lock = d / "package-lock.json"
        if not lock.is_file():
            skipped.append(f"{_rel(ctx.root, str(d)) or '.'} 無 package-lock.json")
            continue
        cmd = [npm, "audit", "--json", "--package-lock-only", "--ignore-scripts"]
        res = _exec(ctx, cmd, d)
        total += res.duration_sec
        if fail := _outcome_failure(res):
            return fail
        data, err = _json_or_error(res)
        if err:
            return err
        if "error" in data and not data.get("vulnerabilities"):
            return ToolResult("error", message=str(data["error"])[:500], duration_sec=total, command=cmd)
        ran += 1
        manifest = _rel(ctx.root, str(lock))
        for name, v in (data.get("vulnerabilities") or {}).items():
            titles = [x.get("title") for x in v.get("via", []) if isinstance(x, dict) and x.get("title")]
            urls = [x.get("url") for x in v.get("via", []) if isinstance(x, dict) and x.get("url")]
            findings.append(RawFinding(
                "npm-audit", f"npm:{name}", normalise_severity(v.get("severity")), "dependency",
                f"{name} {v.get('range', '')}：{'; '.join(titles) or '受相依套件影響'}", manifest, None,
                extra={"package": name, "fix_available": v.get("fixAvailable"), "urls": urls}))
    if ran == 0:
        return ToolResult("skipped", message="; ".join(skipped) or "沒有 package.json", duration_sec=total)
    return ToolResult("ok", findings, "; ".join(skipped), total, cmd)


def run_pip_audit(ctx: ToolContext) -> ToolResult:
    local = VENV_SCRIPTS / ("pip-audit.exe" if sys.platform == "win32" else "pip-audit")
    exe = str(local) if local.exists() else shutil.which("pip-audit")
    if exe is None:
        return ToolResult("not_installed", message="找不到 pip-audit")
    reqs = [m for m in ctx.manifests if re.search(r"(^|/)requirements[^/]*\.txt$", m, re.I)]
    if not reqs:
        return ToolResult("skipped", message="沒有 requirements*.txt（pip-audit 僅稽核已固定版本的需求檔）")
    findings: list[RawFinding] = []
    total, cmd, notes, ran = 0.0, None, [], 0
    for rel in reqs:
        # --no-deps --disable-pip: never build/install anything; requires pinned requirements.
        cmd = [exe, "-r", rel, "--no-deps", "--disable-pip", "-f", "json", "--progress-spinner", "off"]
        res = _exec(ctx, cmd)
        total += res.duration_sec
        if fail := _outcome_failure(res):
            return fail
        data, err = _json_or_error(res)
        if err:
            notes.append(f"{rel}: 無法稽核（可能有未固定版本的套件）")
            continue
        ran += 1
        deps = data.get("dependencies", data) if isinstance(data, dict) else data
        for dep in deps or []:
            for vuln in dep.get("vulns", []):
                fixes = ", ".join(vuln.get("fix_versions") or []) or "無"
                findings.append(RawFinding(
                    "pip-audit", vuln.get("id", "PYSEC"), "high", "dependency",
                    f"{dep.get('name')} {dep.get('version')}：{vuln.get('id')}（修正版本：{fixes}）", rel, None,
                    extra={"package": dep.get("name"), "aliases": vuln.get("aliases", []),
                           "description": (vuln.get("description") or "")[:500]}))
    if ran == 0:
        return ToolResult("error", message="; ".join(notes), duration_sec=total, command=cmd)
    return ToolResult("ok", findings, "; ".join(notes), total, cmd)


def run_semgrep(ctx: ToolContext) -> ToolResult:
    exe = shutil.which("semgrep")
    if exe is None:
        return ToolResult("not_installed", message="semgrep 未安裝（選用工具）")
    cmd = [exe, "scan", "--config", "p/default", "--json", "--metrics=off", "--disable-version-check", "."]
    res = _exec(ctx, cmd)
    if fail := _outcome_failure(res):
        return fail
    data, err = _json_or_error(res)
    if err:
        return err
    findings = [RawFinding(
        "semgrep", r.get("check_id", "semgrep"), normalise_severity((r.get("extra") or {}).get("severity")),
        "security", (r.get("extra") or {}).get("message", ""), _rel(ctx.root, r.get("path", "")),
        (r.get("start") or {}).get("line"), (r.get("start") or {}).get("col"), (r.get("end") or {}).get("line"))
        for r in data.get("results", [])]
    return ToolResult("ok", findings, duration_sec=res.duration_sec, command=cmd)


def _has(lang: str) -> Callable[[ToolContext], bool]:
    return lambda ctx: lang in ctx.languages


def _has_js(ctx: ToolContext) -> bool:
    return bool({"javascript", "typescript"} & set(ctx.languages))


TOOLS: tuple[Tool, ...] = (
    Tool("ruff", "auto", _has("python"), run_ruff),
    Tool("bandit", "auto", _has("python"), run_bandit),
    Tool("eslint", "project_code", _has_js, run_eslint),
    Tool("tsc", "project_code", _has("typescript"), run_tsc),
    Tool("npm-audit", "network", _has_js, run_npm_audit),
    Tool("pip-audit", "network", _has("python"), run_pip_audit),
    Tool("semgrep", "network", lambda ctx: True, run_semgrep, lambda: shutil.which("semgrep") is not None),
)


def authorised(tool: Tool, ctx: ToolContext) -> tuple[bool, str]:
    if tool.tier == "project_code" and not ctx.allow_project_tools:
        return False, "此工具會執行專案內的程式碼/設定，需在專案設定開啟「允許執行專案工具」"
    if tool.tier == "network" and not ctx.allow_network_audit:
        return False, "此工具會將依賴清單或程式碼送往外部服務，需在專案設定開啟「允許網路稽核」"
    return True, ""
