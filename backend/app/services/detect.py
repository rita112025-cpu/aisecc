"""Language, dependency and test-framework detection. Pure file parsing; never executes project code."""

from __future__ import annotations

import json
import re
import tomllib
import xml.etree.ElementTree as ET  # noqa: S405 - parsing local project files only
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.services.fswalk import WalkResult, read_text

EXT_LANG = {
    ".py": "python", ".pyi": "python",
    ".ts": "typescript", ".tsx": "typescript", ".mts": "typescript", ".cts": "typescript",
    ".js": "javascript", ".jsx": "javascript", ".mjs": "javascript", ".cjs": "javascript",
    ".cs": "csharp",
}
REQ_LINE = re.compile(r"^\s*([A-Za-z0-9][A-Za-z0-9._\-]*)(\[[^\]]*\])?\s*(.*?)\s*(?:#.*)?$")
MAX_MANIFEST_BYTES = 5_000_000


@dataclass
class Dependency:
    name: str
    spec: str
    ecosystem: str  # pypi | npm | nuget
    source: str  # manifest relative path
    dev: bool = False

    @property
    def pinned_version(self) -> str | None:
        m = re.fullmatch(r"==\s*([^\s,;]+)", self.spec.strip())
        if m:
            return m.group(1)
        if self.ecosystem in ("npm", "nuget") and re.fullmatch(r"\d+(\.\d+)*([-+][\w.]+)?", self.spec.strip()):
            return self.spec.strip()
        return None


@dataclass
class Detection:
    languages: list[str] = field(default_factory=list)
    language_files: dict[str, int] = field(default_factory=dict)
    manifests: list[str] = field(default_factory=list)
    dependencies: list[Dependency] = field(default_factory=list)
    test_frameworks: list[str] = field(default_factory=list)
    package_manager: str | None = None
    has_node_modules: bool = False
    python_envs: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["dependencies"] = [asdict(x) | {"pinned_version": x.pinned_version} for x in self.dependencies]
        return d


def detect(root: Path, walked: WalkResult) -> Detection:
    det = Detection()
    rels = [f.rel for f in walked.files]
    relset = set(rels)
    for rel in rels:
        lang = EXT_LANG.get(Path(rel).suffix.lower())
        if lang:
            det.language_files[lang] = det.language_files.get(lang, 0) + 1

    for rel in rels:
        name = Path(rel).name.lower()
        try:
            if re.fullmatch(r"requirements([-_.][\w.-]+)?\.txt", name):
                det.manifests.append(rel)
                det.dependencies += parse_requirements(root / rel, rel)
            elif name == "pyproject.toml":
                det.manifests.append(rel)
                det.dependencies += parse_pyproject(root / rel, rel)
            elif name == "package.json":
                det.manifests.append(rel)
                det.dependencies += parse_package_json(root / rel, rel)
            elif name.endswith(".csproj"):
                det.manifests.append(rel)
                det.dependencies += parse_csproj(root / rel, rel)
        except (ValueError, OSError, ET.ParseError, tomllib.TOMLDecodeError) as exc:
            det.warnings.append(f"無法解析 {rel}：{exc}")

    langs = {k for k, v in det.language_files.items() if v > 0}
    if any(Path(m).name.lower() in ("pyproject.toml", "setup.py") or "requirements" in m.lower()
           for m in det.manifests) or "setup.py" in relset:
        langs.add("python")
    if any(m.lower().endswith(".csproj") for m in det.manifests) or any(r.lower().endswith(".sln") for r in rels):
        langs.add("csharp")
    if "tsconfig.json" in relset or any(r.endswith("/tsconfig.json") for r in rels):
        langs.add("typescript")
    if any(Path(m).name == "package.json" for m in det.manifests) and "typescript" not in langs:
        langs.add("javascript")
    det.languages = sorted(langs)

    if "pnpm-lock.yaml" in relset:
        det.package_manager = "pnpm"
    elif "yarn.lock" in relset:
        det.package_manager = "yarn"
    elif "package-lock.json" in relset or any(Path(m).name == "package.json" for m in det.manifests):
        det.package_manager = "npm"
    det.has_node_modules = (root / "node_modules").is_dir()
    det.python_envs = [d for d in (".venv", "venv", "env") if (root / d / "pyvenv.cfg").is_file()]
    det.test_frameworks = detect_test_frameworks(root, rels, det.dependencies)
    return det


