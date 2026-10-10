import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import piexif
import pytest
from PIL import Image

from kino.pipeline import save as save_mod
from kino.pipeline.save import (
    link_or_copy,
    read_text_tags,
    remove_photo,
    save_for_kids,
    save_photo,
)

SENT = datetime(2026, 3, 7, 14, 5, 9)


def mk(kid_slug="noa", group_slug="class", group_name="Class A", sent=SENT, iid=42, sender="Dana"):
    kid = SimpleNamespace(id=1, slug=kid_slug)
    group = SimpleNamespace(slug=group_slug, name=group_name)
    image = SimpleNamespace(id=iid, sent_at=sent, sender_name=sender)
    return kid, group, image


def noisy(w=64, h=48) -> Image.Image:
    rng = np.random.default_rng(1)
    return Image.fromarray(rng.integers(0, 255, (h, w, 3), dtype=np.uint8))


@pytest.fixture
def jpg(tmp_path: Path) -> Path:
    p = tmp_path / "raw.jpg"
    noisy().save(p, "JPEG", quality=90)
    return p


def sos_data(b: bytes) -> bytes:
    i = b.index(b"\xff\xda")
    return b[i:]


def test_path_format_naive(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk()
    out = save_photo(jpg, kid, group, image, tmp_path / "p")
    assert out == tmp_path / "p/noa/2026/03/2026-03-07_140509_class_00000042.jpg"
    assert out.exists()


def test_aware_non_utc_converted_to_utc(jpg: Path, tmp_path: Path) -> None:
    sent = datetime(2026, 3, 8, 1, 30, 0, tzinfo=timezone(timedelta(hours=3)))
    kid, group, image = mk(sent=sent)
    out = save_photo(jpg, kid, group, image, tmp_path / "p")
    assert out.name == "2026-03-07_223000_class_00000042.jpg"
    assert out.parent.name == "03"


def test_exif_and_pixels_untouched(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk()
    out = save_photo(jpg, kid, group, image, tmp_path / "p")
    ex = piexif.load(str(out))
    assert ex["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:03:07 14:05:09"
    assert ex["Exif"][piexif.ExifIFD.DateTimeDigitized] == b"2026:03:07 14:05:09"
    assert ex["0th"][piexif.ImageIFD.ImageDescription] == b"Class A"
    assert ex["0th"][piexif.ImageIFD.Artist] == b"Dana"
    assert sos_data(out.read_bytes()) == sos_data(jpg.read_bytes())
    assert np.array_equal(np.asarray(Image.open(out)), np.asarray(Image.open(jpg)))


def test_hebrew_roundtrip(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk(group_name="גן שמש", group_slug="gan-shemesh", sender="דנה")
    out = save_photo(jpg, kid, group, image, tmp_path / "p")
    assert read_text_tags(out) == ("גן שמש", "דנה")


def test_png_converted_with_orientation(tmp_path: Path) -> None:
    im = noisy(60, 30)
    ex = Image.Exif()
    ex[0x0112] = 6  # rotate 90 CW -> displayed 30x60
    src = tmp_path / "a.png"
    im.save(src, "PNG", exif=ex)
    kid, group, image = mk()
    out = save_photo(src, kid, group, image, tmp_path / "p")
    with Image.open(out) as o:
        assert o.format == "JPEG"
        assert o.size == (30, 60)
    assert read_text_tags(out)[0] == "Class A"


def test_webp_converted(tmp_path: Path) -> None:
    src = tmp_path / "a.webp"
    noisy().save(src, "WEBP")
    out = save_photo(src, *mk()[0:3], tmp_path / "p")
    with Image.open(out) as o:
        assert o.format == "JPEG"


def test_heic_converted(tmp_path: Path) -> None:
    pytest.importorskip("pillow_heif")
    import pillow_heif

    src = tmp_path / "a.heic"
    pillow_heif.from_pillow(noisy()).save(src)
    out = save_photo(src, *mk()[0:3], tmp_path / "p")
    with Image.open(out) as o:
        assert o.format == "JPEG"


def test_second_kid_hardlink_and_copy_fallback(
    jpg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, group, image = mk()
    k1 = SimpleNamespace(id=1, slug="noa")
    k2 = SimpleNamespace(id=2, slug="itai")
    root = tmp_path / "p"
    res = save_for_kids(jpg, [k1, k2], group, image, root)
    assert os.stat(res[1]).st_ino == os.stat(res[2]).st_ino
    assert os.stat(res[1]).st_nlink == 2
    k3 = SimpleNamespace(id=3, slug="yam")

    def boom(*a: object, **k: object) -> None:
        raise OSError("xdev")

    monkeypatch.setattr(save_mod.os, "link", boom)
    res3 = save_for_kids(jpg, [k1, k3], group, image, tmp_path / "q")
    assert os.stat(res3[1]).st_ino != os.stat(res3[3]).st_ino
    assert res3[1].read_bytes() == res3[3].read_bytes()


def test_link_or_copy_idempotent(tmp_path: Path) -> None:
    src = tmp_path / "s"
    src.write_bytes(b"x")
    dst = tmp_path / "d/e/f"
    link_or_copy(src, dst)
    link_or_copy(src, dst)
    assert dst.read_bytes() == b"x"


def test_save_twice_idempotent(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk()
    a = save_photo(jpg, kid, group, image, tmp_path / "p")
    b = save_photo(jpg, kid, group, image, tmp_path / "p")
    assert a == b and a.exists()
    assert len(list(a.parent.iterdir())) == 1
    save_for_kids(jpg, [kid], group, image, tmp_path / "p")
    save_for_kids(jpg, [kid], group, image, tmp_path / "p")


@pytest.mark.parametrize("bad", ["../../etc", "/etc", "a\x00b", "..", "a/b", ""])
def test_traversal_rejected(jpg: Path, tmp_path: Path, bad: str) -> None:
    kid, group, image = mk(kid_slug=bad)
    with pytest.raises(ValueError):
        save_photo(jpg, kid, group, image, tmp_path / "p")
    kid, group, image = mk(group_slug=bad or "x/y")
    with pytest.raises(ValueError):
        save_photo(jpg, kid, group, image, tmp_path / "p")
    assert not (tmp_path / "etc").exists()


def test_atomic_no_partial(jpg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("crash")

    monkeypatch.setattr(save_mod.piexif, "insert", boom)
    kid, group, image = mk()
    root = tmp_path / "p"
    with pytest.raises(RuntimeError):
        save_photo(jpg, kid, group, image, root)
    assert [p for p in root.rglob("*") if p.is_file()] == []


def test_remove_prunes_and_keeps(jpg: Path, tmp_path: Path) -> None:
    root = tmp_path / "p"
    kid, group, image = mk()
    out = save_photo(jpg, kid, group, image, root)
    remove_photo(out, root)
    assert root.exists() and list(root.iterdir()) == []
    out = save_photo(jpg, kid, group, image, root)
    other = out.parent / "other.jpg"
    other.write_bytes(b"1")
    remove_photo(out, root)
    assert other.exists()


def test_remove_outside_root_refused(tmp_path: Path) -> None:
    root = tmp_path / "p"
    root.mkdir()
    victim = tmp_path / "victim.txt"
    victim.write_text("x")
    with pytest.raises(ValueError):
        remove_photo(victim, root)
    with pytest.raises(ValueError):
        remove_photo(root / ".." / "victim.txt", root)
    with pytest.raises(ValueError):
        remove_photo(root, root)
    assert victim.exists() and root.exists()


def test_remove_keeps_hardlinked_sibling(jpg: Path, tmp_path: Path) -> None:
    root = tmp_path / "p"
    _, group, image = mk()
    k1 = SimpleNamespace(id=1, slug="noa")
    k2 = SimpleNamespace(id=2, slug="itai")
    res = save_for_kids(jpg, [k1, k2], group, image, root)
    remove_photo(res[1], root)
    assert not res[1].exists()
    assert os.stat(res[2]).st_nlink == 1
    assert Image.open(res[2]).format == "JPEG"
    assert not (root / "noa").exists()
