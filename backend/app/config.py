"""Runtime configuration loaded from environment variables (prefix AISECC_)."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

REPO_ROOT = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AISECC_", env_file=None)

    data_dir: Path = REPO_ROOT / "data"
    host: str = "127.0.0.1"
    port: int = 8765
    # Thread pool size for background jobs; kept small to avoid SQLite write contention.
    max_workers: int = 2
    frontend_dist: Path = REPO_ROOT / "frontend" / "dist"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "aisecc.sqlite3"

    @property
    def logs_dir(self) -> Path:
        return self.data_dir / "logs"

    @property
    def reports_dir(self) -> Path:
        return self.data_dir / "reports"

    @property
    def workspaces_dir(self) -> Path:
        return self.data_dir / "workspaces"

    @property
    def backups_dir(self) -> Path:
        return self.data_dir / "backups"

    def ensure_dirs(self) -> None:
        for d in (self.data_dir, self.logs_dir, self.reports_dir, self.workspaces_dir, self.backups_dir):
            d.mkdir(parents=True, exist_ok=True)


_active: Settings | None = None


@lru_cache
def _env_settings() -> Settings:
    return Settings()


def set_active_settings(settings: Settings | None) -> None:
    """The app factory registers its settings so services use the same data directory."""
    global _active
    _active = settings


def get_settings() -> Settings:
    return _active if _active is not None else _env_settings()
