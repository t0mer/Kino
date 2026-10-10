import os
from pathlib import Path

import numpy as np
import pytest

from kino.config import Settings
from kino.pipeline._insightface import (
    ARCFACE_TEMPLATE,
    distance2bbox,
    distance2kps,
    estimate_norm,
    make_anchors,
    nms,
)
from kino.pipeline.detect import (
    Detection,
    choose_grid,
    decode_outputs,
    detect_image,
    merge_nms,
    needs_tiling,
    tile_grid,
)


def det(x1, y1, x2, y2, score=0.9, from_tile=False):
    kps = np.array([[x1, y1]] * 5, dtype=np.float32) + np.arange(5, dtype=np.float32)[:, None]
    return Detection((x1, y1, x2, y2), score, kps, from_tile)


class FakeDetector:
    """Returns faces given in full-image coords, cropped to what each call sees."""

    def __init__(self, faces, full_size):
        self.faces = faces
        self.full_size = full_size  # (w, h)
        self.base: np.ndarray | None = None
        self.calls: list[tuple[int, int, int]] = []

    def detect(self, img, det_size):
        h, w = img.shape[:2]
        if self.base is None:
            self.base = img
        self.calls.append((w, h, det_size))
        if (w, h) == self.full_size:
            return [Detection(f.bbox, f.score, f.kps.copy(), False) for f in self.faces]
        # tile: img[0,0,0] encodes nothing; locate by marker pixels
        off = img.__array_interface__["data"][0] - self.base.__array_interface__["data"][0]
        y0, x0 = divmod(off, self.base.strides[0])
        x0 //= 3
        out = []
        for f in self.faces:
            b = f.bbox
            if b[0] >= x0 and b[2] <= x0 + w and b[1] >= y0 and b[3] <= y0 + h:
                off = np.array([x0, y0], dtype=np.float32)
                out.append(
                    Detection((b[0] - x0, b[1] - y0, b[2] - x0, b[3] - y0), f.score, f.kps - off)
                )
        return out


def marked_image(w, h):
    img = np.zeros((h, w, 3), dtype=np.uint8)
    # encode tile origin / 100 in pixel (0,0) via per-region fill
    for y in range(0, h, 100):
        for x in range(0, w, 100):
            img[y : y + 100, x : x + 100, 1] = x // 100
            img[y : y + 100, x : x + 100, 2] = y // 100
    return img


def test_tile_grid_covers_with_overlap():
    for w, h, g in [(1000, 800, 2), (3000, 2000, 3), (1600, 1600, 2)]:
        tiles = tile_grid(w, h, g, 0.2)
        assert len(tiles) == g * g
        cover = np.zeros((h, w), dtype=bool)
        for x0, y0, x1, y1 in tiles:
            assert 0 <= x0 < x1 <= w and 0 <= y0 < y1 <= h
            cover[y0:y1, x0:x1] = True
        assert cover.all()
        t0, t1 = tiles[0], tiles[1]
        assert t0[2] - t1[0] > 0  # horizontal overlap
        assert abs((t0[2] - t1[0]) / (t0[2] - t0[0]) - 0.2) < 0.02


def test_choose_grid():
    assert choose_grid(2000, 1000) == 2
    assert choose_grid(2001, 1000) == 3
    assert choose_grid(500, 2500) == 3


@pytest.mark.parametrize(
    "mode,w,h,faces,expected",
    [
        ("off", 5000, 5000, [], False),
        ("always", 100, 100, [det(0, 0, 90, 90)], True),
        ("auto", 1200, 1200, [det(0, 0, 90, 90)], True),
        ("auto", 1199, 2000, [det(0, 0, 90, 90)], False),
        ("auto", 1199, 1199, [], True),
        ("auto", 1000, 1000, [det(0, 0, 40, 40)], False),
        ("auto", 1000, 1000, [det(0, 0, 39, 80)], True),
        ("auto", 1000, 1000, [det(0, 0, 80, 39)], True),
    ],
)
def test_needs_tiling(mode, w, h, faces, expected):
    assert needs_tiling(mode, w, h, faces) is expected


def test_merge_nms_prefers_score_and_flags_tile_only():
    a = det(10, 10, 60, 60, 0.9, False)
    b = det(11, 11, 61, 61, 0.95, True)
    out = merge_nms([a, b])
    assert len(out) == 1 and out[0].score == 0.95 and out[0].from_tile is False
    c = det(200, 200, 250, 250, 0.8, True)
    d = det(201, 201, 251, 251, 0.7, True)
    out = merge_nms([c, d])
    assert len(out) == 1 and out[0].score == 0.8 and out[0].from_tile is True
    assert len(merge_nms([a, c])) == 2


def test_detect_image_maps_tiles_back_and_dedupes():
    w, h = 1300, 1000  # min side < 1200 but zero faces in full pass forces tiling? no: face given
    small = det(640, 480, 660, 500, 0.9)  # 20px -> forces tiling; lies in overlap of tiles
    fake = FakeDetector([small], (w, h))
    settings = Settings(tiling="auto", tile_grid="2x2", tile_overlap=0.2)
    out = detect_image(fake, marked_image(w, h), settings)
    assert len(out) == 1
    assert out[0].from_tile is False  # also found by full pass
    assert any(c[2] == 640 for c in fake.calls)


