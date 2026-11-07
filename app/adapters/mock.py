"""In-memory destination used for demos and tests. Behaves like a real catalog search:
fuzzy retrieval over title+artist, returns noisy results (decoys included), enforces batch limits,
and can inject rate limits / transient failures so the retry logic is exercised for real.
"""
from __future__ import annotations

import json
import uuid
from pathlib import Path

from rapidfuzz import fuzz

from app.adapters.base import (
    AuthError,
    DestinationAdapter,
    PlaylistRef,
    RateLimited,
    TransientError,
    register_adapter,
)
from app.core.config import Settings
from app.core.schema import Candidate
from app.services.normalize import fold

_CATALOG_PATH = Path(__file__).resolve().parents[2] / "data" / "demo_catalog.json"
MAX_BATCH = 100

# playlists created through any MockDestination instance (id -> dict)
PLAYLISTS: dict[str, dict] = {}


def _load_catalog() -> list[Candidate]:
    return [Candidate(**row) for row in json.loads(_CATALOG_PATH.read_text(encoding="utf-8"))]


class MockDestination(DestinationAdapter):
    name = "mock"

    def __init__(
        self,
        catalog: list[Candidate] | None = None,
        *,
        rate_limit_every: int = 0,  # raise RateLimited on every Nth search call
        transient_every: int = 0,  # raise TransientError on every Nth search call
        fail_add_after: int | None = None,  # raise TransientError once N tracks were added in total
        require_token: str | None = None,
        token: str | None = None,
    ):
        self.catalog = catalog if catalog is not None else _load_catalog()
        self.rate_limit_every = rate_limit_every
        self.transient_every = transient_every
        self.fail_add_after = fail_add_after
        self.require_token = require_token
        self.token = token
        self.search_calls = 0
        self.add_calls: list[list[str]] = []
        self._added_total = 0
        self._failed_once = False

    def _auth(self) -> None:
        if self.require_token and self.token != self.require_token:
            raise AuthError("invalid or missing access token")

    async def search(self, query: str, limit: int) -> list[Candidate]:
        self._auth()
        self.search_calls += 1
        if self.rate_limit_every and self.search_calls % self.rate_limit_every == 0:
            raise RateLimited(retry_after=0.01)
        if self.transient_every and self.search_calls % self.transient_every == 0:
            raise TransientError("simulated timeout")
        q = fold(query)
        scored = []
        for c in self.catalog:
            hay = fold(f"{c.title} {' '.join(c.artists)}")
            s = fuzz.token_set_ratio(q, hay)
            if s >= 60:
                scored.append((s, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [c for _, c in scored[:limit]]

    async def create_playlist(self, name: str, description: str = "") -> PlaylistRef:
        self._auth()
        pid = f"mock_pl_{uuid.uuid4().hex[:10]}"
        PLAYLISTS[pid] = {"name": name, "description": description, "tracks": []}
        return PlaylistRef(id=pid, url=f"https://mock.trackdeck.local/playlist/{pid}")

    async def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        self._auth()
        if len(track_ids) > MAX_BATCH:
            raise ValueError(f"batch too large ({len(track_ids)} > {MAX_BATCH})")
        if playlist_id not in PLAYLISTS:
            raise ValueError("unknown playlist")
        if (
            self.fail_add_after is not None
            and not self._failed_once
            and self._added_total + len(track_ids) > self.fail_add_after
        ):
            self._failed_once = True
            raise TransientError("simulated write failure")
        self.add_calls.append(list(track_ids))
        PLAYLISTS[playlist_id]["tracks"].extend(track_ids)
        self._added_total += len(track_ids)


def _factory(credentials: dict, settings: Settings) -> MockDestination:
    return MockDestination(token=credentials.get("access_token"))


def register_defaults(settings: Settings) -> None:
    if settings.enable_mock_destination:
        register_adapter("mock", _factory)
