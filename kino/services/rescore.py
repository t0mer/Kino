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
from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kino.config import Settings
from kino.db.models import Face, Group, Image, Kid, Match, SavedPhoto
from kino.db.session import blob_to_embedding, get_session
from kino.pipeline.decide import (
    Action,
    Decision,
    Thresholds,
    decide,
    one_face_per_kid,
)
from kino.pipeline.matcher import KidInfo, Matcher
from kino.pipeline.quality import Tier, tier_for
from kino.services import InvalidError, ServiceError, review
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


# --- re-score (writes matches) ----------------------------------------------------------

RESCORE_BATCH = 100  # images per transaction
_DECISION = {"auto_save": "auto_saved", "review": "review", "drop": "dropped"}
_SCORE_TOL = 1e-6


@dataclass
class RescoreResult:
    examined: int = 0  # faces with an embedding on `done` images in the window
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    protected: int = 0  # faces left alone: the user decided, or the photo was saved
    saved: int = 0  # photos newly saved for a kid (new auto-save outcomes)


def is_protected(m: Match, saved: set[tuple[int, int]], image_id: int) -> bool:
    """A match re-scoring must never change: decided by the user, confirmed or rejected,
    or auto-saved with its photo still saved for the kid."""
    return (
        m.decided_by == "user"
        or m.decision in ("confirmed", "rejected")
        or (m.decision == "auto_saved" and (image_id, m.kid_id) in saved)
    )


def rescore(settings: Settings, days: int, now: datetime | None = None) -> RescoreResult:
    """Re-run matching and decisions on the stored faces of `done` photos from the last
    ``days`` days, with the current gallery, thresholds and kid settings (CLAUDE.md §11).

    Per face, the worker's outcome is recomputed and written to its ``matches`` (one active
    match per face, for its best kid): a new row is created, or the existing system match
    for that kid is updated; system matches of the face for other kids become ``dropped``.
    Faces with a protected match (:func:`is_protected`) are skipped entirely, so a user
    decision is never overwritten. Running it twice changes nothing the second time.

    A new auto-save outcome saves the photo and plans its forward like the worker (only for
    kids with auto-save enabled; ``decide`` handles that). When the original is gone, the
    kid is already saved from another face of the photo, or saving fails, the face goes to
    review instead. Unlike the worker it does not auto-enrich the gallery, so
    ``gallery_version`` is untouched.

    Runs synchronously in batches of RESCORE_BATCH photos, one transaction each; the
    background jobs mechanism (Task 8) will take it over.
    """
    from kino.tasks import cleanup_raw

    if not 1 <= days <= 365:
        raise InvalidError("days must be between 1 and 365")
    now = now or _utcnow()
    with get_session() as s:
        matcher = build_matcher(s)
        thresholds = Thresholds.from_settings(s)
        image_ids = list(
            s.scalars(
                select(Image.id)
                .where(Image.status == "done", photo_date() >= now - timedelta(days=days))
                .order_by(Image.id)
            )
        )
    res = RescoreResult()
    for i in range(0, len(image_ids), RESCORE_BATCH):
        fx = review._Effects()
        saved_images: set[int] = set()
        part = RescoreResult()
        try:
            with get_session() as s:
                _rescore_batch(
                    s,
                    settings,
                    matcher,
                    thresholds,
                    image_ids[i : i + RESCORE_BATCH],
                    part,
                    fx,
                    saved_images,
                    now,
                )
        except BaseException:
            review._undo(fx, settings)
            raise
        for name in vars(res):
            setattr(res, name, getattr(res, name) + getattr(part, name))
        for fid in fx.forward_ids:
            try:
                review.enqueue_forward(fid)
            except Exception as e:  # the retry_forwards sweep picks the row up later
                logger.error("enqueue of forward {} failed: {}", fid, type(e).__name__)
        for image_id in saved_images:
            try:
                cleanup_raw(image_id)
            except Exception as e:
                logger.warning("raw cleanup for image {} failed: {}", image_id, type(e).__name__)
    logger.info("re-score of the last {} day(s): {}", days, vars(res))
    return res


