import os
import urllib.request
from pathlib import Path

import cv2
import numpy as np
import pytest

from kino.config import Settings
from kino.pipeline import embed as embed_mod
from kino.pipeline.detect import Detection
from kino.pipeline.embed import (
    OnnxEmbedder,
    align_face,
    make_crop,
    should_embed,
)
from kino.pipeline.quality import Tier, estimate_pose, face_quality, tier_for

KPS = np.array([[30, 40], [70, 40], [50, 60], [35, 80], [65, 80]], dtype=np.float32)


def det(size=100.0, kps=KPS):
    return Detection(bbox=(0.0, 0.0, size, size), score=0.9, kps=kps.copy())


def sharp_img():
    rng = np.random.default_rng(0)
    return rng.integers(0, 255, (112, 112, 3), dtype=np.uint8)


@pytest.mark.parametrize(
    ("size", "tier"),
    [(80, Tier.L), (79, Tier.M), (40, Tier.M), (39, Tier.S), (24, Tier.S), (23, Tier.XS)],
)
def test_tier_boundaries(size, tier):
    assert tier_for(size) is tier


def test_should_embed():
    assert not should_embed(Tier.XS)
    assert all(should_embed(t) for t in (Tier.L, Tier.M, Tier.S))


def test_blur_lower_when_blurred():
    s = sharp_img()
    b = cv2.GaussianBlur(s, (0, 0), 3)
    assert face_quality(det(), b).blur_score < face_quality(det(), s).blur_score


def test_yaw_symmetric_zero_and_sign_flips():
    assert estimate_pose(KPS)[0] == pytest.approx(0.0)
    turned = KPS.copy()
    turned[2, 0] += 8
    mirrored = turned.copy()
    mirrored[:, 0] = 100 - mirrored[:, 0]
    mirrored = mirrored[[1, 0, 2, 4, 3]]
    yaw, _ = estimate_pose(turned)
    assert yaw > 0
    assert estimate_pose(mirrored)[0] == pytest.approx(-yaw)


def test_pitch_sensible():
    _, frontal = estimate_pose(KPS)
    down = KPS.copy()
    down[2, 1] += 8
    up = KPS.copy()
    up[2, 1] -= 8
    assert estimate_pose(down)[1] > frontal > estimate_pose(up)[1]


def test_quality_monotonic_and_bounded():
    s = sharp_img()
    blurred = cv2.GaussianBlur(s, (0, 0), 3)
    assert face_quality(det(30), s).quality < face_quality(det(60), s).quality
    assert face_quality(det(60), s).quality <= face_quality(det(120), s).quality
    assert face_quality(det(), blurred).quality < face_quality(det(), s).quality
    turned = KPS.copy()
    turned[2, 0] += 15
    assert face_quality(det(kps=turned), s).quality < face_quality(det(), s).quality
    extreme = KPS.copy()
    extreme[2] = [500, -500]
    for d in (det(1), det(1000), det(kps=extreme)):
        for img in (s, np.zeros((112, 112, 3), np.uint8), blurred):
            assert 0.0 <= face_quality(d, img).quality <= 1.0


class _FakeSession:
    def get_inputs(self):
        class _I:
            name = "data"

        return [_I()]

    def run(self, _outs, feed):
        x = feed["data"]
        assert x.shape == (1, 3, 112, 112) and x.dtype == np.float32
        rng = np.random.default_rng(1)
        return [rng.normal(size=(1, 512)).astype(np.float32) * 5]


def test_embedder_normalised(monkeypatch):
    monkeypatch.setattr(embed_mod.ort, "InferenceSession", lambda *a, **k: _FakeSession())
    e = OnnxEmbedder("x.onnx", 1)
    v = e.embed(sharp_img())
    assert v.shape == (512,) and v.dtype == np.float32
    assert np.linalg.norm(v) == pytest.approx(1.0, abs=1e-5)


def test_embedder_wrong_dim(monkeypatch):
    class Bad(_FakeSession):
        def run(self, _o, feed):
            return [np.ones((1, 128), np.float32)]

    monkeypatch.setattr(embed_mod.ort, "InferenceSession", lambda *a, **k: Bad())
    with pytest.raises(ValueError, match="512"):
        OnnxEmbedder("x.onnx").embed(sharp_img())


def test_make_crop_size_and_padding():
    img = np.full((200, 300, 3), 255, np.uint8)
    d = Detection(bbox=(0.0, 0.0, 40.0, 40.0), score=0.9, kps=KPS)
    out = cv2.imdecode(np.frombuffer(make_crop(img, d), np.uint8), cv2.IMREAD_COLOR)
    assert out.shape == (160, 160, 3)
    assert out[2, 2].mean() < 30  # top-left corner is padding (black)
    assert out[80, 80].mean() > 200  # face centre is image


def test_make_crop_tiny_image_no_raise():
    img = np.full((5, 5, 3), 90, np.uint8)
    d = Detection(bbox=(-10.0, -10.0, 3.0, 3.0), score=0.9, kps=KPS)
    out = cv2.imdecode(np.frombuffer(make_crop(img, d), np.uint8), cv2.IMREAD_COLOR)
    assert out.shape == (160, 160, 3)


def test_align_face_shape():
    img = np.zeros((200, 200, 3), np.uint8)
    d = Detection(bbox=(0.0, 0.0, 100.0, 100.0), score=0.9, kps=KPS * 1.5)
    assert align_face(img, d).shape == (112, 112, 3)


@pytest.mark.models
def test_real_auraface_similarity(tmp_path_factory):
    from kino.models.download import ensure_model
    from kino.pipeline.detect import ScrfdDetector

    data = Path(os.environ.get("KINO_TEST_DATA_DIR", "") or tmp_path_factory.mktemp("kino-data"))
    data.mkdir(parents=True, exist_ok=True)
    base = "https://upload.wikimedia.org/wikipedia/commons/thumb/"
    urls = {
        "face.jpg": base + "d/d3/Albert_Einstein_Head.jpg/330px-Albert_Einstein_Head.jpg",
        "other.jpg": base + "7/7e/Marie_Curie_c1920.jpg/330px-Marie_Curie_c1920.jpg",
    }
    for name, url in urls.items():
        p = data / name
        if not p.exists():
            req = urllib.request.Request(url, headers={"User-Agent": "kino-tests/0"})
            p.write_bytes(urllib.request.urlopen(req, timeout=30).read())  # noqa: S310
    detector = ScrfdDetector(ensure_model("scrfd_10g", data), 2)
    emb = embed_mod.get_embedder(Settings(data_dir=data))

    def vec(img):
        ds = detector.detect(img, 1024, 2.0)
        assert ds
        return emb.embed(align_face(img, max(ds, key=lambda d: d.score)))

    a = cv2.imread(str(data / "face.jpg"))
    b = cv2.imread(str(data / "other.jpg"))
    assert a is not None and b is not None
    small = cv2.resize(a, None, fx=0.5, fy=0.5, interpolation=cv2.INTER_AREA)
    same = float(vec(a) @ vec(small))
    diff = float(vec(a) @ vec(b))
    print(f"same={same:.3f} diff={diff:.3f}")
    assert same > 0.6 and same > diff + 0.3
