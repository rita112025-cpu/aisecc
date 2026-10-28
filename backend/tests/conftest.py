from __future__ import annotations

import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.config import Settings, set_active_settings
from app.db import dispose_engine, init_engine
from app.migrate import upgrade_db


@pytest.fixture
def settings(tmp_path: Path) -> Iterator[Settings]:
    s = Settings(data_dir=tmp_path / "資料 data")
    s.ensure_dirs()
    set_active_settings(s)
    yield s
    set_active_settings(None)


@pytest.fixture
def db(settings: Settings) -> Iterator[Path]:
    upgrade_db(settings.db_path)
    init_engine(settings.db_path)
    yield settings.db_path
    dispose_engine()


def git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True, encoding="utf-8"
    ).stdout


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """A small Python git repo at a path containing Chinese characters and spaces."""
    repo = tmp_path / "範例 專案 repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "core.autocrlf", "false")
    (repo / "pyproject.toml").write_text(
        '[project]\nname = "demo"\nversion = "0.1.0"\ndependencies = ["requests>=2.0", "fastapi==0.115.0"]\n'
        "\n[tool.pytest.ini_options]\ntestpaths = [\"tests\"]\n",
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src" / "calc.py").write_text(
        "def add(a, b):\n    return a + b\n\n\ndef div(a, b):\n    return a / b\n", encoding="utf-8"
    )
    (repo / "tests").mkdir()
    (repo / "tests" / "test_calc.py").write_text(
        "import sys, pathlib\nsys.path.insert(0, str(pathlib.Path(__file__).parents[1] / 'src'))\n"
        "from calc import add\n\n\ndef test_add():\n    assert add(1, 2) == 3\n",
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text("ignored_dir/\n*.tmp\n", encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "init")
    return repo
