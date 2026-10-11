"""SCRFD post-processing helpers (algorithm from InsightFace, MIT; see LICENSE)."""

import numpy as np
from numpy.typing import NDArray

F32 = NDArray[np.float32]


def make_anchors(height: int, width: int, stride: int, num_anchors: int) -> F32:
    """Anchor centres in input pixels, shape (H*W*num_anchors, 2), (x, y) order."""
    ys, xs = np.mgrid[0:height, 0:width]
    centres = np.stack([xs, ys], axis=-1).astype(np.float32) * stride
    centres = centres.reshape(-1, 2)
    if num_anchors > 1:
        centres = np.stack([centres] * num_anchors, axis=1).reshape(-1, 2)
    return centres


def distance2bbox(points: F32, distance: F32) -> F32:
    """Decode (left, top, right, bottom) distances to x1,y1,x2,y2 boxes."""
    x1 = points[:, 0] - distance[:, 0]
    y1 = points[:, 1] - distance[:, 1]
    x2 = points[:, 0] + distance[:, 2]
    y2 = points[:, 1] + distance[:, 3]
    return np.stack([x1, y1, x2, y2], axis=-1)


def distance2kps(points: F32, distance: F32) -> F32:
    """Decode keypoint offsets (N, 2K) to absolute coordinates (N, 2K)."""
    out = np.empty_like(distance)
    out[:, 0::2] = points[:, 0:1] + distance[:, 0::2]
    out[:, 1::2] = points[:, 1:2] + distance[:, 1::2]
    return out


def nms(dets: F32, thresh: float) -> list[int]:
    """Greedy NMS over rows [x1, y1, x2, y2, score]; returns kept indices."""
    if len(dets) == 0:
        return []
    x1, y1, x2, y2, scores = dets[:, 0], dets[:, 1], dets[:, 2], dets[:, 3], dets[:, 4]
    areas = (x2 - x1 + 1) * (y2 - y1 + 1)
    order = scores.argsort()[::-1]
    keep: list[int] = []
    while order.size > 0:
        i = int(order[0])
        keep.append(i)
        xx1 = np.maximum(x1[i], x1[order[1:]])
        yy1 = np.maximum(y1[i], y1[order[1:]])
        xx2 = np.minimum(x2[i], x2[order[1:]])
        yy2 = np.minimum(y2[i], y2[order[1:]])
        w = np.maximum(0.0, xx2 - xx1 + 1)
        h = np.maximum(0.0, yy2 - yy1 + 1)
        inter = w * h
        ovr = inter / (areas[i] + areas[order[1:]] - inter)
        order = order[1:][ovr <= thresh]
    return keep
