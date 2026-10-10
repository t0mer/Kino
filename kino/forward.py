"""Forward saved photos to the destination chat (CLAUDE.md section 9.9)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any

from loguru import logger
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kino.db.models import Forward, Group, Image, Kid, Setting
from kino.openwa.client import mask_path

DEFAULT_CAPTION = "📸 {kids} · {group} · {date}"
MAX_CAPTION = 1000
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


def _get(session: Session, key: str, default: Any) -> Any:
    row = session.get(Setting, key)
    return default if row is None or row.value is None else row.value


def _put(session: Session, key: str, value: Any) -> None:
    row = session.get(Setting, key)
    if row is None:
        session.add(Setting(key=key, value=value))
    else:
        row.value = value


def get_forward_config(session: Session) -> ForwardConfig:
    raw_map = _get(session, K_PER_KID, {})
    per_kid: dict[int, str] = {}
    if isinstance(raw_map, dict):
        for k, v in raw_map.items():
            try:
                if v:
                    per_kid[int(k)] = str(v)
            except (TypeError, ValueError):
                continue
    default = _get(session, K_DEFAULT, None)
    template = _get(session, K_TEMPLATE, DEFAULT_CAPTION)
    return ForwardConfig(
        enabled=bool(_get(session, K_ENABLED, False)),
        default_chat_id=str(default) if default else None,
        per_kid=per_kid,
        caption_template=template if isinstance(template, str) and template else DEFAULT_CAPTION,
        include_review_confirmed=bool(_get(session, K_REVIEW, True)),
    )


_CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")
_PLACEHOLDER_RE = re.compile(r"\{(\w+)\}")


def _clean(text: str | None, limit: int = MAX_VALUE) -> str:
    return _CONTROL_RE.sub("", text or "")[:limit]


def render_caption(
    template: str, kids: list[str], group: str, sender: str, sent_at: datetime
) -> str:
    """Fill {kids} {group} {sender} {date} {time}; unknown placeholders stay literal."""
    values = {
        "kids": ", ".join(_clean(k) for k in kids),
        "group": _clean(group),
        "sender": _clean(sender),
        "date": f"{sent_at:%Y-%m-%d}",
        "time": f"{sent_at:%H:%M}",
    }
    out = _PLACEHOLDER_RE.sub(lambda m: values.get(m.group(1), m.group(0)), template)
    return _clean(out, MAX_CAPTION)


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
    _put(session, K_DEFAULT, chat_id)
    _put(session, K_ENABLED, True)


def plan_forwards(
    session: Session,
    image: Image,
    matched_kids: list[Kid],
    saved_paths: dict[int, Path],
    group: Group,
) -> list[Forward]:
    """One Forward row per (image, destination); idempotent."""
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
        caption = render_caption(
            cfg.caption_template,
            [k.display_name for k in kids],
            group.name or "",
            image.sender_name or "",
            image.sent_at or datetime(1970, 1, 1),
        )
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
            n += 1
    return n


def forward_blocks_raw_cleanup(session: Session, image_id: int) -> bool:
    """True while any forward for the image is still pending (raw file must be kept)."""
    return (
        session.scalar(
            select(Forward.id).where(Forward.image_id == image_id, Forward.status == "pending")
        )
        is not None
    )
