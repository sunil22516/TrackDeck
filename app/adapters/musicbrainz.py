"""MusicBrainz as a free, keyless catalog.

It cannot create playlists on a streaming service, so this is the *file-export* destination: tracks are
resolved to canonical recordings (title, artists, album, length, ISRC when known) and the user downloads the result.

Operational rules (from MusicBrainz's API policy):
  * identify the app in the User-Agent, with a contact          -> TRACKDECK_MUSICBRAINZ_CONTACT
  * stay at <= 1 request/second per IP, across *all* users      -> process-wide throttle below
Because that budget is shared, results are cached (popular songs are looked up once for everybody).
"""
from __future__ import annotations

import asyncio
import time
from collections import OrderedDict

import httpx

from app.adapters.base import (
    AdapterError,
    DestinationAdapter,
    PlaylistRef,
    RateLimited,
    TransientError,
    register_adapter,
)
from app.core.config import Settings
from app.core.schema import Candidate, Track
from app.services.normalize import fold

BASE_URL = "https://musicbrainz.org"
CACHE_TTL = 24 * 3600
CACHE_MAX = 5000


class _Throttle:
    """Spaces calls >= interval apart, process-wide. Rebuilds its lock if the event loop changes (tests)."""

    def __init__(self) -> None:
        self._lock: asyncio.Lock | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._next = 0.0

    async def wait(self, interval: float) -> None:
        loop = asyncio.get_running_loop()
        if self._lock is None or self._loop is not loop:
            self._lock, self._loop = asyncio.Lock(), loop
        async with self._lock:
            now = time.monotonic()
            start = max(now, self._next)
            if start > now:
                await asyncio.sleep(start - now)
            self._next = start + interval


THROTTLE = _Throttle()
_CACHE: OrderedDict[tuple[str, int], tuple[float, list[Candidate]]] = OrderedDict()


def _cache_get(key: tuple[str, int]) -> list[Candidate] | None:
    hit = _CACHE.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_TTL:
        _CACHE.move_to_end(key)
        return hit[1]
    _CACHE.pop(key, None)
    return None


def _cache_put(key: tuple[str, int], value: list[Candidate]) -> None:
    _CACHE[key] = (time.monotonic(), value)
    while len(_CACHE) > CACHE_MAX:
        _CACHE.popitem(last=False)


def _to_candidate(rec: dict) -> Candidate | None:
    if not rec.get("id") or not rec.get("title"):
        return None
    credit = rec.get("artist-credit") or []
    artists = [c.get("name") or (c.get("artist") or {}).get("name") for c in credit if isinstance(c, dict)]
    releases = rec.get("releases") or []
    isrcs = rec.get("isrcs") or []
    return Candidate(
        id=rec["id"],
        title=rec["title"],
        artists=[a for a in artists if a],
        album=releases[0].get("title") if releases else None,
        duration_ms=rec.get("length"),
        isrc=isrcs[0] if isrcs else None,
        url=f"{BASE_URL}/recording/{rec['id']}",
    )


class MusicBrainzDestination(DestinationAdapter):
    name = "musicbrainz"
    can_write_playlists = False

    def __init__(self, contact: str, min_interval: float = 1.1, transport: httpx.AsyncBaseTransport | None = None):
        self.min_interval = min_interval
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": f"TrackDeck/0.1 ( {contact} )", "Accept": "application/json"},
            timeout=httpx.Timeout(15.0),
            transport=transport,
        )

    def queries_for(self, track: Track) -> list[str]:
        from app.services.normalize import normalize_title

        title = normalize_title(track.title).clean
        artist = fold(track.artists[0]) if track.artists else ""
        out: list[str] = []
        if track.isrc:
            out.append(f"isrc:{track.isrc}")  # exact, one request
        if artist:
            out.append(f'recording:"{title}" AND artist:"{artist}"')
            out.append(f"{title} {artist}")  # loose fallback tolerates typos / alternate credits
        else:
            out.append(f'recording:"{title}"')
        return out

    async def search(self, query: str, limit: int) -> list[Candidate]:
        key = (query, limit)
        if (cached := _cache_get(key)) is not None:
            return cached
        await THROTTLE.wait(self.min_interval)
        try:
            r = await self._client.get("/ws/2/recording", params={"query": query, "fmt": "json", "limit": limit})
        except (httpx.TimeoutException, httpx.TransportError) as e:
            raise TransientError(f"MusicBrainz unreachable: {type(e).__name__}") from e
        if r.status_code in (429, 503):
            try:
                wait = float(r.headers.get("Retry-After", "2"))
            except ValueError:
                wait = 2.0
            raise RateLimited(retry_after=wait, message="MusicBrainz asked us to slow down")
        if r.status_code >= 500:
            raise TransientError(f"MusicBrainz error {r.status_code}")
        if r.status_code >= 400:
            raise AdapterError(f"MusicBrainz rejected the query ({r.status_code})")
        try:
            recs = r.json().get("recordings", [])
        except ValueError as e:
            raise TransientError("MusicBrainz returned invalid JSON") from e
        found = [c for c in (_to_candidate(x) for x in recs) if c]
        _cache_put(key, found)
        return found

    async def create_playlist(self, name: str, description: str = "") -> PlaylistRef:
        raise AdapterError("MusicBrainz is a catalog, not a playlist service. Download the matched file instead.")

    async def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        raise AdapterError("MusicBrainz is a catalog, not a playlist service.")

    async def aclose(self) -> None:
        await self._client.aclose()


def register_musicbrainz(settings: Settings) -> None:
    if settings.musicbrainz_contact:
        register_adapter(
            "musicbrainz",
            lambda creds, s: MusicBrainzDestination(s.musicbrainz_contact or "", s.musicbrainz_min_interval),
        )
