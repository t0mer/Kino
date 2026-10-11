# ruff: noqa: F811  (fixtures imported from test_api_review)
"""REST: activity list, image detail, original file and crops (CLAUDE.md §11.7, §12, §13)."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import event

from kino.db import session as dbs
from kino.db.models import Forward, Image, SavedPhoto
from kino.services.files import safe_join
from tests.test_api_review import (  # noqa: F401  (fixtures + seed helpers)
    data_dir,
    enqueued,
    jpeg,
    photos_dir,
    seed_face,
    seed_group,
    seed_image,
    seed_kid,
    seed_match,
    unit,
    world,
)

# --- safe_join --------------------------------------------------------------------------


def test_safe_join_accepts_inside(tmp_path):
    (tmp_path / "a").mkdir()
    assert safe_join(tmp_path, "a/b.jpg") == (tmp_path / "a" / "b.jpg").resolve()
    assert safe_join(tmp_path, str(tmp_path / "a" / "b.jpg")) == (tmp_path / "a/b.jpg").resolve()


@pytest.mark.parametrize("rel", ["../x", "a/../../x", "/etc/passwd", "a\0b", "", ".."])
def test_safe_join_rejects(tmp_path, rel):
    with pytest.raises(ValueError):
        safe_join(tmp_path, rel)


def test_safe_join_rejects_symlink_escape(tmp_path):
    out = tmp_path / "outside"
    out.mkdir()
    base = tmp_path / "base"
    base.mkdir()
    (base / "link").symlink_to(out)
    with pytest.raises(ValueError):
        safe_join(base, "link/secret.jpg")


# --- file -------------------------------------------------------------------------------


def set_raw(img_id: int, value: str | None) -> None:
    with dbs.get_session() as s:
        s.get(Image, img_id).raw_path = value


def test_file_serves_raw(world):
    r = world["api"].get(f"/api/images/{world['img']}/file")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private"
    assert r.headers["content-disposition"].startswith("inline")
    assert r.content[:2] == b"\xff\xd8"


def test_file_prefers_saved_copy(world):
    p = photos_dir() / "noa" / "saved.jpg"
    p.parent.mkdir(parents=True)
    p.write_bytes(jpeg(77))
    with dbs.get_session() as s:
        s.add(SavedPhoto(image_id=world["img"], kid_id=world["noa"], path=str(p)))
    r = world["api"].get(f"/api/images/{world['img']}/file")
    assert r.content == p.read_bytes()


def test_file_missing_original(world):
    Path(_raw(world["img"])).unlink()
    r = world["api"].get(f"/api/images/{world['img']}/file")
    assert r.status_code == 404
    assert r.json() == {"detail": "original no longer stored"}


def _raw(img_id: int) -> str:
    with dbs.get_session() as s:
        return s.get(Image, img_id).raw_path


def test_file_unknown_image(world):
    assert world["api"].get("/api/images/9999/file").status_code == 404


@pytest.mark.parametrize("evil", ["../../etc/passwd", "/etc/passwd", "a\0b"])
def test_file_traversal_in_stored_path_is_404(world, evil):
    set_raw(world["img"], evil)
    r = world["api"].get(f"/api/images/{world['img']}/file")
    assert r.status_code == 404
    assert b"root:" not in r.content


def test_file_symlink_out_of_dir_is_404(world, tmp_path_factory):
    secret = tmp_path_factory.mktemp("secret") / "s.jpg"
    secret.write_bytes(jpeg(5))
    link = data_dir() / "raw" / "link.jpg"
    link.symlink_to(secret)
    set_raw(world["img"], str(link))
    r = world["api"].get(f"/api/images/{world['img']}/file")
    assert r.status_code == 404


def test_crop_serves_and_unknown_404(world):
    r = world["api"].get(f"/api/crops/{world['face']}")
    assert r.status_code == 200 and r.headers["cache-control"] == "private"
    assert world["api"].get("/api/crops/9999").status_code == 404


def test_crop_traversal_is_404(world):
    from kino.db.models import Face

    with dbs.get_session() as s:
        s.get(Face, world["face"]).crop_path = "../../etc/passwd"
    assert world["api"].get(f"/api/crops/{world['face']}").status_code == 404


# --- list -------------------------------------------------------------------------------


def test_list_row_shape(world):
    api = world["api"]
    with dbs.get_session() as s:
        s.get(Image, world["img"]).error = f"boom at {data_dir()}/raw/x.jpg " + "e" * 1000
    page = api.get("/api/images").json()
    assert page["next_cursor"] is None
    (it,) = page["items"]
    assert it["id"] == world["img"]
    assert it["group_name"] == "Class 3B"
    assert it["sender"] == "Dana"
    assert it["status"] == "done"
    assert it["face_count"] == 1
    assert it["matches"] == {"review": 1}
    assert it["has_file"] is True
    assert str(data_dir()) not in (it["error"] or "")
    assert len(it["error"]) <= 300
    assert "sender_id" not in it


def test_list_pagination_filters_and_order(world):
    api = world["api"]
    ids = [world["img"]] + [seed_image(world["gid"], msg=f"m{i}") for i in range(2, 6)]
    other = seed_group(chat="g2@g.us", name="Other", slug="other")
    oi = seed_image(other, msg="o1", raw=False)
    with dbs.get_session() as s:
        s.get(Image, oi).status = "failed"
    p1 = api.get("/api/images?limit=3").json()
    assert [i["id"] for i in p1["items"]] == [oi, ids[4], ids[3]]
    p2 = api.get(f"/api/images?limit=3&cursor={p1['next_cursor']}").json()
    assert [i["id"] for i in p2["items"]] == [ids[2], ids[1], ids[0]]
    assert p2["next_cursor"] is None
    assert [i["id"] for i in api.get("/api/images?status=failed").json()["items"]] == [oi]
    assert api.get("/api/images?status=failed").json()["items"][0]["has_file"] is False
    assert len(api.get(f"/api/images?group_id={world['gid']}").json()["items"]) == 5
    assert api.get("/api/images?limit=201").status_code == 422
    assert api.get("/api/images?status=bogus").status_code == 422


def test_list_has_no_n_plus_one(world):
    for i in range(2, 12):
        im = seed_image(world["gid"], msg=f"m{i}")
        seed_match(seed_face(im, unit(0)), world["noa"])
    n = 0

    def count(*_a, **_k):
        nonlocal n
        n += 1

    eng = dbs.get_engine()
    event.listen(eng, "before_cursor_execute", count)
    try:
        r = world["api"].get("/api/images")
    finally:
        event.remove(eng, "before_cursor_execute", count)
    assert len(r.json()["items"]) == 11
    assert n <= 12  # fixed number of queries (auth/session overhead included)


# --- detail -----------------------------------------------------------------------------


def test_detail(world):
    with dbs.get_session() as s:
        s.add(
            Forward(
                image_id=world["img"],
                dest_chat_id="x@c.us",
                kid_ids=[world["noa"]],
                status="failed",
                attempts=4,
                last_error=f"{data_dir()}/boom",
            )
        )
    d = world["api"].get(f"/api/images/{world['img']}").json()
    assert d["id"] == world["img"] and d["group_name"] == "Class 3B"
    (f,) = d["faces"]
    assert (
        f["id"] == world["face"] and f["tier"] == "L" and f["crop_url"] == f"/api/crops/{f['id']}"
    )
    assert {"bbox", "det_score", "size_px", "quality", "from_tile"} <= f.keys()
    (m,) = d["matches"]
    assert m["kid_name"] == "Noa" and m["decision"] == "review" and m["face_id"] == f["id"]
    assert {"score", "margin", "method", "decided_by"} <= m.keys()
    (fw,) = d["forwards"]
    assert fw["status"] == "failed" and str(data_dir()) not in (fw["last_error"] or "")
    assert d["file_url"] == f"/api/images/{world['img']}/file"
    text = world["api"].get(f"/api/images/{world['img']}").text
    assert str(data_dir()) not in text and "embedding" not in text and "raw_path" not in text


def test_detail_unknown_404(world):
    assert world["api"].get("/api/images/9999").status_code == 404


def test_auth_required(world):
    api = world["api"]
    api.cookies.clear()
    for path in ("/api/images", "/api/images/1", "/api/images/1/file", "/api/crops/1"):
        assert api.get(path).status_code == 401, path
