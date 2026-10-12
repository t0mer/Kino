"""Huey queues and the image pipeline task (CLAUDE.md sections 3 and 9).

`process_image` is idempotent: the row is claimed atomically (`queued` -> `processing`) and
terminal statuses are never reprocessed, so duplicate enqueues (webhook redelivery, the
sweeper) are harmless. It never raises into huey: an image-specific error (decode, missing
raw file, ...) marks the image `failed` with a sanitised message, so a poison image is not
retried forever. Infrastructure errors (pipeline/model init, SQLite OperationalError) put the
row back to `queued` for the sweeper, at most MAX_TRANSIENT_REQUEUES times.

Heavy models (detector, embedder) and the gallery matcher are built once per worker
process, lazily on first use (or by the consumer's startup hook); tests swap them with
`set_pipeline(...)`.
"""

from __future__ import annotations

import os
import threading
import time
import uuid
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
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from kino import jobs, metrics
from kino.config import Settings, get_settings
from kino.db.models import Face, Group, Image, Kid, Match, ReferenceFace, SavedPhoto, Setting
from kino.db.session import embedding_to_blob, get_session
from kino.pipeline import save as save_mod
from kino.pipeline.decide import Decision, Thresholds, decide, one_face_per_kid
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
MAX_TRANSIENT_REQUEUES = 10
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
        jobs.fail_interrupted_jobs()
    except Exception as e:
        logger.error("interrupted-job cleanup failed: {}", type(e).__name__)
    try:
        get_pipeline()
    except Exception as e:  # first job retries the load and fails that image cleanly
        logger.error("pipeline warm-up failed: {}", type(e).__name__)
        return
    from kino.pipeline.embed import MODEL_NAMES

    for name in ("scrfd_10g", MODEL_NAMES[get_settings().embedder]):
        metrics.set_model_info(name)


# --- helpers -----------------------------------------------------------------------------


def _sanitize(stage: str, e: BaseException) -> str:
    from kino.forward import sanitize_error

    text = "".join(c if c.isprintable() else "?" for c in sanitize_error(e))
    return f"{stage}: {text}"[:ERROR_MAX]


@dataclass
class _Run:
    """Per-job state used for metrics, error messages and cleanup on failure."""

    claimed_at: datetime
    stage: str = "claim"
    # Unique per run, so crop files of concurrent/recovered runs of one image never collide.
    token: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    crops: list[Path] = field(default_factory=list)
    # Photo files this run created (did not exist before its save) -> (st_dev, st_ino).
    created_photos: dict[Path, tuple[int, int]] = field(default_factory=dict)

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


def _claim(image_id: int) -> datetime | None:
    """Atomically take a `queued` row; returns the claim stamp (the run's identity) or None."""
    stamp = _utcnow()
    with get_session() as s:
        res = s.execute(
            update(Image)
            .where(Image.id == image_id, Image.status == "queued")
            .values(status="processing", error=None, processing_started_at=stamp)
        )
        return stamp if res.rowcount == 1 else None  # type: ignore[attr-defined]


class _Superseded(Exception):
    """The row is no longer owned by this run (e.g. the sweeper recovered it)."""


def _owned(image_id: int, claimed_at: datetime) -> tuple[Any, ...]:
    return (
        Image.id == image_id,
        Image.status == "processing",
        Image.processing_started_at == claimed_at,
    )


def _finish(s: Session, image_id: int, claimed_at: datetime, **values: Any) -> None:
    """Final status write, only if this run still owns the row; otherwise the caller's
    transaction is rolled back and this run's output discarded."""
    res = s.execute(
        update(Image).where(*_owned(image_id, claimed_at)).values(**values),
        execution_options={"synchronize_session": False},
    )
    if res.rowcount != 1:  # type: ignore[attr-defined]
        raise _Superseded()


def bump_gallery_version(s: Session) -> None:
    row = s.get(Setting, "gallery_version")
    cur = row.value if row is not None else None
    new = cur + 1 if isinstance(cur, int) and not isinstance(cur, bool) else 1
    if row is None:
        s.add(Setting(key="gallery_version", value=new))
    else:
        row.value = new


