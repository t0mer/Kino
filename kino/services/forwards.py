"""Forward rows: listing and manual retry (CLAUDE.md §9.9). Sending lives in kino.forward."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.db.models import FORWARD_STATUS, Forward
from kino.services import ConflictError, InvalidError, NotFoundError


def list_forwards(session: Session, status: str | None = None, limit: int = 50) -> list[Forward]:
    """Newest first."""
    if status is not None and status not in FORWARD_STATUS:
        raise InvalidError(f"status must be one of {', '.join(FORWARD_STATUS)}")
    q = select(Forward).order_by(Forward.id.desc()).limit(limit)
    if status:
        q = q.where(Forward.status == status)
    return list(session.scalars(q))


def reset_failed(session: Session, forward_id: int) -> Forward:
    """Put a failed forward back to pending; the caller enqueues it after committing."""
    from kino import forward

    row = session.get(Forward, forward_id)
    if row is None:
        raise NotFoundError(f"forward {forward_id} does not exist")
    if not forward.retry_forward(session, forward_id):
        raise ConflictError(f"forward {forward_id} has not failed (status {row.status})")
    session.flush()
    return row


def enqueue(forward_id: int) -> None:
    from kino import forward

    forward.send_forward(forward_id)
