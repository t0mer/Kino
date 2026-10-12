"""Review digest (CLAUDE.md section 10): tell the user when photos wait for review.

Scheduling: the huey task ``review_digest`` fires every minute and calls
:func:`run_digest_if_due`, which checks whether the digest cron expression (UI setting, else
``KINO_DIGEST_CRON``; empty disables) matches the current minute in the configured timezone.
huey's own ``crontab`` cannot take a runtime expression, hence the per-minute tick; the
check is a cheap string match before any DB work.

A digest goes out only when there are pending review items, the pending set (a fingerprint of
the match ids) differs from the one the previous digest reported, and a destination chat is
set. The ``digests`` row is written only after a successful send; a failed send is logged
(sanitised) and retried on the next matching minute.

Apprise: the ``apprise`` package is not a dependency of Kino, so ``apprise_urls`` are not
used yet (a warning is logged once if any are configured).
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from loguru import logger
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from kino.config import Settings, get_settings
from kino.db.models import Digest, Face, Kid, Match
from kino.db.session import get_session
from kino.forward import sanitize_error
from kino.openwa.client import OpenWAClient, mask_path
from kino.services.settings_svc import DigestConfig, get_digest

_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 7))
_apprise_warned = False


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        rng, _, step_s = part.partition("/")
        step = int(step_s) if step_s else 1
        if step < 1:
            raise ValueError("bad step")
        if rng == "*":
            a, b = lo, hi
        elif "-" in rng:
            x, _, y = rng.partition("-")
            a, b = int(x), int(y)
        else:
            a = int(rng)
            b = hi if step_s else a
        if a < lo or b > hi or a > b:
            raise ValueError("out of range")
        out.update(range(a, b + 1, step))
    return out


def cron_matches(expr: str, when: datetime) -> bool:
    """Standard 5-field cron (``*``, lists, ranges, ``/step``; weekday 0 or 7 = Sunday)
    against ``when`` (local time, minute resolution). Day-of-month and weekday combine with
    OR when both are restricted, as in Vixie cron. Invalid expressions never match."""
    fields = expr.split()
    if len(fields) != 5:
        return False
    try:
        minute, hour, dom, month, dow = (
            _field(f, lo, hi) for f, (lo, hi) in zip(fields, _RANGES, strict=True)
        )
    except ValueError:
        return False
    if 7 in dow:
        dow = (dow - {7}) | {0}
    weekday = (when.weekday() + 1) % 7  # Monday=0 -> cron Monday=1, Sunday=0
    if when.minute not in minute or when.hour not in hour or when.month not in month:
        return False
    dom_ok, dow_ok = when.day in dom, weekday in dow
    if fields[2] != "*" and fields[4] != "*":
        return dom_ok or dow_ok
    return dom_ok and dow_ok


@dataclass
class Pending:
    fingerprint: str
    photos: int
    by_kid: dict[str, int]  # kid display name -> pending photos


def pending_review(s: Session) -> Pending:
    rows = s.execute(
        select(Match.id, Match.kid_id, Face.image_id)
        .join(Face, Face.id == Match.face_id)
        .where(Match.decision == "review")
        .order_by(Match.id)
    ).all()
    fingerprint = hashlib.sha256(",".join(str(r[0]) for r in rows).encode()).hexdigest()
    images: set[int] = set()
    per_kid: dict[int, set[int]] = {}
    for _, kid_id, image_id in rows:
        images.add(image_id)
        per_kid.setdefault(kid_id, set()).add(image_id)
    names = dict(s.execute(select(Kid.id, Kid.display_name).where(Kid.id.in_(per_kid))).all())
    by_kid = {
        names.get(k, "?"): len(v) for k, v in sorted(per_kid.items(), key=lambda kv: -len(kv[1]))
    }
    return Pending(fingerprint, len(images), by_kid)


def render_message(p: Pending, base_url: str | None) -> str:
    noun = "photo" if p.photos == 1 else "photos"
    text = f"{p.photos} {noun} waiting for review"
    if p.by_kid:
        text += " (" + ", ".join(f"{n} {c}" for n, c in p.by_kid.items()) + ")"
    if base_url:
        text += f" · {base_url.rstrip('/')}/review"
    return text


def _default_client(st: Settings) -> OpenWAClient:
    if not (st.openwa_url and st.openwa_api_key and st.openwa_session_id):
        raise RuntimeError("OpenWA is not configured")
    return OpenWAClient(st.openwa_url, st.openwa_api_key.get_secret_value(), st.openwa_session_id)


def run_digest(
    settings: Settings | None = None,
    *,
    client_factory: Callable[[Settings], OpenWAClient] | None = None,
) -> bool:
    """Send a digest now if one is warranted; returns True when one was sent."""
    global _apprise_warned
    st = settings or get_settings()
    with get_session() as s:
        cfg: DigestConfig = get_digest(s, st)
        pending = pending_review(s)
        last = s.scalar(select(Digest.pending_hash).order_by(Digest.id.desc()).limit(1))
        count = s.scalar(select(func.count()).select_from(Digest)) or 0
    if cfg.apprise_urls and not _apprise_warned:
        _apprise_warned = True
        logger.warning("digest: apprise is not installed; notification URLs are ignored")
    if not cfg.chat_id or pending.photos == 0:
        return False
    if count and last == pending.fingerprint:
        return False
    text = render_message(pending, cfg.public_base_url)
    try:
        client = (client_factory or _default_client)(st)
        asyncio.run(client.send_text(cfg.chat_id, text))
    except Exception as e:
        logger.warning("digest to {} failed: {}", mask_path(cfg.chat_id), sanitize_error(e))
        return False
    with get_session() as s:
        s.add(
            Digest(
                sent_at=_utcnow(),
                pending_count=pending.photos,
                dest_chat_id=cfg.chat_id,
                pending_hash=pending.fingerprint,
            )
        )
    logger.info("digest sent to {}: {} photo(s) pending", mask_path(cfg.chat_id), pending.photos)
    return True


def run_digest_if_due(
    now: datetime | None = None,
    settings: Settings | None = None,
    *,
    client_factory: Callable[[Settings], OpenWAClient] | None = None,
) -> bool:
    st = settings or get_settings()
    with get_session() as s:
        cron = get_digest(s, st).cron
    if not cron.strip():
        return False
    local = (now or datetime.now(UTC)).astimezone(ZoneInfo(st.timezone)).replace(tzinfo=None)
    if not cron_matches(cron, local):
        return False
    return run_digest(st, client_factory=client_factory)
