# Requirements: TruckBuddy (working name), a voice copilot for truckers

## Core Value
A trucker taps the friendly agent, says what they need ("I need a place to park and sleep"), and the agent finds the closest place that fits their truck and sets up the route by itself. No typing, no wrong places.

## Personas & Roles
- Truck driver (single role, no login). Uses the app on a phone mounted in the cab. Hands mostly busy, so voice comes first.

## Features
| id | feature | persona | phase |
|---|---|---|---|
| F-1 | Full-screen map with the driver's live GPS position (Sacramento fallback) | driver | 1 |
| F-2 | Truck profile chip on top (default 13 ft 6 in, 53 ft trailer) used in every search | driver | 1 |
| F-3 | Category chips (Truck stops, Hotels w/ truck parking, Rest areas) that show colored pins nearby | driver | 1 |
| F-4 | Muse-style agent avatar at the bottom, "Tap to talk", with Ready and Listening states | driver | 1 |
| F-5 | Voice loop: speech-to-text, then GPT 5.5 works out the need, searches places itself, and replies by voice | driver | 1 |
| F-6 | The agent sets up navigation itself: route line on the map plus a card with time, distance, place name and truck info | driver | 1 |
| F-7 | "Open in Google Maps" / "Open in Waze" buttons to drive the route, plus End route | driver | 1 |
| F-8 | Editable truck profile (height, trailer length, weight, hazmat) | driver | 2 |
| F-9 | Saved places, Mute, Speaker toggle, layer switch | driver | 2 |
| F-10 | Multi-turn memory ("not that one, something cheaper"), plus the agent proactively suggests stops (e.g. a break reminder) | driver | 2 |

## Phases
- Phase 1 (MVP): tap the agent, speak, it finds a truck-friendly place and sets the route. Delivers F-1 to F-7. [requirement](phase1/requirement.md) · [journeys](phase1/journeys.md)
- Phase 2: personalisation and smarter conversation. Delivers F-8 to F-10.

## UI/UX Rules
- Follow the attached 3-screen design (Ready / Listening / Navigating): light map, white rounded chips, bottom sheet, center circular avatar with a small blue mic badge.
- Avatar: a cute, soft beige blob character with two dot eyes (Muse-like). It gently pulses while listening and while thinking.
- Pin colors: Truck stops blue, Hotels purple, Rest areas green.
- Big touch targets (at least 56 px) for in-cab use. Short spoken replies (1 to 2 sentences).

## Integrations
- Map display: react-native-maps (Apple Maps on iOS, Google Maps on Android). The user may switch providers later.
- Places data: OpenStreetMap Overpass API, free with no key (fuel stations with hgv=yes / truck stops, motels/hotels with truck parking, rest areas, hgv parking). Can be swapped for Google Places later if a key is given.
- Route preview geometry, ETA and distance: OSRM public routing, free with no key. It is not truck-aware, so this is a declared limitation for the hackathon.
- Speech-to-text: OpenAI Whisper (Emergent Universal Key).
- Agent brain: GPT 5.5 with tool calling (Emergent Universal Key).
- Voice reply: OpenAI TTS (Emergent Universal Key).
- Driving handoff: Google Maps / Waze deep links.

## Edge Cases
- Location permission denied or no GPS: use Sacramento, CA (38.5816, -121.4944) and show a small "Demo location" tag.
- Nothing found nearby: widen the search radius once, then the agent says so out loud.
- Vague request: the agent picks the best-fit closest match. It asks a follow-up only if the request is truly ambiguous.
- Mic permission denied: show a clear message and keep the map usable.
- Parking availability is not known, so show "Parking availability unknown".
- Waze / Google Maps not installed: fall back to the web URL.

## Seed Data
None. All places come live from OpenStreetMap. Default truck profile: 13 ft 6 in, 53 ft trailer.
