"""On-disk JPEG thumbnails of saved photos, cached under ``<data_dir>/thumbs``."""

from __future__ import annotations

import contextlib
import hashlib
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

import kino.pipeline.prefilter  # noqa: F401  (sets Image.MAX_IMAGE_PIXELS)
from kino.services.files import safe_join

DEFAULT_W = 400
# Allowed widths: a request is rounded up to the next one, so a photo has few cache files.
SIZES = (160, 240, 320, 400, 640, 800, 1024)
# Decoders the thumbnailer may reach (HEIF is registered by kino.pipeline.prefilter).
_FORMATS = ("JPEG", "PNG", "WEBP", "MPO", "HEIF")


def clamp_width(w: int | None) -> int:
    """Snap ``w`` up to the next allowed size (the largest if above all of them)."""
    if w is None:
        return DEFAULT_W
    return next((s for s in SIZES if s >= w), SIZES[-1])


def thumb_dir(data_dir: Path) -> Path:
    return data_dir / "thumbs"


def _valid(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError):
        return False


def _source_key(source: Path) -> str | None:
    """Short hash of the source file's identity, so a reused id never hits a stale thumb."""
    try:
        st = source.stat()
    except OSError:
        return None
    return hashlib.sha1(f"{source}|{st.st_size}|{st.st_mtime_ns}".encode()).hexdigest()[:12]


def get_thumb(data_dir: Path, saved_photo_id: int, w: int, source: Path) -> Path | None:
    """The cached thumbnail for ``source`` (generated on a miss), or None if undecodable."""
    key = _source_key(source)
    if key is None:
        return None
    base = thumb_dir(data_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = safe_join(base, f"{saved_photo_id}_{w}_{key}.jpg")
    if target.is_file() and _valid(target):
        return target
    try:
        with Image.open(source, formats=_FORMATS) as src:
            if src.format in ("JPEG", "MPO"):
                src.draft("RGB", (w, w))  # decode at reduced scale: far less memory
            im = ImageOps.exif_transpose(src)
            im.thumbnail((w, w))
            rgb = im.convert("RGB")
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError, Image.DecompressionBombError):
        return None
    fd, tmp = tempfile.mkstemp(dir=base, prefix=".tmp-", suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as fh:
            rgb.save(fh, "JPEG", quality=80)
        os.replace(tmp, target)
        for old in base.glob(f"{saved_photo_id}_*.jpg"):  # other sources or stale keys
            if old.name.split("_")[-1] != f"{key}.jpg":
                with contextlib.suppress(OSError):
                    old.unlink()
    except OSError:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        return None
    return target


def remove_thumbs(data_dir: Path, saved_photo_ids: list[int]) -> None:
    """Delete every cached size of the given saved photos (best effort)."""
    base = thumb_dir(data_dir)
    if not base.is_dir():
        return
    for sid in saved_photo_ids:
        for p in base.glob(f"{sid}_*.jpg"):
            with contextlib.suppress(OSError):
                p.unlink()
