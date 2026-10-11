"""Forward saved photos to the destination chat (CLAUDE.md section 9.9)."""

from __future__ import annotations

import asyncio
import mimetypes
import random
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from filelock import FileLock, Timeout
from huey import crontab
from huey.consumer import Consumer
from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kino import metrics
from kino.config import get_settings
from kino.db.models import Forward, Group, Image, Kid, Setting
from kino.db.session import get_session
from kino.openwa.client import OpenWAClient, mask_path
from kino.pipeline.save import to_local
from kino.tasks import forward_huey

DEFAULT_CAPTION = "📸 {kids} · {group} · {date}"
MAX_CAPTION = 1024  # OpenWA SendMediaMessageDto caption maxLength
MAX_VALUE = 120

K_ENABLED = "forward.enabled"
K_DEFAULT = "forward.default_chat_id"
K_PER_KID = "forward.per_kid"
K_TEMPLATE = "forward.caption_template"
K_REVIEW = "forward.include_review_confirmed"


class ForwardError(Exception):
    """Invalid forwarding destination or configuration."""


class ForwardConfig(BaseModel):
    enabled: bool = False
    default_chat_id: str | None = None
    per_kid: dict[int, str] = Field(default_factory=dict)
    caption_template: str = DEFAULT_CAPTION
    include_review_confirmed: bool = True


def get_setting(session: Session, key: str, default: Any) -> Any:
    row = session.get(Setting, key)
    return default if row is None or row.value is None else row.value