def test_tile_only_face_mapped_exactly():
    w, h = 1300, 1000
    f = det(1000, 700, 1030, 730, 0.8)

    class TileOnly(FakeDetector):
        def detect(self, img, det_size):
            if (img.shape[1], img.shape[0]) == self.full_size:
                return []
            return super().detect(img, det_size)

    out = detect_image(TileOnly([f], (w, h)), marked_image(w, h), Settings(tile_grid="2x2"))
    assert len(out) == 1 and out[0].from_tile is True
    assert out[0].bbox == f.bbox
    np.testing.assert_allclose(out[0].kps, f.kps)


def test_low_score_dropped():
    w, h = 800, 600
    fake = FakeDetector([det(100, 100, 200, 200, 0.49), det(300, 300, 400, 400, 0.5)], (w, h))
    out = detect_image(fake, marked_image(w, h), Settings(tiling="off"))
    assert [d.score for d in out] == [0.5]


def test_tiling_off_single_call():
    fake = FakeDetector([det(100, 100, 200, 200)], (800, 600))
    detect_image(fake, marked_image(800, 600), Settings(tiling="off"))
    assert len(fake.calls) == 1


def test_decode_outputs_letterbox_math():
    det_size, scale = 640, 0.5
    outs = []
    # stride 8: one anchor hit at grid (x=10, y=5), anchor index 0 -> centre (80, 40)
    for stride in (8, 16, 32):
        n = (det_size // stride) ** 2 * 2
        outs.append(np.zeros((n, 1), dtype=np.float32))
    n8 = (det_size // 8) ** 2 * 2
    idx = (5 * (det_size // 8) + 10) * 2
    outs[0][idx, 0] = 0.9
    boxes = [np.zeros((len(o), 4), dtype=np.float32) for o in outs]
    kps = [np.zeros((len(o), 10), dtype=np.float32) for o in outs]
    boxes[0][idx] = [1, 2, 3, 4]  # *stride 8 -> 8,16,24,32
    kps[0][idx] = np.arange(10, dtype=np.float32) / 8
    assert n8 == len(outs[0])
    res = decode_outputs([*outs, *boxes, *kps], det_size, scale, 0.5)
    assert len(res) == 1
    assert res[0].bbox == pytest.approx(
        ((80 - 8) / 0.5, (40 - 16) / 0.5, (80 + 24) / 0.5, (40 + 32) / 0.5)
    )
    assert res[0].kps[0] == pytest.approx([(80 + 0) / 0.5, (40 + 1) / 0.5])
    assert res[0].kps[4] == pytest.approx([(80 + 8) / 0.5, (40 + 9) / 0.5])


def test_vendored_helpers():
    pts = make_anchors(2, 3, 8, 2)
    assert pts.shape == (12, 2) and pts[0].tolist() == pts[1].tolist() == [0, 0]
    assert pts[2].tolist() == [8, 0]
    assert distance2bbox(np.array([[10, 10]], np.float32), np.array([[1, 2, 3, 4]], np.float32))[
        0
    ].tolist() == [9, 8, 13, 14]
    d = np.array([[10, 10, 20, 20, 5, 5, 10, 20, 20, 20, 1.0]], dtype=np.float32)[:, :10]
    keep = nms(
        np.array([[0, 0, 10, 10, 0.9], [1, 1, 10, 10, 0.8], [50, 50, 60, 60, 0.7]], np.float32), 0.4
    )
    assert keep == [0, 2]
    assert d.shape == (1, 10)
    assert distance2kps(np.array([[1, 2]], np.float32), np.ones((1, 10), np.float32))[
        0, :2
    ].tolist() == [2, 3]


def test_estimate_norm_identity_on_template():
    m = estimate_norm(ARCFACE_TEMPLATE.copy())
    np.testing.assert_allclose(m, [[1, 0, 0], [0, 1, 0]], atol=1e-4)
    scaled = ARCFACE_TEMPLATE * 2 + 30
    m2 = estimate_norm(scaled.astype(np.float32))
    mapped = scaled @ m2[:, :2].T + m2[:, 2]
    np.testing.assert_allclose(mapped, ARCFACE_TEMPLATE, atol=1e-3)


@pytest.mark.models
def test_real_scrfd_detects_face():
    import urllib.request

    import cv2

    from kino.models.download import ensure_model
    from kino.pipeline.detect import ScrfdDetector

    scratch = Path("/tmp/claude-0/-opt-dev-Kino/b1b798f9-80d6-4fe9-b229-0e319832be2c/scratchpad")
    data = scratch / "kino-data"
    data.mkdir(parents=True, exist_ok=True)
    model = ensure_model("scrfd_10g", data)
    img_path = scratch / "face.jpg"
    if not img_path.exists():
        url = os.environ.get(
            "KINO_TEST_FACE_URL",
            "https://upload.wikimedia.org/wikipedia/commons/thumb/d/d3/"
            "Albert_Einstein_Head.jpg/330px-Albert_Einstein_Head.jpg",
        )
        req = urllib.request.Request(url, headers={"User-Agent": "kino-tests/0"})
        img_path.write_bytes(urllib.request.urlopen(req, timeout=30).read())  # noqa: S310
    img = cv2.imread(str(img_path))
    assert img is not None
    out = detect_image(ScrfdDetector(model, 2), img, Settings())
    assert len(out) >= 1
