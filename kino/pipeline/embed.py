"""Face embedding (ArcFace-style ONNX) and display crops. No restoration/super-resolution."""

from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from kino.config import Settings
from kino.models.download import ensure_model
from kino.pipeline._insightface import norm_crop
from kino.pipeline.detect import Detection
from kino.pipeline.quality import Tier

EMBED_DIM = 512
_MODEL_NAMES = {"auraface": "auraface_glintr100", "buffalo_l": "buffalo_l_r50"}


class Embedder(Protocol):
    def embed(self, aligned_112_bgr: NDArray[np.uint8]) -> NDArray[np.float32]: ...


class OnnxEmbedder:
    def __init__(self, model_path: Path | str, ort_threads: int = 2) -> None:
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = ort_threads
        self._session = ort.InferenceSession(
            str(model_path), opts, providers=["CPUExecutionProvider"]
        )
        self._input = self._session.get_inputs()[0].name

    def embed(self, aligned_112_bgr: NDArray[np.uint8]) -> NDArray[np.float32]:
        rgb = cv2.cvtColor(aligned_112_bgr, cv2.COLOR_BGR2RGB)
        blob = ((rgb.astype(np.float32) - 127.5) / 127.5).transpose(2, 0, 1)[None]
        out = np.asarray(self._session.run(None, {self._input: blob})[0], dtype=np.float32)
        vec = out.reshape(-1)
        if vec.shape[0] != EMBED_DIM:
            raise ValueError(f"embedder produced {vec.shape[0]}-D output, expected {EMBED_DIM}")
        norm = float(np.linalg.norm(vec))
        if norm == 0.0:
            raise ValueError("embedder produced a zero vector")
        return (vec / norm).astype(np.float32)


def get_embedder(settings: Settings) -> Embedder:
    path = ensure_model(_MODEL_NAMES[settings.embedder], settings.data_dir)
    return OnnxEmbedder(path, settings.ort_threads)


def should_embed(tier: Tier) -> bool:
    """XS faces are never embedded (CLAUDE.md 9.3)."""
    return tier is not Tier.XS


def align_face(img: NDArray[np.uint8], det: Detection) -> NDArray[np.uint8]:
    return norm_crop(img, det.kps, 112)


def make_crop(
    img: NDArray[np.uint8], det: Detection, size: int = 160, margin: float = 0.3
) -> bytes:
    """JPEG display crop around the face, padded (not stretched) where it leaves the image."""
    x1, y1, x2, y2 = det.bbox
    side = max(x2 - x1, y2 - y1, 1.0) * (1 + 2 * margin)
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    scale = size / side
    m = np.array([[scale, 0, size / 2 - cx * scale], [0, scale, size / 2 - cy * scale]])
    out = cv2.warpAffine(
        img,
        m,
        (size, size),
        flags=cv2.INTER_AREA if scale < 1 else cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=(0, 0, 0),
    )
    ok, buf = cv2.imencode(".jpg", out, [cv2.IMWRITE_JPEG_QUALITY, 90])
    if not ok:
        raise ValueError("failed to encode crop")
    return bytes(buf.tobytes())
