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

## Wake word

Not in this version. Picovoice closed its free tier on 2026-06-30, so Porcupine was removed;
the copilot starts with a tap. `src/hooks/useWakeWord.ts` keeps the hook's shape and reports
"unsupported", which also hides the Hands-free switch in the truck profile.
Candidate for the next version: `react-native-voice-activator` (MIT, sherpa-onnx on device,
any phrase without training or a key). It must be tested on a real phone before it ships.

- **Dialog after a route is set:** once the copilot answers "Route is set", the conversation ends and
  the mic turns off. Follow-ups like "not that one" or "something closer" need a new tap.

See the root README for running the app and the backend.
