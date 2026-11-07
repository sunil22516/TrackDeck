from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRACKDECK_", env_file=".env", extra="ignore")

    app_name: str = "TrackDeck"
    environment: str = "development"

    # sqlite for dev, e.g. postgresql+asyncpg://user:pass@host/db for prod
    database_url: str = "sqlite+aiosqlite:///./trackdeck.db"

    # Upload / abuse limits
    max_upload_bytes: int = 5 * 1024 * 1024
    max_tracks_per_playlist: int = 500  # MusicBrainz is ~1 search/sec shared by everyone; raise with a paid catalog
    max_playlists_per_upload: int = 50

    # Matching
    auto_accept_threshold: float = 0.88  # >= -> matched automatically
    review_threshold: float = 0.70  # >= -> needs user review, below -> unresolved
    ambiguity_margin: float = 0.03  # top-2 closer than this (and different songs) -> review
    search_candidates: int = 10
    stored_candidates: int = 5

    # Pipeline
    match_concurrency: int = 8
    adapter_max_retries: int = 4
    adapter_backoff_base: float = 0.5  # seconds, exponential
    playlist_write_batch: int = 100

    # Destinations
    enable_mock_destination: bool = True  # demo catalog; disable in production
    musicbrainz_contact: str | None = None  # email or URL; MusicBrainz requires it in the User-Agent. Enables the adapter.
    musicbrainz_min_interval: float = 1.1  # seconds between requests, process-wide (their limit is 1/sec)

    # Abuse / capacity (free-tier friendly)
    max_concurrent_matching: int = 2  # transfers matched at once; the rest wait their turn
    upload_limit_per_hour: int = 10  # per client IP
    trust_proxy_headers: bool = False  # True behind Render/Vercel/etc. so X-Forwarded-For is the client IP
    retention_hours: int = 24  # transfers (and their track data) are deleted after this
    cors_origins: list[str] = ["http://localhost:3000"]

    @field_validator("database_url")
    @classmethod
    def _normalize_db_url(cls, v: str) -> str:
        return normalize_db_url(v)


def normalize_db_url(url: str) -> str:
    """Accept the URL exactly as Neon/Supabase/Render hand it out and make it asyncpg-compatible."""
    from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

    for old in ("postgres://", "postgresql://"):
        if url.startswith(old):
            url = "postgresql+asyncpg://" + url[len(old):]
    if not url.startswith("postgresql+asyncpg://"):
        return url
    parts = urlsplit(url)
    q = []
    for k, val in parse_qsl(parts.query):
        if k == "sslmode":  # asyncpg spells it `ssl`
            q.append(("ssl", val))
        elif k in ("channel_binding", "options"):  # unsupported by asyncpg; safe to drop
            continue
        else:
            q.append((k, val))
    return urlunsplit(parts._replace(query=urlencode(q)))


@lru_cache
def get_settings() -> Settings:
    return Settings()
