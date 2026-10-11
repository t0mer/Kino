"""GET /api/crops/{face_id}: a pipeline face's display crop (path-checked, authenticated)."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import FileResponse

from kino.api.deps import protected_router
from kino.config import Settings
from kino.db.session import get_session
from kino.services import review as svc

router = protected_router(tags=["crops"])


@router.get("/api/crops/{face_id}")
def face_crop(request: Request, face_id: int) -> FileResponse:
    settings: Settings = request.app.state.settings
    with get_session() as s:
        path = svc.face_crop(s, settings, face_id)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "private"})
