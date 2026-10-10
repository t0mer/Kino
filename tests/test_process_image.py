import io
import os
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import piexif
import pytest
from PIL import Image as PILImage
from sqlalchemy import func, select

from kino import tasks
from kino.config import get_settings
from kino.db import session as dbs
from kino.db.models import (
    Face,
    Forward,
    Group,
    Image,
    Kid,
    KidGroup,
    Match,
    ReferenceFace,
    SavedPhoto,
    Setting,
)
from kino.pipeline.detect import Detection

SENT = datetime(2026, 3, 7, 14, 5, 9)
DEST = "97250000001@c.us"


# --- fakes -------------------------------------------------------------------------------


def det(x1, y1, side, score=0.9, from_tile=False):
    w = h = float(side)
    kps = np.array(
        [
            [x1 + 0.3 * w, y1 + 0.4 * h],
            [x1 + 0.7 * w, y1 + 0.4 * h],
            [x1 + 0.5 * w, y1 + 0.62 * h],
            [x1 + 0.35 * w, y1 + 0.8 * h],
            [x1 + 0.65 * w, y1 + 0.8 * h],
        ],
        dtype=np.float32,
    )
    return Detection((x1, y1, x1 + w, y1 + h), score, kps, from_tile)


class FakeDetector:
    def __init__(self, dets=None, delay=None):
        self.dets = list(dets or [])
        self.calls: list[tuple[int, ...]] = []
        self.delay = delay

    def detect(self, img, det_size, max_upscale=1.0):
        self.calls.append(img.shape)
        if self.delay:
            self.delay.wait(2)
        return list(self.dets)


def unit(i):
    v = np.zeros(512, dtype=np.float32)
    v[i] = 1.0
    return v


class FakeEmbedder:
    """Returns the configured vectors in call order (detections are score-ordered)."""

    def __init__(self, vectors=()):
        self.vectors = list(vectors)
        self.calls = 0

    def embed(self, aligned):
        assert aligned.shape == (112, 112, 3)
        v = self.vectors[self.calls % len(self.vectors)] if self.vectors else unit(511)
        self.calls += 1
        return v


class Enqueue:
    """Records forward enqueues and what a fresh session sees at that moment."""

    def __init__(self, fail=False):
        self.ids: list[int] = []
        self.visible: list[str | None] = []
        self.fail = fail

    def __call__(self, fid):
        with dbs.get_session() as s:
            row = s.get(Forward, fid)
            self.visible.append(row.status if row else None)
        self.ids.append(fid)
        if self.fail:
            raise RuntimeError("queue down")


# --- fixtures ----------------------------------------------------------------------------


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("KINO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KINO_PHOTOS_DIR", str(tmp_path / "photos"))
    monkeypatch.setenv("KINO_TILING", "off")
    get_settings.cache_clear()
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 'kino.db'}")
    dbs.run_migrations()
    tasks.huey.immediate = True
    yield tmp_path
    tasks.huey.immediate = False
    tasks.reset_pipeline()
    eng.dispose()
    get_settings.cache_clear()


def pipeline(dets=(), vectors=(), enqueue=None, **kw):
    d, e = FakeDetector(dets, **kw), FakeEmbedder(vectors)
    q = enqueue or Enqueue()
    tasks.set_pipeline(detector=d, embedder=e, enqueue_forward=q)
    return d, e, q


def noisy_jpeg(w=400, h=300, exif=None) -> bytes:
    rng = np.random.default_rng(7)
    buf = io.BytesIO()
    kw = {"exif": exif} if exif else {}
    PILImage.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8)).save(
        buf, "JPEG", quality=90, **kw
    )
    return buf.getvalue()


def seed_group(name="Class 3B", slug="class-3b"):
    with dbs.get_session() as s:
        g = Group(chat_id="120363021234567890@g.us", name=name, slug=slug, watched=True)
        s.add(g)
        s.flush()
        return g.id


def seed_kid(slug, display, vec, auto=True, group_id=None):
    with dbs.get_session() as s:
        k = Kid(display_name=display, slug=slug, auto_save_enabled=auto)
        s.add(k)
        s.flush()
        s.add(
            ReferenceFace(
                kid_id=k.id,
                embedding=dbs.embedding_to_blob(vec),
                source="upload",
                quality=1.0,
                status="active",
            )
        )
        if group_id is not None:
            s.add(KidGroup(kid_id=k.id, group_id=group_id))
        return k.id


