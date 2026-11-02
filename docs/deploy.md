# Deploying the backend

## What happens on a push to main

`.github/workflows/deploy-backend.yml` runs on every push to `main` that touches `backend/**` (and on manual
dispatch). It tars `backend/` without `tests/` and `__pycache__`, streams the tarball over SSH to
`tbdeploy@$TRUCKBUDDY_HOST` (AWS Lightsail) whose authorized key is restricted to one forced command, with the
short commit hash as the only argument, then polls `http://$HOST/api/health` up to 30 times, 5 s apart.
Secrets: `TRUCKBUDDY_DEPLOY_KEY`, `TRUCKBUDDY_KNOWN_HOSTS`, `TRUCKBUDDY_HOST`.

The forced command on the server (unpack, install, restart, keep data dirs) is **not in this repo**.

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
`ROUTE_ALTERNATIVES`, `ROUTE_INCIDENTS`, `ROUTE_SPEED_LIMITS`, `REROUTE_ENABLED`, `RATE_LIMIT_PER_MIN`,
`CORS_ORIGINS`, `DEV_DOCS`, `TRIP_EVENTS_DAYS`.

Data that must survive a deploy: `DATABASE_URL` (or the SQLite file), `PARKING_CACHE_DIR`, the TTS cache dir
(`.tts_cache` next to the app unless configured). The forced command must keep them outside the unpacked tree.

## Smoke after a deploy

`live-smoke.yml` is manual and expects `SMOKE_BASE_URL` / `SMOKE_ACCESS_CODE`. Locally:

```
EXPO_PUBLIC_BACKEND_URL=https://api.truckbuddy.io ACCESS_CODE=… python -m pytest -n 0 -m smoke tests/smoke -q
```

## TODO (Abdul)

- systemd unit name and where the forced command script lives on the box
- install path of the unpacked backend, the venv, and the data directories it preserves
- how to roll back to the previous tarball (is the previous release kept?)
- where the logs go (`journalctl -u …`) and their retention
