import json

import pytest

from app.adapters import mock as mock_mod
from app.adapters.mock import MockDestination
from app.db.models import TransferStatus
from tests.conftest import make_client, settle

TRACKS = [
    {"title": "Blinding Lights - Remastered", "artists": ["The Weeknd"], "album": "After Hours", "duration_ms": 200040},
    {"title": "Get Lucky (feat. Pharrell Williams)", "artists": ["Daft Punk"]},
    {"title": "Bohemian Rhapsody", "artists": ["Queen"]},
    {"title": "Hurt", "artists": ["Nine Inch Nails"]},
    {"title": "Zzzzq Xxqwv Nonexistent", "artists": ["Nobody At All"]},
    {"title": "Blinding Lights", "artists": ["The Weeknd"]},  # duplicate of #1
]


async def create_json(client, destination="mock", tracks=TRACKS, **kw):
    r = await client.post("/v1/transfers/json", json={"destination": destination, "playlist_name": "Test", "tracks": tracks, **kw})
    assert r.status_code == 202, r.text
    return r.json()


async def test_full_flow_happy_path():
    async with make_client() as (c, app):
        t = await create_json(c)
        await settle(app)

        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "ready"
        assert t["processed"] == t["total"] - 1  # duplicate is never searched
        assert t["counts"]["matched"] == 4
        assert t["counts"]["unresolved"] == 1
        assert t["counts"]["duplicate"] == 1

        rep = (await c.get(f"/v1/transfers/{t['id']}/report")).json()
        assert [x["title"] for x in rep["not_found"]] == ["Zzzzq Xxqwv Nonexistent"]
        assert rep["duplicates"][0]["position"] == 5

        r = await c.post(f"/v1/transfers/{t['id']}/commit")
        assert r.status_code == 202
        await settle(app)

        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "completed" and t["counts"]["added"] == 4
        assert t["playlist_url"].startswith("https://mock.trackdeck.local/")

        # order preserved, exactly the right songs, exactly one playlist
        assert len(mock_mod.PLAYLISTS) == 1
        written = next(iter(mock_mod.PLAYLISTS.values()))["tracks"]
        assert written == ["m001", "m006", "m012", "m009"]  # BL, Get Lucky, Bohemian (studio), NIN Hurt

        rep = (await c.get(f"/v1/transfers/{t['id']}/report")).json()
        assert rep["success_rate"] == 0.8  # 4 of 5 non-duplicate tracks


async def test_review_resolve_then_commit():
    async with make_client() as (c, app):
        t = await create_json(c, tracks=[{"title": "Hurt", "artists": []}, {"title": "Dreams", "artists": ["Fleetwood Mac"]}])
        await settle(app)
        review = (await c.get(f"/v1/transfers/{t['id']}/tracks", params={"status": "review", "include_candidates": True})).json()
        assert len(review) == 1 and "ambiguous" in review[0]["reason"]
        tid = review[0]["id"]

        # commit w/o include_review leaves it out
        await c.post(f"/v1/transfers/{t['id']}/commit"); await settle(app)
        assert next(iter(mock_mod.PLAYLISTS.values()))["tracks"] == ["m030"]

        # user picks Johnny Cash; re-commit appends it (resume/append semantics)
        r = await c.post(f"/v1/transfers/{t['id']}/tracks/{tid}/resolve", json={"candidate_id": "m010"})
        assert r.status_code == 200 and r.json()["status"] == "matched" and r.json()["user_resolved"]
        await c.post(f"/v1/transfers/{t['id']}/commit"); await settle(app)
        assert len(mock_mod.PLAYLISTS) == 1
        assert next(iter(mock_mod.PLAYLISTS.values()))["tracks"] == ["m030", "m010"]


