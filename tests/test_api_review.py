"""REST: review queue, decisions, saved photos and face crops (CLAUDE.md §9.8, §9.9, §11.4, §12).

No face images are committed: photos are random-noise JPEGs and embeddings are synthetic
unit vectors.
"""

from __future__ import annotations

import io
import os
import threading
from datetime import datetime
from pathlib import Path

import numpy as np
import pytest
from PIL import Image as PILImage
from sqlalchemy import func, select

from kino.config import get_settings
from kino.db import session as dbs
from kino.db.models import (
    Face,
    Forward,
    Group,
    Image,
    Kid,
    Match,
    NegativeFace,
    ReferenceFace,
    SavedPhoto,
    Setting,
)
from kino.services import review as review_svc

SENT = datetime(2026, 5, 6, 7, 8, 9)
DEST = "972500000000@c.us"


def unit(i: int, j: int | None = None, w: float = 0.0) -> np.ndarray:
    v = np.zeros(512, dtype=np.float32)
    v[i] = 1.0
    if j is not None:
        v[j] = w
    return v / np.linalg.norm(v)


def jpeg(seed: int = 1) -> bytes:
    arr = np.random.default_rng(seed).integers(0, 255, (300, 400, 3), dtype=np.uint8)
    buf = io.BytesIO()
    PILImage.fromarray(arr).save(buf, "JPEG")
    return buf.getvalue()


def data_dir() -> Path:
    return get_settings().data_dir


def photos_dir() -> Path:
    return get_settings().photos_dir


@pytest.fixture
def enqueued(monkeypatch):
    calls: list[int] = []
    monkeypatch.setattr(review_svc, "enqueue_forward", calls.append)
    return calls


def seed_group(chat="120363000000000001@g.us", name="Class 3B", slug="class-3b") -> int:
    with dbs.get_session() as s:
        g = Group(chat_id=chat, name=name, slug=slug, watched=True)
        s.add(g)
        s.flush()
        return g.id


def seed_kid(slug: str, name: str, refs=(), active=True) -> int:
    with dbs.get_session() as s:
        k = Kid(display_name=name, slug=slug, active=active)
        s.add(k)
        s.flush()
        for n, vec in enumerate(refs):
            crop = data_dir() / "crops" / "refs" / f"{slug}_{n}.jpg"
            crop.parent.mkdir(parents=True, exist_ok=True)
            crop.write_bytes(jpeg(100 + n))
            s.add(
                ReferenceFace(
                    kid_id=k.id,
                    embedding=dbs.embedding_to_blob(vec),
                    crop_path=str(crop),
                    source="upload",
                    quality=1.0,
                    status="active",
                )
            )
        return k.id


def seed_image(group_id: int | None, msg="m1", raw=True) -> int:
    with dbs.get_session() as s:
        img = Image(
            message_id=msg, group_id=group_id, sent_at=SENT, sender_name="Dana", status="done"
        )
        s.add(img)
        s.flush()
        if raw:
            p = data_dir() / "raw" / "2026" / "05" / f"{img.id}.jpg"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(jpeg(img.id))
            img.raw_path = str(p)
        return img.id


def seed_face(image_id: int, vec, size=120.0, quality=0.8, n=0) -> int:
    with dbs.get_session() as s:
        crop = data_dir() / "crops" / "2026" / "05" / f"{image_id}_{n}.jpg"
        crop.parent.mkdir(parents=True, exist_ok=True)
        crop.write_bytes(jpeg(200 + image_id * 10 + n))
        f = Face(
            image_id=image_id,
            bbox_x=10.0 * n,
            bbox_y=20.0,
            bbox_w=size,
            bbox_h=size,
            det_score=0.9,
            size_px=size,
            blur_score=500.0,
            yaw=0.0,
            pitch=0.0,
            quality=quality,
            embedding=None if vec is None else dbs.embedding_to_blob(vec),
            crop_path=str(crop),
        )
        s.add(f)
        s.flush()
        return f.id


def seed_match(face_id: int, kid_id: int, decision="review", score=0.4) -> int:
    with dbs.get_session() as s:
        m = Match(
            face_id=face_id,
            kid_id=kid_id,
            score=score,
            margin=0.1,
            method="face",
            decision=decision,
            decided_by="system",
        )
        s.add(m)
        s.flush()
        return m.id


