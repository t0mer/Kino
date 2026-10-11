"""REST: /api/kids (CLAUDE.md §11.1, §12)."""

from __future__ import annotations

import numpy as np
from sqlalchemy import func, select

from kino.db import session as dbs
from kino.db.models import (
    Cluster,
    Face,
    Forward,
    Group,
    Image,
    KidGroup,
    Match,
    NegativeFace,
    ReferenceFace,
    SavedPhoto,
    Setting,
)

EMB = dbs.embedding_to_blob(np.ones(512, dtype=np.float32))


def make_group(chat_id: str = "1203@g.us", name: str = "Class", watched: bool = False) -> int:
    with dbs.get_session() as s:
        g = Group(chat_id=chat_id, name=name, slug="class", watched=watched)
        s.add(g)
        s.flush()
        return g.id


def create(api, **body):
    return api.post("/api/kids", json=body)


def test_requires_auth_and_csrf(api):
    api.headers.pop("X-CSRF-Token")
    assert create(api, display_name="Noa").status_code == 403
    api.cookies.clear()
    assert api.get("/api/kids").status_code == 401


def test_create_hebrew_name_transliterates_slug(api):
    r = create(api, display_name=" נועה ", birth_year=2018)
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["display_name"] == "נועה"
    assert body["slug"] == "noa"
    assert body["birth_year"] == 2018
    assert body["auto_save_enabled"] is False and body["active"] is True
    assert body["threshold_offset"] == 0.0
    assert body["group_ids"] == [] and body["reference_count"] == 0
    assert api.get(f"/api/kids/{body['id']}").json()["slug"] == "noa"


def test_slug_collision_gets_suffix(api):
    assert create(api, display_name="Noa").json()["slug"] == "noa"
    assert create(api, display_name="נועה").json()["slug"] == "noa-2"
    assert create(api, display_name="noa").status_code == 409  # same name as "Noa"
    assert create(api, display_name="NOA!!").json()["slug"] == "noa-3"
    assert create(api, display_name="Noa K").json()["slug"] == "noa-k"
    r = create(api, display_name="Other", slug="noa-k")
    assert r.status_code == 409 and "slug" in r.json()["detail"]


def test_create_validation(api):
    assert create(api, display_name="").status_code == 422
    assert create(api, display_name="   ").status_code == 422
    assert create(api, display_name="A", threshold_offset=0.5).status_code == 422
    assert create(api, display_name="A", threshold_offset=-0.2).status_code == 422
    assert create(api, display_name="A", bogus=1).status_code == 422
    assert create(api, display_name="A", group_ids=[999]).status_code == 422
    assert api.get("/api/kids").json() == []


def test_create_with_groups_and_list(api):
    gid = make_group()
    kid = create(api, display_name="Itai", group_ids=[gid], threshold_offset=0.1).json()
    assert kid["group_ids"] == [gid]
    with dbs.get_session() as s:
        s.add(ReferenceFace(kid_id=kid["id"], embedding=EMB, source="upload"))
        s.add(ReferenceFace(kid_id=kid["id"], embedding=EMB, source="upload", status="retired"))
        img = Image(message_id="m1", status="done")
        s.add(img)
        s.flush()
        s.add(SavedPhoto(image_id=img.id, kid_id=kid["id"], path="/x.jpg"))
    rows = api.get("/api/kids").json()
    assert len(rows) == 1
    assert rows[0]["reference_count"] == 1
    assert rows[0]["today"] == 1 and rows[0]["last_7_days"] == 1
    assert rows[0]["threshold_offset"] == 0.1


def test_get_missing_is_404(api):
    assert api.get("/api/kids/42").status_code == 404
    assert api.patch("/api/kids/42", json={"active": False}).status_code == 404
    assert api.delete("/api/kids/42").status_code == 404


