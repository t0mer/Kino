"""/api/stats: dashboard counters."""

from __future__ import annotations

from fastapi import Request

from kino.api.deps import protected_router
from kino.api.schemas import KidStat, StatsOut
from kino.config import Settings
from kino.db.session import get_session
from kino.services import stats as svc

router = protected_router(prefix="/api/stats", tags=["stats"])


@router.get("", response_model=StatsOut)
def get_stats(request: Request) -> StatsOut:
    env: Settings = request.app.state.settings
    with get_session() as s:
        st = svc.collect(s, env.timezone)
        return StatsOut(
            images=st.images,
            queue_depth=st.queue_depth,
            review_count=st.review_count,
            forwards=st.forwards,
            forward_queue_depth=st.forward_queue_depth,
            kids=[
                KidStat(
                    id=k.kid.id,
                    display_name=k.kid.display_name,
                    today=k.today,
                    last_7_days=k.last_7_days,
                )
                for k in st.kids
            ],
        )
