"""Request/response models for the REST API. Inputs forbid unknown fields."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from kino.pipeline.decide import OFFSET_MAX, OFFSET_MIN


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
