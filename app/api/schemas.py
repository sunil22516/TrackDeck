from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field

from app.core.schema import Candidate, Track


class TransferOut(BaseModel):
    id: str
    status: str
    destination: str
    playlist_name: str
    total: int
    processed: int
    counts: dict[str, int]
    playlist_url: str | None = None
    error: str | None = None
    created_at: datetime


class CreateFromJson(BaseModel):
    destination: str
    playlist_name: str = Field(min_length=1, max_length=300)
    tracks: list[Track] = Field(min_length=1)
    dedupe: bool = True


class TrackOut(BaseModel):
    id: int
    position: int
    status: str
    source: Track
    confidence: str | None
    score: float | None
    reason: str | None
    chosen: Candidate | None
    user_resolved: bool
    candidates: list[dict] | None = None


class ResolveIn(BaseModel):
    candidate_id: str | None = None
    skip: bool = False


class CommitIn(BaseModel):
    include_review: bool = Field(
        default=False,
        description="Also write medium-confidence tracks using the suggested match. Off by default: "
        "a skipped song is better than a wrong one.",
    )
