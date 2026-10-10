"""Huey queues and the image pipeline task (CLAUDE.md sections 3 and 9).

`process_image` is idempotent: the row is claimed atomically (`queued` -> `processing`) and
terminal statuses are never reprocessed, so duplicate enqueues (webhook redelivery, the
sweeper) are harmless. It never raises into huey: any pipeline error marks the image
`failed` with a sanitised message, so a poison image is not retried forever.

Heavy models (detector, embedder) and the gallery matcher are built once per worker
process, lazily on first use (or by the consumer's startup hook); tests swap them with
`set_pipeline(...)`.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import numpy as np
from huey import SqliteHuey, crontab
from loguru import logger
from numpy.typing import NDArray
from sqlalchemy import or_, select, update
from sqlalchemy.orm import Session

from kino import metrics
from kino.config import Settings, get_settings
from kino.db.models import Face, Group, Image, Kid, Match, ReferenceFace, SavedPhoto, Setting
from kino.db.session import embedding_to_blob, get_session
from kino.pipeline import save as save_mod
from kino.pipeline.decide import Decision, Thresholds, decide
from kino.pipeline.detect import Detector, detect_image
from kino.pipeline.embed import Embedder, align_face, make_crop, should_embed
from kino.pipeline.matcher import Matcher
from kino.pipeline.prefilter import load_for_pipeline
from kino.pipeline.quality import Tier, face_quality, tier_for

_data_dir = get_settings().data_dir
_data_dir.mkdir(parents=True, exist_ok=True)

huey = SqliteHuey("kino", filename=str(_data_dir / "queue.db"))

# Forwarding has its own queue so a single consumer (`-w 1`) keeps sends ordered and rate limited.
forward_huey = SqliteHuey("kino-forward", filename=str(_data_dir / "forward_queue.db"))

AUTO_REF_MIN_QUALITY = 0.7
AUTO_REF_CAP = 10
AUTO_REF_WINDOW = timedelta(days=30)
ERROR_MAX = 300
STALE_QUEUED = timedelta(minutes=5)
STUCK_PROCESSING = timedelta(minutes=30)


def _utcnow() -> datetime:
    """Naive UTC, consistent with the DB models."""
    return datetime.now(UTC).replace(tzinfo=None)


# --- pipeline dependencies ---------------------------------------------------------------


@dataclass
class Pipeline:
    detector: Detector
    embedder: Embedder
    matcher: Matcher
    enqueue_forward: Callable[[int], None]
    now: Callable[[], datetime] = _utcnow


def _default_detector(st: Settings) -> Detector:
    from kino.models.download import ensure_model
    from kino.pipeline.detect import ScrfdDetector

    return ScrfdDetector(ensure_model("scrfd_10g", st.data_dir), st.ort_threads)


def _default_embedder(st: Settings) -> Embedder:
    from kino.pipeline.embed import get_embedder

    return get_embedder(st)


def _default_enqueue_forward(forward_id: int) -> None:
    from kino.forward import send_forward

    send_forward(forward_id)


_lock = threading.Lock()
_pipeline: Pipeline | None = None
_overrides: dict[str, Any] = {}


def set_pipeline(
    *,
    detector: Detector | None = None,
    embedder: Embedder | None = None,
    matcher: Matcher | None = None,
    enqueue_forward: Callable[[int], None] | None = None,
    now: Callable[[], datetime] | None = None,
) -> None:
    """Override pipeline parts (tests). Unset parts use the production defaults."""
    global _pipeline
    given = {
        "detector": detector,
        "embedder": embedder,
        "matcher": matcher,
        "enqueue_forward": enqueue_forward,
        "now": now,
    }
    with _lock:
        _overrides.clear()
        _overrides.update({k: v for k, v in given.items() if v is not None})
        _pipeline = None


def reset_pipeline() -> None:
    set_pipeline()


def get_pipeline() -> Pipeline:
    """The per-process pipeline; models load once, on first use."""
    global _pipeline
    with _lock:
        if _pipeline is None:
            st = get_settings()
            o = _overrides
            _pipeline = Pipeline(
                detector=o["detector"] if "detector" in o else _default_detector(st),
                embedder=o["embedder"] if "embedder" in o else _default_embedder(st),
                matcher=o.get("matcher") or Matcher(),
                enqueue_forward=o.get("enqueue_forward") or _default_enqueue_forward,
                now=o.get("now") or _utcnow,
            )
        return _pipeline


@huey.on_startup()  # type: ignore[untyped-decorator]
def _warm_pipeline() -> None:
    try:
        get_pipeline()
    except Exception as e:  # first job retries the load and fails that image cleanly
        logger.error("pipeline warm-up failed: {}", type(e).__name__)


# --- helpers -----------------------------------------------------------------------------


def _sanitize(stage: str, e: BaseException) -> str:
    from kino.forward import sanitize_error

    text = "".join(c if c.isprintable() else "?" for c in sanitize_error(e))
    return f"{stage}: {text}"[:ERROR_MAX]


@dataclass
class _Run:
    """Per-job state used for metrics, error messages and cleanup on failure."""

    stage: str = "claim"
    crops: list[Path] = field(default_factory=list)
    planned_photos: list[Path] = field(default_factory=list)

    @contextmanager
    def step(self, name: str) -> Iterator[None]:
        self.stage = name
        t0 = time.perf_counter()
        try:
            yield
        finally:
            metrics.pipeline_seconds.labels(stage=name).observe(time.perf_counter() - t0)


@dataclass
class _FaceWork:
    row: Face
    tier: Tier
    quality: float
    emb: NDArray[np.float32] | None


@dataclass
class _Outcome:
    status: str
    forward_ids: list[int] = field(default_factory=list)
    saved_any: bool = False
    tiers: list[tuple[str, str]] = field(default_factory=list)
    matches: list[tuple[str, str]] = field(default_factory=list)


# System drops are `dropped`; `rejected` is reserved for the user's "N" action (M2 review).
_DECISION = {"auto_save": "auto_saved", "review": "review", "drop": "dropped"}


def _claim(image_id: int) -> bool:
    with get_session() as s:
        res = s.execute(
            update(Image)
            .where(Image.id == image_id, Image.status == "queued")
            .values(status="processing", error=None, processing_started_at=_utcnow())
        )
        return bool(res.rowcount == 1)  # type: ignore[attr-defined]


def _bump_gallery_version(s: Session) -> None:
    row = s.get(Setting, "gallery_version")
    cur = row.value if row is not None else None
    new = cur + 1 if isinstance(cur, int) and not isinstance(cur, bool) else 1
    if row is None:
        s.add(Setting(key="gallery_version", value=new))
    else:
        row.value = new


def _one_face_per_kid(decided: list[tuple[_FaceWork, Decision]], image_id: int) -> None:
    """A kid appears at most once per photo: per kid, only the highest-scoring face keeps its
    auto_save/review decision; every other auto_save for that kid is demoted to review (a
    lookalike, sibling or classmate must never be saved or enrolled as the kid). Drops stay."""
    top: dict[int, tuple[_FaceWork, Decision]] = {}
    for w, dec in decided:
        if dec.action == "drop" or dec.kid_id is None:
            continue
        cur = top.get(dec.kid_id)
        if cur is None or dec.score > cur[1].score:
            top[dec.kid_id] = (w, dec)
    for w, dec in decided:
        if dec.kid_id is None or dec.action == "drop" or top[dec.kid_id][0] is w:
            continue
        if dec.action == "auto_save":
            dec.action = "review"
            dec.reason = "kid already matched by a higher-scoring face in this photo"
            logger.info("image {}: face demoted to review ({})", image_id, dec.reason)


def _auto_enrich(s: Session, image: Image, by_kid: dict[int, _FaceWork], now: datetime) -> bool:
    """Enrol each kid's single auto-saved face (its top-scoring face in the photo, decided
    against the pre-job gallery) as an `auto` reference when it is tier L with quality >= 0.7.
    Keeps at most AUTO_REF_CAP active auto refs per kid within AUTO_REF_WINDOW."""
    changed = False
    for kid_id, best in by_kid.items():
        if best.tier is not Tier.L or best.quality < AUTO_REF_MIN_QUALITY:
            continue
        assert best.row.embedding is not None
        s.add(
            ReferenceFace(
                kid_id=kid_id,
                face_id=best.row.id,
                embedding=best.row.embedding,
                crop_path=best.row.crop_path,
                source="auto",
                quality=best.quality,
                photo_date=image.sent_at,
                weight=best.quality,
                added_at=now,
                status="active",
            )
        )
        s.flush()
        recent = s.scalars(
            select(ReferenceFace)
            .where(
                ReferenceFace.kid_id == kid_id,
                ReferenceFace.source == "auto",
                ReferenceFace.status == "active",
                ReferenceFace.added_at >= now - AUTO_REF_WINDOW,
            )
            .order_by(ReferenceFace.added_at.desc(), ReferenceFace.id.desc())
        ).all()
        for old in recent[AUTO_REF_CAP:]:
            old.status = "retired"
        changed = True
    if changed:
        _bump_gallery_version(s)
    return changed


def _upsert_saved(s: Session, image_id: int, kid_id: int, path: Path) -> None:
    row = s.scalar(
        select(SavedPhoto).where(SavedPhoto.image_id == image_id, SavedPhoto.kid_id == kid_id)
    )
    if row is None:
        s.add(SavedPhoto(image_id=image_id, kid_id=kid_id, path=str(path)))
    else:
        row.path = str(path)


def _review_pending(s: Session, image_id: int) -> bool:
    return (
        s.scalar(
            select(Match.id)
            .join(Face, Face.id == Match.face_id)
            .where(Face.image_id == image_id, Match.decision == "review")
            .limit(1)
        )
        is not None
    )


# --- the pipeline ------------------------------------------------------------------------


def _process(image_id: int, st: Settings, pipe: Pipeline, run: _Run, t0: float) -> _Outcome:
    from kino import forward as fwd

    with get_session() as s:
        image = s.get(Image, image_id)
        if image is None:  # deleted after the claim
            raise LookupError("image row disappeared")
        if not image.raw_path:
            raise FileNotFoundError("raw image file is missing")
        raw = Path(image.raw_path)

        with run.step("load"):
            loaded = load_for_pipeline(raw)
        if loaded.bgr is None:
            image.status = "skipped"
            image.skip_reason = loaded.skip_reason
            image.processing_ms = int((time.perf_counter() - t0) * 1000)
            return _Outcome("skipped")
        img = loaded.bgr

        with run.step("detect"):
            dets = detect_image(pipe.detector, img, st)

        out = _Outcome("done")
        works: list[_FaceWork] = []
        now = pipe.now()
        crop_dir = st.data_dir / "crops" / f"{now:%Y}" / f"{now:%m}"
        with run.step("embed"):
            for n, d in enumerate(dets):
                aligned = align_face(img, d)
                fq = face_quality(d, aligned)
                tier = tier_for(fq.size_px)
                emb = pipe.embedder.embed(aligned) if should_embed(tier) else None
                crop_path = crop_dir / f"{image_id}_{n}.jpg"
                crop_path.parent.mkdir(parents=True, exist_ok=True)
                run.crops.append(crop_path)
                crop_path.write_bytes(make_crop(img, d))
                x1, y1, x2, y2 = d.bbox
                row = Face(
                    image_id=image_id,
                    bbox_x=x1,
                    bbox_y=y1,
                    bbox_w=x2 - x1,
                    bbox_h=y2 - y1,
                    det_score=d.score,
                    size_px=fq.size_px,
                    blur_score=fq.blur_score,
                    yaw=fq.yaw,
                    pitch=fq.pitch,
                    quality=fq.quality,
                    from_tile=d.from_tile,
                    embedding=embedding_to_blob(emb) if emb is not None else None,
                    crop_path=str(crop_path),
                    created_at=now,
                )
                s.add(row)
                works.append(_FaceWork(row, tier, fq.quality, emb))
                out.tiers.append((tier.value, "tile" if d.from_tile else "full"))
            s.flush()

        auto: dict[int, _FaceWork] = {}
        with run.step("match"):
            pipe.matcher.maybe_reload(s)
            thresholds = Thresholds.from_settings(s)
            slugs = {k.id: k.slug for k in s.scalars(select(Kid))}
            decided: list[tuple[_FaceWork, Decision]] = []
            for w in works:
                if w.emb is None:
                    continue
                scores = pipe.matcher.score(w.emb, image.group_id)
                if not scores:
                    continue
                best = scores[0].kid_id
                dec = decide(
                    w.tier,
                    scores,
                    pipe.matcher.kid(best),
                    thresholds,
                    pipe.matcher.group_mapped(best, image.group_id),
                )
                if dec.kid_id is not None:
                    decided.append((w, dec))
            _one_face_per_kid(decided, image_id)
            for w, dec in decided:
                assert dec.kid_id is not None
                decision = _DECISION[dec.action]
                s.add(
                    Match(
                        face_id=w.row.id,
                        kid_id=dec.kid_id,
                        score=dec.score,
                        margin=dec.margin,
                        method="face",
                        decision=decision,
                        decided_by="system",
                        decided_at=now,
                    )
                )
                out.matches.append((slugs.get(dec.kid_id, str(dec.kid_id)), decision))
                if dec.action == "auto_save":
                    auto[dec.kid_id] = w
            s.flush()

        if auto:
            kids = [k for k in (s.get(Kid, kid_id) for kid_id in auto) if k is not None]
            group = s.get(Group, image.group_id) if image.group_id is not None else None
            if group is None:
                raise ValueError("image has no group")
            with run.step("save"):
                run.planned_photos = [
                    save_mod.build_dest_path(k, group, image, st.photos_dir, timezone=st.timezone)
                    for k in kids
                ]
                paths = save_mod.save_for_kids(
                    raw,
                    kids,
                    group,
                    image,
                    st.photos_dir,
                    strip_gps=st.strip_gps,
                    timezone=st.timezone,
                )
                for kid_id, path in paths.items():
                    _upsert_saved(s, image_id, kid_id, path)
                out.saved_any = True
            with run.step("forward"):
                rows = fwd.plan_forwards(s, image, kids, paths, group)
                out.forward_ids = [r.id for r in rows if r.status == "pending"]
            with run.step("enrich"):
                _auto_enrich(s, image, auto, now)

        image.status = "done"
        image.error = None
        image.skip_reason = None
        image.processing_ms = int((time.perf_counter() - t0) * 1000)
        return out


def _cleanup_raw(image_id: int) -> None:
    """Delete the raw file once every matched kid is saved, nothing is pending review and
    no forward still needs it. Called only after a successful commit."""
    from kino.forward import forward_blocks_raw_cleanup

    path: str | None = None
    with get_session() as s:
        if forward_blocks_raw_cleanup(s, image_id) or _review_pending(s, image_id):
            return
        image = s.get(Image, image_id)
        if image is None or image.status != "done":
            return
        path, image.raw_path = image.raw_path, None
    if path:
        Path(path).unlink(missing_ok=True)


def _mark_failed(image_id: int, error: str, ms: int) -> None:
    with get_session() as s:
        s.execute(
            update(Image)
            .where(Image.id == image_id, Image.status == "processing")
            .values(status="failed", error=error, processing_ms=ms)
        )


def _discard_files(run: _Run, photos_dir: Path) -> None:
    for p in run.crops:
        p.unlink(missing_ok=True)
    for p in run.planned_photos:
        if p.exists():
            try:
                save_mod.remove_photo(p, photos_dir)
            except (OSError, ValueError) as e:
                logger.warning("could not remove partial photo: {}", type(e).__name__)


def run_pipeline(image_id: int) -> str:
    """Process one image; returns the final status, or `not_claimed` if another run owns it
    (or it is already terminal). Never raises for pipeline errors."""
    t0 = time.perf_counter()
    if not _claim(image_id):
        return "not_claimed"
    st = get_settings()
    run = _Run()
    try:
        pipe = get_pipeline()
        outcome = _process(image_id, st, pipe, run, t0)
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        err = _sanitize(run.stage, e)
        logger.error("image {} failed at {}: {}", image_id, run.stage, err)
        _discard_files(run, st.photos_dir)
        _mark_failed(image_id, err, ms)
        metrics.images.labels(status="failed").inc()
        return "failed"

    metrics.images.labels(status=outcome.status).inc()
    for tier, source in outcome.tiers:
        metrics.faces_detected.labels(tier=tier, source=source).inc()
    for kid, decision in outcome.matches:
        metrics.matches.labels(kid=kid, decision=decision, method="face").inc()
    for fid in outcome.forward_ids:  # after the commit, so the sender sees the row
        try:
            pipe.enqueue_forward(fid)
        except Exception as e:  # the retry_forwards sweep picks the row up later
            logger.error("enqueue of forward {} failed: {}", fid, type(e).__name__)
    if outcome.saved_any:
        try:
            _cleanup_raw(image_id)
        except Exception as e:
            logger.warning("raw cleanup for image {} failed: {}", image_id, type(e).__name__)
    logger.info(
        "image {} {} in {} ms",
        image_id,
        outcome.status,
        int((time.perf_counter() - t0) * 1000),
    )
    return outcome.status


@huey.task()  # type: ignore[untyped-decorator]
def process_image(image_id: int) -> None:
    """Run the face pipeline on one stored image (idempotent; never raises)."""
    try:
        run_pipeline(image_id)
    except Exception as e:  # e.g. DB unavailable during the claim; the sweeper retries
        logger.error("process_image {} aborted: {}", image_id, type(e).__name__)


# --- stale image sweeper -----------------------------------------------------------------


def _enqueue_image(image_id: int) -> None:
    process_image(image_id)


def sweep_stale_images(
    now: datetime | None = None, enqueue: Callable[[int], None] = _enqueue_image
) -> dict[str, list[int]]:
    """Re-enqueue `queued` rows older than 5 minutes (a lost enqueue) and recover rows stuck
    in `processing` for over 30 minutes (a crashed worker): back to `queued` the first time,
    `failed` with error `stuck` the second time. Duplicate enqueues are harmless because
    `process_image` claims rows atomically."""
    now = now or _utcnow()
    result: dict[str, list[int]] = {"requeued": [], "recovered": [], "failed": []}
    to_enqueue: list[tuple[str, int]] = []
    with get_session() as s:
        for iid in s.scalars(
            select(Image.id).where(Image.status == "queued", Image.received_at < now - STALE_QUEUED)
        ):
            to_enqueue.append(("requeued", iid))
        stuck = s.execute(
            select(Image.id, Image.stuck_count).where(
                Image.status == "processing",
                or_(
                    Image.processing_started_at.is_(None),
                    Image.processing_started_at < now - STUCK_PROCESSING,
                ),
            )
        ).all()
        for iid, count in stuck:
            guard = (Image.id == iid, Image.status == "processing")
            if count >= 1:
                s.execute(update(Image).where(*guard).values(status="failed", error="stuck"))
                result["failed"].append(iid)
                metrics.images.labels(status="failed").inc()
            else:
                s.execute(
                    update(Image)
                    .where(*guard)
                    .values(status="queued", stuck_count=count + 1, processing_started_at=None)
                )
                to_enqueue.append(("recovered", iid))
    for kind, iid in to_enqueue:  # after the commit
        try:
            enqueue(iid)
        except Exception as e:  # the next sweep tries again
            logger.error("sweeper enqueue of image {} failed: {}", iid, type(e).__name__)
            continue
        result[kind].append(iid)
    if any(result.values()):
        logger.info("image sweeper: {}", {k: len(v) for k, v in result.items()})
    return result


@huey.periodic_task(crontab(minute="*/5"))  # type: ignore[untyped-decorator]
def sweep_images() -> None:
    try:
        sweep_stale_images()
    except Exception as e:
        logger.error("image sweeper failed: {}", type(e).__name__)
