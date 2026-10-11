import multiprocessing
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from kino import metrics
from kino.config import get_settings


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in list(os.environ):
        if k.startswith("KINO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KINO_WEBHOOK_SECRET", "s")
    monkeypatch.setenv("KINO_METRICS_PUBLIC", "true")
    get_settings.cache_clear()
    yield tmp_path
    get_settings.cache_clear()
    metrics.reset()


def _child(directory: str) -> None:
    from kino import metrics as m

    m.setup(Path(directory))
    m.images.labels(status="done").inc(3)
    m.forwards.labels(status="sent").inc()
    m.set_model_info("scrfd_10g")


def _scrape(env) -> str:
    from kino.api.app import create_app

    with TestClient(create_app()) as c:
        r = c.get("/metrics")
        assert r.status_code == 200
        return r.text


def test_worker_process_counters_visible_in_api_metrics(env):
    from kino.api.app import create_app

    app = create_app()  # API start: sets up <data_dir>/metrics
    mdir = env / "metrics"
    assert mdir.is_dir()
    p = multiprocessing.get_context("spawn").Process(target=_child, args=(str(mdir),))
    p.start()
    p.join(30)
    assert p.exitcode == 0
    with TestClient(app) as c:
        text = c.get("/metrics").text
    assert 'kino_images_total{status="done"} 3.0' in text
    assert 'kino_forwards_total{status="sent"} 1.0' in text
    assert 'kino_model_info{license="insightface' in text and 'name="scrfd_10g"' in text


def test_db_derived_gauges(env):
    from kino.db import session as dbs
    from kino.db.models import Face, Forward, Group, Image, Kid, Match, ReferenceFace

    _scrape(env)  # creates the schema
    with dbs.get_session() as s:
        g = Group(chat_id="1@g.us", name="g", slug="g")
        k = Kid(display_name="Noa", slug="noa")
        s.add_all([g, k])
        s.flush()
        img = Image(message_id="m1", group_id=g.id, status="queued")
        img2 = Image(message_id="m2", group_id=g.id, status="done")
        s.add_all([img, img2])
        s.flush()
        face = Face(
            image_id=img2.id, bbox_x=0, bbox_y=0, bbox_w=1, bbox_h=1, det_score=1.0, size_px=1
        )
        s.add(face)
        s.flush()
        s.add(Match(face_id=face.id, kid_id=k.id, score=0.4, margin=0.4, decision="review"))
        s.add(ReferenceFace(kid_id=k.id, embedding=b"\x00" * 4, source="upload"))
        s.add(Forward(image_id=img2.id, dest_chat_id="d@c.us", kid_ids=[k.id], status="pending"))
    text = _scrape(env)
    assert "kino_queue_depth 1.0" in text
    assert "kino_review_queue_size 1.0" in text
    assert 'kino_gallery_refs{kid="noa",source="upload"} 1.0' in text
    assert "kino_forward_queue_depth 1.0" in text


def test_metrics_still_gated(env, monkeypatch):
    from kino.api.app import create_app

    monkeypatch.setenv("KINO_METRICS_PUBLIC", "false")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        assert c.get("/metrics").status_code == 403


def test_dead_process_files_removed_on_setup(env):
    mdir = env / "metrics"
    mdir.mkdir()
    host = metrics.HOST
    dead = mdir / f"counter_{host}-999999999.db"
    other_host = mdir / "counter_otherhost-999999999.db"
    mine = mdir / f"counter_{host}-{os.getpid()}.db"
    for f in (dead, other_host, mine):
        f.write_bytes(b"")
    metrics.setup(mdir)
    assert not dead.exists()
    assert other_host.exists()  # another container: its pids are not ours to judge
    assert mine.exists()
