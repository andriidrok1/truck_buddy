# Phase 1 Journeys

- JRN-1: steps: open the app, allow location. expected: map centered on the current position with a blue arrow; top chip "13 ft 6 in · 53 ft trailer"; chips Truck stops / Hotels / Rest areas; sheet "Where to next?" with avatar and "Tap to talk".
- JRN-2: steps: open the app, deny location (or test in a browser without location). expected: map centered on Sacramento, CA with a "Demo location" tag; everything else works.
- JRN-3: steps: tap "Truck stops", then "Hotels", then "Rest areas". expected: blue, purple and green pins appear near the map center for each; backend place search returns real OpenStreetMap results with name and coordinates.
- JRN-4: steps: tap the avatar. expected: sheet changes to "I'm listening" with a waveform, Mute, Close, "Tap to stop".
- JRN-5: steps: tap the avatar, say "I need a truck stop", tap to stop. expected: a thinking state, then a route line to a real nearby truck stop, the Navigating sheet with "X min · Y mi", the place name and "Parking availability unknown", and a short spoken reply naming the place.
- JRN-6: steps: say "I'm tired, find me a hotel where I can park my rig". expected: the agent picks a hotel/motel (purple) with truck parking without any further questions, and the route is set.
- JRN-7: steps: say "find a rest area". expected: the route goes to a rest area (green).
- JRN-8: backend: send a text request (bypassing voice) to the agent endpoint with Sacramento coordinates and "truck stop". expected: the response has the chosen place (name, lat, lng, category), route geometry, duration, distance and a reply text.
- JRN-9: steps: with a route set, tap "Open in Google Maps", then "Open in Waze". expected: each opens with the destination preset (or its web URL if not installed).
- JRN-10: steps: with a route set, tap End route. expected: the route line is gone and "Where to next?" is back.
- JRN-11: steps: tap the avatar, then Close. expected: back to Ready, no agent call made.
- JRN-12: steps: deny the microphone, then tap the avatar. expected: "Microphone access is needed to talk to your copilot"; the map stays usable.
- JRN-13: backend: send empty or garbled audio/text to the agent. expected: a graceful error; the app shows "Sorry, I didn't catch that. Try again." and returns to Ready.
- JRN-14: backend: GET /api/openapi.json. expected: 200 with the spec.