def enable_forwarding(include_review=True):
    with dbs.get_session() as s:
        s.merge(Setting(key="forward.enabled", value=True))
        s.merge(Setting(key="forward.default_chat_id", value=DEST))
        s.merge(Setting(key="forward.include_review_confirmed", value=include_review))


def get(model, ident):
    with dbs.get_session() as s:
        return s.get(model, ident)


def rows(model, *where):
    with dbs.get_session() as s:
        return list(s.scalars(select(model).where(*where).order_by(model.id)))


def count(model, *where) -> int:
    with dbs.get_session() as s:
        return s.scalar(select(func.count()).select_from(model).where(*where)) or 0


def gallery_version():
    row = get(Setting, "gallery_version")
    return None if row is None else row.value


@pytest.fixture
def world(api, enqueued):
    """One watched group, kids Noa (2 refs) and Itai (1 ref), and one image with a tier-L
    face of Noa in review."""
    gid = seed_group()
    noa = seed_kid("noa", "Noa", refs=[unit(0), unit(0, 1, 1.0)])
    itai = seed_kid("itai", "איתי", refs=[unit(5)])
    img = seed_image(gid)
    face = seed_face(img, unit(0, 2, 0.3))
    mid = seed_match(face, noa)
    return {"api": api, "gid": gid, "noa": noa, "itai": itai, "img": img, "face": face, "m": mid}


def act(api, mid, action, **kw):
    return api.post(f"/api/review/{mid}", json={"action": action, **kw})


# --- auth -------------------------------------------------------------------------------


def test_review_routes_require_auth_and_csrf(world):
    api = world["api"]
    api.headers.pop("X-CSRF-Token")
    assert act(api, world["m"], "confirm").status_code == 403
    assert (
        api.post("/api/review/bulk", json={"match_ids": [1], "action": "reject"}).status_code == 403
    )
    assert api.delete("/api/photos/1").status_code == 403
    api.cookies.clear()
    for path in ("/api/review", "/api/review/count", "/api/photos", "/api/crops/1"):
        assert api.get(path).status_code == 401, path
    assert get(Match, world["m"]).decision == "review"


# --- listing ----------------------------------------------------------------------------


def test_list_review_item_fields_and_nearest_refs(world):
    r = world["api"].get("/api/review")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["next_cursor"] is None
    [it] = body["items"]
    assert it["match_id"] == world["m"] and it["face_id"] == world["face"]
    assert it["kid_id"] == world["noa"] and it["kid_name"] == "Noa"
    assert it["tier"] == "L" and it["method"] == "face"
    assert it["group_name"] == "Class 3B" and it["sent_at"].startswith("2026-05-06T07:08:09")
    assert it["crop_url"] == f"/api/crops/{world['face']}"
    assert it["image_url"] == f"/api/images/{world['img']}/file"
    assert it["bbox"] == {"x": 0.0, "y": 20.0, "w": 120.0, "h": 120.0}
    refs = it["nearest_refs"]
    assert len(refs) == 2  # Noa has only two
    assert refs[0]["similarity"] >= refs[1]["similarity"]
    assert all(r["crop_url"].startswith("/api/references/") for r in refs)


def test_list_review_nearest_refs_capped_at_three(world):
    with dbs.get_session() as s:
        for i in range(4):
            s.add(
                ReferenceFace(
                    kid_id=world["noa"],
                    embedding=dbs.embedding_to_blob(unit(0, 10 + i, 0.5 + i)),
                    source="upload",
                    status="active",
                )
            )
    [it] = world["api"].get("/api/review").json()["items"]
    assert len(it["nearest_refs"]) == 3
    sims = [r["similarity"] for r in it["nearest_refs"]]
    assert sims == sorted(sims, reverse=True)


def test_image_url_null_when_no_file_stored(world):
    with dbs.get_session() as s:
        img = s.get(Image, world["img"])
        Path(img.raw_path).unlink()
    [it] = world["api"].get("/api/review").json()["items"]
    assert it["image_url"] is None  # R4: no 500, no dead link


