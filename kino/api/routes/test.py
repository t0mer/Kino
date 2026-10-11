"""/api/test: the Test page dry run (CLAUDE.md §11.5, §12).

``POST /api/test/score`` re-scores stored embeddings in memory with the current gallery and
either the stored thresholds or an override (same shape and validation as
``PUT /api/settings/thresholds``, which is what "Apply thresholds" calls). Nothing is written.
"""

from __future__ import annotations

from fastapi import Request

from kino.api.deps import protected_router
from kino.api.schemas import (
    ScoreBucketOut,
    ScoreFaceOut,
    ScoreIn,
    ScoreKidCounts,
    ScoreOut,
    ThresholdsOut,
    naive_utc,
)
from kino.config import Settings
from kino.db.session import get_session
from kino.services import rescore as svc
from kino.services.settings_svc import thresholds_dict

router = protected_router(prefix="/api/test", tags=["test"])


def _settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _face(ev: svc.Evaluated, p: svc.Preview) -> ScoreFaceOut:
    f, d = ev.face, ev.decision
    return ScoreFaceOut(
        face_id=f.face_id,
        image_id=f.image_id,
        crop_url=f"/api/crops/{f.face_id}" if p.has_crop.get(f.face_id) else None,
        score=round(d.score, 4),
        margin=round(d.margin, 4),
        tier=ev.tier.value,
        kid_id=d.kid_id,
        kid_name=p.kid_names.get(d.kid_id) if d.kid_id is not None else None,
        group_id=f.group_id,
        group_name=p.group_names.get(f.group_id) if f.group_id is not None else None,
        sent_at=f.sent_at,
        reason=d.reason,
        auto_save_disabled=ev.auto_save_disabled,
    )


@router.post("/score", response_model=ScoreOut, response_model_by_alias=True)
def score(request: Request, body: ScoreIn) -> ScoreOut:
    with get_session() as s:
        p = svc.score_preview(
            s,
            _settings(request),
            kid_ids=body.kid_ids,
            date_from=naive_utc(body.date_from) if body.date_from else None,
            date_to=naive_utc(body.date_to) if body.date_to else None,
            thresholds=body.thresholds.model_dump() if body.thresholds else None,
        )
        s.rollback()  # a dry run: nothing to commit
    return ScoreOut(
        date_from=p.date_from,
        date_to=p.date_to,
        examined=p.examined,
        truncated=p.truncated,
        thresholds=ThresholdsOut.model_validate(thresholds_dict(p.thresholds)),
        buckets={
            name: ScoreBucketOut(count=b.count, items=[_face(ev, p) for ev in b.items])
            for name, b in p.buckets.items()
        },
        per_kid=[
            ScoreKidCounts(
                kid_id=kid,
                kid_name=p.kid_names.get(kid),
                would_auto_save=c["auto_save"],
                would_review=c["review"],
                would_drop=c["drop"],
            )
            for kid, c in sorted(p.per_kid.items())
        ],
    )
