# TruckBuddy app

## Running on a phone

- **Expo Go (quickest):** start the backend on your laptop, put the phone on the same Wi-Fi, then
  `EXPO_PUBLIC_BACKEND_URL=http://<laptop-ip>:8000 npx expo start` and scan the QR code with the
  iPhone camera. Maps, location, recording and playback work; `react-native-keyboard-controller`
  is swapped for plain views by `src/keyboard.tsx` because Expo Go does not ship its native module.
- **Dev build (JS reloads without rebuilding):** `npx eas-cli build --profile development --platform ios`,
  install from the link, then `npx expo start`. Rebuild only when native dependencies change.
- **Preview build (what App Review sees):** `npx eas-cli build --profile preview --platform ios`.
  `EXPO_PUBLIC_BACKEND_URL` comes from `eas.json` for preview and production.

## Android

### Google Maps key (required for a device build)

`react-native-maps` draws Google Maps on Android, and Google Maps renders a blank grey canvas
without an API key. The key lives outside git: `app.config.ts` reads it from
`GOOGLE_MAPS_ANDROID_KEY` and `expo prebuild` writes it into the manifest.

1. Google Cloud console: enable **Maps SDK for Android**, create an API key.
2. Restrict the key to Android apps: package `io.truckbuddy.app` plus the SHA-1 of the signing
   certificate. `npx eas-cli credentials --platform android` prints the SHA-1 of the keystore EAS
   signs with (one per build profile: the development profile and production use different keystores,
   add both fingerprints to the same key).
3. Store it as an EAS secret so every cloud build sees it (one call per environment the builds use):
   `npx eas-cli env:set development --name GOOGLE_MAPS_ANDROID_KEY --value <key> --visibility secret`
   and the same for `preview` and `production`. For a local `npx expo run:android`, export the
   variable in the shell (or put it in `.env.local`, which is git-ignored).
4. Check what prebuild will write: `GOOGLE_MAPS_ANDROID_KEY=x npx expo config --type prebuild`
   should show `android.config.googleMaps.apiKey`. `--type public` never shows it (the public
   manifest strips keys), so do not be alarmed when it is missing there.

### Builds

- **Dev build:** `npx eas-cli build --profile development --platform android`, install the APK from
  the link, then `npx expo start` and open the project from the dev client.
- **Preview / production:** same as iOS with `--platform android`; `eas.json` submits production to
  the Play **internal** track as a draft.

### Google Play closed test rule

A personal developer account created after November 2023 cannot publish to production until a
closed test has run with **at least 12 testers opted in, continuously, for 14 days**. Start the
closed track early: upload a preview build, add the testers by e-mail or Google Group, make sure
all 12 have installed it, and only then apply for production access in Play Console.

### Device checklist (before each Android build goes to testers)

1. Map renders tiles and the user arrow (a grey grid means the Maps key or its restriction is wrong).
2. Location prompt appears once; denying it shows the "Location off" tag, and tapping it reopens Settings.
3. Tap the copilot, say a stop: the recording uploads, the transcript shows, the reply plays on the loudspeaker.
4. "Navigate" opens Google Maps in driving mode; Waze opens the app when installed, otherwise the browser.
5. Truck profile: Motive key saves ("Connected"), survives an app restart, and is gone after Remove.
6. Hardware back button: leaves the sheet / screen you are on, and only exits the app from the map.

### Audit: iOS-only code paths (2026-10-27)

Every `Platform.OS === "ios"` branch in `src/` and `app/` has an Android path. Nothing was broken;
these are the Android-specific gaps still open:

- `app.config.ts` lists `comgooglemaps` / `waze` under `ios.infoPlist.LSApplicationQueriesSchemes`
  only. Android needs no equivalent today because `openWithFallback` relies on `openURL` throwing
  (not on `canOpenURL`); if a `canOpenURL` check is ever added, add a `<queries>` block through a
  config plugin.
- Location services switched off (not just denied): `useDriverLocation` catches the error and stays
  on the last known fix. Android could call `Location.enableNetworkProviderAsync()` to show the
  system "turn on location" dialog; iOS has no such API, so the hook does not try.
- No `BackHandler` anywhere: the hardware back button is handled by expo-router only, so on the map
  screen it backgrounds the app even when the options sheet or the providers list is open.
