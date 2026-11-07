"""Transfer pipeline: match phase and commit phase.

Phase 1 (match):  dedupe -> search destination -> score -> classify -> persist.   No writes to the destination.
Phase 2 (commit): create playlist (once) -> add matched tracks in order, batch by batch -> persist progress.

Both phases are resumable and safe to re-trigger; ownership of a transfer is claimed with an atomic
status transition so concurrent workers/requests can't double-run it.
"""
from __future__ import annotations

import asyncio
import logging
import random
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TypeVar

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.adapters.base import RETRYABLE, AdapterError, AuthError, DestinationAdapter, RateLimited
from app.core.config import Settings
from app.core.schema import Candidate, Track
from app.db.models import TrackStatus, Transfer, TransferStatus, TransferTrack
from app.services.matching import Confidence, MatchOutcome, Reranker, decide
from app.services.normalize import normalize_artist, normalize_title

log = logging.getLogger("trackdeck.pipeline")
T = TypeVar("T")

MAX_RETRY_DELAY = 60.0


# --------------------------------------------------------------------------- retry
async def with_retry(
    fn: Callable[[], Awaitable[T]],
    settings: Settings,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Exponential backoff + jitter. Honors Retry-After on rate limits. Auth errors are never retried."""
    attempt = 0
    while True:
        try:
            return await fn()
        except RETRYABLE as exc:
            if attempt >= settings.adapter_max_retries:
                raise
            backoff = settings.adapter_backoff_base * (2**attempt)
            delay = max(exc.retry_after, backoff) if isinstance(exc, RateLimited) else backoff
            delay = min(delay, MAX_RETRY_DELAY) * (1 + random.random() * 0.1)
            log.info("retrying after %s (attempt %d, sleeping %.2fs)", type(exc).__name__, attempt + 1, delay)
            attempt += 1
            await sleep(delay)


# --------------------------------------------------------------------------- job runner
class JobRunner:
    """Minimal in-process background runner. The pipeline functions are plain coroutines, so swapping this
    for ARQ/Celery/RQ later means calling them from a worker instead of `submit`."""

    def __init__(self, max_concurrent_matching: int = 2) -> None:
        self._tasks: set[asyncio.Task] = set()
        # Shared upstream rate limits (e.g. MusicBrainz 1 req/s) mean only a few transfers should match at once.
        self.match_slots = asyncio.Semaphore(max_concurrent_matching)

    async def gated(self, coro: Awaitable[None]) -> None:
        async with self.match_slots:
            await coro

    def submit(self, coro: Awaitable[None], name: str = "job") -> asyncio.Task:
        task = asyncio.create_task(coro, name=name)  # type: ignore[arg-type]
        self._tasks.add(task)
        task.add_done_callback(self._done)
        return task

    def _done(self, task: asyncio.Task) -> None:
        self._tasks.discard(task)
        if not task.cancelled() and task.exception():
            log.error("job %s crashed", task.get_name(), exc_info=task.exception())

    async def join(self) -> None:
        while self._tasks:
            await asyncio.gather(*list(self._tasks), return_exceptions=True)

    async def shutdown(self) -> None:
        for t in list(self._tasks):
            t.cancel()
        await asyncio.gather(*list(self._tasks), return_exceptions=True)


# --------------------------------------------------------------------------- state helpers
async def claim(
    sm: async_sessionmaker[AsyncSession], transfer_id: str, allowed: set[str], to: TransferStatus
) -> bool:
    """Atomic compare-and-set on status. Returns True only for the caller that won."""
    async with sm() as s:
        res = await s.execute(
            update(Transfer)
            .where(Transfer.id == transfer_id, Transfer.status.in_([str(a) for a in allowed]))
            .values(status=str(to), error=None)
        )
        await s.commit()
        return res.rowcount == 1


async def _set_status(sm, transfer_id: str, status: TransferStatus, error: str | None = None) -> None:
    async with sm() as s:
        await s.execute(update(Transfer).where(Transfer.id == transfer_id).values(status=str(status), error=error))
        await s.commit()


async def recover_interrupted(sm: async_sessionmaker[AsyncSession]) -> int:
    """On boot, anything left MATCHING/WRITING belongs to a dead process. Mark FAILED so it can be resumed."""
    async with sm() as s:
        res = await s.execute(
            update(Transfer)
            .where(Transfer.status.in_([TransferStatus.MATCHING, TransferStatus.WRITING]))
            .values(status=TransferStatus.FAILED, error="Interrupted by a server restart. Re-run to resume.")
        )
        await s.commit()
        return res.rowcount or 0


def dedupe_key(track: Track) -> str:
    if track.isrc:
        return f"isrc:{track.isrc}"
    nt = normalize_title(track.title)
    artist = normalize_artist(track.artists[0]) if track.artists else ""
    return f"{nt.clean}|{artist}|{','.join(sorted(nt.versions))}"


def _dump_candidates(outcome: MatchOutcome, n: int) -> list[dict]:
    return [
        {"candidate": sc.candidate.model_dump(), "score": round(sc.score, 4), "flags": sc.flags}
        for sc in outcome.ranked[:n]
    ]


# --------------------------------------------------------------------------- matching
async def match_track(
    track: Track,
    adapter: DestinationAdapter,
    settings: Settings,
    reranker: Reranker | None = None,
) -> MatchOutcome:
    """Search progressively broader queries; stop as soon as we have a HIGH-confidence match."""
    pool: dict[str, Candidate] = {}
    outcome = MatchOutcome(Confidence.LOW, None, [], "no candidates returned by destination")
    for query in adapter.queries_for(track):
        found = await with_retry(lambda q=query: adapter.search(q, settings.search_candidates), settings)
        for c in found:
            pool.setdefault(c.id, c)
        outcome = decide(track, list(pool.values()), settings)
        if outcome.confidence == Confidence.HIGH:
            break

    if outcome.confidence == Confidence.MEDIUM and reranker is not None and outcome.ranked:
        try:
            picked = await reranker.pick(track, outcome.ranked[:5])
        except Exception:  # LLM hook must never break a transfer
            log.warning("reranker failed; leaving track for review", exc_info=True)
            picked = None
        chosen = next((r for r in outcome.ranked if r.candidate.id == picked), None) if picked else None
        if chosen:
            outcome = MatchOutcome(Confidence.HIGH, chosen, outcome.ranked, "reranker pick")
    return outcome


def _apply_outcome(row: TransferTrack, outcome: MatchOutcome, settings: Settings) -> None:
    row.candidates = _dump_candidates(outcome, settings.stored_candidates)
    row.confidence = outcome.confidence.value
    row.reason = outcome.reason
    row.score = round(outcome.best.score, 4) if outcome.best else None
    if outcome.confidence == Confidence.HIGH:
        row.status, row.chosen = TrackStatus.MATCHED, outcome.best.candidate.model_dump()
    elif outcome.confidence == Confidence.MEDIUM:
        row.status, row.chosen = TrackStatus.REVIEW, outcome.best.candidate.model_dump()  # suggestion only
    else:
        row.status, row.chosen = TrackStatus.UNRESOLVED, None


async def run_matching(
    sm: async_sessionmaker[AsyncSession],
    transfer_id: str,
    adapter: DestinationAdapter,
    settings: Settings,
    reranker: Reranker | None = None,
) -> None:
    """Caller must already have claimed the transfer (-> MATCHING)."""
    pending: list[asyncio.Task] = []
    try:
        async with sm() as s:
            transfer = await s.get(Transfer, transfer_id)
            if transfer is None:
                return
            dedupe = bool(transfer.options.get("dedupe", True))
            rows = (
                (
                    await s.execute(
                        select(TransferTrack)
                        .where(
                            TransferTrack.transfer_id == transfer_id,
                            TransferTrack.status.in_([TrackStatus.PENDING, TrackStatus.UNRESOLVED]),
                        )
                        .order_by(TransferTrack.position)
                    )
                )
                .scalars()
                .all()
            )

            # Source-side dedupe over *all* tracks so a rematch doesn't re-flag originals as duplicates
            seen: set[str] = set()
            if dedupe:
                existing = (
                    await s.execute(
                        select(TransferTrack).where(
                            TransferTrack.transfer_id == transfer_id,
                            TransferTrack.status.notin_([TrackStatus.PENDING, TrackStatus.UNRESOLVED, TrackStatus.DUPLICATE]),
                        )
                    )
                ).scalars()
                seen = {dedupe_key(Track(**r.source)) for r in existing}

            todo: list[tuple[int, Track]] = []
            for row in rows:
                tr = Track(**row.source)
                key = dedupe_key(tr)
                if dedupe and key in seen:
                    row.status, row.reason = TrackStatus.DUPLICATE, "duplicate of an earlier track in the source"
                    continue
                seen.add(key)
                todo.append((row.id, tr))
            await s.commit()

        sem = asyncio.Semaphore(settings.match_concurrency)
        auth_failure: list[AuthError] = []

        async def work(row_id: int, track: Track) -> tuple[int, MatchOutcome | Exception]:
            async with sem:
                if auth_failure:
                    return row_id, auth_failure[0]
                try:
                    return row_id, await match_track(track, adapter, settings, reranker)
                except AuthError as e:
                    auth_failure.append(e)
                    return row_id, e
                except AdapterError as e:
                    return row_id, e

        pending.extend(asyncio.create_task(work(rid, tr)) for rid, tr in todo)
        flush_every = 25
        buffer: list[tuple[int, MatchOutcome | Exception]] = []
        search_errors = 0

        async def flush() -> None:
            if not buffer:
                return
            async with sm() as s:
                rows_by_id = {
                    r.id: r
                    for r in (
                        await s.execute(select(TransferTrack).where(TransferTrack.id.in_([b[0] for b in buffer])))
                    ).scalars()
                }
                for rid, res in buffer:
                    row = rows_by_id[rid]
                    if isinstance(res, Exception):
                        row.status, row.chosen, row.confidence = TrackStatus.UNRESOLVED, None, "low"
                        row.reason = f"search failed: {type(res).__name__}: {res}"
                    else:
                        _apply_outcome(row, res, settings)
                await s.execute(
                    update(Transfer).where(Transfer.id == transfer_id).values(processed=Transfer.processed + len(buffer))
                )
                await s.commit()
            buffer.clear()

        for fut in asyncio.as_completed(pending):
            item = await fut
            search_errors += isinstance(item[1], Exception)
            buffer.append(item)
            if len(buffer) >= flush_every:
                await flush()
        await flush()

        if auth_failure:
            await _set_status(sm, transfer_id, TransferStatus.FAILED, f"Authentication failed: {auth_failure[0]}")
        elif todo and search_errors * 2 > len(todo):
            # Mostly infrastructure errors, not "song not found": say so and let the user retry.
            await _set_status(
                sm, transfer_id, TransferStatus.FAILED,
                f"The music catalog was unreachable for {search_errors} of {len(todo)} tracks. "
                "Your file is safe; retry in a minute.",
            )
        else:
            await _set_status(sm, transfer_id, TransferStatus.READY)
    except asyncio.CancelledError:
        await _set_status(sm, transfer_id, TransferStatus.FAILED, "Cancelled (server shutting down). Re-run to resume.")
        raise
    except Exception as e:  # noqa: BLE001
        log.exception("matching crashed for %s", transfer_id)
        await _set_status(sm, transfer_id, TransferStatus.FAILED, f"Internal error during matching: {type(e).__name__}")
    finally:
        for t in pending:
            if not t.done():
                t.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        await adapter.aclose()


# --------------------------------------------------------------------------- commit
async def run_commit(
    sm: async_sessionmaker[AsyncSession],
    transfer_id: str,
    adapter: DestinationAdapter,
    settings: Settings,
    include_review: bool = False,
) -> None:
    """Caller must already have claimed the transfer (-> WRITING)."""
    try:
        async with sm() as s:
            transfer = await s.get(Transfer, transfer_id)
            if transfer is None:
                return
            dedupe = bool(transfer.options.get("dedupe", True))
            description = transfer.options.get("description", "Transferred with TrackDeck")
            playlist_id, name = transfer.dest_playlist_id, transfer.playlist_name

        if playlist_id is None:
            ref = await with_retry(lambda: adapter.create_playlist(name, description), settings)
            playlist_id = ref.id
            async with sm() as s:  # persist immediately so a retry appends to the SAME playlist
                await s.execute(
                    update(Transfer).where(Transfer.id == transfer_id).values(dest_playlist_id=ref.id, dest_playlist_url=ref.url)
                )
                await s.commit()

        writable = [TrackStatus.MATCHED, TrackStatus.FAILED] + ([TrackStatus.REVIEW] if include_review else [])
        async with sm() as s:
            rows = (
                (
                    await s.execute(
                        select(TransferTrack)
                        .where(TransferTrack.transfer_id == transfer_id, TransferTrack.status.in_(writable))
                        .order_by(TransferTrack.position)
                    )
                )
                .scalars()
                .all()
            )
            already = set()
            if dedupe:  # destination-side dedupe, including tracks added by a previous partial commit
                already = {
                    r.chosen["id"]
                    for r in (
                        await s.execute(
                            select(TransferTrack).where(
                                TransferTrack.transfer_id == transfer_id, TransferTrack.status == TrackStatus.ADDED
                            )
                        )
                    ).scalars()
                    if r.chosen
                }
            to_write: list[tuple[int, str]] = []
            for r in rows:
                dest_id = (r.chosen or {}).get("id")
                if not dest_id:
                    continue
                if dedupe and dest_id in already:
                    r.status, r.reason = TrackStatus.DUPLICATE, "same destination track already in playlist"
                    continue
                already.add(dest_id)
                to_write.append((r.id, dest_id))
            await s.commit()

        batch_size = settings.playlist_write_batch
        for i in range(0, len(to_write), batch_size):
            batch = to_write[i : i + batch_size]
            ids = [d for _, d in batch]
            row_ids = [r for r, _ in batch]
            try:
                await with_retry(lambda ids=ids: adapter.add_tracks(playlist_id, ids), settings)
            except AdapterError as e:
                async with sm() as s:
                    await s.execute(
                        update(TransferTrack)
                        .where(TransferTrack.id.in_(row_ids))
                        .values(status=TrackStatus.FAILED, reason=f"write failed: {type(e).__name__}: {e}")
                    )
                    await s.commit()
                await _set_status(
                    sm, transfer_id, TransferStatus.FAILED,
                    f"Write stopped at track batch {i // batch_size + 1} ({type(e).__name__}). "
                    "Re-run commit to resume; already-added tracks are skipped.",
                )
                return
            async with sm() as s:  # checkpoint after every batch
                await s.execute(
                    update(TransferTrack).where(TransferTrack.id.in_(row_ids)).values(status=TrackStatus.ADDED, reason=None)
                )
                await s.commit()

        await _set_status(sm, transfer_id, TransferStatus.COMPLETED)
    except asyncio.CancelledError:
        await _set_status(sm, transfer_id, TransferStatus.FAILED, "Cancelled (server shutting down). Re-run to resume.")
        raise
    except AuthError as e:
        await _set_status(sm, transfer_id, TransferStatus.FAILED, f"Authentication failed: {e}")
    except Exception as e:  # noqa: BLE001
        log.exception("commit crashed for %s", transfer_id)
        await _set_status(sm, transfer_id, TransferStatus.FAILED, f"Internal error during write: {type(e).__name__}")
    finally:
        await adapter.aclose()


# --------------------------------------------------------------------------- reporting
async def build_report(s: AsyncSession, transfer: Transfer) -> dict:
    rows = (
        (await s.execute(select(TransferTrack).where(TransferTrack.transfer_id == transfer.id).order_by(TransferTrack.position)))
        .scalars()
        .all()
    )
    counts = Counter(r.status for r in rows)
    eligible = len(rows) - counts[TrackStatus.DUPLICATE] - counts[TrackStatus.SKIPPED]

    def brief(r: TransferTrack) -> dict:
        return {
            "id": r.id,
            "position": r.position,
            "title": r.source["title"],
            "artists": r.source.get("artists", []),
            "reason": r.reason,
            "score": r.score,
            "suggestion": r.chosen,
        }

    return {
        "transfer_id": transfer.id,
        "status": transfer.status,
        "destination": transfer.destination,
        "playlist_name": transfer.playlist_name,
        "playlist_url": transfer.dest_playlist_url,
        "counts": {s: counts.get(s, 0) for s in TrackStatus},
        "total": len(rows),
        "success_rate": round(counts[TrackStatus.ADDED] / eligible, 4) if eligible and transfer.status == TransferStatus.COMPLETED else None,
        "needs_review": [brief(r) for r in rows if r.status == TrackStatus.REVIEW],
        "not_found": [brief(r) for r in rows if r.status == TrackStatus.UNRESOLVED],
        "failed": [brief(r) for r in rows if r.status == TrackStatus.FAILED],
        "duplicates": [brief(r) for r in rows if r.status == TrackStatus.DUPLICATE],
    }


# --------------------------------------------------------------------------- retention
async def purge_expired(sm: async_sessionmaker[AsyncSession], hours: int) -> int:
    """Privacy: delete finished transfers (and, by cascade, their track data) older than `hours`."""
    from datetime import UTC, datetime, timedelta

    from sqlalchemy import delete

    cutoff = datetime.now(UTC) - timedelta(hours=hours)
    async with sm() as s:
        res = await s.execute(
            delete(Transfer).where(
                Transfer.created_at < cutoff,
                Transfer.status.notin_([TransferStatus.MATCHING, TransferStatus.WRITING]),
            )
        )
        await s.commit()
        return res.rowcount or 0
