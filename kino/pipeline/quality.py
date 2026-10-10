"""Face quality: size tiers, blur, pose and a combined score in [0, 1]."""

from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np
from numpy.typing import NDArray

from kino.pipeline.detect import Detection

# Combined quality = W_SIZE*size + W_BLUR*blur + W_POSE*pose, each term in [0, 1].
W_SIZE = 0.4
W_BLUR = 0.3
W_POSE = 0.3
SIZE_FULL_PX = 80.0  # size term saturates here (tier L)
BLUR_FULL = 100.0  # Laplacian variance (112x112 aligned crop) that counts as sharp
YAW_MAX_DEG = 60.0  # pose term reaches 0 at this |yaw|
PITCH_MAX_DEG = 45.0
_FRONTAL_NOSE_RATIO = 0.55  # nose-to-eyeline / eyeline-to-mouth for a frontal face


class Tier(Enum):
    L = "L"
    M = "M"
    S = "S"
    XS = "XS"


def tier_for(size_px: float) -> Tier:
    """L >= 80, M 40-79, S 24-39, XS < 24 (size = min bbox side, original pixels)."""
    if size_px >= 80:
        return Tier.L
    if size_px >= 40:
        return Tier.M
    if size_px >= 24:
        return Tier.S
    return Tier.XS


@dataclass
class FaceQuality:
    size_px: float
    blur_score: float
    yaw: float
    pitch: float
    quality: float


def estimate_pose(kps: NDArray[np.float32]) -> tuple[float, float]:
    """Rough (yaw, pitch) in degrees from landmarks (l-eye, r-eye, nose, l-mouth, r-mouth).

    yaw: horizontal nose offset from the eye midpoint over the inter-eye distance, scaled
    so an offset of one inter-eye distance is 90 degrees; positive = nose right in the image.
    pitch: nose-to-eyeline over eyeline-to-mouth vertical distance, relative to the frontal
    ratio 0.55, scaled to +-45 degrees; positive = nose far from the eyes (looking down).
    """
    k = np.asarray(kps, dtype=np.float64)
    eye_mid = (k[0] + k[1]) / 2
    mouth_mid = (k[3] + k[4]) / 2
    inter_eye = max(float(np.linalg.norm(k[1] - k[0])), 1e-6)
    yaw = float(np.clip((k[2][0] - eye_mid[0]) / inter_eye, -1.0, 1.0)) * 90.0
    eye_to_mouth = max(float(mouth_mid[1] - eye_mid[1]), 1e-6)
    ratio = float(k[2][1] - eye_mid[1]) / eye_to_mouth
    rel = float(np.clip((ratio - _FRONTAL_NOSE_RATIO) / _FRONTAL_NOSE_RATIO, -1.0, 1.0))
    return yaw, rel * PITCH_MAX_DEG


def face_quality(det: Detection, aligned_crop: NDArray[np.uint8]) -> FaceQuality:
    size_px = float(det.size_px)
    gray = (
        cv2.cvtColor(aligned_crop, cv2.COLOR_BGR2GRAY) if aligned_crop.ndim == 3 else aligned_crop
    )
    blur = float(cv2.Laplacian(gray, cv2.CV_64F).var())
    yaw, pitch = estimate_pose(det.kps)
    size_t = min(size_px / SIZE_FULL_PX, 1.0)
    blur_t = min(blur / BLUR_FULL, 1.0)
    pose_t = max(0.0, 1.0 - abs(yaw) / YAW_MAX_DEG) * max(0.0, 1.0 - abs(pitch) / PITCH_MAX_DEG)
    q = W_SIZE * size_t + W_BLUR * blur_t + W_POSE * pose_t
    return FaceQuality(size_px, blur, yaw, pitch, float(min(max(q, 0.0), 1.0)))


__all__ = ["FaceQuality", "Tier", "estimate_pose", "face_quality", "tier_for"]
