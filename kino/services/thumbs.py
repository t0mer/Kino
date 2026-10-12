"""On-disk JPEG thumbnails of saved photos, cached under ``<data_dir>/thumbs``."""

from __future__ import annotations

import contextlib
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps, UnidentifiedImageError

import kino.pipeline.prefilter  # noqa: F401  (sets Image.MAX_IMAGE_PIXELS)
from kino.services.files import safe_join

DEFAULT_W = 400
MIN_W = 128
MAX_W = 1024


def clamp_width(w: int | None) -> int:
    return DEFAULT_W if w is None else max(MIN_W, min(MAX_W, w))


def thumb_dir(data_dir: Path) -> Path:
    return data_dir / "thumbs"


def _valid(path: Path) -> bool:
    try:
        with Image.open(path) as im:
            im.verify()
        return True
    except (OSError, UnidentifiedImageError, SyntaxError, ValueError):
        return False


def get_thumb(data_dir: Path, saved_photo_id: int, w: int, source: Path) -> Path | None:
    """The cached thumbnail for ``source`` (generated on a miss), or None if undecodable."""
    base = thumb_dir(data_dir)
    base.mkdir(parents=True, exist_ok=True)
    target = safe_join(base, f"{saved_photo_id}_{w}.jpg")
    if target.is_file() and _valid(target):
        return target
    try:
        with Image.open(source) as src:
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
