"""Request/response models for the REST API. Inputs forbid unknown fields."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
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


# --- enrollment: uploads, references, coverage --------------------------------------------

TempFaceId = Annotated[str, Field(min_length=1, max_length=64)]


class BBox(BaseModel):
    x: float
    y: float
    w: float
    h: float


class UploadFaceOut(BaseModel):
    temp_face_id: str
    bbox: BBox
    quality: float
    tier: str
    selectable: bool
    reason: str | None  # "too small" | "too blurry" | "profile too extreme"
    crop_url: str


class UploadOut(BaseModel):
    upload_id: str
    filename: str
    width: int | None
    height: int | None
    photo_date: datetime | None
    faces: list[UploadFaceOut]
    error: str | None = None
    status: int = 200  # per-file outcome: 200, or 413/415/422 for a rejected file


class ReferencesIn(In):
    temp_face_ids: list[TempFaceId] = Field(default_factory=list, max_length=200)
    face_ids: list[int] = Field(default_factory=list, max_length=200)
    source: Literal["upload", "cluster", "review"] = "upload"
    photo_date: datetime | None = None

    @model_validator(mode="after")
    def _some(self) -> ReferencesIn:
        if not self.temp_face_ids and not self.face_ids:
            raise ValueError("pick at least one face (temp_face_ids or face_ids)")
        return self


class ReferenceOut(BaseModel):
    id: int
    kid_id: int
    face_id: int | None
    source: str
    status: str
    quality: float | None
    photo_date: datetime | None
    added_at: datetime
    weight: float
    yaw: float | None
    crop_url: str | None


class ReferencesAdded(BaseModel):
    created: list[ReferenceOut]
    duplicates: int


class ReferenceUpdate(In):
    status: Literal["active", "retired"] | None = None
    kid_id: int | None = None
    photo_date: datetime | None = None


class NegativeOut(BaseModel):
    id: int
    kid_id: int
    source_face_id: int | None
    added_at: datetime


class CoverageOut(BaseModel):
    kid_id: int
    count: int
    recommended: int
    yaw_known: int
    yaw_min: float | None
    yaw_max: float | None
    left: int
    frontal: int
    right: int
    dated: int
    older_than_year: int
    oldest: datetime | None
    newest: datetime | None
    hints: list[str]


# --- review queue and saved photos ----------------------------------------------------------


class NearRefOut(BaseModel):
    reference_id: int
    similarity: float
    crop_url: str | None


class ReviewItemOut(BaseModel):
    match_id: int
    face_id: int
    image_id: int
    kid_id: int
    kid_name: str
    score: float
    margin: float | None
    tier: str
    method: str
    group_id: int | None
    group_name: str | None
    sent_at: datetime | None
    bbox: BBox
    crop_url: str | None
    image_url: str | None  # R4: None when neither a saved copy nor the raw file exists
    nearest_refs: list[NearRefOut]


class ReviewPage(BaseModel):
    items: list[ReviewItemOut]
    next_cursor: int | None


class ReviewCount(BaseModel):
    total: int
    by_kid: dict[int, int]


class ReviewActionIn(In):
    action: Literal["confirm", "reject", "someone_else", "reassign"]
    kid_id: int | None = None

    @model_validator(mode="after")
    def _kid(self) -> ReviewActionIn:
        if (self.action == "reassign") != (self.kid_id is not None):
            raise ValueError("kid_id is required for reassign and only allowed there")
        return self


class ReviewResult(BaseModel):
    match_id: int
    decision: str
    kid_id: int
    changed: bool  # False: the same action had already been applied (idempotent repeat)
    saved_photo_id: int | None = None
    reference_ids: list[int] = Field(default_factory=list)
    negative_id: int | None = None
    forward_ids: list[int] = Field(default_factory=list)


class ReviewBulkIn(In):
    match_ids: list[int] = Field(min_length=1, max_length=200)
    action: Literal["confirm", "reject"]


class ReviewBulkItem(BaseModel):
    match_id: int
    status: int  # HTTP-like per-item status: 200 ok, 404/409/422/502 on failure
    detail: str | None = None
    result: ReviewResult | None = None


class ReviewBulkOut(BaseModel):
    results: list[ReviewBulkItem]


class PhotoOut(BaseModel):
    id: int
    image_id: int
    kid_id: int
    kid_name: str
    group_id: int | None
    group_name: str | None
    sent_at: datetime | None
    saved_at: datetime
    file_url: str


class PhotoPage(BaseModel):
    items: list[PhotoOut]
    next_cursor: int | None


class PhotoRemoved(BaseModel):
    id: int
    removed_files: int
    cancelled_forwards: int
    rejected_matches: int
    retired_references: int


# --- images / activity --------------------------------------------------------------------


class ImageListItem(BaseModel):
    id: int
    group_id: int | None
    group_name: str | None
    sender: str | None
    sent_at: datetime | None
    received_at: datetime
    status: str
    skip_reason: str | None
    processing_ms: int | None
    error: str | None
    face_count: int
    matches: dict[str, int]
    has_file: bool


class ImagePage(BaseModel):
    items: list[ImageListItem]
    next_cursor: int | None


class ImageFaceOut(BaseModel):
    id: int
    bbox: tuple[float, float, float, float]  # x, y, w, h in upright image pixels
    det_score: float
    size_px: float
    quality: float | None
    tier: str
    from_tile: bool
    crop_url: str | None


class ImageMatchOut(BaseModel):
    id: int
    face_id: int
    kid_id: int
    kid_name: str
    score: float
    margin: float | None
    method: str
    decision: str
    decided_by: str


class ImageForwardOut(BaseModel):
    id: int
    status: str
    attempts: int
    kid_ids: list[int]
    last_error: str | None
    created_at: datetime
    sent_at: datetime | None


class ImageDetailOut(BaseModel):
    id: int
    group_id: int | None
    group_name: str | None
    sender: str | None
    sent_at: datetime | None
    received_at: datetime
    width: int | None
    height: int | None
    status: str
    skip_reason: str | None
    duplicate_of: int | None
    processing_ms: int | None
    error: str | None
    has_file: bool
    file_url: str | None
    faces: list[ImageFaceOut]
    matches: list[ImageMatchOut]
    forwards: list[ImageForwardOut]


# --- test page / re-score -----------------------------------------------------------------


class ScoreIn(In):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    kid_ids: Annotated[list[int], Field(max_length=200)] | None = None
    date_from: datetime | None = Field(default=None, alias="from")
    date_to: datetime | None = Field(default=None, alias="to")
    thresholds: ThresholdsIn | None = None

    @model_validator(mode="after")
    def _range(self) -> ScoreIn:
        if self.date_from and self.date_to and naive_utc(self.date_from) > naive_utc(self.date_to):
            raise ValueError("'from' must not be after 'to'")
        return self


def naive_utc(value: datetime) -> datetime:
    """Naive UTC (the DB convention); naive input is taken as UTC already."""
    if value.tzinfo is None:
        return value
    return value.astimezone(UTC).replace(tzinfo=None)


class ScoreFaceOut(BaseModel):
    face_id: int
    image_id: int
    crop_url: str | None
    score: float
    margin: float
    tier: str
    kid_id: int | None
    kid_name: str | None
    group_id: int | None
    group_name: str | None
    sent_at: datetime
    reason: str
    auto_save_disabled: bool


class ScoreBucketOut(BaseModel):
    count: int
    items: list[ScoreFaceOut]


class ScoreKidCounts(BaseModel):
    kid_id: int
    kid_name: str | None
    would_auto_save: int
    would_review: int
    would_drop: int


class ScoreOut(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    date_from: datetime = Field(serialization_alias="from")
    date_to: datetime = Field(serialization_alias="to")
    examined: int
    truncated: bool  # more faces matched the range than are scored per dry run
    thresholds: ThresholdsOut
    buckets: dict[str, ScoreBucketOut]  # would_auto_save / would_review / would_drop
    per_kid: list[ScoreKidCounts]


class RescoreIn(In):
    days: Annotated[int, Field(ge=1, le=365, strict=True)]


class RescoreOut(BaseModel):
    examined: int
    created: int
    updated: int
    unchanged: int
    protected: int
    saved: int
