"""Saved photos: browse per kid, and "remove from <kid>" (CLAUDE.md §9.8, §9.9, §11.7).

Removing a photo from a kid deletes that kid's file and ``saved_photos`` row, takes the kid
out of any unsent forward (a forward that also names siblings keeps going for them, one
that names only this kid is cancelled; already-sent messages stay in WhatsApp), and marks
the kid's saved match(es) on that image ``rejected`` / ``decided_by=user``, so a later
re-score never saves the photo for the kid again. The kid's references that were learned
from that photo (``source`` ``review`` or ``auto`` with a ``face_id`` of the image) are
retired, and ``gallery_version`` is bumped when any were; uploaded and cluster references
are left alone.

Listing is newest saved first (descending ``saved_photos.id``); ``cursor`` is the last id
seen. ``from``/``to`` filter on the message's ``sent_at`` (naive UTC).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from kino import metrics
from kino.db.models import Face, Forward, Group, Image, Kid, Match, ReferenceFace, SavedPhoto
from kino.services import NotFoundError


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class PhotoRow:
    photo: SavedPhoto
    image: Image
    kid: Kid
    group: Group | None


def list_photos(
    session: Session,
    kid_id: int | None = None,
    group_id: int | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    cursor: int | None = None,
    limit: int = 100,
) -> tuple[list[PhotoRow], int | None]:
    q = (
        select(SavedPhoto, Image, Kid, Group)
        .join(Image, Image.id == SavedPhoto.image_id)
        .join(Kid, Kid.id == SavedPhoto.kid_id)
        .outerjoin(Group, Group.id == Image.group_id)
    )
    if kid_id is not None:
        q = q.where(SavedPhoto.kid_id == kid_id)
    if group_id is not None:
        q = q.where(Image.group_id == group_id)
    if date_from is not None:
        q = q.where(Image.sent_at >= date_from)
    if date_to is not None:
        q = q.where(Image.sent_at <= date_to)
    if cursor is not None:
        q = q.where(SavedPhoto.id < cursor)
    rows = session.execute(q.order_by(SavedPhoto.id.desc()).limit(limit + 1)).all()
    more = len(rows) > limit
    out = [PhotoRow(p, i, k, g) for p, i, k, g in rows[:limit]]
    return out, (out[-1].photo.id if more and out else None)


@dataclass
class PhotoRemoval:
    saved_photo_id: int
    files: list[Path] = field(default_factory=list)  # delete after the commit
    cancelled_forwards: int = 0
    rejected_matches: int = 0
    retired_references: int = 0


def remove_photo(
    session: Session, saved_photo_id: int, now: datetime | None = None
) -> PhotoRemoval:
    from kino.forward import get_forward_config
    from kino.services.kids import drop_kid_from_forward

    sp = session.get(SavedPhoto, saved_photo_id)
    if sp is None:
        raise NotFoundError(f"saved photo {saved_photo_id} not found")
    out = PhotoRemoval(sp.id)
    shared = session.scalar(
        select(SavedPhoto.id).where(SavedPhoto.path == sp.path, SavedPhoto.id != sp.id)
    )
    if shared is None:
        out.files.append(Path(sp.path))
    template = get_forward_config(session).caption_template
    for f in session.scalars(
        select(Forward).where(Forward.image_id == sp.image_id, Forward.status == "pending")
    ):
        if sp.kid_id in (f.kid_ids or []) and not drop_kid_from_forward(
            session, f, sp.kid_id, {sp.path}, template
        ):
            f.status = "cancelled"
            f.last_error = "photo removed from kid"
            f.next_attempt_at = None
            metrics.forwards.labels(status="cancelled").inc()
            out.cancelled_forwards += 1
    face_ids = select(Face.id).where(Face.image_id == sp.image_id).scalar_subquery()
    res = session.execute(
        update(Match)
        .where(
            Match.face_id.in_(face_ids),
            Match.kid_id == sp.kid_id,
            Match.decision.in_(("auto_saved", "confirmed")),
        )
        .values(decision="rejected", decided_by="user", decided_at=now or _utcnow()),
        execution_options={"synchronize_session": False},
    )
    out.rejected_matches = int(res.rowcount)  # type: ignore[attr-defined]
    retired = session.execute(
        update(ReferenceFace)
        .where(
            ReferenceFace.kid_id == sp.kid_id,
            ReferenceFace.face_id.in_(face_ids),
            ReferenceFace.source.in_(("review", "auto")),
            ReferenceFace.status == "active",
        )
        .values(status="retired"),
        execution_options={"synchronize_session": False},
    )
    out.retired_references = int(retired.rowcount)  # type: ignore[attr-defined]
    if out.retired_references:
        from kino.tasks import bump_gallery_version

        bump_gallery_version(session)
    session.delete(sp)
    session.flush()
    return out


__all__ = ["PhotoRemoval", "PhotoRow", "list_photos", "remove_photo"]
