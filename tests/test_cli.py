"""CLI tests: CliRunner, temp data dir, fakes via the existing injection points."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kino import cli
from kino.config import Settings
from kino.db import session as dbs
from kino.models.download import ModelError

runner = CliRunner()


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Isolated data dir + DB; every DB-touching command opens this database."""
    settings = Settings(
        data_dir=tmp_path,
        photos_dir=tmp_path / "photos",
        webhook_secret="s3cret-webhook-secret-value",  # type: ignore[arg-type]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(dbs, "default_url", lambda: f"sqlite:///{tmp_path / 'kino.db'}")
    yield settings
    dbs.get_engine().dispose()


def run(*args: str, input: str | None = None):
    return runner.invoke(cli.app, list(args), input=input)


def one_line_error(result) -> str:
    lines = [ln for ln in result.stderr.splitlines() if ln.strip()]
    assert result.exit_code != 0
    assert len(lines) == 1, lines
    assert lines[0].startswith("error: ")
    return lines[0]


# --- help / version -----------------------------------------------------------------------


COMMANDS = [
    [],
    ["version"],
    ["api"],
    ["worker"],
    ["forward-worker"],
    ["migrate"],
    ["models"],
    ["models", "download"],
    ["enroll"],
]


@pytest.mark.parametrize("cmd", COMMANDS, ids=lambda c: " ".join(c) or "root")
def test_help_works_for_every_command(cmd):
    r = run(*cmd, "--help")
    assert r.exit_code == 0, r.output
    assert "Usage" in r.output


def test_version():
    r = run("version")
    assert r.exit_code == 0
    assert r.output.strip()


# --- migrate / api / worker ----------------------------------------------------------------


def test_migrate_creates_schema(env):
    r = run("migrate")
    assert r.exit_code == 0, r.output
    assert (env.data_dir / "kino.db").exists()


def test_api_runs_uvicorn_factory_after_migrations(env, monkeypatch):
    calls = {}
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: calls.update(args=a, kw=kw))
    r = run("api", "--host", "127.0.0.1", "--port", "9001")
    assert r.exit_code == 0, r.output
    assert calls["args"] == ("kino.api.app:create_app",)
    assert calls["kw"]["factory"] is True
    assert calls["kw"]["host"] == "127.0.0.1"
    assert calls["kw"]["port"] == 9001
    assert (env.data_dir / "kino.db").exists()


def test_api_without_webhook_secret_fails_cleanly(env, monkeypatch):
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=env.data_dir))
    monkeypatch.setattr(cli.uvicorn, "run", lambda *a, **kw: pytest.fail("must not start"))
    msg = one_line_error(run("api"))
    assert "KINO_WEBHOOK_SECRET" in msg


def test_worker_runs_main_queue_with_process_workers(env, monkeypatch):
    from kino import tasks

    made = {}

    class FakeConsumer:
        def __init__(self, h, **kw):
            made["huey"], made["kw"] = h, kw

        def run(self):
            made["ran"] = True

    monkeypatch.setattr(cli, "Consumer", FakeConsumer)
    r = run("worker", "--workers", "3")
    assert r.exit_code == 0, r.output
    assert made["huey"] is tasks.huey
    assert made["kw"] == {"workers": 3, "worker_type": "process"}
    assert made["ran"]


def test_worker_defaults_to_settings_workers(env, monkeypatch):
    made = {}

    class FakeConsumer:
        def __init__(self, h, **kw):
            made.update(kw)

        def run(self):
            pass

    monkeypatch.setattr(cli, "Consumer", FakeConsumer)
    assert run("worker").exit_code == 0
    assert made["workers"] == env.workers


def test_forward_worker_runs_forward_consumer_only(env, monkeypatch):
    from kino import forward

    called = {}
    monkeypatch.setattr(forward, "run_forward_consumer", lambda: called.update(ok=True))
    monkeypatch.setattr(cli, "Consumer", lambda *a, **k: pytest.fail("wrong consumer"))
    r = run("forward-worker")
    assert r.exit_code == 0, r.output
    assert called == {"ok": True}


def test_forward_worker_lock_error_is_one_line(env, monkeypatch):
    from kino import forward

    def boom():
        raise forward.ForwardError("another forward consumer is already running")

    monkeypatch.setattr(forward, "run_forward_consumer", boom)
    assert "already running" in one_line_error(run("forward-worker"))


