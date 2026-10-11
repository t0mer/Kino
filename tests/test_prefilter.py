import io

import numpy as np
import pytest
from PIL import Image

from kino.pipeline.prefilter import load_for_pipeline, skip_reason, sniff_image

pillow_heif = pytest.importorskip("pillow_heif", reason="pillow_heif is needed to encode HEIC")


def _heic(frames: int = 1, size=(320, 240)) -> bytes:
    rng = np.random.default_rng(3)
    ims = [
        Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
        for _ in range(frames)
    ]
    buf = io.BytesIO()
    ims[0].save(buf, "HEIF", save_all=True, append_images=ims[1:])
    return buf.getvalue()


def test_heic_sniffed_as_image():
    info = sniff_image(_heic())
    assert (info.format, info.ext, info.width, info.height) == ("HEIF", "heic", 320, 240)


def test_multi_image_heic_is_not_skipped_as_animated(tmp_path):
    data = _heic(frames=2)
    with Image.open(io.BytesIO(data)) as im:
        assert getattr(im, "n_frames", 1) > 1  # a burst/depth HEIC container
        assert skip_reason(im) is None
    p = tmp_path / "a.heic"
    p.write_bytes(data)
    loaded = load_for_pipeline(p)
    assert loaded.skip_reason is None and loaded.bgr is not None
    assert loaded.bgr.shape == (240, 320, 3)


def test_animated_gif_still_skipped():
    frames = [Image.new("RGB", (300, 300), c) for c in ("red", "blue")]
    buf = io.BytesIO()
    frames[0].save(buf, "GIF", save_all=True, append_images=frames[1:])
    with Image.open(io.BytesIO(buf.getvalue())) as im:
        assert skip_reason(im) == "animated"


def _mpo(frames: int = 2, size=(320, 240)) -> bytes:
    rng = np.random.default_rng(5)
    ims = [
        Image.fromarray(rng.integers(0, 255, (size[1], size[0], 3), dtype=np.uint8))
        for _ in range(frames)
    ]
    buf = io.BytesIO()
    ims[0].save(buf, "MPO", save_all=True, append_images=ims[1:])
    return buf.getvalue()


def test_mpo_is_a_jpeg_and_not_animated(tmp_path):
    data = _mpo()
    info = sniff_image(data)
    assert (info.format, info.ext, info.width, info.height) == ("MPO", "jpg", 320, 240)
    with Image.open(io.BytesIO(data)) as im:
        assert im.format == "MPO" and getattr(im, "n_frames", 1) > 1
        assert skip_reason(im) is None
    p = tmp_path / "a.jpg"
    p.write_bytes(data)
    loaded = load_for_pipeline(p)
    assert loaded.skip_reason is None and loaded.bgr is not None
    assert loaded.bgr.shape == (240, 320, 3)
