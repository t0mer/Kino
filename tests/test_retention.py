"""Retention: raw files and non-kid face data (CLAUDE.md section 13)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

from kino import retention
from kino.config import Settings
from kino.db import session as dbs
from kino.db.models import (
    Face,
    Forward,
    Image,
    Kid,
    Match,
    NegativeFace,
    ReferenceFace,
    SavedPhoto,
)
from kino.db.session import embedding_to_blob
from kino.services import settings_svc

NOW = datetime.now(UTC).replace(tzinfo=None)
OLD = NOW - timedelta(days=60)
EMB = embedding_to_blob(np.ones(512, dtype=np.float32) / np.sqrt(512))


@pytest.fixture
def env(tmp_path):
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 'r.db'}")
    dbs.run_migrations()
    (tmp_path / "raw").mkdir()
    (tmp_path / "crops").mkdir()
    st = Settings(data_dir=tmp_path, photos_dir=tmp_path / "photos")
    with dbs.get_session() as s:
        s.add(Kid(id=1, display_name="Noa", slug="noa"))
    yield st, tmp_path
    eng.dispose()


def add_image(s, n, tmp, received=OLD, status="done", raw=True):
    path = tmp / "raw" / f"{n}.jpg"
    if raw:
        path.write_bytes(b"x")
    s.add(
        Image(
            id=n,
            message_id=f"m{n}",
            received_at=received,
            status=status,
            raw_path=str(path) if raw else None,
        )
    )
    s.flush()
    return path


def add_face(s, n, image_id, tmp, created=OLD, crop=True):
    path = tmp / "crops" / f"{n}.jpg"
    if crop:
        path.write_bytes(b"c")
    s.add(
        Face(
            id=n,
            image_id=image_id,
            bbox_x=0,
            bbox_y=0,
            bbox_w=50,
            bbox_h=50,
            det_score=0.9,
            size_px=50,
            embedding=EMB,
            crop_path=str(path) if crop else None,
            created_at=created,
        )
    )
    s.flush()
    return path


def test_raw_retention_rules(env):
    st, tmp = env
    with dbs.get_session() as s:
        old = add_image(s, 1, tmp)
        fresh = add_image(s, 2, tmp, received=NOW - timedelta(days=1))
        saved = add_image(s, 3, tmp)
        s.add(SavedPhoto(image_id=3, kid_id=1, path="p"))
        review = add_image(s, 4, tmp)
        add_face(s, 40, 4, tmp)
        s.add(Match(face_id=40, kid_id=1, score=0.4, decision="review"))
        fwd = add_image(s, 5, tmp)
        s.add(Forward(image_id=5, dest_chat_id="x@c.us", status="pending"))
        queued = add_image(s, 6, tmp, status="queued")
        done_fwd = add_image(s, 7, tmp)
        s.add(Forward(image_id=7, dest_chat_id="x@c.us", status="sent"))
    res = retention.run_retention(st, NOW)
    assert res.raw_files == 2  # images 1 and 7
    assert not old.exists() and not done_fwd.exists()
    for kept in (fresh, saved, review, fwd, queued):
        assert kept.exists()
    with dbs.get_session() as s:
        assert s.get(Image, 1).raw_path is None
        assert s.get(Image, 4).raw_path is not None
    assert retention.run_retention(st, NOW).raw_files == 0  # idempotent


def test_face_retention_keeps_kid_refs_negatives(env):
    st, tmp = env
    with dbs.get_session() as s:
        add_image(s, 1, tmp, received=NOW, raw=False)
        gone = add_face(s, 1, 1, tmp)
        young = add_face(s, 2, 1, tmp, created=NOW - timedelta(days=2))
        matched = add_face(s, 3, 1, tmp)
        s.add(Match(face_id=3, kid_id=1, score=0.6, decision="auto_saved"))
        rejected = add_face(s, 4, 1, tmp)
        s.add(Match(face_id=4, kid_id=1, score=0.3, decision="rejected"))
        ref = add_face(s, 5, 1, tmp)
        s.add(ReferenceFace(kid_id=1, face_id=5, embedding=EMB, source="auto"))
        neg = add_face(s, 6, 1, tmp)
        s.add(NegativeFace(kid_id=1, embedding=EMB, source_face_id=6))
    res = retention.run_retention(st, NOW)
    assert res.face_embeddings == 2 and res.face_crops == 2  # faces 1 and 4
    assert not gone.exists() and not rejected.exists()
    for kept in (young, matched, ref, neg):
        assert kept.exists()
    with dbs.get_session() as s:
        f = s.get(Face, 1)
        assert f.embedding is None and f.crop_path is None
        assert s.get(Face, 3).embedding is not None
    assert retention.run_retention(st, NOW).face_embeddings == 0


def test_settings_override_and_path_safety(env, tmp_path_factory):
    st, tmp = env
    outside = tmp_path_factory.mktemp("outside") / "victim.jpg"
    outside.write_bytes(b"v")
    with dbs.get_session() as s:
        add_image(s, 1, tmp, received=NOW - timedelta(days=3))
        s.add(Image(id=2, message_id="m2", received_at=OLD, status="done", raw_path=str(outside)))
        settings_svc.update_retention(s, raw_days=2, face_days=30)
    res = retention.run_retention(st, NOW)
    assert res.raw_files == 1  # 3-day-old image goes with raw_days=2
    assert outside.exists()  # outside the raw dir: refused
