# TruckBuddy — PRD (Phase 1, MOCK MVP)

## Original problem
Voice copilot for truckers (Expo + FastAPI). Map + Muse-style avatar; the driver taps, speaks, and the agent finds the closest truck-friendly place and sets the route. Full spec: /app/memory/product/overview.md, phase1/requirement.md, phase1/journeys.md.
Scope change from the user: a simple MOCK MVP. No Whisper, GPT, TTS, Overpass or OSRM for now. Mocks are isolated in a service layer.

## Architecture
- Backend (/app/backend): server.py has the routes. models.py has the Pydantic models. services/places.py, routing.py and agent.py are provider interfaces with MOCK implementations (swap the provider via get_*_provider()).
- Endpoints: GET /api/health, GET /api/places, POST /api/route, POST /api/agent, GET /api/openapi.json, GET /api/docs
- Mobile (/app/mobile): app/index.tsx is the single map screen. src/components contains MapLayer.native (react-native-maps), MapLayer (web OSM tile fallback), TopBar, CopilotSheet, Avatar, Waveform, Toast/CaptionBubble and Pin. src/hooks/useDriverLocation (expo-location, Sacramento fallback). src/utils/navLinks (Google Maps / Waze deep links with web fallback).

## Implemented (2026-10)
- F-1 map with GPS and a "Demo location" fallback
- F-2 truck chip
- F-3 category chips with colored pins
- F-4 avatar in Ready, Listening and Thinking states
- F-5 MOCK voice loop: the spoken text comes from the selected chip (default is hotel); the reply is spoken with expo-speech and shown as a caption
- F-6 route line, destination label and Navigating card
- F-7 Google Maps / Waze handoff and End route
- Tapping a pin also routes to it
- Saved, Route, Mute, Speaker, menu and layers buttons are visual only

## Backlog
- P0: real STT (Whisper) + GPT 5.5 tool-calling agent + OpenAI TTS; real mic recording (expo-audio)
- P0: real Overpass places + OSRM routing behind the same interfaces
- P1: Google Maps API key for Android production builds
- P2 (Phase 2): editable truck profile, saved places, multi-turn memory, layers

## 2026-06 — Phase 1: real voice loop (implemented)
- expo-audio mic recording (tap avatar → "I'm listening", tap again → send, Close cancels + aborts request)
- POST /api/voice (multipart audio) → Whisper STT (noise/silence filtered) → GPT-5.5 agent with tools search_places / set_route → OpenAI TTS (tts-1, coral), audio at GET /api/voice/audio/{id}
- Statuses: route_set / chat (no route change) / no_results / not_understood
- Captions "You said: …" + reply text; avatar Speaking state; mic permission card (Allow → Open Settings)
- Places/routing still MOCK (Sacramento) — Phase 2: OSM Overpass + OSRM
- Backlog P1: real places & routes; P2: truck profile editor, saved places, Mute/Speaker/layers, conversation memory, more languages

## 2026-06 — Merge with branch Voice-for-agent (satellite parking)
- Base: other agent's satellite parking check (/api/parking, /api/satellite, ParkingStatus, OptionsList, optional Google Places via GOOGLE_MAPS_API_KEY)
- On top: real voice loop (/api/voice, Whisper, GPT-5.5 tools, TTS)
- User decisions: agent auto-routes to best place, others under "See N other options"; satellite check of 3 nearest before choosing, reply says how to enter + where to park (entry_tip, park_tip, E/P spots with lat/lng); category chips removed
- Map layer files untouched (owned by the other agent); spots[] lat/lng ready for map pins
- Backlog: show E/P spots on the main map (map agent), real routes, Google key, truck profile editor

