# Deploying the backend

## What happens on a push to main

Prod is on Railway (project `truckbuddy`, environment `production`), owned by Andrii.

- `.github/workflows/deploy-backend.yml` runs on every push to `main` that touches `backend/**` (and on manual
  dispatch). It uploads `backend/` with `railway up --service backend`; Railway builds `backend/Dockerfile`
  (see `backend/railway.json`), checks `/api/health` before switching traffic, then the job polls
  `https://api.truckbuddy.io/api/health` up to 30 times, 5 s apart.
- `.github/workflows/deploy-site.yml` does the same for `website/` (service `site`, Caddy, `website/Dockerfile`)
  and checks `/privacy/`, `/terms/` and `/support/` on `https://truckbuddy.io`.
- Secret: `RAILWAY_TOKEN`, a project token for the production environment.
- Roll back: in the Railway dashboard open the service → Deployments → pick the previous one → Redeploy, or revert
  the commit on `main` and let the workflow ship it.

Railway services: `backend` (volume at `/data`, `TTS_DIR=/data/tts`), `Postgres` (its `DATABASE_URL` is referenced
by `backend`), `site`. Environment variables, including API keys, are set in the Railway dashboard, never in the repo.

DNS is at Namecheap: `api` CNAME and `@` ALIAS / `www` CNAME to the targets Railway shows for each custom domain, plus
one `_railway-verify` TXT record per domain (the CLI doesn't print these; the dashboard does).

## Database

Production uses Postgres via `DATABASE_URL` (Neon). The schema is managed by numbered files in
`backend/migrations/` (Postgres) and `backend/migrations/sqlite/` (the twin for tests); `services/db.py`
applies the pending ones at startup (`memory.warm()` → `db.migrate()`) and on the first connection, recording
them in `schema_version`. A deploy with a new migration needs nothing else; a rollback of code that depends on
a newer table is not covered (write migrations additive).

Without `DATABASE_URL` the store is SQLite at `MEMORY_SQLITE_PATH` (default `backend/data/memory.sqlite`),
meant for tests and a keyless dev box, not for production.

## Environment

Read at startup or on use (see README.md "Backend environment" for meanings):
`ACCESS_CODE`, `TESTER_CODE`, `JWT_SECRET`, `OPENAI_API_KEY`, `OPENAI_TTS_MODEL`, `HERE_API_KEY`,
`ORS_API_KEY`, `GOOGLE_MAPS_API_KEY`, `EIA_API_KEY`, `EIA_ENABLED`, `DATABASE_URL`, `MEMORY_STORE`,
`MEMORY_SQLITE_PATH`, `PARKING_CACHE_DIR`, `PARKING_WARM`, `TPIMS_ENABLED`, `TPIMS_FEEDS`, `WEATHER_ALERTS`,
`NWS_USER_AGENT`, `CHAIN_CONTROLS`, `CHAIN_DISTRICTS`, `HOS_MOTIVE`, `PLACE_REVIEWS`, `CATEGORIES_EXTRA`,
`ROUTE_ALTERNATIVES`, `ROUTE_INCIDENTS`, `ROUTE_SPEED_LIMITS`, `REROUTE_ENABLED`, `RATE_LIMIT_PER_MIN`, `DEVICES_PER_IP_PER_10MIN`, `TOKEN_GRACE_DAYS`, `TOKEN_GRACE_NOT_BEFORE`,
`APPLE_AUDIENCES`, `GOOGLE_CLIENT_IDS`, `SIGNINS_PER_IP_PER_10MIN`, `ACCOUNT_SESSION_IDLE_DAYS`,
`APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY`,
`CORS_ORIGINS`, `DEV_DOCS`, `TRIP_EVENTS_DAYS`, `WORKERS` (or uvicorn's `WEB_CONCURRENCY`), `TTS_DIR`,
`SESSION_STORE`, `TTS_STREAM`, `CREDENTIALS_KEY`, `LOG_TRANSCRIPTS`, `HEALTH_HERE`.

## Logs and health

Every line carries the request id (`X-Request-Id` from the client or generated, echoed in the response) after the
level. A copilot turn is one `turn {json}` line with `req`, `driver`, `sid`, `org`, status, intent and timings;
`scripts/turn_stats.py` reads it and the older `turn k=v` lines. Driver transcripts stay out of the log stream
unless `LOG_TRANSCRIPTS=1` (set it on the test stand, not in production). `/api/health?deep=1` (tester token only) adds a
database `SELECT 1` and one HERE geocode call (cached 30 s per worker, one refresh at a time; `HEALTH_HERE=0` skips
HERE) and answers 503 when one fails. httpx and httpcore log at WARNING, so request URLs (HERE's `apiKey`, a
driver's geocode text and position) never reach the log. The Apple calls log an exception class or Apple's
status and error code, never a code, a token or the key.

## Account deletion and Apple

Apple requires an app with account deletion to revoke the Sign in with Apple token. That needs `APPLE_TEAM_ID`,
`APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` (the team's Sign in with Apple .p8, line breaks as `\n`) and `CREDENTIALS_KEY`
on the service. With them the backend calls `appleid.apple.com/auth/token` after an Apple sign-in and
`/auth/revoke` on `DELETE /api/account`. Without any of them sign-in and deletion still work, the log gets one
warning per Apple sign-in (`apple refresh token not kept ...`), and the deletion answers `apple_revoked: null`.
Set them before a build with account deletion goes to App Review. An account created while they were missing
gets its token on the next sign-in. Rotating `CREDENTIALS_KEY` makes the stored tokens unreadable: deletion then
answers `apple_revoked: false` until the driver signs in again.

README.md "Account deletion" lists what a deletion removes and keeps. The log stream is not touched by it: lines
with a deleted driver's id stay until the host's log retention drops them.

## More than one worker

The process holds no state a second worker needs: the dialog lives in `dialog_sessions`, breakdown results in
`jobs`, parking verdicts and the approach to each entrance in `parking_checks` (mirrored in memory, refreshed
every minute), TTS clips in `TTS_DIR` (default `backend/data/tts`, keep it outside the unpacked tree and shared
between workers on one box), and the feeds one worker polls for all (NWS alerts, Caltrans chain controls,
TPIMS counts) in `feed_cache`; the poller is whichever worker holds the Postgres advisory lock for that feed, so with
the lock held only one worker fetches each feed (the others read the cached copy); without Postgres every
worker polls. The lock calls share one connection and run one at a time.
Set `WORKERS` to the number uvicorn runs: the per-driver budgets (`RATE_LIMIT_PER_MIN`, `RATE_PER_10MIN`,
`RATE_PER_DAY`) are per process and are divided by it. The sign-up limit `DEVICES_PER_IP_PER_10MIN` is per
process too and is not divided, so with N workers one IP can register up to N times as many devices; the same holds for `SIGNINS_PER_IP_PER_10MIN`. `TTS_STREAM` is ignored with more than one worker (a
stream lives in the process that started it). Shutdown waits up to 5 s for background writes (memory, trip
events, jobs, parking) before closing the pool, so a deploy mid-dialog loses nothing.

Data that must survive a deploy: `DATABASE_URL` (or the SQLite file), `TTS_DIR` (clips and the pinned fixed lines),
`PARKING_CACHE_DIR` only when the store is off (files are the fallback). On Railway `TTS_DIR` lives on the `/data` volume and the database is the `Postgres` service.

## Smoke after a deploy

`live-smoke.yml` is manual and expects `SMOKE_BASE_URL` / `SMOKE_ACCESS_CODE`. Locally:

```
EXPO_PUBLIC_BACKEND_URL=https://api.truckbuddy.io ACCESS_CODE=… python -m pytest -n 0 -m smoke tests/smoke -q
```
