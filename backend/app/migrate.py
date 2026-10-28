"""Programmatic Alembic upgrade used by install.ps1, app startup and tests."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from alembic import command
from alembic.config import Config

BACKEND_DIR = Path(__file__).resolve().parents[1]


def alembic_config(db_path: Path) -> Config:
    cfg = Config(str(BACKEND_DIR / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    cfg.attributes["db_url"] = f"sqlite:///{db_path.as_posix()}"
    return cfg


def upgrade_db(db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    command.upgrade(alembic_config(db_path), "head")
    # WAL is persistent per file; set it here once, while no other connection holds a lock.
    with closing(sqlite3.connect(db_path, timeout=5)) as conn:
        conn.execute("PRAGMA journal_mode=WAL")


if __name__ == "__main__":
    from app.config import get_settings

    settings = get_settings()
    settings.ensure_dirs()
    upgrade_db(settings.db_path)
    print(f"Database ready: {settings.db_path}")
