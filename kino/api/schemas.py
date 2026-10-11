"""Request/response models for the REST API. Inputs forbid unknown fields."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from urllib.parse import urlsplit

from huey import crontab
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from kino.forward import MAX_CAPTION
from kino.pipeline.decide import AUTO_FLOOR, OFFSET_MAX, OFFSET_MIN


class In(BaseModel):
    model_config = ConfigDict(extra="forbid")


Offset = Annotated[float, Field(ge=OFFSET_MIN, le=OFFSET_MAX, allow_inf_nan=False)]
BirthYear = Annotated[int, Field(ge=1900, le=2100)]
Name = Annotated[str, Field(min_length=1, max_length=100)]
SlugIn = Annotated[str, Field(min_length=1, max_length=40)]
ChatId = Annotated[str, Field(min_length=1, max_length=200, pattern=r"^\S+$")]

# --- kids ---------------------------------------------------------------------------------


class KidCreate(In):
    display_name: Name
    slug: SlugIn | None = None
    birth_year: BirthYear | None = None
    active: bool = True
    auto_save_enabled: bool = False
    threshold_offset: Offset = 0.0
    group_ids: list[int] = Field(default_factory=list, max_length=500)


class KidUpdate(In):
    display_name: Name | None = None
    slug: SlugIn | None = None
    birth_year: BirthYear | None = None
    active: bool | None = None
    auto_save_enabled: bool | None = None
    threshold_offset: Offset | None = None
    group_ids: list[int] | None = Field(default=None, max_length=500)


class KidOut(BaseModel):
    id: int
    display_name: str
    slug: str
    birth_year: int | None
    active: bool
    auto_save_enabled: bool
    threshold_offset: float
    group_ids: list[int]
    reference_count: int
    today: int
    last_7_days: int
    created_at: datetime


class KidDeleted(BaseModel):
    id: int
    removed_files: int
    cancelled_forwards: int


# --- groups -------------------------------------------------------------------------------


class GroupUpdate(In):
    watched: bool | None = None
    kid_ids: list[int] | None = Field(default=None, max_length=500)


class GroupOut(BaseModel):
    id: int
    chat_id: str
    name: str | None
    slug: str | None
    watched: bool
    kid_ids: list[int]
    is_forward_destination: bool
    created_at: datetime
    last_seen_at: datetime | None


class GroupsRefreshed(BaseModel):
    imported: int
    total: int


# --- settings -----------------------------------------------------------------------------

Unit = Annotated[float, Field(ge=-1.0, le=1.0, allow_inf_nan=False)]
Days = Annotated[int, Field(ge=1, le=3650)]
Caption = Annotated[str, Field(min_length=1, max_length=MAX_CAPTION)]


class ForwardSettings(In):
    enabled: bool
    default_chat_id: ChatId | None
    per_kid: dict[int, ChatId] = Field(default_factory=dict, max_length=500)
    caption_template: Caption
    include_review_confirmed: bool


class CaptionPreviewIn(In):
    caption_template: Annotated[str, Field(max_length=MAX_CAPTION)]


class CaptionPreview(BaseModel):
    caption: str


def _check_url(value: str, schemes: tuple[str, ...]) -> str:
    parts = urlsplit(value)
    if parts.scheme.lower() not in schemes or not parts.netloc:
        raise ValueError(f"must be a URL starting with {' or '.join(s + '://' for s in schemes)}")
    return value


class DigestIn(In):
    chat_id: ChatId | None = None
    cron: Annotated[str, Field(max_length=100)] = ""
    # None keeps the stored URLs (the UI never sees them in clear text).
    apprise_urls: list[Annotated[str, Field(min_length=3, max_length=2000)]] | None = Field(
        default=None, max_length=20
    )
    public_base_url: Annotated[str, Field(max_length=500)] | None = None

    @field_validator("cron")
    @classmethod
    def _cron(cls, v: str) -> str:
        v = v.strip()
        if not v:
            return v
        fields = v.split()
        if len(fields) != 5:
            raise ValueError("cron needs 5 fields: minute hour day month weekday")
        try:
            crontab(*fields)
        except ValueError as e:
            raise ValueError(f"invalid cron expression: {e}") from None
        return " ".join(fields)

    @field_validator("apprise_urls")
    @classmethod
    def _apprise(cls, v: list[str] | None) -> list[str] | None:
        if v is not None:
            for url in v:
                if not urlsplit(url).scheme or "://" not in url:
                    raise ValueError("each notification URL needs a scheme, e.g. tgram://...")
        return v

    @field_validator("public_base_url")
    @classmethod
    def _base(cls, v: str | None) -> str | None:
        return _check_url(v.strip(), ("http", "https")) if v and v.strip() else None


class DigestOut(BaseModel):
    chat_id: str | None
    cron: str
    apprise_urls: list[str]  # masked: scheme only
    public_base_url: str | None


class TierAutoReview(In):
    auto: Unit
    review: Unit

    @model_validator(mode="after")
    def _order(self) -> TierAutoReview:
        if self.auto < self.review:
            raise ValueError("auto must be at least review")
        if self.auto < AUTO_FLOOR:
            raise ValueError(f"auto must be at least {AUTO_FLOOR:.2f} (the auto-save floor)")
        return self


class TierReview(In):
    review: Unit


class ThresholdsIn(In):
    L: TierAutoReview
    M: TierAutoReview
    S: TierReview
    prior_bonus: Unit
    unmapped_penalty: Unit
    margin_min: Unit


class ThresholdsOut(BaseModel):
    L: TierAutoReview
    M: TierAutoReview
    S: TierReview
    prior_bonus: float
    unmapped_penalty: float
    margin_min: float
    auto_floor: float  # read-only: auto-save never happens below this score


class Retention(In):
    raw_days: Days
    face_days: Days


class ModelInfo(BaseModel):
    name: str
    filename: str
    license: str
    sha256: str
    present: bool


class SettingsOut(BaseModel):
    forward: ForwardSettings
    digest: DigestOut
    thresholds: ThresholdsOut
    retention: Retention
    embedder: str
    models: list[ModelInfo]
    version: str


# --- OpenWA / forwards / stats ------------------------------------------------------------


class ChatOut(BaseModel):
    id: str
    name: str | None
    is_group: bool
    watched: bool
    selectable: bool  # watched groups can never be a forward destination


class WebhookStatus(BaseModel):
    registered: bool
    active: bool | None = None
    id: str | None = None
    url: str | None = None
    events: list[str] = Field(default_factory=list)


class WebhookRegisterIn(In):
    url: Annotated[str, Field(max_length=500)] | None = None

    @field_validator("url")
    @classmethod
    def _url(cls, v: str | None) -> str | None:
        return _check_url(v.strip(), ("http", "https")) if v and v.strip() else None


class WebhookRegistered(BaseModel):
    action: str
    id: str
    url: str
    events: list[str]


class ForwardTestIn(In):
    chat_id: ChatId


class ForwardTestOut(BaseModel):
    sent: bool


class ForwardOut(BaseModel):
    id: int
    image_id: int
    dest_chat_id: str
    kid_ids: list[int]
    caption: str | None
    status: str
    attempts: int
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None
    next_attempt_at: datetime | None


class KidStat(BaseModel):
    id: int
    display_name: str
    today: int
    last_7_days: int


class StatsOut(BaseModel):
    images: dict[str, int]
    queue_depth: int
    review_count: int
    forwards: dict[str, int]
    forward_queue_depth: int
    kids: list[KidStat]
