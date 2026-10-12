"""Clustering compute and the build step over synthetic embeddings (CLAUDE.md §10)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
from sqlalchemy import select

from kino import clustering
from kino.db import session as dbs
from kino.db.models import Cluster, Face, Image, Kid, Match
from kino.services import clusters as svc

DIM = 512
NOW = datetime.now(UTC).replace(tzinfo=None)


def identity(seed: int, n: int, spread: float = 0.5) -> np.ndarray:
    rng = np.random.default_rng(seed)
    center = rng.normal(size=DIM)
    center /= np.linalg.norm(center)
    pts = center + spread * rng.normal(size=(n, DIM)) / np.sqrt(DIM)
    return (pts / np.linalg.norm(pts, axis=1, keepdims=True)).astype(np.float32)


def noise(seed: int, n: int) -> np.ndarray:
    v = np.random.default_rng(seed).normal(size=(n, DIM))
    return (v / np.linalg.norm(v, axis=1, keepdims=True)).astype(np.float32)


def test_two_identities_and_noise() -> None:
    x = np.vstack([identity(1, 8), identity(2, 6), noise(4, 4)])
    labels = clustering.cluster_embeddings(x)
    assert len(set(labels[:8])) == 1 and labels[0] != -1
    assert len(set(labels[8:14])) == 1 and labels[8] != -1
    assert labels[0] != labels[8]
    assert (labels[14:] == -1).all()


def test_too_few_points_is_all_noise() -> None:
    assert (clustering.cluster_embeddings(identity(1, 3)) == -1).all()
    assert clustering.cluster_embeddings(np.zeros((0, DIM), dtype=np.float32)).size == 0


_msg = iter(range(100_000))


def add_face(
    emb: np.ndarray,
    size: float = 100.0,
    age_days: float = 1.0,
    quality: float = 0.9,
    crop: str | None = None,
) -> int:
    with dbs.get_session() as s:
        img = Image(message_id=f"m{next(_msg)}", status="done")
        s.add(img)
        s.flush()
        f = Face(
            image_id=img.id,
            bbox_x=0,
            bbox_y=0,
            bbox_w=size,
            bbox_h=size,
            det_score=0.9,
            size_px=size,
            blur_score=500.0,
            yaw=0.0,
            pitch=0.0,
            quality=quality,
            embedding=dbs.embedding_to_blob(emb),
            crop_path=crop,
            created_at=NOW - timedelta(days=age_days),
        )
        s.add(f)
        s.flush()
        return f.id


def add_many(x: np.ndarray, **kw: float) -> list[int]:
    return [add_face(row, **kw) for row in x]


def cluster_of(fid: int) -> int | None:
    with dbs.get_session() as s:
        f = s.get(Face, fid)
        assert f is not None
        return f.cluster_id


def test_build_excludes_matched_small_old_and_is_idempotent(api) -> None:  # type: ignore[no-untyped-def]
    a = add_many(identity(1, 6))
    b = add_many(identity(2, 5))
    small = add_many(identity(3, 6), size=30.0)  # tier S
    old = add_many(identity(4, 6), age_days=90)
    with dbs.get_session() as s:
        k = Kid(display_name="K", slug="k")
        s.add(k)
        s.flush()
        s.add(Match(face_id=a[0], kid_id=k.id, score=0.9, method="face", decision="review"))
    with dbs.get_session() as s:
        res = svc.build_clusters(s, NOW)
    assert res.clusters == 2
    assert cluster_of(a[0]) is None  # matched to a kid
    assert len({cluster_of(f) for f in a[1:]}) == 1 and cluster_of(a[1]) is not None
    assert len({cluster_of(f) for f in b}) == 1
    assert all(cluster_of(f) is None for f in small + old)
    before = {f: cluster_of(f) is not None for f in a + b}
    with dbs.get_session() as s:
        svc.build_clusters(s, NOW)
        n = len(list(s.scalars(select(Cluster))))
    assert n == 2
    assert {f: cluster_of(f) is not None for f in a + b} == before


def test_labeled_untouched_and_unlabeled_rebuilt(api) -> None:  # type: ignore[no-untyped-def]
    a = add_many(identity(1, 6))
    add_many(identity(7, 6))
    with dbs.get_session() as s:
        svc.build_clusters(s, NOW)
    cid = cluster_of(a[0])
    with dbs.get_session() as s:
        c = s.get(Cluster, cid)
        assert c is not None
        c.label_status = "labeled"
    b = add_many(identity(2, 5))
    with dbs.get_session() as s:
        svc.build_clusters(s, NOW)
        rows = list(s.scalars(select(Cluster)))
    assert {c.label_status for c in rows} == {"labeled", "unlabeled"}
    assert all(cluster_of(f) == cid for f in a)
    assert cluster_of(b[0]) not in (None, cid)


def test_identity_among_strangers_forms_a_cluster() -> None:
    labels = clustering.cluster_embeddings(np.vstack([identity(1, 6), noise(2, 25)]))
    assert len(set(labels[:6])) == 1 and labels[0] == 0
    assert (labels[6:] == -1).all()


def test_pure_noise_forms_nothing() -> None:
    assert (clustering.cluster_embeddings(noise(5, 30)) == -1).all()


def _use(fid: int, kind: str) -> None:
    from kino.db.models import NegativeFace, ReferenceFace

    with dbs.get_session() as s:
        k = Kid(display_name=f"K{kind}{fid}", slug=f"k{kind}{fid}")
        s.add(k)
        s.flush()
        emb = dbs.embedding_to_blob(identity(9, 1)[0])
        if kind == "ref":
            s.add(
                ReferenceFace(kid_id=k.id, face_id=fid, embedding=emb, source="upload", quality=0.9)
            )
        else:
            s.add(NegativeFace(kid_id=k.id, embedding=emb, source_face_id=fid))


def test_build_excludes_reference_and_negative_source_faces(api) -> None:  # type: ignore[no-untyped-def]
    ids = add_many(identity(1, 10))
    add_many(identity(2, 6))  # HDBSCAN needs a second group to split from
    with dbs.get_session() as s:
        svc.build_clusters(s, NOW)
    members = [f for f in ids if cluster_of(f) is not None]
    assert len(members) >= 6
    _use(members[0], "ref")
    _use(members[1], "neg")
    with dbs.get_session() as s:
        res = svc.build_clusters(s, NOW)
    assert res.candidates == 16 - 2
    assert cluster_of(members[0]) is None and cluster_of(members[1]) is None


def test_label_skips_reference_and_negative_source_faces(api) -> None:  # type: ignore[no-untyped-def]
    from kino.config import get_settings

    ids = add_many(identity(1, 10))
    add_many(identity(2, 6))  # HDBSCAN needs a second group to split from
    with dbs.get_session() as s:
        svc.build_clusters(s, NOW)
        k = Kid(display_name="K", slug="kk")
        s.add(k)
        s.flush()
        kid_id = k.id
    members = [f for f in ids if cluster_of(f) is not None]
    cid = cluster_of(members[0])
    assert cid is not None and len(members) >= 6
    # enrolled / rejected after the cluster was built
    _use(members[0], "ref")
    _use(members[1], "neg")
    res = svc.label_cluster(get_settings(), cid, kid_id)
    assert res.skipped == 2 and res.selected == len(members) - 2
    assert cluster_of(members[0]) is None and cluster_of(members[1]) is None