def put_setting(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def get_forward_config(session: Session) -> ForwardConfig:
    raw_map = get_setting(session, K_PER_KID, {})
    per_kid: dict[int, str] = {}
    if isinstance(raw_map, dict):
        for k, v in raw_map.items():
            try:
                if v:
                    per_kid[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
    default = get_setting(session, K_DEFAULT, None)
    template = get_setting(session, K_TEMPLATE, DEFAULT_CAPTION)
    return ForwardConfig(
        enabled=bool(get_setting(session, K_ENABLED, False)),
        default_chat_id=str(default) if default else None,
        per_kid=per_kid,
        caption_template=template if isinstance(template, str) and template else DEFAULT_CAPTION,
        include_review_confirmed=bool(get_setting(session, K_REVIEW, True)),
    )


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _clean(text: str | None, limit: int = MAX_VALUE) -> str:
    return _CONTROL_RE.sub("", text or "")[:limit]


def render_caption(
    template: str, kids: list[str], group: str, sender: str, sent_at: datetime
) -> str:
    """Fill {kids} {group} {sender} {date} {time}; unknown placeholders stay literal.

    ``sent_at`` is rendered as given: callers pass it already in local time (KINO_TIMEZONE)."""
    values = {
        "kids": ", ".join(_clean(k) for k in kids),
        "group": _clean(group),
        "sender": _clean(sender),
        "date": f"{sent_at:%Y-%m-%d}",
        "time": f"{sent_at:%H:%M}",
    }
    out = _CONTROL_RE.sub(
        "", _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)
    )
    return _cap_utf16(out, MAX_CAPTION)


def _cap_utf16(text: str, limit: int) -> str:
    """Truncate to ``limit`` UTF-16 code units: OpenWA checks the JS string length."""
    units = 0
    for i, ch in enumerate(text):
        units += 2 if ord(ch) > 0xFFFF else 1
        if units > limit:
            return text[:i]
    return text


def image_caption(template: str, kids: list[str], image: Image, group: Group | None) -> str:
    """Caption for a forwarded photo, in local time (KINO_TIMEZONE)."""
    return render_caption(
        template,
        kids,
        (group.name if group else None) or "",
        image.sender_name or "",
        to_local(image.sent_at or datetime(1970, 1, 1), get_settings().timezone),
    )


def validate_destination(chat_id: str | None, session: Session) -> None:
    """Reject empty destinations and watched groups (their photos would be re-ingested)."""
    if not chat_id or not chat_id.strip():
        raise ForwardError("destination chat id is empty")
    watched = session.scalar(
        select(Group.id).where(Group.chat_id == chat_id.strip(), Group.watched.is_(True))
    )
    if watched is not None:
        raise ForwardError("destination is a watched group; forwarding there would loop")


def set_default_destination(session: Session, chat_id: str) -> None:
    chat_id = chat_id.strip()
    validate_destination(chat_id, session)
    put_setting(session, K_DEFAULT, chat_id)
    put_setting(session, K_ENABLED, True)


def ensure_not_destination(session: Session, chat_id: str) -> None:
    """Reject watching a chat that forwards go to (its own photos would be re-ingested)."""
    cfg = get_forward_config(session)
    destinations = {cfg.default_chat_id, *cfg.per_kid.values()} - {None}
    if chat_id.strip() in destinations:
        raise ForwardError("this group is a forward destination; it cannot also be watched")


def disable_forwarding(session: Session) -> None:
    put_setting(session, K_ENABLED, False)


def plan_forwards(
    session: Session,
    image: Image,
    matched_kids: list[Kid],
    saved_paths: dict[int, Path],
    group: Group,
) -> list[Forward]:
    """One Forward row per (image, destination); idempotent.

    An existing row for the same (image, destination) is reused as it is, whatever its status. A
    `cancelled` row is deliberately NOT revived automatically (the photo was removed from a kid
    on purpose); a new forward needs an explicit manual action.
    """
    cfg = get_forward_config(session)
    if not cfg.enabled or not cfg.default_chat_id and not cfg.per_kid:
        return []
    by_dest: dict[str, list[Kid]] = {}
    for kid in matched_kids:
        dest = cfg.per_kid.get(kid.id) or cfg.default_chat_id
        if dest:
            by_dest.setdefault(dest, []).append(kid)
    rows: list[Forward] = []
    for dest, kids in by_dest.items():
        try:
            validate_destination(dest, session)
        except ForwardError as e:
            logger.warning("skipping forward to {}: {}", mask_path(dest), e)
            continue
        existing = session.scalar(
            select(Forward).where(Forward.image_id == image.id, Forward.dest_chat_id == dest)
        )
        if existing is not None:
            rows.append(existing)
            continue
        first = next((saved_paths[k.id] for k in kids if k.id in saved_paths), None)
        caption = image_caption(cfg.caption_template, [k.display_name for k in kids], image, group)
        row = Forward(
            image_id=image.id,
            dest_chat_id=dest,
            kid_ids=[k.id for k in kids],
            caption=caption,
            status="pending",
            file_path=str(first) if first else None,
        )
        try:
            with session.begin_nested():
                session.add(row)
        except IntegrityError:  # lost a race; reuse the winner
            row = session.scalars(
                select(Forward).where(Forward.image_id == image.id, Forward.dest_chat_id == dest)
            ).one()
        rows.append(row)
    session.flush()
    return rows


def cancel_forwards(session: Session, image_id: int, kid_id: int) -> int:
    """Cancel pending forwards for the image that include the kid; sent rows are untouched."""
    n = 0
    for f in session.scalars(
        select(Forward).where(Forward.image_id == image_id, Forward.status == "pending")
    ):
        if kid_id in (f.kid_ids or []):
            f.status = "cancelled"
            metrics.forwards.labels(status="cancelled").inc()
            n += 1
    return n


# --- sender -----------------------------------------------------------------------------

BACKOFF_SECONDS = (60, 300, 1800, 7200)  # retries 1..4 after a failure
MAX_FILE_BYTES = 20 * 1024 * 1024
STALE_PENDING = timedelta(minutes=5)
CLAIM_LEASE = timedelta(minutes=5)
_DIGITS_RE = re.compile(r"\d{6,}")


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def sanitize_error(e: BaseException) -> str:
    """Error text safe to store: no long digit runs (phone numbers), bounded length."""
    return _DIGITS_RE.sub("****", f"{type(e).__name__}: {e}")[:300]


class RateLimiter:
    """Spaces sends by at least 60/rate seconds plus 2-5 s jitter. Clock/sleeper/rng injectable."""

    def __init__(
        self,
        rate_per_min: int,
        clock: Callable[[], float] = time.monotonic,
        sleeper: Callable[[float], None] = time.sleep,
        rng: random.Random | None = None,
    ) -> None:
        self.interval = 60.0 / max(rate_per_min, 1)
        self._clock = clock
        self._sleep = sleeper
        self._rng = rng or random.Random()
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            gap = self.interval + self._rng.uniform(2.0, 5.0)
            delay = self._last + gap - self._clock()
            if delay > 0:
                self._sleep(delay)
        self._last = self._clock()


_limiter: RateLimiter | None = None


def _default_limiter() -> RateLimiter:
    global _limiter
    if _limiter is None:
        _limiter = RateLimiter(get_settings().forward_rate_per_min)
    return _limiter


def _photos_dir() -> Path:
    return get_settings().photos_dir


def _recheck(forward_id: int, dest: str) -> str | None:
    """Re-validate just before sending; returns a final result, or None to go ahead."""
    with get_session() as s:
        f = s.get(Forward, forward_id)
        if f is None or f.status != "pending":
            return "skipped"  # cancelled (or otherwise settled) while waiting
        reason = None
        cfg = get_forward_config(s)
        if not cfg.enabled:
            reason = "forwarding was disabled"
        elif dest not in {cfg.default_chat_id, *cfg.per_kid.values()}:
            # `forward set` moved the destination while this row waited; the old chat must
            # not keep receiving photos.
            reason = "destination changed"
        else:
            try:
                validate_destination(dest, s)
            except ForwardError as e:
                reason = str(e)
        if reason is None:
            return None
        f.status = "cancelled"
        f.last_error = reason
        f.next_attempt_at = None
        metrics.forwards.labels(status="cancelled").inc()
        s.flush()
        return "cancelled"


def _read_saved_file(path: str | None) -> bytes:
    """Read the saved photo; it must be inside the photos dir and at most MAX_FILE_BYTES."""
    if not path:
        raise FileNotFoundError("saved photo file is missing")
    p = Path(path).resolve()
    if not p.is_relative_to(_photos_dir().resolve()):
        raise PermissionError("saved photo path is outside the photos directory")
    if not p.is_file():
        raise FileNotFoundError("saved photo file is missing")
    if p.stat().st_size > MAX_FILE_BYTES:
        raise ValueError("saved photo file is too large to forward")
    return p.read_bytes()


def _default_client() -> OpenWAClient:
    st = get_settings()
    if not (st.openwa_url and st.openwa_api_key and st.openwa_session_id):
        raise ForwardError("OpenWA is not configured")
    return OpenWAClient(st.openwa_url, st.openwa_api_key.get_secret_value(), st.openwa_session_id)


def _fail(forward_id: int, error: str, *, terminal: bool, now: datetime) -> None:
    """Record a failed attempt (attempts was already incremented by the claim)."""
    with get_session() as s:
        f = s.get(Forward, forward_id)
        if f is None or f.status != "pending":
            return
        f.last_error = error
        if terminal or f.attempts > len(BACKOFF_SECONDS):
            f.status = "failed"
            f.next_attempt_at = None
            metrics.forwards.labels(status="failed").inc()
        else:
            f.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[f.attempts - 1])
        s.flush()


