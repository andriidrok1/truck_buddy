# TruckBuddy

Voice copilot for truck drivers. The driver taps the avatar and asks for a truck stop, hotel, rest area, a destination or roadside help;
the copilot finds a truck-friendly place, builds a truck route, checks the parking lot
on satellite imagery and talks the driver in.

## Layout

```
backend/   FastAPI API (server.py, models.py, services/), pytest suites in backend/tests
frontend/  Expo + expo-router app in TypeScript (screens in app/, everything else in src/)
website/   truckbuddy.io marketing site (static)
memory/    product notes and the original PRD
plan/      implementation plans
```

## Running locally

```bash
cd backend && uvicorn server:app --host 0.0.0.0 --port 8001 --reload
cd frontend && yarn install && yarn expo start
```

The app calls `EXPO_PUBLIC_BACKEND_URL + "/api/..."` (set in `frontend/.env`). Every
route except `/api/health`, `/api/auth/access-code`, audio and satellite images needs
the Bearer token the app gets by redeeming the access code.

### Backend environment

| Variable | Purpose |
| --- | --- |
| `OPENAI_API_KEY` | Speech-to-text, the agent, satellite vision and TTS (required) |
| `DATABASE_URL` | Postgres (Neon) for per-driver conversation memory. Without it memory lives in a local SQLite file (`MEMORY_SQLITE_PATH`, default `backend/data/memory.sqlite`); `MEMORY_STORE=pg\|sqlite\|off` forces a store |
| `JWT_SECRET` | Signs the device tokens (required) |
| `ACCESS_CODE`, `TESTER_CODE` | Shared access codes; the tester code also unlocks simulated locations |
| `GOOGLE_MAPS_API_KEY` | Places search and geocoding (mock places without it) |
| `HERE_API_KEY`, `ORS_API_KEY` | Truck routing |
| `ELEVENLABS_*` | Optional TTS provider (off by default) |
| `CORS_ORIGINS` | Comma-separated origins for the web build (default `*`) |
| `PARKING_CACHE_DIR`, `PARKING_WARM` | Satellite parking check cache; `PARKING_WARM=1` pre-checks the demo places at startup (off by default) |
| `RATE_LIMIT_PER_MIN` | Requests per minute per driver on authenticated routes (default 40, `0` = off for test backends) |
| `DEV_DOCS` | `1` enables Swagger at `/api/docs` (dev only) |
| `ROUTE_SPEED_LIMITS`, `ROUTE_INCIDENTS`, `ROUTE_ALTERNATIVES`, `REROUTE_ENABLED` | HERE extras on every route: posted limits, closures, 2 alternatives with road labels and tolls, route handle for `POST /api/reroute` (all default on) |
| `PLACE_REVIEWS` | Google reviews on places and the spoken gist ("what do drivers say about it?"); same SKU, no extra cost |
| `EIA_ENABLED`, `EIA_API_KEY` | Weekly regional diesel average for the price comparison (free key at eia.gov/opendata) |
| `CATEGORIES_EXTRA` | `walmart,cat_scale,truck_wash,weigh_station` or `all`; off by default until the app has pins for them. CAT scales come from `backend/data/cat_scale.json` (`scripts/import_cat_scales.py`) |
| `WEATHER_ALERTS`, `NWS_USER_AGENT` | NWS alerts along new routes and the weather intent; the User-Agent must carry a contact |
| `CHAIN_CONTROLS`, `CHAIN_DISTRICTS` | Caltrans chain controls on California routes (districts polled every 10 min) |
| `TPIMS_ENABLED`, `TPIMS_FEEDS` | Live truck parking counts (WI, KY, IL open feeds; JSON to add states); capacity from `backend/data/truck_parking_capacity.json` (`scripts/import_ntad.py`) |
| `HOS_MOTIVE` | Hours of service from the driver's own Motive key (`X-Motive-Key` header, never stored) |
| `OPENAI_TTS_MODEL` | `gpt-4o-mini-tts` (default) or `tts-1` |

### Tests

`backend/tests` are integration tests against a running backend: they read
`EXPO_PUBLIC_BACKEND_URL` (or `frontend/.env`) and `ACCESS_CODE`, then call the live
API. Run with `cd backend && pytest`. For the app, `cd frontend && yarn typecheck && yarn test`
and `yarn lint --max-warnings 0`; CI (`.github/workflows/frontend.yml`) runs the same three and fails on any lint error or warning.

## Frontend conventions

- Colors come from `src/theme.ts` (`useTheme()`), never hard-coded in a screen.
- Storage goes through `src/utils/storage` (`secureGet`/`secureSet` for tokens; key
  names must match `[A-Za-z0-9._-]` or SecureStore throws on a phone).
- File uploads go through `src/utils/upload.ts` (`expo/fetch` on native); never build
  `FormData` parts yourself.
- All API calls go through `src/api.ts`, which adds the token, sends a 401 back to the
  access-code screen and puts a deadline on every request.
- `src/components/error-boundary.tsx` is mounted once in `app/_layout.tsx`.