def test_patch(api):
    gid = make_group()
    kid = create(api, display_name="Noa", birth_year=2018).json()
    r = api.patch(
        f"/api/kids/{kid['id']}",
        json={"auto_save_enabled": True, "threshold_offset": -0.05, "group_ids": [gid]},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["auto_save_enabled"] is True
    assert body["threshold_offset"] == -0.05
    assert body["group_ids"] == [gid]
    assert body["birth_year"] == 2018  # untouched
    body = api.patch(f"/api/kids/{kid['id']}", json={"birth_year": None}).json()
    assert body["birth_year"] is None
    body = api.patch(f"/api/kids/{kid['id']}", json={"group_ids": []}).json()
    assert body["group_ids"] == []
    assert api.patch(f"/api/kids/{kid['id']}", json={"threshold_offset": 0.31}).status_code == 422
    assert api.patch(f"/api/kids/{kid['id']}", json={"slug": "x", "nope": 1}).status_code == 422
    assert api.patch(f"/api/kids/{kid['id']}", json={"group_ids": [77]}).status_code == 422


def test_patch_name_and_slug_conflicts(api):
    a = create(api, display_name="Noa").json()
    create(api, display_name="Itai")
    assert api.patch(f"/api/kids/{a['id']}", json={"display_name": "itai"}).status_code == 409
    assert api.patch(f"/api/kids/{a['id']}", json={"slug": "itai"}).status_code == 409
    r = api.patch(f"/api/kids/{a['id']}", json={"display_name": "Noa", "slug": "Noa K"})
    assert r.status_code == 200 and r.json()["slug"] == "noa-k"


def _gallery_version() -> object:
    with dbs.get_session() as s:
        row = s.get(Setting, "gallery_version")
        return None if row is None else row.value


def test_delete_cascades(api, tmp_path):
    from kino.forward import get_forward_config, put_setting

    gid = make_group()
    noa = create(api, display_name="Noa", group_ids=[gid]).json()["id"]
    itai = create(api, display_name="Itai").json()["id"]
    photos = tmp_path / "photos"
    (photos / "noa").mkdir(parents=True)
    mine = photos / "noa" / "a.jpg"
    mine.write_bytes(b"jpg")
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    sibling = photos / "itai.jpg"
    sibling.write_bytes(b"jpg")
    with dbs.get_session() as s:
        img = Image(message_id="m1", status="done")
        img2 = Image(message_id="m2", status="done")
        s.add_all([img, img2])
        s.flush()
        face = Face(
            image_id=img.id, bbox_x=0, bbox_y=0, bbox_w=90, bbox_h=90, det_score=0.9, size_px=90
        )
        s.add(face)
        s.flush()
        cluster = Cluster(size=4, label_status="labeled", kid_id=noa)
        s.add_all(
            [
                ReferenceFace(kid_id=noa, embedding=EMB, source="upload"),
                ReferenceFace(kid_id=itai, embedding=EMB, source="upload"),
                NegativeFace(kid_id=noa, embedding=EMB),
                Match(face_id=face.id, kid_id=noa, score=0.6, decision="auto_saved"),
                SavedPhoto(image_id=img.id, kid_id=noa, path=str(mine)),
                SavedPhoto(image_id=img2.id, kid_id=noa, path=str(outside)),
                SavedPhoto(image_id=img.id, kid_id=itai, path=str(sibling)),
                Forward(image_id=img.id, dest_chat_id="d@c.us", kid_ids=[noa, itai]),
                Forward(image_id=img2.id, dest_chat_id="d@c.us", kid_ids=[noa], status="sent"),
                Forward(image_id=img2.id, dest_chat_id="e@c.us", kid_ids=[itai]),
                cluster,
            ]
        )
        put_setting(s, "forward.per_kid", {str(noa): "x@c.us", str(itai): "y@c.us"})
    before = _gallery_version()

    r = api.delete(f"/api/kids/{noa}")
    assert r.status_code == 200, r.text
    assert r.json() == {"id": noa, "removed_files": 1, "cancelled_forwards": 1}
    assert not mine.exists()
    assert outside.exists() and sibling.exists()  # outside photos dir / other kid's file
    assert _gallery_version() != before
    with dbs.get_session() as s:
        for model in (ReferenceFace, NegativeFace, Match, SavedPhoto, KidGroup):
            n = s.scalar(select(func.count()).select_from(model).where(model.kid_id == noa))
            assert n == 0, model
        assert s.scalar(select(func.count()).select_from(ReferenceFace)) == 1  # Itai's
        statuses = {(f.image_id, f.dest_chat_id): f.status for f in s.scalars(select(Forward))}
        assert sorted(statuses.values()) == ["cancelled", "pending", "sent"]
        c = s.scalar(select(Cluster))
        assert (c.kid_id, c.label_status) == (None, "unlabeled")
        assert get_forward_config(s).per_kid == {itai: "y@c.us"}
    assert api.get(f"/api/kids/{noa}").status_code == 404
