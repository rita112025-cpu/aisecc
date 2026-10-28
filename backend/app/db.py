"""SQLite engine/session management.

SQLite allows a single writer. We combine WAL mode, a busy timeout and an
in-process re-entrant lock around write transactions so that background job
threads and API requests never fail with "database is locked" under normal load.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import create_engine, event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

WRITE_LOCK = threading.Lock()
_local = threading.local()
BUSY_TIMEOUT_MS = 5000


class _DB:
    engine: Engine | None = None
    factory: sessionmaker[Session] | None = None
    write_factory: sessionmaker[Session] | None = None
    path: Path | None = None


_db = _DB()


def _on_connect(dbapi_conn, _record) -> None:  # noqa: ANN001 - DBAPI connection
    # Take over transaction control from pysqlite so we can emit BEGIN IMMEDIATE for writers.
    dbapi_conn.isolation_level = None
    cur = dbapi_conn.cursor()
    # busy_timeout first so the remaining pragmas also wait on a lock held elsewhere.
    cur.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
    # journal_mode is persistent in the file; only switch when needed (switching needs a lock).
    if cur.execute("PRAGMA journal_mode").fetchone()[0].lower() != "wal":
        cur.execute("PRAGMA journal_mode=WAL")
    cur.execute("PRAGMA foreign_keys=ON")
    cur.execute("PRAGMA synchronous=NORMAL")
    cur.close()


def make_engine(db_path: Path) -> Engine:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(
        f"sqlite:///{db_path.as_posix()}",
        connect_args={"check_same_thread": False, "timeout": BUSY_TIMEOUT_MS / 1000},
        future=True,
    )
    event.listen(engine, "connect", _on_connect)
    event.listen(engine, "begin", _on_begin)
    return engine


def _on_begin(conn) -> None:  # noqa: ANN001 - SQLAlchemy Connection
    # Writers acquire the RESERVED lock up front so contention surfaces (and waits) at
    # transaction start instead of failing at commit time.
    immediate = conn.get_execution_options().get("sqlite_immediate", False)
    conn.exec_driver_sql("BEGIN IMMEDIATE" if immediate else "BEGIN")


def init_engine(db_path: Path) -> Engine:
    dispose_engine()
    _db.engine = make_engine(db_path)
    _db.factory = sessionmaker(bind=_db.engine, expire_on_commit=False, future=True)
    _db.write_factory = sessionmaker(
        bind=_db.engine.execution_options(sqlite_immediate=True), expire_on_commit=False, future=True
    )
    _db.path = db_path
    return _db.engine


def dispose_engine() -> None:
    if _db.engine is not None:
        _db.engine.dispose()
    _db.engine = None
    _db.factory = None
    _db.write_factory = None


def get_engine() -> Engine:
    if _db.engine is None:
        raise RuntimeError("Database engine is not initialised")
    return _db.engine


def new_session() -> Session:
    if _db.factory is None:
        raise RuntimeError("Database engine is not initialised")
    return _db.factory()


@contextmanager
def read_session() -> Iterator[Session]:
    session = new_session()
    try:
        yield session
    finally:
        session.close()


class DatabaseBusyError(RuntimeError):
    """Raised when SQLite stays locked longer than the busy timeout (e.g. another process)."""


@contextmanager
def write_session() -> Iterator[Session]:
    """BEGIN IMMEDIATE + busy_timeout handle cross-process writers; the in-process lock only
    avoids needless contention between our own threads."""
    if _db.write_factory is None:
        raise RuntimeError("Database engine is not initialised")
    if getattr(_local, "in_write", False):
        # A second BEGIN IMMEDIATE on this thread would wait on our own lock until busy_timeout.
        raise RuntimeError("巢狀 write_session：請傳入外層 session")
    with WRITE_LOCK:
        _local.in_write = True
        session = _db.write_factory()
        try:
            session.connection()  # starts BEGIN IMMEDIATE now
            yield session
            session.commit()
        except OperationalError as exc:
            session.rollback()
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                raise DatabaseBusyError("資料庫忙碌中（可能有其他程序鎖定），請稍後再試") from exc
            raise
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
            _local.in_write = False


def get_db() -> Iterator[Session]:
    """FastAPI dependency for read-mostly request handlers."""
    session = new_session()
    try:
        yield session
    finally:
        session.close()