async def test_resolve_validation():
    async with make_client() as (c, app):
        t = await create_json(c, tracks=[{"title": "Hurt", "artists": []}])
        await settle(app)
        tid = (await c.get(f"/v1/transfers/{t['id']}/tracks")).json()[0]["id"]
        url = f"/v1/transfers/{t['id']}/tracks/{tid}/resolve"
        assert (await c.post(url, json={"candidate_id": "not-a-candidate"})).status_code == 422
        assert (await c.post(url, json={})).status_code == 422
        assert (await c.post(url, json={"candidate_id": "m010", "skip": True})).status_code == 422
        assert (await c.post(url, json={"skip": True})).json()["status"] == "skipped"
        assert (await c.post(f"/v1/transfers/{t['id']}/tracks/99999/resolve", json={"skip": True})).status_code == 404


async def test_commit_with_nothing_matched_is_409():
    async with make_client() as (c, app):
        t = await create_json(c, tracks=[{"title": "Zzzq Wwvx", "artists": ["Nope"]}])
        await settle(app)
        assert (await c.post(f"/v1/transfers/{t['id']}/commit")).status_code == 409
        assert not mock_mod.PLAYLISTS  # and no empty playlist was created


async def test_double_commit_is_rejected():
    async with make_client() as (c, app):
        t = await create_json(c, tracks=TRACKS[:3])
        await settle(app)
        r1 = await c.post(f"/v1/transfers/{t['id']}/commit")
        r2 = await c.post(f"/v1/transfers/{t['id']}/commit")
        assert r1.status_code == 202 and r2.status_code == 409
        await settle(app)
        assert len(mock_mod.PLAYLISTS) == 1
        assert len(next(iter(mock_mod.PLAYLISTS.values()))["tracks"]) == 3


# --------------------------------------------------------------------------- resilience
async def test_rate_limits_and_timeouts_are_retried(register):
    flaky = register("flaky", MockDestination(rate_limit_every=3, transient_every=5))
    async with make_client() as (c, app):
        t = await create_json(c, destination="flaky", tracks=TRACKS[:4])
        await settle(app)
        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "ready" and t["counts"]["matched"] == 4 and t["counts"]["unresolved"] == 0
        assert flaky.search_calls > 4  # retries really happened


async def test_auth_failure_fails_transfer_and_never_writes(register):
    register("locked", MockDestination(require_token="secret"))
    async with make_client() as (c, app):
        r = await c.post("/v1/transfers/json", json={"destination": "locked", "playlist_name": "x", "tracks": TRACKS[:3]})
        await settle(app)
        t = (await c.get(f"/v1/transfers/{r.json()['id']}")).json()
        assert t["status"] == "failed" and "Authentication failed" in t["error"]
        assert not mock_mod.PLAYLISTS


async def test_valid_token_header_works(register):
    register("locked", MockDestination(require_token="secret"))
    async with make_client() as (c, app):
        r = await c.post(
            "/v1/transfers/json", headers={"X-Destination-Token": "secret"},
            json={"destination": "locked", "playlist_name": "x", "tracks": TRACKS[:3]},
        )
        await settle(app)
        t = (await c.get(f"/v1/transfers/{r.json()['id']}")).json()
        assert t["status"] == "ready" and t["counts"]["matched"] == 3
        # token must never be echoed or persisted
        assert "secret" not in json.dumps(t)
        rows = (await c.get(f"/v1/transfers/{t['id']}/tracks", params={"include_candidates": True})).text
        assert "secret" not in rows


async def test_write_failure_mid_playlist_then_resume_has_no_duplicates(register):
    inst = register("writefail", MockDestination(fail_add_after=3))
    async with make_client(playlist_write_batch=2, adapter_max_retries=0) as (c, app):
        tracks = [
            {"title": t, "artists": [a]}
            for t, a in [("Blinding Lights", "The Weeknd"), ("Dreams", "Fleetwood Mac"), ("Africa", "Toto"),
                         ("Hello", "Adele"), ("Believer", "Imagine Dragons")]
        ]
        t = await create_json(c, destination="writefail", tracks=tracks)
        await settle(app)
        await c.post(f"/v1/transfers/{t['id']}/commit"); await settle(app)

        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "failed" and "Re-run commit to resume" in t["error"]
        assert t["counts"]["added"] == 2 and t["counts"]["failed"] == 2 and t["counts"]["matched"] == 1
        pl_id = t["playlist_url"].rsplit("/", 1)[-1]

        await c.post(f"/v1/transfers/{t['id']}/commit"); await settle(app)
        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "completed" and t["counts"]["added"] == 5

        assert len(mock_mod.PLAYLISTS) == 1 and pl_id in mock_mod.PLAYLISTS  # same playlist reused
        written = mock_mod.PLAYLISTS[pl_id]["tracks"]
        assert written == ["m001", "m030", "m031", "m039", "m034"]  # complete, ordered, no duplicates


