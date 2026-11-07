from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.base import DestinationAdapter, available_destinations, build_adapter
from app.api.schemas import CommitIn, CreateFromJson, ResolveIn, TrackOut, TransferOut
from app.core.config import Settings
from app.core.ratelimit import client_ip
from app.core.schema import Candidate, ParsedPlaylist, Track
from app.db.models import TrackStatus, Transfer, TransferStatus, TransferTrack
from app.importers.formats import DashOrder, ImportError_, export_csv, export_json, export_txt, parse_playlists
from app.services import pipeline

router = APIRouter(prefix="/v1")


# --------------------------------------------------------------------------- deps
def get_settings_dep(request: Request) -> Settings:
    return request.app.state.settings


async def get_session(request: Request):
    async with request.app.state.sm() as session:
        yield session


Session = Annotated[AsyncSession, Depends(get_session)]
Cfg = Annotated[Settings, Depends(get_settings_dep)]
# Destination OAuth token travels in a header (never in URLs/bodies that get logged) and is never stored.
DestToken = Annotated[str | None, Header(alias="X-Destination-Token")]


def _adapter(name: str, token: str | None, settings: Settings) -> DestinationAdapter:
    try:
        return build_adapter(name, {"access_token": token} if token else {}, settings)
    except KeyError as e:
        raise HTTPException(400, str(e.args[0])) from None


async def _get_transfer(s: AsyncSession, transfer_id: str) -> Transfer:
    t = await s.get(Transfer, transfer_id)
    if t is None:
        raise HTTPException(404, "transfer not found")
    return t


async def _counts(s: AsyncSession, transfer_id: str) -> dict[str, int]:
    rows = (
        await s.execute(
            select(TransferTrack.status, func.count())
            .where(TransferTrack.transfer_id == transfer_id)
            .group_by(TransferTrack.status)
        )
    ).all()
    found = dict(rows)
    return {st.value: found.get(st.value, 0) for st in TrackStatus}


async def _out(s: AsyncSession, t: Transfer) -> TransferOut:
    return TransferOut(
        id=t.id, status=t.status, destination=t.destination, playlist_name=t.playlist_name,
        total=t.total, processed=t.processed, counts=await _counts(s, t.id),
        playlist_url=t.dest_playlist_url, error=t.error, created_at=t.created_at,
    )


# --------------------------------------------------------------------------- meta
@router.get("/destinations")
async def destinations() -> dict:
    return {"destinations": available_destinations()}


# --------------------------------------------------------------------------- create
async def _create_transfers(
    request: Request,
    s: AsyncSession,
    settings: Settings,
    playlists: list[ParsedPlaylist],
    destination: str,
    token: str | None,
    dedupe: bool,
    name_override: str | None,
    source_kind: str,
) -> list[TransferOut]:
    request.app.state.limiter.check(
        client_ip(request, settings.trust_proxy_headers), settings.upload_limit_per_hour, 3600
    )
    if not playlists:
        raise HTTPException(422, "No playlists found in upload.")
    if len(playlists) > settings.max_playlists_per_upload:
        raise HTTPException(422, f"Too many playlists (max {settings.max_playlists_per_upload}).")
    playlists = [p for p in playlists if p.tracks]
    if not playlists:
        raise HTTPException(422, "All playlists in the upload are empty.")
    for p in playlists:
        if len(p.tracks) > settings.max_tracks_per_playlist:
            raise HTTPException(422, f"'{p.name}' has {len(p.tracks)} tracks (max {settings.max_tracks_per_playlist}).")

    # validate destination (and build adapters) before persisting anything
    adapters = [_adapter(destination, token, settings) for _ in playlists]

    created: list[Transfer] = []
    for p in playlists:
        t = Transfer(
            destination=destination,
            source_kind=source_kind,
            playlist_name=(name_override if name_override and len(playlists) == 1 else p.name)[:300],
            options={"dedupe": dedupe},
            total=len(p.tracks),
            status=TransferStatus.MATCHING,
        )
        s.add(t)
        await s.flush()
        s.add_all(
            TransferTrack(transfer_id=t.id, position=i, source=tr.model_copy(update={"position": i}).model_dump())
            for i, tr in enumerate(p.tracks)
        )
        created.append(t)
    await s.commit()

    runner: pipeline.JobRunner = request.app.state.runner
    sm = request.app.state.sm
    for t, adapter in zip(created, adapters):
        runner.submit(
            runner.gated(pipeline.run_matching(sm, t.id, adapter, settings, request.app.state.reranker)),
            name=f"match:{t.id}",
        )
    return [await _out(s, t) for t in created]


