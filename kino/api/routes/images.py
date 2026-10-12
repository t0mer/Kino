"""Activity list, image detail, original file and face crops (CLAUDE.md §11.7, §12, §13)."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal

from fastapi import Query, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from PIL import Image, UnidentifiedImageError

from kino.api.deps import protected_router
from kino.api.schemas import (
    ImageDetailOut,
    ImageFaceOut,
    ImageForwardOut,
    ImageListItem,
    ImageMatchOut,
    ImagePage,
)
from kino.config import Settings
from kino.db.session import get_session
from kino.services import images as svc
from kino.services import review as review_svc
from kino.services.images import display_sender, mask_ids, sanitize_error

router = protected_router(tags=["images"])

_PRIVATE = {
    "Cache-Control": "private",
    "X-Content-Type-Options": "nosniff",
    "Content-Security-Policy": "default-src 'none'; sandbox",
}
_INLINE = {**_PRIVATE, "Content-Disposition": "inline"}
_ATTACHMENT = {**_PRIVATE, "Content-Disposition": "attachment"}
_SAFE_TYPES = frozenset(
    {"image/jpeg", "image/png", "image/webp", "image/gif", "image/heic", "image/heif"}
)

_sender = display_sender


def _media_type(path: Path) -> str:
    """The media type from the file's content (header only), not its name."""
    try:
        with Image.open(path) as im:
            return Image.MIME.get(im.format or "", "application/octet-stream")
    except (UnidentifiedImageError, OSError, SyntaxError):
        return "application/octet-stream"


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


@router.get("/api/images", response_model=ImagePage)
def list_images(
    request: Request,
    status: Literal["queued", "processing", "done", "failed", "skipped"] | None = None,
    group_id: int | None = None,
    cursor: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ImagePage:
    with get_session() as s:
        rows, nxt = svc.list_images(s, _settings(request), status, group_id, cursor, limit)
        items = [
            ImageListItem(
                id=r.image.id,
                group_id=r.image.group_id,
                group_name=mask_ids(r.group_name),
                sender=_sender(r.image.sender_name),
                sent_at=r.image.sent_at,
                received_at=r.image.received_at,
                status=r.image.status,
                skip_reason=r.image.skip_reason,
                processing_ms=r.image.processing_ms,
                error=sanitize_error(r.image.error),
                face_count=r.face_count,
                matches=r.matches,
                has_file=r.has_file,
            )
            for r in rows
        ]
    return ImagePage(items=items, next_cursor=nxt)


@router.get("/api/images/{image_id}", response_model=ImageDetailOut)
def image_detail(request: Request, image_id: int) -> ImageDetailOut:
    with get_session() as s:
        d = svc.image_detail(s, _settings(request), image_id)
        im = d.image
        return ImageDetailOut(
            id=im.id,
            group_id=im.group_id,
            group_name=mask_ids(d.group_name),
            sender=_sender(im.sender_name),
            sent_at=im.sent_at,
            received_at=im.received_at,
            width=im.width,
            height=im.height,
            status=im.status,
            skip_reason=im.skip_reason,
            duplicate_of=im.duplicate_of,
            processing_ms=im.processing_ms,
            error=sanitize_error(im.error),
            has_file=d.has_file,
            file_url=f"/api/images/{im.id}/file" if d.has_file else None,
            faces=[
                ImageFaceOut(
                    id=f.face.id,
                    bbox=(f.face.bbox_x, f.face.bbox_y, f.face.bbox_w, f.face.bbox_h),
                    det_score=f.face.det_score,
                    size_px=f.face.size_px,
                    quality=f.face.quality,
                    tier=f.tier,
                    from_tile=f.face.from_tile,
                    crop_url=f"/api/crops/{f.face.id}" if f.face.crop_path else None,
                )
                for f in d.faces
            ],
            matches=[
                ImageMatchOut(
                    id=m.match.id,
                    face_id=m.match.face_id,
                    kid_id=m.match.kid_id,
                    kid_name=m.kid_name,
                    score=m.match.score,
                    margin=m.match.margin,
                    method=m.match.method,
                    decision=m.match.decision,
                    decided_by=m.match.decided_by,
                )
                for m in d.matches
            ],
            forwards=[
                ImageForwardOut(
                    id=f.id,
                    status=f.status,
                    attempts=f.attempts,
                    kid_ids=list(f.kid_ids or []),
                    last_error=sanitize_error(f.last_error),
                    created_at=f.created_at,
                    sent_at=f.sent_at,
                )
                for f in d.forwards
            ],
        )


@router.get("/api/images/{image_id}/file", response_model=None)
def image_file(request: Request, image_id: int) -> Response:
    """R4: a saved copy, else the raw original, else 404 ``original no longer stored``."""
    with get_session() as s:
        image = svc.get_image(s, image_id)
        path = review_svc.image_file(s, image, _settings(request))
    if path is None:
        return JSONResponse({"detail": "original no longer stored"}, 404, headers=_PRIVATE)
    media = _media_type(path)
    headers = _INLINE if media in _SAFE_TYPES else _ATTACHMENT
    return FileResponse(path, media_type=media, headers=headers)


@router.get("/api/crops/{face_id}")
def face_crop(request: Request, face_id: int) -> FileResponse:
    with get_session() as s:
        path = review_svc.face_crop(s, _settings(request), face_id)
    return FileResponse(path, media_type="image/jpeg", headers=_INLINE)
