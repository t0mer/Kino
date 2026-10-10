"""Image sniffing and perceptual-hash helpers for untrusted bytes."""

from __future__ import annotations

import io
from dataclasses import dataclass

import imagehash
from PIL import Image, ImageOps, UnidentifiedImageError

MAX_IMAGE_PIXELS = 100_000_000
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

_EXT = {"JPEG": "jpg", "PNG": "png", "WEBP": "webp", "GIF": "gif", "BMP": "bmp"}


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
