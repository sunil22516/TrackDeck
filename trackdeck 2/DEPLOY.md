# Deploy TrackDeck for $0 (about 30 minutes)

**Stack:** Vercel (web) + Render free web service (API) + Neon free Postgres + MusicBrainz (free catalog).
Free-tier terms change. Confirm each service's current limits when you sign up.

## 0. Push to GitHub
Put this whole folder in your repo (`api` at the root, `web/` inside it).

## 1. Database: Neon
1. neon.tech → new project → copy the **connection string**.
2. Keep it. Paste it **as-is** later (`postgresql://…?sslmode=require`); TrackDeck converts it for asyncpg.

## 2. API: Render
1. render.com → **New → Blueprint** → select the repo (uses `render.yaml`).
2. When prompted, set:
   - `TRACKDECK_DATABASE_URL` = the Neon string
   - `TRACKDECK_MUSICBRAINZ_CONTACT` = your real email (required by MusicBrainz, and it enables the catalog)
   - `TRACKDECK_CORS_ORIGINS` = `["https://YOUR-APP.vercel.app"]` (fill in after step 3, then redeploy)
3. Deploy. Check `https://YOUR-API.onrender.com/health` → `{"status":"ok"}` and `/v1/destinations` → `{"destinations":["musicbrainz"]}`.

## 3. Web: Vercel
1. vercel.com → **Add New Project** → same repo → **Root Directory: `web`**.
2. Environment variable: `NEXT_PUBLIC_API_URL` = `https://YOUR-API.onrender.com`.
3. Deploy, then put the Vercel URL into `TRACKDECK_CORS_ORIGINS` on Render and redeploy the API.

## 4. Smoke test (do this before telling anyone)
Upload a 10–20 song CSV/TXT. Expect: progress bar → review list → downloadable matched `.csv` with ISRCs.
Then upload a real Spotify export. Check that obscure songs land in "need your decision", not silently wrong.

## What to expect on the free tier
- **Cold starts:** Render free sleeps after ~15 min idle; the first visit takes up to a minute. The UI shows "Waking the server…". A free uptime pinger (e.g. UptimeRobot hitting `/health` every 5 min) keeps it warm but uses your free instance hours.
- **Speed:** MusicBrainz allows ~1 request/sec across *all* your users. ~1 song/sec; a 500-song playlist takes ~10 min. Only 2 transfers match at once; others queue. Fine for a first 20–50 testers, not for a launch.
- **Interrupted jobs** (server restarts mid-match) are marked failed and resume with "Retry unmatched tracks".
- **Limits in place:** 5 MB files, 500 tracks/playlist, 10 uploads/hour/IP, data auto-deleted after 24 h.

## Before you charge anyone
- **Check MusicBrainz/MetaBrainz terms for commercial use.** The free web service is aimed at light, non-commercial use. If TrackDeck earns money, read their commercial-use policy and consider a paid data feed. This is the main business risk of the free catalog.
- Add a short **privacy policy** (what you store, 24 h deletion, no accounts) and a contact email.
- Add **auth** if you want accounts or history; currently transfers are private only by unguessable IDs.
