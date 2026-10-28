from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from app.services.detect import detect, parse_csproj, parse_package_json, parse_requirements
from app.services.fswalk import decode_bytes, resolve_inside, walk


def _mk(root: Path, rel: str, text: str = "x", encoding: str = "utf-8") -> Path:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding=encoding)
    return p


def test_walk_respects_gitignore_and_default_ignores(tmp_path: Path) -> None:
    _mk(tmp_path, ".gitignore", "*.log\nsecret_dir/\n")
    _mk(tmp_path, "a.py")
    _mk(tmp_path, "debug.log")
    _mk(tmp_path, "secret_dir/x.py")
    _mk(tmp_path, "node_modules/pkg/index.js")
    _mk(tmp_path, "sub/.gitignore", "local.txt\n")
    _mk(tmp_path, "sub/local.txt")
    _mk(tmp_path, "sub/keep.txt")
    rels = {f.rel for f in walk(tmp_path).files}
    assert rels == {".gitignore", "a.py", "sub/.gitignore", "sub/keep.txt"}


def test_walk_chinese_and_space_paths(tmp_path: Path) -> None:
    _mk(tmp_path, "中文 資料夾/模組 一.py", "print('嗨')")
    assert [f.rel for f in walk(tmp_path).files] == ["中文 資料夾/模組 一.py"]


def test_walk_truncates_at_limit(tmp_path: Path) -> None:
    for i in range(30):
        _mk(tmp_path, f"f{i:02}.txt")
    r = walk(tmp_path, max_files=10)
    assert r.truncated and len(r.files) == 10


def test_walk_large_file_skipped(tmp_path: Path) -> None:
    _mk(tmp_path, "big.bin", "x" * 5000)
    _mk(tmp_path, "small.txt")
    r = walk(tmp_path, max_file_bytes=1000)
    assert r.skipped_large == ["big.bin"] and [f.rel for f in r.files] == ["small.txt"]


def test_walk_does_not_follow_junction(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    _mk(outside, "leak.py")
    proj = tmp_path / "proj"
    _mk(proj, "ok.py")
    link = proj / "link"
    if sys.platform == "win32":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(outside)], check=True, capture_output=True)
    else:
        os.symlink(outside, link)
    r = walk(proj)
    assert [f.rel for f in r.files] == ["ok.py"]
    assert r.symlinks == ["link"]


def test_walk_cancellation(tmp_path: Path) -> None:
    for i in range(1200):
        _mk(tmp_path, f"d/f{i}.txt")

    class Stop(Exception):
        pass

    def cancel() -> None:
        raise Stop

    with pytest.raises(Stop):
        walk(tmp_path, check_cancel=cancel)


def test_decode_various_encodings() -> None:
    assert decode_bytes("中文".encode()) == "中文"
    assert decode_bytes(b"\xef\xbb\xbfabc") == "abc"
    assert decode_bytes("中文測試內容".encode("utf-16")) == "中文測試內容"
    big5 = ("這是一段繁體中文的註解內容，" * 5).encode("cp950")
    assert "繁體中文" in decode_bytes(big5)


@pytest.mark.parametrize("bad", ["../x", "..\\x", "C:\\Windows\\win.ini", "/etc/passwd", "a/../../b", ""])
def test_resolve_inside_rejects_escape(tmp_path: Path, bad: str) -> None:
    with pytest.raises(ValueError):
        resolve_inside(tmp_path, bad)


def test_resolve_inside_accepts_nested(tmp_path: Path) -> None:
    assert resolve_inside(tmp_path, "a/b.py") == (tmp_path / "a" / "b.py").resolve()


def test_parse_requirements(tmp_path: Path) -> None:
    text = "requests==2.31.0\nflask>=2  # web\n-e .\n\nuvicorn[standard]==0.30 ; python_version>'3'\n"
    p = _mk(tmp_path, "requirements.txt", text)
    deps = parse_requirements(p, "requirements.txt")
    assert [(d.name, d.pinned_version) for d in deps] == [("requests", "2.31.0"), ("flask", None), ("uvicorn", "0.30")]


def test_parse_package_json_and_invalid(tmp_path: Path) -> None:
    p = _mk(tmp_path, "package.json", '{"dependencies":{"react":"18.3.1"},"devDependencies":{"vitest":"^2.0.0"}}')
    deps = parse_package_json(p, "package.json")
    assert {(d.name, d.dev) for d in deps} == {("react", False), ("vitest", True)}
    bad = _mk(tmp_path, "bad/package.json", "{not json")
    with pytest.raises(ValueError):
        parse_package_json(bad, "bad/package.json")


def test_parse_csproj(tmp_path: Path) -> None:
    p = _mk(tmp_path, "App.Tests.csproj", '<Project Sdk="Microsoft.NET.Sdk"><ItemGroup>'
            '<PackageReference Include="xunit" Version="2.9.0" />'
            '<PackageReference Include="Microsoft.NET.Test.Sdk"><Version>17.10.0</Version></PackageReference>'
            "</ItemGroup></Project>")
    assert {(d.name, d.spec) for d in parse_csproj(p, "App.Tests.csproj")} == {
        ("xunit", "2.9.0"), ("Microsoft.NET.Test.Sdk", "17.10.0")}


def test_detect_multi_language_project(tmp_path: Path) -> None:
    _mk(tmp_path, "pyproject.toml", '[project]\nname="x"\ndependencies=["pytest>=8"]\n')
    _mk(tmp_path, "src/app.py")
    _mk(tmp_path, "web/package.json", '{"devDependencies":{"vitest":"2.1.0","@playwright/test":"1.48.0"}}')
    _mk(tmp_path, "web/tsconfig.json", "{}")
    _mk(tmp_path, "web/src/main.ts")
    _mk(tmp_path, "svc/Svc.Tests.csproj", '<Project><ItemGroup><PackageReference Include="xunit" Version="2.9.0"/>'
                                          "</ItemGroup></Project>")
    _mk(tmp_path, "svc/A.cs")
    det = detect(tmp_path, walk(tmp_path))
    assert det.languages == ["csharp", "python", "typescript"]
    assert det.test_frameworks == ["dotnet", "playwright", "pytest", "vitest"]
    assert det.package_manager == "npm"


def test_detect_broken_manifest_is_warning(tmp_path: Path) -> None:
    _mk(tmp_path, "pyproject.toml", "[project\nbroken")
    det = detect(tmp_path, walk(tmp_path))
    assert det.warnings and "pyproject.toml" in det.warnings[0]