def _claim(forward_id: int, now: datetime) -> bool:
    """Atomically lease a due pending row; only the caller that gets rowcount 1 may send."""
    with get_session() as s:
        res = s.execute(
            update(Forward)
            .where(
                Forward.id == forward_id,
                Forward.status == "pending",
                or_(Forward.next_attempt_at.is_(None), Forward.next_attempt_at <= now),
            )
            .values(attempts=Forward.attempts + 1, next_attempt_at=now + CLAIM_LEASE)
        )
        return bool(res.rowcount == 1)  # type: ignore[attr-defined]


def deliver_forward(
    forward_id: int,
    *,
    client_factory: Callable[[], Any] = _default_client,
    limiter: RateLimiter | None = None,
    now: Callable[[], datetime] = _utcnow,
) -> str:
    """Send one pending forward; returns the resulting status (or 'skipped').

    The row is leased atomically first, so duplicate queue entries cannot double-send. Delivery
    is at-least-once: a crash after the send but before the row is marked sent re-sends once the
    lease expires.
    """
    if not _claim(forward_id, now()):
        return "skipped"
    with get_session() as s:
        f = s.get(Forward, forward_id)
        if f is None:
            return "skipped"
        dest, caption, path = f.dest_chat_id, f.caption or "", f.file_path
    try:
        data = _read_saved_file(path)
    except (OSError, ValueError) as e:
        _fail(forward_id, sanitize_error(e), terminal=True, now=now())
        return "failed"
    mimetype = mimetypes.guess_type(path or "")[0] or "image/jpeg"
    (limiter or _default_limiter()).wait()
    verdict = _recheck(forward_id, dest)
    if verdict is not None:
        return verdict

    async def send() -> str:
        client = client_factory()
        try:
            return str(await client.send_image(dest, data, mimetype, caption))
        finally:
            close = getattr(client, "aclose", None)
            if close is not None:
                await close()

    try:
        message_id = asyncio.run(send())
    except Exception as e:  # any send failure is retried/recorded, never crashes the worker
        logger.warning("forward {} to {} failed: {}", forward_id, mask_path(dest), type(e).__name__)
        _fail(forward_id, sanitize_error(e), terminal=False, now=now())
        return "retry"
    with get_session() as s:
        f = s.get(Forward, forward_id)
        if f is not None:
            f.status = "sent"
            # Stored as returned. The documented send-image messageId is the serialized
            # `true_<chat>_<id>` form; the webhook echo guard normalises it and bare ids alike.
            f.openwa_message_id = message_id
            f.sent_at = now()
            f.next_attempt_at = None
            f.last_error = None
            metrics.forwards.labels(status="sent").inc()
            s.flush()
    return "sent"