def test_list_review_pagination_filters_and_count(world):
    api = world["api"]
    img2 = seed_image(world["gid"], msg="m2")
    f2 = seed_face(img2, unit(5), n=1)
    m2 = seed_match(f2, world["itai"])
    f3 = seed_face(img2, unit(0), n=2)
    m3 = seed_match(f3, world["noa"])
    seed_match(seed_face(img2, unit(7), n=3), world["noa"], decision="auto_saved")
    p1 = api.get("/api/review?limit=2").json()
    assert [i["match_id"] for i in p1["items"]] == [world["m"], m2]
    assert p1["next_cursor"] == m2
    p2 = api.get(f"/api/review?limit=2&cursor={p1['next_cursor']}").json()
    assert [i["match_id"] for i in p2["items"]] == [m3] and p2["next_cursor"] is None
    only = api.get(f"/api/review?kid_id={world['itai']}").json()["items"]
    assert [i["match_id"] for i in only] == [m2]
    assert api.get("/api/review?method=burst").json()["items"] == []
    assert api.get("/api/review?method=bogus").status_code == 422
    c = api.get("/api/review/count").json()
    assert c == {"total": 3, "by_kid": {str(world["noa"]): 2, str(world["itai"]): 1}}


# --- confirm ----------------------------------------------------------------------------


def test_confirm_saves_forwards_enrols_and_cleans_raw(world, enqueued):
    enable_forwarding()
    v0 = gallery_version()
    raw = Path(get(Image, world["img"]).raw_path)
    r = act(world["api"], world["m"], "confirm")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["changed"] is True and body["decision"] == "confirmed"
    m = get(Match, world["m"])
    assert (m.decision, m.decided_by) == ("confirmed", "user") and m.decided_at is not None
    [sp] = rows(SavedPhoto)
    assert body["saved_photo_id"] == sp.id and sp.kid_id == world["noa"]
    path = Path(sp.path)
    assert path.is_file() and path.is_relative_to(photos_dir() / "noa")
    [ref] = rows(ReferenceFace, ReferenceFace.source == "review")
    assert body["reference_ids"] == [ref.id] and ref.face_id == world["face"]
    assert ref.crop_path and Path(ref.crop_path).is_file()
    assert gallery_version() != v0
    [fw] = rows(Forward)
    assert fw.dest_chat_id == DEST and fw.kid_ids == [world["noa"]] and fw.status == "pending"
    assert enqueued == [fw.id] and body["forward_ids"] == [fw.id]
    assert not raw.exists() and get(Image, world["img"]).raw_path is None


def test_confirm_keeps_raw_while_other_review_pending(world):
    f2 = seed_face(world["img"], unit(5), n=1)
    seed_match(f2, world["itai"])
    assert act(world["api"], world["m"], "confirm").status_code == 200
    assert get(Image, world["img"]).raw_path is not None


def test_confirm_without_review_forwarding(world, enqueued):
    enable_forwarding(include_review=False)
    assert act(world["api"], world["m"], "confirm").status_code == 200
    assert count(Forward) == 0 and enqueued == []


def test_confirm_never_forwards_to_watched_group(world, enqueued):
    enable_forwarding()
    with dbs.get_session() as s:
        s.merge(Setting(key="forward.default_chat_id", value="120363000000000001@g.us"))
    assert act(world["api"], world["m"], "confirm").status_code == 200
    assert count(Forward) == 0 and enqueued == []
    assert count(SavedPhoto) == 1


def test_confirm_small_face_saves_without_reference(world):
    img = seed_image(world["gid"], msg="m2")
    f = seed_face(img, unit(0), size=30.0)
    mid = seed_match(f, world["noa"])
    v0 = gallery_version()
    r = act(world["api"], mid, "confirm")
    assert r.status_code == 200 and r.json()["reference_ids"] == []
    assert count(ReferenceFace, ReferenceFace.source == "review") == 0
    assert gallery_version() == v0
    assert count(SavedPhoto) == 1


def test_confirm_low_quality_face_saves_without_reference(world):
    img = seed_image(world["gid"], msg="m2")
    mid = seed_match(seed_face(img, unit(0), quality=0.2), world["noa"])
    assert act(world["api"], mid, "confirm").json()["reference_ids"] == []


