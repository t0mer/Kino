"""Gallery matcher: scores a face embedding against every kid's references.

Group prior accounting lives in ONE place: ``decide.decide`` (via its
``group_mapped`` flag, obtained from ``Matcher.group_mapped``). ``Matcher.score``
returns raw scores only (reference weighting, top-k mean, negative penalty,
margin) and never applies the +/- group threshold adjustments.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

import numpy as np
from numpy.typing import NDArray
from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.db import models
from kino.db.session import blob_to_embedding

TOP_K = 3
RECENCY_FLOOR = 0.2
RECENCY_HALF_LIFE_DAYS = 365.0
NEGATIVE_PENALTY = 0.1

Vec = NDArray[np.float32]


def _utcnow() -> datetime:
    """Naive UTC, consistent with models._now."""
    return datetime.now(UTC).replace(tzinfo=None)


@dataclass
class KidScore:
    kid_id: int
    score: float
    margin: float  # best score minus second best (second = 0 with a single kid)


@dataclass(frozen=True)
class KidInfo:
    id: int
    auto_save_enabled: bool
    threshold_offset: float


@dataclass
class _Gallery:
    refs: Vec  # (n, d)
    weights: Vec  # (n,)
    negatives: Vec | None  # (m, d)


class Matcher:
    def __init__(self, now: Callable[[], datetime] = _utcnow) -> None:
        self._now = now
        self._galleries: dict[int, _Gallery] = {}
        self._kids: dict[int, KidInfo] = {}
        self._groups: set[tuple[int, int]] = set()
        self._version: object = object()  # sentinel: never equals a real version

    def _read_version(self, session: Session) -> object:
        row = session.get(models.Setting, "gallery_version")
        return None if row is None else row.value

    def _load_kids(self, session: Session) -> None:
        """Live kid settings + kid-group mappings; cheap, refreshed on every maybe_reload."""
        from kino.pipeline.decide import clamp_offset  # decide imports this module

        self._kids = {
            k.id: KidInfo(k.id, k.auto_save_enabled, clamp_offset(k.threshold_offset))
            for k in session.scalars(select(models.Kid).where(models.Kid.active.is_(True)))
        }
        self._groups = {(kg.kid_id, kg.group_id) for kg in session.scalars(select(models.KidGroup))}

    def _load_embeddings(self, session: Session) -> None:
        """Heavy part (reference/negative matrices), cached by gallery_version."""
        now = self._now()
        refs: dict[int, list[tuple[Vec, float]]] = {}
        for r in session.scalars(
            select(models.ReferenceFace).where(models.ReferenceFace.status == "active")
        ):
            if r.photo_date is None:
                recency = 1.0
            else:
                age = max((now - r.photo_date).total_seconds() / 86400.0, 0.0)
                recency = max(0.5 ** (age / RECENCY_HALF_LIFE_DAYS), RECENCY_FLOOR)
            quality = 1.0 if r.quality is None else r.quality
            emb = blob_to_embedding(r.embedding)
            refs.setdefault(r.kid_id, []).append((emb, quality * recency))
        negs: dict[int, list[Vec]] = {}
        for n in session.scalars(select(models.NegativeFace)):
            negs.setdefault(n.kid_id, []).append(blob_to_embedding(n.embedding))
        galleries: dict[int, _Gallery] = {}
        for kid_id, items in refs.items():
            neg = negs.get(kid_id)
            galleries[kid_id] = _Gallery(
                np.stack([e for e, _ in items]).astype(np.float32),
                np.array([w for _, w in items], dtype=np.float32),
                np.stack(neg).astype(np.float32) if neg else None,
            )
        self._galleries = galleries
        self._version = self._read_version(session)

    def reload(self, session: Session) -> None:
        self._load_kids(session)
        self._load_embeddings(session)

    def maybe_reload(self, session: Session) -> bool:
        """Always refresh kid settings/mappings; rebuild embeddings only if
        settings.gallery_version changed. Returns whether embeddings were rebuilt."""
        self._load_kids(session)
        if self._read_version(session) == self._version:
            return False
        self._load_embeddings(session)
        return True

    def kid(self, kid_id: int) -> KidInfo:
        return self._kids[kid_id]

    def group_mapped(self, kid_id: int, group_id: int | None) -> bool:
        return group_id is not None and (kid_id, group_id) in self._groups

    def score(self, face_emb: Vec, group_id: int | None) -> list[KidScore]:
        """Raw per-kid scores, best first. ``group_id`` is accepted for API symmetry
        but the group prior is applied in ``decide`` only (see module docstring)."""
        del group_id
        face = np.asarray(face_emb, dtype=np.float32)
        raw: list[tuple[int, float]] = []
        for kid_id, g in self._galleries.items():
            if kid_id not in self._kids:  # inactive or deleted kid
                continue
            sims = g.refs @ face
            k = min(TOP_K, len(sims))
            idx = np.argsort(-sims, kind="stable")[:k]
            wsum = float(g.weights[idx].sum())
            score = float((sims[idx] * g.weights[idx]).sum() / wsum) if wsum > 0 else 0.0
            if g.negatives is not None and float((g.negatives @ face).max()) >= score:
                score -= NEGATIVE_PENALTY
            raw.append((kid_id, score))
        if not raw:
            return []
        raw.sort(key=lambda t: (-t[1], t[0]))
        margin = raw[0][1] - (raw[1][1] if len(raw) > 1 else 0.0)
        return [KidScore(k, s, margin) for k, s in raw]
