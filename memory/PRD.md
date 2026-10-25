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