def test_double_confirm_is_idempotent_and_other_action_conflicts(world, enqueued):
    enable_forwarding()
    api = world["api"]
    assert act(api, world["m"], "confirm").status_code == 200
    again = act(api, world["m"], "confirm")
    assert again.status_code == 200 and again.json()["changed"] is False
    assert count(SavedPhoto) == 1 and count(Forward) == 1 and len(enqueued) == 1
    assert count(ReferenceFace, ReferenceFace.source == "review") == 1
    for action, kw in (
        ("reject", {}),
        ("someone_else", {}),
        ("reassign", {"kid_id": world["itai"]}),
    ):
        r = act(api, world["m"], action, **kw)
        assert r.status_code == 409, (action, r.text)
        assert "already decided" in r.json()["detail"]
    assert get(Match, world["m"]).decision == "confirmed"


def test_confirm_falls_back_to_sibling_saved_copy_when_raw_deleted(world):
    """R4: the worker deleted the raw file after saving it for a sibling."""
    sib_face = seed_face(world["img"], unit(5), n=1)
    seed_match(sib_face, world["itai"], decision="auto_saved")
    sib = photos_dir() / "itai" / "2026" / "05" / "sibling.jpg"
    sib.parent.mkdir(parents=True)
    sib.write_bytes(jpeg(9))
    with dbs.get_session() as s:
        s.add(SavedPhoto(image_id=world["img"], kid_id=world["itai"], path=str(sib)))
        img = s.get(Image, world["img"])
        Path(img.raw_path).unlink()
        img.raw_path = None
    r = act(world["api"], world["m"], "confirm")
    assert r.status_code == 200, r.text
    sp = get(SavedPhoto, r.json()["saved_photo_id"])
    assert os.path.samefile(sp.path, sib)


def test_confirm_without_any_stored_copy_is_409_and_stays_in_review(world):
    with dbs.get_session() as s:
        img = s.get(Image, world["img"])
        Path(img.raw_path).unlink()
    r = act(world["api"], world["m"], "confirm")
    assert r.status_code == 409 and "no longer stored" in r.json()["detail"]
    assert get(Match, world["m"]).decision == "review"
    assert count(SavedPhoto) == 0 and count(ReferenceFace, ReferenceFace.source == "review") == 0


def test_confirm_for_inactive_kid_is_409(world):
    with dbs.get_session() as s:
        s.get(Kid, world["noa"]).active = False
    r = act(world["api"], world["m"], "confirm")
    assert r.status_code == 409 and "inactive" in r.json()["detail"]
    assert get(Match, world["m"]).decision == "review"


def test_confirm_after_kid_deleted_match_gone_is_404(world):
    """Deleting a kid cascades its matches: the review item itself is gone."""
    assert world["api"].delete(f"/api/kids/{world['noa']}").status_code == 200
    assert act(world["api"], world["m"], "confirm").status_code == 404


def test_reassign_to_deleted_kid_is_409(world):
    gone = seed_kid("gone", "Gone")
    assert world["api"].delete(f"/api/kids/{gone}").status_code == 200
    r = act(world["api"], world["m"], "reassign", kid_id=gone)
    assert r.status_code == 409 and "deleted" in r.json()["detail"]
    assert get(Match, world["m"]).decision == "review"


def test_reassign_kid_deleted_between_check_and_update_is_409(world, monkeypatch):
    """The FK error of the conditional UPDATE maps to the same 409."""
    monkeypatch.setattr(review_svc, "_kid_exists", lambda s, kid_id: True)
    r = act(world["api"], world["m"], "reassign", kid_id=99999)
    assert r.status_code == 409 and "deleted" in r.json()["detail"]
    assert get(Match, world["m"]).decision == "review"