def _one_face_per_kid(decided: list[tuple[_FaceWork, Decision]], image_id: int) -> None:
    """See :func:`kino.pipeline.decide.one_face_per_kid`."""
    for _ in one_face_per_kid(decided):
        logger.info(
            "image {}: face demoted to review (kid already matched by a higher-scoring face "
            "in this photo)",
            image_id,
        )


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
        bump_gallery_version(s)
    return changed


def upsert_saved(s: Session, image_id: int, kid_id: int, path: Path) -> None:
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
            _finish(
                s,
                image_id,
                run.claimed_at,
                status="skipped",
                skip_reason=loaded.skip_reason,
                processing_ms=int((time.perf_counter() - t0) * 1000),
            )
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
                crop_path = crop_dir / f"{image_id}_{run.token}_{n}.jpg"
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
                planned = [
                    save_mod.build_dest_path(k, group, image, st.photos_dir, timezone=st.timezone)
                    for k in kids
                ]
                fresh = [p for p in planned if not p.exists()]
                try:
                    paths = save_mod.save_for_kids(
                        raw,
                        kids,
                        group,
                        image,
                        st.photos_dir,
                        strip_gps=st.strip_gps,
                        timezone=st.timezone,
                    )
                finally:
                    for p in fresh:
                        try:
                            ident = os.stat(p)
                        except OSError:
                            continue
                        run.created_photos[p] = (ident.st_dev, ident.st_ino)
                for kid_id, path in paths.items():
                    upsert_saved(s, image_id, kid_id, path)
                out.saved_any = True
            with run.step("forward"):
                rows = fwd.plan_forwards(s, image, kids, paths, group)
                out.forward_ids = [r.id for r in rows if r.status == "pending"]
            with run.step("enrich"):
                _auto_enrich(s, image, auto, now)

        _finish(
            s,
            image_id,
            run.claimed_at,
            status="done",
            error=None,
            skip_reason=None,
            processing_ms=int((time.perf_counter() - t0) * 1000),
        )
        return out


def cleanup_raw(image_id: int) -> None:
    """Delete the raw file once every matched kid is saved and nothing is pending review.
    Called only after a successful commit. Pending forwards do not keep it: they send the
    saved (EXIF-rewritten, GPS-stripped) file, never the raw one."""
    path: str | None = None
    with get_session() as s:
        if _review_pending(s, image_id):
            return
        image = s.get(Image, image_id)
        if image is None or image.status != "done":
            return
        path, image.raw_path = image.raw_path, None
    if path:
        Path(path).unlink(missing_ok=True)


def _mark_failed(image_id: int, claimed_at: datetime, error: str, ms: int) -> str:
    with get_session() as s:
        res = s.execute(
            update(Image)
            .where(*_owned(image_id, claimed_at))
            .values(status="failed", error=error, processing_ms=ms)
        )
        return "failed" if res.rowcount == 1 else "superseded"  # type: ignore[attr-defined]


class _PipelineInitError(Exception):
    """Building the detector/embedder failed (download, checksum, load): infrastructure."""


def _requeue_transient(image_id: int, claimed_at: datetime, error: str, ms: int) -> str:
    """Put the row back to `queued` for the sweeper; after MAX_TRANSIENT_REQUEUES attempts
    mark it `failed` ('infrastructure') so a broken setup cannot loop silently forever."""
    with get_session() as s:
        image = s.get(Image, image_id)
        if (
            image is None
            or image.status != "processing"
            or image.processing_started_at != claimed_at
        ):
            return "superseded"
        image.transient_count += 1
        image.processing_ms = ms
        if image.transient_count >= MAX_TRANSIENT_REQUEUES:
            image.status = "failed"
            image.error = f"infrastructure: {error}"[:ERROR_MAX]
            return "failed"
        image.status = "queued"
        image.processing_started_at = None
        image.error = error
        return "queued"


