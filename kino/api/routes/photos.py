"""/api/photos: browse saved photos and remove one from a kid (CLAUDE.md §11.7, §12).

``file_url`` points at ``/api/images/{image_id}/file`` (served by the image file route).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import Query, Request
from loguru import logger

from kino.api.deps import protected_router
from kino.api.schemas import PhotoOut, PhotoPage, PhotoRemoved
from kino.config import Settings
from kino.db.session import get_session
from kino.pipeline import save as save_mod
from kino.services import photos as svc

router = protected_router(prefix="/api/photos", tags=["photos"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _naive_utc(value: datetime | None) -> datetime | None:
    if value is None or value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


@router.get("", response_model=PhotoPage)
def list_photos(
    kid_id: int | None = None,
    group_id: int | None = None,
    date_from: Annotated[datetime | None, Query(alias="from")] = None,
    date_to: Annotated[datetime | None, Query(alias="to")] = None,
    cursor: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> PhotoPage:
    with get_session() as s:
        rows, nxt = svc.list_photos(
            s, kid_id, group_id, _naive_utc(date_from), _naive_utc(date_to), cursor, limit
        )
        items = [
            PhotoOut(
                id=r.photo.id,
                image_id=r.image.id,
                kid_id=r.kid.id,
                kid_name=r.kid.display_name,
                group_id=r.group.id if r.group else None,
                group_name=r.group.name if r.group else None,
                sent_at=r.image.sent_at,
                saved_at=r.photo.saved_at,
                file_url=f"/api/images/{r.image.id}/file",
            )
            for r in rows
        ]
    return PhotoPage(items=items, next_cursor=nxt)


@router.delete("/{saved_photo_id}", response_model=PhotoRemoved)
def remove_photo(request: Request, saved_photo_id: int) -> PhotoRemoved:
    with get_session() as s:
        res = svc.remove_photo(s, saved_photo_id)
    removed = 0
    for path in res.files:  # only after the commit
        try:
            existed = path.exists()
            save_mod.remove_photo(path, _settings(request).photos_dir)
            removed += int(existed)
        except (OSError, ValueError) as e:  # outside the photos dir, or unremovable
            logger.warning("could not delete a saved photo: {}", type(e).__name__)
    return PhotoRemoved(
        id=res.saved_photo_id,
        removed_files=removed,
        cancelled_forwards=res.cancelled_forwards,
        rejected_matches=res.rejected_matches,
    )