@router.post("/transfers", response_model=list[TransferOut], status_code=202)
async def create_from_file(
    request: Request,
    s: Session,
    settings: Cfg,
    file: Annotated[UploadFile, File(description="Spotify data export JSON, CSV, TXT or TrackDeck JSON")],
    destination: Annotated[str, Form()],
    x_destination_token: DestToken = None,
    playlist_name: Annotated[str | None, Form()] = None,
    playlist: Annotated[list[str] | None, Form(description="Only import these playlist names (repeatable)")] = None,
    dedupe: Annotated[bool, Form()] = True,
    dash_order: Annotated[DashOrder, Form()] = "artist_title",
):
    raw = await file.read(settings.max_upload_bytes + 1)
    if len(raw) > settings.max_upload_bytes:
        raise HTTPException(413, f"File too large (max {settings.max_upload_bytes // 1024} KB).")
    try:
        playlists = parse_playlists(raw, file.filename or "playlist", dash_order)
    except ImportError_ as e:
        raise HTTPException(422, str(e)) from None
    if playlist:
        wanted = {p.lower() for p in playlist}
        playlists = [p for p in playlists if p.name.lower() in wanted]
    return await _create_transfers(
        request, s, settings, playlists, destination, x_destination_token, dedupe, playlist_name, "file"
    )


@router.post("/transfers/json", response_model=TransferOut, status_code=202)
async def create_from_json(
    body: CreateFromJson, request: Request, s: Session, settings: Cfg, x_destination_token: DestToken = None
):
    if len(body.tracks) > settings.max_tracks_per_playlist:
        raise HTTPException(422, f"Too many tracks (max {settings.max_tracks_per_playlist}).")
    out = await _create_transfers(
        request, s, settings, [ParsedPlaylist(name=body.playlist_name, tracks=body.tracks)],
        body.destination, x_destination_token, body.dedupe, None, "json",
    )
    return out[0]


# --------------------------------------------------------------------------- read
@router.get("/transfers/{transfer_id}", response_model=TransferOut)
async def get_transfer(transfer_id: str, s: Session):
    return await _out(s, await _get_transfer(s, transfer_id))


@router.get("/transfers/{transfer_id}/tracks", response_model=list[TrackOut])
async def list_tracks(
    transfer_id: str,
    s: Session,
    status: TrackStatus | None = None,
    include_candidates: bool = False,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
):
    await _get_transfer(s, transfer_id)
    q = select(TransferTrack).where(TransferTrack.transfer_id == transfer_id)
    if status:
        q = q.where(TransferTrack.status == status.value)
    rows = (await s.execute(q.order_by(TransferTrack.position).limit(limit).offset(offset))).scalars().all()
    return [
        TrackOut(
            id=r.id, position=r.position, status=r.status, source=Track(**r.source), confidence=r.confidence,
            score=r.score, reason=r.reason, chosen=Candidate(**r.chosen) if r.chosen else None,
            user_resolved=r.user_resolved, candidates=r.candidates if include_candidates else None,
        )
        for r in rows
    ]


@router.get("/transfers/{transfer_id}/report")
async def report(transfer_id: str, s: Session):
    return await pipeline.build_report(s, await _get_transfer(s, transfer_id))


@router.get("/transfers/{transfer_id}/export")
async def export(
    transfer_id: str,
    s: Session,
    format: Annotated[str, Query(pattern="^(txt|csv|json)$")] = "txt",
    kind: Annotated[str, Query(pattern="^(source|matched|unmatched)$", description=(
        "source: the uploaded playlist. matched: the destination-side tracks (canonical title/artist/ISRC) "
        "for every accepted match. unmatched: source tracks still needing review or not found."
    ))] = "source",
):
    """TXT/CSV/JSON download. `matched` is the file-export product: a cleaned playlist other tools can import."""
    t = await _get_transfer(s, transfer_id)
    rows = (
        (await s.execute(select(TransferTrack).where(TransferTrack.transfer_id == transfer_id).order_by(TransferTrack.position)))
        .scalars()
        .all()
    )
    if kind == "matched":
        ok = (TrackStatus.MATCHED, TrackStatus.ADDED)
        tracks = [
            Track(**{**{k: v for k, v in r.chosen.items() if k in Track.model_fields}, "source_id": r.chosen["id"]})
            for r in rows if r.status in ok and r.chosen
        ]
    elif kind == "unmatched":
        bad = (TrackStatus.REVIEW, TrackStatus.UNRESOLVED, TrackStatus.FAILED)
        tracks = [Track(**r.source) for r in rows if r.status in bad]
    else:
        tracks = [Track(**r.source) for r in rows]
    safe = "".join(c if c.isalnum() or c in "-_ " else "_" for c in t.playlist_name).strip() or "playlist"
    suffix = "" if kind == "source" else f" ({kind})"
    if format == "txt":
        body, media = export_txt(t.playlist_name, tracks), "text/plain"
    elif format == "csv":
        body, media = export_csv(tracks), "text/csv"
    else:
        body, media = export_json(t.playlist_name, tracks), "application/json"
    return PlainTextResponse(
        body, media_type=f"{media}; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{safe}{suffix}.{format}"',
            "X-Track-Count": str(len(tracks)),
        },
    )


