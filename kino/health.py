"""Gallery health (CLAUDE.md sections 10 and 11.6): flag reference faces that look wrong.

An active reference is an outlier when, relative to its kid's gallery,

* ``far_from_centroid``: its cosine similarity to the kid's active centroid is below
  mean - 2 sigma of the kid's reference similarities (needs at least MIN_REFS active
  references, otherwise nothing is flagged), or
* ``closer_to_other_kid``: it is more similar to another kid's centroid than to its own
  (the other kid must also have MIN_REFS active references, so a thin gallery cannot
  produce flags).

The nightly task persists the verdict on ``reference_faces.outlier`` / ``outlier_reason``
so the Gallery can order by it; :func:`evaluate_kid` computes it live without writing.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from loguru import logger
from numpy.typing import NDArray
from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.db.models import Kid, ReferenceFace
from kino.db.session import blob_to_embedding, get_session

MIN_REFS = 5
SIGMAS = 2.0
FAR = "far_from_centroid"
OTHER = "closer_to_other_kid"


@dataclass
class Outlier:
    reference_id: int
    similarity: float
    reason: str


@dataclass
class KidHealth:
    kid_id: int
    active_refs: int
    evaluated: bool
    mean_similarity: float | None = None
    threshold: float | None = None
    outliers: list[Outlier] = field(default_factory=list)


def _unit(v: NDArray[np.float32]) -> NDArray[np.float32]:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _load(session: Session) -> dict[int, tuple[list[int], NDArray[np.float32]]]:
    """kid id -> (reference ids, unit-norm embedding matrix) for active references."""
    ids: dict[int, list[int]] = {}
    vecs: dict[int, list[NDArray[np.float32]]] = {}
    for ref_id, kid_id, blob in session.execute(
        select(ReferenceFace.id, ReferenceFace.kid_id, ReferenceFace.embedding)
        .where(ReferenceFace.status == "active")
        .order_by(ReferenceFace.id)
    ):
        ids.setdefault(kid_id, []).append(ref_id)
        vecs.setdefault(kid_id, []).append(_unit(blob_to_embedding(blob)))
    return {k: (ids[k], np.vstack(vecs[k])) for k in ids}


def _evaluate(kid_id: int, data: dict[int, tuple[list[int], NDArray[np.float32]]]) -> KidHealth:
    ids, mat = data.get(kid_id, ([], np.zeros((0, 1), dtype=np.float32)))
    n = len(ids)
    if n < MIN_REFS:
        return KidHealth(kid_id, n, evaluated=False)
    centroid = _unit(mat.mean(axis=0))
    own = mat @ centroid
    mean, std = float(own.mean()), float(own.std())
    threshold = mean - SIGMAS * std
    others = [
        _unit(m.mean(axis=0)) for k, (i, m) in data.items() if k != kid_id and len(i) >= MIN_REFS
    ]
    other_sims = (mat @ np.vstack(others).T).max(axis=1) if others else None
    out: list[Outlier] = []
    for idx, ref_id in enumerate(ids):
        sim = float(own[idx])
        if other_sims is not None and float(other_sims[idx]) > sim:
            out.append(Outlier(ref_id, sim, OTHER))
        elif sim < threshold:
            out.append(Outlier(ref_id, sim, FAR))
    return KidHealth(kid_id, n, True, mean, threshold, out)


def evaluate_kid(session: Session, kid_id: int) -> KidHealth:
    """Live health of one kid's active gallery (no writes)."""
    return _evaluate(kid_id, _load(session))


def refresh_outlier_flags(session: Session) -> dict[int, int]:
    """Recompute and persist the flags for every kid; returns kid id -> outlier count."""
    data = _load(session)
    verdict: dict[int, Outlier] = {}
    counts: dict[int, int] = {}
    for kid_id in session.scalars(select(Kid.id)):
        h = _evaluate(kid_id, data)
        counts[kid_id] = len(h.outliers)
        verdict.update({o.reference_id: o for o in h.outliers})
    for ref in session.scalars(select(ReferenceFace)):
        o = verdict.get(ref.id)
        ref.outlier = o is not None
        ref.outlier_reason = o.reason if o else None
    session.flush()
    return counts


def run_gallery_health() -> dict[int, int]:
    with get_session() as s:
        counts = refresh_outlier_flags(s)
    total = sum(counts.values())
    if total:
        logger.info("gallery health: {} outlier reference(s) flagged", total)
    return counts