async def test_rematch_recovers_tracks_lost_to_search_outage(register):
    down = register("down", MockDestination(transient_every=1))  # every search fails
    async with make_client(adapter_max_retries=1) as (c, app):
        t = await create_json(c, destination="down", tracks=TRACKS[:3])
        await settle(app)
        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "failed" and "unreachable" in t["error"] and t["counts"]["unresolved"] == 3
        rep = (await c.get(f"/v1/transfers/{t['id']}/report")).json()
        assert "search failed" in rep["not_found"][0]["reason"]

        down.transient_every = 0  # outage over
        assert (await c.post(f"/v1/transfers/{t['id']}/rematch")).status_code == 202
        await settle(app)
        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "ready" and t["counts"]["matched"] == 3 and t["counts"]["unresolved"] == 0
        assert t["processed"] == 3


async def test_restart_recovery_marks_interrupted_jobs_resumable():
    from sqlalchemy import update

    from app.db.models import Transfer
    from app.services.pipeline import recover_interrupted

    async with make_client() as (c, app):
        t = await create_json(c, tracks=TRACKS[:2])
        await settle(app)
        async with app.state.sm() as s:
            await s.execute(update(Transfer).where(Transfer.id == t["id"]).values(status=TransferStatus.WRITING))
            await s.commit()
        assert await recover_interrupted(app.state.sm) == 1
        t = (await c.get(f"/v1/transfers/{t['id']}")).json()
        assert t["status"] == "failed" and "restart" in t["error"]
        assert (await c.post(f"/v1/transfers/{t['id']}/commit")).status_code == 202  # resumable


# --------------------------------------------------------------------------- files / validation
SPOTIFY_EXPORT = {
    "playlists": [
        {"name": "Gym", "items": [
            {"track": {"trackName": "Blinding Lights", "artistName": "The Weeknd", "albumName": "After Hours", "trackUri": "spotify:track:abc"}, "episode": None},
            {"track": None, "episode": {"name": "Some Podcast"}},
            {"track": {"trackName": "Dreams", "artistName": "Fleetwood Mac", "albumName": "Rumours", "trackUri": "spotify:track:def"}},
        ]},
        {"name": "Chill", "items": [{"track": {"trackName": "Africa", "artistName": "Toto", "albumName": "Toto IV"}}]},
        {"name": "Empty", "items": []},
    ]
}


async def test_spotify_export_batch_upload_and_filter():
    async with make_client() as (c, app):
        files = {"file": ("Playlist1.json", json.dumps(SPOTIFY_EXPORT), "application/json")}
        r = await c.post("/v1/transfers", files=files, data={"destination": "mock"})
        assert r.status_code == 202
        got = {t["playlist_name"]: t["total"] for t in r.json()}
        assert got == {"Gym": 2, "Chill": 1}  # podcast skipped, empty playlist dropped
        await settle(app)

        r = await c.post("/v1/transfers", files=files, data={"destination": "mock", "playlist": "chill"})
        assert [t["playlist_name"] for t in r.json()] == ["Chill"]
        await settle(app)


