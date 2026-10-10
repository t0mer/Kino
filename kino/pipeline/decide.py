"""Turn a face's tier and kid scores into a save / review / drop decision (§9.6).

This is the ONLY place the group prior is applied: a kid mapped to the face's group
gets ``-prior_bonus`` on both thresholds, an unmapped kid ``+unmapped_penalty``.
``Matcher.score`` returns raw scores; callers must not adjust them for the group.
"""

from dataclasses import dataclass
from typing import Any, Literal, Protocol

from sqlalchemy.orm import Session

from kino.db import models
from kino.pipeline.matcher import KidScore
from kino.pipeline.quality import Tier

Action = Literal["auto_save", "review", "drop"]
_EPS = 1e-9


class KidLike(Protocol):
    @property
    def auto_save_enabled(self) -> bool: ...
    @property
    def threshold_offset(self) -> float: ...


@dataclass
class Thresholds:
    L_auto: float = 0.45
    L_review: float = 0.32
    M_auto: float = 0.50
    M_review: float = 0.35
    S_review: float = 0.40  # tier S never auto-saves
    prior_bonus: float = 0.03
    unmapped_penalty: float = 0.05
    margin_min: float = 0.08

    @classmethod
    def from_settings(cls, session: Session) -> "Thresholds":
        """Read settings key ``thresholds``: {"L": {"auto", "review"}, "M": {...},
        "S": {"review"}, "prior_bonus", "unmapped_penalty", "margin_min"}."""
        row = session.get(models.Setting, "thresholds")
        t = cls()
        data: Any = row.value if row is not None else None
        if not isinstance(data, dict):
            return t
        for tier in ("L", "M", "S"):
            sub = data.get(tier)
            if isinstance(sub, dict):
                for key in ("auto", "review"):
                    if key in sub and hasattr(t, f"{tier}_{key}"):
                        setattr(t, f"{tier}_{key}", float(sub[key]))
        for key in ("prior_bonus", "unmapped_penalty", "margin_min"):
            if key in data:
                setattr(t, key, float(data[key]))
        return t

    def for_tier(self, tier: Tier) -> tuple[float | None, float | None]:
        if tier is Tier.L:
            return self.L_auto, self.L_review
        if tier is Tier.M:
            return self.M_auto, self.M_review
        if tier is Tier.S:
            return None, self.S_review
        return None, None


@dataclass
class Decision:
    kid_id: int | None
    action: Action
    score: float
    margin: float
    reason: str


def decide(
    tier: Tier,
    scores: list[KidScore],
    kid: KidLike,
    thresholds: Thresholds,
    group_mapped: bool,
) -> Decision:
    """``scores`` is best-first and ``kid`` is the best kid's info; the face goes to
    the best kid only (one kid per face)."""
    if not scores:
        return Decision(None, "drop", 0.0, 0.0, "no candidate kids")
    best = scores[0]

    def result(action: Action, reason: str) -> Decision:
        return Decision(best.kid_id, action, best.score, best.margin, reason)

    auto_t, review_t = thresholds.for_tier(tier)
    if review_t is None:
        return result("drop", "tier too small")
    shift = kid.threshold_offset + (
        -thresholds.prior_bonus if group_mapped else thresholds.unmapped_penalty
    )
    review_t += shift
    if best.score < review_t - _EPS:
        return result("drop", "below review threshold")
    if auto_t is None:
        return result("review", "tier never auto-saves")
    if best.score < auto_t + shift - _EPS:
        return result("review", "below auto-save threshold")
    if best.margin < thresholds.margin_min - _EPS:
        return result("review", "margin below minimum")
    if not kid.auto_save_enabled:
        return result("review", "auto-save disabled for kid")
    return result("auto_save", "above auto-save threshold")