def test_save_failure_rolls_back_decision(world, monkeypatch):
    from kino.pipeline import save as save_mod

    def boom(*a, **k):
        dest = save_mod.build_dest_path(a[1][0], a[2], a[3], a[4], timezone=k["timezone"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(save_mod, "save_for_kids", boom)
    r = act(world["api"], world["m"], "confirm")
    assert r.status_code == 502, r.text
    assert get(Match, world["m"]).decision == "review"
    assert count(SavedPhoto) == 0 and count(ReferenceFace, ReferenceFace.source == "review") == 0
    assert not any(p.is_file() for p in photos_dir().rglob("*"))


def test_reference_crop_removed_when_commit_fails(world, monkeypatch):
    from kino.services import review

    def explode(*a, **k):
        raise RuntimeError("late failure")

    monkeypatch.setattr(review, "bump_gallery", explode)  # after files exist: reassign bumps last
    refs_before = set((data_dir() / "crops" / "refs").iterdir())
    with pytest.raises(RuntimeError):
        review.apply_decision(get_settings(), world["m"], "reassign", world["itai"])
    assert get(Match, world["m"]).decision == "review"
    assert not any(p.is_file() for p in photos_dir().rglob("*"))
    assert set((data_dir() / "crops" / "refs").iterdir()) == refs_before


# --- reject / someone else / reassign -----------------------------------------------------


def test_reject(world):
    v0 = gallery_version()
    r = act(world["api"], world["m"], "reject")
    assert r.status_code == 200 and r.json()["decision"] == "rejected"
    assert (get(Match, world["m"]).decided_by) == "user"
    assert count(SavedPhoto) == 0 and count(NegativeFace) == 0 and gallery_version() == v0
    assert act(world["api"], world["m"], "reject").json()["changed"] is False
    assert act(world["api"], world["m"], "confirm").status_code == 409


def test_someone_else_adds_negative_and_bumps(world):
    v0 = gallery_version()
    r = act(world["api"], world["m"], "someone_else")
    assert r.status_code == 200, r.text
    [neg] = rows(NegativeFace)
    assert r.json()["negative_id"] == neg.id
    assert neg.kid_id == world["noa"] and neg.source_face_id == world["face"]
    assert neg.crop_path and Path(neg.crop_path).is_file()
    assert get(Match, world["m"]).decision == "rejected"
    assert gallery_version() != v0
    again = act(world["api"], world["m"], "someone_else")
    assert again.status_code == 200 and again.json()["changed"] is False
    assert count(NegativeFace) == 1


def test_reassign_saves_for_other_kid(world):
    v0 = gallery_version()
    r = act(world["api"], world["m"], "reassign", kid_id=world["itai"])
    assert r.status_code == 200, r.text
    m = get(Match, world["m"])
    assert (m.kid_id, m.decision) == (world["itai"], "confirmed")
    [sp] = rows(SavedPhoto)
    assert sp.kid_id == world["itai"] and "/itai/" in sp.path
    assert gallery_version() != v0
    same = act(world["api"], world["m"], "reassign", kid_id=world["itai"])
    assert same.status_code == 200 and same.json()["changed"] is False
    assert act(world["api"], world["m"], "confirm").json()["changed"] is False


def test_reassign_rescores_for_new_kid(world):
    assert act(world["api"], world["m"], "reassign", kid_id=world["itai"]).status_code == 200
    m = get(Match, world["m"])
    face = np.frombuffer(get(Face, world["face"]).embedding, dtype=np.float32)
    assert m.score == pytest.approx(float(face @ unit(5)))  # Itai's only reference
    assert m.margin is None


def test_reassign_validation(world):
    api = world["api"]
    assert act(api, world["m"], "reassign").status_code == 422
    assert act(api, world["m"], "confirm", kid_id=world["itai"]).status_code == 422
    assert act(api, world["m"], "reassign", kid_id=99999).status_code == 409
    assert act(api, world["m"], "maybe").status_code == 422
    assert get(Match, world["m"]).decision == "review"
    assert act(api, 99999, "confirm").status_code == 404


def test_one_face_per_kid_per_photo(world):
    """Itai is already saved from another face of this photo: no second face for him."""
    f2 = seed_face(world["img"], unit(5), n=1)
    seed_match(f2, world["itai"], decision="auto_saved")
    r = act(world["api"], world["m"], "reassign", kid_id=world["itai"])
    assert r.status_code == 409 and "another face" in r.json()["detail"]
    assert get(Match, world["m"]).decision == "review"


def test_confirm_joins_sibling_pending_forward(world, enqueued):
    enable_forwarding()
    f2 = seed_face(world["img"], unit(5), n=1)
    seed_match(f2, world["itai"], decision="auto_saved")
    with dbs.get_session() as s:
        s.add(Forward(image_id=world["img"], dest_chat_id=DEST, kid_ids=[world["itai"]]))
    assert act(world["api"], world["m"], "confirm").status_code == 200
    [fw] = rows(Forward)
    assert fw.kid_ids == [world["itai"], world["noa"]] and "Noa" in (fw.caption or "")


def test_confirm_after_sibling_forward_sent_is_not_forwarded_again(world, enqueued):
    enable_forwarding()
    f2 = seed_face(world["img"], unit(5), n=1)
    seed_match(f2, world["itai"], decision="auto_saved")
    with dbs.get_session() as s:
        s.add(
            Forward(
                image_id=world["img"], dest_chat_id=DEST, kid_ids=[world["itai"]], status="sent"
            )
        )
    r = act(world["api"], world["m"], "confirm")
    assert r.status_code == 200 and r.json()["forward_ids"] == []
    [fw] = rows(Forward)
    assert fw.status == "sent" and fw.kid_ids == [world["itai"]] and enqueued == []


def test_list_review_query_count_is_constant(world):
    from sqlalchemy import event

    def queries() -> int:
        n = [0]

        def hit(*a, **k):
            n[0] += 1

        engine = dbs.get_engine()
        event.listen(engine, "before_cursor_execute", hit)
        try:
            assert world["api"].get("/api/review").status_code == 200
        finally:
            event.remove(engine, "before_cursor_execute", hit)
        return n[0]

    one = queries()
    for n in range(6):
        img = seed_image(world["gid"], msg=f"q{n}")
        seed_match(seed_face(img, unit(0)), world["noa"])
    assert queries() == one


# --- bulk ---------------------------------------------------------------------------------


def test_bulk_mixed_results(world):
    api = world["api"]
    img2 = seed_image(world["gid"], msg="m2")
    m2 = seed_match(seed_face(img2, unit(5)), world["itai"])
    done = seed_match(seed_face(img2, unit(9), n=1), world["noa"], decision="dropped")
    r = api.post(
        "/api/review/bulk",
        json={"match_ids": [world["m"], 99999, m2, done, m2], "action": "confirm"},
    )
    assert r.status_code == 200, r.text
    res = {x["match_id"]: x for x in r.json()["results"]}
    assert len(r.json()["results"]) == 4  # duplicates collapsed
    assert res[world["m"]]["status"] == 200 and res[m2]["status"] == 200
    assert res[99999]["status"] == 404 and res[done]["status"] == 409
    assert count(SavedPhoto) == 2


def test_bulk_limits(world):
    api = world["api"]
    too_many = list(range(1, 202))
    assert (
        api.post("/api/review/bulk", json={"match_ids": too_many, "action": "reject"}).status_code
        == 422
    )
    assert (
        api.post("/api/review/bulk", json={"match_ids": [], "action": "reject"}).status_code == 422
    )
    r = api.post("/api/review/bulk", json={"match_ids": [world["m"]], "action": "someone_else"})
    assert r.status_code == 422


def test_concurrent_confirms_apply_exactly_once(world, enqueued):
    enable_forwarding()
    ids = [world["m"]]
    for n in range(4):
        img = seed_image(world["gid"], msg=f"c{n}")
        ids.append(seed_match(seed_face(img, unit(0, 20 + n, 0.2)), world["noa"]))
    settings = get_settings()
    results: list[list[str]] = [[], [], [], []]
    barrier = threading.Barrier(4)

    def worker(slot: int) -> None:
        barrier.wait()
        for mid in ids:
            try:
                r = review_svc.apply_decision(settings, mid, "confirm")
                results[slot].append("changed" if r.changed else "same")
            except Exception as e:  # pragma: no cover - surfaced by the asserts
                results[slot].append(type(e).__name__)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    flat = [x for r in results for x in r]
    assert flat.count("changed") == len(ids) and flat.count("same") == 3 * len(ids), flat
    assert count(SavedPhoto) == len(ids) and count(Forward) == len(ids)
    assert len(enqueued) == len(ids)
    assert all(m.decision == "confirmed" for m in rows(Match))


# --- photos -------------------------------------------------------------------------------


def _confirm_two(world):
    enable_forwarding()
    f2 = seed_face(world["img"], unit(5), n=1)
    m2 = seed_match(f2, world["itai"])
    assert act(world["api"], world["m"], "confirm").status_code == 200
    assert act(world["api"], m2, "confirm").status_code == 200
    return m2


def test_list_photos_filters_and_pagination(world):
    api = world["api"]
    _confirm_two(world)
    g2 = seed_group(chat="120363000000000002@g.us", name="Dance", slug="dance")
    img2 = seed_image(g2, msg="m2")
    m3 = seed_match(seed_face(img2, unit(0)), world["noa"])
    assert act(api, m3, "confirm").status_code == 200
    all_ = api.get("/api/photos").json()
    assert len(all_["items"]) == 2 and all_["next_cursor"] is None
    ids = [p["id"] for p in all_["items"]]
    assert ids == sorted(ids, reverse=True)
    first = all_["items"][0]
    assert first["file_url"] == f"/api/images/{img2}/file" and first["group_name"] == "Dance"
    assert len(api.get(f"/api/photos?kid_id={world['noa']}").json()["items"]) == 2
    assert len(api.get(f"/api/photos?kid_id={world['itai']}").json()["items"]) == 1
    assert len(api.get(f"/api/photos?group_id={g2}").json()["items"]) == 1
    assert len(api.get("/api/photos?from=2026-05-07T00:00:00").json()["items"]) == 0
    assert len(api.get("/api/photos?to=2026-05-07T00:00:00Z").json()["items"]) == 2
    p1 = api.get("/api/photos?limit=1").json()
    assert len(p1["items"]) == 1 and p1["next_cursor"] == p1["items"][-1]["id"]
    p2 = api.get(f"/api/photos?limit=1&cursor={p1['next_cursor']}").json()
    assert len(p2["items"]) == 1 and p2["next_cursor"] is None


def test_delete_photo_keeps_sibling_forward_and_rejects_match(world, enqueued):
    api = world["api"]
    _confirm_two(world)
    [fw] = rows(Forward)
    assert sorted(fw.kid_ids) == sorted([world["noa"], world["itai"]])
    noa_sp = rows(SavedPhoto, SavedPhoto.kid_id == world["noa"])[0]
    itai_sp = rows(SavedPhoto, SavedPhoto.kid_id == world["itai"])[0]
    v0 = gallery_version()
    r = api.delete(f"/api/photos/{noa_sp.id}")
    assert r.status_code == 200, r.text
    assert gallery_version() != v0
    assert r.json() == {
        "id": noa_sp.id,
        "removed_files": 1,
        "cancelled_forwards": 0,
        "rejected_matches": 1,
        "retired_references": 1,
    }
    assert not Path(noa_sp.path).exists() and Path(itai_sp.path).exists()
    fw = get(Forward, fw.id)
    assert fw.status == "pending" and fw.kid_ids == [world["itai"]]
    assert fw.file_path == itai_sp.path
    m = get(Match, world["m"])
    assert (m.decision, m.decided_by) == ("rejected", "user")
    assert api.delete(f"/api/photos/{noa_sp.id}").status_code == 404
    # Noa's review reference from this photo is retired (Itai's face duplicated his upload ref)
    [ref] = rows(ReferenceFace, ReferenceFace.source == "review")
    assert (ref.kid_id, ref.status) == (world["noa"], "retired")
    assert all(r.status == "active" for r in rows(ReferenceFace, ReferenceFace.source == "upload"))
    r = api.delete(f"/api/photos/{itai_sp.id}")
    assert r.json()["cancelled_forwards"] == 1
    assert get(Forward, fw.id).status == "cancelled"
    assert not any(p.is_file() for p in photos_dir().rglob("*"))


def test_delete_photo_outside_photos_dir_leaves_file(world, tmp_path):
    outside = tmp_path / "elsewhere.jpg"
    outside.write_bytes(b"x")
    with dbs.get_session() as s:
        s.add(SavedPhoto(image_id=world["img"], kid_id=world["noa"], path=str(outside)))
    sp = rows(SavedPhoto)[0]
    r = world["api"].delete(f"/api/photos/{sp.id}")
    assert r.status_code == 200 and r.json()["removed_files"] == 0
    assert outside.exists() and count(SavedPhoto) == 0


# --- crops --------------------------------------------------------------------------------


def test_face_crop_route(world, tmp_path):
    api = world["api"]
    r = api.get(f"/api/crops/{world['face']}")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"
    assert r.content[:2] == b"\xff\xd8"
    assert api.get("/api/crops/99999").status_code == 404
    outside = tmp_path / "secret.jpg"
    outside.write_bytes(b"secret")
    with dbs.get_session() as s:
        s.get(Face, world["face"]).crop_path = str(data_dir() / "crops" / ".." / "kino.db")
    assert api.get(f"/api/crops/{world['face']}").status_code == 404
    with dbs.get_session() as s:
        s.get(Face, world["face"]).crop_path = str(outside)
    assert api.get(f"/api/crops/{world['face']}").status_code == 404
