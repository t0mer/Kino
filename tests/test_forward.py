from datetime import datetime
from pathlib import Path

import pytest
from sqlalchemy import select

from kino import forward as fw
from kino.db import session as dbs
from kino.db.models import Forward, Group, Image, Kid, Setting
from kino.forward import ForwardError

SENT = datetime(2026, 3, 7, 14, 5)


@pytest.fixture
def s(tmp_path):
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 't.db'}")
    dbs.run_migrations()
    with dbs.get_session() as sess:
        yield sess
    eng.dispose()


def seed(s):
    g = Group(chat_id="1203630@g.us", name="Class", slug="class", watched=True)
    k1 = Kid(display_name="Noa", slug="noa")
    k2 = Kid(display_name="נועם", slug="noam")
    img = Image(message_id="m1", sent_at=SENT, sender_name="Dana", status="done")
    s.add_all([g, k1, k2, img])
    s.flush()
    img.group_id = g.id
    return g, k1, k2, img


def cfg(s, **kw):
    for k, v in kw.items():
        s.merge(Setting(key="forward." + k, value=v))
    s.flush()


def test_siblings_one_row(s):
    g, k1, k2, img = seed(s)
    cfg(s, enabled=True, default_chat_id="97250000001@c.us")
    rows = fw.plan_forwards(s, img, [k1, k2], {k1.id: Path("/a.jpg"), k2.id: Path("/b.jpg")}, g)
    assert len(rows) == 1
    assert "Noa, נועם" in rows[0].caption
    assert rows[0].kid_ids == [k1.id, k2.id]
    assert rows[0].file_path == "/a.jpg"


def test_no_caption_when_disabled(s):
    g, k1, _, img = seed(s)
    cfg(s, enabled=True, default_chat_id="d@c.us", send_caption=False)
    rows = fw.plan_forwards(s, img, [k1], {k1.id: Path("/a.jpg")}, g)
    assert rows[0].caption == ""


def test_per_kid_destinations(s):
    g, k1, k2, img = seed(s)
    cfg(s, enabled=True, default_chat_id="d@c.us", per_kid={str(k2.id): "x@c.us"})
    rows = fw.plan_forwards(s, img, [k1, k2], {}, g)
    assert {r.dest_chat_id for r in rows} == {"d@c.us", "x@c.us"}
    assert len(rows) == 2


def test_disabled_or_no_destination(s):
    g, k1, _, img = seed(s)
    assert fw.plan_forwards(s, img, [k1], {}, g) == []
    cfg(s, enabled=True)
    assert fw.plan_forwards(s, img, [k1], {}, g) == []
    cfg(s, enabled=False, default_chat_id="d@c.us")
    assert fw.plan_forwards(s, img, [k1], {}, g) == []


def test_watched_destination_rejected_and_skipped(s):
    g, k1, _, img = seed(s)
    with pytest.raises(ForwardError):
        fw.validate_destination(g.chat_id, s)
    with pytest.raises(ForwardError):
        fw.validate_destination("", s)
    cfg(s, enabled=True, default_chat_id=g.chat_id)
    assert fw.plan_forwards(s, img, [k1], {}, g) == []
    with pytest.raises(ForwardError):
        fw.set_default_destination(s, g.chat_id)


def test_set_default_destination_enables(s):
    seed(s)
    fw.set_default_destination(s, " d@c.us ")
    c = fw.get_forward_config(s)
    assert c.enabled and c.default_chat_id == "d@c.us"
    assert c.caption_template == fw.DEFAULT_CAPTION


def test_plan_idempotent(s):
    g, k1, _, img = seed(s)
    cfg(s, enabled=True, default_chat_id="d@c.us")
    a = fw.plan_forwards(s, img, [k1], {}, g)
    b = fw.plan_forwards(s, img, [k1], {}, g)
    assert a[0].id == b[0].id
    assert len(list(s.scalars(select(Forward)))) == 1


def test_caption_rendering():
    t = "{kids} · {group} · {sender} · {date} {time} {nope}"
    out = fw.render_caption(t, ["Noa", "נועם"], "Class", "Dana", SENT)
    assert out == "Noa, נועם · Class · Dana · 2026-03-07 14:05 {nope}"
    assert fw.render_caption("{kids", ["a"], "g", "s", SENT) == "{kids"
    assert fw.render_caption("{}{{kids}}", ["a"], "g", "s", SENT) == "{}{a}"
    assert fw.render_caption("{kids}", ["a\x00b\x07"], "g", "s", SENT) == "ab"
    assert len(fw.render_caption("{kids}", ["x" * 5000] * 50, "g", "s", SENT)) <= 1024
    # OpenWA SendMediaMessageDto caption maxLength is 1024
    assert fw.render_caption("x" * 5000, [], "", "", SENT) == "x" * 1024
    assert fw.render_caption("{kids}" * 30, ["y" * 120], "", "", SENT) == "y" * 1024


