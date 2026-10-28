from __future__ import annotations

import sqlite3
import threading
import time
from pathlib import Path

from sqlalchemy import create_engine

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from app.db import dispose_engine, init_engine, read_session, write_session
from app.migrate import alembic_config, upgrade_db
from app.models import Base, Finding, Project, Scan


def test_models_match_migrations(tmp_path: Path) -> None:
    db = tmp_path / "drift.db"
    upgrade_db(db)
    engine = create_engine(f"sqlite:///{db.as_posix()}")
    with engine.connect() as conn:
        diff = compare_metadata(MigrationContext.configure(conn), Base.metadata)
    engine.dispose()
    assert diff == []


def test_upgrade_downgrade_roundtrip(tmp_path: Path) -> None:
    db = tmp_path / "rt.db"
    cfg = alembic_config(db)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    with sqlite3.connect(db) as conn:
        names = {r[0] for r in conn.execute("select name from sqlite_master where type='table'")}
    assert "projects" not in names
    command.upgrade(cfg, "head")


def test_db_path_with_chinese_and_spaces(tmp_path: Path) -> None:
    db = tmp_path / "資料 庫" / "app.db"
    upgrade_db(db)
    init_engine(db)
    try:
        with write_session() as s:
            s.add(Project(name="中文", path="D:/專案 一", path_key="d:/專案 一"))
        with read_session() as s:
            assert s.query(Project).one().name == "中文"
    finally:
        dispose_engine()


def test_cascade_deletes_findings(db: Path) -> None:
    with write_session() as s:
        p = Project(name="p", path="C:/c", path_key="c:/c")
        s.add(p)
        s.flush()
        scan = Scan(project_id=p.id, kind="analysis")
        s.add(scan)
        s.flush()
        s.add(Finding(scan_id=scan.id, project_id=p.id, tool="t", rule_id="r", severity="low",
                      category="lint", message="m", fingerprint="f"))
    with write_session() as s:
        s.delete(s.query(Project).one())
    with read_session() as s:
        assert s.query(Finding).count() == 0


def test_writer_waits_for_external_reserved_lock(db: Path) -> None:
    blocker = sqlite3.connect(db, timeout=1, isolation_level=None, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")  # RESERVED lock held by "another process"
    threading.Timer(2.0, lambda: blocker.execute("ROLLBACK")).start()
    t0 = time.monotonic()
    with write_session() as s:
        s.add(Project(name="w", path="C:/w", path_key="c:/w"))
    waited = time.monotonic() - t0
    blocker.close()
    assert waited >= 1.5
    with read_session() as s:
        assert s.query(Project).filter_by(name="w").count() == 1
