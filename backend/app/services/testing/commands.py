"""Test command suggestion, approval hashing and runner-specific arguments.

Detection only reads files. Suggested commands are stored unapproved; nothing here executes code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath

from app.services.analysis.tools import node_bin
from app.services.detect import Detection

FRAMEWORKS = ("pytest", "vitest", "playwright", "dotnet")
KEY_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,59}$")


@dataclass
class CommandSpec:
    key: str
    framework: str
    argv: list[str]
    cwd: str = "."
    timeout_sec: int = 600
    max_memory_mb: int = 2048

    def to_dict(self) -> dict:
        return asdict(self)


def command_hash(framework: str, argv: list[str], cwd: str, timeout_sec: int, max_memory_mb: int) -> str:
    payload = json.dumps([framework, argv, cwd, timeout_sec, max_memory_mb], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _python_for(root: Path, cwd: str) -> str:
    """Project venv interpreter (relative to cwd) if present, else `python` from PATH."""
    rel_exe = "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
    for base in (root / cwd, root):
        for venv in (".venv", "venv"):
            exe = base / venv / rel_exe
            if exe.is_file():
                return Path(os.path.relpath(exe, root / cwd)).as_posix()
    return "python"


def suggest_commands(root: Path, det: Detection, rels: list[str]) -> list[CommandSpec]:
    specs: list[CommandSpec] = []
    if "pytest" in det.test_frameworks:
        cfgs = [r for r in rels if PurePosixPath(r).name in ("pytest.ini", "pyproject.toml", "setup.cfg", "tox.ini")]
        cwd = min((str(PurePosixPath(r).parent) for r in cfgs), key=len, default=".")
        specs.append(CommandSpec("pytest", "pytest", [_python_for(root, cwd), "-m", "pytest"], cwd))
    pkg_dirs = sorted({str(PurePosixPath(m).parent) for m in det.manifests if PurePosixPath(m).name == "package.json"})
    for d in pkg_dirs:
        base = root / d
        suffix = "" if d == "." else "-" + re.sub(r"[^a-z0-9]+", "-", d.lower()).strip("-")
        vitest = node_bin(base, "vitest", "vitest")
        if vitest is not None:
            specs.append(CommandSpec(f"vitest{suffix}"[:60], "vitest",
                                     ["node", vitest.relative_to(base.resolve()).as_posix(), "run"], d))
        pw = node_bin(base, "@playwright/test", "playwright")
        if pw is not None:
            specs.append(CommandSpec(f"playwright{suffix}"[:60], "playwright",
                                     ["node", pw.relative_to(base.resolve()).as_posix(), "test"], d, 1800, 4096))
    if "dotnet" in det.test_frameworks:
        target = next((r for r in rels if r.lower().endswith(".sln")), None) or next(
            (m for m in det.manifests if m.lower().endswith(".csproj") and "test" in m.lower()), None)
        specs.append(CommandSpec("dotnet", "dotnet", ["dotnet", "test", *([target] if target else [])], ".", 1800,
                                 4096))
    return specs


def reporter_args(framework: str, out_dir: Path, coverage: bool, pytest_cov: bool) -> tuple[list[str], dict[str, str]]:
    """Arguments appended by the system (never part of the approved command) to collect results."""
    junit = out_dir / "results.xml"
    if framework == "pytest":
        args = ["-p", "no:cacheprovider", "--junitxml", str(junit), "-o", "junit_family=legacy"]
        if coverage and pytest_cov:
            args += ["--cov", "--cov-report", f"xml:{out_dir / 'coverage.xml'}"]
        return args, {}
    if framework == "vitest":
        args = ["--reporter=default", "--reporter=junit", f"--outputFile.junit={junit}"]
        if coverage:
            args += ["--coverage.enabled=true", "--coverage.reporter=json-summary",
                     f"--coverage.reportsDirectory={out_dir / 'coverage'}"]
        return args, {}
    if framework == "playwright":
        return ["--reporter=line,junit"], {"PLAYWRIGHT_JUNIT_OUTPUT_NAME": str(junit)}
    if framework == "dotnet":
        args = ["--logger", "trx;LogFileName=results.trx", "--results-directory", str(out_dir)]
        if coverage:
            args += ["--collect", "XPlat Code Coverage"]
        return args, {}
    raise ValueError(f"不支援的框架：{framework}")


def rerun_args(framework: str, tests: list[dict]) -> list[str]:
    """Selection arguments for re-running specific tests. Raises ValueError if none can be mapped."""
    if not tests:
        raise ValueError("沒有可重跑的測試")
    if framework == "pytest":
        ids = [i for i in (_pytest_node_id(t) for t in tests) if i]
        if not ids:
            raise ValueError("無法將失敗測試對應到 pytest node id")
        return ids
    raw = [_raw_name(t["name"]) for t in tests]
    if framework == "vitest":
        return ["-t", "|".join(re.escape(n.split(" > ")[-1]) for n in raw)]
    if framework == "playwright":
        return ["--grep", "|".join(re.escape(n.split(" › ")[-1]) for n in raw)]
    if framework == "dotnet":
        return ["--filter", "|".join(f"FullyQualifiedName={n}" for n in raw)]
    raise ValueError(f"不支援的框架：{framework}")


def _raw_name(name: str) -> str:
    base = re.sub(r" \[#\d+\]$", "", name)
    return base.rsplit("::", 1)[-1]


def _pytest_node_id(t: dict) -> str | None:
    file, classname, name = t.get("file"), t.get("classname"), _raw_name(t["name"])
    if not file:
        return None
    module = PurePosixPath(file.replace("\\", "/")).with_suffix("").as_posix().replace("/", ".")
    parts = [file.replace("\\", "/")]
    if classname and classname.startswith(module + "."):
        parts += classname[len(module) + 1:].split(".")
    parts.append(name)
    return "::".join(parts)
