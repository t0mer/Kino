"""SQLAlchemy 2 models for Kino (CLAUDE.md §7). `persons` arrives with M4."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _enum(name: str, *values: str) -> Enum:
    # Stored as VARCHAR with a CHECK constraint (portable, SQLite-friendly).
    return Enum(*values, name=name, native_enum=False, create_constraint=True, length=32)


IMAGE_STATUS = ("queued", "processing", "done", "failed", "skipped")
MATCH_METHOD = ("face", "burst")
MATCH_DECISION = ("auto_saved", "review", "rejected", "confirmed", "dropped")
DECIDED_BY = ("system", "user")
REF_SOURCE = ("upload", "cluster", "review", "auto")
REF_STATUS = ("active", "retired")
CLUSTER_LABEL = ("unlabeled", "labeled", "ignored")
FORWARD_STATUS = ("pending", "sent", "failed", "cancelled")


class Base(DeclarativeBase):
    pass


class Kid(Base):
    __tablename__ = "kids"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(String, nullable=False)
    slug: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    birth_year: Mapped[int | None] = mapped_column(Integer)
    active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    auto_save_enabled: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    threshold_offset: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Group(Base):
    __tablename__ = "groups"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    chat_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str | None] = mapped_column(String)
    slug: Mapped[str | None] = mapped_column(String)
    watched: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime)


class KidGroup(Base):
    __tablename__ = "kid_groups"

    kid_id: Mapped[int] = mapped_column(ForeignKey("kids.id", ondelete="CASCADE"), primary_key=True)
    group_id: Mapped[int] = mapped_column(
        ForeignKey("groups.id", ondelete="CASCADE"), primary_key=True
    )


class Image(Base):
    __tablename__ = "images"
    __table_args__ = (Index("ix_images_group_id_sent_at", "group_id", "sent_at"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    message_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"))
    sender_id: Mapped[str | None] = mapped_column(String)
    sender_name: Mapped[str | None] = mapped_column(String)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)
    received_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    raw_path: Mapped[str | None] = mapped_column(String)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    phash: Mapped[str | None] = mapped_column(String, index=True)
    duplicate_of: Mapped[int | None] = mapped_column(ForeignKey("images.id"))
    status: Mapped[str] = mapped_column(
        _enum("image_status", *IMAGE_STATUS), default="queued", nullable=False
    )
    skip_reason: Mapped[str | None] = mapped_column(String)
    processing_ms: Mapped[int | None] = mapped_column(Integer)
    error: Mapped[str | None] = mapped_column(Text)


class Cluster(Base):
    __tablename__ = "clusters"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    size: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    label_status: Mapped[str] = mapped_column(
        _enum("cluster_label_status", *CLUSTER_LABEL), default="unlabeled", nullable=False
    )
    kid_id: Mapped[int | None] = mapped_column(ForeignKey("kids.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Face(Base):
    __tablename__ = "faces"
    __table_args__ = (Index("ix_faces_image_id", "image_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    bbox_x: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_y: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_w: Mapped[float] = mapped_column(Float, nullable=False)
    bbox_h: Mapped[float] = mapped_column(Float, nullable=False)
    det_score: Mapped[float] = mapped_column(Float, nullable=False)
    size_px: Mapped[float] = mapped_column(Float, nullable=False)
    blur_score: Mapped[float | None] = mapped_column(Float)
    yaw: Mapped[float | None] = mapped_column(Float)
    pitch: Mapped[float | None] = mapped_column(Float)
    quality: Mapped[float | None] = mapped_column(Float)
    from_tile: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary)
    crop_path: Mapped[str | None] = mapped_column(String)
    cluster_id: Mapped[int | None] = mapped_column(ForeignKey("clusters.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Match(Base):
    __tablename__ = "matches"
    __table_args__ = (Index("ix_matches_decision", "decision"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    face_id: Mapped[int] = mapped_column(ForeignKey("faces.id", ondelete="CASCADE"))
    kid_id: Mapped[int] = mapped_column(ForeignKey("kids.id", ondelete="CASCADE"))
    score: Mapped[float] = mapped_column(Float, nullable=False)
    margin: Mapped[float | None] = mapped_column(Float)
    method: Mapped[str] = mapped_column(
        _enum("match_method", *MATCH_METHOD), default="face", nullable=False
    )
    decision: Mapped[str] = mapped_column(_enum("match_decision", *MATCH_DECISION), nullable=False)
    decided_by: Mapped[str] = mapped_column(
        _enum("decided_by", *DECIDED_BY), default="system", nullable=False
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, default=_now)


class ReferenceFace(Base):
    __tablename__ = "reference_faces"
    __table_args__ = (Index("ix_reference_faces_kid_id_status", "kid_id", "status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kid_id: Mapped[int] = mapped_column(ForeignKey("kids.id", ondelete="CASCADE"))
    face_id: Mapped[int | None] = mapped_column(ForeignKey("faces.id", ondelete="SET NULL"))
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    crop_path: Mapped[str | None] = mapped_column(String)
    source: Mapped[str] = mapped_column(_enum("reference_source", *REF_SOURCE), nullable=False)
    quality: Mapped[float | None] = mapped_column(Float)
    photo_date: Mapped[datetime | None] = mapped_column(DateTime)
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    weight: Mapped[float] = mapped_column(Float, default=1.0, nullable=False)
    status: Mapped[str] = mapped_column(
        _enum("reference_status", *REF_STATUS), default="active", nullable=False
    )


class NegativeFace(Base):
    __tablename__ = "negative_faces"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    kid_id: Mapped[int] = mapped_column(ForeignKey("kids.id", ondelete="CASCADE"), index=True)
    embedding: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    crop_path: Mapped[str | None] = mapped_column(String)
    source_face_id: Mapped[int | None] = mapped_column(ForeignKey("faces.id", ondelete="SET NULL"))
    added_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class SavedPhoto(Base):
    __tablename__ = "saved_photos"
    __table_args__ = (UniqueConstraint("image_id", "kid_id", name="uq_saved_photos_image_kid"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    kid_id: Mapped[int] = mapped_column(ForeignKey("kids.id", ondelete="CASCADE"))
    path: Mapped[str] = mapped_column(String, nullable=False)
    saved_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)


class Forward(Base):
    __tablename__ = "forwards"
    __table_args__ = (UniqueConstraint("image_id", "dest_chat_id", name="uq_forwards_image_dest"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    image_id: Mapped[int] = mapped_column(ForeignKey("images.id", ondelete="CASCADE"))
    dest_chat_id: Mapped[str] = mapped_column(String, nullable=False)
    kid_ids: Mapped[list[int]] = mapped_column(JSON, default=list, nullable=False)
    caption: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(
        _enum("forward_status", *FORWARD_STATUS), default="pending", nullable=False
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text)
    openwa_message_id: Mapped[str | None] = mapped_column(String, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime)


class Digest(Base):
    __tablename__ = "digests"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    sent_at: Mapped[datetime] = mapped_column(DateTime, default=_now, nullable=False)
    pending_count: Mapped[int] = mapped_column(Integer, nullable=False)
    dest_chat_id: Mapped[str | None] = mapped_column(String)


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[Any] = mapped_column(JSON)
