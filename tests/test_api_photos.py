# ruff: noqa: F811  (fixtures imported from test_api_review)
"""REST: photo thumbnails, sender/sibling fields and per-kid counts."""

from __future__ import annotations

import io
from pathlib import Path

from PIL import Image as PILImage
from sqlalchemy import event

from kino.db import session as dbs
from kino.db.models import Image, SavedPhoto
from tests.test_api_review import (  # noqa: F401  (fixtures + seed helpers)
    act,
    data_dir,
    enqueued,
    seed_face,
    seed_group,
    seed_image,
    seed_match,
    unit,
    world,
)


def confirm_all(world) -> list[SavedPhoto]:
    f2 = seed_face(world["img"], unit(5), n=1)
    m2 = seed_match(f2, world["itai"])
    assert act(world["api"], world["m"], "confirm").status_code == 200
    assert act(world["api"], m2, "confirm").status_code == 200
    with dbs.get_session() as s:
        rows = list(s.query(SavedPhoto).order_by(SavedPhoto.id))
        s.expunge_all()
    return rows


def test_thumb_generated_cached_and_headers(world):
    api = world["api"]
    sp = confirm_all(world)[0]
    r = api.get(f"/api/photos/{sp.id}/thumb")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.headers["cache-control"] == "private, max-age=86400"
    assert r.headers["x-content-type-options"] == "nosniff"
    im = PILImage.open(io.BytesIO(r.content))
    assert max(im.size) == 400 and im.mode == "RGB"
    cached = data_dir() / "thumbs" / f"{sp.id}_400.jpg"
    assert cached.is_file()
    # cache reused: replace with a marker thumb that is still a valid JPEG
    PILImage.new("RGB", (10, 7)).save(cached, "JPEG")
    assert PILImage.open(io.BytesIO(api.get(f"/api/photos/{sp.id}/thumb").content)).size == (10, 7)
    # garbled cache is a miss
    cached.write_bytes(b"garbage")
    r = api.get(f"/api/photos/{sp.id}/thumb")
    assert max(PILImage.open(io.BytesIO(r.content)).size) == 400
    # width clamped
    r = api.get(f"/api/photos/{sp.id}/thumb?w=5")
    assert max(PILImage.open(io.BytesIO(r.content)).size) == 128
    r = api.get(f"/api/photos/{sp.id}/thumb?w=99999")
    assert max(PILImage.open(io.BytesIO(r.content)).size) == 400  # source is only 400 wide
    assert (data_dir() / "thumbs" / f"{sp.id}_1024.jpg").is_file()


def test_thumb_applies_exif_orientation(world):
    sp = confirm_all(world)[0]
    img = PILImage.new("RGB", (300, 100), (200, 30, 30))
    exif = img.getexif()
    exif[0x0112] = 6  # rotate 90 CW on display
    img.save(sp.path, "JPEG", exif=exif)
    r = world["api"].get(f"/api/photos/{sp.id}/thumb")
    w, h = PILImage.open(io.BytesIO(r.content)).size
    assert (w, h) == (100, 300)


def test_thumb_404s_and_traversal(world, tmp_path):
    api = world["api"]
    sp = confirm_all(world)[0]
    assert api.get("/api/photos/9999/thumb").status_code == 404
    Path(sp.path).unlink()
    assert api.get(f"/api/photos/{sp.id}/thumb").status_code == 404
    outside = tmp_path / "secret.jpg"
    PILImage.new("RGB", (50, 50)).save(outside, "JPEG")
    with dbs.get_session() as s:
        s.get(SavedPhoto, sp.id).path = str(outside)
    assert api.get(f"/api/photos/{sp.id}/thumb").status_code == 404
    with dbs.get_session() as s:
        s.get(SavedPhoto, sp.id).path = "../../etc/passwd"
    assert api.get(f"/api/photos/{sp.id}/thumb").status_code == 404
    assert not (data_dir() / "thumbs").exists() or not list((data_dir() / "thumbs").glob("*.jpg"))


