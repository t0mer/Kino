"""Saving matched photos to the per-kid folder layout (CLAUDE.md section 9.8).

Policy:
* JPEG input keeps its original pixel/entropy bytes; only the EXIF segment is
  rewritten (``piexif.insert``).
* PNG/WebP/HEIC input is the ONLY case where pixels are re-encoded: converted to
  JPEG at quality 95 with EXIF orientation applied to the pixels.
* Dates use ``image.sent_at`` in UTC (naive values are assumed to be UTC, aware
  values are converted to UTC).
* EXIF ``ImageDescription``/``Artist`` are ASCII fields; non-ASCII text (Hebrew)
  is stored as raw UTF-8 bytes there (most readers decode it) and ALSO in
  ``XPTitle``/``XPAuthor`` (UTF-16LE, the Windows convention), which is what
  :func:`read_text_tags` prefers.
* ``image_id_short`` is the image id zero-padded to 8 digits.
* Files are written to a temp file in the destination directory and moved with
  ``os.replace`` so a crash never leaves a partial destination.
"""

from __future__ import annotations

import contextlib
import io
import os
import shutil
import tempfile
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Protocol

import piexif
from PIL import Image, ImageOps

from kino.slug import slugify

try:  # HEIC support is optional at runtime
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

_XP_TITLE = 0x9C9B
_XP_AUTHOR = 0x9C9D


class _KidLike(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def slug(self) -> str: ...


class _GroupLike(Protocol):
    @property
    def slug(self) -> str | None: ...
    @property
    def name(self) -> str | None: ...


class _ImageLike(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def sent_at(self) -> datetime | None: ...
    @property
    def sender_name(self) -> str | None: ...


def _utc(dt: datetime | None) -> datetime:
    if dt is None:
        return datetime.fromtimestamp(0, UTC)
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def _component(value: str, what: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or any(c in value for c in "/\\\x00")
        or ".." in value
        or value != value.strip()
    ):
        raise ValueError(f"unsafe {what} path component: {value!r}")
    return value


def _inside(root: Path, candidate: Path) -> Path:
    resolved = candidate.resolve()
    if not resolved.is_relative_to(root.resolve()):
        raise ValueError(f"path escapes destination root: {candidate}")
    return resolved


def build_dest_path(kid: _KidLike, group: _GroupLike, image: _ImageLike, dest_root: Path) -> Path:
    """Compute (and validate) the destination path for one kid."""
    ts = _utc(image.sent_at)
    group_slug = group.slug or slugify(group.name or "", "group")
    name = f"{ts:%Y-%m-%d_%H%M%S}_{_component(group_slug, 'group')}_{image.id:08d}.jpg"
    path = dest_root / _component(kid.slug, "kid") / f"{ts:%Y}" / f"{ts:%m}" / name
    _inside(dest_root, path)
    return path


def _clean(text: str | None) -> str:
    return (text or "").replace("\x00", "")


def _xp(text: str) -> bytes:
    return text.encode("utf-16-le") + b"\x00\x00"


def _build_exif(existing: bytes | None, image: _ImageLike, group: _GroupLike) -> bytes:
    exif: dict[str, Any] = {"0th": {}, "Exif": {}, "GPS": {}, "Interop": {}, "1st": {}}
    if existing:
        with contextlib.suppress(Exception):
            exif = piexif.load(existing)
    exif["thumbnail"] = None
    exif["1st"] = {}
    stamp = _utc(image.sent_at).strftime("%Y:%m:%d %H:%M:%S").encode("ascii")
    exif["Exif"][piexif.ExifIFD.DateTimeOriginal] = stamp
    exif["Exif"][piexif.ExifIFD.DateTimeDigitized] = stamp
    desc = _clean(group.name)
    artist = _clean(image.sender_name)
    zeroth = exif["0th"]
    zeroth[piexif.ImageIFD.ImageDescription] = desc.encode("utf-8")
    zeroth[piexif.ImageIFD.Artist] = artist.encode("utf-8")
    zeroth[_XP_TITLE] = _xp(desc)
    zeroth[_XP_AUTHOR] = _xp(artist)
    result: bytes = piexif.dump(exif)
    return result


def read_text_tags(path: Path) -> tuple[str, str]:
    """Return (group name, sender) from XPTitle/XPAuthor (Unicode-safe)."""
    zeroth = piexif.load(str(path))["0th"]

    def dec(tag: int) -> str:
        raw = zeroth.get(tag, b"")
        return bytes(raw).decode("utf-16-le").rstrip("\x00")

    return dec(_XP_TITLE), dec(_XP_AUTHOR)


def _write_atomic(dst: Path, write: Any) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=dst.parent, prefix=".kino-", suffix=".tmp")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        write(tmp)
        os.replace(tmp, dst)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


def save_photo(
    raw_path: Path, kid: _KidLike, group: _GroupLike, image: _ImageLike, dest_root: Path
) -> Path:
    """Write the photo for ``kid`` under ``dest_root`` with rewritten EXIF."""
    dst = build_dest_path(kid, group, image, dest_root)
    data = Path(raw_path).read_bytes()

    if data[:2] == b"\xff\xd8":

        def write(tmp: Path) -> None:
            tmp.write_bytes(data)
            piexif.insert(_build_exif(data, image, group), str(tmp))

    else:

        def write(tmp: Path) -> None:
            with Image.open(io.BytesIO(data)) as im:
                im.load()
                oriented = ImageOps.exif_transpose(im)
                rgb = oriented.convert("RGB")
            rgb.save(tmp, "JPEG", quality=95, exif=_build_exif(None, image, group))

    _write_atomic(dst, write)
    return dst


def link_or_copy(src: Path, dst: Path) -> None:
    """Hardlink ``src`` to ``dst``; fall back to a copy. Idempotent."""
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        if os.path.samefile(src, dst):
            return
        if src.read_bytes() == dst.read_bytes():
            return
        dst.unlink()

    def write(tmp: Path) -> None:
        tmp.unlink()
        try:
            os.link(src, tmp)
        except OSError:
            shutil.copy2(src, tmp)

    _write_atomic(dst, write)


def save_for_kids(
    raw_path: Path,
    kids: Sequence[_KidLike],
    group: _GroupLike,
    image: _ImageLike,
    dest_root: Path,
) -> dict[int, Path]:
    """Save for the first kid, hardlink (or copy) for the rest."""
    result: dict[int, Path] = {}
    first: Path | None = None
    for kid in kids:
        if first is None:
            first = save_photo(raw_path, kid, group, image, dest_root)
            result[kid.id] = first
        else:
            dst = build_dest_path(kid, group, image, dest_root)
            link_or_copy(first, dst)
            result[kid.id] = dst
    return result


def remove_photo(path: Path, dest_root: Path) -> None:
    """Delete a saved file and prune empty parents, never leaving ``dest_root``."""
    root = dest_root.resolve()
    target = _inside(dest_root, Path(path).parent) / Path(path).name
    target.unlink(missing_ok=True)
    parent = target.parent
    while parent != root and parent.is_relative_to(root):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent
