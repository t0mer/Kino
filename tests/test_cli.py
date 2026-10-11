"""CLI tests: CliRunner, temp data dir, fakes via the existing injection points."""

from __future__ import annotations

from pathlib import Path

import pytest
from typer.testing import CliRunner

from kino import cli
from kino.config import Settings
from kino.db import session as dbs
from kino.models.download import ModelError
from kino.openwa.client import OpenWAClient

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
    ["groups"],
    ["groups", "list"],
    ["groups", "watch"],
    ["groups", "unwatch"],
    ["kids"],
    ["kids", "list"],
    ["kids", "set-auto-save"],
    ["kids", "set-offset"],
    ["forward"],
    ["forward", "set"],
    ["forward", "show"],
    ["forward", "disable"],
    ["forward", "retry"],
    ["forward", "list"],
    ["openwa"],
    ["openwa", "register-webhook"],
    ["bench"],
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


# --- groups / kids / forward ----------------------------------------------------------------------

import httpx  # noqa: E402

from kino.db.models import Forward, Image  # noqa: E402

GID = "120363000111222333@g.us"
DEST = "972500000001@c.us"


def seed_group(chat_id=GID, name="Class", watched=False):
    run("migrate")
    with dbs.get_session() as s:
        g = Group(chat_id=chat_id, name=name, slug="class", watched=watched)
        s.add(g)
        s.flush()
        return g.id


def seed_kid(name="Noa", slug="noa"):
    run("migrate")
    with dbs.get_session() as s:
        k = Kid(display_name=name, slug=slug)
        s.add(k)
        s.flush()
        return k.id


def test_groups_watch_sets_watched_and_replaces_mapping(env):
    seed_group()
    seed_kid("Noa", "noa")
    seed_kid("Itai", "itai")
    r = run("groups", "watch", GID, "--kid", "Noa", "--kid", "itai")
    assert r.exit_code == 0, r.output
    assert GID not in r.output  # chat ids are masked
    with dbs.get_session() as s:
        assert s.scalar(select(Group)).watched is True
        assert len(s.scalars(select(KidGroup)).all()) == 2
    assert run("groups", "watch", GID, "--kid", "Noa").exit_code == 0
    with dbs.get_session() as s:
        assert len(s.scalars(select(KidGroup)).all()) == 1
    assert run("groups", "watch", GID).exit_code == 0  # no --kid keeps the mapping
    with dbs.get_session() as s:
        assert len(s.scalars(select(KidGroup)).all()) == 1


def test_groups_watch_unknown_group_and_kid(env):
    seed_group()
    assert "unknown group" in one_line_error(run("groups", "watch", "999@g.us"))
    assert "no kid named" in one_line_error(run("groups", "watch", GID, "--kid", "Nobody"))
    with dbs.get_session() as s:
        assert s.scalar(select(Group)).watched is False


def test_groups_watch_rejects_forward_destination(env):
    seed_group()
    assert run("forward", "set", GID).exit_code == 0
    assert "forward destination" in one_line_error(run("groups", "watch", GID))
    with dbs.get_session() as s:
        assert s.scalar(select(Group)).watched is False


def test_groups_unwatch_and_list(env):
    seed_group(watched=True)
    r = run("groups", "list")
    assert "watched" in r.output and "Class" in r.output
    assert GID not in r.output and "1203****333@g.us" in r.output
    assert run("groups", "unwatch", GID).exit_code == 0
    assert "unwatched" in run("groups", "list").output


