"""Test page dry run, re-score and "flag wrong" (CLAUDE.md §9.5, §9.6, §11 intro, §11.5).

Everything here re-uses the stored face embeddings: no detection, no embedding model.
Scores come from :meth:`Matcher.score_many` (the worker's own scoring code, vectorised) and
decisions from :func:`kino.pipeline.decide.decide` plus
:func:`kino.pipeline.decide.one_face_per_kid`, so a dry run predicts exactly what the worker
would decide for the same gallery, thresholds and kid settings.

Dates filter on the photo's ``sent_at`` (falling back to ``received_at``), naive UTC.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kino.config import Settings
from kino.db.models import Face, Group, Image, Kid
from kino.db.session import blob_to_embedding
from kino.pipeline.decide import (
    Action,
    Decision,
    Thresholds,
    decide,
    one_face_per_kid,
)
from kino.pipeline.matcher import KidInfo, Matcher
from kino.pipeline.quality import Tier, tier_for
from kino.services import InvalidError
from kino.services import enroll_web as enroll

MAX_FACES = 20_000  # faces scored per dry run; the newest photos win
BUCKET_CAP = 60  # crops returned per bucket
DEFAULT_DAYS = 14

BUCKETS: tuple[Action, ...] = ("auto_save", "review", "drop")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def photo_date() -> Any:
    """SQL expression for a photo's date: ``sent_at``, else ``received_at``."""
    return func.coalesce(Image.sent_at, Image.received_at)


# --- shared evaluation ------------------------------------------------------------------


@dataclass
class FaceRow:
    face_id: int
    image_id: int
    group_id: int | None
    sent_at: datetime
    size_px: float
    crop_path: str | None
    embedding: bytes


@dataclass
class Evaluated:
    face: FaceRow
    tier: Tier
    decision: Decision  # kid_id None: no kid has references (the worker stores no match)
    auto_save_disabled: bool = False  # would auto-save, but the kid's auto-save is off


def build_matcher(session: Session) -> Matcher:
    m = Matcher()
    m.reload(session)
    return m


def evaluate(matcher: Matcher, thresholds: Thresholds, faces: Sequence[FaceRow]) -> list[Evaluated]:
    """Score and decide ``faces`` like the worker does; faces of one photo must be passed
    together (the one-face-per-kid rule is applied per photo).

    The worker decides with the kid's real auto-save flag; ``decide`` checks that flag last,
    so deciding with auto-save forced on and demoting afterwards gives the same outcome while
    telling which reviews are only due to the flag."""
    if not faces:
        return []
    embs = np.stack([blob_to_embedding(f.embedding) for f in faces])
    out: list[Evaluated] = []
    by_image: dict[int, list[tuple[Evaluated, Decision]]] = {}
    for f, scores in zip(faces, matcher.score_many(embs), strict=True):
        tier = tier_for(f.size_px)
        if not scores:
            ev = Evaluated(f, tier, Decision(None, "drop", 0.0, 0.0, "no kid has references"))
            out.append(ev)
            continue
        best = scores[0].kid_id
        info = matcher.kid(best)
        forced = KidInfo(info.id, True, info.threshold_offset)
        dec = decide(tier, scores, forced, thresholds, matcher.group_mapped(best, f.group_id))
        ev = Evaluated(f, tier, dec)
        out.append(ev)
        by_image.setdefault(f.image_id, []).append((ev, dec))
    for decided in by_image.values():
        one_face_per_kid(decided)
    for ev in out:
        d = ev.decision
        if d.action == "auto_save" and d.kid_id is not None:
            if not matcher.kid(d.kid_id).auto_save_enabled:
                d.action = "review"
                d.reason = "auto-save disabled for kid"
                ev.auto_save_disabled = True
    return out


def _face_rows(q: Any) -> list[FaceRow]:
    return [FaceRow(*r) for r in q]


def _face_query() -> Any:
    return (
        select(
            Face.id,
            Face.image_id,
            Image.group_id,
            photo_date(),
            Face.size_px,
            Face.crop_path,
            Face.embedding,
        )
        .join(Image, Image.id == Face.image_id)
        .where(Face.embedding.is_not(None))
    )