def enable_forwarding():
    with dbs.get_session() as s:
        s.merge(Setting(key="forward.enabled", value=True))
        s.merge(Setting(key="forward.default_chat_id", value=DEST))


def seed_image(env, data=None, group_id=None, msg="m1", status="queued", **kw):
    data = noisy_jpeg() if data is None else data
    with dbs.get_session() as s:
        img = Image(
            message_id=msg,
            group_id=group_id,
            sent_at=SENT,
            sender_name="Dana",
            status=status,
            **kw,
        )
        s.add(img)
        s.flush()
        raw = env / "raw" / f"{img.id}.jpg"
        raw.parent.mkdir(parents=True, exist_ok=True)
        raw.write_bytes(data)
        img.raw_path = str(raw)
        return img.id, raw


def get(model, ident):
    with dbs.get_session() as s:
        return s.get(model, ident)


def rows(model, *where):
    with dbs.get_session() as s:
        return list(s.scalars(select(model).where(*where).order_by(model.id)))


def run(image_id):
    tasks.process_image(image_id)
    return get(Image, image_id)


@pytest.fixture
def world(env):
    gid = seed_group()
    noa = seed_kid("noa", "Noa", unit(0), group_id=gid)
    itai = seed_kid("itai", "איתי", unit(1), group_id=gid)
    enable_forwarding()
    return env, gid, noa, itai


# --- end to end --------------------------------------------------------------------------