def _rescore_batch(
    s: Session,
    settings: Settings,
    matcher: Matcher,
    thresholds: Thresholds,
    image_ids: list[int],
    res: RescoreResult,
    fx: review._Effects,
    saved_images: set[int],
    now: datetime,
) -> None:
    rows = _face_rows(
        s.execute(_face_query().where(Face.image_id.in_(image_ids)).order_by(Face.id))
    )
    existing: dict[int, list[Match]] = {}
    for m in s.scalars(
        select(Match).where(Match.face_id.in_([r.face_id for r in rows])).order_by(Match.id)
    ):
        existing.setdefault(m.face_id, []).append(m)
    saved = {
        (img, kid)
        for img, kid in s.execute(
            select(SavedPhoto.image_id, SavedPhoto.kid_id).where(SavedPhoto.image_id.in_(image_ids))
        )
    }
    res.examined += len(rows)
    taken = set(saved)  # (image, kid) already saved, or confirmed from another face
    todo: list[FaceRow] = []
    for r in rows:
        ms = existing.get(r.face_id, [])
        if any(is_protected(m, saved, r.image_id) for m in ms):
            res.protected += 1
            taken.update(
                (r.image_id, m.kid_id) for m in ms if m.decision in ("confirmed", "auto_saved")
            )
        else:
            todo.append(r)
    for ev in evaluate(matcher, thresholds, todo):
        f, dec = ev.face, ev.decision
        ms = existing.get(f.face_id, [])
        changed = False
        if dec.kid_id is None:  # no kid has references: retire stale suggestions
            for m in ms:
                changed |= _set(m, decision="dropped", decided_at=now)
            _count(res, changed)
            continue
        action = dec.action
        if action == "auto_save":
            if (f.image_id, dec.kid_id) in taken or not _save(
                s, settings, f.image_id, dec.kid_id, fx
            ):
                action = "review"
            else:
                taken.add((f.image_id, dec.kid_id))
                saved_images.add(f.image_id)
                res.saved += 1
        decision = _DECISION[action]
        target = next((m for m in ms if m.kid_id == dec.kid_id), None)
        for m in ms:
            if m is not target:
                changed |= _set(m, decision="dropped", decided_at=now)
        if target is None:
            s.add(
                Match(
                    face_id=f.face_id,
                    kid_id=dec.kid_id,
                    score=dec.score,
                    margin=dec.margin,
                    method="face",
                    decision=decision,
                    decided_by="system",
                    decided_at=now,
                )
            )
            res.created += 1
            if changed:  # also retired another kid's suggestion
                res.updated += 1
            continue
        if (
            target.decision != decision
            or target.method != "face"
            or abs(target.score - dec.score) > _SCORE_TOL
            or target.margin is None
            or abs(target.margin - dec.margin) > _SCORE_TOL
        ):
            target.decision = decision
            target.method = "face"
            target.score = dec.score
            target.margin = dec.margin
            target.decided_at = now
            changed = True
        _count(res, changed)
    s.flush()


def _set(m: Match, *, decision: str, decided_at: datetime) -> bool:
    if m.decision == decision:
        return False
    m.decision = decision
    m.decided_at = decided_at
    return True


def _count(res: RescoreResult, changed: bool) -> None:
    if changed:
        res.updated += 1
    else:
        res.unchanged += 1


def _save(s: Session, settings: Settings, image_id: int, kid_id: int, fx: review._Effects) -> bool:
    """Save the photo for the kid like the worker (file, saved_photos row, forward plan).
    False when it cannot be saved (original gone, no group, inactive kid, storage error):
    the face then goes to review."""
    from kino.tasks import upsert_saved

    image = s.get(Image, image_id)
    kid = s.get(Kid, kid_id)
    if image is None or kid is None or not kid.active or image.group_id is None:
        return False
    group = s.get(Group, image.group_id)
    if group is None:
        return False
    try:
        path = review._save_file(s, settings, image, kid, group, fx)
    except ServiceError as e:  # OriginalMissing, unreadable original, storage failure
        logger.info("re-score: image {} goes to review instead of saving: {}", image_id, e)
        return False
    upsert_saved(s, image_id, kid_id, path)
    fx.forward_ids.extend(review.forward_kid(s, image, kid, path, group))
    return True
