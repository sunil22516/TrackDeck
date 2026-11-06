"""Universal track schema. Every importer produces it, every adapter consumes/returns it."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Track(BaseModel):
    title: str
    artists: list[str] = Field(default_factory=list)
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    source_id: str | None = None
    position: int = 0

    @field_validator("title")
    @classmethod
    def _title_not_blank(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("title must not be blank")
        return v

    @field_validator("artists")
    @classmethod
    def _clean_artists(cls, v: list[str]) -> list[str]:
        return [a.strip() for a in v if a and a.strip()]

    @field_validator("isrc")
    @classmethod
    def _clean_isrc(cls, v: str | None) -> str | None:
        if not v:
            return None
        v = v.replace("-", "").strip().upper()
        return v if len(v) == 12 and v.isalnum() else None

    @field_validator("duration_ms")
    @classmethod
    def _positive(cls, v: int | None) -> int | None:
        return v if v and v > 0 else None


class Candidate(BaseModel):
    """A track found on the destination catalog."""

    id: str
    title: str
    artists: list[str] = Field(default_factory=list)
    album: str | None = None
    duration_ms: int | None = None
    isrc: str | None = None
    url: str | None = None


class ParsedPlaylist(BaseModel):
    name: str
    tracks: list[Track]
