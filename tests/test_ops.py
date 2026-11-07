from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select, update

from app.core.config import normalize_db_url
from app.core.ratelimit import SlidingWindow
from app.db.models import Transfer, TransferStatus, TransferTrack
from app.services.pipeline import purge_expired
from tests.conftest import make_client, settle

T = [{"title": "Dreams", "artists": ["Fleetwood Mac"]}]


def test_neon_style_urls_are_made_asyncpg_compatible():
    got = normalize_db_url("postgresql://u:p@ep-x.neon.tech/db?sslmode=require&channel_binding=require")
    assert got == "postgresql+asyncpg://u:p@ep-x.neon.tech/db?ssl=require"
    assert normalize_db_url("postgres://u:p@h/db").startswith("postgresql+asyncpg://")
    assert normalize_db_url("postgresql+asyncpg://u:p@h/db?ssl=require") == "postgresql+asyncpg://u:p@h/db?ssl=require"
    assert normalize_db_url("sqlite+aiosqlite:///./x.db") == "sqlite+aiosqlite:///./x.db"


def test_sliding_window():
    import pytest
    from fastapi import HTTPException

    w = SlidingWindow()
    w.check("a", 2, 60, now=0); w.check("a", 2, 60, now=1)
    with pytest.raises(HTTPException) as e:
        w.check("a", 2, 60, now=2)
    assert e.value.status_code == 429 and "Retry-After" in e.value.headers
    w.check("b", 2, 60, now=2)       # other clients unaffected
    w.check("a", 2, 60, now=70)      # window slid


async def test_upload_rate_limit_via_api():
    async with make_client(upload_limit_per_hour=2) as (c, app):
        body = {"destination": "mock", "playlist_name": "x", "tracks": T}
        codes = [(await c.post("/v1/transfers/json", json=body)).status_code for _ in range(3)]
        assert codes == [202, 202, 429]
        await settle(app)


async def test_forwarded_header_only_trusted_when_configured():
    body = {"destination": "mock", "playlist_name": "x", "tracks": T}
    async with make_client(upload_limit_per_hour=1, trust_proxy_headers=True) as (c, app):
        a = await c.post("/v1/transfers/json", json=body, headers={"X-Forwarded-For": "1.1.1.1"})
        b = await c.post("/v1/transfers/json", json=body, headers={"X-Forwarded-For": "2.2.2.2"})
        assert (a.status_code, b.status_code) == (202, 202)
        await settle(app)
    async with make_client(upload_limit_per_hour=1) as (c, app):  # spoofing ignored by default
        a = await c.post("/v1/transfers/json", json=body, headers={"X-Forwarded-For": "1.1.1.1"})
        b = await c.post("/v1/transfers/json", json=body, headers={"X-Forwarded-For": "2.2.2.2"})
        assert (a.status_code, b.status_code) == (202, 429)
        await settle(app)


async def test_retention_purges_old_finished_transfers_and_their_tracks():
    async with make_client() as (c, app):
        ids = []
        for _ in range(3):
            ids.append((await c.post("/v1/transfers/json", json={"destination": "mock", "playlist_name": "x", "tracks": T})).json()["id"])
        await settle(app)
        old = datetime.now(UTC) - timedelta(hours=25)
        async with app.state.sm() as s:
            await s.execute(update(Transfer).where(Transfer.id == ids[0]).values(created_at=old))
            await s.execute(update(Transfer).where(Transfer.id == ids[1]).values(created_at=old, status=TransferStatus.WRITING))
            await s.commit()
        assert await purge_expired(app.state.sm, 24) == 1          # only the old, *idle* one
        assert (await c.get(f"/v1/transfers/{ids[0]}")).status_code == 404
        assert (await c.get(f"/v1/transfers/{ids[1]}")).status_code == 200   # running jobs are never purged
        assert (await c.get(f"/v1/transfers/{ids[2]}")).status_code == 200
        async with app.state.sm() as s:
            n = (await s.execute(select(func.count()).select_from(TransferTrack).where(TransferTrack.transfer_id == ids[0]))).scalar_one()
            assert n == 0


async def test_only_N_transfers_match_at_once():
    import asyncio

    from app.adapters.mock import MockDestination
    from app.adapters.base import register_adapter, unregister_adapter

    active = {"now": 0, "max": 0}

    class Slow(MockDestination):
        async def search(self, query, limit):
            active["now"] += 1; active["max"] = max(active["max"], active["now"])
            await asyncio.sleep(0.05)
            active["now"] -= 1
            return await super().search(query, limit)

    register_adapter("down", lambda creds, s: Slow())
    try:
        async with make_client(max_concurrent_matching=1, match_concurrency=1) as (c, app):
            for _ in range(3):
                await c.post("/v1/transfers/json", json={"destination": "down", "playlist_name": "x", "tracks": T * 1})
            await settle(app)
        assert active["max"] == 1
    finally:
        unregister_adapter("down")
