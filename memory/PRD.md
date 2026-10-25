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