def test_cancel_only_pending(s):
    g, k1, _, img = seed(s)
    cfg(s, enabled=True, default_chat_id="d@c.us")
    (f,) = fw.plan_forwards(s, img, [k1], {}, g)
    assert fw.cancel_forwards(s, img.id, k1.id + 99) == 0
    assert fw.cancel_forwards(s, img.id, k1.id) == 1
    assert f.status == "cancelled"
    f.status = "sent"
    assert fw.cancel_forwards(s, img.id, k1.id) == 0
    assert f.status == "sent"


# --- sender ---

from datetime import timedelta  # noqa: E402

from kino.openwa.client import OpenWAError  # noqa: E402


class FakeClient:
    def __init__(self, fail=0, msg="MSG1"):
        self.fail, self.msg, self.calls = fail, msg, []

    async def send_image(self, chat_id, data, mimetype, caption):
        self.calls.append((chat_id, data, mimetype, caption))
        if self.fail:
            raise OpenWAError("send-image failed", 500, "/x/97250000001@c.us")
        return self.msg

    async def aclose(self):
        pass


class Clock:
    def __init__(self):
        self.t = 1000.0
        self.sleeps = []

    def __call__(self):
        return self.t

    def sleep(self, d):
        self.sleeps.append(d)
        self.t += d


class FixedRng:
    def uniform(self, a, b):
        return a


def limiter(clock=None):
    c = clock or Clock()
    return fw.RateLimiter(6, c, c.sleep, FixedRng()), c


@pytest.fixture
def pending(s, tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "_photos_dir", lambda: tmp_path)
    g, k1, _, img = seed(s)
    f = tmp_path / "p.jpg"
    f.write_bytes(b"\xff\xd8jpegdata")
    cfg(s, enabled=True, default_chat_id="97250000001@c.us")
    (row,) = fw.plan_forwards(s, img, [k1], {k1.id: f}, g)
    s.commit()
    return row.id, f


def row(fid):
    with dbs.get_session() as s2:
        return s2.get(Forward, fid)


T0 = datetime(2026, 3, 7, 12, 0)


def test_send_success(pending):
    fid, _ = pending
    c = FakeClient()
    assert (
        fw.deliver_forward(fid, client_factory=lambda: c, limiter=limiter()[0], now=lambda: T0)
        == "sent"
    )
    r = row(fid)
    assert (r.status, r.openwa_message_id, r.sent_at) == ("sent", "MSG1", T0)
    assert c.calls[0][1] == b"\xff\xd8jpegdata" and c.calls[0][2] == "image/jpeg"
    # not pending any more -> skipped, no second send
    assert fw.deliver_forward(fid, client_factory=lambda: c, limiter=limiter()[0]) == "skipped"
    assert len(c.calls) == 1


def deliver(fid, c, lim, at=T0):
    return fw.deliver_forward(fid, client_factory=lambda: c, limiter=lim, now=lambda: at)


def test_retry_schedule_then_failed(pending):
    fid, _ = pending
    c = FakeClient(fail=1)
    lim, _ = limiter()
    at = T0
    for secs in [60, 300, 1800, 7200]:
        assert deliver(fid, c, lim, at) == "retry"
        r = row(fid)
        assert r.status == "pending"
        assert r.next_attempt_at == at + timedelta(seconds=secs)
        assert "50000001" not in (r.last_error or "")
        assert deliver(fid, c, lim, at) == "skipped"  # not due yet
        at = r.next_attempt_at
    assert deliver(fid, c, lim, at) == "retry"
    r = row(fid)
    assert r.status == "failed" and r.attempts == 5 and r.last_error


def test_manual_retry_resets_failed(pending):
    fid, _ = pending
    with dbs.get_session() as s2:
        f = s2.get(Forward, fid)
        f.status, f.attempts, f.last_error = "failed", 5, "boom"
        assert fw.retry_forward(s2, fid)
        assert (f.status, f.attempts, f.last_error) == ("pending", 0, None)
        assert not fw.retry_forward(s2, fid)  # pending rows are not retried manually


def test_missing_file_fails_cleanly(pending):
    fid, f = pending
    f.unlink()
    c = FakeClient()
    assert fw.deliver_forward(fid, client_factory=lambda: c, limiter=limiter()[0]) == "failed"
    r = row(fid)
    assert r.status == "failed" and "missing" in r.last_error and not c.calls


def test_rate_limiter_spacing():
    lim, c = limiter()
    lim.wait()
    assert c.sleeps == []
    lim.wait()
    assert c.sleeps == [pytest.approx(12.0)]  # 10 s interval + 2 s jitter
    c.t += 100
    lim.wait()
    assert len(c.sleeps) == 1


