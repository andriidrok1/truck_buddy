# QA: how TruckBuddy is tested, and the phone checklist for 1.1

Three layers, cheapest first. Everything below runs without keys unless it says otherwise.

## 1. Backend, offline (CI on every PR)

```bash
cd backend && python -m pytest -n 0 -m "not live" -q
```

- `tests/offline/`: parsers on recorded provider responses (`tests/fixtures/<provider>/*.json`), transport tests through `httpx.MockTransport` (an unmocked call fails the test), agent flows with a canned classifier (`FakeLlm`) and no side effects (`agent_sandbox`), endpoint tests through FastAPI's `TestClient` (`api_client`). Spoken lines are asserted verbatim, so a feature that silently stops talking fails a test.
- Re-record fixtures with real keys (manual, never in CI): `python scripts/record_fixtures.py --only here,nws`. Keys are stripped from what is written.
- Live tests (the older `tests/test_iter*.py`) need `EXPO_PUBLIC_BACKEND_URL` and `ACCESS_CODE`; without them they are skipped.
- Nightly smoke against Railway (`.github/workflows/live-smoke.yml`): `/health` says `routing: here`, one route, one agent turn, one TTS. Enable with the repo variable `SMOKE_ENABLED=true` and the secrets `SMOKE_BASE_URL`, `SMOKE_ACCESS_CODE`.

## 2. App, offline (CI on every PR)

```bash
cd frontend && yarn typecheck && yarn lint && yarn test
```

- Pure modules under `src/geo`, `src/voice`, `src/state` carry the logic (route matching, off-route detection, speech queue, advisories, the reply reducer) and have jest tests.
- `fixtures/responses/*.json` are typed against `AgentResponse`: a backend field rename fails `tsc`.
- Replay harness (tester screen, Expo Go): "Replay: I-80 clean / missed exit / stop at a truck stop" plays a synthetic GPS trace along the active route through the same code the road uses; the announcement log on that screen shows every line the copilot spoke with a timestamp. The same traces run in jest, so what passes in CI is what you hear in the cab.

## 3. Phone checklist (one line per feature, do them on a real drive)

Backend features are flags on Railway; see `backend/.env.example`. Turn a feature off there if it misbehaves, no deploy needed.

- Route reply: Sacramento → Reno. The copilot names a closure if there is one ("Heads up, there's a road closure on this route…"), chains in winter ("Chains or snow tires required on I-80 near Nyack"), and a weather warning when NWS has one.
- Speed shield: the limit on the banner reads 65 on I-80 and drops near Donner. Speed alerts are OFF by default; turn on in Truck profile, drive 5 over for 10 s, hear "You're doing 70 in a 55."
- Off-route: miss an exit on purpose. One "Rerouting." within three fixes, the line redraws from where you are, no U-turn. Park at a truck stop: nothing fires.
- Speech order: ask the copilot something right before a turn. The turn prompt waits for the answer, then plays. Ambient lines (weather, speed) never cut a copilot answer.
- Another way: "another way?" after a route. The map line changes; the copilot names the roads and the extra time. Third time: "That's the only truck route I've got."
- Stops: "stop at the Pilot in Fernley on the way". ETA grows by about 30 minutes, the stop shows in the list. "Clear the stops" brings the old ETA back.
- Reviews: "what do drivers say about it?" after a find. Tap a review on the card: Google opens.
- Diesel: a truck stop with a price ends with "Diesel's $5.39 there. That's 30 cents above the California average." (needs `EIA_API_KEY`).
- Parking counts: near Portage, WI the rest area says "56 truck spots open there right now"; near Dunnigan, CA it says "12 truck spots there, no live count".
- Weather: "weather in Reno at 3pm" and "what's it like there" with a route.
- Hours: paste a Motive key in Truck profile, ask "how many hours do I have?". Remove it, hear "Connect Motive in settings…". Kill and relaunch: the key survives.
- Categories (after `CATEGORIES_EXTRA=all` on Railway): "find a CAT scale", "is there a Walmart nearby", "truck wash". Pins have their own icons.
- Options list: "truck stops near me" shows the list; "the second one" picks it.
- Bluetooth: headset or the truck's stereo gets both the copilot and the turn prompts; no drop to phone-call quality between prompts.
- Android (dev build): Google tiles, heading arrow, mic, speaker and Bluetooth, hardware back with the keyboard open, pins redraw after a scroll.

## 4. Before enabling a feature for everyone

| Feature | Flag | Needs |
|---|---|---|
| Regional diesel | `EIA_ENABLED` | `EIA_API_KEY` (free) |
| Extra categories | `CATEGORIES_EXTRA` | app with the new pins (F6), CAT's OK on their list |
| Weigh stations | same | `backend/data/weigh_station.json` from `scripts/import_weigh_stations.py` (Overpass) |
| Reviews | `PLACE_REVIEWS` | `GOOGLE_MAPS_API_KEY`; the card shows author + link (F6) |
| Hours | `HOS_MOTIVE` | the ELD section in Truck profile (F7) |
