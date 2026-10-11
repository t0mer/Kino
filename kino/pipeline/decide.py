"""Turn a face's tier and kid scores into a save / review / drop decision (§9.6).

This is the ONLY place the group prior is applied: a kid mapped to the face's group
gets ``-prior_bonus`` on both thresholds, an unmapped kid ``+unmapped_penalty``.
``Matcher.score`` returns raw scores; callers must not adjust them for the group.

Safety bounds (CLAUDE.md 9.6): a kid's ``threshold_offset`` is clamped to [OFFSET_MIN, OFFSET_MAX]
(the CLI refuses values outside it; anything else that reaches here is clamped with a
warning), and the effective auto-save threshold for tiers L/M never drops below
AUTO_FLOOR, whatever the combination of settings, offset and group prior. Without the
floor a low threshold plus a single enrolled kid (margin == score) would auto-save and
forward unrelated children.
"""

import math
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from loguru import logger
from sqlalchemy.orm import Session

from kino.db import models
from kino.pipeline.matcher import KidScore
from kino.pipeline.quality import Tier

Action = Literal["auto_save", "review", "drop"]
# Scores come from float32 dot products (error ~1e-7), so a score that is exactly on a
# threshold in exact arithmetic must still pass; 1e-6 absorbs that rounding.
_EPS = 1e-6

OFFSET_MIN = -0.1
OFFSET_MAX = 0.3
AUTO_FLOOR = 0.30


def clamp_offset(value: float) -> float:
    """Bound a kid threshold offset to [OFFSET_MIN, OFFSET_MAX] (non-finite -> 0)."""
    if not math.isfinite(value):
        logger.warning("ignoring non-finite kid threshold offset")
        return 0.0
    clamped = min(max(value, OFFSET_MIN), OFFSET_MAX)
    if clamped != value:
        logger.warning("kid threshold offset {:+.3f} clamped to {:+.2f}", value, clamped)
    return clamped


class KidLike(Protocol):
    @property
    def id(self) -> int: ...
    @property
    def auto_save_enabled(self) -> bool: ...
    @property
    def threshold_offset(self) -> float: ...


def _num(v: Any) -> float:
    if isinstance(v, bool) or not isinstance(v, int | float):
        raise TypeError(f"not a number: {type(v).__name__}")
    f = float(v)
    if not math.isfinite(f) or not -1.0 <= f <= 1.0:
        raise ValueError(f"out of range: {f}")
    return f


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
            if sub is None:
                continue
            keys = ("auto", "review") if tier != "S" else ("review",)
            try:
                vals = {k: _num(sub[k]) for k in keys if k in sub}
                if "auto" in vals and vals["auto"] < vals.get(
                    "review", getattr(t, f"{tier}_review")
                ):
                    raise ValueError("auto below review")
                if "review" in vals and tier != "S" and "auto" not in vals:
                    if getattr(t, f"{tier}_auto") < vals["review"]:
                        raise ValueError("auto below review")
            except (ValueError, TypeError, KeyError) as exc:
                logger.warning("ignoring invalid thresholds for tier {}: {}", tier, exc)
                continue
            for k, v in vals.items():
                setattr(t, f"{tier}_{k}", v)
        for key in ("prior_bonus", "unmapped_penalty", "margin_min"):
            if key in data:
                try:
                    setattr(t, key, _num(data[key]))
                except (ValueError, TypeError) as exc:
                    logger.warning("ignoring invalid threshold {}: {}", key, exc)
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
    """``scores`` is best-first; ``kid`` must be the best kid's info (ValueError
    otherwise). The face goes to the best kid only (one kid per face)."""
    if not scores:
        return Decision(None, "drop", 0.0, 0.0, "no candidate kids")
    best = scores[0]
    if kid.id != best.kid_id:
        raise ValueError(f"kid {kid.id} does not match best-scoring kid {best.kid_id}")

    def result(action: Action, reason: str) -> Decision:
        return Decision(best.kid_id, action, best.score, best.margin, reason)

    auto_t, review_t = thresholds.for_tier(tier)
    if review_t is None:
        return result("drop", "tier too small")
    shift = clamp_offset(kid.threshold_offset) + (
        -thresholds.prior_bonus if group_mapped else thresholds.unmapped_penalty
    )
    review_t += shift
    if best.score < review_t - _EPS:
        return result("drop", "below review threshold")
    if auto_t is None:
        return result("review", "tier never auto-saves")
    if best.score < max(auto_t + shift, AUTO_FLOOR) - _EPS:
        return result("review", "below auto-save threshold")
    if best.margin < thresholds.margin_min - _EPS:
        return result("review", "margin below minimum")
    if not kid.auto_save_enabled:
        return result("review", "auto-save disabled for kid")
    return result("auto_save", "above auto-save threshold")