# --- models download -----------------------------------------------------------------------


def test_models_download_default_embedder(env, monkeypatch, tmp_path):
    seen = []

    def fake(name, data_dir):
        seen.append((name, data_dir))
        p = tmp_path / f"{name}.onnx"
        p.write_bytes(b"x" * 10)
        return p

    monkeypatch.setattr(cli, "ensure_model", fake)
    r = run("models", "download")
    assert r.exit_code == 0, r.output
    assert [n for n, _ in seen] == ["scrfd_10g", "auraface_glintr100"]
    assert "scrfd_10g" in r.output and "checksum OK" in r.output and "10 B" in r.output


def test_models_download_buffalo_and_all(env, monkeypatch, tmp_path):
    seen = []

    def fake(name, data_dir):
        seen.append(name)
        p = tmp_path / f"{name}.onnx"
        p.write_bytes(b"x")
        return p

    monkeypatch.setattr(cli, "ensure_model", fake)
    assert run("models", "download", "--embedder", "buffalo_l").exit_code == 0
    assert seen == ["scrfd_10g", "buffalo_l_r50"]
    seen.clear()
    assert run("models", "download", "--all").exit_code == 0
    assert seen == ["scrfd_10g", "auraface_glintr100", "buffalo_l_r50"]


def test_models_download_error_is_one_line(env, monkeypatch):
    def boom(name, data_dir):
        raise ModelError("downloading model scrfd_10g failed: ConnectError")

    monkeypatch.setattr(cli, "ensure_model", boom)
    assert "ConnectError" in one_line_error(run("models", "download"))


def test_invalid_config_is_one_line(monkeypatch):
    def boom():
        return Settings(workers=0)

    monkeypatch.setattr(cli, "get_settings", boom)
    assert "workers" in one_line_error(run("migrate"))


@pytest.fixture(autouse=True)
def _reset_logging():
    yield
    from loguru import logger

    logger.remove()


# --- enroll ------------------------------------------------------------------------------------

import numpy as np  # noqa: E402
import piexif  # noqa: E402
from PIL import Image as PILImage  # noqa: E402
from sqlalchemy import select  # noqa: E402

from kino import tasks  # noqa: E402
from kino.db.models import Group, Kid, KidGroup, ReferenceFace, Setting  # noqa: E402
from kino.pipeline.detect import Detection  # noqa: E402


def det(x1, y1, side, score=0.9):
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
    return Detection((x1, y1, x1 + w, y1 + h), score, kps)


class FakeDetector:
    """Detections keyed by image width, so each test photo gets its own scripted faces."""

    def __init__(self, by_width):
        self.by_width = by_width

    def detect(self, img, det_size, max_upscale=1.0):
        return list(self.by_width.get(img.shape[1], []))


class FakeEmbedder:
    """Returns the next scripted unit vector for each call."""

    def __init__(self, axes):
        self.axes = list(axes)

    def embed(self, aligned):
        v = np.zeros(512, dtype=np.float32)
        v[self.axes.pop(0)] = 1.0
        return v


def write_jpeg(path, width, exif_date=None):
    rng = np.random.default_rng(width)  # texture so the blur score is not zero
    arr = rng.integers(0, 255, (400, width, 3), dtype=np.uint8)
    kw = {}
    if exif_date:
        kw["exif"] = piexif.dump({"Exif": {piexif.ExifIFD.DateTimeOriginal: exif_date.encode()}})
    PILImage.fromarray(arr).save(path, "JPEG", **kw)


@pytest.fixture
def photos(tmp_path):
    d = tmp_path / "kidphotos"
    d.mkdir()
    return d


@pytest.fixture
def pipeline():
    def _set(by_width, axes):
        tasks.set_pipeline(detector=FakeDetector(by_width), embedder=FakeEmbedder(axes))

    yield _set
    tasks.reset_pipeline()


def refs(kid_slug="noa"):
    with dbs.get_session() as s:
        kid = s.scalar(select(Kid).where(Kid.slug == kid_slug))
        return list(s.scalars(select(ReferenceFace).where(ReferenceFace.kid_id == kid.id)))


def gallery_version():
    with dbs.get_session() as s:
        row = s.get(Setting, "gallery_version")
        return None if row is None else row.value


