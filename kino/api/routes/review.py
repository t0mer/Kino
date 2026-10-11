"""/api/review: the review queue, single and bulk decisions (CLAUDE.md §11.4, §12).

A repeated identical action answers 200 with ``changed: false``; a different action on an
item that was already decided answers 409. Bulk runs each item in its own transaction:
one failing item never aborts the others.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query, Request
from loguru import logger

from kino.api.deps import protected_router
from kino.api.errors import status_for
from kino.api.schemas import (
    BBox,
    NearRefOut,
    ReviewActionIn,
    ReviewBulkIn,
    ReviewBulkItem,
    ReviewBulkOut,
    ReviewCount,
    ReviewItemOut,
    ReviewPage,
    ReviewResult,
)
from kino.config import Settings
from kino.db.session import get_session
from kino.services import review as svc

router = protected_router(prefix="/api/review", tags=["review"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _item(it: svc.ReviewItem) -> ReviewItemOut:
    m, f = it.match, it.face
    return ReviewItemOut(
        match_id=m.id,
        face_id=f.id,
        image_id=it.image.id,
        kid_id=it.kid.id,
        kid_name=it.kid.display_name,
        score=m.score,
        margin=m.margin,
        tier=it.tier,
        method=m.method,
        group_id=it.group.id if it.group else None,
        group_name=it.group.name if it.group else None,
        sent_at=it.image.sent_at,
        bbox=BBox(x=f.bbox_x, y=f.bbox_y, w=f.bbox_w, h=f.bbox_h),
        crop_url=f"/api/crops/{f.id}" if it.has_crop else None,
        image_url=f"/api/images/{it.image.id}/file" if it.has_image else None,
        nearest_refs=[
            NearRefOut(
                reference_id=n.reference_id,
                similarity=round(n.similarity, 4),
                crop_url=f"/api/references/{n.reference_id}/crop" if n.has_crop else None,
            )
            for n in it.nearest
        ],
    )


def _result(r: svc.DecisionResult) -> ReviewResult:
    return ReviewResult(**vars(r))


@router.get("", response_model=ReviewPage)
def list_review(
    request: Request,
    kid_id: int | None = None,
    method: str | None = None,
    cursor: int | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> ReviewPage:
    with get_session() as s:
        items, nxt = svc.list_review(s, _settings(request), kid_id, method, cursor, limit)
        return ReviewPage(items=[_item(i) for i in items], next_cursor=nxt)


@router.get("/count", response_model=ReviewCount)
def count() -> ReviewCount:
    with get_session() as s:
        total, by_kid = svc.review_count(s)
    return ReviewCount(total=total, by_kid=by_kid)


@router.post("/bulk", response_model=ReviewBulkOut)
def bulk(request: Request, body: ReviewBulkIn) -> ReviewBulkOut:
    settings = _settings(request)
    out: list[ReviewBulkItem] = []
    for mid in dict.fromkeys(body.match_ids):
        try:
            r = svc.apply_decision(settings, mid, body.action)
        except Exception as e:
            status = status_for(e)
            if status is None:
                logger.error("bulk review of {} failed: {}", mid, type(e).__name__)
                out.append(ReviewBulkItem(match_id=mid, status=500, detail="internal error"))
            else:
                out.append(ReviewBulkItem(match_id=mid, status=status, detail=str(e)))
            continue
        out.append(ReviewBulkItem(match_id=mid, status=200, result=_result(r)))
    return ReviewBulkOut(results=out)


@router.post("/{match_id}", response_model=ReviewResult)
def decide(request: Request, match_id: int, body: ReviewActionIn) -> ReviewResult:
    return _result(svc.apply_decision(_settings(request), match_id, body.action, body.kid_id))
