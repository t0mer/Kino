"""Enrollment uploads: detect faces in uploaded photos, serve their temp crops (§11.2, §13)."""

from __future__ import annotations

from typing import Annotated

from fastapi import HTTPException, Path, Request
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from loguru import logger
from starlette.datastructures import UploadFile
from starlette.exceptions import HTTPException as StarletteHTTPException

from kino.api.deps import protected_router
from kino.api.schemas import BBox, UploadFaceOut, UploadOut
from kino.config import Settings
from kino.db.session import get_session
from kino.services import enroll_web as svc
from kino.services import kids as kids_svc

router = protected_router(tags=["enroll"])

# Whole-request cap, checked from Content-Length before the body is read.
MAX_REQUEST_BYTES = svc.MAX_UPLOAD_FILES * svc.MAX_UPLOAD_BYTES + 1024 * 1024
TempFaceId = Annotated[str, Path(pattern=r"^[0-9a-f]{32}$")]


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _out(r: svc.UploadResult) -> UploadOut:
    return UploadOut(
        upload_id=r.upload_id,
        filename=r.filename,
        width=r.width,
        height=r.height,
        photo_date=r.photo_date,
        error=r.error,
        status=r.status,
        faces=[
            UploadFaceOut(
                temp_face_id=f.temp_face_id,
                bbox=BBox(x=f.bbox[0], y=f.bbox[1], w=f.bbox[2], h=f.bbox[3]),
                quality=round(f.quality, 4),
                tier=f.tier,
                selectable=f.selectable,
                reason=f.reason,
                crop_url=f"/api/temp-crops/{f.temp_face_id}",
            )
            for f in r.faces
        ],
    )


def _process(kid_id: int, files: list[tuple[str, bytes]], settings: Settings) -> list[UploadOut]:
    from kino.tasks import get_pipeline

    try:
        pipe = get_pipeline()
    except Exception as e:
        logger.error("upload: face models unavailable: {}", type(e).__name__)
        raise HTTPException(503, "face models are not available yet; try again later") from None
    with get_session() as s:
        kids_svc.get_kid(s, kid_id)
        results = [
            svc.process_upload(s, kid_id, name, data, pipe.detector, pipe.embedder, settings)
            for name, data in files
        ]
    return [_out(r) for r in results]


@router.post("/api/kids/{kid_id}/uploads", response_model=list[UploadOut])
async def upload_photos(request: Request, kid_id: int) -> list[UploadOut]:
    """Detect faces in up to 20 photos (20 MB each). Per-file problems are reported in the
    file's entry; the request fails only when no file could be used."""
    try:
        length = int(request.headers.get("content-length", "0"))
    except ValueError:
        raise HTTPException(400, "invalid Content-Length") from None
    if length > MAX_REQUEST_BYTES:
        raise HTTPException(413, "upload is too large")
    with get_session() as s:
        kids_svc.get_kid(s, kid_id)
    try:
        form = await request.form(max_files=svc.MAX_UPLOAD_FILES, max_fields=20)
    except StarletteHTTPException as e:
        if "files" in str(e.detail).lower():
            raise HTTPException(413, f"at most {svc.MAX_UPLOAD_FILES} files per upload") from None
        raise HTTPException(400, "malformed upload") from None
    try:
        files: list[tuple[str, bytes]] = []
        for item in form.getlist("files"):
            if not isinstance(item, UploadFile):
                raise HTTPException(422, "'files' must be file parts")
            # Read at most one byte past the limit; the service rejects the oversize file.
            files.append((item.filename or "", await item.read(svc.MAX_UPLOAD_BYTES + 1)))
    finally:
        await form.close()
    if not files:
        raise HTTPException(422, "add at least one photo in the 'files' field")
    out = await run_in_threadpool(_process, kid_id, files, _settings(request))
    failed = [o for o in out if o.error is not None]
    if len(failed) == len(out):
        detail = "; ".join(f"{o.filename}: {o.error}" for o in failed)
        raise HTTPException(failed[0].status, detail)
    return out


@router.get("/api/temp-crops/{temp_face_id}")
def temp_crop(request: Request, temp_face_id: TempFaceId) -> FileResponse:
    with get_session() as s:
        path = svc.temp_crop(s, _settings(request), temp_face_id)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})