- `ParkingSatMap.native.tsx` uses `mapType="none"` on Android (Esri tiles only) and
  `shouldReplaceMapContent` on iOS; the two look the same but have not been compared on a device.
- `android.adaptiveIcon` has no `monochromeImage`, so Android 13+ themed icons fall back to the
  default tinted glyph.
- The wake word (`useWakeWord`) is off on both platforms; the `Platform.OS !== "ios" || appActive`
  guard in `app/index.tsx` is dormant until a detector ships.

## Offline: the saved route and the MapLibre map

Two layers, independent of each other:

- **The route survives no signal and restarts (always on).** The active route is written to
  AsyncStorage (`active_route_v1`) on every change and restored on launch when younger than 12 h
  ("Route restored: …" banner, Resume / Dismiss). Turn-by-turn, advisories and the speed shield
  read only the stored route and GPS. In a dead zone the copilot says so once, points back at the
  loaded line instead of rerouting ("The route is 0.3 miles to the west"), refuses voice turns
  with "No signal right now", and speaks through the phone's own voice (`expo-speech`) because
  the cloud voice needs the server. Works in Expo Go.
- **Map tiles offline (MapLibre build only).** With react-native-maps (the default) the base map
  is Apple Maps / Google Maps and goes blank without signal. The MapLibre provider draws
  OpenFreeMap vector tiles and can save a corridor pack per route.

### Building with MapLibre

```sh
EXPO_PUBLIC_MAP_PROVIDER=maplibre npx eas-cli build --profile development --platform ios
EXPO_PUBLIC_MAP_PROVIDER=maplibre npx expo start   # the same variable at bundle time
```

The variable does two things: `app.config.ts` adds the `@maplibre/maplibre-react-native` config
plugin (native SDK on both platforms), and `src/map/provider.ts` makes the JS pick
`MapLayerLibre` instead of `MapLayerApple`. Without it nothing changes: the plugin is not added,
the library is never required (`src/map/libre.ts` loads it lazily inside a try, like the keyboard
controller), Expo Go keeps working. Expo Go cannot run MapLibre at all: with the variable set in
Expo Go the switch falls back to Apple Maps and the offline-map UI stays hidden. Set the variable
in `eas.json` `env` for a profile to make it stick.

`ParkingSatMap` stays on react-native-maps whatever the provider: its Esri satellite tiles are
online-only.

### Attribution (required)

OpenFreeMap is free and needs no key, on one condition: the attribution must stay visible.
`MapLayerLibre` renders "© OpenFreeMap © OpenMapTiles © OpenStreetMap contributors" in the map's
corner and keeps MapLibre's own attribution button on; do not remove either. The dark theme uses
`https://tiles.openfreemap.org/styles/dark`, light uses `.../liberty`.

### Pack sizes

A pack is the route's bounding box plus 3 km, zoom 6 to 14, named `route-<hash of the geometry>`
(`src/map/bounds.ts`). The card shows an estimate before the download (tile count × ~25 KB):

| Route | Bbox | Estimate |
| --- | --- | --- |
| Sacramento → Stockton (~75 km, north-south) | ~0.8° × 0.3° | ~15 MB |
| Sacramento → Reno (~210 km, diagonal) | ~1.7° × 1.0° | ~90 MB |
| Sacramento → Los Angeles (~600 km) | ~3.5° × 4° | ~800 MB, do not |

The last three packs are kept (the oldest is deleted before a new one downloads); there is no
state-wide pack on purpose. Packs are listed with size and a delete button under Truck profile ›
Offline maps. The tile count limit is raised to 40 000 per device for the download. The real size
on disk comes back from MapLibre once the pack is complete ("Saved · 84 MB").

Needs a device with the dev build to verify: tile rendering, camera follow/fit, marker taps, the
download itself and offline rendering from a pack. None of that runs under jest.

## Wake word

Not in this version. Picovoice closed its free tier on 2026-06-30, so Porcupine was removed;
the copilot starts with a tap. `src/hooks/useWakeWord.ts` keeps the hook's shape and reports
"unsupported", which also hides the Hands-free switch in the truck profile.
Candidate for the next version: `react-native-voice-activator` (MIT, sherpa-onnx on device,
any phrase without training or a key). It must be tested on a real phone before it ships.

- **Dialog after a route is set:** once the copilot answers "Route is set", the conversation ends and
  the mic turns off. Follow-ups like "not that one" or "something closer" need a new tap.

See the root README for running the app and the backend.
