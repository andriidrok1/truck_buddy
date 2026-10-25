# Phase 1 Requirement (phase-1): MVP voice copilot

Goal: the driver taps the agent, speaks a need, and the agent itself finds the closest truck-friendly place and shows a route that can be handed to Google Maps or Waze. See ../overview.md for integrations and UI rules.

> SCOPE CHANGE (user request): Phase 1 is a MOCK MVP. Places, agent, STT/TTS and routing are MOCKED (hardcoded Sacramento places, keyword-based mock agent, simple polyline). Real map, GPS fallback, full UI and Google Maps/Waze deep links stay real. The real integrations are swapped in later.

## Functional requirements
- REQ-1 [always] The system shall show a full-screen map centered on the driver's GPS position with a blue navigation arrow.
- REQ-2 [unwanted] If location permission is denied or unavailable, then the system shall center on Sacramento, CA and show a "Demo location" tag.
- REQ-3 [always] The system shall show a top chip reading "13 ft 6 in · 53 ft trailer".
- REQ-4 [always] The system shall show three chips: "Truck stops", "Hotels", "Rest areas".
- REQ-5 [event] When the driver taps a category chip, the system shall show pins of that category near the map center (Truck stops blue, Hotels purple, Rest areas green).
- REQ-6 [unwanted] If the places lookup fails or returns nothing, then the system shall show "No places found nearby".
- REQ-7 [always] While idle, the system shall show the bottom sheet "Where to next?" with the avatar, "Tap to talk", Saved and Route buttons.
- REQ-8 [event] When the driver taps the avatar, the system shall start recording and show "I'm listening" with an animated waveform, Mute, Close and "Tap to stop".
- REQ-9 [unwanted] If microphone permission is denied, then the system shall show "Microphone access is needed to talk to your copilot".
- REQ-10 [event] When the driver taps the avatar again while listening, the system shall send the audio for transcription and show a thinking state.
- REQ-11 [event] When transcription returns, the system shall pass the text, the location and the truck profile to the GPT 5.5 agent.
- REQ-12 [event] When the agent decides the need, the system shall call a place-search tool itself, with no further input from the driver.
- REQ-13 [event] When the agent picks a place, the system shall draw a route line from the driver to that place.
- REQ-14 [event] When the route is drawn, the system shall show the Navigating sheet with "<min> min · <mi> mi", the place name, truck info (e.g. "Truck access"), and "Parking availability unknown".
- REQ-15 [event] When the agent finishes, the system shall play its spoken reply (1 to 2 sentences) through OpenAI TTS.
- REQ-16 [unwanted] If transcription, the agent or TTS fails, then the system shall show "Sorry, I didn't catch that. Try again." and return to Ready.
- REQ-17 [event] When the driver taps "Open in Google Maps" or "Open in Waze", the system shall open that app with the destination set.
- REQ-18 [unwanted] If the chosen nav app is not installed, then the system shall open its web URL instead.
- REQ-19 [event] When the driver taps End route, the system shall clear the route and return to Ready.
- REQ-20 [event] When the driver taps Close while listening, the system shall discard the recording and return to Ready.
- REQ-21 [unwanted] If the agent finds no matching place within the search radius, then the system shall widen the radius once before saying "I couldn't find one nearby" (interpreted).
- REQ-22 [always] The backend shall expose its OpenAPI spec at /api/openapi.json.

## Quality requirements
```
Q-1  Tag: Agent.Latency
     Scale: seconds from "Tap to stop" to the route drawn
     Meter: stopwatch, 5 trials
     Must: 15 s
     Plan: 7 s
Q-2  Tag: Agent.Correctness
     Scale: % of requests where the chosen place matches the asked category and is among the 3 closest of that type
     Meter: 5 spoken requests (truck stop, hotel with truck parking, rest area, "need to sleep", "need fuel")
     Must: 80%
     Plan: 100%
Q-3  Tag: Agent.Reply quality
     Scale: spoken reply length and relevance
     Meter: human
     Must: at most 3 sentences, names the place and distance
     Plan: 1 to 2 sentences, natural tone
Q-4  Tag: UI.Fidelity
     Scale: match with the attached 3 screens (layout, chips, avatar, sheets)
     Meter: human, side-by-side screenshot
     Must: all three states recognizable
     Plan: near pixel match, avatar not stretched or blurry
Q-5  Tag: Device.Compat
     Scale: works in Expo Go on iOS and Android
     Meter: human
     Must: one platform fully working
     Plan: both
```
