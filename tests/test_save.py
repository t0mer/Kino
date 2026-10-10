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
    out = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    assert out == tmp_path / "p/noa/2026/03/2026-03-07_140509_class_00000042.jpg"
    assert out.exists()


def test_aware_non_utc_converted_to_utc(jpg: Path, tmp_path: Path) -> None:
    sent = datetime(2026, 3, 8, 1, 30, 0, tzinfo=timezone(timedelta(hours=3)))
    kid, group, image = mk(sent=sent)
    out = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    assert out.name == "2026-03-07_223000_class_00000042.jpg"
    assert out.parent.name == "03"


def test_exif_and_pixels_untouched(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk()
    out = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    ex = piexif.load(str(out))
    assert ex["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:03:07 14:05:09"
    assert ex["Exif"][piexif.ExifIFD.DateTimeDigitized] == b"2026:03:07 14:05:09"
    assert ex["0th"][piexif.ImageIFD.ImageDescription] == b"Class A"
    assert ex["0th"][piexif.ImageIFD.Artist] == b"Dana"
    assert sos_data(out.read_bytes()) == sos_data(jpg.read_bytes())
    with Image.open(out) as a, Image.open(jpg) as b:
        assert np.array_equal(np.asarray(a), np.asarray(b))


def test_hebrew_roundtrip(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk(group_name="גן שמש", group_slug="gan-shemesh", sender="דנה")
    out = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    assert read_text_tags(out) == ("גן שמש", "דנה")


def test_png_converted_with_orientation(tmp_path: Path) -> None:
    im = noisy(60, 30)
    ex = Image.Exif()
    ex[0x0112] = 6  # rotate 90 CW -> displayed 30x60
    src = tmp_path / "a.png"
    im.save(src, "PNG", exif=ex)
    kid, group, image = mk()
    out = save_photo(src, kid, group, image, tmp_path / "p", timezone="UTC")
    with Image.open(out) as o:
        assert o.format == "JPEG"
        assert o.size == (30, 60)
    assert read_text_tags(out)[0] == "Class A"


def test_webp_converted(tmp_path: Path) -> None:
    src = tmp_path / "a.webp"
    noisy().save(src, "WEBP")
    out = save_photo(src, *mk()[0:3], tmp_path / "p", timezone="UTC")
    with Image.open(out) as o:
        assert o.format == "JPEG"


def test_heic_converted(tmp_path: Path) -> None:
    pytest.importorskip("pillow_heif")
    import pillow_heif

    src = tmp_path / "a.heic"
    pillow_heif.from_pillow(noisy()).save(src)
    out = save_photo(src, *mk()[0:3], tmp_path / "p", timezone="UTC")
    with Image.open(out) as o:
        assert o.format == "JPEG"


def test_second_kid_hardlink_and_copy_fallback(
    jpg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, group, image = mk()
    k1 = SimpleNamespace(id=1, slug="noa")
    k2 = SimpleNamespace(id=2, slug="itai")
    root = tmp_path / "p"
    res = save_for_kids(jpg, [k1, k2], group, image, root, timezone="UTC")
    assert os.stat(res[1]).st_ino == os.stat(res[2]).st_ino
    assert os.stat(res[1]).st_nlink == 2
    k3 = SimpleNamespace(id=3, slug="yam")

    def boom(*a: object, **k: object) -> None:
        raise OSError("xdev")

    monkeypatch.setattr(save_mod.os, "link", boom)
    res3 = save_for_kids(jpg, [k1, k3], group, image, tmp_path / "q", timezone="UTC")
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
    a = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    b = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    assert a == b and a.exists()
    assert len(list(a.parent.iterdir())) == 1
    save_for_kids(jpg, [kid], group, image, tmp_path / "p", timezone="UTC")
    save_for_kids(jpg, [kid], group, image, tmp_path / "p", timezone="UTC")


@pytest.mark.parametrize("bad", ["../../etc", "/etc", "a\x00b", "..", "a/b", ""])
def test_traversal_rejected(jpg: Path, tmp_path: Path, bad: str) -> None:
    kid, group, image = mk(kid_slug=bad)
    with pytest.raises(ValueError):
        save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    kid, group, image = mk(group_slug=bad or "x/y")
    with pytest.raises(ValueError):
        save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    assert not (tmp_path / "etc").exists()


def test_atomic_no_partial(jpg: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*a: object, **k: object) -> None:
        raise RuntimeError("crash")

    monkeypatch.setattr(save_mod, "rewrite_jpeg_metadata", boom)
    kid, group, image = mk()
    root = tmp_path / "p"
    with pytest.raises(RuntimeError):
        save_photo(jpg, kid, group, image, root, timezone="UTC")
    assert [p for p in root.rglob("*") if p.is_file()] == []


def test_remove_prunes_and_keeps(jpg: Path, tmp_path: Path) -> None:
    root = tmp_path / "p"
    kid, group, image = mk()
    out = save_photo(jpg, kid, group, image, root, timezone="UTC")
    remove_photo(out, root)
    assert root.exists() and list(root.iterdir()) == []
    out = save_photo(jpg, kid, group, image, root, timezone="UTC")
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
    res = save_for_kids(jpg, [k1, k2], group, image, root, timezone="UTC")
    remove_photo(res[1], root)
    assert not res[1].exists()
    assert os.stat(res[2]).st_nlink == 1
    with Image.open(res[2]) as im:
        assert im.format == "JPEG"
    assert not (root / "noa").exists()


def test_resave_keeps_siblings_linked(jpg: Path, tmp_path: Path) -> None:
    root = tmp_path / "p"
    _, group, image = mk()
    k1 = SimpleNamespace(id=1, slug="noa")
    k2 = SimpleNamespace(id=2, slug="itai")
    save_for_kids(jpg, [k1, k2], group, image, root, timezone="UTC")
    res = save_for_kids(jpg, [k1, k2], group, image, root, timezone="UTC")
    s1, s2 = os.stat(res[1]), os.stat(res[2])
    assert s1.st_ino == s2.st_ino and s1.st_nlink == 2


def test_hostile_text_saves(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk(group_name="x" * 100_000, sender="a\ud800b\x00c\x07")
    out = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")
    ex = piexif.load(str(out))
    assert len(ex["0th"][piexif.ImageIFD.ImageDescription]) <= 256
    assert read_text_tags(out)[1] == "a?bc"


def gps_jpeg(path: Path) -> None:
    gps = {
        piexif.GPSIFD.GPSLatitudeRef: b"N",
        piexif.GPSIFD.GPSLatitude: ((32, 1), (5, 1), (0, 1)),
        piexif.GPSIFD.GPSLongitudeRef: b"E",
        piexif.GPSIFD.GPSLongitude: ((34, 1), (46, 1), (0, 1)),
    }
    exif = {
        "0th": {piexif.ImageIFD.Make: b"Cam", piexif.ImageIFD.DateTime: b"2000:01:01 00:00:00"},
        "Exif": {
            piexif.ExifIFD.OffsetTimeOriginal: b"+03:00",
            piexif.ExifIFD.SubSecTimeOriginal: b"123",
            piexif.ExifIFD.OffsetTime: b"+03:00",
        },
        "GPS": gps,
    }
    noisy().save(path, "JPEG", exif=piexif.dump(exif))


def test_gps_stripped_by_default_and_stale_tags(tmp_path: Path) -> None:
    src = tmp_path / "g.jpg"
    gps_jpeg(src)
    kid, group, image = mk()
    ex = piexif.load(str(save_photo(src, kid, group, image, tmp_path / "p", timezone="UTC")))
    assert ex["GPS"] == {}
    assert ex["0th"][piexif.ImageIFD.Make] == b"Cam"
    assert ex["0th"][piexif.ImageIFD.DateTime] == b"2026:03:07 14:05:09"
    assert piexif.ExifIFD.SubSecTimeOriginal not in ex["Exif"]
    # stale +03:00 from the camera is replaced by the configured zone's offset
    assert ex["Exif"][piexif.ExifIFD.OffsetTimeOriginal] == b"+00:00"
    assert ex["Exif"][piexif.ExifIFD.OffsetTime] == b"+00:00"


def test_gps_kept_when_disabled(tmp_path: Path) -> None:
    src = tmp_path / "g.jpg"
    gps_jpeg(src)
    kid, group, image = mk()
    out = save_photo(src, kid, group, image, tmp_path / "p", strip_gps=False, timezone="UTC")
    assert piexif.load(str(out))["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"


def test_file_mode(jpg: Path, tmp_path: Path) -> None:
    out = save_photo(jpg, *mk()[0:3], tmp_path / "p", timezone="UTC")
    umask = os.umask(0)
    os.umask(umask)
    assert out.stat().st_mode & 0o777 == 0o644 & ~umask


def test_missing_sent_at(jpg: Path, tmp_path: Path) -> None:
    kid, group, image = mk(sent=None)
    with pytest.raises(ValueError, match="sent_at missing"):
        save_photo(jpg, kid, group, image, tmp_path / "p", timezone="UTC")


def test_timezone_local_date_folder_and_exif(jpg: Path, tmp_path: Path) -> None:
    # 23:30 UTC on 31 March is 02:30 on 1 April in Israel (IDT, +03:00)
    kid, group, image = mk(sent=datetime(2026, 3, 31, 23, 30, 0))
    out = save_photo(jpg, kid, group, image, tmp_path / "p", timezone="Asia/Jerusalem")
    assert out == tmp_path / "p/noa/2026/04/2026-04-01_023000_class_00000042.jpg"
    ex = piexif.load(str(out))
    assert ex["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:04:01 02:30:00"
    assert ex["0th"][piexif.ImageIFD.DateTime] == b"2026:04:01 02:30:00"
    assert ex["Exif"][piexif.ExifIFD.OffsetTimeOriginal] == b"+03:00"
    assert ex["Exif"][piexif.ExifIFD.OffsetTime] == b"+03:00"


def test_timezone_winter_offset_and_siblings(jpg: Path, tmp_path: Path) -> None:
    _, group, image = mk(sent=datetime(2026, 1, 15, 21, 0, 0))
    k1 = SimpleNamespace(id=1, slug="noa")
    k2 = SimpleNamespace(id=2, slug="itai")
    res = save_for_kids(jpg, [k1, k2], group, image, tmp_path / "p", timezone="Asia/Jerusalem")
    assert res[2].name == "2026-01-15_230000_class_00000042.jpg"
    assert piexif.load(str(res[1]))["Exif"][piexif.ExifIFD.OffsetTimeOriginal] == b"+02:00"


# --- GPS / location stripping across JPEG segment layouts --------------------------------

_EXIF_HDR = b"Exif\x00\x00"
_XMP_HDR = b"http://ns.adobe.com/xap/1.0/\x00"
_XMP_GPS = (
    b'<x:xmpmeta xmlns:x="adobe:ns:meta/"><rdf:RDF><rdf:Description '
    b'exif:GPSLatitude="32,5.0N" exif:GPSLongitude="34,46.0E"/></rdf:RDF></x:xmpmeta>'
)


def _seg(marker: int, payload: bytes) -> bytes:
    return bytes([0xFF, marker]) + (len(payload) + 2).to_bytes(2, "big") + payload


def _gps_exif_payload() -> bytes:
    gps = {
        piexif.GPSIFD.GPSLatitudeRef: b"N",
        piexif.GPSIFD.GPSLatitude: ((32, 1), (5, 1), (0, 1)),
        piexif.GPSIFD.GPSLongitudeRef: b"E",
        piexif.GPSIFD.GPSLongitude: ((34, 1), (46, 1), (0, 1)),
    }
    return piexif.dump({"0th": {piexif.ImageIFD.Make: b"Cam"}, "GPS": gps})


def _segments(b: bytes) -> list[tuple[int, bytes]]:
    """(marker, payload) for every segment before SOS."""
    out, i = [], 2
    while b[i + 1] != 0xDA:
        n = int.from_bytes(b[i + 2 : i + 4], "big")
        out.append((b[i + 1], b[i + 4 : i + 2 + n]))
        i += 2 + n
    return out


def _layout(tmp_path: Path, segs: list[bytes]) -> Path:
    """A real JPEG with its header segments replaced by ``segs`` (SOI kept, DQT.. kept)."""
    import io

    buf = io.BytesIO()
    noisy().save(buf, "JPEG", quality=90)
    plain = buf.getvalue()
    i = 2
    while plain[i + 1] in (0xE0, 0xE1, 0xE2, 0xED, 0xFE):  # drop Pillow's APPn/COM
        i += 2 + int.from_bytes(plain[i + 2 : i + 4], "big")
    p = tmp_path / "layout.jpg"
    p.write_bytes(plain[:2] + b"".join(segs) + plain[i:])
    return p


_JFIF = _seg(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
_ICC = _seg(0xE2, b"ICC_PROFILE\x00\x01\x01" + b"\x00" * 64)
_IPTC = _seg(0xED, b"Photoshop 3.0\x008BIM\x04\x04\x00\x00\x00\x00\x00\x08\x1c\x02\x5a\x00\x03TLV")


@pytest.mark.parametrize(
    "layout",
    [
        pytest.param([_JFIF, _seg(0xE1, _gps_exif_payload())], id="app0-exif"),
        pytest.param([_JFIF, _ICC, _seg(0xE1, _gps_exif_payload())], id="app0-icc-exif"),
        pytest.param(
            [_seg(0xE1, _XMP_HDR + _XMP_GPS), _seg(0xE1, _gps_exif_payload())], id="xmp-exif"
        ),
        pytest.param([_ICC, _IPTC, _seg(0xE1, _gps_exif_payload()), _JFIF], id="exif-not-first"),
        pytest.param([_JFIF, _ICC], id="no-exif"),
    ],
)
def test_location_stripped_in_every_layout(tmp_path: Path, layout: list[bytes]) -> None:
    src = _layout(tmp_path, layout)
    kid, group, image = mk()
    out = save_photo(src, kid, group, image, tmp_path / "p", timezone="UTC").read_bytes()
    segs = _segments(out)
    exifs = [p for m, p in segs if m == 0xE1 and p.startswith(_EXIF_HDR)]
    assert len(exifs) == 1
    ex = piexif.load(out)
    assert ex["GPS"] == {}
    assert ex["Exif"][piexif.ExifIFD.DateTimeOriginal] == b"2026:03:07 14:05:09"
    assert not any(m == 0xE1 and p.startswith(_XMP_HDR) for m, p in segs)
    assert b"GPSLatitude" not in out
    assert not any(m == 0xED for m, _ in segs)  # Photoshop/IPTC (may hold location)
    if any(s == _ICC for s in layout):
        assert (0xE2, _ICC[4:]) in segs  # ICC profile kept
    if layout[0] == _JFIF:
        assert segs[0][0] == 0xE0  # JFIF stays first
    assert sos_data(out) == sos_data(src.read_bytes())
    if any(s.startswith(b"\xff\xe1") and _EXIF_HDR in s[:10] for s in layout):
        assert ex["0th"][piexif.ImageIFD.Make] == b"Cam"  # other original EXIF preserved


def test_xmp_kept_when_gps_stripping_disabled(tmp_path: Path) -> None:
    src = _layout(
        tmp_path, [_JFIF, _seg(0xE1, _XMP_HDR + _XMP_GPS), _seg(0xE1, _gps_exif_payload())]
    )
    kid, group, image = mk()
    out = save_photo(src, kid, group, image, tmp_path / "p", strip_gps=False, timezone="UTC")
    segs = _segments(out.read_bytes())
    assert sum(1 for m, p in segs if m == 0xE1 and p.startswith(_EXIF_HDR)) == 1
    assert any(m == 0xE1 and p.startswith(_XMP_HDR) for m, p in segs)
    assert piexif.load(str(out))["GPS"][piexif.GPSIFD.GPSLatitudeRef] == b"N"