## 2026-06 — Truck routing to any destination
- Voice "take me to <city/address/landmark>" → agent tool route_to_address → geocode (HERE Geocode+Discover, ORS, Nominatim; low-confidence rejected) → truck route
- Routing: HERE v8 transportMode=truck (height/length/weight/hazmat) → ORS driving-hgv fallback → mock; friendly deduped notices; summary in h/min
- Editable truck profile (/truck-profile; height ft/in, trailer ft, weight lb, hazmat) stored on device, sent with every request; chip in top bar + hazmat badge
- Keys: HERE_API_KEY, ORS_API_KEY (backend)
- Backlog: real places for stops (Google key), turn-by-turn guidance in app, E/P spots on map (map agent)

## 2026-06 — First-run onboarding ("let's get to know each other")
- /onboarding: copilot speaks each step (POST /api/speak TTS); driver answers by voice (POST /api/onboarding/parse → Whisper + gpt-5.4-mini extraction) or inputs
- Steps: name → truck (height/trailer/weight/hazmat) → preferences list (avoid tolls, avoid ferries & tunnels, favorite chains, break reminder) → summary → map
- Stored in the truck profile (device); sent with every request; HERE avoid[features], ORS avoid_features; agent uses name + favorite chains
- Break reminder spoken every N hours of an active route; menu button reopens onboarding

## 2026-06 — Speed + conversational onboarding
- Stop search answer 16s → ~3.5s: no blocking satellite checks in the agent (cached + background warm-up), templated route replies (one less LLM round); app fetches /api/parking after the route and speaks entry/park directions after the reply
- Main mic is hands-free: stops on silence (expo-audio metering), tap to stop early
- Onboarding: centered avatar, thought bubble with only the question, auto-listen after each spoken question, "Type instead" fallback, TTS lines pre-generated
- Single-flight parking checks and /api/speak; negative satellite tips filtered; parking card updates immediately, tips spoken right after the reply; compact typing layout in onboarding

## 2026-06 — Clean sheet + live dialog
- Removed Google Maps / Waze handoff buttons and no-op Saved/Route/Mute/Speaker buttons; calmer sheet typography
- Continuous conversation: one tap starts a dialog; after each spoken answer the copilot listens again; session memory (session_id, last 8 turns + last places) for follow-ups ("not that one", "the next closest"); ends on silence, goodbye, [END] from the agent, 2 misses, Close or End route
- Note: true voice-to-voice realtime (barge-in) needs WebRTC → native build only; current dialog works in Expo Go

## Voice latency pass (Jun 2026)
- One gpt-5.4-mini JSON classification per turn (find/go/pick/chat/bye/unclear); backend picks the place by rules (services/agent.py choose_place). gpt-5.5 only for satellite parking. Eval: tests/eval_intent_models.py (mini 20/20, 5.5 20/20).
- App flow: /api/voice/transcribe -> caption "You said" -> /api/agent?speak=true (active_distance_mi/duration_min, no re-route). Filler line ("On it." etc., cached /api/speak) plays meanwhile. 2-sentence replies: audio_url + audio_url_2 (parallel TTS).
- /api/warm (3 categories + top-2 parking checks), shared httpx client (services/http.py), 16 kHz mono recording, silence 700 ms, timings in response + log (services/timing.py).
- Server time to reply ready: find 6.3 s -> 3.6 s, Reno 4.4 s -> 3.2 s (TTS is the largest remaining stage).

