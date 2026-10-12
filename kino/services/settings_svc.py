"""Runtime settings stored in the DB ``settings`` table (CLAUDE.md §6, §11.7).

Sections: ``forward`` (keys ``forward.*``, owned by kino.forward), ``digest``, ``thresholds``
(read by :meth:`kino.pipeline.decide.Thresholds.from_settings`) and ``retention``. Values are
validated by the API schemas before they reach these functions; defaults come from the
environment (KINO_*).

Background jobs (M3: retention cleanup, review digest) must read their values through
:func:`get_retention` / :func:`get_digest` here, never straight from the environment, so that
edits made in the UI take effect.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from kino.config import Settings
from kino.db.models import Kid
from kino.forward import (
    DEFAULT_CAPTION,
    K_DEFAULT,
    K_ENABLED,
    K_PER_KID,
    K_REVIEW,
    K_SEND_CAPTION,
    K_TEMPLATE,
    ForwardConfig,
    get_forward_config,
    get_setting,
    put_setting,
    validate_destination,
)
from kino.pipeline.decide import AUTO_FLOOR, Thresholds
from kino.services import InvalidError

K_DIGEST = "digest"
K_RETENTION = "retention"
K_THRESHOLDS = "thresholds"
K_WEBHOOK_URL = "openwa.webhook_url"
WEBHOOK_PATH = "/api/webhook/openwa"
DEFAULT_WEBHOOK_URL = "http://kino-api:8080" + WEBHOOK_PATH

__all__ = ["DEFAULT_CAPTION", "ForwardConfig", "get_forward_config"]


# --- forward ------------------------------------------------------------------------------


def update_forward(
    session: Session,
    *,
    enabled: bool,
    default_chat_id: str | None,
    per_kid: dict[int, str],
    caption_template: str,
    include_review_confirmed: bool,
    send_caption: bool = True,
) -> ForwardConfig:
    """Replace the forwarding section. Every destination is checked against watched groups
    (forwarding into a watched group would re-ingest the photos)."""
    default = default_chat_id.strip() if default_chat_id else None
    per_kid = {k: v.strip() for k, v in per_kid.items() if v and v.strip()}
    known = set(session.scalars(select(Kid.id).where(Kid.id.in_(per_kid))))
    if missing := set(per_kid) - known:
        raise InvalidError(f"unknown kid id(s): {', '.join(map(str, sorted(missing)))}")
    for dest in {default, *per_kid.values()} - {None}:
        validate_destination(dest, session)
    if enabled and not default and not per_kid:
        raise InvalidError("pick a destination chat before enabling forwarding")
    put_setting(session, K_ENABLED, enabled)
    put_setting(session, K_DEFAULT, default)
    put_setting(session, K_PER_KID, {str(k): v for k, v in sorted(per_kid.items())})
    put_setting(session, K_TEMPLATE, caption_template)
    put_setting(session, K_REVIEW, include_review_confirmed)
    put_setting(session, K_SEND_CAPTION, send_caption)
    session.flush()
    return get_forward_config(session)


# --- digest -------------------------------------------------------------------------------


@dataclass
class DigestConfig:
    chat_id: str | None
    cron: str  # empty disables the digest
    apprise_urls: list[str] = field(default_factory=list)
    public_base_url: str | None = None


def get_digest(session: Session, env: Settings) -> DigestConfig:
    stored = get_setting(session, K_DIGEST, None)
    if not isinstance(stored, dict):
        urls = [u.strip() for u in (env.apprise_urls or "").split(",") if u.strip()]
        return DigestConfig(chat_id=None, cron=env.digest_cron, apprise_urls=urls)
    return DigestConfig(
        chat_id=stored.get("chat_id") or None,
        cron=str(stored.get("cron") or ""),
        apprise_urls=[str(u) for u in stored.get("apprise_urls") or []],
        public_base_url=stored.get("public_base_url") or None,
    )


def update_digest(
    session: Session,
    env: Settings,
    *,
    chat_id: str | None,
    cron: str,
    apprise_urls: list[str] | None,
    public_base_url: str | None,
) -> DigestConfig:
    """Replace the digest section; ``apprise_urls=None`` keeps the stored URLs (they hold
    tokens and are never sent back to the browser)."""
    current = get_digest(session, env)
    urls = (
        current.apprise_urls
        if apprise_urls is None
        else _resolve_masked(apprise_urls, current.apprise_urls)
    )
    cfg = DigestConfig(
        chat_id=chat_id.strip() if chat_id else None,
        cron=cron.strip(),
        apprise_urls=urls,
        public_base_url=public_base_url.rstrip("/") if public_base_url else None,
    )
    put_setting(session, K_DIGEST, asdict(cfg))
    session.flush()
    return cfg


def _resolve_masked(incoming: list[str], stored: list[str]) -> list[str]:
    """The UI only ever sees masked URLs and sends them back unchanged. A value equal to the
    mask of the stored URL at the same index means "keep that URL"; any other masked value
    cannot be resolved and is rejected (it must never overwrite a real URL)."""
    out = []
    for i, url in enumerate(incoming):
        if i < len(stored) and url == mask_url(stored[i]):
            out.append(stored[i])
        elif url.partition("://")[2] == "****":
            raise InvalidError(
                f"notification URL {i + 1} is masked and does not match the stored one; "
                "re-enter it in full"
            )
        else:
            out.append(url)
    return out


def mask_url(url: str) -> str:
    """Keep only the scheme of a notification URL: the rest usually holds credentials."""
    scheme = urlsplit(url).scheme
    return f"{scheme}://****" if scheme else "****"


# --- thresholds ---------------------------------------------------------------------------


def thresholds_dict(t: Thresholds) -> dict[str, Any]:
    return {
        "L": {"auto": t.L_auto, "review": t.L_review},
        "M": {"auto": t.M_auto, "review": t.M_review},
        "S": {"review": t.S_review},
        "prior_bonus": t.prior_bonus,
        "unmapped_penalty": t.unmapped_penalty,
        "margin_min": t.margin_min,
        "auto_floor": AUTO_FLOOR,
    }


def get_thresholds(session: Session) -> dict[str, Any]:
    return thresholds_dict(Thresholds.from_settings(session))


def update_thresholds(session: Session, data: dict[str, Any]) -> dict[str, Any]:
    """Store thresholds, then read them back through ``Thresholds.from_settings`` (the
    pipeline's own parser). Anything it would ignore is rejected instead of saved."""
    put_setting(session, K_THRESHOLDS, data)
    session.flush()
    got = get_thresholds(session)
    if {k: got[k] for k in data} != data:
        raise InvalidError("thresholds were not accepted by the pipeline's threshold parser")
    return got


# --- retention ----------------------------------------------------------------------------


def get_retention(session: Session, env: Settings) -> dict[str, int]:
    stored = get_setting(session, K_RETENTION, None)
    out = {"raw_days": env.raw_retention_days, "face_days": env.face_retention_days}
    if isinstance(stored, dict):
        for key in out:
            v = stored.get(key)
            if isinstance(v, int) and not isinstance(v, bool) and v > 0:
                out[key] = v
    return out


def update_retention(session: Session, *, raw_days: int, face_days: int) -> dict[str, int]:
    put_setting(session, K_RETENTION, {"raw_days": raw_days, "face_days": face_days})
    session.flush()
    return {"raw_days": raw_days, "face_days": face_days}


# --- OpenWA webhook URL -------------------------------------------------------------------


def stored_webhook_url(session: Session) -> str | None:
    v = get_setting(session, K_WEBHOOK_URL, None)
    return str(v) if v else None


def default_webhook_url(session: Session, env: Settings) -> str:
    """Last registered URL, else the public base URL + webhook path, else the Docker default."""
    stored = stored_webhook_url(session)
    if stored:
        return stored
    base = get_digest(session, env).public_base_url
    return base + WEBHOOK_PATH if base else DEFAULT_WEBHOOK_URL


def remember_webhook_url(session: Session, url: str) -> None:
    put_setting(session, K_WEBHOOK_URL, url)
    session.flush()
