from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass

from app.core.config import Settings
from app.core.schema import Candidate, Track


class AdapterError(Exception):
    """Base class. Non-retryable unless a subclass says otherwise."""


class AuthError(AdapterError):
    """Credentials missing/expired/insufficient scope. Never retried."""


class RateLimited(AdapterError):
    def __init__(self, retry_after: float = 1.0, message: str = "rate limited"):
        super().__init__(message)
        self.retry_after = retry_after


class TransientError(AdapterError):
    """Timeouts, 5xx, connection resets. Retried with backoff."""


RETRYABLE = (RateLimited, TransientError)


@dataclass
class PlaylistRef:
    id: str
    url: str | None = None


class DestinationAdapter(ABC):
    """One implementation per music service. The pipeline only ever talks to this interface.

    Credentials (OAuth access tokens etc.) are passed in at construction, live in memory for the
    duration of a job, and are never persisted by TrackDeck.
    """

    name: str
    can_write_playlists: bool = True  # False for catalog-only destinations (file export)

    def queries_for(self, track: Track) -> list[str]:
        """Search strings, most specific first. Adapters override this to use their service's query syntax."""
        from app.services.matching import build_queries

        return build_queries(track)

    @abstractmethod
    async def search(self, query: str, limit: int) -> list[Candidate]: ...

    @abstractmethod
    async def create_playlist(self, name: str, description: str = "") -> PlaylistRef: ...

    @abstractmethod
    async def add_tracks(self, playlist_id: str, track_ids: list[str]) -> None:
        """Append in the given order. Implementations must respect the service's batch limits."""

    async def aclose(self) -> None:  # pragma: no cover - optional
        return None


AdapterFactory = Callable[[dict, Settings], DestinationAdapter]

_REGISTRY: dict[str, AdapterFactory] = {}


def register_adapter(name: str, factory: AdapterFactory) -> None:
    _REGISTRY[name] = factory


def unregister_adapter(name: str) -> None:
    _REGISTRY.pop(name, None)


def available_destinations() -> list[str]:
    return sorted(_REGISTRY)


def build_adapter(name: str, credentials: dict | None, settings: Settings) -> DestinationAdapter:
    try:
        factory = _REGISTRY[name]
    except KeyError:
        raise KeyError(f"Unknown destination '{name}'. Available: {available_destinations()}") from None
    return factory(credentials or {}, settings)
