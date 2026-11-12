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
| `DEVICES_PER_IP_PER_10MIN` | New device sign-ups per IP per 10 min (default 20, `0` = no limit; raise to ~200 while a build is in App Review) |
| `APPLE_AUDIENCES`, `GOOGLE_CLIENT_IDS` | Sign in with Apple / Google (`POST /api/auth/apple`, `/api/auth/google`): comma-separated audiences a provider token must be issued for. Apple: the iOS bundle id (`io.truckbuddy.ios`). Google: the OAuth client ids of every platform (iOS, Android, Web; a token's `azp` must be in the list too). Empty (the default) = that sign-in answers 503 "Sign-in is not configured"; an unlisted audience is never accepted |
| `SIGNINS_PER_IP_PER_10MIN` | Failed Apple/Google sign-ins per IP per 10 min (default 30, `0` = no limit); sign-ins that succeed are not counted, so drivers behind one NAT do not block each other. The same number caps `POST /api/auth/logout` calls per IP |
| `ACCOUNT_SESSION_IDLE_DAYS` | A signed-in phone (account session) not used for this many days is signed out the next time it shows up (default 180, `0` = never) |
| `APPLE_TEAM_ID`, `APPLE_KEY_ID`, `APPLE_PRIVATE_KEY` | The Sign in with Apple key of the developer team (the .p8 from Apple Developer → Keys; the PEM text, line breaks may be written as `\n`). With all three and `CREDENTIALS_KEY`, a sign-in that carries Apple's authorization code trades it for a refresh token (in the background, the sign-in never waits on it; one exchange per account at a time, four at once per process, none when the account already holds a token), stores it encrypted on the account, and `DELETE /api/account` revokes it at Apple. Without any of them sign-in still works, nothing is exchanged or stored (one warning per sign-in), and deleting an account answers `apple_revoked: null`: the app stays listed under Sign in with Apple in the driver's Apple ID. Required before a build with account deletion goes to App Review |
| `TURNS_PER_DAY`, `NEW_DEVICE_TURNS`, `SAT_NEW_PER_DAY` | Copilot turns per driver per Pacific day (default 150), turns on a device's first 24 h (default 30), new satellite checks per day service-wide (default 300); `0` = off (`services/quota.py`) |
| `DAILY_USD_CAP` | Estimated paid-API spend per Pacific day, whole service, in dollars (default 5, `0` = off). 80% alerts; 100% skips ElevenLabs and new `/speak` clips; 110% stops new satellite checks and Gemini grounding; 120% refuses copilot turns with a spoken line. Map, routes and place search keep working. Prices in `services/spend.py`; `spend_today_usd` and `spend_stage` in `/api/health` |
| `GEMINI_CALLS_PER_DAY`, `HERE_CALLS_PER_DAY` | Daily call caps per provider against loops (default 200 and 3000, `0` = off); a call over the cap is refused before it is sent |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | Where spend alerts go (one per threshold per day); without them the alert is only a log line |
| `TOKEN_GRACE_DAYS`, `TOKEN_GRACE_NOT_BEFORE` | An expired driver token keeps working this many days past its exp while the device row exists and "Delete my data" has not been used since (default 365, `0` = off; app build 2 never refreshes its token). `TOKEN_GRACE_NOT_BEFORE` (ISO date) refuses the grace to tokens issued before it; rotating `JWT_SECRET` kills every token |
| `DEV_DOCS` | `1` enables Swagger at `/api/docs` (dev only) |
| `ROUTE_SPEED_LIMITS`, `ROUTE_INCIDENTS`, `ROUTE_ALTERNATIVES`, `REROUTE_ENABLED` | HERE extras on every route: posted limits, closures, 2 alternatives with road labels and tolls, route handle for `POST /api/reroute` (all default on) |
| `PLACE_REVIEWS` | Google reviews on places and the spoken gist ("what do drivers say about it?"); same SKU, no extra cost |
| `EIA_ENABLED`, `EIA_API_KEY` | Weekly regional diesel average for the price comparison (free key at eia.gov/opendata) |
| `CATEGORIES_EXTRA` | `walmart,cat_scale,truck_wash,weigh_station` or `all`; off by default until the app has pins for them. CAT scales come from `backend/data/cat_scale.json` (`scripts/import_cat_scales.py`) |
| `WEATHER_ALERTS`, `NWS_USER_AGENT` | NWS alerts along new routes and the weather intent; the User-Agent must carry a contact |
| `CHAIN_CONTROLS`, `CHAIN_DISTRICTS` | Caltrans chain controls on California routes (districts polled every 10 min) |
| `TPIMS_ENABLED`, `TPIMS_FEEDS` | Live truck parking counts (WI, KY, IL open feeds; JSON to add states); capacity from `backend/data/truck_parking_capacity.json` (`scripts/import_ntad.py`) |
| `HOS_MOTIVE` | Hours of service from the driver's own Motive key (`X-Motive-Key` header, never stored) or a fleet's key stored encrypted, used only for drivers the fleet mapped to a Motive user |
| `CREDENTIALS_KEY` | Fernet key for secrets at rest: a fleet's Motive key and an account's Apple refresh token (without it neither can be stored) |
| `LOG_TRANSCRIPTS` | `1` puts driver transcripts in the log stream (dev, test stand); off by default |
| `HEALTH_HERE` | `/api/health?deep=1` (tester token) makes one HERE geocode call (cached 30 s); `0` skips it |
| `OPENAI_TTS_MODEL` | `gpt-4o-mini-tts` (default) or `tts-1` |
| `GEMINI_TTS_ENABLED` | `1` makes Gemini TTS the copilot voice (off by default); needs `GEMINI_API_KEY`. Order: Gemini, then ElevenLabs if on, then OpenAI. With `TTS_STREAM=1` (one worker) the first clip streams from Gemini (`streamGenerateContent?alt=sse`, first audio in about 0.5 s); no audio within 6 s, an error or a 429 before it and OpenAI streams instead. A 429, a timeout (6 s) or a bad reply pauses Gemini for 60 s and OpenAI speaks; from 100% of `DAILY_USD_CAP` OpenAI speaks. Counted as `gemini_tts` in `/api/health` `calls_today`, not against `GEMINI_CALLS_PER_DAY` |
| `GEMINI_TTS_MODEL`, `GEMINI_TTS_VOICE` | Gemini TTS model and prebuilt voice (default `gemini-3.8-flash-lite-tts`, `Achird`) |
| `GEMINI_TTS_CONCURRENCY` | Gemini TTS requests in flight per worker (default 2; the TTS models have low rate limits) |

### Account deletion

`DELETE /api/account` (a token from Apple or Google sign-in; a device or access-code token gets 409 and uses `DELETE /api/memory`) removes everything stored for the driver, in this order: the dialogs, the trip events, the conversation, the learned facts, the visits, the stored truck profile and name; then, in one transaction, the breakdown jobs, the device row, the driver row, the organisation of one that was made for the driver, every signed-in phone's session and the account row itself. A deletion that fails on the way answers 503 and can be repeated with the same token, because the sessions and the account go last. The other phones of the account are refused with 401 within a minute. For an Apple account the stored refresh token is revoked at Apple first; `apple_revoked` in the answer is `true` (Apple accepted the call; it also answers so for a token it had already revoked), `false` (Apple refused or could not be reached; the deletion goes on) or `null` (no stored token, or no key). The stored token goes with the account row, so a deletion that failed still has it for the retry. Signing in again with the same Apple or Google identity makes a new, empty account. The rows of an account (device, driver, org) are made at sign-in only, in a transaction that holds the account row, so a request, a refresh or a sign-in that was on its way during the deletion cannot bring the driver back: it gets 401, and a sign-in starts a new account.

The only owner of a fleet gets 409 while the fleet still has other phones, drivers or a stored key: nobody could manage it afterwards and there is no way to add an owner. An owner alone in the fleet takes it along (the org, its trucks, codes and keys). Dispatchers, drivers and one of several owners delete freely.

What stays: a fleet's organisation (unless its only owner was alone in it), its trucks and their profiles, and the fleet code the driver used, marked as used by `deleted` so it cannot bind another phone. Rows that hold no driver id (parking verdicts, feed caches, the daily spend counters). Server logs: lines with the driver's id, request ids and timings (no transcripts unless `LOG_TRANSCRIPTS=1`) are not erased by a deletion and age out with the host's log retention.

`DELETE /api/memory/history` is the smaller "Clear memory" of the history screen: the conversation, the learned facts and the dialogs. The truck profile, the trip events and the visit counts stay. `DELETE /api/memory` is unchanged.

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
