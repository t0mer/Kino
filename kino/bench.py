"""Per-stage timing of the real detect / quality / embed pipeline (no database writes)."""

from __future__ import annotations

import statistics
import time
from dataclasses import dataclass, field
from pathlib import Path

from kino.config import Settings
from kino.pipeline.detect import Detector, detect_image
from kino.pipeline.embed import Embedder, align_face, should_embed
from kino.pipeline.prefilter import load_for_pipeline
from kino.pipeline.quality import face_quality, tier_for

TARGET_SECONDS = 2.5
STAGES = ("load", "detect", "quality", "embed")


@dataclass
class BenchResult:
    stages: dict[str, list[float]] = field(default_factory=lambda: {s: [] for s in STAGES})
    totals: list[float] = field(default_factory=list)
    faces: int = 0
    skipped: list[str] = field(default_factory=list)


def stats(values: list[float]) -> tuple[float, float, float]:
    """(mean, p50, p95) in seconds; p95 by nearest rank."""
    if not values:
        return 0.0, 0.0, 0.0
    ordered = sorted(values)
    rank = max(0, min(len(ordered) - 1, round(0.95 * len(ordered) + 0.5) - 1))
    return statistics.fmean(values), statistics.median(values), ordered[rank]


def _one(
    path: Path, detector: Detector, embedder: Embedder, settings: Settings
) -> tuple[dict[str, float], int] | None:
    t0 = time.perf_counter()
    loaded = load_for_pipeline(path)
    if loaded.bgr is None:
        return None
    img = loaded.bgr
    t1 = time.perf_counter()
    dets = detect_image(detector, img, settings)
    t2 = time.perf_counter()
    aligned = [align_face(img, d) for d in dets]
    tiers = [tier_for(face_quality(d, a).size_px) for d, a in zip(dets, aligned, strict=True)]
    t3 = time.perf_counter()
    for a, tier in zip(aligned, tiers, strict=True):
        if should_embed(tier):
            embedder.embed(a)
    t4 = time.perf_counter()
    times = {"load": t1 - t0, "detect": t2 - t1, "quality": t3 - t2, "embed": t4 - t3}
    return times, len(dets)


def run_bench(
    images: list[Path], detector: Detector, embedder: Embedder, settings: Settings, repeat: int
) -> BenchResult:
    result = BenchResult()
    if images:  # one untimed pass so session creation / first-run costs are not measured
        _one(images[0], detector, embedder, settings)
    for path in images:
        for _ in range(repeat):
            one = _one(path, detector, embedder, settings)
            if one is None:
                result.skipped.append(path.name)
                break
            times, n = one
            for k, v in times.items():
                result.stages[k].append(v)
            result.totals.append(sum(times.values()))
            result.faces += n
    return result
