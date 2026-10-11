"""Image sniffing and perceptual-hash helpers for untrusted bytes."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import imagehash
import numpy as np
from numpy.typing import NDArray
from PIL import Image, ImageOps, UnidentifiedImageError

try:  # HEIC/HEIF decoding for the pipeline loader
    import pillow_heif

    pillow_heif.register_heif_opener()
except ImportError:  # pragma: no cover
    pass

MAX_IMAGE_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

# HEIF covers iPhone HEIC photos (decoded by pillow_heif; save.py converts them to JPEG).
# MPO is a JPEG with extra pictures (Samsung/Fuji/3D cameras); its primary image is a JPEG
# and save.py drops the secondary images when rewriting the metadata.
_EXT = {
    "JPEG": "jpg",
    "MPO": "jpg",
    "PNG": "png",
    "WEBP": "webp",
    "GIF": "gif",
    "BMP": "bmp",
    "HEIF": "heic",
}
# Containers whose extra frames are not animation: only the primary image is used.
STILL_MULTI_FORMATS = frozenset({"HEIF", "MPO"})


class NotAnImage(ValueError):
    """Bytes are not a decodable, acceptable image."""


@dataclass(frozen=True)
class ImageInfo:
    format: str
    ext: str
    width: int
    height: int


def sniff_image(data: bytes) -> ImageInfo:
    """Identify an image by content (not declared mimetype); raises NotAnImage."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            fmt = im.format or ""
            w, h = im.size
            if fmt not in _EXT:
                raise NotAnImage(f"unsupported format {fmt or 'unknown'}")
            if w < 1 or h < 1 or w * h > MAX_IMAGE_PIXELS:
                raise NotAnImage("image dimensions out of range")
            im.verify()
    except NotAnImage:
        raise
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as e:
        raise NotAnImage(type(e).__name__) from None
    return ImageInfo(fmt, _EXT[fmt], w, h)


def compute_phash(data: bytes) -> str:
    """64-bit perceptual hash as 16 hex chars (EXIF orientation applied)."""
    try:
        with Image.open(io.BytesIO(data)) as im:
            upright = ImageOps.exif_transpose(im)
            return str(imagehash.phash(upright))
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, SyntaxError) as e:
        raise NotAnImage(type(e).__name__) from None


def hamming(a: str, b: str) -> int:
    """Hamming distance between two hex hashes."""
    return (int(a, 16) ^ int(b, 16)).bit_count()


# --- pipeline prefilter (CLAUDE.md 9.1) ---------------------------------------------------

MIN_SIDE_PX = 200


def skip_reason(im: Image.Image) -> str | None:
    """Why the pipeline should skip this image, or None.

    * ``animated``: more than one frame (animated GIF/WebP). A HEIF container with several
      images (bursts, depth/portrait shots) or an MPO (multi-picture JPEG) is a still
      photo: its primary image is used.
    * ``sticker``: a WebP with an alpha channel; WhatsApp stickers are transparent WebP,
      photos are not.
    * ``too_small``: smallest side under 200 px.
    """
    multi = getattr(im, "is_animated", False) or getattr(im, "n_frames", 1) > 1
    if multi and im.format not in STILL_MULTI_FORMATS:
        return "animated"
    if im.format == "WEBP" and (im.mode in ("RGBA", "LA", "PA") or "transparency" in im.info):
        return "sticker"
    if min(im.size) < MIN_SIDE_PX:
        return "too_small"
    return None


@dataclass
class LoadedImage:
    """Upright BGR pixels, or the reason the image is skipped (then ``bgr`` is None)."""

    bgr: NDArray[np.uint8] | None
    skip_reason: str | None = None


def load_for_pipeline(path: Path) -> LoadedImage:
    """Decode with Pillow, apply EXIF orientation and return a BGR array.

    Undecodable files raise (Pillow's own errors); the caller marks the image failed.
    """
    with Image.open(path) as im:
        reason = skip_reason(im)
        if reason is not None:
            return LoadedImage(None, reason)
        im.load()
        rgb = ImageOps.exif_transpose(im).convert("RGB")
    bgr = np.ascontiguousarray(np.asarray(rgb, dtype=np.uint8)[:, :, ::-1])
    return LoadedImage(bgr)
