"""Typed application settings persisted in the `settings` table."""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator

from app.db import read_session, write_session
from app.models import Setting


class AppSettings(BaseModel):
    retention_days: int = Field(default=90, ge=1, le=3650)
    # Non-protected audit entries only; approvals/changes are always kept.
    audit_retention_days: int = Field(default=730, ge=365, le=36500)
    ai_provider: str = Field(default="none", pattern="^(none|ollama|openai_compatible)$")
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = ""
    ai_timeout_sec: int = Field(default=120, ge=5, le=1800)
    # External (non-local) AI endpoints receive private code; requires explicit opt-in.
    allow_external_ai: bool = False
    external_ai_base_url: str = ""
    external_ai_model: str = ""
    # Name of the environment variable holding the API key; the key itself is never stored.
    external_ai_api_key_env: str = "AISECC_EXTERNAL_AI_KEY"
    max_ai_calls_per_proposal: int = Field(default=1, ge=1, le=3)
    scan_max_files: int = Field(default=200_000, ge=100, le=5_000_000)
    scan_max_file_bytes: int = Field(default=2_000_000, ge=1_000, le=100_000_000)
    tool_timeout_sec: int = Field(default=600, ge=10, le=7200)
    complexity_threshold: int = Field(default=15, ge=2, le=200)
    function_length_threshold: int = Field(default=80, ge=10, le=5000)

    @field_validator("ollama_url")
    @classmethod
    def _ollama_local(cls, v: str) -> str:
        if not is_local_url(v):
            raise ValueError("Ollama 位址必須是本機 (127.0.0.1 / localhost)")
        return v

    @field_validator("external_ai_api_key_env")
    @classmethod
    def _key_env_namespaced(cls, v: str) -> str:
        # Only our own namespace, so a settings write cannot exfiltrate e.g. GITHUB_TOKEN.
        if not re.fullmatch(r"AISECC_[A-Z0-9_]+", v):
            raise ValueError("API 金鑰環境變數名稱必須以 AISECC_ 開頭（僅大寫英數與底線）")
        return v

    @field_validator("external_ai_base_url")
    @classmethod
    def _external_url(cls, v: str) -> str:
        if v and not (urlparse(v).scheme == "https" and urlparse(v).hostname) and not is_local_url(v):
            raise ValueError("外部 AI 位址必須是 https:// 或本機位址")
        return v


def is_local_url(v: str) -> bool:
    parsed = urlparse(v)
    return parsed.scheme in ("http", "https") and (parsed.hostname or "") in {"127.0.0.1", "localhost", "::1"}


def load_settings() -> AppSettings:
    with read_session() as s:
        stored = {row.key: row.value for row in s.query(Setting).all()}
    known = {k: v for k, v in stored.items() if k in AppSettings.model_fields}
    return AppSettings(**known)


def save_settings(patch: dict[str, Any]) -> AppSettings:
    merged = load_settings().model_dump()
    merged.update(patch)
    validated = AppSettings(**merged)
    with write_session() as s:
        for key, value in validated.model_dump().items():
            row = s.get(Setting, key)
            if row is None:
                s.add(Setting(key=key, value=value))
            elif row.value != value:
                row.value = value
    return validated
