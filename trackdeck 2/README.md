# TrackDeck backend

Playlist transfer between music services: **import → normalize → match → review → write → report.**
Deterministic matching first; an LLM is an optional tie-breaker hook, never the default.

## Run it

```bash
pip install -r requirements-dev.txt
pytest                                  # 64 tests
uvicorn app.main:app --reload           # http://localhost:8000/docs
```

Works out of the box against a built-in **mock destination** (40-song catalog with remix/karaoke/live decoys),
so you can exercise the whole flow with no platform keys.

```bash
printf 'The Weeknd - Blinding Lights\nQueen - Bohemian Rhapsody\nSome Band - Fake Song\n' > mix.txt
curl -F file=@mix.txt -F destination=mock -F playlist_name="Road Trip" localhost:8000/v1/transfers   # -> 202, matching starts
curl localhost:8000/v1/transfers/<id>                      # status + progress + counts
curl localhost:8000/v1/transfers/<id>/report               # needs_review / not_found lists
curl -X POST localhost:8000/v1/transfers/<id>/commit       # create playlist + write
```

## Flow

| Step | Endpoint | Notes |
|---|---|---|
| Import | `POST /v1/transfers` (file) · `POST /v1/transfers/json` | Spotify data export, CSV, TXT, TrackDeck JSON. A multi-playlist export creates one transfer per playlist (batch). |
| Progress | `GET /v1/transfers/{id}` | `status`: matching → ready → writing → completed / failed |
| Review | `GET …/tracks?status=review&include_candidates=true`, `POST …/tracks/{tid}/resolve` | Pick a candidate or skip. Only engine-found candidates are accepted. |
| Retry | `POST …/rematch` | Re-runs pending/unresolved only (e.g. after an outage). Resolved tracks untouched. |
| Write | `POST …/commit` | Safe to repeat: resumes after failure, or appends newly-resolved tracks. Same playlist is reused. |
| Report | `GET …/report` | Counts, success rate, per-track reasons, playlist URL |
| Portability | `GET …/export?format=txt\|csv\|json` | Platform-independent copy of the source playlist |

Destination OAuth tokens go in the `X-Destination-Token` header. **They are held in memory for the job and never stored or logged.**

## Matching (`app/services/matching.py`)

Weighted score — title 40 / artist 30 / album 15 / duration 15 — with **missing signals dropped, not penalised**.
Thresholds: ≥0.88 auto, ≥0.70 review, below = unresolved (all configurable). Safety rails:

- **ISRC equal → instant match.**
- **Hard gates:** title or artist too different ⇒ LOW, even if album and duration match (same-album-different-song can't sneak through).
- **Version guard:** live / remix / karaoke / sped-up / cover on only one side ⇒ penalty. Remaster, "feat.", explicit/clean tags are ignored.
- **Ambiguity:** two *different* songs scoring within 0.03 ⇒ review. Single-vs-album releases of the same recording are *not* ambiguous.
- Design principle: **a skipped song beats a wrong song.** Review tracks are excluded from commit unless `include_review=true`.

The weights are your starting heuristic, not validated optima. Tune thresholds against real transfers.

## Reliability

- Retries with exponential backoff + jitter; honors `Retry-After`; auth errors are never retried.
- Atomic status claims (`UPDATE … WHERE status IN …`) block double-commit and concurrent workers.
- Commit checkpoints after every batch; playlist ID is saved before any tracks are written.
- A failed batch stops the write (order is preserved); re-commit resumes from there.
- On boot, jobs orphaned by a crash are marked failed-and-resumable.
- Bounded concurrency; 500 tracks match in <1s against the mock.

## Adding a real platform

Implement `DestinationAdapter` (`search`, `create_playlist`, `add_tracks`), raise `RateLimited` / `TransientError` / `AuthError`
as appropriate, and `register_adapter("name", factory)`. Nothing else changes. **Before building any adapter, verify the
platform's current API terms and write-access eligibility.** That was the open risk in your plan, and it's a business question, not a code one.

## Known gaps (deliberately not faked)

- **Real catalog: MusicBrainz only** (free, file-export). Its tests use faked responses because this sandbox can't reach the live service; verify against the real API once (see DEPLOY.md). No Spotify/YouTube writers yet.
- **No user accounts.** Transfers are protected only by unguessable 128-bit IDs. Add auth + owner scoping before public launch.
- **No rate limiting on your own API**, no Alembic migrations (uses `create_all`), no metrics/tracing.
- **Postgres:** the full suite passes on PostgreSQL 16 (`TRACKDECK_TEST_DB=postgresql://… pytest`). Neon-specific SSL/endpoint behavior is untested.
- Jobs run in-process. Fine for one instance; for scale, call `run_matching` / `run_commit` from ARQ/Celery workers (they're plain coroutines).
- If a platform applies an `add_tracks` call but the response is lost, a retry can duplicate those tracks. Adapters for services with idempotency keys should use them.

## Web app (`web/`)

Next.js 15 + TypeScript + Tailwind 4, talking only to the real routes above.

```bash
cd web && cp .env.example .env.local && npm install && npm run dev   # http://localhost:3000 (backend on :8000)
```

Theme: Uchiha monochrome. `components/Hero.tsx` ports the Lando Norris WebGL trail-reveal (top.jpg dissolves into bottom.jpg), then "awakens" a spinning Sharingan on the eyes after enough pointer travel. Sections: hero, transfer tool, how it works, formats, features, pricing, FAQ.

Flow: upload, live matching progress, review screen (use best match / pick another / skip), then download the
matched playlist (`GET /export?kind=matched`) or the unresolved list (`kind=unmatched`).

## Deploying
See **DEPLOY.md** (Vercel + Render + Neon, all free tiers).