def parse_requirements(path: Path, rel: str) -> list[Dependency]:
    deps = []
    for raw in read_text(path, MAX_MANIFEST_BYTES).splitlines():
        line = raw.strip()
        if not line or line.startswith(("#", "-", "git+", "http:", "https:")):
            continue
        line = line.split(";")[0].strip()
        m = REQ_LINE.match(line)
        if m:
            deps.append(Dependency(m.group(1), m.group(3).strip(), "pypi", rel,
                                   dev="dev" in rel.lower() or "test" in rel.lower()))
    return deps


def _pep508(spec: str, rel: str, dev: bool) -> Dependency | None:
    m = REQ_LINE.match(spec.split(";")[0].strip())
    return Dependency(m.group(1), m.group(3).strip(), "pypi", rel, dev) if m else None


def parse_pyproject(path: Path, rel: str) -> list[Dependency]:
    data = tomllib.loads(read_text(path, MAX_MANIFEST_BYTES))
    deps: list[Dependency] = []
    project = data.get("project", {})
    for spec in project.get("dependencies", []) or []:
        d = _pep508(spec, rel, False)
        if d:
            deps.append(d)
    for group in (project.get("optional-dependencies", {}) or {}).values():
        for spec in group:
            d = _pep508(spec, rel, True)
            if d:
                deps.append(d)
    for group in (data.get("dependency-groups", {}) or {}).values():
        for spec in group:
            if isinstance(spec, str):
                d = _pep508(spec, rel, True)
                if d:
                    deps.append(d)
    poetry = data.get("tool", {}).get("poetry", {})
    for section, dev in (("dependencies", False), ("dev-dependencies", True)):
        for name, ver in (poetry.get(section, {}) or {}).items():
            if name.lower() != "python":
                deps.append(Dependency(name, ver if isinstance(ver, str) else json.dumps(ver), "pypi", rel, dev))
    for grp in (poetry.get("group", {}) or {}).values():
        for name, ver in (grp.get("dependencies", {}) or {}).items():
            deps.append(Dependency(name, ver if isinstance(ver, str) else json.dumps(ver), "pypi", rel, True))
    return deps


def parse_package_json(path: Path, rel: str) -> list[Dependency]:
    data = json.loads(read_text(path, MAX_MANIFEST_BYTES) or "{}")
    if not isinstance(data, dict):
        raise ValueError("package.json 不是物件")
    deps = []
    for section, dev in (("dependencies", False), ("devDependencies", True), ("optionalDependencies", False)):
        for name, ver in (data.get(section) or {}).items():
            deps.append(Dependency(name, str(ver), "npm", rel, dev))
    return deps


def parse_csproj(path: Path, rel: str) -> list[Dependency]:
    tree = ET.fromstring(read_text(path, MAX_MANIFEST_BYTES))  # noqa: S314 - local file, no entity expansion in stdlib ET
    deps = []
    for el in tree.iter():
        if el.tag.split("}")[-1] == "PackageReference":
            name = el.get("Include") or el.get("Update")
            version = el.get("Version") or next(
                (c.text or "" for c in el if c.tag.split("}")[-1] == "Version"), "")
            if name:
                deps.append(Dependency(name, version, "nuget", rel))
    return deps


def detect_test_frameworks(root: Path, rels: list[str], deps: list[Dependency]) -> list[str]:
    names = {d.name.lower() for d in deps}
    found: set[str] = set()
    relset = set(rels)
    if "pytest" in names or "pytest.ini" in relset or any(Path(r).name == "conftest.py" for r in rels):
        found.add("pytest")
    elif any(Path(r).name.startswith("test_") and r.endswith(".py") for r in rels):
        found.add("pytest")
    pyproject = root / "pyproject.toml"
    if pyproject.is_file():
        try:
            if "pytest" in tomllib.loads(read_text(pyproject)).get("tool", {}):
                found.add("pytest")
        except (tomllib.TOMLDecodeError, OSError):
            pass
    if "vitest" in names or any(Path(r).name.startswith("vitest.config") for r in rels):
        found.add("vitest")
    if "jest" in names or any(Path(r).name.startswith("jest.config") for r in rels):
        found.add("jest")
    if "@playwright/test" in names or any(Path(r).name.startswith("playwright.config") for r in rels):
        found.add("playwright")
    dotnet_markers = {"microsoft.net.test.sdk", "xunit", "nunit", "mstest.testframework"}
    if names & dotnet_markers:
        found.add("dotnet")
    return sorted(found)
