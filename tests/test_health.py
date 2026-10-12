"""Gallery health: planted outliers are flagged, persisted and exposed."""

from __future__ import annotations

import numpy as np
import pytest

from kino import health
from kino.db import session as dbs
from kino.db.models import Kid, ReferenceFace
from kino.db.session import embedding_to_blob

DIM = 64
rng = np.random.default_rng(7)


def unit(v):
    return (v / np.linalg.norm(v)).astype(np.float32)


def near(center, noise=0.05):
    return unit(center + rng.normal(0, noise, DIM))


def add_refs(s, kid_id, vecs, start):
    for i, v in enumerate(vecs):
        s.add(
            ReferenceFace(
                id=start + i, kid_id=kid_id, embedding=embedding_to_blob(v), source="upload"
            )
        )


@pytest.fixture
def db(tmp_path):
    eng = dbs.init_engine(f"sqlite:///{tmp_path / 'h.db'}")
    dbs.run_migrations()
    with dbs.get_session() as s:
        s.add_all(
            [Kid(id=1, display_name="Noa", slug="noa"), Kid(id=2, display_name="Itai", slug="i")]
        )
    yield
    eng.dispose()


def seeded():
    a, b = unit(rng.normal(size=DIM)), unit(rng.normal(size=DIM))
    with dbs.get_session() as s:
        add_refs(s, 1, [near(a) for _ in range(9)], 1)
        add_refs(s, 1, [unit(rng.normal(size=DIM))], 10)  # random: far from Noa's centroid
        add_refs(s, 2, [near(b) for _ in range(8)], 20)
        add_refs(s, 1, [near(b)], 30)  # Itai's face filed under Noa
    return a, b


def test_flags_planted_outliers(db):
    seeded()
    with dbs.get_session() as s:
        h = health.evaluate_kid(s, 1)
    assert h.evaluated and h.active_refs == 11
    flagged = {o.reference_id: o.reason for o in h.outliers}
    assert flagged[30] == health.OTHER
    assert 10 in flagged  # far or closer to another kid
    assert not set(flagged) & set(range(1, 10))


def test_too_few_refs_flags_nothing(db):
    with dbs.get_session() as s:
        add_refs(s, 1, [unit(rng.normal(size=DIM)) for _ in range(4)], 1)
        h = health.evaluate_kid(s, 1)
    assert not h.evaluated and h.outliers == []


def test_nightly_persists_flags_and_clears_them(db):
    seeded()
    counts = health.run_gallery_health()
    assert counts[1] >= 1 and counts[2] == 0
    with dbs.get_session() as s:
        assert s.get(ReferenceFace, 30).outlier
        assert s.get(ReferenceFace, 30).outlier_reason == health.OTHER
        assert not s.get(ReferenceFace, 1).outlier
        s.get(ReferenceFace, 30).status = "retired"
    health.run_gallery_health()
    with dbs.get_session() as s:
        assert not s.get(ReferenceFace, 30).outlier


def test_endpoint_and_reference_fields(api):
    a = unit(rng.normal(size=DIM))
    with dbs.get_session() as s:
        s.add(Kid(id=1, display_name="Noa", slug="noa"))
        s.flush()
        add_refs(s, 1, [near(a) for _ in range(7)], 1)
        add_refs(s, 1, [unit(-a + rng.normal(0, 0.05, DIM))], 8)
    r = api.get("/api/kids/1/health")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["evaluated"] and [o["reference_id"] for o in body["outliers"]] == [8]
    assert body["outliers"][0]["reason"] == health.FAR
    assert api.get("/api/kids/99/health").status_code == 404
    health.run_gallery_health()
    refs = {x["id"]: x for x in api.get("/api/kids/1/references").json()}
    assert refs[8]["outlier"] is True and refs[8]["outlier_reason"] == health.FAR
    assert refs[1]["outlier"] is False and refs[1]["outlier_reason"] is None
