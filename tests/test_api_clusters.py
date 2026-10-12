"""REST: unknown-face clusters (CLAUDE.md §11.3, §13, §19)."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from sqlalchemy import func, select

from kino.config import get_settings
from kino.db import session as dbs
from kino.db.models import Cluster, Face, Group, Image, Kid, ReferenceFace, Setting
from tests.test_clustering import add_face, add_many, identity


def crop(name: str, base: Path | None = None) -> str:
    p = (base or get_settings().data_dir / "crops") / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"\xff\xd8jpeg")
    return str(p)


def seed_clusters(n_a: int = 8, n_b: int = 6) -> tuple[list[int], list[int]]:
    a = [add_face(e, crop=crop(f"a{i}.jpg")) for i, e in enumerate(identity(1, n_a))]
    b = [add_face(e, crop=crop(f"b{i}.jpg")) for i, e in enumerate(identity(2, n_b))]
    return a, b


def kid(active: bool = True) -> int:
    with dbs.get_session() as s:
        k = Kid(display_name="Noa", slug=f"noa{np.random.randint(10**9)}", active=active)
        s.add(k)
        s.flush()
        return k.id


def run(api) -> None:  # type: ignore[no-untyped-def]
    from kino.services import clusters

    clusters.run_clustering()


def test_unauthenticated_is_401(api) -> None:  # type: ignore[no-untyped-def]
    api.cookies.clear()
    assert api.get("/api/clusters").status_code == 401
    assert api.post("/api/clusters/run").status_code == 401
    assert api.post("/api/clusters/1/ignore").status_code == 401


def test_list_and_detail(api) -> None:  # type: ignore[no-untyped-def]
    a, _b = seed_clusters()
    with dbs.get_session() as s:
        g = Group(chat_id="1@g.us", name="Class 3B", slug="c", watched=True)
        s.add(g)
        s.flush()
        for fid in a:
            f = s.get(Face, fid)
            assert f is not None
            img = s.get(Image, f.image_id)
            assert img is not None
            img.group_id = g.id
    run(api)
    r = api.get("/api/clusters")
    assert r.status_code == 200
    items = r.json()["items"]
    assert [i["size"] for i in items] == [8, 6]
    assert len(items[0]["sample_crop_urls"]) == 6
    assert items[0]["groups"] == ["Class 3B"]
    assert items[0]["date_from"] is not None
    d = api.get(f"/api/clusters/{items[0]['id']}").json()
    assert len(d["faces"]) == 8 and d["faces"][0]["crop_url"].startswith("/api/crops/")
    page = api.get("/api/clusters?limit=1").json()
    assert page["next_cursor"]
    nxt = api.get(f"/api/clusters?limit=1&cursor={page['next_cursor']}").json()
    assert nxt["items"][0]["size"] == 6 and nxt["next_cursor"] is None
    assert api.get("/api/clusters?status=bogus").status_code == 422
    assert api.get("/api/clusters/9999").status_code == 404


def test_run_endpoint_enqueues(api, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from kino import tasks

    called: list[int] = []

    class Job:
        id = "job-1"

    monkeypatch.setattr(tasks, "cluster_unknown_faces_task", lambda: called.append(1) or Job())
    r = api.post("/api/clusters/run")
    assert r.status_code == 202 and r.json() == {"status": "queued", "job_id": "job-1"}
    assert called == [1]


def first_cluster(api, idx: int = 0) -> int:  # type: ignore[no-untyped-def]
    return int(api.get("/api/clusters").json()["items"][idx]["id"])


def test_label_creates_best_references_and_bumps_version(api) -> None:  # type: ignore[no-untyped-def]
    a = [
        add_face(e, quality=0.5 + i * 0.02, crop=crop(f"l{i}.jpg"))
        for i, e in enumerate(identity(1, 20))
    ]
    add_many(identity(2, 6))
    run(api)
    cid = first_cluster(api)
    k = kid()
    with dbs.get_session() as s:
        s.add(Setting(key="gallery_version", value=5))
    r = api.post(f"/api/clusters/{cid}/label", json={"kid_id": k})
    assert r.status_code == 200, r.text
    assert r.json()["references_created"] <= 15 and r.json()["rescore"] is not None
    with dbs.get_session() as s:
        refs = list(s.scalars(select(ReferenceFace).where(ReferenceFace.kid_id == k)))
        assert 1 <= len(refs) <= 15 and {x.source for x in refs} == {"cluster"}
        best = {x.face_id for x in refs}
        top15 = set(sorted(a, key=lambda i: -(s.get(Face, i).quality or 0))[:15])  # type: ignore[union-attr]
        assert best <= top15
        assert s.get(Setting, "gallery_version").value > 5  # type: ignore[union-attr]
        c = s.get(Cluster, cid)
        assert c is not None and c.label_status == "labeled" and c.kid_id == k
    assert api.post(f"/api/clusters/{cid}/label", json={"kid_id": k}).status_code == 409


def test_label_validates_kid_and_faces(api) -> None:  # type: ignore[no-untyped-def]
    seed_clusters()
    run(api)
    cid = first_cluster(api)
    assert api.post(f"/api/clusters/{cid}/label", json={"kid_id": 9999}).status_code == 404
    assert api.post(f"/api/clusters/{cid}/label", json={"kid_id": kid(False)}).status_code == 409
    k = kid()
    assert (
        api.post(f"/api/clusters/{cid}/label", json={"kid_id": k, "face_ids": [1e6]}).status_code
        == 422
    )
    assert api.post(f"/api/clusters/{cid}/label", json={"kid_id": k, "extra": 1}).status_code == 422


def test_label_selected_faces_only_releases_the_rest(api) -> None:  # type: ignore[no-untyped-def]
    seed_clusters()
    run(api)
    cid = first_cluster(api)
    d = api.get(f"/api/clusters/{cid}").json()
    ids = [f["face_id"] for f in d["faces"]]
    k = kid()
    r = api.post(f"/api/clusters/{cid}/label", json={"kid_id": k, "face_ids": ids[:3]})
    assert r.status_code == 200 and r.json()["selected"] == 3
    with dbs.get_session() as s:
        left = s.scalar(select(func.count()).select_from(Face).where(Face.cluster_id == cid))
        assert left == 3
        assert s.get(Face, ids[5]).cluster_id is None  # type: ignore[union-attr]


def test_ignore_leaves_no_file_or_row(api) -> None:  # type: ignore[no-untyped-def]
    a, b = seed_clusters()
    run(api)
    cid = first_cluster(api)
    with dbs.get_session() as s:
        members = list(s.scalars(select(Face).where(Face.cluster_id == cid)))
        ids = [f.id for f in members]
        files = [Path(f.crop_path) for f in members if f.crop_path]
    assert files and all(p.exists() for p in files)
    r = api.post(f"/api/clusters/{cid}/ignore")
    assert r.status_code == 200 and r.json()["faces_deleted"] == len(ids)
    assert not any(p.exists() for p in files)
    with dbs.get_session() as s:
        assert s.scalar(select(func.count()).select_from(Face).where(Face.id.in_(ids))) == 0
        assert s.get(Cluster, cid) is None
        assert s.scalar(select(func.count()).select_from(Cluster)) == 1  # the other one stays
    assert api.post(f"/api/clusters/{cid}/ignore").status_code == 404


def test_ignore_never_deletes_outside_crops_dir(api, tmp_path) -> None:  # type: ignore[no-untyped-def]
    seed_clusters()
    outside = tmp_path.parent / "victim.jpg"
    outside.write_bytes(b"keep me")
    inside_data = get_settings().data_dir / "raw" / "keep.jpg"
    inside_data.parent.mkdir(parents=True, exist_ok=True)
    inside_data.write_bytes(b"keep me too")
    run(api)
    cid = first_cluster(api)
    with dbs.get_session() as s:
        fs = list(s.scalars(select(Face).where(Face.cluster_id == cid)))
        fs[0].crop_path = str(outside)
        fs[1].crop_path = str(get_settings().data_dir / "crops" / ".." / ".." / outside.name)
        fs[2].crop_path = str(inside_data)
    assert api.post(f"/api/clusters/{cid}/ignore").status_code == 200
    assert outside.read_bytes() == b"keep me" and inside_data.exists()


def test_split(api) -> None:  # type: ignore[no-untyped-def]
    # one broad cluster made of two sub-identities that HDBSCAN merges at min_cluster_size 4
    base = identity(1, 1)[0]
    rng = np.random.default_rng(5)
    pts = []
    for off in (0.0, 1.0):
        d = rng.normal(size=512)
        d /= np.linalg.norm(d)
        for _ in range(5):
            v = base + 0.55 * off * d + 0.03 * rng.normal(size=512) / np.sqrt(512)
            pts.append((v / np.linalg.norm(v)).astype(np.float32))
    add_many(np.stack(pts))
    with dbs.get_session() as s:
        c = Cluster(size=10, label_status="unlabeled")
        s.add(c)
        s.flush()
        for f in s.scalars(select(Face)):
            f.cluster_id = c.id
        cid = c.id
    r = api.post(f"/api/clusters/{cid}/split")
    assert r.status_code == 200, r.text
    assert len(r.json()["cluster_ids"]) == 2
    with dbs.get_session() as s:
        assert s.get(Cluster, cid) is None


def test_split_tight_cluster_conflicts(api) -> None:  # type: ignore[no-untyped-def]
    seed_clusters()
    run(api)
    cid = first_cluster(api)
    assert api.post(f"/api/clusters/{cid}/split").status_code == 409
    assert api.get(f"/api/clusters/{cid}").status_code == 200  # untouched
