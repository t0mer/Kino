"""/api/kids: list, create, edit and delete kids (CLAUDE.md §11.1, §12)."""

from __future__ import annotations

from fastapi import Request

from kino.api.deps import protected_router
from kino.api.schemas import KidCreate, KidDeleted, KidOut, KidUpdate
from kino.config import Settings
from kino.db.session import get_session
from kino.services import kids as svc

router = protected_router(prefix="/api/kids", tags=["kids"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _out(s: svc.KidSummary) -> KidOut:
    k = s.kid
    return KidOut(
        id=k.id,
        display_name=k.display_name,
        slug=k.slug,
        birth_year=k.birth_year,
        active=k.active,
        auto_save_enabled=k.auto_save_enabled,
        threshold_offset=k.threshold_offset,
        group_ids=s.group_ids,
        reference_count=s.reference_count,
        today=s.today,
        last_7_days=s.last_7_days,
        created_at=k.created_at,
    )


def _one(request: Request, kid_id: int) -> KidOut:
    with get_session() as s:
        svc.get_kid(s, kid_id)
        return _out(svc.summarize_kids(s, _settings(request).timezone, [kid_id])[0])


@router.get("", response_model=list[KidOut])
def list_kids(request: Request) -> list[KidOut]:
    with get_session() as s:
        return [_out(k) for k in svc.summarize_kids(s, _settings(request).timezone)]


@router.post("", response_model=KidOut, status_code=201)
def create_kid(request: Request, body: KidCreate) -> KidOut:
    with get_session() as s:
        kid = svc.create_kid(s, **body.model_dump())
        kid_id = kid.id
    return _one(request, kid_id)


@router.get("/{kid_id}", response_model=KidOut)
def get_kid(request: Request, kid_id: int) -> KidOut:
    return _one(request, kid_id)


@router.patch("/{kid_id}", response_model=KidOut)
def update_kid(request: Request, kid_id: int, body: KidUpdate) -> KidOut:
    # Only fields present in the body change; an explicit null clears birth_year.
    changes = body.model_dump(include=body.model_fields_set)
    for key in ("display_name", "slug", "active", "auto_save_enabled", "threshold_offset"):
        if changes.get(key, 0) is None:
            del changes[key]  # null means "unchanged" for non-nullable fields
    with get_session() as s:
        svc.update_kid(s, kid_id, **changes)
    return _one(request, kid_id)


@router.delete("/{kid_id}", response_model=KidDeleted)
def delete_kid(request: Request, kid_id: int) -> KidDeleted:
    with get_session() as s:
        result = svc.delete_kid(s, kid_id)
    removed = svc.remove_saved_files(result.files, _settings(request).photos_dir)
    return KidDeleted(
        id=result.kid_id, removed_files=removed, cancelled_forwards=result.cancelled_forwards
    )
