"""Group backfill: history -> shared ingestion, job progress, endpoints (CLAUDE.md 8.2)."""

from __future__ import annotations

import base64
import io
import time

import httpx
import numpy as np
import pytest
from PIL import Image
from sqlalchemy import select

from kino import backfill, jobs, tasks
from kino.config import get_settings
from kino.db import session as dbs
from kino.db.models import Forward, Group
from kino.db.models import Image as ImageRow
from kino.openwa.client import OpenWAClient

CHAT = "120363000111222333@g.us"
CHAT2 = "120363000999888777@g.us"


def jpeg(kind: str = "grad") -> bytes:
    if kind == "grad":
        a = np.add.outer(np.arange(256), np.arange(256)) // 2
    else:
        a = ((np.indices((256, 256)).sum(0) // 32) % 2) * 255
    buf = io.BytesIO()
    Image.fromarray(a.astype(np.uint8)).convert("RGB").save(buf, "JPEG", quality=95)
    return buf.getvalue()


def msg(mid: str, kind: str | None = "grad", age_days: float = 1.0, **over):
    m = {
        "id": f"false_{CHAT}_{mid}_1111@lid",
        "type": "image",
        "timestamp": int(time.time() - age_days * 86400),
        "author": "1111@lid",
        "fromMe": False,
    }
    m.update(over)
    return m


@pytest.fixture
def env(api, monkeypatch):
    monkeypatch.setenv("KINO_OPENWA_SESSION_ID", "sess")
    monkeypatch.setenv("KINO_BACKFILL_DELAY_SECONDS", "0")
    get_settings.cache_clear()
    enq: list[int] = []
    monkeypatch.setattr(tasks, "process_image", lambda i: enq.append(i))
    return enq


def make_client(history: dict[str, list], media: dict[str, bytes | int], fail_history=False):
    """history: chat_id -> messages; media: message id fragment -> bytes or HTTP status."""

    def h(req: httpx.Request) -> httpx.Response:
        path = req.url.path
        if path.endswith("/history"):
            if fail_history:
                return httpx.Response(500, text="boom 972501234567")
            chat = path.split("/messages/")[1].split("/history")[0]
            return httpx.Response(200, json=history.get(chat, []))
        for frag, val in media.items():
            if frag in path:
                if isinstance(val, int):
                    return httpx.Response(val)
                return httpx.Response(200, content=val)
        return httpx.Response(404)

    return OpenWAClient("http://owa:2785", "k", "sess", transport=httpx.MockTransport(h))


def add_group(chat=CHAT, watched=True) -> int:
    with dbs.get_session() as s:
        g = Group(chat_id=chat, name="Class", slug="class", watched=watched)
        s.add(g)
        s.flush()
        return g.id


async def run(gid: int, client, days=7) -> int:
    jid = jobs.create_job("backfill", {"days": days}, subject_id=gid)
    await backfill.run_backfill(jid, get_settings(), client)
    await client.aclose()
    return jid


def rows():
    with dbs.get_session() as s:
        return list(s.scalars(select(ImageRow).order_by(ImageRow.id)))


async def test_backfill_ingests_images_in_window(env):
    gid = add_group()
    history = {
        CHAT: [
            msg("A", "grad"),
            msg("B", "check", age_days=2),
            msg("T", type="text"),
            msg("OLD", age_days=30),
        ]
    }
    media = {"_A_": jpeg("grad"), "_B_": jpeg("check")}
    jid = await run(gid, make_client(history, media))
    j = jobs.get_job(jid)
    assert (j.status, j.total, j.processed, j.skipped, j.errors) == ("done", 2, 2, 0, 0)
    assert j.started_at and j.finished_at
    imgs = rows()
    assert [i.status for i in imgs] == ["queued", "queued"]
    assert sorted(env) == sorted(i.id for i in imgs)
    # oldest first, and the sent time comes from the message
    assert imgs[0].sent_at < imgs[1].sent_at


async def test_rerun_does_not_duplicate_or_reenqueue(env):
    gid = add_group()
    history = {CHAT: [msg("A")]}
    media = {"_A_": jpeg()}
    await run(gid, make_client(history, media))
    assert len(env) == 1
    jid = await run(gid, make_client(history, media))
    j = jobs.get_job(jid)
    assert (j.processed, j.skipped, j.errors) == (0, 1, 0)
    assert len(rows()) == 1 and len(env) == 1


async def test_same_image_in_two_groups_is_phash_duplicate(env):
    g1, g2 = add_group(), add_group(CHAT2)
    media = {"_A_": jpeg(), "_Z_": jpeg()}
    await run(g1, make_client({CHAT: [msg("A")]}, media))
    m2 = msg("Z", id=f"false_{CHAT2}_Z_1111@lid")
    jid = await run(g2, make_client({CHAT2: [m2]}, media))
    assert jobs.get_job(jid).skipped == 1
    imgs = rows()
    assert [i.status for i in imgs] == ["queued", "skipped"]
    assert imgs[1].duplicate_of == imgs[0].id and len(env) == 1


async def test_message_failures_are_counted_not_fatal(env):
    gid = add_group()
    history = {
        CHAT: [
            msg("A"),
            msg("B", "check"),
            msg("C"),
            {"type": "image", "timestamp": int(time.time())},
        ]
    }
    # B: media fetch fails; C: not an image; last: malformed
    media = {"_A_": jpeg(), "_B_": 500, "_C_": b"<html>nope</html>"}
    jid = await run(gid, make_client(history, media))
    j = jobs.get_job(jid)
    assert j.status == "done"
    assert (j.total, j.processed, j.errors) == (4, 1, 3)
    assert len(rows()) == 1


async def test_history_failure_fails_job_sanitised(env):
    gid = add_group()
    jid = await run(gid, make_client({}, {}, fail_history=True))
    j = jobs.get_job(jid)
    assert j.status == "failed" and j.error == "OpenWAError"
    assert "972" not in (j.error or "")


async def test_forward_echo_is_ignored(env):
    gid = add_group()
    echo = msg("E", fromMe=True)
    with dbs.get_session() as s:
        s.add(Forward(image_id=_seed_image(s), dest_chat_id="d@c.us", openwa_message_id=echo["id"]))
    jid = await run(gid, make_client({CHAT: [echo]}, {"_E_": jpeg()}))
    assert jobs.get_job(jid).skipped == 1
    assert len(env) == 0


def _seed_image(s) -> int:
    g = s.scalar(select(Group))
    img = ImageRow(message_id="seed", group_id=g.id, status="done")
    s.add(img)
    s.flush()
    return img.id


async def test_from_me_photo_is_ingested(env):
    gid = add_group()
    jid = await run(gid, make_client({CHAT: [msg("M", fromMe=True)]}, {"_M_": jpeg()}))
    assert jobs.get_job(jid).processed == 1


async def test_inline_media_needs_no_fetch(env):
    gid = add_group()
    m = msg("I", media={"mimetype": "image/jpeg", "data": base64.b64encode(jpeg()).decode()})
    jid = await run(gid, make_client({CHAT: [m]}, {}))
    assert jobs.get_job(jid).processed == 1


async def test_unwatched_group_can_be_backfilled(env):
    gid = add_group(watched=False)
    jid = await run(gid, make_client({CHAT: [msg("A")]}, {"_A_": jpeg()}))
    assert jobs.get_job(jid).processed == 1


def test_validate_days():
    from kino.services import InvalidError

    for bad in (0, 91, -1):
        with pytest.raises(InvalidError):
            backfill.validate_days(bad)
    assert backfill.validate_days(1) == 1 and backfill.validate_days(90) == 90


# --- endpoints ----------------------------------------------------------------------------


@pytest.fixture
def started(api, monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(tasks, "backfill_group_task", lambda jid: calls.append(jid))
    api.app.state.openwa = make_client({}, {})
    return calls


def test_endpoints_require_auth(api):
    gid = add_group()
    api.cookies.clear()
    assert api.post(f"/api/groups/{gid}/backfill?days=3").status_code in (401, 403)
    assert api.get("/api/jobs/1").status_code == 401
    assert api.get("/api/jobs").status_code == 401


def test_start_backfill_202_and_job_view(api, started):
    gid = add_group()
    r = api.post(f"/api/groups/{gid}/backfill?days=5")
    assert r.status_code == 202, r.text
    jid = r.json()["job_id"]
    assert started == [jid]
    j = api.get(f"/api/jobs/{jid}").json()
    assert j["status"] == "queued" and j["kind"] == "backfill" and j["group_id"] == gid
    assert j["params"] == {"days": 5} and j["total"] == 0
    assert [x["id"] for x in api.get("/api/jobs?kind=backfill&limit=5").json()] == [jid]


def test_days_validation(api, started):
    gid = add_group()
    for d in (0, 91, -3):
        assert api.post(f"/api/groups/{gid}/backfill?days={d}").status_code == 422
    assert api.post(f"/api/groups/{gid}/backfill?days=abc").status_code == 422
    assert started == []


def test_unknown_group_404_and_conflict_409(api, started):
    assert api.post("/api/groups/999/backfill?days=3").status_code == 404
    gid = add_group()
    assert api.post(f"/api/groups/{gid}/backfill?days=3").status_code == 202
    assert api.post(f"/api/groups/{gid}/backfill?days=3").status_code == 409
    other = add_group(CHAT2)
    assert api.post(f"/api/groups/{other}/backfill?days=3").status_code == 202


def test_finished_job_allows_new_backfill(api, started):
    gid = add_group()
    jid = api.post(f"/api/groups/{gid}/backfill?days=3").json()["job_id"]
    jobs.finish_job(jid)
    assert api.post(f"/api/groups/{gid}/backfill?days=3").status_code == 202


def test_unknown_job_404(api):
    assert api.get("/api/jobs/12345").status_code == 404


def test_openwa_not_configured_503(api):
    gid = add_group()
    assert api.post(f"/api/groups/{gid}/backfill?days=3").status_code == 503


def test_enqueue_failure_marks_job_failed(api, monkeypatch):
    def boom(jid):
        raise RuntimeError("queue down")

    monkeypatch.setattr(tasks, "backfill_group_task", boom)
    api.app.state.openwa = make_client({}, {})
    gid = add_group()
    with pytest.raises(RuntimeError):
        api.post(f"/api/groups/{gid}/backfill?days=3")
    assert jobs.list_jobs("backfill", 1)[0].status == "failed"


def test_backfill_group_not_configured_fails_job(env):
    jid = jobs.create_job("backfill", {"days": 7}, subject_id=add_group())
    backfill.backfill_group(jid)
    j = jobs.get_job(jid)
    assert j is not None and j.status == "failed" and j.error == "RuntimeError"


def test_backfill_group_uses_wizard_config(env, monkeypatch):
    from kino.services.openwa_cfg import save_openwa_config

    seen = {}

    async def fake_run(job_id, settings, client, now=None, *, session_id=None):
        seen.update(host=client._http.base_url.host, session_id=session_id)

    monkeypatch.setattr(backfill, "run_backfill", fake_run)
    with dbs.get_session() as s:
        save_openwa_config(s, get_settings(), url="http://wizard-owa:2785", api_key="wiz-key")
    backfill.backfill_group(jobs.create_job("backfill", {"days": 7}, subject_id=add_group()))
    assert seen == {"host": "wizard-owa", "session_id": "sess"}  # session from the env
