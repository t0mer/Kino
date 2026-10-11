"""Reference faces: confirm picked faces, list/edit/delete, negatives, coverage (§11.2, §11.6)."""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request, Response
from fastapi.responses import FileResponse

from kino.api.deps import protected_router
from kino.api.schemas import (
    CoverageOut,
    NegativeOut,
    ReferenceOut,
    ReferencesAdded,
    ReferencesIn,
    ReferenceUpdate,
)
from kino.config import Settings
from kino.db.models import ReferenceFace
from kino.db.session import get_session
from kino.services import enroll_web as svc

router = protected_router(tags=["references"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _out(r: ReferenceFace) -> ReferenceOut:
    return ReferenceOut(
        id=r.id,
        kid_id=r.kid_id,
        face_id=r.face_id,
        source=r.source,
        status=r.status,
        quality=r.quality,
        photo_date=r.photo_date,
        added_at=r.added_at,
        weight=r.weight,
        yaw=r.yaw,
        crop_url=f"/api/references/{r.id}/crop" if r.crop_path else None,
    )


@router.post("/api/kids/{kid_id}/references", response_model=ReferencesAdded, status_code=201)
def add_references(request: Request, kid_id: int, body: ReferencesIn) -> ReferencesAdded:
    with get_session() as s:
        res = svc.add_references(
            s,
            kid_id,
            _settings(request),
            temp_face_ids=body.temp_face_ids,
            face_ids=body.face_ids,
            source=body.source,
            photo_date=body.photo_date.replace(tzinfo=None) if body.photo_date else None,
        )
        return ReferencesAdded(created=[_out(r) for r in res.created], duplicates=res.duplicates)


@router.get("/api/kids/{kid_id}/references", response_model=list[ReferenceOut])
def list_references(
    kid_id: int,
    status: str | None = None,
    source: str | None = None,
    cursor: int | None = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 200,
) -> list[ReferenceOut]:
    with get_session() as s:
        return [_out(r) for r in svc.list_references(s, kid_id, status, source, cursor, limit)]


@router.get("/api/kids/{kid_id}/coverage", response_model=CoverageOut)
def coverage(kid_id: int) -> CoverageOut:
    with get_session() as s:
        c = svc.coverage(s, kid_id)
    return CoverageOut(**vars(c))


@router.get("/api/references/{ref_id}", response_model=ReferenceOut)
def get_reference(ref_id: int) -> ReferenceOut:
    with get_session() as s:
        return _out(svc.get_reference(s, ref_id))


@router.patch("/api/references/{ref_id}", response_model=ReferenceOut)
def update_reference(ref_id: int, body: ReferenceUpdate) -> ReferenceOut:
    with get_session() as s:
        if "photo_date" in body.model_fields_set:  # explicit null clears the date
            pd = body.photo_date.replace(tzinfo=None) if body.photo_date else None
            ref = svc.update_reference(s, ref_id, body.status, body.kid_id, pd)
        else:
            ref = svc.update_reference(s, ref_id, body.status, body.kid_id)
        return _out(ref)


@router.delete("/api/references/{ref_id}", status_code=204)
def delete_reference(request: Request, ref_id: int) -> Response:
    with get_session() as s:
        crop = svc.delete_reference(s, _settings(request), ref_id)
    svc.remove_file(crop)  # only after the commit
    return Response(status_code=204)


@router.post("/api/references/{ref_id}/to-negative", response_model=NegativeOut)
def to_negative(request: Request, ref_id: int) -> NegativeOut:
    with get_session() as s:
        neg = svc.reference_to_negative(s, _settings(request), ref_id)
        return NegativeOut(
            id=neg.id, kid_id=neg.kid_id, source_face_id=neg.source_face_id, added_at=neg.added_at
        )


@router.get("/api/references/{ref_id}/crop")
def reference_crop(request: Request, ref_id: int) -> FileResponse:
    with get_session() as s:
        path = svc.reference_crop(s, _settings(request), ref_id)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})