def test_enroll_creates_kid_references_and_bumps_version(env, photos, pipeline):
    write_jpeg(photos / "a.jpg", 400, exif_date="2024:05:06 07:08:09")
    write_jpeg(photos / "b.jpg", 410)
    pipeline({400: [det(100, 100, 120)], 410: [det(50, 60, 90)]}, [0, 1])
    r = run("enroll", "Noa", str(photos), "--birth-year", "2018")
    assert r.exit_code == 0, r.output
    rows = refs()
    assert len(rows) == 2
    assert all(x.source == "upload" and x.status == "active" for x in rows)
    assert all(x.quality >= 0.4 for x in rows)
    dates = {x.photo_date.year for x in rows}
    assert 2024 in dates  # EXIF date wins; the other falls back to file mtime
    for x in rows:
        assert (env.data_dir / "crops" / "refs") in Path(x.crop_path).parents
        assert Path(x.crop_path).is_file()
    assert gallery_version() == 1
    with dbs.get_session() as s:
        kid = s.scalar(select(Kid))
        assert (kid.display_name, kid.slug, kid.birth_year) == ("Noa", "noa", 2018)
    assert "2 added, 2 active" in r.output and "10" in r.output
    assert "a.jpg" in r.output and "enrolled" in r.output


def test_enroll_skips_no_face_and_small_faces(env, photos, pipeline):
    write_jpeg(photos / "none.jpg", 400)
    write_jpeg(photos / "tiny.jpg", 410)
    pipeline({400: [], 410: [det(10, 10, 30)]}, [])
    r = run("enroll", "Noa", str(photos))
    assert r.exit_code == 0, r.output
    assert refs() == []
    assert gallery_version() is None  # nothing added, nothing to reload
    assert r.output.count("skipped") == 2
    assert "no usable face" in r.output


def test_enroll_skips_ambiguous_faces(env, photos, pipeline):
    write_jpeg(photos / "pair.jpg", 400)
    write_jpeg(photos / "dominant.jpg", 410)
    pipeline(
        {
            400: [det(20, 20, 120), det(220, 20, 100)],  # area ratio 1.44 < 1.5
            410: [det(20, 20, 150), det(250, 20, 60)],  # 6.25x
        },
        [0],
    )
    r = run("enroll", "Noa", str(photos))
    assert r.exit_code == 0, r.output
    assert len(refs()) == 1
    assert "several comparable faces" in r.output


def test_enroll_is_idempotent(env, photos, pipeline):
    write_jpeg(photos / "a.jpg", 400)
    write_jpeg(photos / "b.jpg", 410)
    pipeline({400: [det(100, 100, 120)], 410: [det(50, 60, 90)]}, [0, 1])
    assert run("enroll", "Noa", str(photos)).exit_code == 0
    pipeline({400: [det(100, 100, 120)], 410: [det(50, 60, 90)]}, [0, 1])
    r = run("enroll", "Noa", str(photos))
    assert r.exit_code == 0, r.output
    assert len(refs()) == 2
    assert r.output.count("duplicate") == 2
    assert gallery_version() == 1  # no second bump


def test_enroll_maps_groups_and_rejects_unknown(env, photos, pipeline):
    write_jpeg(photos / "a.jpg", 400)
    pipeline({400: [det(100, 100, 120)]}, [0])
    run("migrate")
    with dbs.get_session() as s:
        s.add(Group(chat_id="120363000@g.us", name="Class", slug="class"))
    r = run("enroll", "Noa", str(photos), "--group", "120363000@g.us")
    assert r.exit_code == 0, r.output
    with dbs.get_session() as s:
        assert len(s.scalars(select(KidGroup)).all()) == 1
    bad = run("enroll", "Dan", str(photos), "--group", "999@g.us")
    assert "unknown group" in one_line_error(bad)
    with dbs.get_session() as s:
        assert s.scalar(select(Kid).where(Kid.slug == "dan")) is None


def test_enroll_errors_are_one_line(env, photos, tmp_path):
    assert "not a directory" in one_line_error(run("enroll", "Noa", str(tmp_path / "missing")))
    assert "no jpg" in one_line_error(run("enroll", "Noa", str(photos)))


def test_enroll_slug_collision(env, photos, pipeline):
    write_jpeg(photos / "a.jpg", 400)
    pipeline({400: [det(100, 100, 120)]}, [0, 1])
    assert run("enroll", "Noa", str(photos)).exit_code == 0
    assert "--slug" in one_line_error(run("enroll", "NOA!", str(photos)))
