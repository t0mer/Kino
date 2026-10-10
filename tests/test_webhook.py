import base64
import copy
import hashlib
import hmac
import io
import json
import threading
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import select

from kino import tasks
from kino.config import get_settings
from kino.db import session as dbs
from kino.db.models import Group
from kino.db.models import Image as ImageRow
from kino.openwa.schemas import SIGNATURE_HEADER
from kino.pipeline.prefilter import compute_phash, hamming

FIX = Path(__file__).parent / "fixtures" / "openwa"
SECRET = "test-secret"
GROUP = "120363021234567890@g.us"


def jpeg(kind: str = "grad", noise: int = 0) -> bytes:
    if kind == "grad":
        a = np.add.outer(np.arange(256), np.arange(256)) // 2
    else:
        a = ((np.indices((256, 256)).sum(0) // 32) % 2) * 255
    a = a.astype(np.uint8)
    if noise:
        a = a.copy()
        a[0:noise, 0:noise] ^= 0x40
    buf = io.BytesIO()
    Image.fromarray(a).convert("RGB").save(buf, "JPEG", quality=95)
    return buf.getvalue()


@pytest.fixture
def env(monkeypatch, tmp_path):
    for k in list(__import__("os").environ):
        if k.startswith("KINO_"):
            monkeypatch.delenv(k)
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("KINO_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("KINO_OPENWA_SESSION_ID", "kino-session")
    get_settings.cache_clear()
    from kino.api import webhook

    webhook._name_cache.clear()
    yield tmp_path
    get_settings.cache_clear()


@pytest.fixture
def enqueued(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(tasks, "process_image", lambda i: calls.append(i))
    return calls


@pytest.fixture
def client(env, enqueued):
    from kino.api.app import create_app

    app = create_app()
    with TestClient(app) as c:
        c.app_state = app  # type: ignore[attr-defined]
        yield c


def payload(name="image_group", **data_over):
    p = json.loads((FIX / f"{name}.json").read_text())
    p["data"].update(data_over)
    return p


def with_media(p, img: bytes):
    p = copy.deepcopy(p)
    p["data"]["media"] = {"mimetype": "image/jpeg", "data": base64.b64encode(img).decode()}
    return p


def post(client, p, secret=SECRET, body=None, sign=True):
    body = body if body is not None else json.dumps(p).encode()
    headers = {"content-type": "application/json"}
    if sign:
        mac = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers[SIGNATURE_HEADER] = f"sha256={mac}"
    return client.post("/api/webhook/openwa", content=body, headers=headers)


def watch(chat_id=GROUP, name="Class 3B"):
    with dbs.get_session() as s:
        g = s.scalar(select(Group).where(Group.chat_id == chat_id))
        if g is None:
            g = Group(chat_id=chat_id, name=name, slug="class-3b")
            s.add(g)
        g.watched = True


def images():
    with dbs.get_session() as s:
        return list(s.scalars(select(ImageRow).order_by(ImageRow.id)))


def test_startup_refuses_without_secret(env, monkeypatch):
    from kino.api.app import create_app

    monkeypatch.delenv("KINO_WEBHOOK_SECRET")
    get_settings.cache_clear()
    with pytest.raises(RuntimeError):
        create_app()


def test_bad_signature_401(client):
    assert post(client, payload(), secret="wrong").status_code == 401


def test_missing_signature_401(client):
    assert post(client, payload(), sign=False).status_code == 401


def test_signature_checked_against_raw_body(client):
    p = payload()
    body = json.dumps(p).encode()
    mac = hmac.new(SECRET.encode(), body, hashlib.sha256).hexdigest()
    body2 = json.dumps(p, indent=2).encode()  # same JSON, different bytes
    r = client.post(
        "/api/webhook/openwa", content=body2, headers={SIGNATURE_HEADER: f"sha256={mac}"}
    )
    assert r.status_code == 401


def test_wrong_session_403(client):
    p = payload()
    p["sessionId"] = "other"
    assert post(client, p).status_code == 403


def test_text_message_ignored(client):
    assert post(client, payload("text_group")).status_code == 200
    assert images() == []


def test_direct_message_ignored(client):
    assert post(client, payload("image_direct")).status_code == 200
    assert images() == []


def test_from_me_ignored(client):
    watch()
    assert post(client, with_media(payload(fromMe=True), jpeg())).status_code == 200
    assert images() == []


def test_non_message_event_ignored(client):
    p = payload()
    p["event"] = "session.status"
    assert post(client, p).status_code == 200


def test_malformed_payloads_never_500(client):
    assert post(client, None, body=b"not json").status_code == 400
    assert post(client, None, body=b"[1,2]").status_code == 400
    assert post(client, {"event": "message.received", "sessionId": "kino-session"}).status_code in (
        200,
        400,
    )
    assert post(client, {"event": "message.received", "data": 5}).status_code in (200, 400)


def test_unwatched_group_upserted_no_image(client):
    r = post(client, with_media(payload(), jpeg()))
    assert r.status_code == 200
    assert images() == []
    with dbs.get_session() as s:
        g = s.scalar(select(Group).where(Group.chat_id == GROUP))
        assert g is not None and not g.watched and g.last_seen_at is not None
        assert g.name  # fallback name when OpenWA is unreachable


def test_watched_group_stores_raw_and_queues(client, env, enqueued):
    watch()
    r = post(client, with_media(payload(), jpeg()))
    assert r.status_code == 200
    (img,) = images()
    assert img.status == "queued" and img.sender_id and img.phash
    assert img.raw_path is not None
    path = Path(img.raw_path)
    assert path.is_file() and path.suffix == ".jpg" and path.stem == str(img.id)
    assert path.parent.parent.parent == env / "raw"
    assert (img.width, img.height) == (256, 256)
    assert enqueued == [img.id]


def test_duplicate_message_id_no_second_row(client, enqueued):
    watch()
    p = with_media(payload(), jpeg())
    assert post(client, p).status_code == 200
    assert post(client, p).status_code == 200
    assert len(images()) == 1 and len(enqueued) == 1


def test_phash_duplicate_skipped(client, enqueued):
    watch()
    post(client, with_media(payload(id="A1"), jpeg("grad")))
    r = post(client, with_media(payload(id="A2"), jpeg("grad", noise=4)))
    assert r.status_code == 200
    a, b = images()
    assert hamming(a.phash, b.phash) <= 4
    assert b.duplicate_of == a.id and b.status == "skipped" and b.raw_path is None
    assert enqueued == [a.id]


def test_different_image_not_duplicate(client, enqueued):
    watch()
    post(client, with_media(payload(id="A1"), jpeg("grad")))
    post(client, with_media(payload(id="A2"), jpeg("checker")))
    a, b = images()
    assert b.duplicate_of is None and b.status == "queued"
    assert enqueued == [a.id, b.id]


def test_non_image_bytes_rejected_despite_mimetype(client, enqueued):
    watch()
    p = payload()
    p["data"]["media"] = {
        "mimetype": "image/jpeg",
        "data": base64.b64encode(b"<html>not an image</html>").decode(),
    }
    assert post(client, p).status_code == 200
    assert images() == [] and enqueued == []


def test_bad_base64_never_500(client):
    watch()
    p = payload()
    p["data"]["media"] = {"mimetype": "image/jpeg", "data": "@@@not-b64@@@"}
    assert post(client, p).status_code in (200, 400)


def test_media_fetched_when_not_inline(client, enqueued):
    watch()

    class Fake:
        async def get_group_name(self, chat_id):
            return "Class 3B"

        async def get_media(self, chat_id, message_id):
            assert (chat_id, message_id) == (GROUP, "3EB0ABC123")
            return jpeg()

    client.app.state.openwa = Fake()
    p = payload()
    p["data"]["media"] = {"mimetype": "image/jpeg", "omitted": True}
    assert post(client, p).status_code == 200
    assert len(images()) == 1 and len(enqueued) == 1


def test_media_fetch_failure_not_500(client):
    from kino.openwa.client import OpenWAError

    watch()

    class Fake:
        async def get_group_name(self, chat_id):
            return "x"

        async def get_media(self, chat_id, message_id):
            raise OpenWAError("http error", 500, "/x")

    client.app.state.openwa = Fake()
    p = payload()
    p["data"]["media"] = {"mimetype": "image/jpeg", "omitted": True}
    assert post(client, p).status_code == 503
    assert images() == []


def test_group_name_cached_and_fallback_on_failure(client):
    from kino.openwa.client import OpenWAError

    calls = []

    class Fake:
        async def get_group_name(self, chat_id):
            calls.append(chat_id)
            return "Fresh Name"

    client.app.state.openwa = Fake()
    post(client, payload("text_group"))  # ignored: no lookup
    post(client, with_media(payload(id="1"), jpeg()))
    post(client, with_media(payload(id="2"), jpeg("checker")))
    assert calls == [GROUP]
    with dbs.get_session() as s:
        assert s.scalar(select(Group.name).where(Group.chat_id == GROUP)) == "Fresh Name"

    class Boom:
        async def get_group_name(self, chat_id):
            raise OpenWAError("http error", 500, "/x")

    client.app.state.openwa = Boom()
    other = "999@g.us"
    r = post(client, with_media(payload(id="3", chatId=other, **{"from": other}), jpeg()))
    assert r.status_code == 200
    with dbs.get_session() as s:
        assert s.scalar(select(Group.name).where(Group.chat_id == other))


def test_concurrent_same_message_one_row(client, enqueued):
    watch()
    p = with_media(payload(id="RACE"), jpeg())
    codes = []

    def go():
        codes.append(post(client, p).status_code)

    ts = [threading.Thread(target=go) for _ in range(6)]
    for t in ts:
        t.start()
    for t in ts:
        t.join()
    assert codes == [200] * 6
    assert len(images()) == 1


def test_health_ready_metrics(client):
    assert client.get("/healthz").json()["status"] == "ok"
    assert client.get("/readyz").status_code == 200
    assert client.get("/metrics").status_code == 403  # gated until KINO_METRICS_PUBLIC


def test_metrics_public(env, monkeypatch, enqueued):
    from kino.api.app import create_app

    monkeypatch.setenv("KINO_METRICS_PUBLIC", "true")
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        post(c, payload("text_group"))
        text = c.get("/metrics").text
    assert "kino_webhook_requests_total" in text
    assert "kino_images_total" in text and "kino_duplicates_total" in text


def test_phash_helpers():
    a = compute_phash(jpeg("grad"))
    assert len(a) == 16 and hamming(a, a) == 0
    assert hamming(a, compute_phash(jpeg("checker"))) > 4


def test_huey_immediate_mode_runs_stub():
    tasks.huey.immediate = True
    try:
        tasks.process_image(1)
    finally:
        tasks.huey.immediate = False