async def test_txt_upload_and_export_roundtrip():
    async with make_client() as (c, app):
        txt = "Daft Punk - Get Lucky\nQueen - Bohemian Rhapsody\n"
        r = await c.post("/v1/transfers", files={"file": ("my mix.txt", txt)}, data={"destination": "mock", "playlist_name": "Road Trip"})
        t = r.json()[0]
        assert t["playlist_name"] == "Road Trip"
        await settle(app)
        out = (await c.get(f"/v1/transfers/{t['id']}/export", params={"format": "txt"}))
        assert out.status_code == 200 and "Get Lucky | Daft Punk" in out.text
        assert "attachment" in out.headers["content-disposition"]
        # exported txt is itself a valid import
        r2 = await c.post("/v1/transfers", files={"file": ("again.txt", out.text)}, data={"destination": "mock"})
        assert r2.json()[0]["total"] == 2
        await settle(app)
        assert "Track Name" in (await c.get(f"/v1/transfers/{t['id']}/export", params={"format": "csv"})).text
        assert json.loads((await c.get(f"/v1/transfers/{t['id']}/export", params={"format": "json"})).text)["name"] == "Road Trip"


async def test_validation_errors():
    async with make_client(max_upload_bytes=200, max_tracks_per_playlist=3) as (c, app):
        assert (await c.post("/v1/transfers", files={"file": ("x.txt", "a - b\n" * 100)}, data={"destination": "mock"})).status_code == 413
        assert (await c.post("/v1/transfers", files={"file": ("x.json", "{not json")}, data={"destination": "mock"})).status_code == 422
        assert (await c.post("/v1/transfers", files={"file": ("x.txt", "\n\n# only comments\n")}, data={"destination": "mock"})).status_code == 422
        assert (await c.post("/v1/transfers", files={"file": ("x.txt", "a - b")}, data={"destination": "nowhere"})).status_code == 400
        assert (await c.post("/v1/transfers/json", json={"destination": "mock", "playlist_name": "x", "tracks": []})).status_code == 422
        too_many = [{"title": f"t{i}", "artists": ["a"]} for i in range(4)]
        assert (await c.post("/v1/transfers/json", json={"destination": "mock", "playlist_name": "x", "tracks": too_many})).status_code == 422
        assert (await c.get("/v1/transfers/doesnotexist")).status_code == 404


async def test_delete_and_cascade():
    async with make_client() as (c, app):
        t = await create_json(c, tracks=TRACKS[:2])
        await settle(app)
        assert (await c.delete(f"/v1/transfers/{t['id']}")).status_code == 204
        assert (await c.get(f"/v1/transfers/{t['id']}")).status_code == 404
        from sqlalchemy import func, select

        from app.db.models import TransferTrack
        async with app.state.sm() as s:
            assert (await s.execute(select(func.count()).select_from(TransferTrack))).scalar_one() == 0


async def test_health_and_destinations():
    async with make_client() as (c, app):
        assert (await c.get("/health")).json() == {"status": "ok"}
        assert "mock" in (await c.get("/v1/destinations")).json()["destinations"]


async def test_export_kinds_matched_and_unmatched():
    async with make_client() as (c, app):
        t = await create_json(c)
        await settle(app)
        tid = t["id"]
        m = await c.get(f"/v1/transfers/{tid}/export", params={"format": "csv", "kind": "matched"})
        assert m.headers["x-track-count"] == "4"
        assert "(matched).csv" in m.headers["content-disposition"]
        # matched export carries the CANONICAL catalog data (remaster tag gone, ISRC present)
        assert "USUG11904206" in m.text and "Remastered" not in m.text
        u = await c.get(f"/v1/transfers/{tid}/export", params={"kind": "unmatched"})
        assert u.headers["x-track-count"] == "1" and "Zzzzq Xxqwv Nonexistent" in u.text
        assert (await c.get(f"/v1/transfers/{tid}/export", params={"kind": "bogus"})).status_code == 422
        # matched export re-imports cleanly
        r = await c.post("/v1/transfers", files={"file": ("m.csv", m.text)}, data={"destination": "mock"})
        assert r.json()[0]["total"] == 4
        await settle(app)
