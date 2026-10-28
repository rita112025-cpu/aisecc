"""FastAPI application factory."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from urllib.parse import urlparse

from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app import __version__
from app.config import Settings, get_settings, set_active_settings
from app.db import DatabaseBusyError, dispose_engine, init_engine
from app.migrate import upgrade_db
from app.services.jobs import DuplicateJobError, JobManager

log = logging.getLogger(__name__)
LOCAL_HOSTS = {"127.0.0.1", "localhost", "[::1]", "::1"}
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def _host_name(host_header: str) -> str:
    """Strip the port from a Host header, keeping IPv6 brackets ("[::1]:8765" -> "[::1]")."""
    if host_header.startswith("["):
        return host_header.split("]", 1)[0] + "]"
    return host_header.rsplit(":", 1)[0]


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        settings.ensure_dirs()
        set_active_settings(settings)
        upgrade_db(settings.db_path)
        init_engine(settings.db_path)
        manager = JobManager(settings.logs_dir, settings.max_workers)
        interrupted = manager.recover_after_restart()
        if interrupted:
            log.warning("marked %d stale jobs as interrupted", interrupted)
        app.state.jobs = manager
        app.state.settings = settings
        yield
        manager.shutdown()
        dispose_engine()

    app = FastAPI(title="AI Software Engineering Control Center", version=__version__, lifespan=lifespan)

    @app.middleware("http")
    async def local_only(request: Request, call_next):  # noqa: ANN001, ANN202
        if _host_name(request.headers.get("host", "")) not in LOCAL_HOSTS:
            return JSONResponse({"detail": "拒絕非本機 Host 標頭的請求"}, status_code=403)
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin and (urlparse(origin).hostname or "") not in LOCAL_HOSTS:
                return JSONResponse({"detail": "拒絕跨站請求"}, status_code=403)
        return await call_next(request)

    @app.exception_handler(DatabaseBusyError)
    async def busy_handler(_: Request, exc: DatabaseBusyError) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=503)

    @app.exception_handler(DuplicateJobError)
    async def dup_handler(_: Request, exc: DuplicateJobError) -> JSONResponse:
        return JSONResponse({"detail": str(exc), "job_id": exc.existing_id}, status_code=409)

    from app.api import findings, jobs, projects, scans

    for router in (projects.router, scans.router, findings.router, jobs.router):
        app.include_router(router, prefix="/api")

    @app.get("/api/health")
    def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    dist = settings.frontend_dist
    if (dist / "index.html").is_file():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str) -> FileResponse:
            candidate = (dist / full_path).resolve()
            if full_path and dist.resolve() in candidate.parents and candidate.is_file():
                return FileResponse(candidate)
            return FileResponse(dist / "index.html")

    return app
