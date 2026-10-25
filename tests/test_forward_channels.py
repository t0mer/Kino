"""Channel-aware forward planning and delivery (WhatsApp + Telegram)."""

from datetime import datetime, timedelta

import pytest

from kino import forward as fw
from kino.db import session as dbs
from kino.db.models import Forward, Group, Image, Kid, Setting, TelegramChat
from kino.delivery.base import DeliveryError, SentRef
from kino.forward import ForwardError
from kino.services import InvalidError
from kino.services.settings_svc import update_forward

SENT = datetime(2026, 3, 7, 14, 5)
T0 = datetime(2026, 3, 7, 12, 0)
TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


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
    k2 = Kid(display_name="Itai", slug="itai")
    img = Image(message_id="m1", sent_at=SENT, sender_name="Dana", status="done")
    s.add_all([g, k1, k2, img])
    s.flush()
    img.group_id = g.id
    return g, k1, k2, img


def cfg(s, **kw):
    for k, v in kw.items():
        s.merge(Setting(key="forward." + k, value=v))
    s.flush()


def test_destination_for_and_old_config_defaults(s):
    g, k1, k2, img = seed(s)
    cfg(s, enabled=True, default_chat_id="d@c.us", per_kid={str(k2.id): "-100"})
    c = fw.get_forward_config(s)
    assert c.default_channel == "whatsapp" and c.per_kid_channel == {}
    assert c.destination_for(k1.id) == ("whatsapp", "d@c.us")
    assert c.destination_for(k2.id) == ("whatsapp", "-100")
    (a, b) = fw.plan_forwards(s, img, [k1, k2], {}, g)
    assert a.channel == b.channel == "whatsapp"


def test_plan_splits_channels(s):
    g, k1, k2, img = seed(s)
    cfg(
        s,
        enabled=True,
        default_chat_id="d@c.us",
        per_kid={str(k2.id): "-100"},
        per_kid_channel={str(k2.id): "telegram"},
    )
    rows = fw.plan_forwards(s, img, [k1, k2], {}, g)
    assert {(r.channel, r.dest_chat_id) for r in rows} == {
        ("whatsapp", "d@c.us"),
        ("telegram", "-100"),
    }
    again = fw.plan_forwards(s, img, [k1, k2], {}, g)
    assert {r.id for r in again} == {r.id for r in rows}


def test_same_chat_id_on_two_channels_is_two_rows(s):
    g, k1, k2, img = seed(s)
    cfg(
        s,
        enabled=True,
        default_chat_id="55",
        per_kid={str(k2.id): "55"},
        per_kid_channel={str(k2.id): "telegram"},
    )
    assert len(fw.plan_forwards(s, img, [k1, k2], {}, g)) == 2


def test_watched_guard_whatsapp_only(s):
    g, k1, _, img = seed(s)
    with pytest.raises(ForwardError):
        fw.validate_destination(g.chat_id, s)
    fw.validate_destination(g.chat_id, s, "telegram")
    cfg(s, enabled=True, default_chat_id=g.chat_id)
    assert fw.plan_forwards(s, img, [k1], {}, g) == []
    cfg(s, default_channel="telegram")
    assert len(fw.plan_forwards(s, img, [k1], {}, g)) == 1


def test_set_default_destination_forces_whatsapp(s):
    cfg(s, default_channel="telegram")
    fw.set_default_destination(s, "d@c.us")
    assert fw.get_forward_config(s).default_channel == "whatsapp"


def test_update_forward_validates_telegram(s, monkeypatch):
    monkeypatch.delenv("KINO_TELEGRAM_BOT_TOKEN", raising=False)
    from kino.config import get_settings

    get_settings.cache_clear()
    kw = dict(
        enabled=True,
        default_chat_id="-100",
        per_kid={},
        caption_template="x",
        include_review_confirmed=True,
    )
    with pytest.raises(InvalidError):
        update_forward(s, default_channel="bogus", **kw)
    with pytest.raises(InvalidError, match="token"):
        update_forward(s, default_channel="telegram", **kw)
    monkeypatch.setenv("KINO_TELEGRAM_BOT_TOKEN", TOKEN)
    get_settings.cache_clear()
    with pytest.raises(InvalidError, match="not linked"):
        update_forward(s, default_channel="telegram", **kw)
    s.add(TelegramChat(chat_id="-100", title="T", type="group"))
    s.flush()
    out = update_forward(s, default_channel="telegram", **kw)
    assert out.default_channel == "telegram"
    get_settings.cache_clear()


# --- delivery ---