# --------------------------------------------------------------------------- review
@router.post("/transfers/{transfer_id}/tracks/{track_id}/resolve", response_model=TrackOut)
async def resolve_track(transfer_id: str, track_id: int, body: ResolveIn, s: Session):
    t = await _get_transfer(s, transfer_id)
    if t.status in (TransferStatus.MATCHING, TransferStatus.WRITING):
        raise HTTPException(409, f"Transfer is {t.status}; try again when it finishes.")
    row = await s.get(TransferTrack, track_id)
    if row is None or row.transfer_id != transfer_id:
        raise HTTPException(404, "track not found")
    if row.status in (TrackStatus.ADDED, TrackStatus.DUPLICATE):
        raise HTTPException(409, f"Track is already {row.status}.")
    if body.skip == (body.candidate_id is not None):
        raise HTTPException(422, "Provide exactly one of candidate_id or skip=true.")

    if body.skip:
        row.status, row.user_resolved = TrackStatus.SKIPPED, True
    else:
        # only candidates the engine actually found for this track can be chosen
        match = next((c["candidate"] for c in (row.candidates or []) if c["candidate"]["id"] == body.candidate_id), None)
        if match is None:
            raise HTTPException(422, "candidate_id is not one of this track's candidates.")
        row.status, row.chosen, row.user_resolved = TrackStatus.MATCHED, match, True
    await s.commit()
    return TrackOut(
        id=row.id, position=row.position, status=row.status, source=Track(**row.source), confidence=row.confidence,
        score=row.score, reason=row.reason, chosen=Candidate(**row.chosen) if row.chosen else None,
        user_resolved=row.user_resolved, candidates=row.candidates,
    )


@router.post("/transfers/{transfer_id}/rematch", response_model=TransferOut, status_code=202)
async def rematch(transfer_id: str, request: Request, s: Session, settings: Cfg, x_destination_token: DestToken = None):
    """Retry tracks that are pending/unresolved (e.g. after a rate-limit outage). Never touches resolved tracks."""
    t = await _get_transfer(s, transfer_id)
    adapter = _adapter(t.destination, x_destination_token, settings)
    sm = request.app.state.sm
    if not await pipeline.claim(sm, transfer_id, {TransferStatus.READY, TransferStatus.FAILED}, TransferStatus.MATCHING):
        raise HTTPException(409, f"Cannot rematch a transfer that is {t.status}.")
    await s.execute(update(Transfer).where(Transfer.id == transfer_id).values(processed=0))
    await s.commit()
    runner = request.app.state.runner
    runner.submit(
        runner.gated(pipeline.run_matching(sm, transfer_id, adapter, settings, request.app.state.reranker)),
        name=f"rematch:{transfer_id}",
    )
    await s.refresh(t)
    return await _out(s, t)


# --------------------------------------------------------------------------- commit
@router.post("/transfers/{transfer_id}/commit", response_model=TransferOut, status_code=202)
async def commit(
    transfer_id: str, request: Request, s: Session, settings: Cfg,
    body: CommitIn | None = None, x_destination_token: DestToken = None,
):
    """Create the destination playlist and write matched tracks. Safe to call again to resume or add newly-resolved tracks."""
    body = body or CommitIn()
    t = await _get_transfer(s, transfer_id)
    writable = [TrackStatus.MATCHED, TrackStatus.FAILED] + ([TrackStatus.REVIEW] if body.include_review else [])
    n = (
        await s.execute(
            select(func.count()).select_from(TransferTrack).where(
                TransferTrack.transfer_id == transfer_id, TransferTrack.status.in_([w.value for w in writable])
            )
        )
    ).scalar_one()
    if n == 0:
        raise HTTPException(409, "Nothing to transfer: no matched tracks. Resolve review items or rematch first.")
    adapter = _adapter(t.destination, x_destination_token, settings)
    if not adapter.can_write_playlists:
        await adapter.aclose()
        raise HTTPException(400, f"'{t.destination}' is a catalog, not a playlist service. Download the matched file instead.")
    sm = request.app.state.sm
    if not await pipeline.claim(
        sm, transfer_id, {TransferStatus.READY, TransferStatus.FAILED, TransferStatus.COMPLETED}, TransferStatus.WRITING
    ):
        raise HTTPException(409, f"Cannot commit a transfer that is {t.status}.")
    request.app.state.runner.submit(
        pipeline.run_commit(sm, transfer_id, adapter, settings, body.include_review), name=f"commit:{transfer_id}"
    )
    await s.refresh(t)
    return await _out(s, t)


# --------------------------------------------------------------------------- delete
@router.delete("/transfers/{transfer_id}", status_code=204)
async def delete_transfer(transfer_id: str, s: Session):
    t = await _get_transfer(s, transfer_id)
    if t.status in (TransferStatus.MATCHING, TransferStatus.WRITING):
        raise HTTPException(409, "Transfer is running; wait for it to finish.")
    await s.delete(t)
    await s.commit()
    return Response(status_code=204)
