import time

import httpx
import pytest

from app.adapters import musicbrainz as mb
from app.adapters.base import AdapterError, RateLimited, TransientError, unregister_adapter
from app.core.config import Settings
from app.core.schema import Track
from app.services.matching import Confidence, decide
from tests.conftest import make_client, settle

REC = {
    "id": "11111111-aaaa-bbbb-cccc-000000000001", "score": 100, "title": "Blinding Lights", "length": 200040,
    "artist-credit": [{"name": "The Weeknd", "joinphrase": "", "artist": {"id": "x", "name": "The Weeknd"}}],
    "releases": [{"id": "r1", "title": "After Hours"}], "isrcs": ["USUG11904206"],
}
KARAOKE = {**REC, "id": "22222222-0000-0000-0000-000000000002", "title": "Blinding Lights (Karaoke Version)",
           "artist-credit": [{"name": "Karaoke Hits"}], "isrcs": []}
NO_LENGTH = {"id": "33333333-0000-0000-0000-000000000003", "title": "Obscure", "artist-credit": [{"name": "Nobody"}]}


@pytest.fixture(autouse=True)
def _fresh():
    mb._CACHE.clear()
    mb.THROTTLE._next = 0.0
    yield
    mb._CACHE.clear()


def adapter(handler, interval=0.0):
    return mb.MusicBrainzDestination("test@example.com", interval, transport=httpx.MockTransport(handler))


def ok(*recs):
    return lambda req: httpx.Response(200, json={"recordings": list(recs)})


async def test_parses_recordings_and_sends_required_headers():
    seen = {}

    def h(req):
        seen["ua"], seen["q"] = req.headers["user-agent"], dict(req.url.params)
        return httpx.Response(200, json={"recordings": [REC, NO_LENGTH, {"id": "", "title": "bad"}]})

    out = await adapter(h).search('recording:"blinding lights"', 10)
    assert "test@example.com" in seen["ua"] and seen["ua"].startswith("TrackDeck/")
    assert seen["q"]["fmt"] == "json" and seen["q"]["limit"] == "10"
    assert [c.title for c in out] == ["Blinding Lights", "Obscure"]  # malformed row dropped, missing fields tolerated
    assert out[0].artists == ["The Weeknd"] and out[0].album == "After Hours"
    assert out[0].duration_ms == 200040 and out[0].isrc == "USUG11904206"
    assert out[1].duration_ms is None and out[1].album is None and out[1].isrc is None


async def test_error_mapping():
    with pytest.raises(RateLimited) as e:
        await adapter(lambda r: httpx.Response(503, headers={"Retry-After": "7"})).search("q", 5)
    assert e.value.retry_after == 7
    with pytest.raises(TransientError):
        await adapter(lambda r: httpx.Response(502)).search("q2", 5)
    with pytest.raises(AdapterError):
        await adapter(lambda r: httpx.Response(400)).search("q3", 5)

    def boom(req):
        raise httpx.ConnectTimeout("slow")

    with pytest.raises(TransientError):
        await adapter(boom).search("q4", 5)
    with pytest.raises(TransientError):
        await adapter(lambda r: httpx.Response(200, text="<html>")).search("q5", 5)


async def test_cache_means_repeat_queries_cost_nothing():
    calls = []

    def h(req):
        calls.append(1)
        return httpx.Response(200, json={"recordings": [REC]})

    a = adapter(h)
    await a.search("same", 10)
    await a.search("same", 10)
    assert len(calls) == 1


async def test_failures_are_not_cached():
    n = {"i": 0}

    def h(req):
        n["i"] += 1
        return httpx.Response(503) if n["i"] == 1 else httpx.Response(200, json={"recordings": [REC]})

    a = adapter(h)
    with pytest.raises(RateLimited):
        await a.search("flaky", 10)
    assert len(await a.search("flaky", 10)) == 1


async def test_throttle_spaces_requests_process_wide():
    a, b = adapter(ok(REC), 0.15), adapter(ok(REC), 0.15)  # two *different* adapter instances
    t0 = time.monotonic()
    await a.search("one", 5)
    await b.search("two", 5)
    await a.search("three", 5)
    assert time.monotonic() - t0 >= 0.28


async def test_query_strategy():
    a = adapter(ok())
    qs = a.queries_for(Track(title="Blinding Lights - Remastered", artists=["The Weeknd"], isrc="USUG11904206"))
    assert qs[0] == "isrc:USUG11904206"
    assert qs[1] == 'recording:"blinding lights" AND artist:"the weeknd"'
    assert a.queries_for(Track(title='He said "hi"'))[-1] == 'recording:"he said hi"'  # quotes can't break the query


async def test_create_playlist_is_refused_clearly():
    with pytest.raises(AdapterError, match="catalog"):
        await adapter(ok()).create_playlist("x")


async def test_real_world_matching_through_the_engine():
    cands = await adapter(ok(KARAOKE, REC)).search("q", 10)
    out = decide(Track(title="Blinding Lights - Remastered", artists=["The Weeknd"], album="After Hours"), cands, Settings())
    assert out.confidence == Confidence.HIGH and out.best.candidate.id == REC["id"]  # karaoke decoy ignored


async def test_end_to_end_file_export_through_api():
    from app.adapters.base import register_adapter

    def h(req):
        return httpx.Response(200, json={"recordings": [REC] if "blinding" in req.url.params["query"] else []})

    register_adapter("musicbrainz", lambda creds, s: adapter(h))  # no contact configured -> app won't overwrite it
    try:
        async with make_client() as (c, app):
            assert "musicbrainz" in (await c.get("/v1/destinations")).json()["destinations"]
            r = await c.post("/v1/transfers/json", json={"destination": "musicbrainz", "playlist_name": "Mix", "tracks": [
                {"title": "Blinding Lights - Remastered", "artists": ["The Weeknd"]},
                {"title": "Totally Unknown Song", "artists": ["Nobody"]},
            ]})
            await settle(app)
            t = (await c.get(f"/v1/transfers/{r.json()['id']}")).json()
            assert t["status"] == "ready" and t["counts"]["matched"] == 1 and t["counts"]["unresolved"] == 1
            f = await c.get(f"/v1/transfers/{t['id']}/export", params={"kind": "matched", "format": "csv"})
            assert "USUG11904206" in f.text and "Remastered" not in f.text
            bad = await c.post(f"/v1/transfers/{t['id']}/commit")  # catalog-only: rejected up front, state untouched
            assert bad.status_code == 400 and "catalog" in bad.json()["detail"]
            assert (await c.get(f"/v1/transfers/{t['id']}")).json()["status"] == "ready"
    finally:
        unregister_adapter("musicbrainz")


async def test_adapter_registered_only_when_contact_configured():
    async with make_client() as (c, _):
        assert "musicbrainz" not in (await c.get("/v1/destinations")).json()["destinations"]
    async with make_client(musicbrainz_contact="me@example.com") as (c, _):
        assert "musicbrainz" in (await c.get("/v1/destinations")).json()["destinations"]
    unregister_adapter("musicbrainz")
