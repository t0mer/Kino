"""REST: Test page dry-run scoring, re-score and "flag wrong" (CLAUDE.md §9.5, §9.6, §11.5).

No detection runs and no face images are committed: embeddings are synthetic unit vectors,
photos are random-noise JPEGs.
"""

from __future__ import annotations

import io
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage
from sqlalchemy import func, select

from kino.config import get_settings
from kino.db import session as dbs
from kino.db.models import (
    Face,
    Group,
    Image,
    Kid,
    Match,
    ReferenceFace,
    Setting,
)
from kino.services import rescore as rescore_svc

NOW = datetime.now(UTC).replace(tzinfo=None)
RECENT = (NOW - timedelta(days=1)).replace(microsecond=0)
DIM = 512


def unit(i: int) -> np.ndarray:
    v = np.zeros(DIM, dtype=np.float32)
    v[i] = 1.0
    return v


def mix(i: int, cos: float, j: int) -> np.ndarray:
    """Unit vector with cosine ``cos`` to unit(i); ``j`` is a dimension used by nothing else."""
    return (cos * unit(i) + np.sqrt(1 - cos**2) * unit(j)).astype(np.float32)


def jpeg(seed: int = 1) -> bytes:
    arr = np.random.default_rng(seed).integers(0, 255, (300, 400, 3), dtype=np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(arr).save(buf, "JPEG")
    return buf.getvalue()


def data_dir() -> Path:
    return get_settings().data_dir


def seed_group() -> int:
    with dbs.get_session() as s:
        g = Group(chat_id="120363000000000001@g.us", name="Class 3B", slug="class-3b", watched=True)
        s.add(g)
        s.flush()
        return g.id


def seed_kid(slug: str, name: str, ref: np.ndarray, auto: bool = True) -> int:
    with dbs.get_session() as s:
        k = Kid(display_name=name, slug=slug, auto_save_enabled=auto)
        s.add(k)
        s.flush()
        s.add(
            ReferenceFace(
                kid_id=k.id,
                embedding=dbs.embedding_to_blob(ref),
                source="upload",
                quality=1.0,
                status="active",
            )
        )
        return k.id


_msg = iter(range(10_000))


def seed_face(
    group_id: int | None,
    vec: np.ndarray | None,
    sent: datetime = RECENT,
    size: float = 120.0,
    raw: bool = True,
    image_id: int | None = None,
) -> int:
    """A face on a new `done` image (or on ``image_id``), with a crop and optionally a raw file."""
    with dbs.get_session() as s:
        if image_id is None:
            img = Image(
                message_id=f"m{next(_msg)}",
                group_id=group_id,
                sent_at=sent,
                sender_name="Dana",
                status="done",
            )
            s.add(img)
            s.flush()
            if raw:
                p = data_dir() / "raw" / "2026" / "10" / f"{img.id}.jpg"
                p.parent.mkdir(parents=True, exist_ok=True)
                p.write_bytes(jpeg(img.id))
                img.raw_path = str(p)
            image_id = img.id
        crop = data_dir() / "crops" / "2026" / "10" / f"{image_id}_{next(_msg)}.jpg"
        crop.parent.mkdir(parents=True, exist_ok=True)
        crop.write_bytes(jpeg(7))
        f = Face(
            image_id=image_id,
            bbox_x=0.0,
            bbox_y=0.0,
            bbox_w=size,
            bbox_h=size,
            det_score=0.9,
            size_px=size,
            blur_score=500.0,
            yaw=0.0,
            pitch=0.0,
            quality=0.9,
            embedding=None if vec is None else dbs.embedding_to_blob(vec),
            crop_path=str(crop),
        )
        s.add(f)
        s.flush()
        return f.id


def image_of(face_id: int) -> int:
    with dbs.get_session() as s:
        face = s.get(Face, face_id)
        assert face is not None
        return face.image_id


def seed_match(face_id: int, kid_id: int, decision: str, by: str = "system") -> int:
    with dbs.get_session() as s:
        m = Match(
            face_id=face_id,
            kid_id=kid_id,
            score=0.3,
            margin=0.1,
            method="face",
            decision=decision,
            decided_by=by,
        )
        s.add(m)
        s.flush()
        return m.id


def count(model, *where) -> int:
    with dbs.get_session() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def matches(face_id: int) -> list[Match]:
    with dbs.get_session() as s:
        return list(s.scalars(select(Match).where(Match.face_id == face_id).order_by(Match.id)))


def gallery_version():
    with dbs.get_session() as s:
        row = s.get(Setting, "gallery_version")
        return None if row is None else row.value


@pytest.fixture
def world(api):
    """Noa (auto-save on, ref unit(0)) and Itai (auto-save off, ref unit(5)); four recent
    single-face photos in an unmapped group (thresholds L: auto 0.50, review 0.37 with the
    +0.05 unmapped penalty): A Noa 0.9, B Noa 0.42, C Noa 0.2, D Itai 0.9."""
    gid = seed_group()
    noa = seed_kid("noa", "Noa", unit(0))
    itai = seed_kid("itai", "איתי", unit(5), auto=False)
    faces = {
        "A": seed_face(gid, mix(0, 0.9, 100), sent=RECENT - timedelta(hours=1)),
        "B": seed_face(gid, mix(0, 0.42, 101), sent=RECENT - timedelta(hours=2)),
        "C": seed_face(gid, mix(0, 0.2, 102), sent=RECENT - timedelta(hours=3)),
        "D": seed_face(gid, mix(5, 0.9, 103), sent=RECENT - timedelta(hours=4)),
    }
    return {"api": api, "gid": gid, "noa": noa, "itai": itai, **faces}


def score(api, **body):
    r = api.post("/api/test/score", json=body)
    assert r.status_code == 200, r.text
    return r.json()


def ids(body, bucket: str) -> set[int]:
    return {it["face_id"] for it in body["buckets"][bucket]["items"]}


def thresholds(api, **tiers):
    t = api.get("/api/settings/thresholds").json()
    t.pop("auto_floor")
    t.update(tiers)
    return t


# --- auth -------------------------------------------------------------------------------


def test_routes_require_auth_and_csrf(world):
    api = world["api"]
    api.headers.pop("X-CSRF-Token")
    assert api.post("/api/test/score", json={}).status_code == 403
    api.cookies.clear()
    assert api.post("/api/test/score", json={}).status_code == 401


# --- dry-run scoring --------------------------------------------------------------------


def test_score_buckets_with_stored_thresholds(world):
    body = score(world["api"])
    assert body["examined"] == 4 and body["truncated"] is False
    assert ids(body, "would_auto_save") == {world["A"]}
    assert ids(body, "would_review") == {world["B"], world["D"]}
    assert ids(body, "would_drop") == {world["C"]}
    assert body["buckets"]["would_review"]["count"] == 2
    [a] = body["buckets"]["would_auto_save"]["items"]
    assert a["kid_id"] == world["noa"] and a["kid_name"] == "Noa"
    assert a["tier"] == "L" and a["score"] == pytest.approx(0.9, abs=1e-4)
    assert a["crop_url"] == f"/api/crops/{world['A']}"
    assert a["group_name"] == "Class 3B" and a["auto_save_disabled"] is False
    assert a["image_id"] == image_of(world["A"])
    d = next(it for it in body["buckets"]["would_review"]["items"] if it["face_id"] == world["D"])
    assert d["auto_save_disabled"] is True and d["kid_name"] == "איתי"
    b = next(it for it in body["buckets"]["would_review"]["items"] if it["face_id"] == world["B"])
    assert b["auto_save_disabled"] is False
    per_kid = {k["kid_id"]: k for k in body["per_kid"]}
    assert per_kid[world["noa"]]["would_auto_save"] == 1
    assert per_kid[world["noa"]]["would_review"] == 1
    assert per_kid[world["noa"]]["would_drop"] == 1
    assert per_kid[world["itai"]]["would_review"] == 1
    assert body["thresholds"]["L"] == {"auto": 0.45, "review": 0.32}
    to = datetime.fromisoformat(body["to"])
    assert to - datetime.fromisoformat(body["from"]) == timedelta(days=14)


def test_threshold_slider_changes_buckets_without_writes(world):
    api = world["api"]
    stricter = thresholds(api, L={"auto": 0.95, "review": 0.45})
    body = score(api, thresholds=stricter)
    assert ids(body, "would_auto_save") == set()
    assert ids(body, "would_review") == {world["A"], world["D"]}
    assert ids(body, "would_drop") == {world["B"], world["C"]}
    looser = thresholds(api, L={"auto": 0.40, "review": 0.10})
    body = score(api, thresholds=looser)
    assert ids(body, "would_auto_save") == {world["A"]}  # B: 0.42 < 0.40 + 0.05
    assert ids(body, "would_review") == {world["B"], world["C"], world["D"]}
    assert ids(body, "would_drop") == set()
    # nothing was written: no matches, thresholds unchanged, no gallery bump
    assert count(Match) == 0
    assert api.get("/api/settings/thresholds").json()["L"] == {"auto": 0.45, "review": 0.32}
    assert gallery_version() is None


def test_threshold_override_validated_like_settings(world):
    api = world["api"]
    bad = thresholds(api, L={"auto": 0.30, "review": 0.40})  # auto below review
    assert api.post("/api/test/score", json={"thresholds": bad}).status_code == 422
    low = thresholds(api, M={"auto": 0.20, "review": 0.10})  # below the auto-save floor
    assert api.post("/api/test/score", json={"thresholds": low}).status_code == 422
    partial = {"L": {"auto": 0.5, "review": 0.3}}
    assert api.post("/api/test/score", json={"thresholds": partial}).status_code == 422
    assert api.post("/api/test/score", json={"nope": 1}).status_code == 422


def test_kid_filter(world):
    body = score(world["api"], kid_ids=[world["itai"]])
    assert ids(body, "would_review") == {world["D"]}
    assert ids(body, "would_auto_save") == ids(body, "would_drop") == set()
    assert [k["kid_id"] for k in body["per_kid"]] == [world["itai"]]


def test_date_range(world):
    api = world["api"]
    old = seed_face(world["gid"], mix(0, 0.9, 110), sent=NOW - timedelta(days=30))
    assert old not in ids(score(api), "would_auto_save")  # default: last 14 days
    frm = (NOW - timedelta(days=40)).isoformat() + "Z"
    to = (NOW - timedelta(days=20)).isoformat() + "Z"
    body = score(api, **{"from": frm, "to": to})
    assert ids(body, "would_auto_save") == {old} and body["examined"] == 1
    r = api.post("/api/test/score", json={"from": to, "to": frm})
    assert r.status_code == 422


def test_truncation(world, monkeypatch):
    monkeypatch.setattr(rescore_svc, "MAX_FACES", 2)
    body = score(world["api"])
    assert body["truncated"] is True and body["examined"] == 2
    # newest first: A and B
    assert ids(body, "would_auto_save") | ids(body, "would_review") == {world["A"], world["B"]}


def test_truncation_never_splits_a_photo(world, monkeypatch):
    img = image_of(world["B"])
    seed_face(None, mix(0, 0.3, 120), image_id=img)  # B's photo now has two faces
    monkeypatch.setattr(rescore_svc, "MAX_FACES", 2)
    body = score(world["api"])
    assert body["truncated"] is True and body["examined"] == 1  # only A's photo fits


def test_one_face_per_kid_per_photo_and_no_candidate_drops(world):
    api = world["api"]
    img = image_of(world["A"])
    twin = seed_face(None, mix(0, 0.8, 130), image_id=img)  # weaker Noa in A's photo
    nobody = seed_face(world["gid"], None)  # no embedding: not scored
    body = score(api)
    assert ids(body, "would_auto_save") == {world["A"]}
    t = next(it for it in body["buckets"]["would_review"]["items"] if it["face_id"] == twin)
    assert "higher-scoring face" in t["reason"]
    assert nobody not in ids(body, "would_drop") and body["examined"] == 5


def test_bucket_caps(world, monkeypatch):
    monkeypatch.setattr(rescore_svc, "BUCKET_CAP", 1)
    body = score(world["api"])
    review = body["buckets"]["would_review"]
    assert review["count"] == 2 and len(review["items"]) == 1
