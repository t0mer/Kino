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
    assert len(fw.render_caption("{kids}", ["x" * 5000] * 50, "g", "s", SENT)) <= 1000
    assert fw.render_caption("x" * 5000, [], "", "", SENT) == "x" * 1000


def test_cancel_only_pending(s):
    g, k1, _, img = seed(s)
    cfg(s, enabled=True, default_chat_id="d@c.us")
    (f,) = fw.plan_forwards(s, img, [k1], {}, g)
    assert fw.forward_blocks_raw_cleanup(s, img.id)
    assert fw.cancel_forwards(s, img.id, k1.id + 99) == 0
    assert fw.cancel_forwards(s, img.id, k1.id) == 1
    assert f.status == "cancelled"
    assert not fw.forward_blocks_raw_cleanup(s, img.id)
    f.status = "sent"
    assert fw.cancel_forwards(s, img.id, k1.id) == 0
    assert f.status == "sent"