def _discard_files(run: _Run, image_id: int, photos_dir: Path) -> None:
    """Remove only what THIS run created: its own (uniquely named) crops, and photo files it
    newly created that are still the very file it wrote (same inode) and are not referenced
    by a committed `saved_photos` row (checked in a fresh session). Files of a concurrent
    valid run, pre-existing files and hardlinked siblings that are referenced survive."""
    for p in run.crops:
        p.unlink(missing_ok=True)
    if not run.created_photos:
        return
    try:
        with get_session() as s:
            committed = {
                str(Path(p).resolve())
                for p in s.scalars(select(SavedPhoto.path).where(SavedPhoto.image_id == image_id))
            }
    except Exception as e:  # cannot tell what is committed: keep the files (safe side)
        logger.warning("keeping photos of image {}: {}", image_id, type(e).__name__)
        return
    for p, ident in run.created_photos.items():
        try:
            st = os.stat(p)
            if str(p.resolve()) in committed or (st.st_dev, st.st_ino) != ident:
                continue
            save_mod.remove_photo(p, photos_dir)
        except FileNotFoundError:
            continue
        except (OSError, ValueError) as e:
            logger.warning("could not remove partial photo: {}", type(e).__name__)


def run_pipeline(image_id: int) -> str:
    """Process one image; returns the final status (`done`, `skipped`, `failed`, `queued`
    after a transient error), `not_claimed` if another run owns it or it is terminal,
    `superseded` if the row was recovered elsewhere mid-run, or `deferred` when the DB was
    unavailable to record the outcome (the row is left for the sweeper). Never raises."""
    t0 = time.perf_counter()
    try:
        claimed_at = _claim(image_id)
    except OperationalError as e:
        logger.warning("image {}: claim deferred: {}", image_id, _sanitize("claim", e))
        return "deferred"
    if claimed_at is None:
        return "not_claimed"
    st = get_settings()
    run = _Run(claimed_at)
    try:
        run.stage = "init"
        try:
            pipe = get_pipeline()
        except Exception as e:
            raise _PipelineInitError() from e
        outcome = _process(image_id, st, pipe, run, t0)
    except _Superseded:
        _discard_files(run, image_id, st.photos_dir)
        logger.warning(
            "image {}: run superseded (row recovered elsewhere); output discarded", image_id
        )
        return "superseded"
    except Exception as e:
        ms = int((time.perf_counter() - t0) * 1000)
        _discard_files(run, image_id, st.photos_dir)
        transient = isinstance(e, _PipelineInitError | OperationalError)
        cause = e.__cause__ if isinstance(e, _PipelineInitError) and e.__cause__ else e
        err = _sanitize(run.stage, cause)
        try:
            if transient:
                status = _requeue_transient(image_id, claimed_at, err, ms)
            else:
                status = _mark_failed(image_id, claimed_at, err, ms)
        except OperationalError as db_err:
            logger.warning(
                "image {}: could not record outcome ({}); left for the sweeper: {}",
                image_id,
                _sanitize("record", db_err),
                err,
            )
            return "deferred"
        if transient:
            logger.warning("image {} hit an infrastructure error ({}): {}", image_id, status, err)
        else:
            logger.error("image {} failed: {}", image_id, err)
        if status == "failed":
            metrics.images.labels(status="failed").inc()
        return status

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
            cleanup_raw(image_id)
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
    `failed` with error `stuck` the second time. A row the sweeper enqueued within the last
    5 minutes is not enqueued again (`last_enqueued_at`), so a backlog does not pile up
    duplicate queue entries; any that remain are harmless because claims are atomic."""
    now = now or _utcnow()
    result: dict[str, list[int]] = {"requeued": [], "recovered": [], "failed": []}
    to_enqueue: list[tuple[str, int]] = []
    with get_session() as s:
        for iid in s.scalars(
            select(Image.id).where(
                Image.status == "queued",
                Image.received_at < now - STALE_QUEUED,
                or_(
                    Image.last_enqueued_at.is_(None),
                    Image.last_enqueued_at < now - STALE_QUEUED,
                ),
            )
        ):
            to_enqueue.append(("requeued", iid))
        stuck = s.execute(
            select(Image.id, Image.stuck_count, Image.processing_started_at).where(
                Image.status == "processing",
                or_(
                    Image.processing_started_at.is_(None),
                    Image.processing_started_at < now - STUCK_PROCESSING,
                ),
            )
        ).all()
        for iid, count, started in stuck:
            guard = (
                Image.id == iid,
                Image.status == "processing",
                Image.processing_started_at.is_(None)
                if started is None
                else Image.processing_started_at == started,
            )
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
    enqueued = result["requeued"] + result["recovered"]
    if enqueued:
        with get_session() as s:
            s.execute(update(Image).where(Image.id.in_(enqueued)).values(last_enqueued_at=now))
    if any(result.values()):
        logger.info("image sweeper: {}", {k: len(v) for k, v in result.items()})
    return result


@huey.periodic_task(crontab(minute="*/5"))  # type: ignore[untyped-decorator]
def sweep_images() -> None:
    try:
        sweep_stale_images()
    except Exception as e:
        logger.error("image sweeper failed: {}", type(e).__name__)


def purge_expired_temp_faces() -> int:
    """Delete enrollment temp faces past their 1 h TTL, with their crops."""
    from kino.services.enroll_web import purge_temp_faces

    with get_session() as s:
        n = purge_temp_faces(s, get_settings())
    if n:
        logger.info("purged {} expired upload face(s)", n)
    return n


@huey.periodic_task(crontab(minute="*/10"))  # type: ignore[untyped-decorator]
def purge_temp_faces_task() -> None:
    try:
        purge_expired_temp_faces()
    except Exception as e:
        logger.error("temp face purge failed: {}", type(e).__name__)


def cluster_unknown_faces() -> None:
    """Rebuild the unlabeled "Who is this?" clusters (CLAUDE.md §10)."""
    from kino.services.clusters import run_clustering

    run_clustering()


@huey.task()  # type: ignore[untyped-decorator]
def cluster_unknown_faces_task() -> None:
    """On-demand run (POST /api/clusters/run); never raises into huey."""
    try:
        cluster_unknown_faces()
    except Exception as e:
        logger.error("clustering failed: {}", type(e).__name__)


@huey.periodic_task(crontab(hour="3", minute="30"))  # type: ignore[untyped-decorator]
def nightly_cluster_unknown_faces() -> None:
    try:
        cluster_unknown_faces()
    except Exception as e:
        logger.error("nightly clustering failed: {}", type(e).__name__)


@huey.task()  # type: ignore[untyped-decorator]
def backfill_group_task(job_id: int) -> None:
    """Group backfill (POST /api/groups/{id}/backfill); the job row carries the outcome."""
    from kino.backfill import backfill_group

    try:
        backfill_group(job_id)
    except Exception as e:
        logger.error("backfill task crashed: {}", type(e).__name__)


@huey.periodic_task(crontab(minute="17"))  # type: ignore[untyped-decorator]
def retention_cleanup() -> None:
    """Hourly retention sweep (CLAUDE.md section 10); never raises into huey."""
    from kino.retention import run_retention

    try:
        run_retention()
    except Exception as e:
        logger.error("retention cleanup failed: {}", type(e).__name__)


@huey.periodic_task(crontab(minute="*"))  # type: ignore[untyped-decorator]
def review_digest() -> None:
    """Per-minute tick; sends the review digest when the configured cron expression matches
    (see kino.digest). Never raises into huey."""
    from kino.digest import run_digest_if_due, run_digest_on_drain

    try:
        if not run_digest_if_due():
            run_digest_on_drain()
    except Exception as e:
        logger.error("review digest failed: {}", type(e).__name__)


@huey.periodic_task(crontab(hour="4", minute="10"))  # type: ignore[untyped-decorator]
def gallery_health() -> None:
    """Nightly outlier flagging of reference faces (CLAUDE.md section 10)."""
    from kino.health import run_gallery_health

    try:
        run_gallery_health()
    except Exception as e:
        logger.error("gallery health failed: {}", type(e).__name__)