## Update (Jun 2026)
- New AI-redrawn 3D copilot avatar (Gemini Nano Banana from user screenshot) → frontend/assets/images/avatar.png
- Manual text input: "Type" button in CopilotSheet → same agent pipeline as voice (no transcription, mic not reopened)
- speech_text.spoken(): "Main St. Exit 12" → "Main Street", "Oak Dr." → "Drive"; "near St. Louis" stays "Saint"
- Spoken time fix: "1 h 50 min" → "one hour and fifty minutes"
- Driving mode: "Go" button on route card → map zooms/follows truck (native: tilted camera), top NavBanner (next turn arrow, distance, instruction, mute toggle), bottom arrival time + remaining time/miles
- Turn-by-turn from HERE actions (RouteInfo.maneuvers, imperial). Spoken prompts ~1 mi / ~0.5 mi-500 ft / at turn when voice on
- Voice guidance on/off: banner button (persisted) + agent intent "voice" (AgentResponse.voice_guidance)
- Fix: speech cut-off — playAndWait safety timer now only guards never-started playback; after start waits real audio duration +2.5s. Turn prompts never interrupt the copilot.
- Voice cut-off fixes (iter14): filler awaited before reply + skipped if answer <300ms; 2nd half preloaded (gap ~100ms web), 404 → console.warn + full caption; playAndWait safety = remaining + dur*0.5 + 2s, armed only when playing & not buffering, listeners removed; /api/voice/audio supports Range (206/416, Accept-Ranges). Logs: [voice] play start/didJustFinish/gap.
- Copilot app controls (iter15): intent 'control' → AgentResponse.actions (services/controls.py validates + builds reply). Trip: start_navigation/end_route/show_options; map: recenter/overview(stays driving, 15s)/zoom_in/out; sound: voice_guidance/wake_word/stop_talking; truck: height/trailer/weight/hazmat; prefs: name/avoid tolls/ferries/favorite chains/break hours. Route-related change with active route → reroute. App sends ui state {driving, voice_guidance, hands_free, options_count}.
- Satellite approach (branch satellite, iter17): services/approach.py (+approach_api.py POST /api/parking/answer). Destination parking checked too; agent context lists verdict/Entry/Park for every session place; intent 'parking' (cached → answer, else 'Let me look at the satellite' + parking_followup → app waits ≤15s, timeout 'Still looking…' then late answer). RouteInfo.approach (bearing last 300 m + street of last HERE maneuver); check_parking(place, approach) rewrites entry tip relative ('On your right/left …') via 2nd vision call on same image; routes end at entry spot E; E marker on map; ParkingStatus 'Satellite imagery, may be dated'.
- iter18 (satellite): entrance side by geometry (cross product heading×(E−approach point 100 m before finish), right if negative; no 2nd vision call); landmarks from base tip via gpt-5.4-mini text (LRU cached); /route returns cached parking and checks satellite in background; demo truck stops = real (49er Sacramento, Flying J Lodi, Petro Corning, Love's Williams) all with verdict+E; geocoder: business-name/town fallback, far weak matches rejected, ORS/Nominatim bias around driver.
- iter19 (satellite): demo places = real, never shifted (_shift removed). Anchors SF + Sacramento, coverage 150 km else empty + NO_PLACES message. Truck stops: 49er, West Sac, Roady's, Sac Valley (Sac); SF Oakland, Western States (San Leandro), Valley Oil (Mountain View), Fairfield TC (I-80); Flying J Lodi, Petro Corning, Love's Williams. Hotels/rest areas real (Hunter Hill, Crystal Springs, Elkhorn N/S, Dunnigan). Search widens to 60 km if <3 within 25 km.
- iter20 (satellite): parking verdicts persisted to backend/.parking_cache/{key}.json (services/parking_store.py, gitignored), memory→disk lookup; verdict without E gets exactly one re-check (better of old/new kept, then final). Startup warm-up (services/parking_warm.py, concurrency 2, PARKING_WARM=0 disables) checks all MOCK_PLACES not on disk + one re-check for those without E. /api/health → parking_cache {total, with_entry}.

## 2026-10-25 Breakdown scenario (branch emergency, merged origin/main b4cc6f8)
- Intent breakdown via breakdown.PROMPT block ({breakdown} in agent SYSTEM); one clarifying question (tire/mechanic), back-out on topic change.
- Research (services/providers_search.py): Google Text Search x3 (reviews), websites (6 s), gpt-5.4-mini fact extraction with verbatim-quote guard, ranking by mode, top 6; HERE Discover fallback.
- GET /api/breakdown/{id} long-poll; follow-ups (call/route cheapest/fastest/closest in code, Q&A from facts).
- Frontend: ProvidersList (filters, sort, cards, call/route, tell-them card, share location SMS); provider routes skip parking check.
- Backlog: hide parking row on provider route card; heading/exit in call card (needs HERE key); HERE fallback live test.
- 2026-10-25: merged main 4c145cc into emergency (/route: instant reply + background check; skip_parking → no check, parking=None). tests/conftest.py: backend URL from frontend/.env, speech fixtures (/tmp/q.mp3 etc.) generated via /api/speak. Fixed /speak single-flight (pin inside the shared task). Agent context: straight-line distance to active place when the app sends no route numbers. Suite: 335 passed.

## Jun 2026 — Access code, long-term memory, mobile web
- Shared access code (env ACCESS_CODE=TRUCK2026, case-insensitive) on first launch -> 365-day JWT per device (sub = random driver id), stored in SecureStore/AsyncStorage; 401 sends the app back to the code screen. 5 wrong tries per IP / 10 min (last X-Forwarded-For hop = ingress)
- All /api routes need Bearer except /health, /auth/access-code, /voice/audio/{id}, /satellite. CORS without credentials
- Long-term memory in Mongo `conversation` per driver: every turn saved; new dialogs get the driver's lines from the last 80 turns as background. History screen (/history, top-bar chat button) with Clear memory (GET/DELETE /api/memory)
- Mobile web: PWA meta tags (home-screen app, no zoom on input)
- Security audit: PASS (only P3 hardening left: token revocation, web token in localStorage)

## Jun 2026 — Follow button, voice capture, addresses, parking follow-up
- Follow button (testID follow-button): camera follows the truck arrow; dragging the map stops it; also fixed the button being off-screen
- Mic: per-listen noise-floor calibration (400 ms; speech = floor+10 dB, pause = floor+4 dB, fixed values as lower bounds), speech after 300 ms sustained, tap path silence 1100 ms, recorder pre-prepared while idle (native, not with wake word), chime + haptic at listen start, metering logs
- STT: Whisper prompt with trucking vocabulary/address style; drop segment only if (no_speech>=0.6 & logprob<-0.8) or logprob<-1.4 or compression>2.4; dropped segments logged; Whisper stock hallucinations ("www.FEMA.gov", "thanks for watching") dropped
- Addresses: number words -> digits, HERE street/place/postalCode/intersection/houseNumber at score>=0.8, read-back on no match, previous town appended to street-only follow-ups; logs for intent/query/geocoder result
- Parking "Let me look at the satellite": retry once, spoken apology if it still fails

## Jun 2026 — Fuel stops vs truck stops, nearby truck lot
- truck_stop results with no truck-stop name (Love's, Pilot, Flying J, TA, Petro, Sapp Bros, Roady's, truck stop, travel center/plaza) and satellite "unlikely" are demoted; named truck stops preferred when verdicts are unknown; if only such places are left the reply calls it "a fuel stop"
- Vision JSON has optional "nearby" {direction, kind: public lot | private yard}: only with "unlikely"; appended to reason and spoken line ("There's a lot with trailers to the southwest, looks like a private yard."); spots stay at the place

## Jun 2026 — Google truck_stop filter
- Drop truck_stop results named truck wash/scale/restaurant/taqueria/tacos/tech/office or with restaurant/cafe/car_wash/office primaryType; keep only truck_stop+fuel type or chain/truck-stop name (chain name always wins)
- Dedupe: 60 m, or 500 m with the same name; curated MOCK_PLACES merged into Google results and preferred
- Raw Google list (name, primaryType, types, km, drop reason) logged per search

## Jun 2026 — Better speech understanding
- STT model: gpt-4o-mini-transcribe (json, en, trucking prompt, temp 0); whisper-1 + confidence guard only as fallback on error
- Agent prompt: words come from noisy-cab speech recognition; act on the corrected trucker meaning ("a few" = fuel, "the stars are out" = start the route), never invent destinations
- Mic: pause also detected when the level drops 14 dB under the driver's average voice this turn (loud cab noise no longer keeps the mic open)

## Jun 2026 — Google-Maps-style top bar, UI polish
- TopBar: "Where to?" search (sends "Take me to …", shows "To: <dest>" while routing), truck settings button on the right (hazmat dot), memory button under it; menu button removed. Light theme only
- Polish: no web focus outline on inputs, white switch thumbs, sheet title "Ask your copilot", destination label/search show only the name before the comma
- Town destinations (HERE locality/area, id here-town-*) skip the satellite check: "No parking check for a town"
- NOTE: ElevenLabs quota is exhausted (0 credits) -> TTS falls back to OpenAI; TTS latency/cache tests fail until topped up

## Jun 2026 — Tester mode
- Code test123 (env TESTER_CODE) logs in as role "tester" (JWT claim + stored on device). Tester sees a flask button -> /tester screen with scenarios; "Flat tire on I-80" moves the app's GPS to I-80 E near Vacaville (38.38585,-121.93664, heading 52). "Back to real GPS" resets. Simulated spot lives in memory (src/sim.ts), resets on app restart

## Jun 2026 — Loud OpenAI voice fallback
- OpenAI tts-1 was ~-26 LUFS vs ElevenLabs ~-15 (12 dB quieter on phones). louder(): bundled ffmpeg (imageio-ffmpeg) +13 dB + limiter -> ~-15..-17 LUFS, ~20 ms extra. OpenAI cache tag has |g13 so old quiet cached clips are not reused

## Jun 2026 — Errors, towns, sessions, controls, find-near, animations (NOT pushed, awaiting user OK)
- Infra errors: status "error" + distinct lines (route down / place search down / LLM lost train of thought, classify 20 s timeout); NOT_UNDERSTOOD only for unintelligible / invalid control
- Towns (here-town-*): town ParkingCheck from cached_parking/best/check_parking, context "Destination is a town; no parking check", old town cache entries dropped
- Session survives route_set (dialog.current kept, live loop paused); fresh after end_route/bye/2 misses/10 min; typed turns share it
- Control phrases (start/stop route, let's go, the stars are out, mute, recenter) never geocoded
- Where to? keeps text + toast while thinking; bare place -> "Take me to X", requests/category words sent as typed
- Truck editor save reroutes silently (requestTruckReroute/takeTruckReroute)
- find near X: geocode X, search around it, route from driver, "N miles from your delivery, M miles from you"; "and a hotel there" reuses point; parking intent with near -> truck stop search; rest_area->truck_stop fallback (said)
- Animations: TopBar slide-in, route summary/name fade-in, parking label zoom, NavBanner slide + maneuver change, avatar double ripple

## Interactive satellite parking map (June 2026)
- Tapping the parking thumbnail opens a full-screen zoomable map (Esri World Imagery, ~300 m frame, same imagery as the verdict).
- E/P spot markers, place pin, last ~300 m of the active route; pinch/pan; footer with verdict, tips, "Open in Google Maps" (Street View), "Imagery © Esri, may be dated · Tap to close".
- Native: react-native-maps + UrlTile (ParkingSatMap.native.tsx); web: Esri tile renderer (ParkingSatMap.tsx). Not pushed yet — awaiting user confirmation.
- Web satellite map: pinch zooms around the fingers, mouse wheel around the cursor; no browser page-zoom on pinch.
- CopilotSheet shows "Route ends at truck entrance" when the route ends at the E spot. Not pushed — awaiting user confirmation.
- iOS earpiece fix (useVoice.ts): replies play in a playback-only session (PLAYBACK_MODE awaited before play()); recorder re-prepared only after a reply finishes / when idle, always after RECORD_MODE (shouldRouteThroughEarpiece:false => .defaultToSpeaker). Not pushed — awaiting iPhone confirmation.
- One voice: ElevenLabs off by default (ELEVENLABS_ENABLED=1 turns it back on); all speech uses OpenAI female voice 'sage', fixed lines re-recorded in sage automatically.