def test_groups_watch_fetch_imports_from_openwa(env, monkeypatch):
    def handler(request):
        assert request.url.path.endswith("/groups")
        return httpx.Response(
            200, json=[{"id": GID, "name": "Class 3B"}, {"id": "x@c.us", "name": "skip"}]
        )

    monkeypatch.setattr(
        cli,
        "OpenWAClient",
        lambda url, key, sid: OpenWAClient(url, key, sid, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(
            data_dir=env.data_dir,
            openwa_url="http://openwa",
            openwa_api_key="k",  # type: ignore[arg-type]
            openwa_session_id="sess",
        ),
    )
    r = run("groups", "watch", GID, "--fetch")
    assert r.exit_code == 0, r.output
    assert "imported 1 new" in r.output
    with dbs.get_session() as s:
        g = s.scalar(select(Group))
        assert (g.name, g.slug, g.watched) == ("Class 3B", "class-3b", True)
        assert len(s.scalars(select(Group)).all()) == 1


def test_groups_fetch_requires_openwa_config(env):
    seed_group()
    assert "KINO_OPENWA_URL" in one_line_error(run("groups", "watch", GID, "--fetch"))


def test_kids_set_auto_save_and_offset(env):
    seed_kid()
    assert run("kids", "set-auto-save", "noa", "--on").exit_code == 0
    with dbs.get_session() as s:
        assert s.scalar(select(Kid)).auto_save_enabled is True
    assert run("kids", "set-auto-save", "Noa", "--off").exit_code == 0
    with dbs.get_session() as s:
        assert s.scalar(select(Kid)).auto_save_enabled is False
    assert run("kids", "set-offset", "Noa", "0.05").exit_code == 0
    with dbs.get_session() as s:
        assert s.scalar(select(Kid)).threshold_offset == pytest.approx(0.05)
    assert run("kids", "set-offset", "Noa", "--", "-0.02").exit_code == 0
    assert "off" in run("kids", "list").output
    assert "-0.02" in run("kids", "list").output


def test_kids_errors(env):
    seed_kid()
    assert "no kid named" in one_line_error(run("kids", "set-auto-save", "Ghost", "--on"))
    assert run("kids", "set-auto-save", "Noa").exit_code != 0  # --on/--off is required
    assert "finite" in one_line_error(run("kids", "set-offset", "Noa", "nan"))


def test_forward_set_show_disable(env):
    run("migrate")
    r = run("forward", "set", DEST)
    assert r.exit_code == 0, r.output
    assert DEST not in r.output
    shown = run("forward", "show").output
    assert "enabled: yes" in shown and "9725****001@c.us" in shown
    assert run("forward", "disable").exit_code == 0
    assert "enabled: no" in run("forward", "show").output


def test_forward_set_rejects_watched_group(env):
    seed_group(watched=True)
    assert "watched group" in one_line_error(run("forward", "set", GID))
    assert run("forward", "set", DEST).exit_code == 0


def test_forward_list_and_retry(env, monkeypatch):
    from kino import forward

    run("migrate")
    with dbs.get_session() as s:
        img = Image(message_id="m1", status="done")
        s.add(img)
        s.flush()
        s.add(
            Forward(
                image_id=img.id, dest_chat_id=DEST, status="failed", attempts=4, last_error="boom"
            )
        )
        s.add(Forward(image_id=img.id, dest_chat_id="other@c.us", status="sent"))
    out = run("forward", "list", "--status", "failed").output
    assert "failed" in out and "boom" in out and "sent" not in out
    assert "status must be" in one_line_error(run("forward", "list", "--status", "bogus"))
    sent = []
    monkeypatch.setattr(forward, "send_forward", lambda fid: sent.append(fid))
    r = run("forward", "retry", "1")
    assert r.exit_code == 0, r.output
    assert sent == [1]
    with dbs.get_session() as s:
        f = s.get(Forward, 1)
        assert (f.status, f.attempts) == ("pending", 0)
    assert "does not exist or has not failed" in one_line_error(run("forward", "retry", "2"))
    assert sent == [1]


# --- openwa register-webhook ---------------------------------------------------------------------

import json  # noqa: E402

SECRET = "s3cret-webhook-secret-value"
HOOK_URL = "http://kino-api:8080/api/webhook/openwa"


@pytest.fixture
def openwa(env, monkeypatch):
    """Fake OpenWA webhook API; `state['hooks']` is what GET returns."""
    state = {"hooks": [], "calls": []}

    def handler(request):
        body = json.loads(request.content) if request.content else None
        state["calls"].append((request.method, request.url.path, body))
        assert request.headers["x-api-key"] == "key"
        if request.method == "GET":
            return httpx.Response(200, json=state["hooks"])
        if request.method == "POST":
            return httpx.Response(201, json={"id": "h-1", **body})
        return httpx.Response(200, json={"id": request.url.path.rsplit("/", 1)[1]})

    monkeypatch.setattr(
        cli,
        "OpenWAClient",
        lambda url, key, sid: OpenWAClient(url, key, sid, transport=httpx.MockTransport(handler)),
    )
    settings = Settings(
        data_dir=env.data_dir,
        openwa_url="http://openwa",
        openwa_api_key="key",  # type: ignore[arg-type]
        openwa_session_id="sess",
        webhook_secret=SECRET,  # type: ignore[arg-type]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    return state


def test_register_webhook_creates_then_updates(openwa):
    r = run("openwa", "register-webhook")
    assert r.exit_code == 0, r.output
    assert r.output.startswith("created webhook h-1")
    assert SECRET not in r.output and SECRET not in r.stderr
    assert "********" in r.output and "message.received" in r.output and HOOK_URL in r.output
    method, path, body = openwa["calls"][-1]
    assert (method, path) == ("POST", "/api/sessions/sess/webhooks")
    assert body["url"] == HOOK_URL
    assert body["events"] == ["message.received"]
    assert body["secret"] == SECRET
    assert {"field": "isGroup", "operator": "is", "value": True} in body["filters"]["conditions"]
    # OpenWA 0.24.0 rejects a bare string here with 400; `type` takes a list
    assert {"field": "type", "operator": "is", "value": ["image"]} in body["filters"]["conditions"]

    openwa["hooks"] = [{"id": "h-1", "url": HOOK_URL}, {"id": "h-2", "url": "http://other"}]
    openwa["calls"].clear()
    r = run("openwa", "register-webhook")
    assert r.exit_code == 0, r.output
    assert r.output.startswith("updated webhook h-1")
    assert SECRET not in r.output
    methods = [(m, p) for m, p, _ in openwa["calls"]]
    assert methods == [
        ("GET", "/api/sessions/sess/webhooks"),
        ("PUT", "/api/sessions/sess/webhooks/h-1"),
    ]
    assert openwa["calls"][-1][2]["active"] is True
    assert "url" not in openwa["calls"][-1][2]


def test_register_webhook_custom_url(openwa):
    r = run("openwa", "register-webhook", "--url", "http://x:1/hook")
    assert r.exit_code == 0, r.output
    assert openwa["calls"][-1][2]["url"] == "http://x:1/hook"


def test_register_webhook_errors_are_one_line(openwa, monkeypatch):
    short = Settings(
        data_dir=cli.get_settings().data_dir,
        openwa_url="http://openwa",
        openwa_api_key="key",  # type: ignore[arg-type]
        openwa_session_id="sess",
        webhook_secret="short",  # type: ignore[arg-type]
    )
    monkeypatch.setattr(cli, "get_settings", lambda: short)
    assert "at least 16" in one_line_error(run("openwa", "register-webhook"))
    monkeypatch.setattr(cli, "get_settings", lambda: Settings(data_dir=short.data_dir))
    assert "KINO_WEBHOOK_SECRET" in one_line_error(run("openwa", "register-webhook"))


def test_register_webhook_http_error_is_masked(env, monkeypatch):
    monkeypatch.setattr(
        cli,
        "OpenWAClient",
        lambda url, key, sid: OpenWAClient(
            url, key, sid, transport=httpx.MockTransport(lambda r: httpx.Response(403))
        ),
    )
    monkeypatch.setattr(
        cli,
        "get_settings",
        lambda: Settings(
            data_dir=env.data_dir,
            openwa_url="http://openwa",
            openwa_api_key="key",  # type: ignore[arg-type]
            openwa_session_id="sess",
            webhook_secret=SECRET,  # type: ignore[arg-type]
        ),
    )
    msg = one_line_error(run("openwa", "register-webhook"))
    assert "403" in msg and "key" not in msg.lower()


# --- bench -----------------------------------------------------------------------------------


def test_bench_prints_stage_timings_and_target(env, photos, pipeline):
    write_jpeg(photos / "a.jpg", 400)
    write_jpeg(photos / "b.jpg", 410)
    pipeline({400: [det(100, 100, 120)], 410: [det(50, 60, 90), det(250, 60, 90)]}, [0] * 10)
    r = run("bench", str(photos), "--repeat", "2")
    assert r.exit_code == 0, r.output
    assert "4 run(s), 6 face(s)" in r.output  # 2 images x 2 repeats; (1 + 2) faces x 2
    for word in ("load", "detect", "quality", "embed", "total", "mean", "p50", "p95"):
        assert word in r.output
    assert "within 2.5s target" in r.output
    assert not (env.data_dir / "kino.db").exists()  # no DB writes


def test_bench_errors(env, photos, tmp_path):
    assert "not a directory" in one_line_error(run("bench", str(tmp_path / "nope")))
    assert "no jpg" in one_line_error(run("bench", str(photos)))


def test_bench_stats():
    from kino.bench import stats

    assert stats([]) == (0.0, 0.0, 0.0)
    mean, p50, p95 = stats([1.0, 2.0, 3.0, 4.0, 100.0])
    assert (mean, p50, p95) == (22.0, 3.0, 100.0)


def test_kids_set_offset_bounded(env):
    seed_kid()
    err = one_line_error(run("kids", "set-offset", "Noa", "--", "-0.5"))
    assert "-0.10" in err and "+0.30" in err
    assert run("kids", "set-offset", "Noa", "0.31").exit_code != 0
    with dbs.get_session() as s:
        assert s.scalar(select(Kid)).threshold_offset == pytest.approx(0.0)
    assert run("kids", "set-offset", "Noa", "--", "-0.05").exit_code == 0
    assert run("kids", "set-offset", "Noa", "0.2").exit_code == 0
    with dbs.get_session() as s:
        assert s.scalar(select(Kid)).threshold_offset == pytest.approx(0.2)


def test_import_groups_fills_missing_slug_only(env):
    run("migrate")
    with dbs.get_session() as s:
        s.add_all(
            [
                Group(chat_id=GID, name="Group 1234567", slug=None),
                Group(chat_id="2@g.us", name="Old", slug="old"),
            ]
        )
        s.flush()
        cli._import_groups(s, [{"id": GID, "name": "גן שמש"}, {"id": "2@g.us", "name": "New"}])
        by_id = {g.chat_id: g for g in s.scalars(select(Group))}
        assert by_id[GID].slug == "gn-shmsh"
        assert (by_id["2@g.us"].name, by_id["2@g.us"].slug) == ("New", "old")
