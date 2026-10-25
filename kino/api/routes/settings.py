"""/api/settings: forwarding, digest, thresholds, retention; read-only models and version."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import Request
from sqlalchemy import select

import kino
from kino.api.deps import protected_router
from kino.api.routes.telegram import telegram_summary
from kino.api.schemas import (
    CaptionPreview,
    CaptionPreviewIn,
    Destination,
    DigestIn,
    DigestOut,
    ForwardSettings,
    ModelInfo,
    Retention,
    SettingsOut,
    ThresholdsIn,
    ThresholdsOut,
)
from kino.config import Settings
from kino.db.models import Kid
from kino.db.session import get_session
from kino.forward import ForwardConfig, render_caption
from kino.models.registry import MODELS
from kino.pipeline.embed import MODEL_NAMES
from kino.pipeline.save import to_local
from kino.services import settings_svc as svc

router = protected_router(prefix="/api/settings", tags=["settings"])


def _env(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def _dest(channel: str, chat_id: str) -> Destination:
    return Destination.model_validate({"channel": channel, "chat_id": chat_id})


def _forward(cfg: ForwardConfig) -> ForwardSettings:
    default = _dest(cfg.default_channel, cfg.default_chat_id) if cfg.default_chat_id else None
    per_kid = {k: _dest(cfg.per_kid_channel.get(k, "whatsapp"), c) for k, c in cfg.per_kid.items()}
    return ForwardSettings(
        enabled=cfg.enabled,
        # legacy fields: the WhatsApp destinations only
        default_chat_id=default.chat_id if default and default.channel == "whatsapp" else None,
        per_kid={k: d.chat_id for k, d in per_kid.items() if d.channel == "whatsapp"},
        default=default,
        per_kid_destinations=per_kid,
        caption_template=cfg.caption_template,
        include_review_confirmed=cfg.include_review_confirmed,
        send_caption=cfg.send_caption,
    )


def _digest(cfg: svc.DigestConfig) -> DigestOut:
    dest = _dest(cfg.channel, cfg.chat_id) if cfg.chat_id else None
    return DigestOut(
        chat_id=cfg.chat_id if dest and dest.channel == "whatsapp" else None,
        destination=dest,
        cron=cfg.cron,
        apprise_urls=[svc.mask_url(u) for u in cfg.apprise_urls],
        public_base_url=cfg.public_base_url,
    )


def _models(env: Settings) -> list[ModelInfo]:
    out = []
    for name in ("scrfd_10g", MODEL_NAMES[env.embedder]):
        spec = MODELS[name]
        out.append(
            ModelInfo(
                name=name,
                filename=spec.filename,
                license=spec.license,
                sha256=spec.sha256,
                present=(env.data_dir / "models" / spec.filename).is_file(),
            )
        )
    return out


@router.get("", response_model=SettingsOut)
def get_all(request: Request) -> SettingsOut:
    env = _env(request)
    with get_session() as s:
        return SettingsOut(
            forward=_forward(svc.get_forward_config(s)),
            digest=_digest(svc.get_digest(s, env)),
            telegram=telegram_summary(s, env),
            thresholds=ThresholdsOut.model_validate(svc.get_thresholds(s)),
            retention=Retention.model_validate(svc.get_retention(s, env)),
            embedder=env.embedder,
            models=_models(env),
            version=kino.__version__,
        )


@router.get("/forward", response_model=ForwardSettings)
def get_forward() -> ForwardSettings:
    with get_session() as s:
        return _forward(svc.get_forward_config(s))


@router.put("/forward", response_model=ForwardSettings)
def put_forward(body: ForwardSettings) -> ForwardSettings:
    default, per_kid = body.resolved()
    with get_session() as s:
        return _forward(
            svc.update_forward(
                s,
                enabled=body.enabled,
                default_chat_id=default.chat_id if default else None,
                default_channel=default.channel if default else "whatsapp",
                per_kid={k: d.chat_id for k, d in per_kid.items()},
                per_kid_channel={k: d.channel for k, d in per_kid.items()},
                caption_template=body.caption_template,
                include_review_confirmed=body.include_review_confirmed,
                send_caption=body.send_caption,
            )
        )


@router.post("/forward/preview", response_model=CaptionPreview)
def preview_caption(request: Request, body: CaptionPreviewIn) -> CaptionPreview:
    """Render a caption template with sample values (the first kids' real names)."""
    with get_session() as s:
        names = list(s.scalars(select(Kid.display_name).order_by(Kid.id).limit(2)))
    now = to_local(datetime.now(UTC).replace(tzinfo=None), _env(request).timezone)
    caption = render_caption(
        body.caption_template, names or ["Noa", "Itai"], "Class 3B", "Dana", now
    )
    return CaptionPreview(caption=caption)


@router.get("/digest", response_model=DigestOut)
def get_digest(request: Request) -> DigestOut:
    with get_session() as s:
        return _digest(svc.get_digest(s, _env(request)))


@router.put("/digest", response_model=DigestOut)
def put_digest(request: Request, body: DigestIn) -> DigestOut:
    if "destination" in body.model_fields_set:
        dest = body.destination
    else:
        dest = Destination(channel="whatsapp", chat_id=body.chat_id) if body.chat_id else None
    with get_session() as s:
        return _digest(
            svc.update_digest(
                s,
                _env(request),
                chat_id=dest.chat_id if dest else None,
                channel=dest.channel if dest else "whatsapp",
                cron=body.cron,
                apprise_urls=body.apprise_urls,
                public_base_url=body.public_base_url,
            )
        )


@router.get("/thresholds", response_model=ThresholdsOut)
def get_thresholds() -> ThresholdsOut:
    with get_session() as s:
        return ThresholdsOut.model_validate(svc.get_thresholds(s))


@router.put("/thresholds", response_model=ThresholdsOut)
def put_thresholds(body: ThresholdsIn) -> ThresholdsOut:
    with get_session() as s:
        return ThresholdsOut.model_validate(svc.update_thresholds(s, body.model_dump()))


@router.get("/retention", response_model=Retention)
def get_retention(request: Request) -> Retention:
    with get_session() as s:
        return Retention.model_validate(svc.get_retention(s, _env(request)))


@router.put("/retention", response_model=Retention)
def put_retention(body: Retention) -> Retention:
    with get_session() as s:
        return Retention.model_validate(svc.update_retention(s, **body.model_dump()))