class FakeTelegram:
    def __init__(self, exc=None):
        self.exc, self.calls = exc, []

    async def send_photo(self, dest, data, mimetype, caption):
        self.calls.append(dest)
        if self.exc:
            raise self.exc
        return SentRef("42")

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


class WA:
    async def send_image(self, chat_id, data, mimetype, caption):
        return "WAID"

    async def aclose(self):
        pass


@pytest.fixture
def tg(s, tmp_path, monkeypatch):
    monkeypatch.setattr(fw, "_photos_dir", lambda: tmp_path)
    g, k1, k2, img = seed(s)
    f = tmp_path / "p.jpg"
    f.write_bytes(b"\xff\xd8jpegdata")
    cfg(
        s,
        enabled=True,
        default_chat_id="d@c.us",
        per_kid={str(k2.id): "-100"},
        per_kid_channel={str(k2.id): "telegram"},
    )
    rows = fw.plan_forwards(s, img, [k1, k2], {k1.id: f, k2.id: f}, g)
    s.commit()
    return {r.channel: r.id for r in rows}


def row(fid):
    with dbs.get_session() as s2:
        return s2.get(Forward, fid)


def lim():
    c = Clock()
    return fw.RateLimiter(6, c, c.sleep, FixedRng())


def run(fid, chan, exc=None, at=T0):
    ch = FakeTelegram(exc)
    return (
        fw.deliver_forward(fid, limiter=lim(), now=lambda: at, channel_factory=lambda n: ch),
        ch,
    )


def test_telegram_stores_sent_ref_only(tg):
    res, ch = run(tg["telegram"], "telegram")
    assert res == "sent" and ch.calls == ["-100"]
    r = row(tg["telegram"])
    assert r.sent_ref == "42" and r.openwa_message_id is None


def test_whatsapp_stores_both(tg):
    wa = WA()
    assert (
        fw.deliver_forward(tg["whatsapp"], client_factory=lambda: wa, limiter=lim(), now=lambda: T0)
        == "sent"
    )
    r = row(tg["whatsapp"])
    assert r.sent_ref == r.openwa_message_id == "WAID"


def test_permanent_error_fails_without_retry(tg):
    res, _ = run(tg["telegram"], "telegram", DeliveryError("chat not found", permanent=True))
    r = row(tg["telegram"])
    assert res == "failed" and r.status == "failed" and r.next_attempt_at is None
    assert r.last_error == "DeliveryError: chat not found"


def test_retry_after_does_not_burn_attempt(tg):
    res, _ = run(tg["telegram"], "telegram", DeliveryError("slow down", retry_after=30))
    r = row(tg["telegram"])
    assert res == "retry" and r.status == "pending"
    assert r.attempts == 0 and r.next_attempt_at == T0 + timedelta(seconds=30)


def test_huge_retry_after_is_normal_failure(tg):
    res, _ = run(tg["telegram"], "telegram", DeliveryError("x", retry_after=99999))
    r = row(tg["telegram"])
    assert res == "retry" and r.attempts == 1
    assert r.next_attempt_at == T0 + timedelta(seconds=60)


def test_token_never_in_last_error(tg):
    exc = RuntimeError(f"https://api.telegram.org/bot{TOKEN}/sendPhoto failed")
    run(tg["telegram"], "telegram", exc)
    err = row(tg["telegram"]).last_error
    assert TOKEN not in err and TOKEN.split(":")[1] not in err


def test_telegram_destination_removed_cancels(tg, s):
    cfg(s, default_chat_id="d@c.us", per_kid={})
    s.commit()
    res, ch = run(tg["telegram"], "telegram")
    assert res == "cancelled" and ch.calls == []


def test_per_channel_limiters(tg, monkeypatch):
    wc, tc = Clock(), Clock()
    monkeypatch.setattr(
        fw,
        "_limiters",
        {
            "whatsapp": fw.RateLimiter(6, wc, wc.sleep, FixedRng()),
            "telegram": fw.RateLimiter(20, tc, tc.sleep, FixedRng(), jitter=(0.0, 0.0)),
        },
    )
    wa, ch = WA(), FakeTelegram()
    for _ in range(2):  # second round waits per channel
        for name in ("whatsapp", "telegram"):
            with dbs.get_session() as s2:
                f = s2.get(Forward, tg[name])
                f.status, f.next_attempt_at = "pending", None
            fw.deliver_forward(
                tg[name],
                client_factory=lambda: wa,
                now=lambda: T0,
                channel_factory=lambda n: wa if n == "whatsapp" else ch,
            )
    assert wc.sleeps == [12.0]  # 60/6 + 2 s jitter
    assert tc.sleeps == [3.0]  # 60/20, no jitter
