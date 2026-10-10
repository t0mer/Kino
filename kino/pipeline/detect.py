"""Face detection: SCRFD plus an optional tiled pass for small/distant faces."""

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from kino.config import Settings
from kino.pipeline._insightface import distance2bbox, distance2kps, make_anchors, nms

MIN_DET_SCORE = 0.5
TILE_DET_SIZE = 640
NMS_IOU = 0.4
_STRIDES = (8, 16, 32)
_NUM_ANCHORS = 2


@dataclass
class Detection:
    """A face in full-image pixel coordinates."""

    bbox: tuple[float, float, float, float]
    score: float
    kps: NDArray[np.float32]
    from_tile: bool = False

    @property
    def size_px(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return min(x2 - x1, y2 - y1)


class Detector(Protocol):
    def detect(self, img_bgr: NDArray[np.uint8], det_size: int) -> list[Detection]: ...


def decode_outputs(
    outputs: list[NDArray[np.float32]], det_size: int, det_scale: float, thresh: float
) -> list[Detection]:
    """Decode the 9 SCRFD-10G outputs (scores, boxes, kps per stride) to detections."""
    fmc = len(_STRIDES)
    dets: list[Detection] = []
    for idx, stride in enumerate(_STRIDES):
        scores = outputs[idx].reshape(-1)
        boxes = outputs[idx + fmc].reshape(-1, 4) * stride
        kps = outputs[idx + 2 * fmc].reshape(-1, 10) * stride
        side = det_size // stride
        anchors = make_anchors(side, side, stride, _NUM_ANCHORS)
        keep = np.where(scores >= thresh)[0]
        if keep.size == 0:
            continue
        b = distance2bbox(anchors[keep], boxes[keep]) / det_scale
        k = distance2kps(anchors[keep], kps[keep]) / det_scale
        for s, bb, kk in zip(scores[keep], b, k, strict=True):
            dets.append(
                Detection(
                    (float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])),
                    float(s),
                    kk.reshape(5, 2).astype(np.float32),
                )
            )
    if not dets:
        return dets
    arr = np.array([[*d.bbox, d.score] for d in dets], dtype=np.float32)
    return [dets[i] for i in nms(arr, NMS_IOU)]


class ScrfdDetector:
    """SCRFD-10GF on onnxruntime CPU."""

    def __init__(self, model_path: Path | str, ort_threads: int = 2) -> None:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = ort_threads
        self._sess = ort.InferenceSession(
            str(model_path), sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self._input = self._sess.get_inputs()[0].name
        self._out_names = [o.name for o in self._sess.get_outputs()]

    def detect(self, img_bgr: NDArray[np.uint8], det_size: int) -> list[Detection]:
        h, w = img_bgr.shape[:2]
        # Never upscale: SCRFD scores drop for faces blown up well past native size.
        scale = min(1.0, det_size / max(h, w))
        new_w, new_h = max(1, round(w * scale)), max(1, round(h * scale))
        resized = cv2.resize(img_bgr, (new_w, new_h))
        canvas = np.zeros((det_size, det_size, 3), dtype=np.uint8)
        canvas[:new_h, :new_w] = resized
        blob = cv2.dnn.blobFromImage(
            canvas, 1.0 / 128, (det_size, det_size), (127.5, 127.5, 127.5), swapRB=True
        )
        outs = self._sess.run(self._out_names, {self._input: blob})
        outputs = [np.asarray(o, dtype=np.float32) for o in outs]
        return decode_outputs(outputs, det_size, scale, MIN_DET_SCORE)


def choose_grid(w: int, h: int) -> int:
    """Tiles per side: 2 when the long side is <= 2000 px, else 3."""
    return 2 if max(w, h) <= 2000 else 3


def tile_grid(w: int, h: int, grid: int, overlap: float) -> list[tuple[int, int, int, int]]:
    """Overlapping tiles (x0, y0, x1, y1) that together cover the whole image."""

    def spans(size: int) -> list[tuple[int, int]]:
        tile = size / (1 + (grid - 1) * (1 - overlap))
        step = tile * (1 - overlap)
        out = []
        for i in range(grid):
            a = round(i * step)
            b = size if i == grid - 1 else min(size, round(i * step + tile))
            out.append((a, b))
        return out

    return [(x0, y0, x1, y1) for (y0, y1) in spans(h) for (x0, x1) in spans(w)]


def needs_tiling(mode: str, w: int, h: int, full_dets: list[Detection]) -> bool:
    """Whether the tiled pass should run (spec 9.2)."""
    if mode == "off":
        return False
    if mode == "always":
        return True
    return min(w, h) >= 1200 or not full_dets or any(d.size_px < 40 for d in full_dets)


def _iou(a: tuple[float, float, float, float], b: tuple[float, float, float, float]) -> float:
    iw = min(a[2], b[2]) - max(a[0], b[0])
    ih = min(a[3], b[3]) - max(a[1], b[1])
    if iw <= 0 or ih <= 0:
        return 0.0
    inter = iw * ih
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def merge_nms(dets: list[Detection], iou: float = NMS_IOU) -> list[Detection]:
    """Greedy NMS preferring higher score; from_tile only if no full-pass member."""
    remaining = sorted(dets, key=lambda d: d.score, reverse=True)
    out: list[Detection] = []
    while remaining:
        best = remaining[0]
        group = [d for d in remaining if d is best or _iou(best.bbox, d.bbox) > iou]
        remaining = [d for d in remaining if all(d is not g for g in group)]
        out.append(Detection(best.bbox, best.score, best.kps, all(g.from_tile for g in group)))
    return out


def detect_image(detector: Detector, img: NDArray[np.uint8], settings: Settings) -> list[Detection]:
    """Full pass, plus tiled pass when needed; merged and score-filtered."""
    h, w = img.shape[:2]
    full = [d for d in detector.detect(img, settings.det_size) if d.score >= MIN_DET_SCORE]
    all_dets = list(full)
    if needs_tiling(settings.tiling, w, h, full):
        grid = choose_grid(w, h) if settings.tile_grid == "auto" else int(settings.tile_grid[0])
        for x0, y0, x1, y1 in tile_grid(w, h, grid, settings.tile_overlap):
            for d in detector.detect(img[y0:y1, x0:x1], TILE_DET_SIZE):
                if d.score < MIN_DET_SCORE:
                    continue
                bx1, by1, bx2, by2 = d.bbox
                all_dets.append(
                    Detection(
                        (bx1 + x0, by1 + y0, bx2 + x0, by2 + y0),
                        d.score,
                        (d.kps + np.array([x0, y0], dtype=np.float32)).astype(np.float32),
                        True,
                    )
                )
    return merge_nms(all_dets)
