from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum

from sqlalchemy import JSON, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def _now() -> datetime:
    return datetime.now(UTC)


def _uuid() -> str:
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


class TransferStatus(StrEnum):
    MATCHING = "matching"
    READY = "ready"  # matching finished; waiting for user review / commit
    WRITING = "writing"
    COMPLETED = "completed"
    FAILED = "failed"


class TrackStatus(StrEnum):
    PENDING = "pending"
    MATCHED = "matched"  # auto-accepted or user-resolved; will be written on commit
    REVIEW = "review"  # medium confidence, user must pick or skip
    UNRESOLVED = "unresolved"  # low confidence / not found
    DUPLICATE = "duplicate"  # duplicate within the source playlist
    SKIPPED = "skipped"  # user chose to skip
    ADDED = "added"  # written to destination
    FAILED = "failed"  # write failed after retries


class Transfer(Base):
    __tablename__ = "transfers"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_uuid)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    status: Mapped[str] = mapped_column(String(16), default=TransferStatus.MATCHING, index=True)
    source_kind: Mapped[str] = mapped_column(String(32), default="file")
    destination: Mapped[str] = mapped_column(String(32))
    playlist_name: Mapped[str] = mapped_column(String(300))
    options: Mapped[dict] = mapped_column(JSON, default=dict)

    total: Mapped[int] = mapped_column(Integer, default=0)
    processed: Mapped[int] = mapped_column(Integer, default=0)  # progress during matching
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    dest_playlist_id: Mapped[str | None] = mapped_column(String(200), nullable=True)
    dest_playlist_url: Mapped[str | None] = mapped_column(String(500), nullable=True)

    tracks: Mapped[list[TransferTrack]] = relationship(
        back_populates="transfer", cascade="all, delete-orphan", order_by="TransferTrack.position"
    )


class TransferTrack(Base):
    __tablename__ = "transfer_tracks"
    __table_args__ = (
        UniqueConstraint("transfer_id", "position", name="uq_transfer_position"),
        Index("ix_tt_transfer_status", "transfer_id", "status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    transfer_id: Mapped[str] = mapped_column(ForeignKey("transfers.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)

    source: Mapped[dict] = mapped_column(JSON)  # universal Track
    status: Mapped[str] = mapped_column(String(16), default=TrackStatus.PENDING)
    score: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(8), nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    chosen: Mapped[dict | None] = mapped_column(JSON, nullable=True)  # Candidate
    candidates: Mapped[list | None] = mapped_column(JSON, nullable=True)  # top-N [{candidate, score, flags}]
    user_resolved: Mapped[bool] = mapped_column(default=False)

    transfer: Mapped[Transfer] = relationship(back_populates="tracks")