# --- dry run (Test page) ----------------------------------------------------------------


def parse_thresholds(data: dict[str, Any] | None, session: Session) -> Thresholds:
    """The stored thresholds, or an override in the settings shape. The override must be
    accepted in full by the pipeline's own parser, exactly like ``PUT /api/settings``."""
    from kino.services.settings_svc import thresholds_dict

    if data is None:
        return Thresholds.from_settings(session)
    t = Thresholds.from_dict(data)
    got = thresholds_dict(t)
    if {k: got[k] for k in data} != data:
        raise InvalidError("thresholds were not accepted by the pipeline's threshold parser")
    return t


@dataclass
class Bucket:
    count: int = 0
    items: list[Evaluated] = field(default_factory=list)


@dataclass
class Preview:
    date_from: datetime
    date_to: datetime
    examined: int
    truncated: bool
    thresholds: Thresholds
    buckets: dict[str, Bucket]
    per_kid: dict[int, dict[str, int]]
    kid_names: dict[int, str]
    group_names: dict[int, str]
    has_crop: dict[int, bool]


def score_preview(
    session: Session,
    settings: Settings,
    *,
    kid_ids: Sequence[int] | None = None,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    thresholds: dict[str, Any] | None = None,
    now: datetime | None = None,
) -> Preview:
    """Re-score stored faces in memory (no DB or file writes) and bucket the outcomes.

    Buckets are sorted so the items most worth checking come first: ``auto_save`` weakest
    first (likely false positives), ``review`` and ``drop`` strongest first (likely misses)."""
    now = now or _utcnow()
    date_to = date_to or now
    date_from = date_from or date_to - timedelta(days=DEFAULT_DAYS)
    if date_from > date_to:
        raise InvalidError("'from' must not be after 'to'")
    t = parse_thresholds(thresholds, session)
    rows = _face_rows(
        session.execute(
            _face_query()
            .where(photo_date() >= date_from, photo_date() <= date_to)
            .order_by(photo_date().desc(), Face.image_id.desc(), Face.id)
            .limit(MAX_FACES + 1)
        )
    )
    truncated = len(rows) > MAX_FACES
    if truncated:
        partial = rows[MAX_FACES].image_id  # never score half a photo
        rows = rows[:MAX_FACES]
        while rows and rows[-1].image_id == partial:
            rows.pop()
    evaluated = evaluate(build_matcher(session), t, rows)
    wanted = set(kid_ids) if kid_ids is not None else None
    buckets = {a: Bucket() for a in BUCKETS}
    per_kid: dict[int, dict[str, int]] = {}
    for ev in evaluated:
        kid = ev.decision.kid_id
        if wanted is not None and kid not in wanted:
            continue
        b = buckets[ev.decision.action]
        b.count += 1
        b.items.append(ev)
        if kid is not None:
            per_kid.setdefault(kid, dict.fromkeys(BUCKETS, 0))[ev.decision.action] += 1
    for action, b in buckets.items():
        b.items.sort(key=lambda e: e.decision.score if action == "auto_save" else -e.decision.score)
        del b.items[BUCKET_CAP:]
    shown = [ev for b in buckets.values() for ev in b.items]
    crops = settings.data_dir / "crops"
    kid_names = {kid: name for kid, name in session.execute(select(Kid.id, Kid.display_name))}
    gids = {ev.face.group_id for ev in shown if ev.face.group_id is not None}
    group_names = {
        gid: name
        for gid, name in session.execute(select(Group.id, Group.name).where(Group.id.in_(gids)))
        if name
    }
    return Preview(
        date_from=date_from,
        date_to=date_to,
        examined=len(rows),
        truncated=truncated,
        thresholds=t,
        buckets={f"would_{a}": b for a, b in buckets.items()},
        per_kid=per_kid,
        kid_names=kid_names,
        group_names=group_names,
        has_crop={
            ev.face.face_id: enroll.contained(ev.face.crop_path, crops) is not None for ev in shown
        },
    )
