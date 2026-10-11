"""REST: enrollment uploads, temp faces, references and coverage (CLAUDE.md §11.2, §11.6, §12).

No face images are committed: photos are random-noise JPEG/PNG/HEIC generated per test and
the detector/embedder are fakes scripted by image width.
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import piexif
import pytest
from PIL import Image as PILImage
from sqlalchemy import func, select

from kino import tasks
from kino.db import session as dbs
from kino.db.models import Face, Image, Kid, NegativeFace, ReferenceFace, Setting, TempFace
from kino.pipeline.detect import Detection
from kino.pipeline.quality import Tier
from kino.services import enroll_web


def det(x1, y1, side, score=0.9, nose=0.5):
    w = h = float(side)
    kps = np.array(
        [
            [x1 + 0.3 * w, y1 + 0.4 * h],
            [x1 + 0.7 * w, y1 + 0.4 * h],
            [x1 + nose * w, y1 + 0.62 * h],
            [x1 + 0.35 * w, y1 + 0.8 * h],
            [x1 + 0.65 * w, y1 + 0.8 * h],
        ],
        dtype=np.float32,
    )
    return Detection((x1, y1, x1 + w, y1 + h), score, kps)


class FakeDetector:
    """Detections keyed by the (upright) image width."""

    def __init__(self, by_width):
        self.by_width = by_width
        self.widths: list[int] = []

    def detect(self, img, det_size, max_upscale=1.0):
        self.widths.append(img.shape[1])
        if img.shape[1] == 666:
            raise RuntimeError("detector crashed")
        return list(self.by_width.get(img.shape[1], []))


class FakeEmbedder:
    """A fresh unit axis per call, so every face is a distinct identity."""

    def __init__(self):
        self.n = 0

    def embed(self, aligned):
        v = np.zeros(512, dtype=np.float32)
        v[self.n % 512] = 1.0
        self.n += 1
        return v


@pytest.fixture
def faces():
    def _set(by_width):
        d = FakeDetector(by_width)
        tasks.set_pipeline(detector=d, embedder=FakeEmbedder())
        return d

    yield _set
    tasks.reset_pipeline()


def photo(width=400, height=300, fmt="JPEG", exif_date=None, orientation=None, flat=False):
    rng = np.random.default_rng(width)
    if flat:
        arr = np.full((height, width, 3), 128, dtype=np.uint8)
    else:
        arr = rng.integers(0, 255, (height, width, 3), dtype=np.uint8)
    kw = {}
    if exif_date or orientation:
        ifd: dict = {"0th": {}, "Exif": {}}
        if exif_date:
            ifd["Exif"][piexif.ExifIFD.DateTimeOriginal] = exif_date.encode()
        if orientation:
            ifd["0th"][piexif.ImageIFD.Orientation] = orientation
        kw["exif"] = piexif.dump(ifd)
    buf = io.BytesIO()
    PILImage.fromarray(arr).save(buf, fmt, **kw)
    return buf.getvalue()


def make_kid(name="Noa", slug="noa") -> int:
    with dbs.get_session() as s:
        k = Kid(display_name=name, slug=slug)
        s.add(k)
        s.flush()
        return k.id


def upload(api, kid_id, *files):
    return api.post(
        f"/api/kids/{kid_id}/uploads",
        files=[("files", (name, data, "image/jpeg")) for name, data in files],
    )


def gallery_version():
    with dbs.get_session() as s:
        row = s.get(Setting, "gallery_version")
        return None if row is None else row.value


def count(model) -> int:
    with dbs.get_session() as s:
        return s.scalar(select(func.count()).select_from(model)) or 0


# --- uploads -------------------------------------------------------------------------------


def test_upload_requires_auth_and_csrf(api, faces):
    kid = make_kid()
    api.headers.pop("X-CSRF-Token")
    assert upload(api, kid, ("a.jpg", photo())).status_code == 403
    api.cookies.clear()
    assert upload(api, kid, ("a.jpg", photo())).status_code == 401


def test_upload_returns_faces_with_boxes_and_tiers(api, faces):
    kid = make_kid()
    faces({400: [det(100, 100, 120), det(10, 10, 30), det(300, 200, 20)]})
    r = upload(api, kid, ("a.jpg", photo(exif_date="2024:05:06 07:08:09")))
    assert r.status_code == 200, r.text
    [entry] = r.json()
    assert entry["filename"] == "a.jpg"
    assert (entry["width"], entry["height"]) == (400, 300)
    assert entry["photo_date"] == "2024-05-06T07:08:09"
    assert entry["error"] is None and entry["upload_id"]
    by_tier = {f["tier"]: f for f in entry["faces"]}
    assert set(by_tier) == {"L", "S", "XS"}
    big = by_tier["L"]
    assert big["selectable"] is True and big["reason"] is None
    assert big["bbox"] == {"x": 100.0, "y": 100.0, "w": 120.0, "h": 120.0}
    assert 0.4 <= big["quality"] <= 1.0
    assert big["crop_url"] == f"/api/temp-crops/{big['temp_face_id']}"
    for t in ("S", "XS"):
        assert by_tier[t]["selectable"] is False and by_tier[t]["reason"] == "too small"
    crop = api.get(big["crop_url"])
    assert crop.status_code == 200 and crop.headers["content-type"] == "image/jpeg"
    assert crop.content[:2] == b"\xff\xd8"
    with dbs.get_session() as s:
        tf = s.get(TempFace, big["temp_face_id"])
        assert tf.kid_id == kid and tf.photo_date == datetime(2024, 5, 6, 7, 8, 9)
        assert tf.embedding is not None
        xs = s.scalar(select(TempFace).where(TempFace.tier == "XS"))
        assert xs.embedding is None  # XS faces are never embedded


def test_upload_zero_faces_and_ten_faces(api, faces):
    kid = make_kid()
    tens = [det(10 + 38 * i, 20, 36) for i in range(10)]
    faces({400: [], 410: tens})
    r = upload(api, kid, ("none.jpg", photo(400)), ("ten.png", photo(410, fmt="PNG")))
    assert r.status_code == 200, r.text
    none, ten = r.json()
    assert none["faces"] == [] and none["error"] is None
    assert len(ten["faces"]) == 10 and ten["error"] is None


def test_upload_applies_exif_orientation(api, faces):
    kid = make_kid()
    d = faces({300: [det(50, 50, 100)]})
    r = upload(api, kid, ("rot.jpg", photo(400, 300, orientation=6)))
    assert r.status_code == 200, r.text
    entry = r.json()[0]
    assert (entry["width"], entry["height"]) == (300, 400)
    assert d.widths == [300] and len(entry["faces"]) == 1


def test_upload_heic(api, faces):
    pillow_heif = pytest.importorskip("pillow_heif")
    if not pillow_heif.libheif_info().get("encoders", {}).get("x265"):
        pytest.skip("no HEIC encoder")
    kid = make_kid()
    faces({400: [det(100, 100, 120)]})
    buf = io.BytesIO()
    rng = np.random.default_rng(1)
    PILImage.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)).save(buf, "HEIF")
    r = upload(api, kid, ("IMG_0001.HEIC", buf.getvalue()))
    assert r.status_code == 200, r.text
    assert r.json()[0]["error"] is None and len(r.json()[0]["faces"]) == 1


def test_bad_file_does_not_fail_the_others(api, faces):
    kid = make_kid()
    faces({400: [det(100, 100, 120)]})
    big = b"\xff\xd8" + b"\0" * (20 * 1024 * 1024 + 10)
    r = upload(
        api,
        kid,
        ("fake.jpg", b"#!/bin/sh\necho not an image\n" * 20),
        ("good.jpg", photo(400)),
        ("huge.jpg", big),
        ("crash.jpg", photo(666)),
    )
    assert r.status_code == 200, r.text
    fake, good, huge, crash = r.json()
    assert fake["status"] == 415 and "not a supported image" in fake["error"]
    assert fake["faces"] == [] and fake["width"] is None
    assert good["error"] is None and len(good["faces"]) == 1
    assert huge["status"] == 413 and "20 MB" in huge["error"]
    assert crash["status"] == 422 and "could not be processed" in crash["error"]
    assert count(TempFace) == 1


def test_only_bad_files_is_an_error(api, faces):
    kid = make_kid()
    faces({})
    r = upload(api, kid, ("big.jpg", b"\xff\xd8" + b"\0" * (25 * 1024 * 1024)))
    assert r.status_code == 413
    assert "big.jpg" in r.json()["detail"]
    r = upload(api, kid, ("x.jpg", b"GIF89a not really"))
    assert r.status_code == 415


def test_pixel_bomb_is_rejected_without_decoding(api, faces, monkeypatch):
    kid = make_kid()
    faces({})
    buf = io.BytesIO()
    PILImage.new("1", (10_000, 10_000)).save(buf, "PNG")  # 100 megapixels, tiny file
    decoded = []
    monkeypatch.setattr(enroll_web.ImageOps, "exif_transpose", lambda im: decoded.append(1) or im)
    r = upload(api, kid, ("bomb.png", buf.getvalue()))
    assert r.status_code == 422, r.text
    assert "megapixels" in r.json()["detail"] or "too large" in r.json()["detail"]
    assert decoded == []


def test_animated_and_gif_rejected(api, faces):
    kid = make_kid()
    faces({})
    frames = [PILImage.new("RGB", (300, 300), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "WEBP", save_all=True, append_images=frames[1:])
    assert upload(api, kid, ("a.webp", buf.getvalue())).status_code == 422
    gif = io.BytesIO()
    frames[0].save(gif, "GIF")
    assert upload(api, kid, ("a.gif", gif.getvalue())).status_code == 415


def test_upload_limits_and_unknown_kid(api, faces):
    faces({})
    assert upload(api, 999, ("a.jpg", photo())).status_code == 404
    kid = make_kid()
    assert api.post(f"/api/kids/{kid}/uploads").status_code == 422
    many = [(f"{i}.jpg", b"x") for i in range(21)]
    assert upload(api, kid, *many).status_code == 413
    r = api.post(
        f"/api/kids/{kid}/uploads",
        content=b"x",
        headers={"content-type": "multipart/form-data; boundary=x", "content-length": "999999999"},
    )
    assert r.status_code == 413


def test_upload_filename_is_sanitized(api, faces):
    kid = make_kid()
    faces({400: []})
    r = upload(api, kid, ("../../etc/passwd.jpg", photo()), ("C:\\x\\y.jpg", photo()))
    assert [e["filename"] for e in r.json()] == ["passwd.jpg", "y.jpg"]


# --- unusable reasons -------------------------------------------------------------------------


def test_unusable_reason_texts():
    assert enroll_web.unusable_reason(Tier.S, 0.9, 500, 0, 0) == "too small"
    assert enroll_web.unusable_reason(Tier.XS, 0.9, 500, 0, 0) == "too small"
    assert enroll_web.unusable_reason(Tier.M, 0.39, 5.0, 0, 0) == "too blurry"
    assert enroll_web.unusable_reason(Tier.M, 0.39, 500.0, 55.0, 0) == "profile too extreme"
    assert enroll_web.unusable_reason(Tier.M, 0.4, 0, 0, 0) is None
    assert enroll_web.unusable_reason(Tier.L, 0.95, 500, 0, 0) is None


def test_low_quality_face_is_not_selectable(api, faces):
    kid = make_kid()
    # tier M (40 px), flat image (no sharpness), nose at the cheek (full profile): q = 0.2
    faces({400: [det(100, 100, 40, nose=0.95)]})
    r = upload(api, kid, ("p.jpg", photo(400, flat=True)))
    [face] = r.json()[0]["faces"]
    assert face["tier"] == "M" and face["quality"] < 0.4
    assert face["selectable"] is False and face["reason"] == "profile too extreme"
    r = api.post(f"/api/kids/{kid}/references", json={"temp_face_ids": [face["temp_face_id"]]})
    assert r.status_code == 422 and "profile too extreme" in r.json()["detail"]


# --- references -------------------------------------------------------------------------------


def _uploaded(api, kid, by_width, *widths):
    faces_fixture = by_width
    tasks.set_pipeline(detector=FakeDetector(faces_fixture), embedder=FakeEmbedder())
    r = upload(api, kid, *[(f"{w}.jpg", photo(w, exif_date="2020:01:02 03:04:05")) for w in widths])
    assert r.status_code == 200, r.text
    return [f for e in r.json() for f in e["faces"]]


def test_confirm_creates_references_once(api, faces, tmp_path):
    kid = make_kid()
    picked = _uploaded(api, kid, {400: [det(100, 100, 120)], 410: [det(50, 60, 90)]}, 400, 410)
    ids = [f["temp_face_id"] for f in picked]
    before = gallery_version()
    r = api.post(f"/api/kids/{kid}/references", json={"temp_face_ids": ids})
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["duplicates"] == 0 and len(body["created"]) == 2
    ref = body["created"][0]
    assert ref["source"] == "upload" and ref["status"] == "active" and ref["kid_id"] == kid
    assert ref["photo_date"] == "2020-01-02T03:04:05"
    assert ref["yaw"] == pytest.approx(0.0, abs=1)
    assert ref["crop_url"] == f"/api/references/{ref['id']}/crop"
    assert api.get(ref["crop_url"]).status_code == 200
    assert gallery_version() != before
    with dbs.get_session() as s:
        for row in s.scalars(select(ReferenceFace)):
            assert (tmp_path / "crops" / "refs") in Path(row.crop_path).parents
    assert count(TempFace) == 0
    assert not list((tmp_path / "crops" / "tmp").iterdir())  # temp crops consumed
    # double submit: the temp faces are gone, nothing is duplicated
    again = api.post(f"/api/kids/{kid}/references", json={"temp_face_ids": ids})
    assert again.status_code == 404
    assert count(ReferenceFace) == 2
    assert api.get(f"/api/temp-crops/{ids[0]}").status_code == 404


def test_confirm_is_all_or_nothing(api, faces):
    kid = make_kid()
    picked = _uploaded(api, kid, {400: [det(100, 100, 120), det(10, 10, 30)]}, 400)
    ids = [f["temp_face_id"] for f in picked]
    r = api.post(f"/api/kids/{kid}/references", json={"temp_face_ids": ids})
    assert r.status_code == 422 and "too small" in r.json()["detail"]
    assert count(ReferenceFace) == 0 and count(TempFace) == 2


def test_confirm_with_photo_date_override_and_validation(api, faces):
    kid = make_kid()
    [f] = _uploaded(api, kid, {400: [det(100, 100, 120)]}, 400)
    url = f"/api/kids/{kid}/references"
    assert api.post(url, json={}).status_code == 422
    assert api.post(url, json={"temp_face_ids": [f["temp_face_id"]], "x": 1}).status_code == 422
    assert (
        api.post(url, json={"temp_face_ids": [f["temp_face_id"]], "source": "auto"}).status_code
        == 422
    )
    assert api.post(url, json={"temp_face_ids": ["nope"]}).status_code == 404
    other = make_kid("Itai", "itai")
    r = api.post(f"/api/kids/{other}/references", json={"temp_face_ids": [f["temp_face_id"]]})
    assert r.status_code == 404  # another kid's upload
    r = api.post(url, json={"temp_face_ids": [f["temp_face_id"]], "photo_date": "2023-07-01"})
    assert r.status_code == 201, r.text
    assert r.json()["created"][0]["photo_date"].startswith("2023-07-01")


def test_expired_temp_face_is_gone_and_purged(api, faces, tmp_path):
    kid = make_kid()
    [f] = _uploaded(api, kid, {400: [det(100, 100, 120)]}, 400)
    tid = f["temp_face_id"]
    with dbs.get_session() as s:
        tf = s.get(TempFace, tid)
        tf.created_at = tf.created_at - timedelta(hours=2)
        crop = Path(tf.crop_path)
    assert api.get(f"/api/temp-crops/{tid}").status_code == 410
    r = api.post(f"/api/kids/{kid}/references", json={"temp_face_ids": [tid]})
    assert r.status_code == 410
    stray = tmp_path / "crops" / "tmp" / "stray.jpg"
    stray.write_bytes(b"x")
    old = (datetime.now() - timedelta(hours=3)).timestamp()
    import os

    os.utime(stray, (old, old))
    fresh = _uploaded(api, kid, {400: [det(100, 100, 120)]}, 400)
    tasks.purge_expired_temp_faces()
    assert not crop.exists() and not stray.exists()
    with dbs.get_session() as s:
        assert s.get(TempFace, tid) is None
        assert s.get(TempFace, fresh[0]["temp_face_id"]) is not None


def test_duplicates_are_skipped(api, faces):
    kid = make_kid()

    class Same(FakeEmbedder):
        def embed(self, aligned):
            v = np.zeros(512, dtype=np.float32)
            v[7] = 1.0
            return v

    tasks.set_pipeline(detector=FakeDetector({400: [det(100, 100, 120)]}), embedder=Same())
    a = upload(api, kid, ("a.jpg", photo())).json()[0]["faces"][0]["temp_face_id"]
    b = upload(api, kid, ("b.jpg", photo())).json()[0]["faces"][0]["temp_face_id"]
    r = api.post(f"/api/kids/{kid}/references", json={"temp_face_ids": [a, b]})
    assert r.status_code == 201
    assert len(r.json()["created"]) == 1 and r.json()["duplicates"] == 1
    assert count(TempFace) == 0


def _pipeline_face(tmp_path, size=100.0, quality=0.8, emb_axis=3) -> int:
    crops = tmp_path / "crops"
    crops.mkdir(exist_ok=True)
    crop = crops / f"face{emb_axis}.jpg"
    crop.write_bytes(photo(160, 160))
    v = np.zeros(512, dtype=np.float32)
    v[emb_axis] = 1.0
    with dbs.get_session() as s:
        img = Image(message_id=f"m{emb_axis}", status="done", sent_at=datetime(2025, 3, 4, 5, 6))
        s.add(img)
        s.flush()
        face = Face(
            image_id=img.id,
            bbox_x=0,
            bbox_y=0,
            bbox_w=size,
            bbox_h=size,
            det_score=0.9,
            size_px=size,
            blur_score=300,
            yaw=-30.0,
            pitch=0.0,
            quality=quality,
            embedding=dbs.embedding_to_blob(v),
            crop_path=str(crop),
        )
        s.add(face)
        s.flush()
        return face.id


def test_reference_from_pipeline_face(api, tmp_path, faces):
    faces({})
    kid = make_kid()
    fid = _pipeline_face(tmp_path)
    small = _pipeline_face(tmp_path, size=30, emb_axis=4)
    url = f"/api/kids/{kid}/references"
    assert api.post(url, json={"face_ids": [999]}).status_code == 404
    r = api.post(url, json={"face_ids": [small]})
    assert r.status_code == 422 and "too small" in r.json()["detail"]
    r = api.post(url, json={"face_ids": [fid], "source": "review"})
    assert r.status_code == 201, r.text
    ref = r.json()["created"][0]
    assert ref["source"] == "review" and ref["face_id"] == fid
    assert ref["photo_date"] == "2025-03-04T05:06:00" and ref["yaw"] == -30.0
    again = api.post(url, json={"face_ids": [fid], "source": "review"})
    assert again.status_code == 201 and again.json()["created"] == []
    assert again.json()["duplicates"] == 1
    with dbs.get_session() as s:
        row = s.get(ReferenceFace, ref["id"])
        assert (tmp_path / "crops" / "refs") in Path(row.crop_path).parents  # own copy


def test_list_get_patch_delete_and_negative(api, tmp_path, faces):
    faces({})
    noa, itai = make_kid(), make_kid("Itai", "itai")
    f1, f2 = _pipeline_face(tmp_path, emb_axis=1), _pipeline_face(tmp_path, emb_axis=2)
    url = f"/api/kids/{noa}/references"
    created = api.post(url, json={"face_ids": [f1, f2]}).json()["created"]
    r1, r2 = created[0]["id"], created[1]["id"]

    assert [x["id"] for x in api.get(url).json()] == [r2, r1]
    assert [x["id"] for x in api.get(url, params={"cursor": r2}).json()] == [r1]
    assert api.get(url, params={"status": "retired"}).json() == []
    assert api.get(url, params={"source": "bogus"}).status_code == 422
    assert api.get("/api/kids/999/references").status_code == 404
    assert api.get(f"/api/references/{r1}").json()["kid_id"] == noa
    assert api.get("/api/references/999").status_code == 404

    v = gallery_version()
    r = api.patch(f"/api/references/{r1}", json={"status": "retired"})
    assert r.status_code == 200 and r.json()["status"] == "retired"
    assert gallery_version() != v
    assert [x["id"] for x in api.get(url, params={"status": "retired"}).json()] == [r1]
    assert api.patch(f"/api/references/{r1}", json={"status": "gone"}).status_code == 422
    assert api.patch(f"/api/references/{r1}", json={"kid_id": 999}).status_code == 404
    assert api.patch(f"/api/references/{r1}", json={"nope": 1}).status_code == 422
    v = gallery_version()
    r = api.patch(f"/api/references/{r1}", json={"kid_id": itai, "photo_date": "2021-01-01"})
    assert r.json()["kid_id"] == itai and r.json()["photo_date"].startswith("2021-01-01")
    assert gallery_version() != v
    r = api.patch(f"/api/references/{r1}", json={"photo_date": None})
    assert r.json()["photo_date"] is None and r.json()["kid_id"] == itai

    with dbs.get_session() as s:
        crop2 = Path(s.get(ReferenceFace, r2).crop_path)
    v = gallery_version()
    r = api.post(f"/api/references/{r2}/to-negative")
    assert r.status_code == 200, r.text
    assert r.json()["kid_id"] == noa
    assert gallery_version() != v
    assert api.get(f"/api/references/{r2}").status_code == 404
    assert not crop2.exists()
    with dbs.get_session() as s:
        neg = s.scalar(select(NegativeFace))
        assert neg.kid_id == noa and neg.source_face_id == f2
        assert Path(neg.crop_path).is_file()
        assert np.argmax(dbs.blob_to_embedding(neg.embedding)) == 2

    with dbs.get_session() as s:
        crop1 = Path(s.get(ReferenceFace, r1).crop_path)
    v = gallery_version()
    assert api.delete(f"/api/references/{r1}").status_code == 204
    assert gallery_version() != v
    assert not crop1.exists()
    assert api.delete(f"/api/references/{r1}").status_code == 404
    assert (tmp_path / "crops" / "face1.jpg").exists()  # the pipeline face crop is untouched


def test_delete_keeps_crop_outside_refs_dir(api, tmp_path, faces):
    faces({})
    kid = make_kid()
    outside = tmp_path / "keep.jpg"
    outside.write_bytes(b"x")
    with dbs.get_session() as s:
        ref = ReferenceFace(
            kid_id=kid,
            embedding=dbs.embedding_to_blob(np.ones(512, dtype=np.float32)),
            source="auto",
            crop_path=str(outside),
        )
        s.add(ref)
        s.flush()
        rid = ref.id
    assert api.get(f"/api/references/{rid}/crop").status_code == 404  # outside crops dir
    assert api.delete(f"/api/references/{rid}").status_code == 204
    assert outside.exists()


def test_temp_crop_route_rejects_bad_ids(api, faces):
    faces({})
    assert api.get("/api/temp-crops/..%2F..%2Fkino.db").status_code in (404, 422)
    assert api.get("/api/temp-crops/" + "a" * 32).status_code == 404


# --- coverage --------------------------------------------------------------------------------


def _ref(kid, yaw=None, photo_date=None):
    with dbs.get_session() as s:
        s.add(
            ReferenceFace(
                kid_id=kid,
                embedding=dbs.embedding_to_blob(np.ones(512, dtype=np.float32)),
                source="upload",
                yaw=yaw,
                photo_date=photo_date,
            )
        )


def test_coverage_hints(api, faces):
    faces({})
    kid = make_kid()
    url = f"/api/kids/{kid}/coverage"
    c = api.get(url).json()
    assert c["count"] == 0 and c["recommended"] == 10
    assert c["hints"] == ["Add 10 more references (recommended minimum is 10)"]
    old = datetime.now() - timedelta(days=500)
    for _ in range(10):
        _ref(kid, yaw=2.0, photo_date=old)
    c = api.get(url).json()
    assert c["count"] == 10 and c["frontal"] == 10 and c["yaw_min"] == 2.0
    assert c["hints"] == [
        "Add side angles",
        "Most references are over a year old — add recent photos.",
    ]
    for y in (-30.0, 30.0):
        for _ in range(6):
            _ref(kid, yaw=y, photo_date=datetime.now())
    c = api.get(url).json()
    assert c["left"] == 6 and c["right"] == 6 and c["older_than_year"] == 10
    assert c["hints"] == []
    assert api.get("/api/kids/999/coverage").status_code == 404


# --- review fixes ------------------------------------------------------------------------


def test_upload_mpo_accepted(api, faces):
    kid = make_kid()
    faces({400: [det(100, 100, 120)]})
    rng = np.random.default_rng(9)
    ims = [
        PILImage.fromarray(rng.integers(0, 255, (300, 400, 3), dtype=np.uint8)) for _ in range(2)
    ]
    buf = io.BytesIO()
    ims[0].save(buf, "MPO", save_all=True, append_images=ims[1:])
    r = upload(api, kid, ("samsung.jpg", buf.getvalue()))
    assert r.status_code == 200, r.text
    assert r.json()[0]["error"] is None and len(r.json()[0]["faces"]) == 1


def test_upload_without_content_length_is_411(api, faces):
    kid = make_kid()
    faces({})

    def body():
        yield b"--x\r\n"

    r = api.post(
        f"/api/kids/{kid}/uploads",
        content=body(),
        headers={"content-type": "multipart/form-data; boundary=x"},
    )
    assert r.status_code == 411


def test_upload_request_cap_is_100_mb(api, faces):
    from kino.api.routes import uploads

    assert uploads.MAX_REQUEST_BYTES == 100 * 1024 * 1024
    kid = make_kid()
    faces({})
    r = api.post(
        f"/api/kids/{kid}/uploads",
        content=b"x",
        headers={
            "content-type": "multipart/form-data; boundary=x",
            "content-length": str(100 * 1024 * 1024 + 1),
        },
    )
    assert r.status_code == 413


def test_upload_processes_files_one_at_a_time(api, faces, monkeypatch):
    """Each file is read, processed and released before the next is read."""
    from kino.api.routes import uploads

    kid = make_kid()
    faces({400: [], 410: []})
    calls: list[int] = []
    real = uploads.svc.process_upload

    def spy(session, kid_id, name, data, *a, **kw):
        calls.append(len(data))
        return real(session, kid_id, name, data, *a, **kw)

    monkeypatch.setattr(uploads.svc, "process_upload", spy)
    real_process = uploads._process
    batches: list[int] = []

    def one(kid_id, files, settings):
        batches.append(len(files))
        return real_process(kid_id, files, settings)

    monkeypatch.setattr(uploads, "_process", one)
    r = upload(api, kid, ("a.jpg", photo(400)), ("b.jpg", photo(410)))
    assert r.status_code == 200, r.text
    assert len(calls) == 2 and batches == [1, 1]


def test_temp_crops_survive_a_failed_confirm(api, faces, monkeypatch):
    from kino.api.routes import references

    kid = make_kid()
    [f] = _uploaded(api, kid, {400: [det(100, 100, 120)]}, 400)
    with dbs.get_session() as s:
        crop = Path(s.get(TempFace, f["temp_face_id"]).crop_path)

    def boom(_r):
        raise RuntimeError("late failure")

    monkeypatch.setattr(references, "_out", boom)
    with pytest.raises(RuntimeError):
        api.post(f"/api/kids/{kid}/references", json={"temp_face_ids": [f["temp_face_id"]]})
    assert crop.exists() and count(TempFace) == 1 and count(ReferenceFace) == 0


def test_reference_crop_survives_a_failed_to_negative(api, faces, tmp_path, monkeypatch):
    from kino.api.routes import references

    faces({})
    kid = make_kid()
    fid = _pipeline_face(tmp_path)
    [ref] = api.post(f"/api/kids/{kid}/references", json={"face_ids": [fid]}).json()["created"]
    with dbs.get_session() as s:
        crop = Path(s.get(ReferenceFace, ref["id"]).crop_path)

    def boom(**_kw):
        raise RuntimeError("late failure")

    monkeypatch.setattr(references, "NegativeOut", boom)
    with pytest.raises(RuntimeError):
        api.post(f"/api/references/{ref['id']}/to-negative")
    assert crop.exists() and count(NegativeFace) == 0 and count(ReferenceFace) == 1


def test_tz_aware_photo_dates_are_stored_as_utc(api, faces, tmp_path):
    kid = make_kid()
    [f] = _uploaded(api, kid, {400: [det(100, 100, 120)]}, 400)
    r = api.post(
        f"/api/kids/{kid}/references",
        json={"temp_face_ids": [f["temp_face_id"]], "photo_date": "2023-07-01T23:30:00+02:00"},
    )
    ref = r.json()["created"][0]
    assert ref["photo_date"] == "2023-07-01T21:30:00"
    r = api.patch(f"/api/references/{ref['id']}", json={"photo_date": "2023-01-01T00:30:00+01:00"})
    assert r.json()["photo_date"] == "2022-12-31T23:30:00"


def test_move_reference_conflicts_with_same_face(api, faces, tmp_path):
    faces({})
    noa, itai = make_kid(), make_kid("Itai", "itai")
    fid = _pipeline_face(tmp_path)
    a = api.post(f"/api/kids/{noa}/references", json={"face_ids": [fid]}).json()["created"][0]
    api.post(f"/api/kids/{itai}/references", json={"face_ids": [fid]})
    r = api.patch(f"/api/references/{a['id']}", json={"kid_id": itai})
    assert r.status_code == 409
    assert api.get(f"/api/references/{a['id']}").json()["kid_id"] == noa
