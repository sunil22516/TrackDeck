"""Tiny in-memory sliding-window limiter. Per-process: fine for one free-tier instance;
swap for Redis (e.g. slowapi + Upstash) if you scale out."""
from __future__ import annotations

import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request


class SlidingWindow:
    def __init__(self) -> None:
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str, limit: int, window: float, now: float | None = None) -> None:
        now = time.monotonic() if now is None else now
        q = self._hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            retry = int(window - (now - q[0])) + 1
            raise HTTPException(429, f"Too many uploads. Try again in {retry // 60 + 1} min.", headers={"Retry-After": str(retry)})
        q.append(now)
        if len(self._hits) > 10_000:  # bound memory
            for k in [k for k, v in self._hits.items() if not v]:
                del self._hits[k]


def client_ip(request: Request, trust_proxy: bool) -> str:
    if trust_proxy:
        fwd = request.headers.get("x-forwarded-for")
        if fwd:
            return fwd.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
