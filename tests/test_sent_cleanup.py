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