@forward_huey.task()  # type: ignore[untyped-decorator]
def send_forward(forward_id: int) -> None:
    """Send one forward. The consumer for `forward_huey` must run with a single worker."""
    deliver_forward(forward_id)


def enqueue_due_forwards(now: datetime | None = None) -> list[int]:
    """List pending rows that are due. Duplicate enqueues are harmless: `_claim` is atomic."""
    now = now or _utcnow()
    due: list[int] = []
    with get_session() as s:
        for f in s.scalars(select(Forward).where(Forward.status == "pending")):
            if f.next_attempt_at is not None:
                is_due = f.next_attempt_at <= now
            else:
                is_due = f.created_at <= now - STALE_PENDING
            if is_due:
                due.append(f.id)
        s.flush()
    return due


@forward_huey.periodic_task(crontab(minute="*/5"))  # type: ignore[untyped-decorator]
def retry_forwards() -> None:
    for fid in enqueue_due_forwards():
        send_forward(fid)


def retry_forward(session: Session, forward_id: int) -> bool:
    """Manual retry of a failed forward: reset to pending (caller enqueues send_forward)."""
    f = session.get(Forward, forward_id)
    if f is None or f.status != "failed":
        return False
    f.status = "pending"
    f.attempts = 0
    f.last_error = None
    f.next_attempt_at = None
    return True


def run_forward_consumer(
    data_dir: Path | None = None,
    consumer_factory: Callable[..., Any] = Consumer,
) -> None:
    """Run the forward queue consumer: exactly one worker thread, one process (file lock)."""
    lock_path = (data_dir or get_settings().data_dir) / "forward_consumer.lock"
    lock = FileLock(str(lock_path), timeout=0)
    try:
        lock.acquire()
    except Timeout:
        raise ForwardError("another forward consumer is already running") from None
    try:
        consumer_factory(forward_huey, workers=1, worker_type="thread").run()
    finally:
        lock.release()
