from datetime import datetime, timedelta

import numpy as np
import pytest

from kino.db import models, session
from kino.db.session import embedding_to_blob
from kino.pipeline.matcher import Matcher

NOW = datetime(2026, 10, 10)
DIM = 8


def unit(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def mix(a: np.ndarray, b: np.ndarray, cos: float) -> np.ndarray:
    """Unit vector with cosine `cos` to a (b orthogonal to a)."""
    return (cos * a + np.sqrt(1 - cos**2) * b).astype(np.float32)


@pytest.fixture
def db(tmp_path):
    eng = session.init_engine(f"sqlite:///{tmp_path / 'kino.db'}")
    models.Base.metadata.create_all(eng)
    yield
    eng.dispose()


def add_kid(s, slug="a", **kw):
    k = models.Kid(display_name=slug, slug=slug, **kw)
    s.add(k)
    s.flush()
    return k


def add_ref(s, kid, emb, quality=1.0, date=NOW, status="active"):
    s.add(
        models.ReferenceFace(
            kid_id=kid.id,
            embedding=embedding_to_blob(emb),
            source="upload",
            quality=quality,
            photo_date=date,
            status=status,
        )
    )


def make(s) -> Matcher:
    m = Matcher(now=lambda: NOW)
    m.reload(s)
    return m


def test_identical_scores_one_orthogonal_zero(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, unit(0))
        m = make(s)
    assert m.score(unit(0), None)[0].score == pytest.approx(1.0)
    assert m.score(unit(1), None)[0].score == pytest.approx(0.0)


def test_topk_weighting_k_is_min3(db):
    with session.get_session() as s:
        k = add_kid(s)
        for c in (0.9, 0.8, 0.7, 0.1):
            add_ref(s, k, mix(unit(0), unit(1), c))
        m = make(s)
    # equal weights; top-3 mean of 0.9,0.8,0.7 -> 0.8 (0.1 ignored)
    assert m.score(unit(0), None)[0].score == pytest.approx(0.8, abs=1e-5)


def test_topk_with_two_refs_uses_both(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, mix(unit(0), unit(1), 0.9))
        add_ref(s, k, mix(unit(0), unit(1), 0.5))
        m = make(s)
    assert m.score(unit(0), None)[0].score == pytest.approx(0.7, abs=1e-5)


def test_old_reference_weighs_less(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, mix(unit(0), unit(1), 1.0))  # recent, cos 1
        add_ref(s, k, mix(unit(0), unit(1), 0.0), date=NOW - timedelta(days=365))
        m = make(s)
    # weights 1 and 0.5**1 -> (1*1 + 0.5*0)/1.5
    assert m.score(unit(0), None)[0].score == pytest.approx(1 / 1.5, abs=1e-5)


def test_recency_floor(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, unit(0))
        add_ref(s, k, unit(1), date=NOW - timedelta(days=20 * 365))
        m = make(s)
    # 0.5**20 floored to 0.2 -> (1 + 0.2*0)/1.2 (a 3y ref is also at the floor: 0.125 -> 0.2)
    assert m.score(unit(0), None)[0].score == pytest.approx(1 / 1.2, abs=1e-5)


def test_negative_at_or_above_score_subtracts(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, mix(unit(0), unit(1), 0.6))
        s.add(models.NegativeFace(kid_id=k.id, embedding=embedding_to_blob(unit(0))))
        m = make(s)
    assert m.score(unit(0), None)[0].score == pytest.approx(0.5, abs=1e-5)


def test_negative_below_score_does_nothing(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, unit(0))
        s.add(models.NegativeFace(kid_id=k.id, embedding=embedding_to_blob(unit(1))))
        m = make(s)
    assert m.score(unit(0), None)[0].score == pytest.approx(1.0)


def test_sorted_and_margin(db):
    with session.get_session() as s:
        a = add_kid(s, "a")
        b = add_kid(s, "b")
        add_ref(s, a, mix(unit(0), unit(1), 0.5))
        add_ref(s, b, mix(unit(0), unit(1), 0.8))
        m = make(s)
    r = m.score(unit(0), None)
    assert [x.kid_id for x in r] == [b.id, a.id]
    assert r[0].margin == pytest.approx(0.3, abs=1e-5)


def test_empty_gallery_and_kid_without_refs(db):
    with session.get_session() as s:
        add_kid(s)
        m = make(s)
    assert m.score(unit(0), None) == []


def test_retired_ignored(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, unit(0), status="retired")
        m = make(s)
    assert m.score(unit(0), None) == []


def test_zero_vector_no_nan(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, unit(0), quality=0.0)  # zero weight must not NaN either
        m = make(s)
    r = m.score(np.zeros(DIM, dtype=np.float32), None)
    assert np.isfinite(r[0].score) and np.isfinite(r[0].margin)
    assert m.score(unit(0), None) == m.score(unit(0), None)


def test_maybe_reload_only_on_version_change(db):
    with session.get_session() as s:
        k = add_kid(s)
        add_ref(s, k, unit(0))
        m = Matcher(now=lambda: NOW)
        calls = []
        orig = m._load_embeddings
        m._load_embeddings = lambda sess: (calls.append(1), orig(sess))[1]  # type: ignore[method-assign]
        assert m.maybe_reload(s) is True  # first load
        assert m.maybe_reload(s) is False
        assert len(calls) == 1
        s.add(models.Setting(key="gallery_version", value=1))
        s.flush()
        assert m.maybe_reload(s) is True
        assert m.maybe_reload(s) is False
        s.get(models.Setting, "gallery_version").value = 2  # type: ignore[union-attr]
        s.flush()
        add_ref(s, k, unit(1))
        s.flush()
        assert m.maybe_reload(s) is True
        assert len(calls) == 3
    assert len(m.score(unit(1), None)) == 1


def test_kid_settings_live_without_version_bump(db):
    with session.get_session() as s:
        k = add_kid(s, auto_save_enabled=True)
        g = models.Group(chat_id="g@g.us")
        s.add(g)
        s.flush()
        add_ref(s, k, unit(0))
        m = Matcher(now=lambda: NOW)
        calls = []
        orig = m._load_embeddings
        m._load_embeddings = lambda sess: (calls.append(1), orig(sess))[1]  # type: ignore[method-assign]
        m.maybe_reload(s)
        assert m.kid(k.id).auto_save_enabled and not m.group_mapped(k.id, g.id)
        k.auto_save_enabled = False
        k.threshold_offset = 0.07
        s.add(models.KidGroup(kid_id=k.id, group_id=g.id))
        s.flush()
        assert m.maybe_reload(s) is False  # embeddings not rebuilt
        assert len(calls) == 1
        assert m.kid(k.id).auto_save_enabled is False
        assert m.kid(k.id).threshold_offset == 0.07
        assert m.group_mapped(k.id, g.id)


def test_inactive_kid_not_scored(db):
    with session.get_session() as s:
        a = add_kid(s, "a")
        b = add_kid(s, "b", active=False)
        add_ref(s, a, unit(0))
        add_ref(s, b, unit(0))
        m = make(s)
        assert [x.kid_id for x in m.score(unit(0), None)] == [a.id]
        a.active = False
        s.flush()
        m.maybe_reload(s)
        assert m.score(unit(0), None) == []


def test_default_now_is_naive_utc():
    assert Matcher()._now().tzinfo is None


def test_kid_info_and_mapping(db):
    with session.get_session() as s:
        k = add_kid(s, auto_save_enabled=True, threshold_offset=0.02)
        g = models.Group(chat_id="g@g.us")
        s.add(g)
        s.flush()
        s.add(models.KidGroup(kid_id=k.id, group_id=g.id))
        add_ref(s, k, unit(0))
        m = make(s)
    info = m.kid(k.id)
    assert info.auto_save_enabled and info.threshold_offset == 0.02
    assert m.group_mapped(k.id, g.id) is True
    assert m.group_mapped(k.id, 999) is False
    assert m.group_mapped(k.id, None) is False
