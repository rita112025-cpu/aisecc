"""Append-only audit trail for every side-effectful operation."""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.db import write_session
from app.models import AuditLog


def record(
    action: str,
    *,
    target_type: str | None = None,
    target_id: str | int | None = None,
    project_id: int | None = None,
    detail: dict[str, Any] | None = None,
    session: Session | None = None,
) -> None:
    entry = AuditLog(
        action=action,
        target_type=target_type,
        target_id=None if target_id is None else str(target_id),
        project_id=project_id,
        detail=detail or {},
    )
    if session is not None:
        session.add(entry)
        return
    with write_session() as s:
        s.add(entry)
