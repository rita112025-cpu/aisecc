from __future__ import annotations

from pathlib import Path

import pytest

from app.services import gitinfo
from app.services.health import health_report
from app.services.inventory import build_inventory, compare_inventories
from tests.conftest import git


def test_git_info_clean_repo(git_repo: Path) -> None:
    info = gitinfo.get_info(git_repo)
    assert info.is_repo and info.branch == "main" and not info.dirty
    assert info.head and len(info.head) == 40 and info.head_subject == "init"


def test_git_info_dirty_with_chinese_filename(git_repo: Path) -> None:
    (git_repo / "src" / "calc.py").write_text("def add(a, b):\n    return b + a\n", encoding="utf-8")
    (git_repo / "新檔 案.py").write_text("x = 1\n", encoding="utf-8")
    info = gitinfo.get_info(git_repo)
    assert info.dirty
    changes = {e.path: e.change for e in info.entries}
    assert changes == {"src/calc.py": "modified", "新檔 案.py": "untracked"}


def test_git_read_does_not_modify_repo(git_repo: Path) -> None:
    index = git_repo / ".git" / "index"
    before = index.stat().st_mtime_ns
    (git_repo / "src" / "calc.py").touch()
    gitinfo.get_info(git_repo)
    gitinfo.diff(git_repo)
    assert index.stat().st_mtime_ns == before
    assert not (git_repo / ".git" / "index.lock").exists()


def test_non_git_directory(tmp_path: Path) -> None:
    assert gitinfo.get_info(tmp_path).is_repo is False


def test_git_diff_and_log(git_repo: Path) -> None:
    (git_repo / "src" / "calc.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
    d = gitinfo.diff(git_repo)
    assert "+    return a + b + 0" in d["diff"]
    assert d["files"][0]["path"] == "src/calc.py"
    assert gitinfo.log(git_repo)[0]["subject"] == "init"


def test_git_diff_rejects_option_injection(git_repo: Path) -> None:
    with pytest.raises(gitinfo.GitError):
        gitinfo.diff(git_repo, base="--output=/tmp/pwn")


def test_git_rename_status_parsing(git_repo: Path) -> None:
    git(git_repo, "mv", "src/calc.py", "src/計算.py")
    entries = {e.path: e.change for e in gitinfo.get_info(git_repo).entries}
    assert entries == {"src/計算.py": "renamed"}


def test_build_inventory(db: Path, git_repo: Path) -> None:
    inv = build_inventory(git_repo)
    assert inv["file_count"] == 4
    assert inv["detection"]["languages"] == ["python"]
    assert "pytest" in inv["detection"]["test_frameworks"]
    assert {d["name"] for d in inv["detection"]["dependencies"]} == {"requests", "fastapi"}
    assert inv["git"]["is_repo"] is True


def test_compare_inventories() -> None:
    a = {"file_count": 3, "detection": {"languages": ["python"], "dependencies": [
        {"ecosystem": "pypi", "name": "flask", "spec": "==2.0"}, {"ecosystem": "pypi", "name": "old", "spec": ""}]},
        "git": {"head": "a"}}
    b = {"file_count": 5, "detection": {"languages": ["python", "typescript"], "dependencies": [
        {"ecosystem": "pypi", "name": "Flask", "spec": "==3.0"}, {"ecosystem": "npm", "name": "react", "spec": "18"}]},
        "git": {"head": "b"}}
    d = compare_inventories(a, b)
    assert d["languages_added"] == ["typescript"]
    assert d["dependencies_added"] == ["npm:react"] and d["dependencies_removed"] == ["pypi:old"]
    assert d["dependencies_changed"] == [{"name": "pypi:flask", "from": "==2.0", "to": "==3.0"}]


def test_health_report_lists_deductions() -> None:
    inv = {"detection": {"test_frameworks": [], "dependencies": []}, "git": {"is_repo": False}}
    r = health_report(inv)
    assert r["score"] == 75 and len(r["deductions"]) == 2
    assert any("尚未執行靜態分析" in n for n in r["notes"])


def test_health_report_tools_missing_not_counted_as_pass() -> None:
    inv = {"detection": {"test_frameworks": ["pytest"], "dependencies": []}, "git": {"is_repo": True}}
    analysis = {"open_by_severity": {"high": 1}, "tool_status": {"eslint": {"state": "needs_authorization"}}}
    r = health_report(inv, analysis)
    assert r["score"] == 92 and any("eslint" in n for n in r["notes"])
