"""5-point face alignment (algorithm from InsightFace, MIT; see LICENSE)."""

import cv2
import numpy as np
from numpy.typing import NDArray

F32 = NDArray[np.float32]

# ArcFace 112x112 reference landmarks: eyes, nose, mouth corners.
ARCFACE_TEMPLATE: F32 = np.array(
    [
        [38.2946, 51.6963],
        [73.5318, 51.5014],
        [56.0252, 71.7366],
        [41.5493, 92.3655],
        [70.7299, 92.2041],
    ],
    dtype=np.float32,
)


def _umeyama(src: NDArray[np.float64], dst: NDArray[np.float64]) -> NDArray[np.float64]:
    """Least-squares similarity transform (3x3) mapping src to dst."""
    n, dim = src.shape
    src_mean, dst_mean = src.mean(axis=0), dst.mean(axis=0)
    src_c, dst_c = src - src_mean, dst - dst_mean
    cov = dst_c.T @ src_c / n
    d = np.ones(dim)
    if np.linalg.det(cov) < 0:
        d[dim - 1] = -1
    u, s, vt = np.linalg.svd(cov)
    rot = u @ np.diag(d) @ vt
    var = src_c.var(axis=0).sum()
    scale = float((s * d).sum() / var)
    t = np.eye(dim + 1)
    t[:dim, :dim] = scale * rot
    t[:dim, dim] = dst_mean - scale * rot @ src_mean
    return t


def estimate_norm(kps: F32, image_size: int = 112) -> NDArray[np.float64]:
    """2x3 similarity matrix mapping 5 landmarks onto the ArcFace template."""
    if image_size % 112 != 0:
        raise ValueError("image_size must be a multiple of 112")
    ratio = image_size / 112.0
    dst = ARCFACE_TEMPLATE.astype(np.float64) * ratio
    m = _umeyama(np.asarray(kps, dtype=np.float64), dst)
    return m[:2, :]


def norm_crop(img: NDArray[np.uint8], kps: F32, image_size: int = 112) -> NDArray[np.uint8]:
    """Align the face to image_size x image_size."""
    m = estimate_norm(kps, image_size)
    out = cv2.warpAffine(img, m, (image_size, image_size), borderValue=0.0)
    return np.asarray(out, dtype=np.uint8)