def test_enqueue_due_lists_rows(pending):
    fid, _ = pending
    now = datetime(2026, 3, 7, 12, 0)
    assert fw.enqueue_due_forwards(now) == []  # fresh row, not stale yet
    created = row(fid).created_at
    assert fw.enqueue_due_forwards(created + timedelta(minutes=6)) == [fid]
    # the sweep does not claim; `_claim` makes duplicate enqueues harmless
    assert fw.enqueue_due_forwards(created + timedelta(minutes=7)) == [fid]
    assert fw._claim(fid, created + timedelta(minutes=7))
    assert fw.enqueue_due_forwards(created + timedelta(minutes=8)) == []  # leased
    assert fw.enqueue_due_forwards(created + timedelta(minutes=13)) == [fid]  # lease expired


class Boom(BaseException):
    pass


class CrashLimiter:
    def wait(self):
        raise Boom


def test_double_delivery_sends_once(pending):
    fid, _ = pending
    c = FakeClient()
    lim = limiter()[0]
    assert deliver(fid, c, lim) == "sent"
    assert (
        fw.deliver_forward(fid, client_factory=lambda: c, limiter=lim, now=lambda: T0) == "skipped"
    )
    assert len(c.calls) == 1


def test_claimed_row_skipped_until_lease_expires(pending):
    fid, _ = pending
    c = FakeClient()
    with pytest.raises(Boom):  # crash after the claim, before the send
        fw.deliver_forward(fid, client_factory=lambda: c, limiter=CrashLimiter(), now=lambda: T0)
    assert row(fid).attempts == 1 and row(fid).status == "pending"
    soon = T0 + timedelta(minutes=1)
    assert (
        fw.deliver_forward(fid, client_factory=lambda: c, limiter=limiter()[0], now=lambda: soon)
        == "skipped"
    )
    later = T0 + fw.CLAIM_LEASE + timedelta(seconds=1)
    assert (
        fw.deliver_forward(fid, client_factory=lambda: c, limiter=limiter()[0], now=lambda: later)
        == "sent"
    )
    assert len(c.calls) == 1


def test_concurrent_claim_single_winner(pending):
    import threading

    fid, _ = pending
    wins = []
    barrier = threading.Barrier(4)

    def go():
        barrier.wait()
        wins.append(fw._claim(fid, T0))

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert wins.count(True) == 1


def test_consumer_single_instance(tmp_path):
    started = []

    class FakeConsumer:
        def __init__(self, huey, **kw):
            started.append(kw)

        def run(self):
            # while running, a second consumer must refuse to start
            with pytest.raises(ForwardError, match="already running"):
                fw.run_forward_consumer(tmp_path, FakeConsumer)

    fw.run_forward_consumer(tmp_path, FakeConsumer)
    assert started == [{"workers": 1, "worker_type": "thread"}]
    fw.run_forward_consumer(tmp_path, FakeConsumer)  # lock released afterwards


def primed_limiter(on_sleep):
    c = Clock()

    def sleeper(d):
        on_sleep()
        c.t += d

    lim = fw.RateLimiter(6, c, sleeper, FixedRng())
    lim.wait()  # the next wait() sleeps
    return lim


def set_status(fid, status):
    with dbs.get_session() as s2:
        s2.get(Forward, fid).status = status


def test_disabled_mid_queue_cancels(pending):
    fid, _ = pending
    c = FakeClient()

    def off():
        with dbs.get_session() as s2:
            s2.merge(Setting(key="forward.enabled", value=False))

    assert deliver(fid, c, primed_limiter(off)) == "cancelled"
    r = row(fid)
    assert r.status == "cancelled" and "disabled" in r.last_error and not c.calls


def test_destination_became_watched_cancels(pending):
    fid, _ = pending

    def watch_it():
        with dbs.get_session() as s2:
            s2.add(Group(chat_id="97250000001@c.us", name="x", slug="x", watched=True))

    c = FakeClient()
    assert deliver(fid, c, primed_limiter(watch_it)) == "cancelled"
    assert "watched" in row(fid).last_error and not c.calls


def test_cancelled_during_limiter_sleep_not_sent(pending):
    fid, _ = pending
    c = FakeClient()
    assert deliver(fid, c, primed_limiter(lambda: set_status(fid, "cancelled"))) == "skipped"
    assert row(fid).status == "cancelled" and not c.calls


def test_file_outside_photos_dir_fails(pending, tmp_path, monkeypatch):
    fid, _ = pending
    monkeypatch.setattr(fw, "_photos_dir", lambda: tmp_path / "other")
    c = FakeClient()
    assert deliver(fid, c, limiter()[0]) == "failed"
    assert "outside" in row(fid).last_error and not c.calls


