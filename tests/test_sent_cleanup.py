import pytest

from kino.config import get_settings
from kino.db import session as dbs
from kino.db.models import Forward, Group, Image, Kid, SavedPhoto, Setting
from kino.delivery.base import DeliveryError
from kino.services import photos as svc
from kino.services.sent_cleanup import delete_sent_messages

TOKEN = "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZ012345"


class Chan:
    def __init__(self, exc=None):
        self.exc, self.deleted, self.closed = exc, [], False

    async def delete(self, dest, ref):
        if self.exc:
            raise self.exc
        self.deleted.append((dest, ref.id))

    async def aclose(self):
        self.closed = True


@pytest.fixture
def s(tmp_path, monkeypatch):
    monkeypatch.setenv("KINO_DATA_DIR", str(tmp_path))
    get_settings.cache_clear()
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 't.db'}")
    dbs.run_migrations()
    with dbs.get_session() as sess:
        yield sess
    eng.dispose()
    get_settings.cache_clear()


def setup(s, *, on=True, channel="telegram", kids=("a",)):
    g = Group(chat_id="1@g.us", name="G", slug="g", watched=True)
    ks = {n: Kid(display_name=n, slug=n) for n in ("a", "b")}
    img = Image(message_id="m", status="done")
    s.add_all([g, *ks.values(), img])
    s.flush()
    for n in ("a", "b"):
        s.add(SavedPhoto(image_id=img.id, kid_id=ks[n].id, path=f"/p/{n}.jpg"))
    s.add(
        Forward(
            image_id=img.id,
            channel=channel,
            dest_chat_id="-100",
            kid_ids=[ks[n].id for n in kids],
            status="sent",
            sent_ref="42",
        )
    )
    s.merge(Setting(key="telegram.delete_on_remove", value=on))
    s.flush()
    sp = s.query(SavedPhoto).filter_by(kid_id=ks["a"].id).one()
    return sp


def test_enabled_deletes(s):
    out = svc.remove_photo(s, setup(s).id)
    ch = Chan()
    assert delete_sent_messages(out.sent_deletions, lambda: ch) == 1
    assert ch.deleted == [("-100", "42")] and ch.closed


def test_disabled_not_deleted(s):
    assert svc.remove_photo(s, setup(s, on=False).id).sent_deletions == []


def test_sibling_on_forward_kept(s):
    assert svc.remove_photo(s, setup(s, kids=("a", "b")).id).sent_deletions == []


def test_whatsapp_never_deleted(s):
    assert svc.remove_photo(s, setup(s, channel="whatsapp").id).sent_deletions == []


def test_delete_failure_swallowed(s):
    out = svc.remove_photo(s, setup(s).id)
    assert len(out.sent_deletions) == 1
    ch = Chan(DeliveryError(f"bot{TOKEN} message can't be deleted"))
    assert delete_sent_messages(out.sent_deletions, lambda: ch) == 0 and ch.closed
    assert s.query(SavedPhoto).filter_by(id=out.saved_photo_id).count() == 0


def test_factory_failure_swallowed():
    def boom():
        raise DeliveryError("no token", permanent=True)

    from kino.services.sent_cleanup import SentDeletion

    assert delete_sent_messages([SentDeletion("-1", "1")], boom) == 0


def _items(n):
    from kino.services.sent_cleanup import SentDeletion

    return [SentDeletion("-1", str(i)) for i in range(n)]


class Sleeps:
    def __init__(self):
        self.calls = []

    async def __call__(self, secs):
        self.calls.append(secs)


def test_pacing_between_deletes_only():
    ch, sl = Chan(), Sleeps()
    assert delete_sent_messages(_items(3), lambda: ch, pace=0.1, sleeper=sl) == 3
    assert sl.calls == [0.1, 0.1]


def test_retry_after_honoured_once_then_succeeds():
    class Flaky(Chan):
        n = 0

        async def delete(self, dest, ref):
            self.n += 1
            if self.n == 1:
                raise DeliveryError("flood", retry_after=500)
            await super().delete(dest, ref)

    ch, sl = Flaky(), Sleeps()
    assert delete_sent_messages(_items(1), lambda: ch, pace=0, sleeper=sl) == 1
    assert sl.calls == [30.0] and ch.n == 2


def test_retry_after_second_failure_swallowed():
    ch, sl = Chan(DeliveryError("flood", retry_after=2)), Sleeps()
    assert delete_sent_messages(_items(1), lambda: ch, pace=0, sleeper=sl) == 0
    assert sl.calls == [2] and ch.closed


def test_total_work_capped():
    ch = Chan()
    assert delete_sent_messages(_items(5), lambda: ch, pace=0, max_items=2) == 2
    assert len(ch.deleted) == 2


def test_collect_filters_in_sql(s):
    from sqlalchemy import event

    from kino.services.sent_cleanup import collect_sent_deletions

    sp = setup(s)
    stmts = []
    event.listen(s.get_bind(), "before_cursor_execute", lambda *a: stmts.append(a[2]))
    assert len(collect_sent_deletions(s, sp.kid_id, sp.image_id)) == 1
    sel = next(x for x in stmts if "FROM forwards" in x)
    assert "channel" in sel and "sent_ref IS NOT NULL" in sel and "image_id" in sel