def test_enrolled_kid_saved_and_forwarded(world):
    env, gid, noa, _ = world
    _, _, q = pipeline([det(100, 80, 120)], [unit(0)])
    iid, raw = seed_image(env, group_id=gid)
    img = run(iid)
    assert img.status == "done" and img.processing_ms is not None and img.error is None
    (face,) = rows(Face, Face.image_id == iid)
    assert face.embedding is not None and not face.from_tile
    assert face.quality and face.quality > 0.7 and face.size_px == 120
    assert Path(face.crop_path).is_file()
    assert PILImage.open(face.crop_path).size == (160, 160)
    assert Path(face.crop_path).is_relative_to(env / "crops")
    (m,) = rows(Match, Match.face_id == face.id)
    assert (m.kid_id, m.decision, m.method, m.decided_by) == (noa, "auto_saved", "face", "system")
    (sp,) = rows(SavedPhoto, SavedPhoto.image_id == iid)
    expected = env / "photos/noa/2026/03" / f"2026-03-07_140509_class-3b_{iid:08d}.jpg"
    assert Path(sp.path) == expected and expected.is_file()
    ex = piexif.load(str(expected))
    assert ex["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:03:07 14:05:09"
    (fw,) = rows(Forward, Forward.image_id == iid)
    assert fw.status == "pending" and fw.dest_chat_id == DEST and fw.kid_ids == [noa]
    assert Path(fw.file_path) == expected
    # enqueued after commit: a fresh session saw the row at enqueue time
    assert q.ids == [fw.id] and q.visible == ["pending"]
    # forward pending -> raw kept
    assert raw.exists() and img.raw_path == str(raw)


def test_timezone_passed_to_save(world, monkeypatch):
    env, gid, noa, _ = world
    monkeypatch.setenv("KINO_TIMEZONE", "Asia/Jerusalem")
    get_settings.cache_clear()
    pipeline([det(100, 80, 120)], [unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    (sp,) = rows(SavedPhoto, SavedPhoto.image_id == iid)
    assert Path(sp.path).name == f"2026-03-07_160509_class-3b_{iid:08d}.jpg"


def test_no_kids_enrolled_done_nothing_saved(env):
    gid = seed_group()
    pipeline([det(100, 80, 120)], [unit(0)])
    iid, raw = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    assert len(rows(Face, Face.image_id == iid)) == 1
    assert rows(Match) == [] and rows(SavedPhoto) == [] and rows(Forward) == []
    assert raw.exists()


def test_zero_faces_done(world):
    env, gid, *_ = world
    pipeline([], [])
    iid, _ = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    assert rows(Face) == []


def test_unknown_face_dropped_not_saved(world):
    env, gid, *_ = world
    pipeline([det(100, 80, 120)], [unit(5)])
    iid, _ = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    (m,) = rows(Match)
    assert (m.decision, m.decided_by) == ("dropped", "system")
    assert rows(SavedPhoto) == []


def test_tiny_image_skipped(world):
    env, gid, *_ = world
    d, _, _ = pipeline([det(10, 10, 50)])
    iid, raw = seed_image(env, data=noisy_jpeg(150, 300), group_id=gid)
    img = run(iid)
    assert (img.status, img.skip_reason) == ("skipped", "too_small")
    assert d.calls == [] and raw.exists()


def _animated() -> bytes:
    frames = [PILImage.new("RGB", (300, 300), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:])
    return buf.getvalue()


def _sticker() -> bytes:
    buf = io.BytesIO()
    PILImage.new("RGBA", (512, 512), (255, 0, 0, 0)).save(buf, "WEBP")
    return buf.getvalue()


@pytest.mark.parametrize(("make", "reason"), [(_animated, "animated"), (_sticker, "sticker")])
def test_animated_and_sticker_skipped(world, make, reason):
    env, gid, *_ = world
    d, _, _ = pipeline([det(10, 10, 50)])
    iid, _ = seed_image(env, data=make(), group_id=gid)
    img = run(iid)
    assert (img.status, img.skip_reason) == ("skipped", reason)
    assert d.calls == []


def test_corrupt_bytes_failed_no_exception(world):
    env, gid, *_ = world
    pipeline([det(10, 10, 50)])
    iid, raw = seed_image(env, data=b"\xff\xd8\xff\xe0 not really a jpeg", group_id=gid)
    img = run(iid)  # must not raise
    assert img.status == "failed"
    assert img.error and len(img.error) <= 300
    assert "not really" not in img.error
    assert raw.exists()


def test_missing_raw_file_failed(world):
    env, gid, *_ = world
    pipeline([])
    iid, raw = seed_image(env, group_id=gid)
    raw.unlink()
    assert run(iid).status == "failed"


def test_two_kids_hardlinked_one_forward(world):
    env, gid, noa, itai = world
    _, _, q = pipeline([det(20, 20, 100, 0.95), det(250, 60, 100, 0.9)], [unit(0), unit(1)])
    iid, _ = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    saved = rows(SavedPhoto, SavedPhoto.image_id == iid)
    assert sorted(s.kid_id for s in saved) == sorted([noa, itai])
    a, b = (Path(s.path) for s in saved)
    assert a != b and os.stat(a).st_ino == os.stat(b).st_ino
    (fw,) = rows(Forward)
    assert sorted(fw.kid_ids) == sorted([noa, itai])
    assert "Noa" in fw.caption and "איתי" in fw.caption
    assert q.ids == [fw.id]


def test_tier_s_goes_to_review(world):
    env, gid, noa, _ = world
    pipeline([det(100, 80, 30)], [unit(0)])
    iid, raw = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    (m,) = rows(Match)
    assert (m.kid_id, m.decision) == (noa, "review")
    assert rows(SavedPhoto) == [] and rows(Forward) == []
    assert raw.exists()


def test_auto_save_disabled_goes_to_review(env):
    gid = seed_group()
    seed_kid("noa", "Noa", unit(0), auto=False, group_id=gid)
    enable_forwarding()
    pipeline([det(100, 80, 120)], [unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    (m,) = rows(Match)
    assert m.decision == "review"
    assert rows(SavedPhoto) == []


def test_xs_face_stored_not_embedded(world):
    env, gid, *_ = world
    _, e, _ = pipeline([det(100, 80, 20)], [unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    (face,) = rows(Face)
    assert face.embedding is None and face.size_px == 20 and face.quality is not None
    assert face.crop_path and Path(face.crop_path).is_file()
    assert e.calls == 0 and rows(Match) == []


def test_from_tile_flag_stored(world):
    env, gid, *_ = world
    pipeline([det(100, 80, 120, from_tile=True)], [unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    assert rows(Face)[0].from_tile is True


def test_exif_orientation_applied_before_detection(world):
    env, gid, *_ = world
    d, _, _ = pipeline([])
    ex = piexif.dump({"0th": {piexif.ImageIFD.Orientation: 6}})
    iid, _ = seed_image(env, data=noisy_jpeg(400, 260, exif=ex), group_id=gid)
    assert run(iid).status == "done"
    assert d.calls[0][:2] == (400, 260)  # rotated: height 400, width 260


# --- idempotency -------------------------------------------------------------------------


def test_run_twice_no_duplicates(world):
    env, gid, *_ = world
    d, _, q = pipeline([det(100, 80, 120)], [unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    run(iid)
    assert len(d.calls) == 1
    assert len(rows(Face)) == 1 and len(rows(Match)) == 1
    assert len(rows(SavedPhoto)) == 1 and len(rows(Forward)) == 1 and len(q.ids) == 1
    assert len([p for p in (env / "photos").rglob("*.jpg")]) == 1


def test_terminal_status_not_reprocessed(world):
    env, gid, *_ = world
    d, _, _ = pipeline([det(100, 80, 120)], [unit(0)])
    for st in ("done", "skipped", "failed", "processing"):
        iid, _ = seed_image(env, group_id=gid, msg=f"m-{st}", status=st)
        assert run(iid).status == st
    assert d.calls == []
    tasks.process_image(999_999)  # unknown id: no error


def test_concurrent_claim_one_processes(world):
    env, gid, *_ = world
    gate = threading.Event()
    d, _, _ = pipeline([det(100, 80, 120)], [unit(0)], delay=gate)
    iid, _ = seed_image(env, group_id=gid)
    results = []
    ts = [threading.Thread(target=lambda: results.append(tasks.run_pipeline(iid))) for _ in "ab"]
    for t in ts:
        t.start()
    gate.set()
    for t in ts:
        t.join()
    assert sorted(results) == ["done", "not_claimed"]
    assert len(d.calls) == 1 and len(rows(Face)) == 1


def test_enqueue_failure_does_not_fail_image(world):
    env, gid, *_ = world
    pipeline([det(100, 80, 120)], [unit(0)], enqueue=Enqueue(fail=True))
    iid, _ = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    assert rows(Forward)[0].status == "pending"


def test_exception_in_save_fails_image(world, monkeypatch):
    env, gid, *_ = world
    _, _, q = pipeline([det(100, 80, 120)], [unit(0)])
    from kino.pipeline import save

    real = save.save_photo

    def boom(*a, **k):
        real(*a, **k)  # file written, then the step fails
        raise OSError("disk full 972501234567")

    monkeypatch.setattr(save, "save_photo", boom)
    iid, raw = seed_image(env, group_id=gid)
    img = run(iid)
    assert img.status == "failed"
    assert "972501234567" not in img.error
    assert raw.exists() and img.raw_path == str(raw)
    assert rows(Forward) == [] and rows(SavedPhoto) == [] and rows(Face) == []
    assert q.ids == []
    assert [p for p in (env / "photos").rglob("*.jpg")] == []
    assert [p for p in (env / "crops").rglob("*.jpg")] == []


# --- raw retention -----------------------------------------------------------------------


def test_raw_deleted_after_save_without_forwarding(env):
    gid = seed_group()
    seed_kid("noa", "Noa", unit(0), group_id=gid)
    pipeline([det(100, 80, 120)], [unit(0)])
    iid, raw = seed_image(env, group_id=gid)
    img = run(iid)
    assert img.status == "done" and img.raw_path is None and not raw.exists()
    assert Path(rows(SavedPhoto)[0].path).is_file()


def test_raw_kept_when_review_pending(env):
    gid = seed_group()
    seed_kid("noa", "Noa", unit(0), group_id=gid)
    seed_kid("itai", "Itai", unit(1), auto=False, group_id=gid)
    pipeline([det(20, 20, 100, 0.95), det(250, 60, 100, 0.9)], [unit(0), unit(1)])
    iid, raw = seed_image(env, group_id=gid)
    img = run(iid)
    assert img.status == "done" and raw.exists() and img.raw_path == str(raw)
    assert len(rows(SavedPhoto)) == 1


# --- auto-enrich -------------------------------------------------------------------------


def test_auto_enrich_adds_reference_and_bumps_version(world):
    env, gid, noa, _ = world
    pipeline([det(100, 80, 120)], [unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    (ref,) = rows(ReferenceFace, ReferenceFace.source == "auto")
    face = rows(Face)[0]
    assert (ref.kid_id, ref.face_id, ref.status) == (noa, face.id, "active")
    assert ref.photo_date == SENT and ref.weight == pytest.approx(face.quality)
    assert ref.embedding == face.embedding
    assert get(Setting, "gallery_version").value is not None


def test_auto_enrich_skipped_for_tier_m(world):
    env, gid, *_ = world
    pipeline([det(100, 80, 60)], [unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    assert rows(Match)[0].decision == "auto_saved"
    assert rows(ReferenceFace, ReferenceFace.source == "auto") == []


def test_auto_enrich_cap_retires_oldest(world):
    env, gid, noa, _ = world
    now = datetime(2026, 3, 10, 12, 0)
    monkey_now = lambda: now  # noqa: E731
    tasks.set_pipeline(
        detector=FakeDetector([det(100, 80, 120)]),
        embedder=FakeEmbedder([unit(0)]),
        enqueue_forward=Enqueue(),
        now=monkey_now,
    )
    with dbs.get_session() as s:
        for i in range(10):
            s.add(
                ReferenceFace(
                    kid_id=noa,
                    embedding=dbs.embedding_to_blob(unit(0)),
                    source="auto",
                    quality=0.9,
                    added_at=now - timedelta(days=20 - i),
                )
            )
        # outside the 30-day window: does not count and is left alone
        s.add(
            ReferenceFace(
                kid_id=noa,
                embedding=dbs.embedding_to_blob(unit(0)),
                source="auto",
                quality=0.9,
                added_at=now - timedelta(days=40),
            )
        )
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    auto = rows(ReferenceFace, ReferenceFace.source == "auto")
    active_recent = [r for r in auto if r.status == "active" and r.added_at >= now - timedelta(30)]
    assert len(active_recent) == 10
    retired = [r for r in auto if r.status == "retired"]
    assert len(retired) == 1 and retired[0].added_at == now - timedelta(days=20)
    assert any(r.face_id is not None and r.status == "active" for r in auto)
    with dbs.get_session() as s:
        n_old = s.scalar(
            select(func.count())
            .select_from(ReferenceFace)
            .where(ReferenceFace.added_at == now - timedelta(days=40))
        )
    assert n_old == 1


def test_matcher_reloaded_once_per_job_and_sees_new_refs(world):
    env, gid, noa, _ = world
    pipeline([det(100, 80, 120)], [unit(7)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    assert rows(Match)[0].decision == "dropped"
    # teach: unit(7) is Noa; the gallery_version bump makes the worker matcher reload
    with dbs.get_session() as s:
        s.add(ReferenceFace(kid_id=noa, embedding=dbs.embedding_to_blob(unit(7)), source="upload"))
        s.merge(Setting(key="gallery_version", value="v-teach"))
    iid2, _ = seed_image(env, group_id=gid, msg="m2")
    run(iid2)
    assert rows(Match)[-1].decision == "auto_saved"


# --- stale image sweeper -----------------------------------------------------------------


def test_claim_records_processing_start(world):
    env, gid, *_ = world
    iid, _ = seed_image(env, group_id=gid)
    before = datetime.now(UTC).replace(tzinfo=None) - timedelta(seconds=5)
    assert tasks._claim(iid)
    img = get(Image, iid)
    assert img.status == "processing" and img.processing_started_at >= before


def test_sweeper_requeues_stale_queued(env):
    gid = seed_group()
    now = datetime(2026, 3, 7, 12, 0)
    old, _ = seed_image(env, group_id=gid, msg="old", received_at=now - timedelta(minutes=6))
    fresh, _ = seed_image(env, group_id=gid, msg="new", received_at=now - timedelta(minutes=2))
    done, _ = seed_image(
        env, group_id=gid, msg="done", status="done", received_at=now - timedelta(hours=1)
    )
    calls: list[int] = []
    result = tasks.sweep_stale_images(now=now, enqueue=calls.append)
    assert calls == [old]
    assert result == {"requeued": [old], "recovered": [], "failed": []}
    assert get(Image, fresh).status == "queued" and get(Image, done).status == "done"


def test_sweeper_recovers_stuck_processing_once_then_fails(env):
    gid = seed_group()
    now = datetime(2026, 3, 7, 12, 0)
    iid, raw = seed_image(
        env,
        group_id=gid,
        status="processing",
        processing_started_at=now - timedelta(minutes=31),
        received_at=now - timedelta(hours=1),
    )
    young, _ = seed_image(
        env,
        group_id=gid,
        msg="young",
        status="processing",
        processing_started_at=now - timedelta(minutes=10),
    )
    calls: list[int] = []
    r1 = tasks.sweep_stale_images(now=now, enqueue=calls.append)
    assert r1["recovered"] == [iid] and calls == [iid]
    img = get(Image, iid)
    assert img.status == "queued" and img.stuck_count == 1
    assert get(Image, young).status == "processing"

    # claimed again and stuck again -> failed 'stuck', not requeued
    later = now + timedelta(hours=1)
    with dbs.get_session() as s:
        s.get(Image, iid).status = "processing"
        s.get(Image, iid).processing_started_at = later - timedelta(minutes=45)
    calls.clear()
    r2 = tasks.sweep_stale_images(now=later, enqueue=calls.append)
    assert r2["failed"] == [iid] and iid not in calls
    img = get(Image, iid)
    assert (img.status, img.error) == ("failed", "stuck")
    assert raw.exists()


def test_sweeper_enqueue_failure_is_contained(env):
    gid = seed_group()
    now = datetime(2026, 3, 7, 12, 0)
    seed_image(env, group_id=gid, received_at=now - timedelta(minutes=10))

    def boom(_):
        raise RuntimeError("queue down")

    assert tasks.sweep_stale_images(now=now, enqueue=boom)["requeued"] == []


def test_sweeper_periodic_task_registered():
    (task_cls,) = [t for t in tasks.huey._registry._periodic_tasks if t.__name__ == "sweep_images"]
    every5 = task_cls().validate_datetime
    assert every5(datetime(2026, 3, 7, 12, 5)) and not every5(datetime(2026, 3, 7, 12, 7))


# --- one face per kid per image ----------------------------------------------------------


def lookalike(i, sim, other=5):
    v = np.zeros(512, dtype=np.float32)
    v[i], v[other] = sim, float(np.sqrt(1 - sim**2))
    return v


def _match_by_face():
    faces = {f.id: f for f in rows(Face)}
    return {faces[m.face_id].bbox_x: m for m in rows(Match)}


def test_lookalike_demoted_and_only_top_face_enrolled(world):
    env, gid, noa, _ = world
    # the lookalike has better detection score and quality order is irrelevant: score decides
    pipeline([det(20, 20, 120, 0.99), det(250, 60, 120, 0.9)], [lookalike(0, 0.8), unit(0)])
    iid, _ = seed_image(env, group_id=gid)
    assert run(iid).status == "done"
    by_x = _match_by_face()
    assert (by_x[250].kid_id, by_x[250].decision) == (noa, "auto_saved")
    assert (by_x[20].kid_id, by_x[20].decision) == (noa, "review")
    assert by_x[20].score == pytest.approx(0.8, abs=1e-5)
    (ref,) = rows(ReferenceFace, ReferenceFace.source == "auto")
    top_face = next(f for f in rows(Face) if f.bbox_x == 250)
    assert ref.face_id == top_face.id
    assert len(rows(SavedPhoto)) == 1 and len(rows(Forward)) == 1


def test_lookalike_review_candidate_demotes_others(world):
    env, gid, noa, _ = world
    # top face for noa is tier S (review); a lower-scoring tier-L face must not auto-save
    pipeline([det(20, 20, 30, 0.99), det(250, 60, 120, 0.9)], [unit(0), lookalike(0, 0.7)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    by_x = _match_by_face()
    assert by_x[20].decision == "review" and by_x[250].decision == "review"
    assert rows(SavedPhoto) == [] and rows(ReferenceFace, ReferenceFace.source == "auto") == []


def test_two_different_kids_still_both_auto_save_and_enrol(world):
    env, gid, noa, itai = world
    pipeline([det(20, 20, 100, 0.95), det(250, 60, 100, 0.9)], [unit(0), unit(1)])
    iid, _ = seed_image(env, group_id=gid)
    run(iid)
    assert {m.decision for m in rows(Match)} == {"auto_saved"}
    refs = rows(ReferenceFace, ReferenceFace.source == "auto")
    assert sorted(r.kid_id for r in refs) == sorted([noa, itai])
