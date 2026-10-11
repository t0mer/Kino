"""Counters for the dashboard (GET /api/stats)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import InstrumentedAttribute, Session

from kino.db.models import FORWARD_STATUS, IMAGE_STATUS, Forward, Image, Match
from kino.services.kids import KidSummary, summarize_kids


@dataclass
class Stats:
    images: dict[str, int]
    forwards: dict[str, int]
    review_count: int
    kids: list[KidSummary]

    @property
    def queue_depth(self) -> int:
        return self.images["queued"]

    @property
    def forward_queue_depth(self) -> int:
        return self.forwards["pending"]


def _by_status(
    session: Session, column: InstrumentedAttribute[str], statuses: tuple[str, ...]
) -> dict[str, int]:
    counts = {s: 0 for s in statuses}
    for status, n in session.execute(select(column, func.count()).group_by(column)).all():
        counts[str(status)] = int(n)
    return counts


def collect(session: Session, timezone: str) -> Stats:
    return Stats(
        images=_by_status(session, Image.status, IMAGE_STATUS),
        forwards=_by_status(session, Forward.status, FORWARD_STATUS),
        review_count=session.scalar(
            select(func.count()).select_from(Match).where(Match.decision == "review")
        )
        or 0,
        kids=summarize_kids(session, timezone),
    )