def test_oversize_file_fails(pending, monkeypatch):
    fid, _ = pending
    monkeypatch.setattr(fw, "MAX_FILE_BYTES", 3)
    c = FakeClient()
    assert deliver(fid, c, limiter()[0]) == "failed"
    assert "too large" in row(fid).last_error and not c.calls


def test_cancelled_row_not_revived(s):
    g, k1, _, img = seed(s)
    cfg(s, enabled=True, default_chat_id="d@c.us")
    (f,) = fw.plan_forwards(s, img, [k1], {}, g)
    fw.cancel_forwards(s, img.id, k1.id)
    (again,) = fw.plan_forwards(s, img, [k1], {}, g)
    assert again.id == f.id and again.status == "cancelled"


def test_bidi_controls_stripped():
    assert fw.render_caption("{kids}", ["a\u202eb\u2066c\u2069"], "g", "s", SENT) == "abc"


def test_destination_changed_cancels(pending):
    fid, _ = pending

    def move():
        with dbs.get_session() as s2:
            s2.merge(Setting(key="forward.default_chat_id", value="97250000002@c.us"))

    c = FakeClient()
    assert deliver(fid, c, primed_limiter(move)) == "cancelled"
    r = row(fid)
    assert r.status == "cancelled" and r.last_error == "destination changed" and not c.calls


def test_destination_still_configured_as_per_kid_override_sends(pending):
    fid, _ = pending

    def move_default_keep_override():
        with dbs.get_session() as s2:
            s2.merge(Setting(key="forward.default_chat_id", value="97250000002@c.us"))
            s2.merge(Setting(key="forward.per_kid", value={"99": "97250000001@c.us"}))

    c = FakeClient()
    assert deliver(fid, c, primed_limiter(move_default_keep_override)) == "sent"


def test_caption_uses_configured_timezone(s, monkeypatch):
    from kino.config import get_settings

    monkeypatch.setenv("KINO_TIMEZONE", "Asia/Jerusalem")
    get_settings.cache_clear()
    try:
        g, k1, _, img = seed(s)
        img.sent_at = datetime(2026, 3, 31, 23, 30)  # naive UTC -> 02:30 on 1 April (IDT)
        cfg(s, enabled=True, default_chat_id="d@c.us", caption_template="{date} {time}")
        (f,) = fw.plan_forwards(s, img, [k1], {}, g)
        assert f.caption == "2026-04-01 02:30"
    finally:
        get_settings.cache_clear()


def test_caption_cap_counts_utf16_units():
    out = fw.render_caption("📸" * 600, [], "", "", SENT)  # each emoji is 2 UTF-16 units
    assert len(out.encode("utf-16-le")) // 2 <= 1024 and out == "📸" * 512


def test_default_client_reads_wizard_config(tmp_path, monkeypatch):
    from kino.config import Settings
    from kino.openwa.client import OpenWAClient
    from kino.services.openwa_cfg import save_openwa_config

    for k in ("KINO_OPENWA_URL", "KINO_OPENWA_API_KEY", "KINO_OPENWA_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 'fw.db'}")
    dbs.run_migrations()
    st = Settings(data_dir=tmp_path)
    monkeypatch.setattr(fw, "get_settings", lambda: st)
    try:
        with pytest.raises(ForwardError, match="not configured"):
            fw._default_client()
        with dbs.get_session() as s:
            save_openwa_config(
                s, st, url="http://wizard-owa:2785", api_key="wiz-key", session_id="wiz-sess"
            )
        client = fw._default_client()
        assert isinstance(client, OpenWAClient)
        assert client._http.base_url.host == "wizard-owa" and client._session == "wiz-sess"
    finally:
        eng.dispose()


def test_default_client_sends_with_the_outgoing_session(s, tmp_path, monkeypatch):
    from kino.config import Settings
    from kino.services.openwa_cfg import save_openwa_config, save_outgoing_session

    for k in ("KINO_OPENWA_URL", "KINO_OPENWA_API_KEY", "KINO_OPENWA_SESSION_ID"):
        monkeypatch.delenv(k, raising=False)
    st = Settings(data_dir=tmp_path)
    monkeypatch.setattr(fw, "get_settings", lambda: st)
    save_openwa_config(s, st, url="http://owa", api_key="k" * 10, session_id="in-sess")
    s.commit()
    assert "/in-sess/" in fw._default_client()._p("messages/send-image")  # unchanged
    save_outgoing_session(s, "out-sess", "Spare")
    s.commit()
    path = fw._default_client()._p("messages/send-image")
    assert "/out-sess/" in path and "in-sess" not in path
