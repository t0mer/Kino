"""Forward rows: listing and manual retry (CLAUDE.md §9.9). Sending lives in kino.forward."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.db.models import FORWARD_STATUS, Forward
from kino.openwa.client import OpenWAClient
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


TEST_CAPTION = "Kino test photo: forwarding to this chat works"


def make_test_photo() -> bytes:
    """A small generated JPEG (no real photo ever leaves the host for a test)."""
    import io

    from PIL import Image, ImageDraw

    img = Image.new("RGB", (480, 270), (36, 52, 71))
    draw = ImageDraw.Draw(img)
    draw.rectangle((24, 24, 456, 246), outline=(242, 201, 76), width=4)
    draw.text((48, 120), "Kino test photo", fill=(255, 255, 255))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return buf.getvalue()


async def send_test(client: OpenWAClient, chat_id: str) -> str:
    """Send the generated test photo; returns OpenWA's message id."""
    return await client.send_image(chat_id, make_test_photo(), "image/jpeg", TEST_CAPTION)