def test_thumb_and_counts_require_auth(world):
    api = world["api"]
    sp = confirm_all(world)[0]
    api.cookies.clear()
    assert api.get(f"/api/photos/{sp.id}/thumb").status_code == 401
    assert api.get("/api/photos/counts").status_code == 401


def test_thumb_deleted_with_photo_and_kid(world):
    api = world["api"]
    a, b = confirm_all(world)
    for sp in (a, b):
        assert api.get(f"/api/photos/{sp.id}/thumb?w=200").status_code == 200
        assert api.get(f"/api/photos/{sp.id}/thumb").status_code == 200
    assert api.delete(f"/api/photos/{a.id}").status_code == 200
    assert not list((data_dir() / "thumbs").glob(f"{a.id}_*.jpg"))
    assert list((data_dir() / "thumbs").glob(f"{b.id}_*.jpg"))
    assert api.delete(f"/api/kids/{b.kid_id}").status_code == 200
    assert not list((data_dir() / "thumbs").glob("*.jpg"))


def test_list_sender_siblings_and_thumb_url(world):
    api = world["api"]
    a, b = confirm_all(world)
    items = {p["id"]: p for p in api.get("/api/photos").json()["items"]}
    pa = items[a.id]
    assert pa["thumb_url"] == f"/api/photos/{a.id}/thumb" and pa["sender_name"] == "Dana"
    assert pa["kid_ids"] == [b.kid_id] and pa["kid_names"] == ["איתי"]
    assert items[b.id]["kid_ids"] == [a.kid_id] and items[b.id]["kid_names"] == ["Noa"]
    only = api.get(f"/api/photos?kid_id={a.kid_id}").json()["items"]
    assert only[0]["kid_ids"] == [b.kid_id]  # siblings ignore the kid filter
    with dbs.get_session() as s:
        s.get(Image, world["img"]).sender_name = "972501234567@c.us"
    assert api.get("/api/photos").json()["items"][0]["sender_name"] is None
    with dbs.get_session() as s:
        s.get(Image, world["img"]).sender_name = "Dana 0501234567 Levi"
    name = api.get("/api/photos").json()["items"][0]["sender_name"]
    assert name and "0501234567" not in name


def test_counts_and_filters(world):
    api = world["api"]
    confirm_all(world)
    g2 = seed_group(chat="120363000000000002@g.us", name="Dance", slug="dance")
    img2 = seed_image(g2, msg="m2")
    assert (
        act(api, seed_match(seed_face(img2, unit(0)), world["noa"]), "confirm").status_code == 200
    )
    r = api.get("/api/photos/counts").json()
    assert r["total"] == 2
    assert r["kids"] == [
        {"kid_id": world["noa"], "kid_name": "Noa", "count": 2},
        {"kid_id": world["itai"], "kid_name": "איתי", "count": 1},
    ]
    r = api.get(f"/api/photos/counts?group_id={g2}").json()
    assert r["total"] == 1 and r["kids"] == [
        {"kid_id": world["noa"], "kid_name": "Noa", "count": 1}
    ]
    assert api.get("/api/photos/counts?from=2026-05-07T00:00:00").json() == {"total": 0, "kids": []}
    assert api.get("/api/photos/counts?to=2026-05-07T00:00:00Z").json()["total"] == 2


def test_list_photos_has_no_n_plus_one(world):
    api = world["api"]
    confirm_all(world)
    n = 0

    def count(*_a, **_k):
        nonlocal n
        n += 1

    def run():
        nonlocal n
        n = 0
        eng = dbs.get_engine()
        event.listen(eng, "before_cursor_execute", count)
        try:
            return api.get("/api/photos")
        finally:
            event.remove(eng, "before_cursor_execute", count)

    assert len(run().json()["items"]) == 2
    small = n
    for i in range(3, 9):
        im = seed_image(world["gid"], msg=f"m{i}")
        assert (
            act(api, seed_match(seed_face(im, unit(0)), world["noa"]), "confirm").status_code == 200
        )
    assert len(run().json()["items"]) == 8
    assert n == small
